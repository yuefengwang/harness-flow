#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/harness/config.sh"

WORKTREE_DIR="${ROOT_DIR}/${HARNESS_WORKTREE_DIR}"
BASE_BRANCH="${HARNESS_BASE_BRANCH}"
BRANCH_PREFIX="${HARNESS_BRANCH_PREFIX}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
error() { printf "${RED}[✗]${NC} %s\n" "$*"; }
title() { printf "\n${BLUE}━━━ %s ━━━${NC}\n" "$*"; }

usage() {
    cat << 'EOF'
用法: harness/dispatch.sh <project> <task-name> [选项]

派发一个新任务到独立的 git worktree 中，自动初始化项目上下文。

Args:
  <project>    项目名 (repo/ 下的子目录，或 simple-workflow 表示平台自身)
  <task-name>  任务名 (用作分支名和 worktree 目录名)

Options:
  --agent claude|gemini|opencode   指定 AI Agent (默认: claude)
  --base <branch>                 指定基准分支 (默认: dev)
  --launch                        自动启动 AI Agent (tmux 或 Docker 后台)
  --docker                        AI Agent 运行在 Docker 容器内
  --help                          显示帮助

示例:
  harness/dispatch.sh sample-java-app add-auth
  harness/dispatch.sh simple-workflow update-template --agent opencode --launch
EOF
    exit 1
}

# ── 解析参数 ──
PROJECT=""; TASK_NAME=""; AGENT="$HARNESS_DEFAULT_AGENT"; LAUNCH=false; DOCKER_MODE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent) AGENT="$2"; shift 2 ;;
        --base) BASE_BRANCH="$2"; shift 2 ;;
        --launch) LAUNCH=true; shift ;;
        --docker) DOCKER_MODE=true; shift ;;
        --help) usage ;;
        *)
            if [[ -z "$PROJECT" ]]; then PROJECT="$1"
            elif [[ -z "$TASK_NAME" ]]; then TASK_NAME="$1"
            else error "多余的参数: $1"; usage
            fi
            shift ;;
    esac
done

[[ -z "$PROJECT" || -z "$TASK_NAME" ]] && usage

# ── Docker 预检查（在创建 worktree 之前） ──
DOCKER_IMAGE="sw-agent:${AGENT}"
if $DOCKER_MODE; then
    if ! command -v docker &>/dev/null; then
        error "未安装 Docker，无法使用 --docker 模式"
        exit 1
    fi
    if ! docker image inspect "$DOCKER_IMAGE" &>/dev/null 2>&1; then
        error "Docker 镜像不存在: ${DOCKER_IMAGE}"
        echo "请先构建: bash docker/build.sh ${AGENT}"
        exit 1
    fi
fi

# ── 校验 ──
IS_PLATFORM=false
if [[ "$PROJECT" == "simple-workflow" ]]; then
    IS_PLATFORM=true
elif [[ ! -d "$ROOT_DIR/repo/$PROJECT" ]]; then
    error "项目 '$PROJECT' 不存在于 repo/"
    echo "可用项目:"
    ls -1 "$ROOT_DIR/repo/"
    echo "也可用: simple-workflow (平台自身)"
    exit 1
fi

# 规范化分支名
SANITIZED_TASK="$(echo "$TASK_NAME" | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9._-]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
BRANCH="${BRANCH_PREFIX}/${PROJECT}/${SANITIZED_TASK}"
WORKTREE_PATH="${WORKTREE_DIR}/${SANITIZED_TASK}"

if git -C "$ROOT_DIR" worktree list --porcelain 2>/dev/null \
    | grep -qF "worktree ${WORKTREE_PATH}"; then
    error "Worktree '${SANITIZED_TASK}' 已存在"
    exit 1
fi

if git -C "$ROOT_DIR" rev-parse --verify "$BRANCH" &>/dev/null 2>&1; then
    error "分支 '${BRANCH}' 已存在"
    exit 1
fi

# ── 创建 Worktree ──
title "派发任务: ${TASK_NAME}"

# 确保 worktree 父目录存在
mkdir -p "$WORKTREE_DIR"

# 创建分支 + worktree（原子操作，不触犯主 repo checkout）
info "创建 worktree: ${WORKTREE_PATH} [${BRANCH}] ← ${BASE_BRANCH}"
git -C "$ROOT_DIR" worktree add -b "$BRANCH" "$WORKTREE_PATH" "$BASE_BRANCH" >/dev/null 2>&1

# ── 初始化项目上下文 ──
# ── 同步基础设施（worktree 是基于 commit 的快照，未提交的文件需手动复制） ──
for item in dev-init.sh sw task-state.yaml harness hooks docker repo .gitignore README.md GEMINI.md; do
    if [[ -e "$ROOT_DIR/$item" ]]; then
        cp -R "$ROOT_DIR/$item" "${WORKTREE_PATH}/" 2>/dev/null || true
    fi
done

# ── 初始化项目上下文 ──
if $IS_PLATFORM; then
    info "初始化平台上下文: simple-workflow"
    mkdir -p "${WORKTREE_PATH}/workflow"
    cat > "${WORKTREE_PATH}/workflow/current-context.md" << PEOF
# 当前项目: simple-workflow (平台自身)
初始化时间: $(date '+%Y-%m-%d %H:%M:%S')

## 开发目标
正在开发 simple-workflow 平台自身，包括 harness/、workflow/ 等模块。

