#!/usr/bin/env bash
# 在任务的目标目录里跑测试。由 check_03-coding.sh / check_04-review.sh 共用 ——
# 两处逻辑此前是复制粘贴的，修一处漏一处。
#
# 用法: run_project_tests <target_dir> <失败前缀>

# 解析 target_dir：从任务 .state 读。找不到返回空串，由调用方决定是否跳过。
resolve_target_dir() {
    local task_name="$1"
    local state="workspace/tasks/${task_name}/.state"
    [ -n "$task_name" ] && [ -f "$state" ] || return 0
    python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('target_dir',''))" \
        "$state" 2>/dev/null || true
}

# Red 见证开关（A2 第 11 节）。打印 1 = 开启，0 = 关闭。
#
# 默认开启：机制默认可关的意思是「出问题能一键退回」，不是「默认不生效」。
# 关闭后 03 阶段不拆子状态，走原来的 run_project_tests。
red_witness_enabled() {
    python3 -c "
import sys
try:
    import yaml
    with open('config/config.yaml', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    coding = ((cfg.get('harness') or {}).get('coding') or {})
    val = coding.get('red_witness', True)
except Exception:
    val = True
print('1' if val else '0')
" 2>/dev/null || echo 1
}

# target_dir 里除了 harness 自己的记账文件之外，有没有真实产出。
# 返回 0 = 有产出，1 = 空。
#
# 存在的理由：03-coding 完全空转也能过闸（任务 T3）。agent 一句话没说就
# 结束，repo/T3 下只有 create_task 写的 .sw-context，而当时的钩子看到没有
# pyproject.toml/test_*.py 就整段跳过测试并打印「✅ 通过」，用户签了门禁就
# 推进到 04 —— 空转的代价被推迟到评审阶段才发现，然后开始来回返工。
has_code_output() {
    local dir="$1"
    [ -n "$dir" ] && [ -d "$dir" ] || return 1
    local entry base
    for entry in "$dir"/* "$dir"/.[!.]*; do
        [ -e "$entry" ] || continue
        base=$(basename "$entry")
        case "$base" in
            # harness 记账、虚拟环境、缓存：都不算 agent 的产出
            .sw-context|.git|.venv|venv|__pycache__|.pytest_cache|\
            .DS_Store|node_modules|*.egg-info) continue ;;
        esac
        return 0
    done
    return 1
}

# src-layout（源码在 src/ 下但包未安装）时，pytest 收集期就会
# ModuleNotFoundError。agent 自己是用 PYTHONPATH=src 跑通的，hook 不加就会
# 得出与 agent 相反的结论 —— 任务 T2 因此卡死：agent 报 25 passed，
# 门禁报 pytest 失败，且用户无从下手。
_pytest_pythonpath() {
    local dir="$1"
    if [ -d "$dir/src" ]; then
        echo "src"
    fi
}

# 项目自带的虚拟环境解释器。没有则返回空串，由调用方回落到 python3。
#
# agent 常常在 target_dir 下 `python -m venv .venv` 再装依赖，然后用那个
# 解释器跑通测试。钩子用 harness 的 python3 就会 ModuleNotFoundError（任务
# T3：pandas 装在 repo/T3/.venv 里，门禁却报缺 pandas）—— 又一次门禁与
# agent 对同一份代码给出相反结论。
_project_python() {
    local dir="$1"
    local candidate
    for candidate in "$dir/.venv/bin/python" "$dir/venv/bin/python"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 0
}

run_project_tests() {
    local dir="$1"
    local fail_label="${2:-pytest 失败}"

    if [ -f "$dir/pytest.ini" ] || [ -f "$dir/pyproject.toml" ] \
       || ( cd "$dir" && ls test_*.py >/dev/null 2>&1 ); then
        echo "运行 pytest ($dir)..."
        local extra_path
        extra_path=$(_pytest_pythonpath "$dir")
        local py
        py=$(_project_python "$dir")
        if [ -n "$py" ]; then
            # 相对路径要在 cd 之前转成绝对路径，否则子 shell 里找不到。
            case "$py" in
                /*) ;;
                *) py="$(pwd)/$py" ;;
            esac
            echo "  使用项目虚拟环境: $py"
        else
            py="python3"
        fi
        local out
        # 用 python3 -m pytest 而不是裸 pytest：PATH 上的 pytest 脚本可能绑在
        # 另一个解释器上（本机是 3.9，而 python3 是 3.12），跑出来的结果和
        # agent 看到的不一致。
        if ! out=$(cd "$dir" && PYTHONPATH="${extra_path}${extra_path:+:}${PYTHONPATH}" \
                    "$py" -m pytest 2>&1); then
            echo "❌ $fail_label"
            # 失败原因必须回显。此前是 >/dev/null 2>&1 全丢弃，用户只看到
            # 「pytest 失败」四个字，反复 /advance 也不知道要改什么。
            echo "$out" | tail -25 | sed 's/^/    /'
            return 1
        fi
        # 成功时也要回显计数（A3 的 2.5）。此前 out 在成功分支被整个丢弃，
        # collected / passed / skipped 全部丢失，A6 的 O3 无从判定 ——
        # 「全部 skip」与「真的全绿」在退出码上都是 0，只有计数能区分。
        echo "$out" | grep -E '(passed|failed|skipped|no tests ran)' | tail -3 \
            | sed 's/^/    /' || true
    else
        # 没有 pytest 语义的测试面。此前这里什么都不打印就 return 0，
        # 于是「没跑」和「跑过且全绿」在输出上无从区分（A3 的 2.4）。
        # 判定不在这里做（属 A6），但事实必须说出来。
        echo "    ⓘ 未发现 pytest 测试面（未执行 pytest，非『通过』）"
    fi

    if [ -f "$dir/package.json" ] && grep -q '"test"' "$dir/package.json"; then
        echo "运行 npm test ($dir)..."
        local out
        if ! out=$(cd "$dir" && npm test 2>&1); then
            echo "❌ $fail_label"
            echo "$out" | tail -25 | sed 's/^/    /'
            return 1
        fi
    fi
    return 0
}
