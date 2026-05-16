#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/01-brainstorming.md"
echo "[Hard Check] 01-brainstorming 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1
grep -q "## Gate" "$FILE" || { echo "❌ 缺少 Gate 章节"; exit 1; }
grep -i -q "\[x\] Design approved" "$FILE" || { echo "❌ 设计未批准 ([x] Design approved)"; exit 1; }

echo "[Hard Check] ✅ 通过"
