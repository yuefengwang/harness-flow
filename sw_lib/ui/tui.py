"""sw_lib.tui — MonitorTUI: 基于 Rich 的结构化 Agent 对话面板

架构原则：
1. 界面与逻辑解耦：UI 状态通过 TUIState 维护，渲染由 Rich 声明式完成。
2. 数据驱动：任何状态变更触发 _refresh_display()，驱动 Live 刷新。
3. 结构化输入：用户输入不直接透传，而是根据当前模式（options/yesno/none）进行校验和转换。
"""

import io
import os
import re
import selectors
import subprocess
import sys
import termios
import threading
import queue
import time
import tty
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional

# 从 config 引入 Rich 组件 (假设 HAS_RICH 为 True，若环境不支持则 MonitorTUI 无法启动)
from ..core.config import (
    STAGES, STAGE_NAMES, TASKS, ROOT, HOOKS_DIR, HAS_RICH,
    HOOK_TIMEOUT_SECONDS, AUTO_ADVANCE_MAX_STAGES,
    is_auto_advance, is_auto_answer,
    Layout, Live, Panel, Text, Console, box,
)
from ..workflow.base import StageInput
from ..workflow.runtime import WorkflowRuntime
from ..core.state import read_state, write_state
from ..core.utils import sw_log, now

# 如果环境没有 Rich，退回到基础 Console 占位
if not HAS_RICH:
    class Panel: pass
    class Text: pass
    class Layout: pass
    class Live: pass

# ── 模式识别正则 ──

# 面板切到「等用户拍板」时打进 .log 的标记。自动化驱动（tests/e2e-flow）靠它
# 知道按键此刻不会被 _validate_input 拒绝 —— 比猜「日志静默几秒」可靠。
PROMPT_READY_MARKER = "⏸ 等待用户拍板:"

# 04-review 的 Route 值：模板与 check_04-review.sh 都要求这种大小写形式，
# 而 parse_route_from_ai_output 返回的是 STAGES 里的小写形式。
_ROUTE_LABELS = {
    "05-archive": "05-Archive",
    "03-coding": "03-Coding",
    "02-planning": "02-Planning",
    "01-brainstorming": "01-Brainstorming",
}


def _normalize_route_label(route: str) -> str:
    """把 STAGES 小写形式转成 Route 字段要求的大小写。"""
    return _ROUTE_LABELS.get((route or "").lower(), route)


YES_NO_PATTERNS = [
    r'do\s+you\s+approve', r'do\s+you\s+agree', r'do\s+you\s+accept',
    r'(?:shall|may|can)\s+I\s+proceed', r'proceed\s+to\s+(?:the\s+)?(?:next\s+)?stage',
    r'proceed\s+to\s+(?:the\s+)?(?:planning|review|archive|coding)',
    r'yes[/\\]?no', r'(?:is\s+this|are\s+you)\s+(?:acceptable|satisfied|ok)',
    r'\u662f\u5426\s*(?:\u540c\u610f|\u6279\u51c6|\u8ba4\u53ef|\u53ef\u4ee5)',
    r'\u8bf7\u8f93\u5165\s*yes\s*/?\s*no',
    r'approve\s+this\s+design', r'ready\s+to\s+(?:proceed|move\s+on|advance)',
    r'can\s+(?:we|I)\s+(?:move|go)\s+(?:forward|ahead)',
]

OPTION_PATTERNS = [
    re.compile(r'^\s*[-*]\s+(?:\u2b50\s*)?\*{0,2}\u9009\u9879\s*([A-Z\d]+)[\uff1a:]\s*(.+)', re.UNICODE),
    re.compile(r'^\s*[-*]\s+\[[ xX]\]\s*([A-Z\d]+)[.、)]\s*(.+)', re.UNICODE),
    re.compile(r'^\s*(\d+)[.、)\uff09]\s+(.+)', re.UNICODE),
    re.compile(r'^\s*[-*]\s+(?:\u2b50\s*)?\*{0,2}([A-Z])[.、)\uff09\u3001:]\s*(.+)', re.UNICODE),
    re.compile(r'^\s*([A-Z])[.、)\uff09：:]\s+(.{2,})', re.UNICODE),
]

# ── 问题上下文检测关键词 ──
# 只有 agent 消息中包含以下指标之一时，才会将其中的编号行视为结构化选项

# 强信号：明确的选项提问，不需要 proximity check
_STRONG_QUESTION_KEYWORDS = [
    r'\u8bf7\u9009\u62e9',                        # 请选择
    r'\bchoose\s+(?:one|an|from|between)\b',       # choose one / choose from
    r'\bselect\s+(?:one|an|from|between)\b',       # select one / select from
    r'\bpick\s+(?:one|an)\b',                       # pick one
    r'\bwhich\s+(?:one|option|approach|solution)\b', # which one/option/approach
    r'\boptions?\s+(?:are|include|available)\b',    # options are / options include
    r'(?:please|pls)\s+(?:choose|select|pick)\b',   # please choose/select/pick
    r'^[-*]\s+\[[ xX]\]',                            # checklist marker (- [ ] or - [x])
    r'\u8bf7\s*(?:\u9009\u62e9|\u9009\u62e9|\u56de\u7b54|\u8f93\u5165)\b',  # 请选择/请回答/请输入
]

# 弱信号：需要接近编号列表才算有效提问上下文
_WEAK_QUESTION_KEYWORDS = [
    r'\?', r'\uff1f',                              # ? / ？
    r'\u9009\u62e9', r'\u9009\u9879',               # 选择 / 选项
    r'\bchoose\b', r'\bselect\b', r'\bpick\b',       # choose / select / pick (loose)
    r'\boptions?\b',                                 # option(s) (loose)
    r'\u51b3\u5b9a',                                 # 决定
]

# 用于 proximity check 的选项行模式（与 OPTION_PATTERNS 中的编号/字母模式对齐）
_OPTION_LINE_PATTERN = re.compile(
    r'^\s*(\d+|[A-Z])[.、)\uff09\u3001：:]\s+',
    re.UNICODE
)

# 描述性步骤特征：如果选项文本包含以下信号，说明这不是选项而是编号步骤
_STEP_SIGNAL_PATTERNS = [
    r'→',                          # flow arrow (最可靠信号)
    r'`[^`]+`',                    # backtick code
    r'\b(?:pip|npm|git|curl|docker|mvn|yarn|npx)\b',  # CLI commands
    r'\b(?:install|deploy|build|configure|scan|detect|compile|run)\b', # build/exec verbs
    r'\b(?:localhost|http[s]?://)', # URLs
    r'\b(?:ModuleNotFoundError|ImportError|PermissionError)\b',  # error names
]

def _looks_like_step(text: str) -> bool:
    """检查文本是否像描述性步骤而非选项标签"""
    for pat in _STEP_SIGNAL_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False

def _has_question_context(messages) -> bool:
    """检查消息块中是否包含提问信号（问号或选择类关键词）。

    检测逻辑分两层：
    1. 强信号（明确的选项请求）→ 直接返回 True，不需要 proximity 检查。
    2. 弱信号（?、选择、choose 等）→ 只有出现在编号/字母列表行附近
       （同一行或上下 2 行内）才算有效提问上下文，防止将描述性编号列表
       （如 "1. 情况说明 2. 状态更新 3. 结论。这样可以吗？"）误判为选项。

    同时支持 List[Tuple[str,str]] (extract_options 调用) 和 List[str] 
    (detect_input_mode 调用) 两种输入格式。
    """
    if not messages:
        return False
    if isinstance(messages[0], tuple):
        msgs = [msg for _, msg in messages]
    else:
        msgs = list(messages)

    combined = "\n".join(msgs)

    for pat in _STRONG_QUESTION_KEYWORDS:
        if re.search(pat, combined, re.IGNORECASE):
            return True

    for pat in _WEAK_QUESTION_KEYWORDS:
        if not re.search(pat, combined, re.IGNORECASE):
            continue

        all_lines: List[Tuple[int, int, str]] = []
        for mi, msg in enumerate(msgs):
            for li, line in enumerate(msg.splitlines()):
                all_lines.append((mi, li, line.strip()))

        first_option_idx = next(
            (i for i, (_, _, line) in enumerate(all_lines) if _OPTION_LINE_PATTERN.match(line)),
            None,
        )

        for idx, (mi, li, signal_line) in enumerate(all_lines):
            if not signal_line:
                continue
            if not re.search(pat, signal_line, re.IGNORECASE):
                continue

            if _OPTION_LINE_PATTERN.match(signal_line):
                return True

            if first_option_idx is not None and idx < first_option_idx:
                if first_option_idx - idx <= 3:
                    return True

    return False


