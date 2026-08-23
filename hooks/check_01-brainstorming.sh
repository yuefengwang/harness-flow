#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/01-brainstorming.md"
echo "[Hard Check] 01-brainstorming 门禁..."

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1

# 门禁签署读 JSON 状态源，不 grep 阶段文件。
# 旧做法是 `grep -i -q "\[x\] Design approved"` —— agent 在正文里复述一句
# 勾选过的 Gate 就能骗过硬校验；签署状态只存在 .state 里。
./sw state get "$TASK_NAME" 01-brainstorming gate >/dev/null 2>&1 \
    || { echo "❌ 设计未批准（Gate 未签署）"; exit 1; }

echo "[Hard Check] ✅ 通过"
