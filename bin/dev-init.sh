#!/usr/bin/env bash
#
# dev-init.sh — 多项目管理初始化工具
#
# 功能:
#   1. 扫描 repo/ 下的所有项目
#   2. 交互式选择要开发的项目
#   3. 加载项目上下文（README + 目录结构）
#   4. 更新 STATUS.md 当前焦点
#   5. 保存当前项目标记
#
# 用法:
#   ./dev-init.sh             交互式选择
#   ./dev-init.sh --list      列出所有项目
#   ./dev-init.sh <project>   直接指定项目名
#

set -euo pipefail

# 获取脚本真实路径（处理软链接）
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
REAL_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"

ROOT_DIR="$(cd "${REAL_DIR}/.." && pwd)"
REPO_DIR="${ROOT_DIR}/repo"
WORKSPACE_DIR="${ROOT_DIR}/workspace"
CURRENT_PROJECT_FILE="${WORKSPACE_DIR}/.current-project"
CONTEXT_FILE="${WORKSPACE_DIR}/current-context.md"
STATUS_FILE="${WORKSPACE_DIR}/STATUS.md"

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
error() { printf "${RED}[✗]${NC} %s\n" "$*"; }
title() { printf "\n${BLUE}━━━ %s ━━━${NC}\n" "$*"; }

# ── 辅助函数 ──

# 列出所有可用项目
list_projects() {
    if [[ ! -d "$REPO_DIR" ]]; then
        error "repo/ 目录不存在，请先创建项目"
        exit 1
    fi
    projects=()
    for dir in "$REPO_DIR"/*/; do
        [[ -d "$dir" ]] && projects+=("$(basename "$dir")")
    done
    if [[ ${#projects[@]} -eq 0 ]]; then
        error "repo/ 下没有找到任何项目"
        exit 1
    fi
    printf '%s\n' "${projects[@]}"
}

# 加载项目上下文
load_context() {
    local project="$1"
    local project_dir="${REPO_DIR}/${project}"
    local readme_file="${project_dir}/README.md"

    title "加载项目上下文: ${project}"

    # 1. 项目信息头
    {
        echo "# 当前项目: ${project}"
        echo "初始化时间: $(date '+%Y-%m-%d %H:%M:%S')"
        echo ""

        # 2. README
        if [[ -f "$readme_file" ]]; then
            echo "## 📖 项目说明 (README)"
            echo ""
            cat "$readme_file"
            echo ""
        else
            warn "项目 ${project} 没有 README.md"
            echo "*（无 README.md）*"
            echo ""
        fi

        # 3. 目录结构
        echo "## 📁 目录结构"
        echo ""
        echo '```'
        if command -v tree &>/dev/null; then
            tree --dirsfirst -I '.git|__pycache__|node_modules|target|build' "$project_dir" 2>/dev/null || true
        else
            find "$project_dir" -not -path '*/\.*' -not -path '*/__pycache__/*' \
                -not -path '*/node_modules/*' -not -path '*/target/*' \
                -not -path '*/build/*' | head -80
        fi
        echo '```'
        echo ""
    } > "$CONTEXT_FILE"

    info "上下文已保存到: ${CONTEXT_FILE}"
}

# 更新 STATUS.md
update_status() {
    local project="$1"
    local today
    today="$(date '+%Y-%m-%d')"

    if [[ ! -f "$STATUS_FILE" ]]; then
        error "STATUS.md 不存在: ${STATUS_FILE}"
        return 1
    fi

    # 更新当前焦点部分
    local new_focus_block="## 🎯 当前焦点\n- **当前项目:** ${project}\n- **活动任务:** 无\n- **当前阶段:** 就绪"

    # 使用 Python 处理多行替换（更可靠）
    python3 -c "
import re
with open('${STATUS_FILE}', 'r') as f:
    content = f.read()

# 替换当前焦点区域
pattern = r'## 🎯 当前焦点.*?(?=\n## |\Z)'
replacement = '''## 🎯 当前焦点
- **当前项目:** ${project}
- **活动任务:** 无
- **当前阶段:** 就绪
- **初始化日期:** ${today}

'''

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

with open('${STATUS_FILE}', 'w') as f:
    f.write(content)
"

    info "STATUS.md 当前焦点已更新为: ${project}"
}

# ── 主逻辑 ──

main() {
    title "Simple Workflow — dev init"
    echo ""

    # 检查 repo/ 目录
    if [[ ! -d "$REPO_DIR" ]]; then
        warn "repo/ 目录不存在，正在创建..."
        mkdir -p "$REPO_DIR"
        info "repo/ 目录已创建，请放入项目后重新运行"
        exit 0
    fi

    # 获取项目列表
    projects=()
    for dir in "$REPO_DIR"/*/; do
        [[ -d "$dir" ]] && projects+=("$(basename "$dir")")
    done

    if [[ ${#projects[@]} -eq 0 ]]; then
        error "repo/ 下没有找到任何项目"
        echo ""
        echo "请先在 repo/ 下创建项目目录，例如:"
        echo "  repo/my-project/"
        echo "  repo/my-project/README.md"
        exit 1
    fi

    # 确定项目
    local selected=""

    if [[ $# -eq 1 && "$1" == "--list" ]]; then
        echo "可用项目:"
        list_projects
        exit 0
    elif [[ $# -ge 1 && "$1" != "--list" ]]; then
        # 直接指定项目名
        selected="$1"
        found=false
        for p in "${projects[@]}"; do
            [[ "$p" == "$selected" ]] && { found=true; break; }
        done
        if ! $found; then
            error "项目 '${selected}' 不存在于 repo/ 中"
            echo "可用项目:"
            list_projects
            exit 1
        fi
    else
        # 交互式选择
        echo "可用项目列表:"
        echo ""
        for i in "${!projects[@]}"; do
            printf "  %2d) %s\n" $((i+1)) "${projects[$i]}"
        done
        echo ""
        read -r -p "请选择项目编号 [1-${#projects[@]}]: " choice
        if [[ ! "$choice" =~ ^[0-9]+$ ]] || [[ "$choice" -lt 1 || "$choice" -gt "${#projects[@]}" ]]; then
            error "无效选择"
            exit 1
        fi
        selected="${projects[$((choice-1))]}"
    fi

    echo ""
    info "选中项目: ${selected}"

    # ── 执行初始化 ──

    # 1. 保存当前项目标记
    echo "$selected" > "$CURRENT_PROJECT_FILE"
    info "当前项目标记已保存"

    # 2. 加载上下文
    load_context "$selected"

    # 3. 更新 STATUS.md
    update_status "$selected"

    # 4. 汇总
    title "初始化完成"
    echo ""
    echo "  项目:       ${selected}"
    echo "  路径:       ${REPO_DIR}/${selected}"
    echo "  上下文:     ${CONTEXT_FILE}"
    echo "  标记文件:   ${CURRENT_PROJECT_FILE}"
    echo ""
    info "已准备就绪，可以开始开发！"
    echo ""
    echo "下一步: 开始新任务时按照工作流执行:"
    echo "  1. 头脑风暴 → 2. 规划 → 3. 编码 → 4. 评审 → 5. 归档"
}

main "$@"
