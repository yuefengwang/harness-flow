#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/harness/config.sh"

WORKTREE_DIR="${ROOT_DIR}/${HARNESS_WORKTREE_DIR}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
error() { printf "${RED}[✗]${NC} %s\n" "$*"; }
title() { printf "\n${BLUE}━━━ %s ━━━${NC}\n" "$*"; }

title "Harness 活跃 Worktree"

# ── 解析 git worktree 列表 ──
worktrees=()
while IFS= read -r line; do
    worktrees+=("$line")
done < <(git -C "$ROOT_DIR" worktree list 2>/dev/null)

if [[ ${#worktrees[@]} -eq 0 ]]; then
    warn "没有 worktree"
    exit 0
fi

# ── 表头 ──
printf "${CYAN}%-30s %-22s %-14s %-12s %s${NC}\n" "TASK" "BRANCH" "PROJECT" "AGENT" "STATUS"
printf "${CYAN}%-30s %-22s %-14s %-12s %s${NC}\n" "$(printf '─%.0s' {1..30})" "$(printf '─%.0s' {1..22})" "$(printf '─%.0s' {1..14})" "$(printf '─%.0s' {1..12})" "$(printf '─%.0s' {1..20})"

for line in "${worktrees[@]}"; do
    # Parse: <path> <commit> [<branch>]
    path="$(echo "$line" | awk '{print $1}')"
    branch="$(echo "$line" | awk '{print $3}' | tr -d '[]')"
    bare_path="${path#${WORKTREE_DIR}/}"

    # 跳过主 repo 自身
    if [[ "$path" == "$ROOT_DIR" ]]; then
        continue
    fi

    # 读取 task.yaml
    task_name="$bare_path"
    project=""
    agent=""
    status=""
    if [[ -f "${path}/.harness/task.yaml" ]]; then
        project="$(grep -r 'project:' "${path}/.harness/task.yaml" 2>/dev/null | head -1 | sed 's/.*project: //' | tr -d '"' || true)"
        agent="$(grep -r 'agent:' "${path}/.harness/task.yaml" 2>/dev/null | head -1 | sed 's/.*agent: //' | tr -d '"' || true)"
        status="$(grep -r 'status:' "${path}/.harness/task.yaml" 2>/dev/null | head -1 | sed 's/.*status: //' | tr -d '"' || true)"
    fi

    project="${project:----}"
    agent="${agent:----}"
    status="${status:----}"

    # 检查是否有正在运行的 tmux session 或 Docker 容器
    session_name="harness-${bare_path}"
    container_name="sw-${bare_path}"
    agent_label="${agent}"
    if command -v tmux &>/dev/null && tmux has-session -t "$session_name" 2>/dev/null; then
        agent_label="${agent}${GREEN}(running)${NC}"
    elif command -v docker &>/dev/null && docker ps --format '{{.Names}}' 2>/dev/null | grep -qF "$container_name"; then
        agent_label="${agent}${GREEN}(running)${NC}"
    fi

    # 截断超长任务名
    display_task="${task_name:0:28}"
    display_branch="${branch#refs/heads/}"
    display_branch="${display_branch:0:20}"

    printf "%-30s %-22s %-14s %-12s %s\n" \
        "${display_task}" \
        "${display_branch}" \
        "${project:0:12}" \
        "${agent_label}" \
        "${status}"
done

echo ""
info "提示: 使用 'harness/pr.sh <task>' 提交 PR，'harness/cleanup.sh <task>' 清理"
