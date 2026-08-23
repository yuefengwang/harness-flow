#!/usr/bin/env python3
"""PTY 驱动脚本 — 启动 `sw init --mock` 完成完整 5 阶段 E2E 测试。

**只跑 MockAgent。** 驱动真实 agent 的模式已删除：那条路径依赖模型输出，
同一份代码两次运行结果不同，失败无法区分是回归还是模型这次心情不好 ——
不满足可执行、可复现、可验证。真实 agent 属于手工验证，不是测试。

MockAgent 的每个阶段输出都是写死的脚本（sw_lib/agents/mock.py），因此本
驱动的每一步都能断言确切文本，而不是"等等看"。

用法:
    python3 tests/e2e-flow/driver.py

流程:
    1. 用 pty.fork() 创建子进程
    2. 子进程运行 sw init --mock，通过 PTY 实时交互
    3. 父进程监控 .log 文件和 PTY 输出
    4. 依次经过 01→02→03→04→05 全部阶段
    5. 退出码 0 = 全部通过，非 0 = 有失败
"""

import argparse
import os
import pty
import json
import re
import select
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List

# ── 路径 ──
ROOT = Path(__file__).resolve().parent.parent.parent
TASK_NAME = f"e2e-{os.getpid()}"
CONTEXT = "Build a CLI note manager with add/list/delete/search commands in Python"

# 从被测代码里取标记，不在这里抄一份字符串：抄了就会在改 TUI/MockAgent 时
# 悄悄失配，而失配的表现是「等待超时」，看起来像流程 bug。
sys.path.insert(0, str(ROOT))
from sw_lib.agents.mock import SCENARIO_DONE_MARKER  # noqa: E402
from sw_lib.ui.tui import PROMPT_READY_MARKER  # noqa: E402

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


def _gate_signed(task_dir: Path, stage: str) -> bool:
    """该阶段的 Gate 是否已签署。判定源是 `.state`，不是 Markdown。"""
    sf = task_dir / ".state"
    if not sf.exists():
        return False
    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    gate = (state.get("stages") or {}).get(stage, {}).get("gate") or {}
    items = gate.get("items") or []
    return bool(items) and all(i.get("checked") for i in items)


def _wait_prompt_ready(fd: int, task_dir: Path, scenarios: int, prompts: int,
                       timeout: float = 30) -> bool:
    """等到按键确实会被接受为止。

    要求两个条件同时成立：

    * MockAgent 的场景脚本已收尾第 ``scenarios`` 次（``SCENARIO_DONE_MARKER``），
      说明 agent 不会再转回 active；
    * TUI 的拍板面板已打开第 ``prompts`` 次（``PROMPT_READY_MARKER``），说明
      ``input_mode`` 已切成 options，``_validate_input`` 会放行。

    缺一不可，前三版分别栽在这两条上：

    1. 猜「日志静默 3 秒」—— 脚本自带 sleep(2)，静默窗口可能落在两行输出之间。
    2. 只等脚本收尾 —— TUI 还没在 40ms 轮询里把 input_mode 切过来，按 A 被拒。
    3. 只等面板打开 —— 01 阶段答完提问后有个瞬时 idle 窗口，面板会在 agent
       继续输出前先开一次；此时按 A，agent 已转回 active，照样被拒。

    不匹配 TUI 屏幕文本：Rich 全屏模式会插 ANSI 序列并按宽度折行，中文提示
    可能被拆散在多次写入里。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        _read_pty(fd, timeout=0.2)  # 持续排空，避免子进程写阻塞
        if _count_log(task_dir, SCENARIO_DONE_MARKER) < scenarios:
            time.sleep(0.1)
            continue
        # 脚本收尾之后面板才算数。收尾前那次打开是 01 阶段答完提问的瞬时
        # idle 窗口造成的，按它去按键会被拒。
        if _count_log_after(task_dir, PROMPT_READY_MARKER,
                            SCENARIO_DONE_MARKER, scenarios) >= prompts:
            return True
        time.sleep(0.1)
    return False


def _count_log_after(task_dir: Path, pattern: str,
                     anchor: str, anchor_nth: int) -> int:
    """统计第 ``anchor_nth`` 次出现 ``anchor`` 之后，``pattern`` 出现了几次。

    用来忽略「脚本还没收尾时面板短暂打开」那类计数噪声。
    """
    log_file = task_dir / ".log"
    if not log_file.exists():
        return 0
    content = log_file.read_text(encoding="utf-8", errors="replace")
    pos = -1
    for _ in range(anchor_nth):
        pos = content.find(anchor, pos + 1)
        if pos < 0:
            return 0
    return content.count(pattern, pos)


def _wait_gate_signed(fd: int, task_dir: Path, stage: str,
                      timeout: float = 15) -> bool:
    """等本阶段的 Gate 真正签上（判定源是 .state）。

    持续排空 PTY，否则 TUI 写满管道会阻塞，看起来像"没反应"。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        _read_pty(fd, timeout=0.3)
        if _gate_signed(task_dir, stage):
            return True
        time.sleep(0.2)
    return False


