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

# 产出区必须有实质内容。
#
# 此前本钩子只验「文件存在 + gate 已签署」，于是任务 helloworld 在模板区
# 全是 `___` 的情况下过闸并推进到 03 —— 门禁只看签署、不看内容。
# 判据落在 sw 写的围栏区（nonce 不可预测），不看模板区：agent 无权改
# 阶段文件（硬层对 workspace/** 的 write 一律 deny）。
python3 -m sw_lib.workflow.output_check "$TASK_NAME" 01-brainstorming || exit 1

echo "[Hard Check] ✅ 通过"
