#!/usr/bin/env python3
"""sw CLI 回归测试"""
import json, subprocess, sys, os, shutil
from pathlib import Path

# 项目根目录 (harness-flow/)
ROOT = Path(__file__).resolve().parent.parent.parent
SW = str(ROOT / "sw")
TASKS = ROOT / "workspace" / "tasks"
passed = 0
failed = 0

def run(cmd, expected_exit=0, desc=""):
    global passed, failed
    # 强制开启非交互模式
    env = os.environ.copy()
    env["SW_NON_INTERACTIVE"] = "1"
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10, env=env)
    exit_ok = result.returncode == expected_exit
    marker = "✓" if exit_ok else "✗"
    if exit_ok:
        passed += 1
    else:
        failed += 1
        print(f"  [{marker}] {desc}")
        print(f"       cmd: {cmd}")
        print(f"       exit: {result.returncode} (expected {expected_exit})")
        print(f"       stderr: {result.stderr}")
        print(f"       stdout: {result.stdout}")
    return result

def cleanup():
    if (TASKS / "test-cli").exists():
        shutil.rmtree(TASKS / "test-cli")
    # init 还会创建 repo/<name> 与 STATUS.json 条目；不清掉会污染真实工作区，
    # 并让后续 `sw status` 指向一个读不出 .state 的幽灵任务。
    repo_dir = ROOT / "repo" / "test-cli"
    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)
    status = ROOT / "workspace" / "STATUS.json"
    if status.exists():
        try:
            data = json.loads(status.read_text(encoding="utf-8"))
            if data.get("tasks", {}).pop("test-cli", None) is not None:
                status.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
        except (ValueError, OSError):
            pass


def sign(stage, route=None):
    """签署门禁（写 .state）。

    门禁凭据只存在 .state 里，改 Markdown 里的 `[ ]` 不再有任何效力 ——
    那正是 agent 能伪造的东西。这里走和 TUI 的 [A] 相同的入口。
    """
    sys.path.insert(0, str(ROOT))
    from sw_lib.workflow import stage_state as ss
    assert ss.sign_gate("test-cli", stage), f"{stage}: 签署失败"
    if route:
        assert ss.write_route("test-cli", route), f"{stage}: 路由写入失败"


def write_sample_output():
    """在 target_dir 下写出最小产出 —— 模拟 agent 完成了编码。

    03-coding 的硬校验拒绝空产出，04-review 走归档路由时要求 README 存在。
    这两道闸门都是 T3 空转事故的产物：不落地文件就没有可推进的东西。
    """
    target = ROOT / "repo" / "test-cli"
    target.mkdir(parents=True, exist_ok=True)
    (target / "cli_demo.py").write_text("def run():\n    return 0\n", encoding="utf-8")
    (target / "README.md").write_text("# test-cli\n\n用法说明。\n", encoding="utf-8")


def resolve_choices(stage):
    """填掉 `- **Chosen**: ___` 占位符，模拟 agent 记录已拍板的方案。

    选项组属于**内容检查**而不是门禁：它检查 agent 是否把用户的决定写进了
    产出，因此仍然读 Markdown，也仍然必须真的填。
    """
    tpl = TASKS / "test-cli" / f"{stage}.md"
    if not tpl.exists():
        return
    tpl.write_text(tpl.read_text().replace("- **Chosen**: ___",
                                           "- **Chosen**: A"),
                   encoding="utf-8")


# ── 开始测试 ──
cleanup()

# ── 1. usage / help ──
print("── 1. usage / help ──")
run(f"python3 {SW}", 1, "no args should fail")
run(f"python3 {SW} --help", 0, "--help should work")
run(f"python3 {SW} help", 0, "help subcommand should work")

# ── 2. init CLI ──
print("\n── 2. init CLI ──")
run(f"python3 {SW} init --name=test-cli --context='test' --type=feature", 0, "init feature")
assert (TASKS / "test-cli").is_dir(), "task dir should exist"
assert (TASKS / "test-cli" / ".state").exists(), ".state should exist"
assert (TASKS / "test-cli" / "01-brainstorming.md").exists(), "templates should exist"
passed += 3
print("  ✓ task dir, .state, templates created")

