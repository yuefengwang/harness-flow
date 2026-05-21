#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/04-review.md"
echo "[Hard Check] 04-review 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1

# Security audit (existing)
grep -q "## Security" "$FILE" || { echo "❌ 缺少 Security 审计"; exit 1; }

# Route field must be filled and valid
ROUTE_LINE=$(grep '\*\*Route\*\*' "$FILE" || true)
if [ -z "$ROUTE_LINE" ]; then
    echo "❌ 缺少 **Route** 字段"
    exit 1
fi

# Extract route value from backtick-quoted string: **Route**: `05-Archive`
ROUTE_VAL=$(echo "$ROUTE_LINE" | sed -n 's/.*`\([^`]*\)`.*/\1/p')
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

echo "[Hard Check] ✅ 通过"
