#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workflow/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/02-planning.md"
echo "[Hard Check] 02-planning 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1
grep -q "## Task DAG" "$FILE" || { echo "❌ 缺少 Task DAG"; exit 1; }
grep -q "## Test Strategy" "$FILE" || { echo "❌ 缺少 Test Strategy"; exit 1; }

echo "[Hard Check] ✅ 通过"
