#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/05-archive.md"
echo "[Hard Check] 05-archive 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1
grep -q "## Memory" "$FILE" || echo "⚠️ 缺少 Memory 章节"
grep -q "## Retro" "$FILE" || echo "⚠️ 缺少 Retro 章节"

# README 一致性
if [ -f "README.md" ]; then
    CHANGES=$(git diff HEAD --name-only | grep README.md || true)
    STAGED=$(git diff --staged --name-only | grep README.md || true)
    [ -z "$CHANGES" ] && [ -z "$STAGED" ] && echo "⚠️ README.md 未更新，请确认"
fi

echo "[Hard Check] ✅ 通过"
