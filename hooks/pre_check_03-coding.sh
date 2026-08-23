#!/usr/bin/env bash
# 03-coding 的阶段前钩子：显式把任务推入 03a（写测试、见证红）。
#
# 由 sw_lib/workflow/base.py 的 _run_pre_hooks() 在每次进入 03 阶段时执行
# （直接 exec，不经 bash —— 所以本文件必须保留执行位）。
#
# 为什么 phase 必须在这里设：见证的判定发生在 post 侧，而 post 侧无法区分
# 「03a 刚写完测试」与「03b 实现没写对」—— 两者都表现为测试红，处置却相反
# （放行 / 拦住）。所以 phase 只能由 harness 显式记录，不能从测试结果反推。
# 详见 tests/unit/workflow/test_red_witness_phase_explicit.py。
#
# 本脚本刻意**只设状态、不跑测试**，因为调用方有两个坑（A2 的 5.1，已核实）：
#   - 失败被吞：check=False 且异常被 except: pass —— 这里报错没人看得见；
#   - 超时 30 秒：跑测试必然撞破。
# 见证与拦截一律放在 hooks/check_03-coding.sh（post 侧，超时 120 秒）。
#
# 退出码恒为 0：失败反正会被吞，硬失败只会让人误以为它有拦截力。

set -u

cd "$(dirname "$0")/.." || exit 0

# shellcheck source=hooks/lib_run_tests.sh
. "$(dirname "$0")/lib_run_tests.sh"

TASK_NAME=${1:-}
[ -n "$TASK_NAME" ] || exit 0

# 开关关闭时不设 phase。否则 .state 里会长出一个 03a，而 post 侧已不再见证 ——
# 留下「进入了见证流程却永不推进」的状态，_stay_for_impl 会据此误判。
# HARNESS_RED_WITNESS 是环境变量层的覆盖，用于单次调试与测试，不必改配置文件。
if [ "${HARNESS_RED_WITNESS:-}" = "0" ]; then
    exit 0
fi
if [ "$(red_witness_enabled)" != "1" ]; then
    exit 0
fi

# --begin 是幂等的：已见证过的任务不会被退回 03a，冻结哈希也不会被抹掉。
# 返工回到 03 的每一轮都会执行到这里，这一点是必须的。
python3 -m sw_lib.workflow.red_witness "$TASK_NAME" --begin || true

exit 0
