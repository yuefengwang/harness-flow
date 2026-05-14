#!/usr/bin/env python3
"""sw CLI 回归测试"""
import subprocess, sys, os, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SW = str(ROOT.parent / "sw")
TASKS = ROOT / "tasks"
passed = 0
failed = 0

def run(cmd, expected_exit=0, desc=""):
    global passed, failed
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    exit_ok = result.returncode == expected_exit
    marker = "✓" if exit_ok else "✗"
    if exit_ok:
        passed += 1
    else:
        failed += 1
        print(f"  [{marker}] {desc}")
        print(f"       cmd: {cmd}")
        print(f"       exit: {result.returncode} (expected {expected_exit})")
        print(f"       stderr: {result.stderr.strip()[:200]}")
        print(f"       stdout: {result.stdout.strip()[:200]}")
        print()
    return result

def cleanup():
    for d in TASKS.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            shutil.rmtree(d, ignore_errors=True)
    trash = TASKS / ".trash"
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)

print("=" * 60)
print("sw CLI 回归测试")
print("=" * 60)

# ── 1. usage ──
print("\n── 1. usage / help ──")
run(f"python3 {SW}", 1, "no args → usage")
run(f"python3 {SW} --help", 0, "--help")
run(f"python3 {SW} help", 0, "help subcommand")

# ── 2. init CLI ──
print("\n── 2. init CLI ──")
cleanup()
run(f"python3 {SW} init --name=test-cli --type=feature", 0, "init with args")
run(f"python3 {SW} init --name=test-cli", 1, "init duplicate name")
assert (TASKS / "test-cli").is_dir(), "task dir should exist"
assert (TASKS / "test-cli" / ".state").exists(), ".state should exist"
assert (TASKS / "test-cli" / "01-brainstorming.md").exists(), "template should be copied"
passed += 3  # assertions
print("  ✓ task dir, .state, templates created")

# ── 3. status ──
print("\n── 3. status ──")
r = run(f"python3 {SW} status --name=test-cli", 0, "status by name")
assert "pending" in r.stdout.lower() or "in_progress" in r.stdout.lower(), "status should show stage_status"
passed += 1

# ── 4. advance (pending → reject) ──
print("\n── 4. advance reject (pending) ──")
run(f"python3 {SW} advance --name=test-cli", 1, "advance on pending should fail")

# ── 5. next ──
print("\n── 5. next ──")
run(f"printf 'y\n' | python3 {SW} next --name=test-cli", 0, "next starts stage")

# Verify state changed to in_progress
sf = TASKS / "test-cli" / ".state"
state_text = sf.read_text()
assert "in_progress" in state_text, "stage_status should be in_progress after next"
passed += 1
print("  ✓ stage_status = in_progress after next")

# ── 6. advance (with validation) ──
print("\n── 6. advance with validation ──")
# Simulate filling in template
tpl = TASKS / "test-cli" / "01-brainstorming.md"
content = tpl.read_text()
content = content.replace("[ ] 选项 A:", "[x] 选项 A:")
content = content.replace("[ ] 选项 B:", "[x] 选项 B:")
content = content.replace("[ ] 选项 C:", "[x] 选项 C:")
content = content.replace("设计是否已批准？ (是/否)", "设计是否已批准？ 是")
tpl.write_text(content)

run(f"printf 'y\n' | python3 {SW} advance --name=test-cli", 0, "advance after filling template")

state_text = sf.read_text()
assert "02-planning" in state_text, "should advance to 02-planning"
assert "pending" in state_text, "new stage should be pending"
passed += 2
print("  ✓ advanced to 02-planning (pending)")

# ── 7. advance --force ──
print("\n── 7. advance --force ──")
run(f"printf 'y\n' | python3 {SW} next --name=test-cli", 0, "next for stage 02")
run(f"python3 {SW} advance --name=test-cli --force", 0, "advance --force")

state_text = sf.read_text()
assert "03-coding" in state_text, "should advance to 03-coding"
passed += 1
print("  ✓ --force advances to 03-coding")

# ── 8. advance at last stage ──
print("\n── 8. advance at last stage ──")
# Simulate quick advance through stages 3→4→5
for stage in range(3, 5):
    run(f"printf 'y\n' | python3 {SW} next --name=test-cli", 0, f"next stage {stage}")
    run(f"python3 {SW} advance --name=test-cli --force", 0, f"advance stage {stage}")

state_text = sf.read_text()
assert "05-archive" in state_text, "should reach 05-archive"
passed += 1

r = run(f"python3 {SW} advance --name=test-cli", 0, "advance at archive")
assert "已完成" in r.stdout, "should say completed"
passed += 1
print("  ✓ archive stage reached, advance says completed")

# ── 9. remove / restore ──
print("\n── 9. remove / restore ──")
# Create fresh task
cleanup()
run(f"python3 {SW} init --name=rm-test", 0, "init for remove test")

run(f"python3 {SW} remove --name=rm-test", 0, "remove")
assert not (TASKS / "rm-test").exists(), "task should be gone"
assert (TASKS / ".trash" / "rm-test").exists(), "should be in trash"
passed += 2

run(f"python3 {SW} list --trash", 0, "list trash")
run(f"python3 {SW} restore --name=rm-test", 0, "restore")
assert (TASKS / "rm-test").exists(), "task should be restored"
assert not (TASKS / ".trash" / "rm-test").exists(), "should be out of trash"
passed += 2
print("  ✓ remove → list --trash → restore")

# ── 10. edge cases ──
print("\n── 10. edge cases ──")
run(f"python3 {SW} remove --name=no-exist", 1, "remove non-existent")
run(f"python3 {SW} restore --name=no-exist", 1, "restore non-existent")
run(f"python3 {SW} remove --name=rm-test", 0, "remove again")
run(f"python3 {SW} init --name=rm-test", 0, "init same name after remove")
run(f"python3 {SW} restore --name=rm-test", 1, "restore when name conflict")
print("  ✓ error handling correct")

# ── 11. list ──
print("\n── 11. list ──")
run(f"python3 {SW} list", 0, "list active")
run(f"python3 {SW} list --trash", 0, "list trash")
print("  ✓ list works")

# ── 12. resume ──
print("\n── 12. resume ──")
cleanup()
run(f"python3 {SW} init --name=resume-test", 0, "init for resume")
run(f"python3 {SW} resume --name=resume-test", 0, "resume")
run(f"python3 {SW} resume --name=no-exist", 1, "resume non-existent")
print("  ✓ resume works")

# ── cleanup ──
cleanup()
print(f"\n{'=' * 60}")
print(f"结果: {passed} passed, {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
