#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/04-review.md"
echo "[Hard Check] 04-review 门禁..."

# shellcheck source=hooks/lib_run_tests.sh
. "$(dirname "$0")/lib_run_tests.sh"

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1

# Security audit (existing)
grep -q "## Security" "$FILE" || { echo "❌ 缺少 Security 审计"; exit 1; }

# Route 决策读 JSON 状态源，不 grep 阶段文件。
# 旧做法在文件里找 `- **Route**: \`...\`` 那一行，而 agent 的产出同样可以写出
# 这个形状 —— 「取第一条匹配」与「取最后一条」的分歧正是 Route 重复写入类
# bug 的来源。合法性（阶段名是否有效）已由写入端 stage_state.write_route 保证，
# 这里只需确认决策存在。值已归一化为小写。
ROUTE_VAL=$(./sw state get "$TASK_NAME" 04-review route 2>/dev/null || true)
if [ -z "$ROUTE_VAL" ]; then
    echo "❌ 缺少 Route 决策（请在 TUI 中选择返工目标或批准归档）"
    exit 1
fi

# If reroute (not 05-Archive), evidence table must be present and have data
if [ "$ROUTE_VAL" != "05-archive" ]; then
    # Check for at least one data row in the Reroute Evidence table
    EVIDENCE_ROWS=$(sed -n '/### Reroute Evidence/,/^##/p' "$FILE" | grep -c '|.*|.*|.*|' || true)
    if [ "$EVIDENCE_ROWS" -lt 3 ]; then
        echo "❌ 返工路由需填写 Reroute Evidence 表（至少一行数据）"
        exit 1
    fi

    # Check that evidence rows have actual content (not all placeholders)
    HAS_DATA=$(sed -n '/### Reroute Evidence/,/^##/p' "$FILE" | grep '|' | tail -n +3 | grep -v '___' | head -1 || true)
    if [ -z "$HAS_DATA" ]; then
        echo "❌ Reroute Evidence 表中不能全是占位符（___），请填写具体问题"
        exit 1
    fi
fi

# 回归测试只在任务自己的代码目录里跑。
# 此前是在 harness 根目录直接跑 pytest —— 那会递归执行 harness 自身的整套测试，
# 让 `sw advance` 无限期挂住（PYTEST_VERSION 守卫只在 pytest 内部有效，
# 从普通脚本调用时不生效）。
TEST_DIR=$(resolve_target_dir "$TASK_NAME")
if [ -n "$TEST_DIR" ] && [ -d "$TEST_DIR" ]; then
    run_project_tests "$TEST_DIR" "回归测试失败" || exit 1
else
    echo "跳过回归测试：任务目标目录不存在 (${TEST_DIR:-未设置})"
fi

# ── README Documentation Validation ──
README_ERRORS=0
README_WARNINGS=0

# target_dir 统一从任务 .state 读（与上面跑测试用的是同一个源）。
# 此前这里改读 workspace/STATUS.json —— 那是个汇总缓存，target_dir 未必落进去，
# 于是同一个钩子里 pytest 找得到目录、README 校验却报「target_dir 为空」
# 并静默跳过全部检查。
TARGET_DIR=$(resolve_target_dir "$TASK_NAME")

# 如果 target_dir 是相对路径，加上 repo/ 前缀
if [ -n "$TARGET_DIR" ] && [ ! -d "$TARGET_DIR" ]; then
    if [ -d "repo/$TARGET_DIR" ]; then
        TARGET_DIR="repo/$TARGET_DIR"
    fi
fi

