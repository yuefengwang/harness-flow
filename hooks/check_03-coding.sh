#!/usr/bin/env bash
set -e
echo "[Hard Check] 03-coding 门禁..."

# shellcheck source=hooks/lib_run_tests.sh
. "$(dirname "$0")/lib_run_tests.sh"

# 只在任务自己的代码目录里跑测试。此前这里直接在 harness 根目录执行 pytest，
# 会递归触发 harness 自身的整套测试（sw advance 因此会无限期挂住）。
TASK_NAME=$1
TARGET_DIR=$(resolve_target_dir "$TASK_NAME")

if [ -z "$TARGET_DIR" ] || [ ! -d "$TARGET_DIR" ]; then
    echo "跳过测试：任务目标目录不存在 (${TARGET_DIR:-未设置})"
    echo "[Hard Check] ✅ 通过"
    exit 0
fi

# 空转拦截：目录存在却没有任何产出，说明这一轮编码什么都没发生。
# 任务 T3 的现场是 agent 直接 "no text/tool in response"，repo/T3 下只有
# .sw-context，钩子却因为「找不到测试就跳过」打印通过 —— 04-review 才发现
# 目录是空的，随后在 03↔04 之间来回返工。空产出必须在 03 就拦住。
if ! has_code_output "$TARGET_DIR"; then
    echo "❌ $TARGET_DIR/ 下没有任何代码产出（本轮编码为空转）"
    echo "   请确认 agent 已按 02-planning 的任务清单写入文件后重试。"
    exit 1
fi

# ── Red 见证（A2）──
#
# 开关关闭时走原有逻辑，行为与加这段之前完全一致（A2 第 11 节的回滚要求）。
#
# 开启时**取代** run_project_tests 而不是叠加：03a 阶段的测试本来就该是
# 失败的，旧逻辑会把那个红判成「pytest 失败」直接拒绝，红绿流程根本走不起来。
if [ "$(red_witness_enabled)" = "1" ]; then
    echo "[Red Witness] 见证 03 阶段红绿流程..."
    if ! python3 -m sw_lib.workflow.red_witness "$TASK_NAME"; then
        exit 1
    fi
    # 未进入见证流程（存量任务 / 返工轮次）时，见证不接管测试执行 ——
    # 「失败的测试不许过闸」这条既有契约仍由 run_project_tests 兑现。
    if [ "$(python3 -m sw_lib.workflow.red_witness "$TASK_NAME" --phase)" = "none" ]; then
        run_project_tests "$TARGET_DIR" "pytest 失败" || exit 1
    fi
else
    run_project_tests "$TARGET_DIR" "pytest 失败" || exit 1
fi

echo "[Hard Check] ✅ 通过"
