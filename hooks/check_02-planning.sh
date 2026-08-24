#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/02-planning.md"
echo "[Hard Check] 02-planning 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1

# 这两个 grep 判的是**模板结构**是否还在（防止文件被整体覆盖），
# 不是判 agent 干了什么 —— 标题是模板自带的，任何情况下都在。
grep -q "## Task DAG" "$FILE" || { echo "❌ 缺少 Task DAG"; exit 1; }
grep -q "## Test Strategy" "$FILE" || { echo "❌ 缺少 Test Strategy"; exit 1; }

# 门禁签署：此前本钩子**完全没验**，未签署的 gate 也能过闸（实测）。
# 与 01 同一写法：读 JSON 状态源，不 grep 阶段文件。
./sw state get "$TASK_NAME" 02-planning gate >/dev/null 2>&1 \
    || { echo "❌ 规划未批准（Gate 未签署）"; exit 1; }

# 产出区必须有实质内容（理由同 check_01-brainstorming.sh）。
python3 -m sw_lib.workflow.output_check "$TASK_NAME" 02-planning || exit 1

echo "[Hard Check] ✅ 通过"