def _count_log(task_dir: Path, pattern: str) -> int:
    """统计 .log 中 pattern 出现的次数。"""
    log_file = task_dir / ".log"
    if not log_file.exists():
        return 0
    return log_file.read_text(encoding="utf-8", errors="replace").count(pattern)


def _wait_log_count(task_dir: Path, pattern: str, at_least: int,
                    timeout: float = 30) -> bool:
    """等 pattern 出现次数达到 at_least。

    _wait_log 是全文匹配，用它等「第 N 次出现」会被前面的旧记录立刻命中
    而假性通过 —— 例如等第 2 次回答时匹配 "[1]"，其实匹配的是第 1 次。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _count_log(task_dir, pattern) >= at_least:
            return True
        time.sleep(0.2)
    return False


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


def _dump_failure(task_dir: Path, stage: str, reason: str,
                  fd: Optional[int] = None) -> None:
    """失败时把现场打印出来。

    _cleanup 会杀掉子进程，之后再去翻任务目录常常已经没有 .log/.state；
    定位问题不该需要另开一轮去 `ls -dt` 找目录再 tail 日志。
    """
    log(f"  FAIL: {reason}")
    log(f"  ── 现场诊断 ({task_dir.name} @ {stage}) ──")
    st = _read_state(task_dir)
    log(f"    state: stage={st.get('stage')} status={st.get('stage_status')}")

    content = _stage_file(task_dir, stage)
    has_ai = "🤖 AI Output" in content
    pos = content.rfind("\n## Gate")
    gate = content[pos:].replace("\n", " / ") if pos >= 0 else "(无 Gate 区)"
    log(f"    {stage}.md: {len(content)}b, AI Output={has_ai}")
    log(f"    Gate: {gate[:200]}")

    for line in _last_log_lines(task_dir, 15):
        log(f"    log: {line}")

    # 被 _validate_input 拒绝的输入只写进 state.error_msg（渲染在面板上），
    # 不进 .log —— 只看日志时表现为「按键像是没发出去」。把屏幕文本捞出来。
    if fd is not None:
        screen = _read_pty(fd, timeout=0.5)
        tail = _strip_ansi(screen)[-600:]
        if tail.strip():
            log(f"    screen: {tail!r}")


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]")


def _strip_ansi(text: str) -> str:
    """去掉 ANSI 控制序列，只留可读文本。"""
    return _ANSI_RE.sub("", text)


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


def _wait_log_quiet(task_dir: Path, quiet_for: float = 1.0,
                    timeout: float = 30) -> bool:
    """等 .log 停止增长，表示 TUI 已处理完上一条输入。

    替代固定 sleep：真实等待时间取决于机器负载，写死秒数要么白等、要么
    在慢机器上偶发失败。
    """
    log_file = task_dir / ".log"
    deadline = time.time() + timeout
    last = -1
    stable_since = None
    while time.time() < deadline:
        size = log_file.stat().st_size if log_file.exists() else 0
        if size == last:
            if stable_since and time.time() - stable_since >= quiet_for:
                return True
        else:
            last = size
            stable_since = time.time()
        time.sleep(0.1)
    return False


def _wait_advanced(task_dir: Path, from_stage: str,
                   timeout: float = 30) -> tuple[str, str]:
    """等阶段真正推进，返回 (stage, stage_status)。

    原实现是 sleep(3) 后读一次、不对就再 sleep(5) 读一次；这里改为轮询到
    状态变化为止，快机器上立刻返回，慢机器上也不会误判成「未推进」。
    """
    deadline = time.time() + timeout
    st = {}
    while time.time() < deadline:
        st = _read_state(task_dir)
        stage = st.get("stage", from_stage)
        status = st.get("stage_status", "")
        if status == "Finished" or stage != from_stage:
            return stage, status
        time.sleep(0.2)
    return st.get("stage", from_stage), st.get("stage_status", "")


# ==============================================================
# 驱动逻辑
# ==============================================================

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
    _wait_log_quiet(task_dir, quiet_for=0.8, timeout=15)
    _read_pty(fd, timeout=0.3)

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
                # 等第 i+1 个提问出现（全文匹配会被上一个提问立刻命中）
                if not _wait_log_count(task_dir, "❓ 收到", i + 1, timeout=30):
                    _dump_failure(task_dir, stage, f"Question {i+1} not received")
                    return 1
                log(f"  Answering Q{i+1}: {answer}")
                _write(fd, answer)
                # 等 TUI 记录下这次回答（user 行计数 +1）再继续
                if not _wait_log_count(task_dir, "user  | [1]", i + 1, timeout=15):
                    _dump_failure(task_dir, stage, f"Answer {i+1} not registered")
                    return 1

        # ── 等待 Agent 启动完毕（stage_status = "running"）──
        log("  Waiting for stage to enter running state...")
        if not _wait_stage_status(task_dir, "running", timeout=30):
            _dump_failure(task_dir, stage, "Stage did not enter running state")
            return 1
        log("  ✓ Stage is running")

        # ── 等签署选项出现，再签署 Gate ──
        # 签署入口只在 agent 收尾（idle/waiting）后才出现；agent 还在生成时按 A
        # 会被输入校验拒绝，且字符留在缓冲区，把下一条命令污染成 "A/advance"。
        # 多轮模式下 .state 全程是 "running"，所以等 TUI 自己报「面板已打开」。
        log("  Waiting for gate sign-off prompt...")
        if not _wait_prompt_ready(fd, task_dir, stage_idx + 1, 1, timeout=30):
            _dump_failure(task_dir, stage, "Gate sign-off prompt did not appear")
            return 1

        # 04-review 要按两次 A：第一次是 Route 决策（比签署更前置），Route 落定
        # 后面板才切换成 Gate 签署选项。Route 只能从这个入口写进 .state ——
        # 改 04-review.md 里的 **Route** 字段不再有任何效力。
        if stage == "04-review":
            log("  Choosing route (A = 05-Archive)...")
            _write(fd, "A")
            if not _wait_log(task_dir, "Route 已设置为 05-Archive", timeout=15):
                _dump_failure(task_dir, stage, "Route choice was not accepted")
                return 1
            log("  ✓ Route set to 05-Archive")
            # Route 落定后面板会重开一次（切成 Gate 签署选项），所以等第 2 次。
            if not _wait_prompt_ready(fd, task_dir, stage_idx + 1, 2, timeout=30):
                _dump_failure(task_dir, stage,
                              "Gate sign-off prompt did not appear after routing")
                return 1

        log("  Signing off stage gate (choice A)...")
        _write(fd, "A")
        # 判定源用 .state 而不是日志文本：_wait_log 是全文匹配，"Gate 已签署"
        # 在第 2 个阶段之后必然已经出现过，于是每一轮都立刻命中 —— 后续阶段
        # 签署失败会被掩成绿色，真正的失败推迟到几十秒后的超时才暴露，
        # 而且报的是无关的现象（"stage file has no AI Output"）。
        if not _wait_gate_signed(fd, task_dir, stage, timeout=15):
            _dump_failure(task_dir, stage, "Gate sign-off was not accepted", fd)
            return 1

        # 签 Gate 就是推进信号，TUI 在签署分支里直接跑 advance —— 这里再发一次
        # /advance 会打到下一阶段（agent 才刚启动），把校验失败写进日志。
        #
        # ── 等待 mock_agent shutdown (由签署触发的 advance 收尾) ──
        # 同理按出现次数等：每个阶段都会打印一次 shutdown。
        if not _wait_log_count(task_dir, "mock_agent shutdown",
                               stage_idx + 1, timeout=30):
            _dump_failure(task_dir, stage, "MockAgent did not shutdown")
            return 1
        log("  ✓ MockAgent shutdown")

        # ── 等待 AI Output ──
        if not _wait_for_ai_output(task_dir, stage):
            _dump_failure(task_dir, stage, "stage file has no AI Output")
            return 1
        log("  ✓ AI Output present")

        # ── 校验推进 ──
        log_lines = _last_log_lines(task_dir)
        if _advance_error_in_log(log_lines):
            _dump_failure(task_dir, stage, "Advance validation failed")
            return 1

        next_stage, stage_status = _wait_advanced(task_dir, stage, timeout=30)

        if stage_status == "Finished" and stage_idx >= len(STAGES) - 1:
            log("  ✓ All stages complete (Finished)")
            stage_idx += 1
            break

        if next_stage != stage:
            log(f"  ✓ Advanced to: {next_stage}")
            stage_idx += 1
        else:
            _dump_failure(task_dir, stage, "Stage did not advance")
            return 1

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


def _drop_status_entry(name: str):
    """从 workspace/STATUS.json 摘掉任务条目。

    driver 是独立脚本（不经 pytest conftest），所以自己负责这步；
    直接改 JSON 而不 import sw_lib，避免为一次清理牵进整个包。
    """
    status = ROOT / "workspace" / "STATUS.json"
    if not status.exists():
        return
    try:
        data = json.loads(status.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.get("tasks", {}).pop(name, None) is None:
        return
    try:
        status.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E Flow PTY Driver")
    parser.add_argument(
        "--no-verify", action="store_true",
        help="跑完不自动执行 verify.py（默认自动验收）"
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="通过后保留任务目录（默认清理，失败时一律保留供排查）"
    )
    args = parser.parse_args()

    log(f"=== E2E Flow Test: {TASK_NAME} ===")
    log("Mode: MockAgent (scripted, deterministic)")
    log(f"Context: {CONTEXT}")

    # 测试自己决定输入：评审路由写死成归档，不看 config.yaml。仓库里那份
    # review_route 现在是 02-Planning，继承它会让 e2e 走返工分支然后卡在
    # 「等第 5 阶段」上 —— 编辑配置文件不该改变测试的含义。
    os.environ["SW_MOCK_REVIEW_ROUTE"] = "05-Archive"

    # 同理固定节奏。默认压到 0.2：脚本仍逐行输出（便于看日志），但不为拟真
    # 打字速度付整轮几十秒。config.yaml 里的 response_delay 是给人演示用的。
    os.environ.setdefault("SW_MOCK_RESPONSE_DELAY", "0.2")

    # ── Fork PTY ──
    pid, fd = pty.fork()
    if pid == 0:
        # 子进程
        init_args = [
            "python3", str(ROOT / "sw"), "init",
            "--name", TASK_NAME,
            "--context", CONTEXT,
            "--mock",
        ]
        os.execvp("python3", init_args)

    # ── 父进程：驱动交互 ──
    try:
        rc = _drive_mock(pid, fd)
    except KeyboardInterrupt:
        log("Interrupted by user")
        return 1
    finally:
        _cleanup(pid, fd)

    task_dir = ROOT / "workspace" / "tasks" / TASK_NAME

    # 驱动通过 ≠ 产出合规：verify 才是验收。默认自动接续，省掉人工复制路径。
    if rc == 0 and not args.no_verify:
        log("")
        log("Running acceptance verification...")
        rc = subprocess.call(
            [sys.executable, str(Path(__file__).parent / "verify.py"),
             "--task-dir", str(task_dir)])

    # 失败一律保留现场；通过则清理，避免残留任务污染 workspace
    if rc == 0 and not args.keep:
        shutil.rmtree(task_dir, ignore_errors=True)
        # 任务目录只是三处产物之一：agent 会在 repo/<task> 下建工作目录，
        # STATUS.json 里也有一条汇总。少清任何一处都会留下孤儿。
        shutil.rmtree(ROOT / "repo" / TASK_NAME, ignore_errors=True)
        _drop_status_entry(TASK_NAME)
        log(f"Cleaned up {TASK_NAME}")
    else:
        log(f"Task dir kept for inspection: {task_dir}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