def extract_options(lines: List[Tuple[str, str]], max_age: int = 20) -> List[Tuple[str, str]]:
    """
    从最近的日志记录中扫描并提取 Agent 提出的结构化选项。
    
    仅从最近日志窗口末尾的 **最新 agent 消息连续块** 中提取选项，
    避免将旧问题的选项与当前问题的选项混淆。
    
    增加提问上下文检查：如果 agent 消息块中不包含问号、选择关键词
    或 option-list 标记，则跳过提取，防止将伪代码中的编号列表
    (如 "1. xxxx 2. xxxx") 误判为结构化选项。
    
    Args:
        lines: 日志行列表 (source, msg)
        max_age: 扫描最近的行数，默认 20
        
    Returns:
        提取到的选项列表 [(label, text), ...]
    """
    recent = lines[-max_age:] if len(lines) > max_age else lines

    # 从末尾向前扫描，收集最新的连续 agent 消息块
    latest_agent_block: List[Tuple[str, str]] = []
    for source, msg in reversed(recent):
        if source == "agent":
            latest_agent_block.append((source, msg))
        elif latest_agent_block:
            break  # 已找到 agent 块末尾，停止扫描
    latest_agent_block.reverse()

    if not latest_agent_block:
        return []

    # 提问上下文守卫：agent 消息中必须包含问号或选择/选项类关键词，
    # 否则跳过选项提取，避免将伪代码中的编号列表误判为提问选项
    if not _has_question_context(latest_agent_block):
        return []

    # 反向扫描选项（从消息末尾向前）：
    # 1. 只保留最后一个连续选项区块（即最新问题对应的选项），
    #    丢弃更早出现的编号列表（如任务描述中的 "1. 居中 2. 运镜 3. 字幕"）。
    # 2. 同一区块内，后出现的选项（更新近的问题）优先覆盖先出现的。
    seen: set = set()
    options_reversed: List[Tuple[str, str]] = []
    _block_sealed = False     # 遇到非选项行后封死，丢弃更早的选项组
    _found_option = False     # 当前区块中至少发现了一个选项
    for _src, msg in reversed(latest_agent_block):
        for line in reversed(msg.splitlines()):
            line = line.strip()
            if not line:
                continue
            is_option = False
            for pat in OPTION_PATTERNS:
                m = pat.match(line)
                if m:
                    if _block_sealed:
                        is_option = True  # 标记已处理过，避免误触 seal
                        break
                    label: str = m.group(1) or m.group(2)
                    text: str = m.group(m.lastindex)
                    text = text.rstrip('*').strip()
                    if label and text and label not in seen:
                        seen.add(label)
                        options_reversed.append((label, text))
                        _found_option = True
                    is_option = True
                    break
            if not is_option and _found_option:
                # 首次在选项行之后遇到非选项行 → 封死区块
                _block_sealed = True
    # 恢复为正向顺序（从上到下）
    options_reversed.reverse()

    # 选项内容过滤器：排除看起来像"描述性步骤"的编号行
    # 如果任何选项文本包含 → 箭头或 CLI 命令等步骤特征，则判定为描述性步骤
    if options_reversed and any(_looks_like_step(text) for _, text in options_reversed):
        return []

    return options_reversed


def detect_input_mode(log_lines: List[Tuple[str, str]], options: List[Tuple[str, str]]) -> str:
    """
    根据最近的对话上下文自动推断当前应处于哪种交互模式。
    
    逻辑准则：
    1. 只有当最近的一条有效消息来自 Agent 时，才允许进入交互模式（yesno/options）。
    2. 如果用户已经回复（最新消息源为 user），则必须退出交互模式，回到 none。
    3. 选项模式需要同时满足：(a) 从 agent 输出中提取到了选项行，(b) agent
       的上下文包含明显的提问信号（问号、选择指令等），避免将普通编号列表
       （如步骤说明）误判为需要用户选择的选项。
    """
    if not log_lines:
        return "none"
        
    # 获取最近的一条非 sw 类型的日志源
    # 我们跳过 'sw' 日志，因为像 '阶段产出已就绪' 这样的提示不应打断 Agent 的提问状态
    last_relevant_src = None
    for src, _ in reversed(log_lines):
        if src in ("agent", "user", "system"):
            last_relevant_src = src
            break
            
    if last_relevant_src != "agent":
        return "none"

    recent_agent = []
    for src, msg in reversed(log_lines):
        if src == "agent":
            recent_agent.append(msg)
            if len(recent_agent) >= 3:
                break
                
    if not recent_agent:
        return "none"
        
    combined = "\n".join(recent_agent).lower()
    for pat in YES_NO_PATTERNS:
        if re.search(pat, combined, re.IGNORECASE):
            return "yesno"
    if options and _has_question_context(recent_agent):
        return "options"
    return "none"


@dataclass
class TUIState:
    """
    UI 状态机数据模型。封装了所有驱动界面更新的变量，确保渲染逻辑是纯函数。
    """
    name: str                        # 任务名称
    stage: str                       # 当前阶段标识
    stage_idx: int                   # 阶段索引
    model_name: str = "N/A"          # 当前使用的模型名
    agent_status: str = "idle"       # Agent 状态 (connecting/active/waiting/idle/error)
    log_lines: List[Tuple[str, str]] = field(default_factory=list) # 历史对话
    input_buffer: str = ""           # 当前实时输入的文本缓冲区
    input_mode: str = "none"         # 输入模式 (none/yesno/options)
    options: List[Tuple[str, str]] = field(default_factory=list)   # 当前可选的结构化选项
    
    # 结构化提问 (Toolbox 发起) 专用状态
    pending_questions: List[Dict[str, Any]] = field(default_factory=list)
    current_q_idx: int = 0
    
    error_msg: str = ""              # 输入校验错误信息
    
    # 滚动管理
    log_scroll_offset: int = 0       # 距离底部的行偏移量 (0 为最新)
    
    # 任务结算
    is_settled: bool = False         # 是否已进入最终结算流程


