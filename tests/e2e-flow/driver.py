#!/usr/bin/env python3
"""PTY 驱动脚本 — 启动 sw init 完成完整 5 阶段 E2E 测试。

支持两种模式：
  - (默认) mock 模式:  sw init --mock              — 快速验证 (约 60s)
  - --no-mock 模式:    sw init --no-mock            — 真实 Agent (约 15-30min)

用法:
    python3 tests/e2e-flow/driver.py                # mock 模式
    python3 tests/e2e-flow/driver.py --no-mock      # 真实 Agent 模式

流程:
    1. 用 pty.fork() 创建子进程
    2. 子进程运行 sw init，通过 PTY 实时交互
    3. 父进程监控 .log 文件和 PTY 输出
    4. 依次经过 01→02→03→04→05 全部阶段
    5. 退出码 0 = 全部通过，非 0 = 有失败
"""

import argparse
import os
import pty
import re
import json
import select
import signal
import sys
import time
from pathlib import Path
from typing import Optional, List

# ── 路径 ──
ROOT = Path(__file__).resolve().parent.parent.parent
TASK_NAME = f"e2e-{os.getpid()}"
CONTEXT = "Build a CLI note manager with add/list/delete/search commands in Python"

# ── Logger ──
def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ── Stage 定义 ──
STAGES = ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]
STAGE_LABELS = {
    "01-brainstorming": "01-Brainstorming",
    "02-planning":      "02-Planning",
    "03-coding":        "03-Coding",
    "04-review":        "04-Review",
    "05-archive":       "05-Archive",
}


# ==============================================================
# 交互基元
# ==============================================================

def _write(fd: int, text: str):
    """向 PTY 发送文本（以 \\r 结尾模拟 Enter）。"""
    os.write(fd, (text + "\r").encode())


def _read_pty(fd: int, timeout: float = 0.5) -> str:
    """读取 PTY 可用输出。"""
    data = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                data += chunk
            except OSError:
                break
    return data.decode("utf-8", errors="replace")


def _wait_log(task_dir: Path, pattern: str, timeout: float = 120) -> Optional[str]:
    """等待 .log 文件中出现指定字符串。返回匹配时的完整日志内容。"""
    log_file = task_dir / ".log"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8", errors="replace")
            if pattern in content:
                return content
        time.sleep(0.5)
    return None


def _wait_log_re(task_dir: Path, regex: str, timeout: float = 120) -> Optional[re.Match]:
    """等待 .log 文件中出现正则匹配。"""
    log_file = task_dir / ".log"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8", errors="replace")
            m = re.search(regex, content)
            if m:
                return m
        time.sleep(0.5)
    return None


def _read_state(task_dir: Path) -> dict:
    """读取任务状态。"""
    state_file = task_dir / ".state"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _stage_file(task_dir: Path, stage: str) -> str:
    """读取阶段文件内容。"""
    f = task_dir / f"{stage}.md"
    return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""


def _last_log_lines(task_dir: Path, n: int = 10) -> List[str]:
    """读取 .log 最后 n 行。"""
    log_file = task_dir / ".log"
    if log_file.exists():
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    return []


def _advance_error_in_log(log_lines: List[str]) -> bool:
    """检查日志行中是否有推进失败信号。"""
    for line in log_lines[-5:]:
        low = line.lower()
        if any(kw in low for kw in ("未通过", "失败", "error", "检测到")):
            return True
    return False


def _wait_for_ai_output(task_dir: Path, stage: str, timeout: float = 30) -> bool:
    """Poll the stage file until AI Output appears or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        content = _stage_file(task_dir, stage)
        if "🤖 AI Output" in content:
            return True
        time.sleep(0.5)
    return False


def _wait_stage_status(task_dir: Path, status: str, timeout: float = 30) -> bool:
    """Poll .state file until stage_status matches the given value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _read_state(task_dir)
        if st.get("stage_status") == status:
            return True
        time.sleep(0.5)
    return False


# ==============================================================
# 驱动逻辑
# ==============================================================

