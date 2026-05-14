"""sw_lib.tui — MonitorTUI: 三段式流式终端监控面板（支持选项焦点切换）

布局：
  Stage Title → 任务名 + 阶段 + 模型 + 状态
  Log 区域   → engine/agent 对话流
  Options    → Agent 提问的选项（Tab 键聚焦，Shift+Tab 返回输入）
  Input 区域 → / 命令 / 选项选择 / 自由输入

焦点切换：
  Tab        → 输入焦点 → 选项焦点
  Shift+Tab  → 选项焦点 → 输入焦点
  选项焦点下：
    ↑/↓ 或 j/k  → 上下移动光标
    Enter        → 选择当前高亮选项
    1-9          → 快捷选择对应编号选项
    a-z          → 快捷选择对应字母选项

技术：
  tty.setcbreak + os.read 绕过 Python TextIOWrapper 缓冲
  termios 保存/恢复终端设置
  ANSI 转义序列管理输入行和选项面板
  threading.Lock 保护 stdout 写入
"""

import io
import os
import re
import selectors
import sys
import termios
import threading
import queue
import tty

from .config import STAGES, STAGE_NAMES, TASKS, ROOT
from .engine import WorkflowEngine
from .utils import sw_log

OPTION_PATTERNS = [
    re.compile(r'^\s*[-*]\s+(?:⭐\s*)?\*{0,2}选项\s*([A-Z\d]+)[：:]\s*(.+)', re.UNICODE),
    re.compile(r'^\s*[-*]\s+\[[ xX]\]\s*([A-Z\d]+)[.、)]\s*(.+)', re.UNICODE),
    re.compile(r'^\s*(\d+)[.、)）]\s+(.+)', re.UNICODE),
    re.compile(r'^\s*[-*]\s+(⭐\s*)?\*{0,2}([A-Z])[.、)：]\s*(.+)', re.UNICODE),
]


def extract_options(lines, max_age=20):
    """从最近的日志行中提取选项列表。

    扫描 agent 输出中包含选项模式（选项A、1. 等）的行，
    返回 [(label, text), ...] 列表。仅扫描最近 max_age 行。
    """
    recent = lines[-max_age:] if len(lines) > max_age else lines
    options = []
    for source, msg in recent:
        if source != "agent":
            continue
        for line in msg.splitlines():
            line = line.strip()
            if not line:
                continue
            for pat in OPTION_PATTERNS:
                m = pat.match(line)
                if m:
                    label = m.group(1) or m.group(2)
                    text = m.group(m.lastindex)
                    text = text.rstrip('*').strip()
                    if label and text:
                        options.append((label, text))
                    break
    return options