class MonitorTUI:
    """
    Harness-Flow 核心交互界面 (TUI)。
    
    采用声明式渲染架构，基于 rich.live 提供实时刷新的监控面板。
    支持异步输入读取和结构化 Agent 对话。
    """
    
    def __init__(self, name: str, stage: str, stage_idx: int, agent_name: str):
        """
        初始化 TUI 监控器。
        
        Args:
            name: 任务 ID
            stage: 初始阶段
            stage_idx: 阶段索引
            agent_name: 指定的 Agent 角色名
        """
        self.state = TUIState(name=name, stage=stage, stage_idx=stage_idx)
        self.agent_name = agent_name
        self.running: bool = True
        self.cmd_queue: queue.Queue = queue.Queue()
        self._write_lock: threading.Lock = threading.Lock()
        
        # 终端底层控制
        self._old_term: Optional[List[Any]] = None
        self._stdin_fd: Optional[int] = None
        self._is_tty: bool = False
        try:
            self._stdin_fd = sys.stdin.fileno()
            self._is_tty = os.isatty(self._stdin_fd)
        except (io.UnsupportedOperation, AttributeError, ValueError):
            pass

        # 结构化提问状态机
        self._q_res_queue: Optional[queue.Queue] = None
        self._q_answers: List[str] = []

        # 「等待用户拍板」标记的去重键。渲染轮询每 40ms 跑一次，不去重会把
        # .log 刷满同一行。入口关闭（用户已拍板）时清空，这样返工回到同一
        # 阶段、面板再次打开时还会记录 —— 否则第二轮的驱动会一直等不到信号。
        self._prompt_logged: str = ""

        # 自动推进 / 自动代答：两个正交开关，构造时快照避免运行中热改导致行为跳变。
        #   _auto_advance —— 阶段边界：stage 结束后是否免去 /advance
        #   _auto_answer  —— 阶段内部：agent 提问是否代答（无人值守才开）
        self._auto_advance: bool = is_auto_advance()
        self._auto_answer: bool = is_auto_answer()
        self._auto_count: int = 0
        self._auto_stopped: bool = False

        # 编排引擎初始化
        self.callbacks = {
            "add_log": self._add_log, 
            "is_running": lambda: self.running,
            "on_ask_user": self._on_ask_user,
            "on_settlement": self._on_settlement
        }
        
        # 预先解析模型名用于显示
        from ..core.config import resolve_agent_model
        self._model_name = resolve_agent_model(stage, agent_name)

        # Rich 渲染引擎组件
        self.console = Console()
        self.live: Optional[Live] = None
        self.layout: Optional[Layout] = None

    @property
    def input_buffer(self) -> str:
        """兼容旧测试的属性转发"""
        return self.state.input_buffer

    @property
    def log_lines(self) -> List[Tuple[str, str]]:
        """兼容旧测试的属性转发"""
        return self.state.log_lines

    @property
    def model_name(self) -> str:
        """获取当前引擎中解析出的具体模型名"""
        return self._model_name

    def _stage_has_output(self) -> bool:
        """Check if the current stage file has AI Output."""
        stage_file = TASKS / self.state.name / f"{self.state.stage}.md"
        if not stage_file.exists():
            return False
        content = stage_file.read_text(encoding="utf-8", errors="replace")
        return "AI Output" in content

    # ── 生命周期 ──

    def run(self):
        if not HAS_RICH:
            print("错误: 当前环境未安装 rich 库，无法启动 TUI 面板。")
            return

        if self._is_tty and self._stdin_fd is not None:
            try:
                self._old_term = termios.tcgetattr(self._stdin_fd)
            except (termios.error, OSError):
                self._old_term = None

        try:
            # 进入 Raw 模式处理键盘输入
            if self._old_term is not None:
                tty.setcbreak(self._stdin_fd)
                new = termios.tcgetattr(self._stdin_fd)
                new[3] &= ~termios.ISIG
                new[3] &= ~termios.ECHO
                new[3] &= ~termios.IEXTEN
                termios.tcsetattr(self._stdin_fd, termios.TCSANOW, new)

            # 初始化 Rich 布局
            self.layout = self._create_layout()

            with Live(self.layout, refresh_per_second=20, screen=True, console=self.console) as live:
                self.live = live

                # 仅在 stage 未启动时启动引擎（跳过已完成/idle 的 stage）
                st = read_state(self.state.name)
                st_status = st.get("stage_status", "pending") if st else "pending"
                if st_status == "pending":
                    self._add_log("sw", f"启动阶段: {self.state.stage}")
                    executor = WorkflowRuntime.get_executor()
                    stage_input = StageInput(
                        task_name=self.state.name,
                        stage=self.state.stage,
                        stage_idx=self.state.stage_idx,
                        metadata={"callbacks": self.callbacks}
                    )
                    threading.Thread(target=executor.invoke, args=(stage_input,), daemon=True).start()
                elif st_status == "idle" and not self._stage_has_output():
                    # idle 但无 AI Output → 之前异常退出未产出，重新启动
                    self._add_log("sw", f"阶段无产出，重启 agent...")
                    write_state(self.state.name, {**st, "stage_status": "pending"})
                    executor = WorkflowRuntime.get_executor()
                    stage_input = StageInput(
                        task_name=self.state.name,
                        stage=self.state.stage,
                        stage_idx=self.state.stage_idx,
                        metadata={"callbacks": self.callbacks}
                    )
                    threading.Thread(target=executor.invoke, args=(stage_input,), daemon=True).start()
                else:
                    self._add_log("sw", f"阶段状态: {st_status}，跳过 agent 重连")

                self.state.model_name = self.model_name

                # 启动输入线程
                if self._is_tty and self._stdin_fd is not None:
                    threading.Thread(target=self._input_loop, daemon=True).start()
                else:
                    threading.Thread(target=self._input_loop_sync, daemon=True).start()

                # 主事件循环
                while self.running:
                    try:
                        cmd = self.cmd_queue.get(timeout=0.04)
                        self._dispatch(cmd)
                        # 立即同步状态，确保渲染前数据一致（如切换到下一个结构化问题）
                        self._update_agent_status()
                        self._refresh_display()
                    except queue.Empty:
                        # 轮询检查 Agent 状态变化
                        self._update_agent_status()
                        self._maybe_auto_advance()
                        self._refresh_display()

        finally:
            self.running = False
            # 关闭当前活跃 Agent
            try:
                executor = WorkflowRuntime.get_executor()
                if executor.active_stage and executor.active_stage.active_agent:
                    executor.active_stage.active_agent.shutdown()
            except Exception:
                pass
                
            if self._old_term is not None:
                try:
                    termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self._old_term)
                except (termios.error, OSError):
                    pass

    # ── 渲染逻辑 ──

    def _create_layout(self) -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=6)
        )
        return layout

    def _refresh_display(self):
        """核心渲染函数：根据当前 State 生成 UI 并更新 Live 视图"""
        if not self.live or not self.layout:
            return
            
        with self._write_lock:
            # 1. 更新 Header
            self.layout["header"].update(self._render_header())
            
            # 2. 更新 Body (Logs)
            self.layout["body"].update(self._render_logs())
            
            # 3. 更新 Footer (Interactive Prompt)
            self.layout["footer"].update(self._render_footer())

    def _render_header(self) -> Panel:
        status_icons = {
            "connecting": "[yellow]⏳[/]",
            "active": "[bold green]●[/]",
            "waiting": "[yellow]◉[/]",
            "idle": "[dim]○[/]",
            "error": "[bold red]✘[/]",
        }
        icon = status_icons.get(self.state.agent_status, "[dim]○[/]")
        title = f"{icon} [bold white]{self.state.name}[/] | {self.state.stage} [dim]({self.state.model_name})[/]"
        if self._auto_advance:
            mode = "[dim]手动[/]" if self._auto_stopped else "[green]自动[/]"
            title += f" [dim]|[/] {mode}"
        
        status_text = ""
        if self.state.agent_status == "connecting": status_text = " [yellow]连接中...[/]"
        elif self.state.agent_status == "waiting": status_text = " [yellow]Agent 正在思考...[/]"
        elif self.state.agent_status == "error": status_text = " [bold red]连接异常[/]"
        
        return Panel(
            Text.from_markup(f"{title}{status_text}"),
            box=box.ROUNDED,
            style="blue"
        )

    def _on_settlement(self):
        """引擎回调：进入任务结算流程"""
        self.state.is_settled = True
        self.state.input_mode = "options" # 借用 options 模式处理 A/B/C
        self.state.options = [
            ("A", "自动提交并物理归档 (移动到 .trash)"),
            ("B", "仅标记完成并保留目录"),
            ("C", "导出任务简报 (FINAL_REPORT.md)并退出")
        ]
        self._add_log("sw", "🎉 任务已进入结算阶段。")
        self._refresh_display()

    def _render_victory_card(self) -> Panel:
        """渲染成就总结卡片"""
        content = Text()
        content.append("\n  🏆 恭喜！任务已圆满完成！\n\n", style="bold yellow")
        
        # 统计数据
        ai_turns = len([l for l in self.state.log_lines if l[0] == "agent"])
        content.append(f"  任务编号 : ", style="bold white")
        content.append(f"{self.state.name}\n", style="cyan")
        content.append(f"  AI 对话  : ", style="bold white")
        content.append(f"{ai_turns} 轮交互\n", style="cyan")
        
        # 交付物检查
        content.append("\n  交付物清单:\n", style="bold white")
        for i, s in enumerate(STAGES):
            p = TASKS / self.state.name / f"{s}.md"
            marker = "[✓]" if p.exists() else "[ ]"
            style = "green" if p.exists() else "dim"
            content.append(f"    {marker} {STAGE_NAMES[i]} 文档 ({s}.md)\n", style=style)
            
        content.append("\n  ✨ 感谢您的辛勤工作！\n", style="italic dim")
        
        return Panel(
            content,
            title="[bold yellow] 任务结算卡片 (Task Summary) [/]",
            box=box.DOUBLE,
            border_style="bright_yellow",
            padding=(1, 2)
        )

    def _render_logs(self) -> Panel:
        # 如果已结算，优先显示胜利卡片
        if self.state.is_settled:
            return self._render_victory_card()

        # 1. 计算可用显示高度
        # 总高度 - header(3) - footer(6) - 边框(2)
        visible_height = max(5, self.console.size.height - 11)
        
        # 2. 构造 Rich Text
        text = Text()
        style_map = {
            "agent": "green", "user": "cyan",
            "system": "yellow", "error": "bold red", "sw": "dim"
        }
        prefix_map = {
            "agent": "agent ", "user": "user  ",
            "system": "sys   ", "error": "ERR   ", "sw": "sw    "
        }

        # 展平日志并关联源信息
        all_lines: List[Tuple[str, str, bool]] = []
        for source, msg in self.state.log_lines:
            lines = msg.splitlines()
            if not lines: lines = [""]
            for i, line in enumerate(lines):
                all_lines.append((source, line, i == 0))
        
        total_lines = len(all_lines)
        offset = self.state.log_scroll_offset
        
        # 计算显示范围
        end = total_lines - offset
        start = max(0, end - visible_height)
        
        # 修正越界
        if start < 0: start = 0
        if end > total_lines: end = total_lines
        if end < start: end = start

        visible_lines = all_lines[start:end]
        
        for source, content, is_first in visible_lines:
            style = style_map.get(source, "dim")
            prefix = prefix_map.get(source, "      ")
            
            if is_first:
                text.append(prefix, style=style)
            else:
                text.append("      ", style=style)
            
            text.append(content + "\n")
            
        # 5. 滚动条指示器
        title = " 对话日志 "
        if offset > 0:
            title += f" [已向上滚动 {offset} 行, 按 ↓ 返回最新] "
            
        return Panel(text, title=title, title_align="left", box=box.ROUNDED)

    def _render_footer(self) -> Panel:
        content = Text()
        
        # 1. 渲染选项
        if self.state.pending_questions:
            idx = min(self.state.current_q_idx, len(self.state.pending_questions) - 1)
            idx = max(0, idx)
            q = self.state.pending_questions[idx]
            q_text = q.get("question", "未知问题")
            labels = '/'.join(o[0] for o in self.state.options)
            content.append(f"❓ [{idx + 1}/{len(self.state.pending_questions)}] {q_text}\n", style="bold white")
            
            # 显示选项
            for label, text in self.state.options:
                content.append(f"  [{label}] ", style="cyan")
                content.append(f"{text}  ", style="white")
            content.append(f"\n👉 请输入 {labels} 选择: ", style="bold cyan")
            
        elif self.state.input_mode == "options":
            for label, text in self.state.options[:6]: # 限制显示数量
                content.append(f"  [{label}] ", style="cyan")
                content.append(f"{text}  ", style="white")
            labels = '/'.join(o[0] for o in self.state.options[:6])
            content.append(f"\n👉 请输入 {labels} 选择: ", style="bold cyan")
            
        elif self.state.input_mode == "yesno":
            content.append("📢 Agent 请求确认。请输入 ", style="yellow")
            content.append("yes", style="bold green")
            content.append(" 或 ")
            content.append("no", style="bold red")
            content.append("\n👉: ", style="bold cyan")
        
        else:
            if self.state.agent_status in ("active", "connecting"):
                content.append("⏳ Agent 正在处理中，请稍候...", style="dim italic")
            else:
                content.append("💬 输入消息或 /命令 (如 /advance, /q)\n", style="dim")
                content.append("👉: ", style="bold cyan")
                
        # 2. 渲染正在输入的缓冲区
        content.append(self.state.input_buffer, style="bold yellow")
        if self.state.input_buffer:
            content.append("█", style="blink") # 模拟光标
            
        # 3. 渲染错误提示
        if self.state.error_msg:
            content.append(f"\n⚠️ {self.state.error_msg}", style="bold red")
            
        return Panel(content, box=box.ROUNDED, style="cyan")

    # ── 逻辑更新 ──

    def _update_agent_status(self):
        # 优先从 WorkflowRuntime 获取当前活跃 Agent 状态 (需匹配当前任务名)
        executor = WorkflowRuntime.get_executor()
        agent = None
        if executor and executor.active_stage:
            potential_agent = executor.active_stage.active_agent
            if potential_agent and getattr(potential_agent, 'name', None) == self.state.name:
                agent = potential_agent
        
        if agent and hasattr(agent, 'status'):
            self.state.agent_status = agent.status
        else:
            # 如果没有活跃 agent，重置为 idle
            self.state.agent_status = "idle"
        
        # 同步工作流阶段状态到 UI 状态
        # 优先从 .state 文件读取（agent 可能直接修改磁盘状态）
        st = read_state(self.state.name)
        if st:
            self.state.stage = st.get("stage", self.state.stage)
            self.state.stage_idx = int(st.get("stage_idx", self.state.stage_idx))
        
        self.state.model_name = self.model_name
        
        # 探测模式。先把结果算在局部变量里，最后成对赋值 —— 中途改
        # self.state.options 会开出一个「input_mode 还是上一轮的 options、
        # options 已经空了」的窗口，输入线程在这 40ms 里提交的按键会被
        # _validate_input 判成无效选项、静默丢掉（e2e 里表现为按 A 没反应）。
        # 下面每个分支都要做磁盘 IO（读 .state），窗口足够大，必然被撞上。
        options = extract_options(self.state.log_lines)
        mode: Optional[str] = None
        # 拍板入口：None = 不是拍板态（提问中，去重键保持不动）
        prompt_kind: Optional[str] = None
        # 两个拍板入口都关了才清去重键 —— 提问态不算「关闭」，清了会让
        # 同一阶段的面板信号重复记录。
        clear_prompt_log = False
        if self.state.pending_questions:
            # 结构化提问下强制 options 模式
            idx = min(self.state.current_q_idx, len(self.state.pending_questions) - 1)
            idx = max(0, idx)
            q = self.state.pending_questions[idx]
            opts = []
            for i, o in enumerate(q.get("options", [])):
                label = str(i + 1)
                if "." in o: label = o.split(".")[0].strip()
                elif ":" in o: label = o.split(":")[0].strip()
                opts.append((label, o))
            options = opts
            mode = "options"
        elif self._is_review_routing_needed():
            options = [
                ("A", "归档 (05-Archive) — 代码通过，正常归档"),
                ("B", "返工编码 (03-Coding) — 代码需修复"),
                ("C", "返工规划 (02-Planning) — 设计需修订"),
            ]
            mode = "options"
            prompt_kind = "route"
        elif self._is_gate_signoff_needed():
            options = [
                ("A", f"批准 {self._stage_label()} — 签署 Gate 并推进到下一阶段"),
                ("B", "需要修订 — 继续与 Agent 讨论后再批准"),
            ]
            mode = "options"
            prompt_kind = "gate"
        else:
            mode = detect_input_mode(self.state.log_lines, options)
            clear_prompt_log = True

        self.state.options = options
        self.state.input_mode = mode
        if clear_prompt_log:
            # 返工回到同一阶段时面板会再次打开，那一轮必须重新记录，
            # 否则驱动等不到信号。
            self._prompt_logged = ""
        elif prompt_kind is not None:
            self._log_prompt_ready(prompt_kind)

    def _log_prompt_ready(self, kind: str) -> None:
        """面板切到「等用户拍板」时留一条痕，每个入口每轮只记一次。

        这是给自动化驱动用的信号。此前 e2e 只能靠「日志静默几秒」猜 agent
        说完没说完，而真正决定按键会不会被 _validate_input 拒绝的是这里的
        input_mode —— 猜静默偶尔早于状态切换，按 A 被拒，看起来像 TUI 卡住。
        状态转换本身可观测，就不必猜。
        """
        token = f"{kind}:{self.state.stage}"
        if self._prompt_logged == token:
            return
        self._prompt_logged = token
        label = "路由决策" if kind == "route" else "门禁签署"
        self._add_log("sw", f"{PROMPT_READY_MARKER} {label}（{self.state.stage}）")

    def _is_review_routing_needed(self) -> bool:
        """04-review 完成后，Route 未填写 → 需要用户选择路由"""
        if self.state.stage != "04-review":
            return False
        if self.state.agent_status not in ("idle", "waiting"):
            return False
        if self.state.pending_questions:
            return False
        try:
            from ..workflow import stage_state as ss
            return ss.read_route(self.state.name) is None
        except Exception:
            return False

    def _stage_label(self) -> str:
        """当前阶段的中文名，用于选项与日志文案。"""
        try:
            return STAGE_NAMES[STAGES.index(self.state.stage)]
        except (ValueError, IndexError):
            return self.state.stage

    def _is_gate_signoff_needed(self) -> bool:
        """agent 收尾但 Gate 未签署 → 需要用户签署后才能推进。

        每个阶段都要有这个入口。Gate 是「用户批准推进」的签名，没有任何 agent
        角色被要求去做（提示词甚至明确禁止 01 的 analyst 碰 Gate）。

        门禁状态读 .state（docs/design-json-state-source.md），不解析 Markdown：
        agent 的产出可以包含 ``## Gate`` 字样，靠找标记定位必然歧义。
        """
        if self.state.agent_status not in ("idle", "waiting"):
            return False
        if self.state.pending_questions:
            return False
        # 04 的 Route 未定时先走路由选择，那是比签署更前置的决定
        if self._is_review_routing_needed():
            return False
        try:
            from ..workflow import stage_state as ss
            gate = ss.read_gate(self.state.name, self.state.stage)
        except Exception:
            return False
        if not gate.exists or gate.signed:
            return False
        return self._agent_has_produced_output()

    def _agent_has_produced_output(self) -> bool:
        """agent 是否已经给出本阶段的产出。

        用来避免阶段刚启动就弹签署选项。产出有三种形态：
          1. _save_stage_output 追加的 AI Output 区；
          2. agent 直接用 write_file 回填模板（占位符消失）；
          3. 还只存在于对话里 —— 多轮模式下产出要等 /advance 触发 _stage_done
             之后才落盘，对话期间文件仍是原始模板。只看文件会让签署入口永不
             出现：用户按 A 被输入校验拒绝，字符还留在缓冲区，把下一条命令
             污染成 "A/advance"。

        注意这里读 Markdown 是正当的 —— 判断的是「agent 产出了没有」，
        属于内容检查，不是状态判定。
        """
        path = TASKS / self.state.name / f"{self.state.stage}.md"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        if content:
            if "## 🤖 AI Output" in content:
                return True
            if "___" not in content:
                return True
        return any(src == "agent" for src, _ in self.state.log_lines)

    def _add_log(self, source: str, msg: str):
        """引擎回调：添加日志"""
        self.state.log_lines.append((source, msg))
        if len(self.state.log_lines) > 2000:
            self.state.log_lines = self.state.log_lines[-1000:]
        
        # 自动聚焦到最新
        self.state.log_scroll_offset = 0
        
        # Split long messages into multi-line log entries for .log file readability
        sw_log(self.state.name, msg, source)

    def _on_ask_user(self, questions: List[Dict[str, Any]], res_queue: queue.Queue):
        """引擎回调：收到结构化提问"""
        # 注意：代答与 auto_advance 无关。auto_advance 只管阶段边界（一个 stage
        # 结束、下一个未开始时是否还要等 /advance）；ask_user 是阶段内的对话轮次，
        # 默认永远交还用户。只有显式开启 auto_answer（无人值守场景）才代答。
        if self._auto_answer:
            answers = [self._default_answer(q) for q in questions]
            self._add_log("sw", f"[auto] 已自动回答 {len(questions)} 个问题: {answers}")
            # 代答同样是「决定已经做出」，必须留痕：否则无人值守模式会卡在
            # 「选项组尚未拍板」上，而现场根本没有人可以去拍板。
            for idx, (q, a) in enumerate(zip(questions, answers)):
                label = (q.get("question") or q.get("header") or "").strip() \
                    or f"question_{idx + 1}"
                self._record_decision_direct(label, a, by="auto")
            res_queue.put(answers)
            return

        self.state.pending_questions = questions
        self.state.current_q_idx = 0
        self._q_answers = []
        self._q_res_queue = res_queue
        
        # 立即同步选项和模式，确保后续 _add_log 触发的渲染能显示问题
        self._update_agent_status()
        self._add_log("sw", f"❓ 收到 {len(questions)} 个结构化问题")

    @staticmethod
    def _default_answer(question: Dict[str, Any]) -> str:
        """无人值守模式下为单个结构化提问生成回答。"""
        options = question.get("options") or []
        if options:
            return str(options[0])
        return "请基于任务需求与你的专业判断自行决定，无需再确认。"

    # ── 输入分发 ──

    def _validate_input(self, text: str) -> Tuple[bool, str]:
        """校验输入是否符合当前模式的要求"""
        if text.startswith("/"):
            return True, ""
        
        if self.state.pending_questions:
            label = text.strip().upper()
            for l, _ in self.state.options:
                if l == label: return True, ""
            labels = '/'.join(o[0] for o in self.state.options)
            return False, f"请从选项中选择: {labels}"

        if self.state.input_mode == "none":
            if self.state.agent_status in ("active", "connecting"):
                return False, "Agent 正在处理中，请稍后输入"
            return True, ""
            
        if self.state.input_mode == "yesno":
            if text.strip().lower() in ("yes", "no", "y", "n"):
                return True, ""
            return False, "请输入 yes 或 no"
            
        if self.state.input_mode == "options":
            label = text.strip().upper()
            for l, _ in self.state.options:
                if l == label: return True, ""
            labels = '/'.join(o[0] for o in self.state.options)
            return False, f"请选择有效的选项: {labels}"
            
        return True, ""

    def _dispatch(self, cmd: str):
        if cmd == "/q":
            self.running = False
            return

        if cmd.startswith("/"):
            self._add_log("user", cmd)
            if cmd == "/advance":
                self._run_advance(auto=False)
                return

            WorkflowRuntime.get_executor().handle_command(cmd[1:])
            return

        # 1. 处理结构化提问回复
        if self.state.pending_questions:
            matched_label = cmd.strip().upper()
            answer_text = ""
            for l, t in self.state.options:
                if l == matched_label:
                    answer_text = t
                    break
            if not answer_text: answer_text = cmd
            
            self._q_answers.append(answer_text)
            self._add_log("user", f"[{self.state.current_q_idx + 1}] {answer_text}")
            self._record_decision(self.state.current_q_idx, answer_text)
            
            self.state.current_q_idx += 1
            self._refresh_display()
            if self.state.current_q_idx >= len(self.state.pending_questions):
                if self._q_res_queue:
                    # 安全性校验：确保回答列表不为空且长度匹配
                    results = list(self._q_answers)
                    if not results:
                        results = ["(无有效回复)"] * len(self.state.pending_questions)
                    
                    self._q_res_queue.put(results)
                    # 告知引擎：交互已完成，恢复运行状态 (如果需要)
                self.state.pending_questions = []
                self._q_res_queue = None
            return

        # 1.5 处理 04-review 路由选择（A/B/C — agent 完成、Route 未填时触发）
        # 必须与渲染层用同一个判定（_is_review_routing_needed）：只看 stage 和
        # input_mode 的话，Route 填好之后面板已经切成签署选项，这里却仍把 A 当
        # 路由，于是把同一个 Route 重复写一遍、Gate 永远签不上（任务 oooo）。
        if (self.state.input_mode == "options"
                and not self.state.is_settled
                and self._is_review_routing_needed()):
            review_routes = {"A": "05-Archive", "B": "03-Coding", "C": "02-Planning"}
            choice = cmd.strip().upper()
            if choice in review_routes:
                target = review_routes[choice]
                route_labels = {"05-Archive": "归档", "03-Coding": "编码", "02-Planning": "规划"}
                self._add_log("user", f"[{choice}] 返工到 {route_labels[target]} ({target})")
                if self._write_review_route(target):
                    self.state.input_mode = "none"
                    self.state.options = []
                    # 路由与签署是两个决定：Route 落定后面板会切成 Gate 签署
                    # 选项，由那一步去推进。这里不直接推进 —— 用户还没批准。
                    self._add_log("sw", f"✓ Route 已设置为 {target}，请批准 Gate 以推进。")
                else:
                    self._add_log(
                        "error",
                        "写入 Route 失败：04-review.md 缺少 **Route** 字段，"
                        "请让 Agent 补齐该字段后重试")
                self._refresh_display()
                return
            # 停在选项模式：agent 此时已 idle，穿透去发 agent 回复没人接收
            self._add_log("error", f"无效选项 {cmd!r}，请输入 A / B / C")
            self._refresh_display()
            return

        # 1.6 处理阶段签署（A/B — agent 完成、Gate 未勾选时触发，所有阶段共用）
        if (self.state.input_mode == "options"
                and not self.state.is_settled
                and self._is_gate_signoff_needed()):
            label = self._stage_label()
            choice = cmd.strip().upper()
            if choice == "A":
                self._add_log("user", f"[A] 批准{label}")
                if not self._write_gate_signoff():
                    self._add_log(
                        "error",
                        f"勾选 Gate 失败：{self.state.stage}.md 缺少 Gate 章节")
                    self._refresh_display()
                    return
                self.state.input_mode = "none"
                self.state.options = []
                self._add_log("sw", f"✓ {label}已批准（Gate 已签署），正在推进...")
                self._refresh_display()
                # 签署即推进：批准之后再要用户敲一次 /advance 是多余的一步 ——
                # 签 Gate 表达的就是「可以走了」。校验没过时 _run_advance 会
                # 打出待办并返回 blocked，此时 Gate 仍是签好的，用户补完内容
                # 再敲 /advance 即可，不必重新批准。
                self._run_advance(auto=False)
                return
            if choice == "B":
                self._add_log("user", "[B] 需要修订")
                self.state.input_mode = "none"
                self.state.options = []
                self._add_log("sw", "请继续说明需要修订的内容。")
                self._refresh_display()
                return
            self._add_log("error", f"无效选项 {cmd!r}，请输入 A（批准）或 B（修订）")
            self._refresh_display()
            return

        # 2. 处理普通 Agent 回复
        if self.state.is_settled:
            self._handle_settlement_choice(cmd)
            return

        response = self._build_agent_response(cmd)
        self._add_log("user", response)
        WorkflowRuntime.get_executor().answer(response)

    def _record_decision(self, q_idx: int, answer_text: str) -> None:
        """把用户对第 q_idx 个提问的选择记进 .state。

        选项组的拍板是判定依据，必须留在状态里；此前它只被拼成字符串回给
        agent，于是能否过闸取决于 agent 有没有回写 Markdown 记法（任务 T1）。
        """
        questions = self.state.pending_questions or []
        if not (0 <= q_idx < len(questions)):
            return
        q = questions[q_idx]
        label = (q.get("question") or q.get("header") or "").strip()
        if not label:
            # 没有题面就用序号兜底，至少保证「拍过板」这件事被记下来。
            label = f"question_{q_idx + 1}"
        self._record_decision_direct(label, answer_text, by="user")

    def _record_decision_direct(self, label: str, answer: str,
                                by: str = "user") -> None:
        """按题面直接落盘一条拍板记录。

        失败一律吞掉：这条路径在用户回答（或代答）的主流程上，抛异常会打断
        对话。最坏结果是闸门保守地继续拦着，比 TUI 崩掉好。
        """
        try:
            from ..workflow import stage_state as ss
            ss.record_decision(self.state.name, self.state.stage,
                               question=label, answer=answer, by=by)
        except Exception as e:
            self._add_log("sw", f"⚠️ 拍板记录写入失败（不影响本轮回答）: {e}")

    def _write_review_route(self, target: str) -> bool:
        """记录 04-review 的路由决策。返回是否写入成功。

        决策存 .state（docs/design-json-state-source.md）。返工时把 Evidence
        表填进 Markdown 供人阅读 —— 那是给人看的证据内容，不是判定依据。
        """
        from ..workflow import stage_state as ss

        if not ss.write_route(self.state.name, target, by="user"):
            return False
        if ss.read_route(self.state.name) != target.lower():
            return False
        if target != "05-Archive":
            self._fill_reroute_evidence(target)
        return True

    def _fill_reroute_evidence(self, target: str) -> None:
        """返工时在 04-review.md 里填好 Evidence 表（纯展示，失败不影响决策）。

        表格内容取自本阶段的拍板记录（`.state` 的 decisions）——那里存着用户在
        ask_user 里选的选项原文，也就是返工的真实理由。此前这里写死了
        「需返工修复的问题 / 详见审查结论」这类占位文本，于是
        inject_reroute_context 注入到 03-coding.md 的返工上下文毫无信息量，
        agent 根本不知道要补什么，返工回去只是把原来的代码重新确认一遍
        （任务 T3：用户要求补 README.md，agent 连续三轮都没做）。
        """
        review_path = TASKS / self.state.name / "04-review.md"
        if not review_path.exists():
            return
        try:
            content = review_path.read_text(encoding="utf-8")
        except OSError:
            return
        stage_labels = {
            "02-Planning": "planning",
            "03-Coding": "coding",
            "01-Brainstorming": "brainstorming",
        }
        stage = stage_labels.get(target, "planning")
        reasons = self._reroute_reasons()
        placeholder = "| {n} | ___ | high/med/low | coding/planning/brainstorming | ___ |"
        updated = content
        for idx in (1, 2):
            row = placeholder.format(n=idx)
            if row not in updated:
                continue
            if idx <= len(reasons):
                reason = reasons[idx - 1]
                sev = "high" if idx == 1 else "med"
                updated = updated.replace(
                    row, f"| {idx} | {reason} | {sev} | {stage} | 见 04-review 决策记录 |", 1)
            else:
                # 没有第二条理由时把占位行删掉，而不是编一条假的出来：
                # extract_evidence_table 会把 `___` 当未填而整表作废。
                updated = updated.replace(row + "\n", "", 1)
        if updated != content:
            try:
                review_path.write_text(updated, encoding="utf-8")
            except OSError:
                pass

    def _reroute_reasons(self) -> List[str]:
        """从拍板记录里取出用户这一轮给出的返工理由，最新的在前。

        取不到就回退成一句明确的兜底 —— 宁可写「用户选择返工，理由见对话记录」
        也不要写「详见审查结论」那种既像内容又没内容的话。
        """
        fallback = ["用户选择返工，具体理由见 04-review 对话记录"]
        try:
            from ..workflow import stage_state as ss
            decisions = ss.read_decisions(self.state.name, self.state.stage)
        except Exception:
            return fallback
        if not decisions:
            return fallback
        ordered = sorted(decisions.items(),
                         key=lambda kv: str(kv[1].get("decided_at") or ""),
                         reverse=True)
        reasons: List[str] = []
        for question, entry in ordered:
            answer = str(entry.get("answer") or "").strip()
            if not answer:
                continue
            # 表格是单行单元格，竖线和换行必须转义/压平，否则整张表结构就散了。
            text = f"{question.strip()} → {answer}"
            text = text.replace("|", "\\|").replace("\n", " ")
            if len(text) > 160:
                text = text[:157] + "..."
            reasons.append(text)
            if len(reasons) == 2:
                break
        return reasons or fallback

    def _write_gate_signoff(self, by: str = "user") -> bool:
        """签署当前阶段的 Gate（用户批准推进）。

        写 .state 而不是改 Markdown 的复选框：agent 的产出可以包含 ``## Gate``
        字样，靠在文件里找标记来定位签署区必然歧义 —— 那是「Gate 区每次 flush
        追加一份」「签署被写进 agent 正文」这类 bug 的根源。
        Markdown 里的 Gate 区随后被单向渲染成状态的映像。
        """
        from ..workflow import stage_state as ss

        if not ss.sign_gate(self.state.name, self.state.stage, by=by):
            return False
        ss.render_gate_section(self.state.name, self.state.stage)
        return True

    # ── 阶段推进（手动 /advance 与自动推进共用）──

    def _maybe_auto_advance(self) -> None:
        """自动推进：仅作用于**阶段边界**（当前 stage 已结束、下一个未开始）。

        auto_advance 的职责边界很窄 —— 它只免去用户敲 /advance 这一步，
        不干预阶段内部的任何对话。因此 agent 有待回答的提问时必须让路：
        无论提问由用户回答还是由 auto_answer 代答，都是阶段内的事，
        推进要等到 agent 真正收尾（idle 且 _invoke_done 置位）之后。

        只在主循环的空闲分支调用。任一次推进失败（blocked/error）就永久
        退回手动，避免对同一门禁反复空转。
        """
        if not self._auto_advance or self._auto_stopped:
            return
        if self.state.is_settled or not self.running:
            return
        # 阶段内仍在对话（等人回答 / agent 未收尾）→ 不是阶段边界，让路
        if self.state.pending_questions or self.state.agent_status != "idle":
            return

        # 主循环每 40ms 走一次这里，任何异常都不能冒泡（否则刷屏并打断渲染）。
        try:
            active = WorkflowRuntime.get_executor().active_stage
        except Exception:
            active = None
        if active is not None:
            if active.active_agent is not None:
                return
            if not active._invoke_done.is_set():
                return

        st = read_state(self.state.name)
        if not st or st.get("stage_status", "pending") == "pending":
            return

        if self._auto_count >= AUTO_ADVANCE_MAX_STAGES:
            self._auto_stopped = True
            self._add_log(
                "error",
                f"[auto] 已达自动推进上限 {AUTO_ADVANCE_MAX_STAGES} 次，转为手动（输入 /advance）",
            )
            return

        self._auto_count += 1
        self._add_log("sw", f"[auto] 自动推进 ({self._auto_count}/{AUTO_ADVANCE_MAX_STAGES})")
        result = self._run_advance(auto=True)
        if result in ("blocked", "error"):
            self._auto_stopped = True
            self._add_log("sw", "[auto] 自动推进已暂停，转为手动（输入 /advance 重试）")

    def _run_advance(self, auto: bool = False) -> str:
        """执行一次阶段推进。

        返回值：
          "advanced"  已推进到下一阶段并启动了 agent
          "settled"   任务已完成（进入结算）
          "blocked"   校验未通过，停在当前阶段
          "error"     推进过程抛错

        auto=True 时会先代为完成用户签署动作（勾 Gate / 回填 Route），
        这些动作在手动模式下必须由用户显式做出。
        """
        from ..core.service import _service

        try:
            st = _service.get_task_state(self.state.name)
            idx = int(st.get("stage_idx", 0))
            cur_status = st.get("stage_status", "pending")

            executor = WorkflowRuntime.get_executor()
            active = executor.active_stage

            # 校验前先把 agent 已产出的内容落盘。多轮模式下产出要等 agent 收尾
            # 才写文件，而收尾发生在校验通过之后 —— 不先 flush，校验永远读的是
            # 没有产出的旧文件，用户会被要求补齐 agent 其实已经给出的内容。
            if active is not None:
                try:
                    active.flush_output(self.state.name)
                except Exception:
                    pass

            if auto:
                self._auto_sign_off(STAGES[idx])

            # 归档阶段特殊处理
            if idx >= len(STAGES) - 1:
                done_items, todo_items = _service.validate_stage(self.state.name)
                for item in done_items:
                    self._add_log("sw", f"  [✓] {item}")
                for item in todo_items:
                    self._add_log("sw", f"  [!] {item}")
                if todo_items:
                    self._add_log("error", f"检测到 {len(todo_items)} 个未完成项")
                    return "blocked"
                self._finalize_active_agent(active)
                _service.advance_stage(self.state.name)
                self._on_settlement()
                return "settled"

            if cur_status == "pending":
                self._add_log("error", "当前阶段尚未开始运行，请等待 Agent 完成后再推进")
                return "blocked"

            # 软校验
            self._add_log("sw", f"--- 阶段校验: {STAGES[idx]} ({STAGE_NAMES[idx]}) ---")
            done_items, todo_items = _service.validate_stage(self.state.name)
            for item in done_items:
                self._add_log("sw", f"  [✓] {item}")
            for item in todo_items:
                self._add_log("sw", f"  [!] {item}")

            # 硬校验
            hook_script = HOOKS_DIR / f"check_{STAGES[idx]}.sh"
            if not hook_script.exists():
                hook_script = HOOKS_DIR / f"post_check_{STAGES[idx]}.sh"
            if hook_script.exists():
                self._add_log("sw", f"--- 系统硬校验: {hook_script.name} ---")
                try:
                    res = subprocess.run(
                        [str(hook_script), self.state.name],
                        cwd=str(ROOT), check=False, timeout=HOOK_TIMEOUT_SECONDS,
                        capture_output=True, text=True,
                    )
                except subprocess.TimeoutExpired:
                    self._add_log("error", f"硬校验超时: {hook_script.name}")
                    return "blocked"
                # 回显 hook 输出：不显示的话用户只看到「未通过」，无从判断改什么
                for stream in (res.stdout, res.stderr):
                    for line in (stream or "").splitlines():
                        if line.strip():
                            self._add_log("sw", f"  {line.rstrip()}")
                if res.returncode != 0:
                    self._add_log("error", "硬校验未通过，必须满足所有条件才能推进")
                    return "blocked"

            if todo_items:
                self._add_log("error", f"检测到 {len(todo_items)} 个未完成项，请完善后重试")
                return "blocked"

            # 校验全部通过后才收尾 agent。顺序很关键：agent 一旦 shutdown
            # 就无法再改文件，若在校验前关掉，「请完善后重试」将无人可执行，
            # 用户只能手工编辑 agent 生成的产出。
            self._finalize_active_agent(active)

            # 执行推进
            _service.advance_stage(self.state.name)
            st = read_state(self.state.name)

            if st.get("stage_status") == "Finished":
                self._on_settlement()
                return "settled"

            self.state.stage = st.get("stage")
            self.state.stage_idx = int(st.get("stage_idx", 0))
            self._add_log("sw", f"阶段推进 → {STAGE_NAMES[self.state.stage_idx]}")

            stage_input = StageInput(
                task_name=self.state.name,
                stage=self.state.stage,
                stage_idx=self.state.stage_idx,
                metadata={"callbacks": self.callbacks},
            )
            threading.Thread(target=executor.invoke, args=(stage_input,), daemon=True).start()
            return "advanced"
        except Exception as e:
            self._add_log("error", f"推进失败: {e}")
            return "error"

    def _finalize_active_agent(self, active) -> None:
        """通知多轮对话中的 agent 收尾退出，并等它落盘完成。

        只应在校验通过、确定要推进时调用：shutdown 不可撤回，提前调用会让
        校验失败后的「请完善后重试」变成死路。
        """
        if not active:
            return
        if active.active_agent:
            self._add_log("sw", "Agent 仍在运行中，正在等待完成...")
            active._stage_done.set()
            active._agent_finalized.wait(timeout=30)
            active._invoke_done.wait(timeout=30)
            self._add_log("sw", "Agent 已退出，继续推进")
        else:
            # Agent 可能已自行完成 —— 等 invoke 落盘结束
            active._invoke_done.wait(timeout=30)

    def _auto_sign_off(self, stage: str) -> None:
        """自动模式下代替用户完成签署动作。

        手动模式里这两件事由用户在 TUI 里点选：勾 Gate（每个阶段都有）、
        04 阶段选 Route。自动模式必须代为完成，否则门禁不可达、自动推进会
        立刻卡住。
        """
        if stage == "04-review":
            from ..workflow import stage_state as ss
            from ..workflow.utils import parse_route_from_ai_output
            # 决策状态读 .state；只有在尚未决策时才去读 agent 的结论正文推断
            # 意图 —— 那是对产出内容的解读，不是状态判定。
            if ss.read_route(self.state.name) is None:
                path = TASKS / self.state.name / "04-review.md"
                if not path.exists():
                    return
                route = parse_route_from_ai_output(
                    path.read_text(encoding="utf-8", errors="replace"))
                if not route:
                    self._add_log(
                        "error",
                        "[auto] 无法从审查结论判定 Route，请手动选择路由后再推进",
                    )
                    return
                target = _normalize_route_label(route)
                if not self._write_review_route(target):
                    self._add_log(
                        "error", f"[auto] Route 写入失败（目标 {target}）")
                    return
                self._add_log("sw", f"[auto] Route 已自动判定为 {target}")

        # Gate 签署对所有阶段一视同仁（04 必须在 Route 落定之后再签）
        if self._is_gate_signoff_needed() and self._write_gate_signoff(by="auto"):
            self._add_log("sw", f"[auto] {self._stage_label()}已自动批准（Gate 已签署）")

    def _handle_settlement_choice(self, choice: str):
        """执行最终结算动作"""
        c = choice.strip().upper()
        from ..core.service import _service
        
        if c == "A":
            self._add_log("sw", "🚀 正在自动归档并清理现场...")
            try:
                _service.remove_task(self.state.name)
                self._add_log("sw", "✓ 归档完成。")
            except Exception as e:
                self._add_log("error", f"归档失败: {e}")
            time.sleep(2)
            self.running = False

        elif c == "B":
            self._add_log("sw", "保持现状，退出监控面板。")
            time.sleep(1)
            self.running = False

        elif c == "C":
            self._add_log("sw", "正在生成任务简报...")
            try:
                report_path = TASKS / self.state.name / "FINAL_REPORT.md"
                content = [f"# {self.state.name} 任务总结报告\n\n生成时间: {now()}\n\n"]
                for s in STAGES:
                    p = TASKS / self.state.name / f"{s}.md"
                    if p.exists():
                        content.append(f"## {s}\n\n{p.read_text()}\n\n---\n")
                report_path.write_text("\n".join(content))
                self._add_log("sw", f"✓ 简报已导出至: {report_path.name}")
            except Exception as e:
                self._add_log("error", f"导出失败: {e}")
            time.sleep(2)
            self.running = False

        else:
            # 静默忽略会让用户以为界面卡死
            self._add_log("error", f"无效选项 {choice!r}，请输入 A / B / C")

    def _build_agent_response(self, raw_text: str) -> str:
        """根据模式构造发送给 Agent 的文本"""
        if self.state.input_mode == "yesno":
            if raw_text.strip().lower() in ("yes", "y"):
                return "yes, I approve."
            else:
                return "no, I don't approve. Please revise based on our discussion."
                
        if self.state.input_mode == "options":
            label = raw_text.strip().upper()
            for l, t in self.state.options:
                if l == label:
                    return f"我选择选项 {l}: {t}"
            # 标签不匹配时返回原文（用户可能输入了完整文本而非缩写）
            return raw_text
                    
        return raw_text

    # ── 键盘输入线程 ──

    def _submit_input(self, text: str) -> bool:
        """校验并提交一行输入。返回是否被接受。

        两个输入循环（TTY 逐字符 / 非 TTY 读行）共用这一份逻辑，免得"被拒时
        清不清缓冲区"这类细节在两处各写一遍、修一处漏一处。
        """
        if not text.strip():
            return False
        ok, msg = self._validate_input(text)
        if ok:
            self.cmd_queue.put(text)
            self.state.input_buffer = ""
            self.state.error_msg = ""
            return True
        # 被拒的输入也要清出缓冲区。留着它，用户接着敲的下一条命令会被拼在
        # 后面 —— 按 A 太早被拒，再输入 /advance 就变成 "A/advance"，一条谁都
        # 不认的命令，看起来像 TUI 没反应。
        self.state.error_msg = msg
        self.state.input_buffer = ""
        return False

    def _input_loop_sync(self):
        """非 TTY 环境下的同步读行模式"""
        while self.running:
            try:
                line = sys.stdin.readline()
                if not line: break
                line = line.strip()
                if line:
                    if not self._submit_input(line):
                        self._refresh_display()
            except (EOFError, KeyboardInterrupt, OSError):
                break

    def _input_loop(self):
        """TTY 环境下的异步字符读取模式，支持 ANSI 转义序列解析"""
        fd = self._stdin_fd
        selector = selectors.SelectSelector()
        selector.register(fd, selectors.EVENT_READ)
        
        # ANSI 转义序列缓冲区
        esc_buf = ""

        while self.running:
            try:
                events = selector.select(timeout=0.05)
                if not events: continue
                raw = os.read(fd, 32)
                if not raw: break

                text = raw.decode("utf-8", errors="replace")
                
                for ch in text:
                    # 1. 如果正在接收转义序列
                    if esc_buf:
                        esc_buf += ch
                        # CSI: \x1b[ + 参数 + 结尾 alpha/~  |  SS3: \x1bO + 结尾 alpha (要求 >=3 字符避免 \x1bO 前缀误判)
                        if (esc_buf.startswith('\x1b[') and (ch.isalpha() or ch == '~')) or \
                           (len(esc_buf) >= 3 and esc_buf.startswith('\x1bO') and ch.isalpha()):
                            # 完成一个序列
                            self._handle_esc(esc_buf)
                            esc_buf = ""
                        elif len(esc_buf) > 10: # 防护
                            esc_buf = ""
                        continue

                    # 2. 开始一个新的转义序列
                    if ch == '\x1b':
                        esc_buf = ch
                        continue

                    # 3. 处理常规按键
                    if ch in ('\n', '\r'):
                        self._submit_input(self.state.input_buffer)
                        self._refresh_display()
                    elif ch == '\x7f' or ch == '\b':
                        self.state.input_buffer = self.state.input_buffer[:-1]
                        self._refresh_display()
                    elif ch == '\x03': # Ctrl-C
                        self.running = False
                        break
                    elif ch.isprintable():
                        self.state.input_buffer += ch
                        self._refresh_display()
                        
            except (OSError, ValueError):
                break
        try:
            selector.unregister(fd)
        except (KeyError, ValueError):
            pass

    def _handle_esc(self, seq: str):
        """解析并处理 ANSI 转义序列 (如方向键)"""
        # 计算显示高度以供翻页参考
        visible_height = max(5, self.console.size.height - 11)
        
        if seq in ('\x1b[A', '\x1bOA'): # Up (CSI / SS3)
            self.state.log_scroll_offset += 1
        elif seq in ('\x1b[B', '\x1bOB'): # Down (CSI / SS3)
            self.state.log_scroll_offset = max(0, self.state.log_scroll_offset - 1)
        elif seq == '\x1b[5~': # PageUp
            self.state.log_scroll_offset += visible_height
        elif seq == '\x1b[6~': # PageDown
            self.state.log_scroll_offset = max(0, self.state.log_scroll_offset - visible_height)
        self._refresh_display()