def _handle_questions_loop(
    fd: int, task_dir: Path, pty_text: str,
    timeout: float = 120,
    answer_defaults: Optional[List[str]] = None,
) -> bool:
    """检测并回答 Agent 的提问。
    
    当 .log 中出现 "❓ 收到" 时，读取 PTY 中的选项，用 answer_defaults 回复。
    返回 True 表示所有问题已解决，False 表示超时。
    """
    if answer_defaults is None:
        answer_defaults = ["A", "A", "A"]  # 默认选第一个

    deadline = time.time() + timeout
    q_count = 0
    last_log_size = 0

    while time.time() < deadline:
        log_file = task_dir / ".log"
        if not log_file.exists():
            time.sleep(0.3)
            continue

        content = log_file.read_text(encoding="utf-8", errors="replace")
        # 检测是否有新问题
        q_matches = list(re.finditer(r"❓ 收到 (\d+) 个结构化问题", content))
        if not q_matches:
            time.sleep(0.5)
            continue

        latest_q = q_matches[-1]
        current_total = int(latest_q.group(1))

        # 已经回答了所有问题？
        # 简单策略：检测到问题 → 逐一回答
        answered = 0
        # 读取一些 PTY 输出以获取选项
        pty_out = _read_pty(fd, timeout=1)

        # 尝试回答每个未答的问题
        for qi in range(current_total):
            # 检查 agent 是否已经继续（不再等待回答）
            # 如果 .log 中出现了 "opencode SDK completed" 或阶段推进，说明问题已处理
            log_now = log_file.read_text(encoding="utf-8", errors="replace")
            if "opencode SDK completed" in log_now or "[✓]" in log_now:
                return True

            answer = answer_defaults[qi] if qi < len(answer_defaults) else answer_defaults[-1]
            log(f"  → Answering Q{qi+1}: {answer}")
            _write(fd, answer)
            time.sleep(1.5)
            answered += 1

        if answered > 0:
            log(f"  → Answered {answered} question(s)")
            # 等待 agent 继续处理
            time.sleep(3)
            return True  # 问题已响应，继续主循环

        time.sleep(0.5)

    log(f"  WARN: Question handling timeout ({timeout}s)")
    return False


