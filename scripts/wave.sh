#!/usr/bin/env bash
# 为一个 Axxx 设计任务开一个隔离的 worktree，供单个 agent 独立开发。
#
# 替代已失效的 bin/dispatch.sh —— 那个脚本的 ROOT_DIR 算到了仓库外，
# 且 source 一个重构后已不存在的 workflow/harness/config.sh。
#
# 用法:
#   scripts/wave.sh A3            # 建 .worktrees/A3，分支 codex/A3-fact-pack
#   scripts/wave.sh A3 --dry-run  # 只打印将要执行的动作
#   scripts/wave.sh --list        # 列出现有 worktree 与对应任务
#   scripts/wave.sh A3 --remove   # 收工后清理
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKTREE_DIR="${ROOT_DIR}/.worktrees"
BASE="${HARNESS_BASE:-dev}"

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YEL=$'\033[1;33m'; NC=$'\033[0m'
info() { printf "%s[+]%s %s\n" "$GRN" "$NC" "$*"; }
warn() { printf "%s[!]%s %s\n" "$YEL" "$NC" "$*"; }
die()  { printf "%s[x]%s %s\n" "$RED" "$NC" "$*" >&2; exit 1; }

# gitignore 的文件不会进 worktree，必须逐个补。
# .evidence_key 最关键：evidence.py:get_key() 在缺失时会静默新建一把，
# 于是 worktree 里签的证据回到主仓库校验会判 tampered，
# 而那个失败看起来完全像是实现 bug。
LINK_ITEMS=(
    "config/credentials.yaml"
    "config/.evidence_key"
)

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 1; }

list_worktrees() {
    git -C "$ROOT_DIR" worktree list
    exit 0
}

TASK=""; DRY_RUN=false; REMOVE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)    list_worktrees ;;
        --dry-run) DRY_RUN=true; shift ;;
        --remove)  REMOVE=true; shift ;;
        --base)    BASE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *)         [[ -z "$TASK" ]] && TASK="$1" || die "多余参数: $1"; shift ;;
    esac
done

[[ -z "$TASK" ]] && usage

# 大小写归一：a3 -> A3
TASK="$(printf '%s' "$TASK" | tr '[:lower:]' '[:upper:]')"

