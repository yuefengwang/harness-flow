#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workflow/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/04-review.md"
echo "[Hard Check] 04-review 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1
grep -q "## Security" "$FILE" || { echo "❌ 缺少 Security 审计"; exit 1; }

# 回归测试
if [ -f "pytest.ini" ] || [ -f "pyproject.toml" ]; then
    pytest >/dev/null 2>&1 || { echo "❌ 回归测试失败"; exit 1; }
fi
if [ -f "package.json" ] && grep -q '"test"' "package.json"; then
    npm test >/dev/null 2>&1 || { echo "❌ 回归测试失败"; exit 1; }
fi

echo "[Hard Check] ✅ 通过"
