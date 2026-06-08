#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/04-review.md"
echo "[Hard Check] 04-review 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1

# Security audit (existing)
grep -q "## Security" "$FILE" || { echo "❌ 缺少 Security 审计"; exit 1; }

# Route field must be filled and valid.
# 只匹配模板格式的行（以 "- **Route**: `" 开头），避免匹配 agent 输出中
# 的其他 **Route** 引用。使用 -m 1 确保只取第一条匹配。
ROUTE_LINE=$(grep -m 1 '^-\s*\*\*Route\*\*:\s*`' "$FILE" || true)
if [ -z "$ROUTE_LINE" ]; then
    echo "❌ 缺少 **Route** 字段（格式: - **Route**: \`目标阶段\`）"
    exit 1
fi

# 从反引号中提取 Route 值: - **Route**: `05-Archive`
ROUTE_VAL=$(echo "$ROUTE_LINE" | sed -n 's/^.*`\([^`]*\)`.*$/\1/p')
if [ -z "$ROUTE_VAL" ]; then
    echo "❌ **Route** 字段值不能为空（格式: **Route**: \`目标阶段\`）"
    exit 1
fi

# Validate route is one of the allowed values
VALID_ROUTES="05-Archive 03-Coding 02-Planning 01-Brainstorming"
FOUND=0
for r in $VALID_ROUTES; do
    if [ "$r" = "$ROUTE_VAL" ]; then
        FOUND=1
        break
    fi
done
if [ "$FOUND" != "1" ]; then
    echo "❌ **Route** 值 \"$ROUTE_VAL\" 无效。允许值: 05-Archive, 03-Coding, 02-Planning, 01-Brainstorming"
    exit 1
fi

# If reroute (not 05-Archive), evidence table must be present and have data
if [ "$ROUTE_VAL" != "05-Archive" ]; then
    # Check for at least one data row in the Reroute Evidence table
    EVIDENCE_ROWS=$(sed -n '/### Reroute Evidence/,/^##/p' "$FILE" | grep -c '|.*|.*|.*|' || true)
    if [ "$EVIDENCE_ROWS" -lt 3 ]; then
        echo "❌ 返工路由需填写 Reroute Evidence 表（至少一行数据）"
        exit 1
    fi

    # Check that evidence rows have actual content (not all placeholders)
    HAS_DATA=$(sed -n '/### Reroute Evidence/,/^##/p' "$FILE" | grep '|' | tail -n +3 | grep -v '___' | head -1 || true)
    if [ -z "$HAS_DATA" ]; then
        echo "❌ Reroute Evidence 表中不能全是占位符（___），请填写具体问题"
        exit 1
    fi
fi

# Regression tests — skip if already running under pytest (avoid nested test run)
if [ -z "${PYTEST_VERSION:-}" ] && { [ -f "pytest.ini" ] || [ -f "pyproject.toml" ]; }; then
    pytest >/dev/null 2>&1 || { echo "❌ 回归测试失败"; exit 1; }
fi
if [ -f "package.json" ] && grep -q '"test"' "package.json"; then
    npm test >/dev/null 2>&1 || { echo "❌ 回归测试失败"; exit 1; }
fi

# ── README Documentation Validation ──
README_ERRORS=0
README_WARNINGS=0

# 从 STATUS.json 查找任务的 target_dir
TARGET_DIR=$(python3 -c "
import json, sys
try:
    with open('workspace/STATUS.json') as f:
        data = json.load(f)
    td = data.get('tasks', {}).get('${TASK_NAME}', {}).get('target_dir', '')
    if td: print(td)
except: pass
" 2>/dev/null || true)

# 如果 target_dir 是相对路径，加上 repo/ 前缀
if [ -n "$TARGET_DIR" ] && [ ! -d "$TARGET_DIR" ]; then
    if [ -d "repo/$TARGET_DIR" ]; then
        TARGET_DIR="repo/$TARGET_DIR"
    fi
fi

if [ -n "$TARGET_DIR" ] && [ -d "$TARGET_DIR" ]; then
    echo "[README Check] 验证文档: $TARGET_DIR/README.md"
    README_FILE="$TARGET_DIR/README.md"

    if [ ! -f "$README_FILE" ]; then
        echo "⚠️  $TARGET_DIR/ 下无 README.md"
        README_WARNINGS=$((README_WARNINGS + 1))
    else
        # 检查占位符（软告警）
        PLACEHOLDERS=$(grep -c 'TODO\|___\|FIXME' "$README_FILE" 2>/dev/null || echo 0)
        if [ "$PLACEHOLDERS" -gt 0 ]; then
            echo "⚠️  README 中包含 $PLACEHOLDERS 个占位符(TODO/___/FIXME)"
            README_WARNINGS=$((README_WARNINGS + 1))
        fi

        # 检测 CLI 入口 → 验证 README 中的命令
        CLI_CMDS=""
        if [ -f "$TARGET_DIR/pyproject.toml" ]; then
            CLI_CMDS=$(python3 -c "
import re, sys
try:
    with open('$TARGET_DIR/pyproject.toml') as f:
        in_scripts = False
        for line in f:
            line = line.strip()
            if line == '[project.scripts]':
                in_scripts = True
                continue
            if in_scripts:
                if line.startswith('['):
                    break
                m = re.match(r'^(\w+)\s*=\s*', line)
                if m:
                    print(m.group(1))
except Exception:
    pass
" 2>/dev/null || true)
        elif [ -f "$TARGET_DIR/package.json" ]; then
            CLI_CMDS=$(python3 -c "
import json
try:
    with open('$TARGET_DIR/package.json') as f:
        d = json.load(f)
    for k in d.get('bin', {}):
        print(k)
except: pass
" 2>/dev/null || true)
        fi

        if [ -n "$CLI_CMDS" ]; then
            for cmd in $CLI_CMDS; do
                # 检查 README 是否用到了这个命令
                if grep -qE "\`${cmd}\b" "$README_FILE" 2>/dev/null || grep -qE "^\s*${cmd}\s" "$README_FILE" 2>/dev/null; then
                    echo "  → README 引用命令: $cmd"
                    # 验证命令已安装
                    if ! command -v "$cmd" >/dev/null 2>&1; then
                        echo "  ❌ 命令 '$cmd' 未安装（pip install -e . 或 npm link 后重试）"
                        README_ERRORS=$((README_ERRORS + 1))
                    else
                        # 验证 help 正常输出
                        if ! "$cmd" --help >/dev/null 2>&1; then
                            echo "  ❌ 命令 '$cmd --help' 执行失败"
                            README_ERRORS=$((README_ERRORS + 1))
                        else
                            echo "  ✓ 命令 '$cmd' 可用"
                        fi
                    fi
                fi
            done
        fi
    fi
else
    echo "[README Check] ⚠️  无法确定目标目录（target_dir 为空），跳过 README 校验"
fi

# 如果目标走向 Archive 但 README 有错误 → 硬阻断
if [ "$ROUTE_VAL" = "05-Archive" ] && [ "$README_ERRORS" -gt 0 ]; then
    echo "❌ README 文档存在 $README_ERRORS 个错误，不能推进到归档阶段。请修复后重试。"
    exit 1
fi

if [ "$README_ERRORS" -gt 0 ] || [ "$README_WARNINGS" -gt 0 ]; then
    echo "[README Check] 发现 $README_ERRORS 个错误, $README_WARNINGS 个警告"
fi

echo "[Hard Check] ✅ 通过"