# 从 docs/design/ 找唯一匹配的设计文档，用它的文件名作分支名后缀。
shopt -s nullglob
matches=("${ROOT_DIR}/docs/design/${TASK}-"*.md)
shopt -u nullglob
[[ ${#matches[@]} -eq 0 ]] && die "docs/design/ 下没有 ${TASK}-*.md，任务 ID 写错了？"
[[ ${#matches[@]} -gt 1 ]] && die "${TASK} 匹配到多份文档，无法确定：${matches[*]}"

DOC_PATH="${matches[0]}"
DOC_NAME="$(basename "$DOC_PATH" .md)"
SLUG="$(printf '%s' "$DOC_NAME" | tr '[:upper:]' '[:lower:]')"
BRANCH="codex/${SLUG}"
WT_PATH="${WORKTREE_DIR}/${TASK}"

if $REMOVE; then
    info "移除 worktree: ${WT_PATH}"
    $DRY_RUN && { echo "  git worktree remove ${WT_PATH}"; exit 0; }
    git -C "$ROOT_DIR" worktree remove "$WT_PATH" 2>/dev/null \
        || git -C "$ROOT_DIR" worktree remove --force "$WT_PATH"
    info "分支 ${BRANCH} 保留（未合并的工作不自动删）"
    exit 0
fi

# ── 前置校验：地基是否已落盘 ──
# A3 明写要调用 A1 的 baseline_diff()，A6 的 O4 要读 A2 的 red_witness。
# 这些文件若还未跟踪，worktree 里就是 MISSING，agent 只会各自重写一份。
UNTRACKED_DEPS=()
for f in docs/design sw_lib/core/git_repo.py sw_lib/workflow/red_witness.py; do
    if [[ -e "${ROOT_DIR}/${f}" ]] \
        && [[ -z "$(git -C "$ROOT_DIR" ls-files -- "$f")" ]]; then
        UNTRACKED_DEPS+=("$f")
    fi
done
if [[ ${#UNTRACKED_DEPS[@]} -gt 0 ]]; then
    warn "以下前置产出存在于工作区但未提交，新 worktree 里会缺失："
    for f in "${UNTRACKED_DEPS[@]}"; do printf "      %s\n" "$f"; done
    warn "先 commit 到 ${BASE}，否则派出去的 agent 看不见前置任务的成果。"
    if [[ "${HARNESS_ALLOW_DIRTY_BASE:-}" == "1" ]]; then
        warn "HARNESS_ALLOW_DIRTY_BASE=1，已按要求跳过该检查"
    elif ! $DRY_RUN; then
        die "拒绝派发。确认要继续请设 HARNESS_ALLOW_DIRTY_BASE=1"
    fi
fi

[[ -e "$WT_PATH" ]] && die "worktree 已存在: ${WT_PATH}"

if $DRY_RUN; then
    cat << EOD
[dry-run] 将执行：
  分支      ${BRANCH}
  worktree  ${WT_PATH}
  基于      ${BASE}
  设计文档  ${DOC_PATH#"$ROOT_DIR"/}
  软链       ${LINK_ITEMS[*]}
  workspace 新建空目录（状态天然隔离）
EOD
    exit 0
fi

info "创建 worktree: ${WT_PATH} [${BRANCH}] <- ${BASE}"
mkdir -p "$WORKTREE_DIR"
if git -C "$ROOT_DIR" rev-parse --verify "$BRANCH" &>/dev/null; then
    warn "分支 ${BRANCH} 已存在，直接挂载"
    git -C "$ROOT_DIR" worktree add "$WT_PATH" "$BRANCH" >/dev/null
else
    git -C "$ROOT_DIR" worktree add -b "$BRANCH" "$WT_PATH" "$BASE" >/dev/null
fi

# ── 补 gitignore 的文件 ──
for item in "${LINK_ITEMS[@]}"; do
    src="${ROOT_DIR}/${item}"
    dst="${WT_PATH}/${item}"
    if [[ ! -e "$src" ]]; then
        warn "跳过（源不存在）: ${item}"
        continue
    fi
    mkdir -p "$(dirname "$dst")"
    ln -sf "$src" "$dst"
    info "已链接: ${item}"
done

# 任务书兜底：docs/design/ 若未提交，worktree 里就没有任务书与 DEV-PROTOCOL，
# agent 无从下手。此时链过去（注意：对它的编辑会落到主仓库）。
if [[ ! -e "${WT_PATH}/docs/design" ]] && [[ -d "${ROOT_DIR}/docs/design" ]]; then
    ln -sf "${ROOT_DIR}/docs/design" "${WT_PATH}/docs/design"
    warn "docs/design 未提交，已链接到主仓库 —— 对设计文档的修改会直接生效"
fi

# workspace 是每个 worktree 独立的任务状态，不共享。
# config.py:22 按 ROOT/workspace 解析，各自一份即为隔离。
mkdir -p "${WT_PATH}/workspace/tasks"
info "已建独立 workspace（状态不与主仓库共享）"

cat << EOD

${GRN}就绪${NC} —— 派给 agent 的开场指令：

  cd ${WT_PATH}
  阅读 docs/design/${DOC_NAME}.md 与 docs/design/DEV-PROTOCOL.md，
  按 DEV-PROTOCOL 第 1 节的红绿六步实施 ${TASK}。
  只改该文档「范围」一节声明的文件；范围外的问题记录但不修。

完成后：
  cd ${ROOT_DIR} && git merge ${BRANCH}
  python3 -m pytest tests/unit -q     # 基线 896 passed / 35 errors，多出即回归
  scripts/wave.sh ${TASK} --remove
EOD