def _drive_mock(pid: int, fd: int) -> int:
    """Mock 模式驱动逻辑 — 快速验证 (约 60s)。"""
    task_dir = ROOT / "workspace" / "tasks" / TASK_NAME

    # ── 等待任务创建 ──
    log("Waiting for task directory...")
    deadline = time.time() + 15
    while time.time() < deadline:
        if task_dir.exists():
            break
        time.sleep(0.3)
    if not task_dir.exists():
        log("FAIL: Task directory not created")
        return 1

    log(f"Task dir: {task_dir}")
    time.sleep(2)
    _read_pty(fd)

    # MockAgent 的提问答案（brainstorming 有 2 个问题）
    BRAINSTORM_ANSWERS = ["B", "3"]

    stage_idx = 0
    while stage_idx < len(STAGES):
        stage = STAGES[stage_idx]
        label = STAGE_LABELS[stage]
        log(f"\n{'='*50}")
        log(f"Stage {stage_idx+1}/5: {label}")

        # ── Stage 01: 回答 MockAgent 提问 ──
        if stage == "01-brainstorming":
            for i, answer in enumerate(BRAINSTORM_ANSWERS):
                log(f"  Waiting for question {i+1}...")
                q_content = _wait_log(task_dir, "❓ 收到", timeout=30)
                if q_content is None:
                    log(f"  FAIL: Question {i+1} not received within timeout")
                    return 1
                log(f"  Answering Q{i+1}: {answer}")
                _write(fd, answer)
                time.sleep(1.5)

        # ── 等待 Agent 启动完毕（stage_status = "running"）──
        log("  Waiting for stage to enter running state...")
        if not _wait_stage_status(task_dir, "running", timeout=30):
            log("  FAIL: Stage did not enter running state within timeout")
            return 1
        log("  ✓ Stage is running")

        # ── 发送 /advance ──
        log("  Sending /advance to finalize and save output...")
        _write(fd, "/advance")

        # ── 等待 mock_agent shutdown (由 /advance 触发) ──
        if not _wait_log(task_dir, "mock_agent shutdown", timeout=30):
            log("  FAIL: MockAgent did not shutdown within timeout")
            return 1
        log("  ✓ MockAgent shutdown")

        # ── 等待 AI Output ──
        if not _wait_for_ai_output(task_dir, stage):
            sf = _stage_file(task_dir, stage)
            log(f"  FAIL: {stage} has no AI Output (size={len(sf)}b)")
            return 1
        log("  ✓ AI Output present")

        time.sleep(1)

        # ── Stage 04: 填写 Route ──
        if stage == "04-review":
            review_file = task_dir / "04-review.md"
            if review_file.exists():
                rcontent = review_file.read_text(encoding="utf-8")
                rcontent = rcontent.replace("- **Route**: `___`", "- **Route**: `05-Archive`", 1)
                review_file.write_text(rcontent, encoding="utf-8")
                log("  ✓ Route set to 05-Archive")
            time.sleep(1)

        # ── 调试输出 ──
        pty_out = _read_pty(fd, timeout=1)
        if pty_out:
            tail = pty_out[-300:].replace("\r\n", " | ").replace("\n", " | ")
            log(f"  TUI tail: ...{tail}")

        # ── 校验推进 ──
        log_lines = _last_log_lines(task_dir)
        if _advance_error_in_log(log_lines):
            log("  FAIL: Advance validation failed!")
            for line in log_lines[-8:]:
                log(f"    {line}")
            return 1

        st = _read_state(task_dir)
        next_stage = st.get("stage", stage)
        stage_status = st.get("stage_status", "")

        if stage_status == "Finished" and stage_idx >= len(STAGES) - 1:
            log("  ✓ All stages complete (Finished)")
            stage_idx += 1
            break

        if next_stage != stage:
            log(f"  ✓ Advanced to: {next_stage}")
            stage_idx += 1
        else:
            log("  Stage not yet advanced, waiting...")
            time.sleep(5)
            st = _read_state(task_dir)
            next_stage = st.get("stage", stage)
            if next_stage != stage:
                log(f"  ✓ Advanced to: {next_stage}")
                stage_idx += 1
            else:
                log(f"  FAIL: Stage did not advance")
                for line in _last_log_lines(task_dir, 5):
                    log(f"    {line}")
                return 1

        time.sleep(3)

    log(f"\n{'='*50}")
    log(f"ALL STAGES PASSED: {TASK_NAME}")
    print(f"\nTASK_DIR={task_dir}", flush=True)
    return 0


def _fill_template_checkboxes(task_dir: Path, stage: str) -> None:
    """Fill [ ] → [x] in template area and Gate area, preserving AI Output.
    
    Mirrors the mock-mode logic in _save_stage_output() — needed in non-mock
    mode because the real agent cannot directly edit the template file, but the
    soft check requires [x] in template/Gate area.
    """
    stage_file = task_dir / f"{stage}.md"
    if not stage_file.exists():
        return

    content = stage_file.read_text(encoding="utf-8", errors="replace")
    
    ai_mrkr = "\n## 🤖 AI Output\n"
    gate_mrkr = "\n## Gate"
    ai_pos = content.find(ai_mrkr)
    
    if ai_pos >= 0:
        gate_pos = content.find(gate_mrkr, ai_pos + len(ai_mrkr))
        if gate_pos >= 0:
            before = content[:ai_pos].replace("[ ]", "[x]")
            output_area = content[ai_pos:gate_pos]
            gate_area = content[gate_pos:].replace("[ ]", "[x]")
            content = before + output_area + gate_area
        else:
            content = content.replace("[ ]", "[x]")
    else:
        content = content.replace("[ ]", "[x]")
    
    stage_file.write_text(content, encoding="utf-8")


