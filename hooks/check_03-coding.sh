#!/usr/bin/env bash
set -e
echo "[Hard Check] 03-coding 门禁..."

if [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || ls test_*.py 2>/dev/null; then
    echo "运行 pytest..."
    pytest >/dev/null 2>&1 || { echo "❌ pytest 失败"; exit 1; }
fi
if [ -f "package.json" ] && grep -q '"test"' "package.json"; then
    echo "运行 npm test..."
    npm test >/dev/null 2>&1 || { echo "❌ npm test 失败"; exit 1; }
fi

echo "[Hard Check] ✅ 通过"
