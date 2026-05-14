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
用法: harness/pr.sh <task-name> [选项]

从 worktree 提交变更并创建 Pull Request。

Args:
  <task-name>  任务名 (与 dispatch 时一致)

Options:
  -m, --message <msg>    PR 标题 (默认从 task.yaml 读取)
  --no-commit            跳过 commit，仅创建 PR
  --help                 显示帮助
EOF
    exit 1
}

TASK_NAME=""; PR_TITLE=""; SKIP_COMMIT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--message) PR_TITLE="$2"; shift 2 ;;
        --no-commit) SKIP_COMMIT=true; shift ;;
        --help) usage ;;
        *)
            if [[ -z "$TASK_NAME" ]]; then TASK_NAME="$1"
            else error "多余的参数: $1"; usage
            fi
            shift ;;
    esac
done

[[ -z "$TASK_NAME" ]] && usage

# ── 定位 worktree ──
# 规范化任务名
SANITIZED_TASK="$(echo "$TASK_NAME" | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9._-]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
WORKTREE_PATH="${WORKTREE_DIR}/${SANITIZED_TASK}"

if [[ ! -d "$WORKTREE_PATH" ]]; then
    error "Worktree 不存在: ${WORKTREE_PATH}"
    echo "可用 worktree:"
    git -C "$ROOT_DIR" worktree list
    exit 1
fi

# ── 读取任务元数据 ──
YAML="${WORKTREE_PATH}/.harness/task.yaml"
PROJECT=""; BRANCH=""
if [[ -f "$YAML" ]]; then
    PROJECT="$(grep -r 'project:' "$YAML" 2>/dev/null | head -1 | sed 's/.*project: //' | tr -d '"' || true)"
    BRANCH="$(grep -r 'branch:' "$YAML" 2>/dev/null | head -1 | sed 's/.*branch: //' | tr -d '"' || true)"
fi

if [[ -z "$BRANCH" ]]; then
    # 从 worktree 获取当前分支
    BRANCH="$(git -C "$WORKTREE_PATH" rev-parse --abbrev-ref HEAD)"
fi

# ── 提交变更 ──
if ! $SKIP_COMMIT; then
    title "提交变更: ${SANITIZED_TASK}"

    cd "$WORKTREE_PATH"

    if git diff --quiet && git diff --cached --quiet && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
        warn "没有未提交的变更"
        echo "使用 --no-commit 跳过此检查"
        echo ""
    else
        if [[ -z "$PR_TITLE" ]]; then
            PR_TITLE="[${PROJECT}] ${TASK_NAME}"
        fi

        git add -A
        git commit -m "${PR_TITLE}" --allow-empty -q
        info "已提交: ${PR_TITLE}"

        info "推送到远程..."
        git push -u origin "$BRANCH" -q
        info "推送完成"
    fi

    cd "$ROOT_DIR"
fi

# ── 创建 PR ──
title "创建 Pull Request"

if [[ -z "$PR_TITLE" ]]; then
    PR_TITLE="[${PROJECT:-${SANITIZED_TASK}}] ${TASK_NAME}"
fi

# 生成 PR body
BODY="## 任务
$(cat << BODYEOF
- **项目:** ${PROJECT:-未指定}
- **分支:** ${BRANCH}
- **任务:** ${TASK_NAME}
$(if [[ -f "$WORKTREE_PATH/workflow/current-context.md" ]]; then echo "- 上下文: workflow/current-context.md"; fi)
BODYEOF
)

## 变更概览
\`\`\`
$(git -C "$WORKTREE_PATH" diff "origin/${HARNESS_BASE_BRANCH:-dev}..." --stat 2>/dev/null || echo "（无法获取变更统计）")
\`\`\`

---

_Harness-Engineering 自动创建_"

if ! command -v gh &>/dev/null; then
    error "需要 GitHub CLI (gh) 来创建 PR"
    echo "请手动创建 PR:"
    echo "  Branch: ${BRANCH}"
    echo "  Title:  ${PR_TITLE}"
    exit 1
fi

INFO="$(gh pr create \
    --repo "$(git -C "$ROOT_DIR" remote get-url origin 2>/dev/null | sed 's/.*github.com[:\/]//;s/\.git$//')" \
    --base "${HARNESS_BASE_BRANCH:-dev}" \
    --head "$BRANCH" \
    --title "$PR_TITLE" \
    --body "$BODY" 2>&1)"

echo "$INFO"

# ── 更新元数据 ──
if [[ -f "$YAML" ]]; then
    sed -i '' "s/status:.*/status: pr_created/" "$YAML" 2>/dev/null || true
fi

echo ""
info "PR 已创建"