def _detect_deadlock(task_dir: Path, stage: str, timeout: float = 15) -> bool:
    """检测 agent 完成后是否进入了死锁状态（状态仍为 running）。
    
    当 .log 中有 "opencode SDK completed" 但 .state 中 stage_status 仍为
    "running" 时，说明 invoke() 的后续步骤（parse/gate/save）可能无声崩溃。
    此函数会等待一小段时间让状态正常转换，超时则判定为死锁。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _read_state(task_dir)
        status = st.get("stage_status", "")
        if status != "running":
            return False  # Not deadlocked
        time.sleep(1)

    # Deadlock confirmed — dump diagnostics
    st = _read_state(task_dir)
    log(f"  ⚠ DEADLOCK DETECTED: stage_status still 'running' after completion")
    log(f"    stage={st.get('stage')}, stage_idx={st.get('stage_idx')}, status={st.get('stage_status')}")
    sf = _stage_file(task_dir, stage)
    has_ai = "🤖 AI Output" in sf
    log(f"    stage_file size={len(sf)}b, has AI Output={has_ai}")
    for line in _last_log_lines(task_dir, 8):
        log(f"    log: {line}")
    return True


def _drive_real(pid: int, fd: int) -> int:
    """真实 Agent 模式驱动逻辑 (约 15-30min)。"""
    task_dir = ROOT / "workspace" / "tasks" / TASK_NAME

    # ── 等待任务创建 ──
    log("Waiting for task directory...")
    deadline = time.time() + 30
    while time.time() < deadline:
        if task_dir.exists():
            break
        time.sleep(0.5)
    if not task_dir.exists():
        log("FAIL: Task directory not created")
        return 1

    log(f"Task dir: {task_dir}")
    time.sleep(3)  # 等待 TUI 和 opencode server 完全初始化
    _read_pty(fd)

    # Track log file position so each stage only matches NEW log entries.
    log_offset = 0

    stage_idx = 0
    while stage_idx < len(STAGES):
        stage = STAGES[stage_idx]
        label = STAGE_LABELS[stage]
        log(f"\n{'='*50}")
        log(f"Stage {stage_idx+1}/5: {label} (real agent)")

        # ── 等待 Agent 完成 ──
        log("  Waiting for agent to complete (this may take several minutes)...")

        completion_signal = "opencode SDK completed"
        question_signal = "❓ 收到"
        stage_timeout = 600  # 10 min per stage

        deadline = time.time() + stage_timeout
        seen_question = False

        while time.time() < deadline:
            if not task_dir.joinpath(".log").exists():
                time.sleep(0.5)
                continue

            log_content = task_dir.joinpath(".log").read_text(
                encoding="utf-8", errors="replace"
            )

            # Only check for completion signal in content AFTER log_offset
            new_content = log_content[log_offset:] if log_offset < len(log_content) else ""

            if completion_signal in new_content:
                log("  ✓ Agent SDK completed")
                log_offset = len(log_content)
                break

            if question_signal in new_content and not seen_question:
                log("  ⚡ Agent asking questions, responding...")
                seen_question = True
                pty_out = _read_pty(fd, timeout=2)
                if pty_out:
                    log(f"  PTY context: {pty_out[-200:].replace(chr(10), ' ')[:200]}")
                # Answer all questions with first option
                for qi in range(3):
                    log(f"  Answering Q{qi+1}: A (first option)")
                    _write(fd, "A")
                    time.sleep(1)
                    new_log = task_dir.joinpath(".log").read_text(
                        encoding="utf-8", errors="replace"
                    )
                    if completion_signal in new_log[log_offset:]:
                        break
                log("  → Questions answered, waiting for agent to resume...")
                time.sleep(5)
                continue

            time.sleep(2)
        else:
            log_lines = _last_log_lines(task_dir, 10)
            log(f"  FAIL: Agent did not complete within {stage_timeout}s")
            for line in log_lines:
                log(f"    {line}")
            return 1

        # ── 死锁检测: agent 已完成但状态未更新 ──
        # 在多轮对话模式下，/advance 触发后才执行保存，因此跳过前序死锁检测

        # ── Fill template/Gate checkboxes before /advance ──
        _fill_template_checkboxes(task_dir, stage)
        log("  ✓ Template checkboxes filled")

        # ── Stage 04: fill Route if needed ──
        if stage == "04-review":
            review_file = task_dir / "04-review.md"
            if review_file.exists():
                rcontent = review_file.read_text(encoding="utf-8")
                if "`___`" in rcontent:
                    rcontent = rcontent.replace("- **Route**: `___`", "- **Route**: `05-Archive`", 1)
                    review_file.write_text(rcontent, encoding="utf-8")
                    log("  ✓ Route filled to 05-Archive")

        # ── 发送 /advance ──
        log("  Sending /advance to save output and advance...")
        _write(fd, "/advance")
        time.sleep(5)

        # ── 等待 AI Output ──
        if not _wait_for_ai_output(task_dir, stage):
            sf = _stage_file(task_dir, stage)
            log(f"  FAIL: {stage} has no AI Output after advance (size={len(sf)}b)")
            log("  → _save_stage_output() did not run after /advance.")
            return 1
        log("  ✓ AI Output present")
        # ── 校验推进 ──
        log_lines = _last_log_lines(task_dir)
        if _advance_error_in_log(log_lines):
            log("  FAIL: Advance validation failed!")
            for line in log_lines:
                log(f"    {line}")
            return 1

        st = _read_state(task_dir)
        next_stage = st.get("stage", stage)
        stage_status = st.get("stage_status", "")

        if stage_status == "Finished" and stage_idx >= len(STAGES) - 1:
            log("  ✓ All stages complete (Finished)")
            stage_idx += 1
            break

        if next_stage != stage:
            log(f"  ✓ Advanced to: {next_stage}")
            stage_idx += 1
        else:
            log("  Stage not yet advanced, waiting...")
            time.sleep(10)
            st = _read_state(task_dir)
            next_stage = st.get("stage", stage)
            if next_stage != stage:
                log(f"  ✓ Advanced to: {next_stage}")
                stage_idx += 1
            else:
                log(f"  FAIL: Stage did not advance")
                for line in _last_log_lines(task_dir, 10):
                    log(f"    {line}")
                return 1

        # ── 等待下一阶段启动 ──
        log("  Waiting for next stage to initialize...")
        time.sleep(5)

    # ── 完成 ──
    log(f"\n{'='*50}")
    log(f"ALL STAGES PASSED: {TASK_NAME}")
    print(f"\nTASK_DIR={task_dir}", flush=True)
    return 0


# ==============================================================
# 主入口
# ==============================================================

def _cleanup(pid: int, fd: int):
    """清理子进程和 PTY 文件描述符。"""
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid != 0:
                break
            time.sleep(0.2)
        else:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    except (OSError, ChildProcessError):
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E Flow PTY Driver")
    parser.add_argument(
        "--no-mock", action="store_true",
        help="使用真实 Agent（默认使用 MockAgent 快速验证）"
    )
    args = parser.parse_args()

    use_mock = not args.no_mock
    mode_str = "MockAgent" if use_mock else "Real Agent"
    log(f"=== E2E Flow Test: {TASK_NAME} ===")
    log(f"Mode: {mode_str}")
    log(f"Context: {CONTEXT}")

    # ── Fork PTY ──
    pid, fd = pty.fork()
    if pid == 0:
        # 子进程
        init_args = [
            "python3", str(ROOT / "sw"), "init",
            "--name", TASK_NAME,
            "--context", CONTEXT,
        ]
        if use_mock:
            init_args.append("--mock")
        else:
            init_args.append("--no-mock")
        os.execvp("python3", init_args)

    # ── 父进程：驱动交互 ──
    try:
        if use_mock:
            return _drive_mock(pid, fd)
        else:
            return _drive_real(pid, fd)
    except KeyboardInterrupt:
        log("Interrupted by user")
        return 1
    finally:
        _cleanup(pid, fd)


if __name__ == "__main__":
    sys.exit(main())
