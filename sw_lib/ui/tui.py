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
import tty
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional, Callable

# 从 config 引入 Rich 组件 (假设 HAS_RICH 为 True，若环境不支持则 MonitorTUI 无法启动)
from ..core.config import STAGES, STAGE_NAMES, TASKS, ROOT, HOOKS_DIR, HAS_RICH, Layout, Live, Panel, Text, Console, box
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
    
    status_hint: str = ""            # 底部状态提示词
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
        
        # 探测模式
        self.state.options = extract_options(self.state.log_lines)
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
            self.state.options = opts
            self.state.input_mode = "options"
        elif self._is_review_routing_needed():
            self.state.options = [
                ("A", "归档 (05-Archive) — 代码通过，正常归档"),
                ("B", "返工编码 (03-Coding) — 代码需修复"),
                ("C", "返工规划 (02-Planning) — 设计需修订"),
            ]
            self.state.input_mode = "options"
        else:
            self.state.input_mode = detect_input_mode(self.state.log_lines, self.state.options)

    def _is_review_routing_needed(self) -> bool:
        """04-review 完成后，Route 未填写 → 需要用户选择路由"""
        if self.state.stage != "04-review":
            return False
        if self.state.agent_status not in ("idle", "waiting"):
            return False
        if self.state.pending_questions:
            return False
        try:
            from ..workflow.utils import parse_route_field
            route = parse_route_field(self.state.name)
            return route is None
        except Exception:
            return False

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
        self.state.pending_questions = questions
        self.state.current_q_idx = 0
        self._q_answers = []
        self._q_res_queue = res_queue
        
        # 立即同步选项和模式，确保后续 _add_log 触发的渲染能显示问题
        self._update_agent_status()
        self._add_log("sw", f"❓ 收到 {len(questions)} 个结构化问题")

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
                from ..core.service import _service
                from ..workflow.base import StageInput
                try:
                    st = _service.get_task_state(self.state.name)
                    idx = int(st.get("stage_idx", 0))
                    cur_status = st.get("stage_status", "pending")

                    # 若 Agent 处于多轮对话模式，先通知其收尾退出
                    executor = WorkflowRuntime.get_executor()
                    active = executor.active_stage
                    if active and active.active_agent:
                        self._add_log("sw", "Agent 仍在运行中，正在等待完成...")
                        active._stage_done.set()
                        active._agent_finalized.wait(timeout=30)
                        active._invoke_done.wait(timeout=30)
                        self._add_log("sw", "Agent 已退出，继续推进")
                    else:
                        # Agent may have already completed — wait for invoke to finish saving
                        if active:
                            active._invoke_done.wait(timeout=30)

                    # 归档阶段特殊处理
                    if idx >= len(STAGES) - 1:
                        done_items, todo_items = _service.validate_stage(self.state.name)
                        for item in done_items:
                            self._add_log("sw", f"  [✓] {item}")
                        for item in todo_items:
                            self._add_log("sw", f"  [!] {item}")
                        if todo_items:
                            self._add_log("error", f"检测到 {len(todo_items)} 个未完成项")
                            return
                        _service.advance_stage(self.state.name)
                        self._on_settlement()
                        return

                    if cur_status == "pending":
                        self._add_log("error", f"当前阶段尚未开始运行，请等待 Agent 完成后再推进")
                        return

                    # 软校验: validate_stage
                    self._add_log("sw", f"--- 阶段校验: {STAGES[idx]} ({STAGE_NAMES[idx]}) ---")
                    done_items, todo_items = _service.validate_stage(self.state.name)
                    for item in done_items:
                        self._add_log("sw", f"  [✓] {item}")
                    for item in todo_items:
                        self._add_log("sw", f"  [!] {item}")

                    # 硬校验: hook shell script
                    hook_script = HOOKS_DIR / f"check_{STAGES[idx]}.sh"
                    if not hook_script.exists():
                        hook_script = HOOKS_DIR / f"post_check_{STAGES[idx]}.sh"
                    if hook_script.exists():
                        self._add_log("sw", f"--- 系统硬校验: {hook_script.name} ---")
                        res = subprocess.run(
                            [str(hook_script), self.state.name],
                            cwd=str(ROOT), check=False, timeout=60
                        )
                        if res.returncode != 0:
                            self._add_log("error", "硬校验未通过，必须满足所有条件才能推进")
                            return

                    # 待办项阻断
                    if todo_items:
                        self._add_log("error", f"检测到 {len(todo_items)} 个未完成项，请完善后重试")
                        return

                    # 执行推进
                    _service.advance_stage(self.state.name)
                    st = read_state(self.state.name)

                    if st.get("stage_status") == "Finished":
                        self._on_settlement()
                        return

                    self.state.stage = st.get("stage")
                    self.state.stage_idx = int(st.get("stage_idx", 0))
                    self._add_log("sw", f"阶段推进 → {STAGE_NAMES[self.state.stage_idx]}")

                    stage_input = StageInput(
                        task_name=self.state.name,
                        stage=self.state.stage,
                        stage_idx=self.state.stage_idx,
                        metadata={"callbacks": self.callbacks}
                    )
                    threading.Thread(target=executor.invoke, args=(stage_input,), daemon=True).start()
                except Exception as e:
                    self._add_log("error", f"推进失败: {e}")
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
        if self.state.stage == "04-review" and self.state.input_mode == "options" and not self.state.is_settled:
            review_routes = {"A": "05-Archive", "B": "03-Coding", "C": "02-Planning"}
            choice = cmd.strip().upper()
            if choice in review_routes:
                target = review_routes[choice]
                route_labels = {"05-Archive": "归档", "03-Coding": "编码", "02-Planning": "规划"}
                self._add_log("user", f"[{choice}] 返工到 {route_labels[target]} ({target})")
                self._write_review_route(target)
                self.state.input_mode = "none"
                self.state.options = []
                self._add_log("sw", f"✓ Route 已设置为 {target}。输入 /advance 推进。")
                self._refresh_display()
                return

        # 2. 处理普通 Agent 回复
        if self.state.is_settled:
            self._handle_settlement_choice(cmd)
            return

        response = self._build_agent_response(cmd)
        self._add_log("user", response)
        WorkflowRuntime.get_executor().answer(response)

    def _write_review_route(self, target: str):
        """写入 04-review.md 中的 Route 字段，返工时自动填充 Evidence 表"""
        review_path = TASKS / self.state.name / "04-review.md"
        if not review_path.exists():
            return
        content = review_path.read_text(encoding="utf-8")
        content = content.replace(
            "- **Route**: `___`",
            f"- **Route**: `{target}`",
            1
        )
        if target != "05-Archive":
            stage_labels = {
                "02-Planning": "planning",
                "03-Coding": "coding",
                "01-Brainstorming": "brainstorming",
            }
            stage = stage_labels.get(target, "planning")
            content = content.replace(
                "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |",
                f"| 1 | 需返工修复的问题 | high | {stage} | 详见审查结论 |",
                1,
            )
            content = content.replace(
                "| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |",
                "| 2 | 需跟踪的改进项 | med | coding | 详见审查结论 |",
                1,
            )
        review_path.write_text(content, encoding="utf-8")

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

    def _input_loop_sync(self):
        """非 TTY 环境下的同步读行模式"""
        while self.running:
            try:
                line = sys.stdin.readline()
                if not line: break
                line = line.strip()
                if line:
                    ok, msg = self._validate_input(line)
                    if ok:
                        self.cmd_queue.put(line)
                    else:
                        self.state.error_msg = msg
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
                        if self.state.input_buffer.strip():
                            ok, msg = self._validate_input(self.state.input_buffer)
                            if ok:
                                self.cmd_queue.put(self.state.input_buffer)
                                self.state.input_buffer = ""
                                self.state.error_msg = ""
                            else:
                                self.state.error_msg = msg
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