if [ -n "$TARGET_DIR" ] && [ -d "$TARGET_DIR" ]; then
    echo "[README Check] 验证文档: $TARGET_DIR/README.md"
    README_FILE="$TARGET_DIR/README.md"

    if [ ! -f "$README_FILE" ]; then
        # 缺 README 在归档路径上是错误而不是警告 —— hook-04-06 的规则就写着
        # "README.md 存在且非空"。任务 T3 里它只是个 ⚠️，于是钩子照样打印
        # ✅ 通过，用户只能靠反复返工来表达"这里还缺文档"，而返工又不带
        # 真实理由，agent 三轮都没补上。走返工路由时仍算警告：那一轮的目的
        # 本来就是去补东西。
        echo "❌ $TARGET_DIR/ 下无 README.md"
        README_ERRORS=$((README_ERRORS + 1))
    else
        # 检查占位符（软告警）
        # `grep -c` 零匹配时**打印 0 并以退出码 1 结束**，所以旧写法
        # `... || echo 0` 会再追加一个 0，变量成了两行的 "0\n0"，
        # 紧接着的 `[ "$PLACEHOLDERS" -gt 0 ]` 报 integer expression expected。
        # 判定恰好没错（`[` 失败走 else，等价于「无占位符」），
        # 但每次 README 干净时都往 stderr 吐一行 shell 错误。
        PLACEHOLDERS=$(grep -c 'TODO\|___\|FIXME' "$README_FILE" 2>/dev/null | head -1)
        [ -n "$PLACEHOLDERS" ] || PLACEHOLDERS=0
        if [ "$PLACEHOLDERS" -gt 0 ]; then
            echo "⚠️  README 中包含 $PLACEHOLDERS 个占位符(TODO/___/FIXME)"
            README_WARNINGS=$((README_WARNINGS + 1))
        fi

        # 检测 CLI 入口 → 验证 README 中的命令
        CLI_CMDS=""
        if [ -f "$TARGET_DIR/pyproject.toml" ]; then
            CLI_CMDS=$(python3 -c "
import re, sys
try:
    with open('$TARGET_DIR/pyproject.toml') as f:
        in_scripts = False
        for line in f:
            line = line.strip()
            if line == '[project.scripts]':
                in_scripts = True
                continue
            if in_scripts:
                if line.startswith('['):
                    break
                m = re.match(r'^(\w+)\s*=\s*', line)
                if m:
                    print(m.group(1))
except Exception:
    pass
" 2>/dev/null || true)
        elif [ -f "$TARGET_DIR/package.json" ]; then
            CLI_CMDS=$(python3 -c "
import json
try:
    with open('$TARGET_DIR/package.json') as f:
        d = json.load(f)
    for k in d.get('bin', {}):
        print(k)
except: pass
" 2>/dev/null || true)
        fi

        if [ -n "$CLI_CMDS" ]; then
            for cmd in $CLI_CMDS; do
                # 检查 README 是否用到了这个命令
                if grep -qE "\`${cmd}\b" "$README_FILE" 2>/dev/null || grep -qE "^\s*${cmd}\s" "$README_FILE" 2>/dev/null; then
                    echo "  → README 引用命令: $cmd"
                    # 验证命令已安装
                    if ! command -v "$cmd" >/dev/null 2>&1; then
                        echo "  ❌ 命令 '$cmd' 未安装（pip install -e . 或 npm link 后重试）"
                        README_ERRORS=$((README_ERRORS + 1))
                    else
                        # 验证 help 正常输出
                        if ! "$cmd" --help >/dev/null 2>&1; then
                            echo "  ❌ 命令 '$cmd --help' 执行失败"
                            README_ERRORS=$((README_ERRORS + 1))
                        else
                            echo "  ✓ 命令 '$cmd' 可用"
                        fi
                    fi
                fi
            done
        fi
    fi
else
    echo "[README Check] ⚠️  无法确定目标目录（target_dir 为空），跳过 README 校验"
fi

# README 的严重性判定已移交客观轨（A6 的 3.2）。
#
# 这里原本会先判 Route 是否为归档、再判 README 错误数是否大于零
# （原 153 行那个复合条件；此处刻意不复现它的字面形式 —— 守护测试
# test_hook_no_longer_gates_readme_on_route 用源码正则钉这个模式，
# 注释里照抄一遍会让它继续报红，而那条红是对的）。
# 即缺 README 在归档路由下是错误、返工路由下只是警告。那形成一个闭环：
# Route 决定严重性，严重性又决定 Route。客观轨一律按最严标准判，
# 与 Route 无关；返工时的宽容由 A9 仲裁器的优先级顺序体现
# （已在返工路径上，不会因 README 再次返工）。
if [ "$README_ERRORS" -gt 0 ] || [ "$README_WARNINGS" -gt 0 ]; then
    echo "[README Check] 发现 $README_ERRORS 个错误, $README_WARNINGS 个警告（判定见客观轨 O6）"
fi

# ── 客观轨（A6）──
#
# 纯程序判定，零 LLM 调用。它消费 A3 的事实包，产出结构化结果并决定
# 是否硬阻断。三件现有 hook 判不出来的事由它兜住：
#   O3 零测试 / 全部 skip —— 前者当前根本没被检查（无测试面时整段 return 0），
#      后者退出码是 0，只有计数能区分它与真全绿；
#   O6 README —— 按最严标准，不看 Route；
#   O5/O8 前提缺失 —— 记 unavailable，**不计为通过**。
echo "[Objective Track] 客观轨判定..."
OBJ_OUT=$(python3 -c '
import json, sys
sys.path.insert(0, ".")
from sw_lib.workflow import objective_check as OC
from sw_lib.workflow.fact_pack import read_claims

task = sys.argv[1]
target = sys.argv[2]
claims = {}
try:
    claims = read_claims(task) or {}
except Exception:
    pass

r = OC.run_checks(target, files_touched=claims.get("files_touched") or None)
for c in r["checks"]:
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️ ",
            "unavailable": "❓"}.get(c["verdict"], "?")
    tail = c.get("detail") or c.get("reason") or ""
    print(f"  {icon} {c["id"]} {c["name"]}: {c["verdict"]} {tail}")
    # 硬失败时 detail 与 reason **都要打**。原先是 `detail or reason`，
    # 于是同时有两者的项（O6）永远只显示 detail —— 「缺 README」说了是什么，
    # 没说谁去修。任务 qqqq 的两次 /advance 输出逐字相同即由此而来：
    # 拦住一条路却不给替代路径（A2 的 10.6）。
    hint = c.get("reason") or ""
    if c["verdict"] == "fail" and hint and hint != c.get("detail"):
        print(f"     ↳ {hint}")
print("HARD_FAIL_IDS=" + ",".join(r["hard_fail_ids"]))
sys.exit(1 if r["hard_fail"] else 0)
' "$TASK_NAME" "${TEST_DIR:-}" 2>&1) && OBJ_RC=0 || OBJ_RC=$?
echo "$OBJ_OUT" | grep -v '^HARD_FAIL_IDS=' || true
if [ "$OBJ_RC" -ne 0 ]; then
    OBJ_IDS=$(echo "$OBJ_OUT" | grep '^HARD_FAIL_IDS=' | cut -d= -f2)
    echo "❌ 客观轨硬失败: ${OBJ_IDS:-未知}"
    echo "   这些是程序判定的事实，不是意见 —— 修掉再推进。"
    exit 1
fi

echo "[Hard Check] ✅ 通过"