class MonitorTUI:
    def __init__(self, name, stage, stage_idx, agent_name):
        self.name = name
        self.stage = stage
        self.stage_idx = stage_idx
        self.agent_name = agent_name
        self.log_lines = []
        self.running = True
        self.input_buffer = ""
        self.cmd_queue = queue.Queue()
        self._old_term = None
        self._stdin_fd = None
        self._is_tty = False
        try:
            self._stdin_fd = sys.stdin.fileno()
            self._is_tty = os.isatty(self._stdin_fd)
        except (io.UnsupportedOperation, AttributeError, ValueError):
            pass
        self._write_lock = threading.Lock()

        # 焦点系统: "input" 或 "options"
        self._focus = "input"
        self._options = []
        self._option_cursor = 0

        callbacks = {"add_log": self._add_log, "is_running": lambda: self.running}

        self.engine = WorkflowEngine(
            name=name, stage=stage, stage_idx=stage_idx,
            agent_name=agent_name, callbacks=callbacks
        )

    @property
    def model_name(self):
        return self.engine.model_name

    # ── ANSI 颜色常量 ──
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD_RED = "\033[1;31m"
    WHITE = "\033[37m"
    BOLD_WHITE = "\033[1;37m"
    BOLD_GREEN = "\033[1;32m"
    BOLD_CYAN = "\033[1;36m"
    REVERSE = "\033[7m"
    BG_CYAN = "\033[46m"
    BG_BLUE = "\033[44m"

    def run(self):
        if self._is_tty and self._stdin_fd is not None:
            try:
                self._old_term = termios.tcgetattr(self._stdin_fd)
            except (termios.error, OSError):
                self._old_term = None

        try:
            if self._old_term is not None:
                tty.setcbreak(self._stdin_fd)
                new = termios.tcgetattr(self._stdin_fd)
                new[3] &= ~termios.ISIG
                new[3] &= ~termios.ECHO
                new[3] &= ~termios.IEXTEN
                termios.tcsetattr(self._stdin_fd, termios.TCSANOW, new)

            self._print_line("sw", f"monitor started — {self.stage} ({STAGE_NAMES[self.stage_idx]})")
            self._print_line("sw", "Tab=切换焦点 ↑↓=选择 Enter=确认 /命令 /q退出")
            self._draw_screen()

            if self.agent_name:
                self.engine.run_stage()

            if self._is_tty and self._stdin_fd is not None:
                threading.Thread(target=self._input_loop, daemon=True).start()
            else:
                threading.Thread(target=self._input_loop_sync, daemon=True).start()

            while self.running:
                try:
                    cmd = self.cmd_queue.get(timeout=0.04)
                    self._dispatch(cmd)
                except queue.Empty:
                    pass
        finally:
            self.running = False
            self._shutdown()
            if self._old_term is not None:
                try:
                    termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self._old_term)
                except (termios.error, OSError):
                    pass

    # ── 终端输出 ──

    def _print_line(self, source, msg):
        color_map = {
            "agent": self.GREEN, "user": self.CYAN,
            "system": self.YELLOW, "error": self.BOLD_RED,
        }
        prefix_map = {
            "agent": "agent ", "user": "user  ",
            "system": "sys   ", "error": "ERR   ",
        }
        color = color_map.get(source, self.DIM)
        prefix = prefix_map.get(source, "sw    ")
        ts = self._now()[-8:]
        line = f"{self.DIM}[{ts}]{self.RESET} {color}{prefix}{self.RESET} "
        if source == "sw":
            line += f"{self.DIM}{msg}{self.RESET}"
        else:
            line += msg
        with self._write_lock:
            sys.stdout.write('\r\033[K')
            sys.stdout.write(line + '\n')
            self._draw_screen_unlocked()
            sys.stdout.flush()

    @staticmethod
    def _now():
        from .utils import now
        return now()

    def _detect_options(self):
        options = extract_options(self.log_lines)
        if options != self._options:
            self._options = options
            self._option_cursor = 0

    def _draw_screen(self):
        with self._write_lock:
            self._draw_screen_unlocked()
            sys.stdout.flush()

    def _draw_screen_unlocked(self):
        self._detect_options()
        self._draw_options_unlocked()
        self._draw_input_unlocked()

    def _draw_options_unlocked(self):
        if not self._options:
            return
        focused = (self._focus == "options")
        for i, (label, text) in enumerate(self._options):
            cursor = "▸" if i == self._option_cursor and focused else " "
            if i == self._option_cursor and focused:
                line = f"  {self.REVERSE}{self.BOLD}{cursor} [{label}]{self.RESET} {text}"
            elif focused:
                line = f"  {self.DIM}{cursor} [{label}]{self.RESET} {self.DIM}{text}{self.RESET}"
            else:
                line = f"  {self.DIM}{cursor} [{label}]{self.RESET} {self.DIM}{text}{self.RESET}"
            sys.stdout.write('\r\033[K')
            sys.stdout.write(line + '\n')
        if focused:
            hint = f"  {self.CYAN}↑↓ 选择 Enter 确认 数字/字母快捷选 Shift+Tab 返回输入{self.RESET}"
        else:
            hint = f"  {self.DIM}Tab 切换到选项{self.RESET}"
        sys.stdout.write('\r\033[K')
        sys.stdout.write(hint + '\n')

    def _draw_input(self):
        with self._write_lock:
            self._draw_input_unlocked()
            sys.stdout.flush()

    def _draw_input_unlocked(self):
        agent = self.engine.agent
        if agent and hasattr(agent, 'status'):
            status_str = agent.status
        elif agent and hasattr(agent, 'is_active') and agent.is_active:
            status_str = "active"
        else:
            status_str = "idle"

        status_icons = {
            "connecting": f"{self.YELLOW}⏳{self.RESET}",
            "active": f"{self.BOLD_GREEN}●{self.RESET}",
            "waiting": f"{self.YELLOW}◉{self.RESET}",
            "idle": f"{self.DIM}○{self.RESET}",
            "error": f"{self.RED}✗{self.RESET}",
        }
        status = status_icons.get(status_str, f"{self.DIM}○{self.RESET}")

        status_hint = ""
        if status_str == "connecting":
            status_hint = " 连接中..."
        elif status_str == "waiting":
            status_hint = " 等待回复"
        elif status_str == "error":
            status_hint = " 连接失败"

        focused_indicator = f"{self.CYAN}▸{self.RESET}" if self._focus == "input" else f"{self.DIM}▸{self.RESET}"

        sys.stdout.write('\r\033[K')

        ui_prefix = (
            f"{status} {self.BOLD_WHITE}{self.name}{self.RESET} "
            f"{self.BOLD_CYAN}{self.stage}{self.RESET} "
            f"{self.DIM}({self.model_name}){self.RESET}"
            f"{self.DIM}{status_hint}{self.RESET}"
        )

        if self._focus == "options":
            prompt = f"{self.DIM}▸ 输入(禁用) | Tab→选项{self.RESET}"
        elif self.input_buffer:
            prompt = f"{focused_indicator} {self.YELLOW}{self.input_buffer}▌{self.RESET}"
        else:
            prompt = f"{focused_indicator} {self.DIM}/cmd /q退出{self.RESET}"

        sys.stdout.write(f"{ui_prefix} {prompt}")

    # ── 非阻塞输入 ──

    def _input_loop_sync(self):
        while self.running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    self.cmd_queue.put(line)
            except (EOFError, KeyboardInterrupt, OSError):
                break

    def _input_loop(self):
        fd = self._stdin_fd
        selector = selectors.SelectSelector()
        selector.register(fd, selectors.EVENT_READ)

        while self.running:
            try:
                events = selector.select(timeout=0.05)
                if not events:
                    continue
                raw = os.read(fd, 16)
                if not raw:
                    break

                # 处理转义序列
                if raw.startswith(b'\x1b['):
                    seq = raw.decode("utf-8", errors="replace")
                    self._handle_escape(seq)
                    continue

                for ch in raw.decode("utf-8", errors="replace"):
                    if ch in ('\n', '\r'):
                        if self._focus == "options" and self._options:
                            self._select_option(self._option_cursor)
                        elif self.input_buffer.strip():
                            self.cmd_queue.put(self.input_buffer)
                            self.input_buffer = ""
                        self._draw_screen()
                    elif ch == '\t':
                        self._toggle_focus()
                    elif ch == '\x7f' or ch == '\b':
                        if self._focus == "input":
                            self.input_buffer = self.input_buffer[:-1]
                            self._draw_input()
                    elif ch == '\x03':
                        self.running = False
                        break
                    elif ch == '\x04':
                        self.running = False
                        break
                    elif ch == '\x1b':
                        try:
                            next_bytes = os.read(fd, 4)
                            seq = ch + next_bytes.decode("utf-8", errors="replace")
                            self._handle_escape(seq)
                        except OSError:
                            pass
                    elif ch.isprintable():
                        if self._focus == "input":
                            self.input_buffer += ch
                            self._draw_input()
                        elif self._focus == "options":
                            if ch in ('j', 'k') and self._options:
                                if ch == 'k':
                                    self._option_cursor = max(0, self._option_cursor - 1)
                                else:
                                    self._option_cursor = min(len(self._options) - 1, self._option_cursor + 1)
                                self._draw_screen()
                            else:
                                self._handle_option_char(ch)
            except (OSError, ValueError):
                break
        try:
            selector.unregister(fd)
        except (KeyError, ValueError):
            pass

    def _handle_escape(self, seq):
        import re
        if re.search(r'\[A$', seq) and self._focus == "options":
            self._option_cursor = max(0, self._option_cursor - 1)
            self._draw_screen()
        elif re.search(r'\[B$', seq) and self._focus == "options":
            self._option_cursor = min(len(self._options) - 1, self._option_cursor + 1)
            self._draw_screen()
        elif re.search(r'\[Z$', seq):
            self._toggle_focus(reverse=True)

    def _handle_option_char(self, ch):
        if not self._options:
            return
        if ch.isdigit() and 1 <= int(ch) <= len(self._options):
            self._select_option(int(ch) - 1)
        elif ch.isalpha():
            ch_upper = ch.upper()
            for i, (label, _) in enumerate(self._options):
                if label.upper() == ch_upper:
                    self._select_option(i)
                    return

    def _toggle_focus(self, reverse=False):
        if reverse:
            if self._focus == "options":
                self._focus = "input"
            else:
                if self._options:
                    self._focus = "options"
        else:
            if self._focus == "input":
                if self._options:
                    self._focus = "options"
            else:
                self._focus = "input"
        self._draw_screen()

    def _select_option(self, idx):
        if 0 <= idx < len(self._options):
            label, text = self._options[idx]
            self._add_log("user", f"选择 [{label}]: {text[:80]}")
            self._focus = "input"
            self.input_buffer = ""
            self.engine.handle_command("advance")
            self._draw_screen()

    # ── 命令分发 ──

    def _dispatch(self, cmd):
        cmd = cmd.strip()
        if not cmd:
            return

        if cmd == "/q":
            self._add_log("user", cmd)
            self.running = False
            return

        if cmd.startswith("/"):
            self._add_log("user", cmd)
            self.engine.handle_command(cmd[1:])
        else:
            self._add_log("user", cmd)
            self.engine.handle_command("advance")

    def _add_log(self, source, msg):
        self.log_lines.append((source, msg))
        if len(self.log_lines) > 2000:
            self.log_lines = self.log_lines[-1000:]
        self._print_line(source, msg)
        sw_log(self.name, msg[:500], source)

    def _shutdown(self):
        self.running = False
        self.engine.shutdown()
        self._draw_input()
        with self._write_lock:
            sys.stdout.write('\n')
            sys.stdout.flush()