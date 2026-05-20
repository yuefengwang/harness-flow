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
import sys
import termios
import threading
import queue
import tty
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional, Callable

# 从 config 引入 Rich 组件 (假设 HAS_RICH 为 True，若环境不支持则 MonitorTUI 无法启动)
from ..core.config import STAGES, STAGE_NAMES, TASKS, ROOT, HAS_RICH, Layout, Live, Panel, Text, Console, box
from ..core.engine import WorkflowEngine
from ..core.state import update_status_active
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
]

def extract_options(lines: List[Tuple[str, str]], max_age: int = 20) -> List[Tuple[str, str]]:
    """
    从最近的日志记录中扫描并提取 Agent 提出的结构化选项。
    
    Args:
        lines: 日志行列表 (source, msg)
        max_age: 扫描最近的行数，默认 20
        
    Returns:
        提取到的选项列表 [(label, text), ...]
    """
    recent = lines[-max_age:] if len(lines) > max_age else lines
    seen = set()
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
                    if label and text and label not in seen:
                        seen.add(label)
                        options.append((label, text))
                    break
    return options

def detect_input_mode(log_lines: List[Tuple[str, str]], options: List[Tuple[str, str]]) -> str:
    """
    根据最近的对话上下文自动推断当前应处于哪种交互模式。
    
    逻辑准则：
    1. 只有当最近的一条有效消息来自 Agent 时，才允许进入交互模式（yesno/options）。
    2. 如果用户已经回复（最新消息源为 user），则必须退出交互模式，回到 none。
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
    if options:
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
        callbacks = {
            "add_log": self._add_log, 
            "is_running": lambda: self.running,
            "on_ask_user": self._on_ask_user,
            "on_settlement": self._on_settlement
        }
        self.engine = WorkflowEngine(
            name=name, stage=stage, stage_idx=stage_idx,
            agent_name=agent_name, callbacks=callbacks
        )
        
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
        return self.engine.model_name

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
                
                # 更新状态面板
                try:
                    update_status_active(self.state.name)
                except Exception as e:
                    sw_log(self.state.name, f"update STATUS active failed: {e}", "err")

                # 启动引擎
                self.engine.run_stage()
                self.state.model_name = self.engine.model_name

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
            self.engine.shutdown()
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
        agent = self.engine.agent
        if agent and hasattr(agent, 'status'):
            self.state.agent_status = agent.status
        
        # 同步工作流阶段状态到 UI 状态
        self.state.stage = self.engine.stage
        self.state.stage_idx = self.engine.stage_idx
        self.state.model_name = self.engine.model_name
        
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
        else:
            self.state.input_mode = detect_input_mode(self.state.log_lines, self.state.options)

    def _add_log(self, source: str, msg: str):
        """引擎回调：添加日志"""
        self.state.log_lines.append((source, msg))
        if len(self.state.log_lines) > 2000:
            self.state.log_lines = self.state.log_lines[-1000:]
        
        # 自动聚焦到最新
        self.state.log_scroll_offset = 0
        
        sw_log(self.state.name, msg[:500], source)
        self._refresh_display()

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
        """处理经过验证的完整指令"""
        cmd = cmd.strip()
        if not cmd: return
        self.state.error_msg = ""
        self._refresh_display()

        if cmd == "/q":
            self.running = False
            return

        if cmd.startswith("/"):
            self._add_log("user", cmd)
            self.engine.handle_command(cmd[1:])
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
                    # 告知引擎：交互已完成，恢复运行状态
                    self.engine.resume_running()
                self.state.pending_questions = []
                self._q_res_queue = None
            return

        # 2. 处理普通 Agent 回复
        if self.state.is_settled:
            self._handle_settlement_choice(cmd)
            return

        response = self._build_agent_response(cmd)
        self._add_log("user", response)
        self.engine.answer(response)

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
                return "yes, I approve. Please proceed to the next stage."
            else:
                return "no, I don't approve. Please revise based on our discussion."
                
        if self.state.input_mode == "options":
            label = raw_text.strip().upper()
            for l, t in self.state.options:
                if l == label:
                    return f"我选择选项 {l}: {t}"
                    
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
                        if (len(esc_buf) > 1 and ch.isalpha()) or ch == '~':
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
        
        if seq == '\x1b[A': # Up
            self.state.log_scroll_offset += 1
        elif seq == '\x1b[B': # Down
            self.state.log_scroll_offset = max(0, self.state.log_scroll_offset - 1)
        elif seq == '\x1b[5~': # PageUp
            self.state.log_scroll_offset += visible_height
        elif seq == '\x1b[6~': # PageDown
            self.state.log_scroll_offset = max(0, self.state.log_scroll_offset - visible_height)
        
        self._refresh_display()
