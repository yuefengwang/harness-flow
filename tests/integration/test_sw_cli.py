#!/usr/bin/env python3
"""sw CLI 回归测试"""
import subprocess, sys, os, shutil
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
import json
state = json.loads(sf.read_text())
state["stage_status"] = "running"
sf.write_text(json.dumps(state, indent=2))

# 勾选模板以满足校验
tpl = TASKS / "test-cli" / "01-brainstorming.md"
content = tpl.read_text()
content = content.replace("[ ] ", "[x] ")
tpl.write_text(content)

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

# Simulate filling in template
tpl = TASKS / "test-cli" / "02-planning.md"
content = tpl.read_text()
content = content.replace("[ ] ", "[x] ")
tpl.write_text(content)

run(f"python3 {SW} advance --name=test-cli", 0, "advance to 03")

state_text = sf.read_text()
assert "03-coding" in state_text, "should advance to 03-coding"
passed += 1
print("  ✓ advanced to 03-coding")

# ── 8. advance at last stage ──
print("\n── 8. advance at last stage ──")
# Simulate quick advance through stages 3→4→5
for stage_name in ["03-coding", "04-review"]:
    # 模拟运行
    state = json.loads(sf.read_text())
    state["stage_status"] = "running"
    sf.write_text(json.dumps(state, indent=2))
    # 模拟勾选
    tpl = TASKS / "test-cli" / f"{stage_name}.md"
    content = tpl.read_text()
    content = content.replace("[ ] ", "[x] ")
    # 04-review 需要填写 Route 字段（check_04-review.sh 会验证）
    if stage_name == "04-review":
        content = content.replace("`___`", "`05-Archive`")
    tpl.write_text(content)
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