## 平台架构
- \`harness/\` — 调度层 (dispatch, status, pr, cleanup)
- \`workflow/\` — 执行层 (模板, 任务, 看板)
- \`docker/\` — 容器隔离
- \`dev-init.sh\` — 项目初始化
PEOF
    # 平台模式下 STATUS.md 焦点
    sed -i '' 's/当前项目:.*/当前项目: simple-workflow (平台自身)/' "${WORKTREE_PATH}/workflow/STATUS.md" 2>/dev/null || true
else
    "${WORKTREE_PATH}/dev-init.sh" "$PROJECT" >/dev/null 2>&1
fi

# ── 任务元数据 ──
mkdir -p "${WORKTREE_PATH}/.harness"
cat > "${WORKTREE_PATH}/.harness/task.yaml" << TASKEOP
task:
  name: "${TASK_NAME}"
  project: "${PROJECT}"
  branch: "${BRANCH}"
  agent: "${AGENT}"
  docker: ${DOCKER_MODE}
  status: initialized
  created_at: $(date '+%Y-%m-%d %H:%M:%S')
  base_branch: "${BASE_BRANCH}"
TASKEOP

# ── 输出 ──
title "Worktree 就绪"
echo ""
echo "  项目:      ${PROJECT}"
echo "  任务:      ${TASK_NAME}"
echo "  分支:      ${BRANCH}"
echo "  Worktree:  ${WORKTREE_PATH}"
echo "  Agent:     ${AGENT}"
echo ""

# ── 自动启动 (可选) ──

DOCKER_IMAGE="sw-agent:${AGENT}"
CONTAINER_NAME="sw-${SANITIZED_TASK}"

if $DOCKER_MODE; then
    DOCKER_OPTS=(
        --rm
        --name "$CONTAINER_NAME"
        -v "${WORKTREE_PATH}:/workspace"
        -w /workspace
    )

    # Git 凭证
    [[ -f "$HOME/.gitconfig" ]] && DOCKER_OPTS+=(-v "$HOME/.gitconfig:/root/.gitconfig:ro")
    [[ -d "$HOME/.ssh" ]] && DOCKER_OPTS+=(-v "$HOME/.ssh:/root/.ssh:ro")

    # Agent 凭证 — 优先从 credentials.yaml 读取，其次 fallback 到环境变量
    read_credential() {
        python3 -c "
import yaml, os
try:
    with open('${ROOT_DIR}/harness/credentials.yaml') as f:
        cfg = yaml.safe_load(f)
    agent_cfg = cfg.get('agents', {}).get('${AGENT}', {})
    keys = '${1}'.split('.')
    cred = agent_cfg
    for k in keys:
        cred = cred.get(k, {})
    if isinstance(cred, dict):
        if 'env' in cred:
            print(os.environ.get(cred['env'], ''))
        elif 'value' in cred:
            print(os.path.expanduser(cred['value']))
        elif 'mount' in cred:
            print(os.path.expanduser(cred['mount']))
except: pass
" 2>/dev/null
    }

    case "$AGENT" in
        claude)
            API_KEY=$(read_credential "api_key")
            [[ -n "${API_KEY:-}" ]] && DOCKER_OPTS+=(-e "ANTHROPIC_API_KEY=${API_KEY}")
            [[ -z "${API_KEY:-}" && -n "${ANTHROPIC_API_KEY:-}" ]] && DOCKER_OPTS+=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
            ;;
        gemini)
            API_KEY=$(read_credential "api_key")
            [[ -n "${API_KEY:-}" ]] && DOCKER_OPTS+=(-e "GOOGLE_API_KEY=${API_KEY}")
            [[ -z "${API_KEY:-}" && -n "${GOOGLE_API_KEY:-}" ]] && DOCKER_OPTS+=(-e "GOOGLE_API_KEY=${GOOGLE_API_KEY}")
            ;;
        opencode)
            CONFIG_DIR=$(read_credential "config_dir")
            [[ -d "${CONFIG_DIR:-}" ]] && DOCKER_OPTS+=(-v "${CONFIG_DIR}:/root/.config/opencode:ro")
            [[ -z "${CONFIG_DIR:-}" && -d "$HOME/.config/opencode" ]] && DOCKER_OPTS+=(-v "$HOME/.config/opencode:/root/.config/opencode:ro")
            ;;
    esac

    if $LAUNCH; then
        info "在 Docker 容器 '${CONTAINER_NAME}' 中后台启动 ${AGENT}"
        docker run -d "${DOCKER_OPTS[@]}" "$DOCKER_IMAGE" \
            sh -c "${AGENT} . 2>&1 | tee -a /workspace/.harness/agent.log"
        info "查看日志: docker logs -f ${CONTAINER_NAME}"
        info "进入容器: docker exec -it ${CONTAINER_NAME} bash"
    else
        echo "启动 Docker Agent:"
        echo "  docker run -it ${DOCKER_OPTS[@]} ${DOCKER_IMAGE} ${AGENT} ."
    fi

elif $LAUNCH; then
    # ── tmux 模式 ──
    if ! command -v tmux &>/dev/null; then
        warn "tmux 未安装，无法自动启动"
        echo "手动启动: cd ${WORKTREE_PATH} && ${AGENT} ."
        exit 0
    fi
    SESSION="harness-${SANITIZED_TASK}"
    info "在 tmux session '${SESSION}' 中启动 ${AGENT}"
    tmux new-session -d -s "$SESSION" -c "$WORKTREE_PATH" \
        "${AGENT} . 2>&1 | tee -a ${WORKTREE_PATH}/.harness/agent.log"
    info "连接: tmux attach -t ${SESSION}"

else
    echo "启动 AI Agent:"
    echo "  cd ${WORKTREE_PATH} && ${AGENT} ."
fi

echo ""
info "提示: 使用 'harness/status.sh' 查看所有活跃 worktree"