# ── 3. status ──
print("\n── 3. status ──")
r = run(f"python3 {SW} status --name=test-cli", 0, "status check")
assert "test-cli" in r.stdout, "should show task name"
assert "pending" in r.stdout.lower(), "status should be pending"
passed += 2

# ── 4. advance reject (pending) ──
print("\n── 4. advance reject (pending) ──")
# 现在 advance 会直接拦截 pending 状态
run(f"python3 {SW} advance --name=test-cli", 1, "advance on pending should fail")

# ── 5. monitor simulation (status → running) ──
print("\n── 5. monitor simulation (status → running) ──")
# 模拟 monitor 的效果：将状态改为 running
sf = TASKS / "test-cli" / ".state"
state = json.loads(sf.read_text())
state["stage_status"] = "running"
sf.write_text(json.dumps(state, indent=2))

# 签署门禁 + 拍板备选方案，两者缺一不可
resolve_choices("01-brainstorming")
sign("01-brainstorming")

run(f"python3 {SW} advance --name=test-cli", 0, "advance after satisfying requirements")

# Verify state changed to pending of next stage
state_text = sf.read_text()
assert "02-planning" in state_text, "should be in planning"
assert "pending" in state_text, "new stage should be pending"
passed += 1
print("  ✓ advanced to 02-planning (pending)")

# ── 6. advance with validation ──
print("\n── 6. advance with validation ──")
# 模拟进入 02 阶段并运行
state = json.loads(sf.read_text())
state["stage_status"] = "running"
sf.write_text(json.dumps(state, indent=2))

sign("02-planning")

run(f"python3 {SW} advance --name=test-cli", 0, "advance to 03")

state_text = sf.read_text()
assert "03-coding" in state_text, "should advance to 03-coding"
passed += 1
print("  ✓ advanced to 03-coding")

# ── 8. advance at last stage ──
print("\n── 8. advance at last stage ──")
# 03-coding / 04-review 的硬校验要求目标目录里真有产出、且带 README —— 空转
# 与缺文档都会被拦（任务 T3）。这里模拟 agent 已经写完代码。
write_sample_output()
# Simulate quick advance through stages 3→4→5
for stage_name in ["03-coding", "04-review"]:
    # 模拟运行
    state = json.loads(sf.read_text())
    state["stage_status"] = "running"
    sf.write_text(json.dumps(state, indent=2))
    # 04-review 还需要 Route 决策（check_04-review.sh 会验证）
    sign(stage_name, route="05-Archive" if stage_name == "04-review" else None)
    # 推进
    run(f"python3 {SW} advance --name=test-cli", 0, f"advance stage {stage_name}")

state_text = sf.read_text()
assert "05-archive" in state_text, "should reach 05-archive"
passed += 1
print("  ✓ archive stage reached")

# ── 9. remove / restore ──
print("\n── 9. remove / restore ──")
run(f"python3 {SW} remove --name=test-cli", 0, "remove task")
assert not (TASKS / "test-cli").exists(), "task should be gone from active"
assert (TASKS / ".trash" / "test-cli").exists(), "task should be in trash"
passed += 2

run(f"python3 {SW} list --trash", 0, "list trash")
run(f"python3 {SW} restore --name=test-cli", 0, "restore task")
assert (TASKS / "test-cli").exists(), "task should be back in active"
passed += 2
print("  ✓ remove → list --trash → restore")

# ── 10. edge cases ──
print("\n── 10. edge cases ──")
run(f"python3 {SW} status --name=no-exist", 1, "status non-existent")
run(f"python3 {SW} remove --name=no-exist", 1, "remove non-existent")
run(f"python3 {SW} restore --name=no-exist", 1, "restore non-existent")
run(f"python3 {SW} resume --name=no-exist", 1, "resume non-existent")
print("  ✓ error handling correct")

# ── 11. list ──
print("\n── 11. list ──")
run(f"python3 {SW} list", 0, "list tasks")
print("  ✓ list works")

# ── 12. resume ──
print("\n── 12. resume ──")
run(f"python3 {SW} resume --name=test-cli", 0, "resume task")
print("  ✓ resume works")

# ── cleanup ──
cleanup()
print(f"\n{'=' * 60}")
print(f"结果: {passed} passed, {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
