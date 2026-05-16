#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/harness/config.sh"

WORKTREE_DIR="${ROOT_DIR}/${HARNESS_WORKTREE_DIR}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
error() { printf "${RED}[✗]${NC} %s\n" "$*"; }
title() { printf "\n${BLUE}━━━ %s ━━━${NC}\n" "$*"; }

usage() {
    cat << 'EOF'
用法: harness/cleanup.sh <task-name> [选项]

清理已完成的 worktree（PR 合并后使用）。

Args:
  <task-name>  任务名 (与 dispatch 时一致)

Options:
  --force      强制清理（即使 PR 未合并）
  --help       显示帮助
EOF
    exit 1
}

TASK_NAME=""; FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        --help) usage ;;
        *)
            if [[ -z "$TASK_NAME" ]]; then TASK_NAME="$1"
            else error "多余的参数: $1"; usage
            fi
            shift ;;
    esac
done

[[ -z "$TASK_NAME" ]] && usage

SANITIZED_TASK="$(echo "$TASK_NAME" | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9._-]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
WORKTREE_PATH="${WORKTREE_DIR}/${SANITIZED_TASK}"

if [[ ! -d "$WORKTREE_PATH" ]]; then
    error "Worktree 不存在: ${WORKTREE_PATH}"
    echo "可用 worktree:"
    git -C "$ROOT_DIR" worktree list
    exit 1
fi

# ── 读取分支 ──
BRANCH="$(git -C "$WORKTREE_PATH" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ -z "$BRANCH" || "$BRANCH" == "HEAD" ]]; then
    YAML="${WORKTREE_PATH}/.harness/task.yaml"
    BRANCH="$(grep -r 'branch:' "$YAML" 2>/dev/null | head -1 | sed 's/.*branch: //' | tr -d '"' || true)"
fi

title "清理 Worktree: ${SANITIZED_TASK}"

# ── 安全检查 ──
if ! $FORCE; then
    # 检查分支是否已合并到 base_branch
    BASE="${HARNESS_BASE_BRANCH:-dev}"
    # 先 fetch
    git -C "$ROOT_DIR" fetch origin "$BASE" 2>/dev/null || true

    if git -C "$ROOT_DIR" branch --merged "origin/${BASE}" 2>/dev/null | grep -qF "$BRANCH"; then
        info "分支 '${BRANCH}' 已合并到 ${BASE}"
    elif git -C "$ROOT_DIR" branch --merged "${BASE}" 2>/dev/null | grep -qF "$BRANCH"; then
        info "分支 '${BRANCH}' 已合并到 ${BASE}"
    else
        error "分支 '${BRANCH}' 尚未合并到 ${BASE}"
        echo "使用 --force 强制清理（将丢失未合并的变更）"
        exit 1
    fi
fi

# ── 清理 ──
# 1. 杀掉关联的 tmux session 或 Docker 容器
SESSION="harness-${SANITIZED_TASK}"
CONTAINER="sw-${SANITIZED_TASK}"
if command -v tmux &>/dev/null; then
    tmux kill-session -t "$SESSION" 2>/dev/null && info "已终止 tmux session: ${SESSION}" || true
fi
if command -v docker &>/dev/null; then
    docker stop "$CONTAINER" 2>/dev/null && info "已停止 Docker 容器: ${CONTAINER}" || true
    docker rm "$CONTAINER" 2>/dev/null || true
fi

# 2. 删除 worktree
info "删除 worktree: ${WORKTREE_PATH}"
git -C "$ROOT_DIR" worktree remove "$WORKTREE_PATH" 2>/dev/null || {
    warn "worktree remove 失败，尝试强制删除"
    git -C "$ROOT_DIR" worktree remove --force "$WORKTREE_PATH" 2>/dev/null || {
        warn "清理 worktree 残留..."
        rm -rf "$WORKTREE_PATH"
        git -C "$ROOT_DIR" worktree prune 2>/dev/null || true
    }
}
info "Worktree 已删除"

# 3. 删除本地分支
if [[ -n "$BRANCH" ]]; then
    # 切回其他分支再删
    CURRENT="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD)"
    if [[ "$CURRENT" == "$BRANCH" ]]; then
        git -C "$ROOT_DIR" checkout "${HARNESS_BASE_BRANCH:-dev}" >/dev/null 2>&1
    fi
    git -C "$ROOT_DIR" branch -D "$BRANCH" 2>/dev/null \
        && info "已删除本地分支: ${BRANCH}" || warn "本地分支 ${BRANCH} 已不存在"
fi

echo ""
title "清理完成"
echo ""
info "Worktree '${SANITIZED_TASK}' 已完全清理"
