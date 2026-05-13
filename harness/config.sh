# Harness 平台配置（被各脚本 source）
# 也可通过环境变量覆盖

# Worktree 存放目录
HARNESS_WORKTREE_DIR="${HARNESS_WORKTREE_DIR:-.worktrees}"

# 基准分支
HARNESS_BASE_BRANCH="${HARNESS_BASE_BRANCH:-dev}"

# 分支前缀
HARNESS_BRANCH_PREFIX="${HARNESS_BRANCH_PREFIX:-harness}"

# 默认 AI Agent (claude | gemini | opencode)
HARNESS_DEFAULT_AGENT="${HARNESS_DEFAULT_AGENT:-claude}"

# Docker 镜像前缀
HARNESS_DOCKER_IMAGE_PREFIX="${HARNESS_DOCKER_IMAGE_PREFIX:-sw-agent}"
