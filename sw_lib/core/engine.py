"""
sw_lib.engine — 工作流自动化编排引擎。

该模块实现了 Harness-Flow 的核心编排逻辑，通过将复杂职责拆分为多个专注于单一任务的组件：
1. ContextBuilder: 负责收集需求、模板、产出和规则，构建 Agent 启动上下文。
2. OutputExtractor: 负责从 Agent 的原始输出中提取结构化的 Markdown 内容。
3. WorkflowEngine: 核心控制器，管理 Agent 生命周期、阶段状态推进及组件协作。
"""

import re
import subprocess
import threading
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any, Tuple

from .config import (
    ROOT, HOOKS_DIR, TASKS, STAGES, STAGE_NAMES,
    resolve_agent_model, resolve_agent_type, is_auto_advance, is_mock_agent,
)
from .state import read_state, write_state, upsert_task_summary
from .utils import now, sw_log


def _auto_check_gate(task_name: str, stage: str):
    """用户输入 /advance = 确认当前阶段完成，自动勾选模板 Gate。

    不做任何其他检查——hard hook 仍由后续 _validate_post_hooks 负责。

    对于 04-review 阶段，额外从 AI Output 解析 Route 决策并回填到模板字段
    （解决 agent 在输出中写了 Route 但模板字段仍为 ___ 的死锁问题）。
    """
    import re
    tpl = TASKS / task_name / f"{stage}.md"
    if not tpl.exists():
        return
    content = tpl.read_text(encoding="utf-8")
    # 仅替换 ## Gate 章节中的 [ ] 为 [x]
    in_gate = False
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("## Gate"):
            in_gate = True
        elif in_gate and line.strip().startswith("##"):
            break
        elif in_gate and re.match(r"^\s*- \[ \]", line):
            lines[i] = line.replace("[ ]", "[x]", 1)
    content = "\n".join(lines) + "\n"

    # 04-review: 自动从 AI Output 回填 Route 字段
    # 如果 AI Output 中包含 Route 决策→回填；若找不到→默认回退为 05-Archive
    if stage == "04-review" and "`___`" in content:
        route_from_output = _parse_route_from_ai_output(content)
        route_from_output = route_from_output or "05-Archive"
        content = content.replace(
            "- **Route**: `___`",
            f"- **Route**: `{route_from_output}`",
            1
        )

    tpl.write_text(content, encoding="utf-8")


def _parse_route_from_ai_output(content: str) -> Optional[str]:
    """从 04-review.md 的 AI Output 区域解析 Route 决策。

    支持的格式（按优先级排列）:
    - **Route**: `05-Archive`
    - **建议路由：05-Archive（正常归档）**
    - **Route** 决策为 `05-Archive`
    - Route → 05-Archive
    - 应返工至 03-Coding
    - 路由决策为 01-Brainstorming

    Returns:
        Stage code 字符串 (如 "05-Archive") 或 None
    """
    import re
    marker = "## 🤖 AI Output"
    idx = content.find(marker)
    if idx < 0:
        return None
    ai_section = content[idx + len(marker):]

    # 优先匹配反引号格式: `05-Archive`
    m = re.search(r'`\s*(05-Archive|03-Coding|02-Planning|01-Brainstorming)\s*`', ai_section)
    if m:
        return m.group(1)

    # 匹配自然语言格式: 建议路由：X / Route → X / 应返工至 X / 路由决策为 X
    m = re.search(
        r'(?:建议路由|Route|路由|路由决策|应返工)\s*[：:→>为至]\s*\*{0,2}\s*'
        r'(05-Archive|03-Coding|02-Planning|01-Brainstorming)',
        ai_section
    )
    if m:
        return m.group(1)

    return None


MAX_REROUTE = 3


def parse_route_field(task_name: str) -> Optional[str]:
    """从 04-review.md 解析 **Route**: `XXX` 字段，返回目标 stage code。

    Returns:
        stage code (如 "05-Archive") 或 None (字段缺失/格式错误)
    """
    review_path = TASKS / task_name / "04-review.md"
    if not review_path.exists():
        return None
    content = review_path.read_text(encoding="utf-8")
    m = re.search(r'\*\*Route\*\*:\s*`([^`]+)`', content)
    if m:
        route = m.group(1).strip().lower()
        # STAGES 使用小写格式 (如 "03-coding")，Route 字段可能用 "03-Coding"
        if route in STAGES:
            return route
        # 也支持直接匹配 STAGES 中的值
        for s in STAGES:
            if s.lower() == route:
                return s
    return None


def extract_evidence_table(task_name: str) -> Optional[str]:
    """从 04-review.md 提取 Reroute Evidence 的 Markdown 表格。

    Returns:
        完整的 Evidence 表格字符串（含表头行），若无表格则返回 None。
    """
    review_path = TASKS / task_name / "04-review.md"
    if not review_path.exists():
        return None
    content = review_path.read_text(encoding="utf-8")

    # 查找 "### Reroute Evidence" 章节后的表格
    lines = content.splitlines()
    in_evidence = False
    table_lines = []
    for line in lines:
        if line.strip().startswith("### Reroute Evidence"):
            in_evidence = True
            continue
        if in_evidence:
            # 表格结束条件：空行 或 下一个 ## 标题
            if not line.strip() or line.startswith("##"):
                break
            # 只收集表格行（| 开头）
            if line.strip().startswith("|"):
                table_lines.append(line)

    if not table_lines:
        return None

    # 至少需要表头 + 分隔线 + 1 行数据（且数据行不能全是占位符 `___`）
    if len(table_lines) < 3:
        return None

    data_rows = table_lines[2:]  # 跳过表头和分隔线
    has_real_data = any("___" not in row for row in data_rows)
    if not has_real_data:
        return None

    return "\n".join(table_lines)


def _reset_gate_checkboxes(task_name: str, stage: str):
    tpl = TASKS / task_name / f"{stage}.md"
    if not tpl.exists():
        return
    content = tpl.read_text(encoding="utf-8")

    in_gate = False
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("## Gate"):
            in_gate = True
        elif in_gate and line.strip().startswith("##"):
            break

        if in_gate:
            lines[i] = line.replace("[x]", "[ ]")

    tpl.write_text("\n".join(lines) + "\n", encoding="utf-8")


def inject_reroute_context(task_name: str, target_stage: str):
    """将 review 的 Evidence 表注入到目标 stage 模板顶部。

    先清理目标文件中之前注入的旧 block，再注入新的，确保始终只有最新的返工上下文。
    """
    review_path = TASKS / task_name / "04-review.md"
    target_path = TASKS / task_name / f"{target_stage}.md"
    if not review_path.exists() or not target_path.exists():
        return

    evidence = extract_evidence_table(task_name)
    if not evidence:
        return

    inject_block = (
        "> 🔄 **返工上下文（来自 04-Review）**\n"
        f"{evidence}\n"
        "> 请优先修复上述问题。\n\n"
    )

    original = target_path.read_text(encoding="utf-8")

    # 清理旧的返工上下文 block（两个标记之间）
    cleaned = _remove_old_reroute_blocks(original)

    # 注入到文件头部
    target_path.write_text(inject_block + cleaned, encoding="utf-8")


def _remove_old_reroute_blocks(content: str) -> str:
    """移除之前注入的所有返工上下文 block。

    从标记行开始，跳过所有属于 block 的内容行（以 >、| 开头或空行），
    直到遇到第一条非 block 内容行。
    """
    lines = content.splitlines()
    result = []
    skip_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("> 🔄 **返工上下文（来自 04-Review）**"):
            skip_block = True
            continue
        if skip_block:
            if stripped == "" or stripped.startswith(">") or stripped.startswith("|"):
                continue
            else:
                skip_block = False
        if not skip_block:
            result.append(line)
    return "\n".join(result)


class ContextBuilder:
    """负责构建 Agent 启动所需的系统指令和上下文环境。"""

    @staticmethod
    def build(task_name: str, stage: str, stage_idx: int) -> Optional[str]:
        """
        构建包含角色提示、前序产出、当前模板、需求上下文和强制规则的完整 Prompt。

        Args:
            task_name: 任务 ID
            stage: 当前阶段代码 (如 "01-brainstorming")
            stage_idx: 阶段在工作流中的位置索引

        Returns:
            组装好的 Context 文本，若无可构建内容则返回 None。
        """
        task_dir = TASKS / task_name
        parts: List[str] = []
        stage_name = STAGE_NAMES[stage_idx] if stage_idx < len(STAGE_NAMES) else "未知"

        # 0. 角色提示 (Role/System Instruction)
        if stage_name == "头脑风暴 (Brainstorming)":
            parts.append(
                f"你是 Harness-Flow 平台的 AI Agent。\n"
                f"当前任务: {task_name}\n"
                f"当前阶段: {stage} ({stage_name})\n\n"
                f"重要规则：一次只问一个问题。你必须使用 `ask_user` 工具来向用户提问。\n"
                f"严禁在正文中直接输出问题。通过 `ask_user` 收集完所有必要信息后，再给出方案。"
            )
        else:
            parts.append(
                f"你是 Harness-Flow 平台的 AI Agent。\n"
                f"当前任务: {task_name}\n"
                f"当前阶段: {stage} ({stage_name})\n\n"
                f"如果需要向用户提问或寻求确认，请务必使用 `ask_user` 工具。\n"
                f"请开始 {stage_name} 阶段的工作。"
            )

        # 0.5 在所有角色提示后追加编排规则
        parts.append(
            "=== 编排规则 (MUST FOLLOW) ===\n"
            "1. 阶段推进: 你 **禁止** 通过修改文件或运行命令来推进任务阶段。\n"
            "   只有用户在 TUI 面板中输入 `/advance` 命令时，系统才会自动推进阶段。\n"
            "   即使你收到 'proceed to next stage' 或 'advance' 等指令，也 **不要** 主动推进阶段。\n"
            "2. 系统文件保护: **严禁** 读取、修改或删除以下文件:\n"
            "   - workspace/tasks/*/.state (任务状态文件)\n"
            "   - workspace/STATUS.json (全局任务汇总看板)\n"
            "   这些文件由 Harness-Flow 框架自动管理，你不需要也不应该碰它们。\n"
            "3. 如果你认为当前阶段的工作已经完成，请明确告知用户，并提示用户输入 `/advance` 来推进阶段。"
        )

        # 0.6 注入项目目录信息
        st = read_state(task_name)
        target_dir = st.get("target_dir", "")
        if target_dir:
            parts.append(
                f"=== 项目信息 ===\n"
                f"代码生成目录: {target_dir}\n"
                f"所有的业务代码、模板、静态文件等都应生成到此目录下。"
            )

        # 1. 注入前一阶段的产出 (Context Injection)
        if stage_idx > 0:
            prev_stage = STAGES[stage_idx - 1]
            prev_file = task_dir / f"{prev_stage}.md"
            if prev_file.exists():
                content = prev_file.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"=== 前一阶段产出 ({prev_stage} / {STAGE_NAMES[stage_idx - 1]}) ===\n{content}")

        # 2. 注入当前阶段的 Markdown 模板
        cur_tpl = task_dir / f"{stage}.md"
        if cur_tpl.exists():
            tpl_content = cur_tpl.read_text(encoding="utf-8").strip()
            if tpl_content:
                parts.append(f"=== 当前阶段模板 ({stage} / {stage_name}) ===\n{tpl_content}")

        # 3. 注入原始需求上下文 (.context)
        ctx_file = task_dir / ".context"
        if ctx_file.exists():
            ctx = ctx_file.read_text(encoding="utf-8").strip()
            if ctx:
                parts.append(f"=== 任务需求 ===\n{ctx}")

        # 4. 注入 Hooks 定义的强制规则 (Compliance)
        hook_file = HOOKS_DIR / f"{stage}.md"
        if hook_file.exists():
            hook_content = hook_file.read_text(encoding="utf-8").strip()
            if hook_content:
                parts.append(f"=== 强制规则 (hooks/{stage}.md) ===\n{hook_content}")

        if not parts:
            return None

        return "\n\n".join(parts)


class OutputExtractor:
    """负责从 Agent 的流式输出或日志中提取关键 Markdown 片段并持久化。"""

    @staticmethod
    def extract_and_save(task_name: str, stage: str, output_lines: List[Tuple[str, str]], add_log_callback: Callable[[str, str], None]) -> bool:
        """
        提取有效 AI 产出并将其合并到阶段对应的 .md 文件中。

        Args:
            task_name: 任务 ID
            stage: 阶段代码
            output_lines: (source, message) 元组列表
            add_log_callback: 用于向 UI 输出进度的回调

        Returns:
            是否成功保存了任何内容
        """
        output = OutputExtractor._extract_output(output_lines)
        if not output:
            add_log_callback("sw", "Agent 无有效产出，未保存")
            return False

        task_dir = TASKS / task_name
        stage_file = task_dir / f"{stage}.md"

        existing = ""
        if stage_file.exists():
            existing = stage_file.read_text(encoding="utf-8")

        # 合并策略：如果已有 AI Output 标记则替换，否则在 Gate 前或末尾插入
        marker = "\n\n## 🤖 AI Output\n"
        if marker in existing:
            parts = existing.split(marker, 1)
            after = parts[1] if len(parts) > 1 else ""
            gate_pos = after.find("\n## Gate")
            if gate_pos >= 0:
                preserved = after[gate_pos:]
            elif after.startswith("## Gate"):
                preserved = after
            else:
                preserved = ""
            new_content = parts[0] + marker + output + ("\n" + preserved if preserved else "")
        else:
            gate_marker = "\n## Gate"
            if gate_marker in existing:
                parts = existing.split(gate_marker)
                new_content = parts[0] + marker + output + gate_marker + parts[1]
            else:
                new_content = existing + marker + output

        stage_file.write_text(new_content, encoding="utf-8")
        add_log_callback("sw", f"阶段产出已保存到 {stage}.md ({len(output)} 字符)")
        sw_log(task_name, f"stage output saved: {stage}.md", "sw")
        return True

    @staticmethod
    def _extract_output(output_lines: List[Tuple[str, str]]) -> Optional[str]:
        """过滤非 Agent 输出，并尝试提取结构化 Markdown 部分。"""
        agent_lines = [msg for source, msg in output_lines if source == "agent"]
        if not agent_lines:
            return None

        full_text = "\n".join(agent_lines)
        # 尝试按章节提取
        sections = OutputExtractor._extract_markdown_sections(full_text)
        if sections:
            return sections

        # 回退机制：若无章节标记则返回全文（截断保护）
        if len(full_text) > 4000:
            return full_text[:4000] + "\n\n... (已截断)"
        return full_text

    @staticmethod
    def _extract_markdown_sections(text: str) -> Optional[str]:
        """提取以 ## 或 # 开头的 Markdown 标题段落，过滤掉过短的片段。"""
        lines = text.splitlines()
        sections: List[str] = []
        current_section: List[str] = []
        current_title: Optional[str] = None

        for line in lines:
            if line.startswith("## ") or line.startswith("# "):
                if current_section and current_title:
                    content = "\n".join(current_section).strip()
                    if len(content) > 20:
                        sections.append(f"{current_title}\n{content}")
                current_title = line.strip()
                current_section = []
            else:
                current_section.append(line)

        # 处理最后一节
        if current_section and current_title:
            content = "\n".join(current_section).strip()
            if len(content) > 20:
                sections.append(f"{current_title}\n{content}")

        return "\n\n".join(sections) if sections else None


class WorkflowEngine:
    """
    工作流自动编排引擎核心。

    负责协调 ContextBuilder, OutputExtractor 和 Agent，管理从任务启动到归档的全生命周期。
    """

    def __init__(self, name: str, stage: str, stage_idx: int, agent_name: str, callbacks: Dict[str, Callable]):
        self.name = name
        self.stage = stage
        self.stage_idx = stage_idx
        self.agent_name = agent_name
        self.callbacks = callbacks
        self.agent: Optional[Any] = None
        self.model_name: str = "N/A"

        self._output_lines: List[Tuple[str, str]] = []
        self._output_lock = threading.Lock()
        self._stage_output_saved: bool = False

        # 初始化辅助组件
        self.context_builder = ContextBuilder()
        self.output_extractor = OutputExtractor()

    def _add_log(self, source, msg):
        with self._output_lock:
            self._output_lines.append((source, msg))
        if "add_log" in self.callbacks:
            self.callbacks["add_log"](source, msg)

    def _is_running(self):
        if "is_running" in self.callbacks:
            return self.callbacks["is_running"]()
        return True

    def _on_ask_user(self, questions, res_queue):
        """处理交互式提问：将状态设为 pending，等待回复"""
        # 1. 持久化状态为 pending (供 CLI 拦截 advance)
        st = read_state(self.name)
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        write_state(self.name, st)

        # 2. 设置 Agent 内存状态为 waiting (供 TUI 显示黄色图标)
        if self.agent:
            self.agent.status = "waiting"

        if "on_ask_user" in self.callbacks:
            self.callbacks["on_ask_user"](questions, res_queue)

    def resume_running(self):
        """从交互挂起状态恢复为运行状态"""
        st = read_state(self.name)
        if st.get("stage_status") == "pending":
            st["stage_status"] = "running"
            st["updated_at"] = now()
            write_state(self.name, st)
        
        if self.agent:
            self.agent.status = "active"
        
        sw_log(self.name, "workflow resumed to running", "sw")

    def _on_agent_complete(self):
        if self._stage_output_saved:
            return

        with self._output_lock:
            lines = list(self._output_lines)

        saved = self.output_extractor.extract_and_save(
            self.name, self.stage, lines, self._add_log
        )

        if saved:
            self._stage_output_saved = True
            sw_log(self.name, "stage output saved (on_complete)", "sw")
            
            cur_label = STAGE_NAMES[self.stage_idx]
            if is_auto_advance():
                self._add_log("sw", f"🚀 自动推进: {cur_label} 阶段已完成，正在校验并进入下一阶段...")
                self.advance_stage()
            else:
                self._add_log("sw", f"✨ {cur_label} 阶段产出已就绪。你可以继续与 Agent 交流，或输入 /advance 推进到下一阶段。")

    def _run_hook_script(self, script_name: str) -> bool:
        """运行指定的 hook 脚本并返回是否通过"""
        hook_script = HOOKS_DIR / script_name
        if not hook_script.exists():
            return True

        try:
            result = subprocess.run(
                [str(hook_script), self.name],
                cwd=str(ROOT), check=False,
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                self._add_log("error", f"Hooks 验证失败: {script_name}")
                if result.stdout.strip():
                    self._add_log("error", result.stdout.strip()[:300])
                return False
            self._add_log("sw", f"Hooks 验证通过: {script_name}")
            return True
        except Exception as e:
            self._add_log("error", f"Hooks 执行异常: {e}")
            return False

    def _validate_pre_hooks(self) -> bool:
        """执行阶段启动前的校验"""
        return self._run_hook_script(f"pre_check_{self.stage}.sh")

    def _validate_post_hooks(self) -> bool:
        """执行阶段推进前的校验"""
        # 优先寻找 post_check，没有则回退到 legacy 的 check_
        post_script = f"post_check_{self.stage}.sh"
        legacy_script = f"check_{self.stage}.sh"

        if (HOOKS_DIR / post_script).exists():
            return self._run_hook_script(post_script)
        return self._run_hook_script(legacy_script)

    def _build_context(self):
        """兼容旧测试"""
        return self.context_builder.build(self.name, self.stage, self.stage_idx)

    def _extract_output(self):
        """兼容旧测试"""
        with self._output_lock:
            lines = list(self._output_lines)
        return self.output_extractor._extract_output(lines)

    # ── Agent 创建 ──

    def _create_agent(self):
        """根据 resolve_agent_type 和 resolve_agent_model 创建对应 Agent"""
        try:
            self.model_name = resolve_agent_model(self.stage, self.agent_name)
        except Exception as e:
            self._add_log("error", f"解析角色配置失败: {e}")
            self.model_name = ""

        try:
            agent_type = resolve_agent_type(self.stage, self.agent_name)
        except Exception as e:
            self._add_log("error", f"解析 Agent 类型失败: {e}")
            agent_type = "gemini"

        from ..agents.base import AgentFactory

        callbacks = {
            "add_log": self._add_log,
            "is_running": self._is_running,
            "on_complete": self._on_agent_complete,
            "on_ask_user": self._on_ask_user,
        }
        if "on_settlement" in self.callbacks:
            callbacks["on_settlement"] = self.callbacks["on_settlement"]

        self.agent = AgentFactory.create(agent_type, callbacks, self.name, self.stage, self.stage_idx, self.model_name)
        return self.agent

    # ── 阶段编排 ──

    def run_stage(self):
        """编排当前阶段：前置校验 → 构建上下文 → 创建 Agent → 启动。启动后状态标记为 \'running\'"""
        # 1. 前置校验 (Pre-hooks)
        if not self._validate_pre_hooks():
            self._add_log("error", "前置 Hooks 校验未通过，Agent 启动已取消。")
            return

        context = self.context_builder.build(self.name, self.stage, self.stage_idx)

        if not context:
            self._add_log("sw", "无上下文可注入，请检查任务文件")
            return

        self._create_agent()

        if not self.agent:
            self._add_log("error", "Agent 创建失败")
            return

        self._add_log("sw", f"启动 Agent ({self.model_name}) — {self.stage} ({STAGE_NAMES[self.stage_idx]})")

        # 更新状态为 running
        st = read_state(self.name)
        st["stage_status"] = "running"
        st["updated_at"] = now()
        write_state(self.name, st)

        # 注入上下文到 .input (用于离线记录)
        task_dir = TASKS / self.name
        input_file = task_dir / ".input"
        with open(input_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{now()}] system | === 启动运行: {self.stage} ===\n")
            f.write(context)
            f.write(f"\n[{now()}] system | --- END ---\n")

        self.agent.start()

        # 注入上下文给 Agent 进程
        if hasattr(self.agent, 'send'):
            self.agent.send(context, is_system=True)

        # PtyAgent 需要 reader 线程
        from ..agents.pty import PtyAgent
        if isinstance(self.agent, PtyAgent):
            threading.Thread(target=self.agent.reader_loop, daemon=True).start()

    def save_stage_output(self):
        """兼容旧接口，手动触发保存"""
        with self._output_lock:
            lines = list(self._output_lines)
        return self.output_extractor.extract_and_save(self.name, self.stage, lines, self._add_log)

    # ── 阶段推进 ──

    def advance_stage(self):
        """验证后置 hooks → 推进到下一阶段 → 自动启动新阶段"""
        sw_log(self.name, "advance_stage called", "sw")

        # 1. 保存当前产出
        self.save_stage_output()

        # 1.5 自动勾选 Gate（/advance = 用户确认）
        _auto_check_gate(self.name, self.stage)

        # 2. 验证后置 hooks (Post-hooks)
        if not self._validate_post_hooks():
            self._add_log("error", "后置 Hooks 验证未通过，无法推进")
            return False

        # 3. 检查是否为最后阶段
        if self.stage_idx >= len(STAGES) - 1:
            # 标记任务状态为已完成
            st_final = read_state(self.name)
            st_final["stage_status"] = "Finished"
            st_final["updated_at"] = now()
            write_state(self.name, st_final)
            upsert_task_summary(self.name, stage_status="Finished")
            sw_log(self.name, "🏁 任务已完成 (Finished)", "sw")

            self._add_log("sw", "🏁 任务所有阶段已完成！正在进入结算流程...")
            if "on_settlement" in self.callbacks:
                self.callbacks["on_settlement"]()
            return True

        # 4. 关闭当前 Agent
        if self.agent and hasattr(self.agent, 'shutdown'):
            self.agent.shutdown()

        # 5. 推进状态 — 支持 04-review 的路由决策
        if self.stage == "04-review":
            target = parse_route_field(self.name)
            if target and target in STAGES:
                next_idx = STAGES.index(target)
            else:
                self._add_log("error", "❌ Route 字段无效或未填写，无法推进。请在 Review Decision 中填写 **Route**: `目标阶段`")
                return False
        else:
            next_idx = self.stage_idx + 1

        next_stage = STAGES[next_idx]
        next_name = STAGE_NAMES[next_idx]

        st = read_state(self.name)

        # 6. 返工处理：注入上下文 + 循环保护
        _is_reroute = (self.stage == "04-review" and next_stage != "05-archive")
        if _is_reroute:
            # 递增返工计数
            st["reroute_count"] = st.get("reroute_count", 0) + 1
            st.setdefault("reroute_history", []).append({
                "from": self.stage,
                "to": next_stage,
                "at": now(),
                "count": st["reroute_count"],
            })

            # 循环保护
            if st["reroute_count"] > MAX_REROUTE:
                st["stage_status"] = "blocked"
                st["updated_at"] = now()
                write_state(self.name, st)
                upsert_task_summary(self.name, stage_status="blocked")
                self._add_log("error", f"⛔ 返工已超过 {MAX_REROUTE} 次，需人工介入。请在 TUI 中处理。")
                sw_log(self.name, f"reroute blocked: exceeded {MAX_REROUTE} reroutes", "sw")
                return False

            # 注入返工上下文
            inject_reroute_context(self.name, next_stage)
            # 返工到 brainstorming 时复位 Gate 勾选
            if next_stage == "01-brainstorming":
                _reset_gate_checkboxes(self.name, next_stage)
            sw_log(self.name, f"reroute: {self.stage} → {next_stage} (count={st['reroute_count']})", "sw")

        st["stage"] = next_stage
        st["stage_idx"] = next_idx
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        write_state(self.name, st)

        upsert_task_summary(self.name,
            stage=next_stage, stage_idx=next_idx, stage_status="pending")

        sw_log(self.name, f"state advanced to {next_stage} ({next_name})", "sw")
        self._stage_output_saved = False

        sw_log(self.name, f"advance → {next_stage} ({next_name})")
        self._add_log("sw", f"阶段推进 → {next_name}")

        # 6. 更新自身状态并自动启动下一阶段，保持 Monitor 沉浸感
        self.stage = next_stage
        self.stage_idx = next_idx

        self._add_log("sw", f"正在为您启动 {next_name} 阶段...")
        self.run_stage()

        return True
    # ── Agent 生命周期 ──

    def restart_agent(self):
        """重启当前 Agent"""
        if self.agent and hasattr(self.agent, 'restart'):
            self.agent.restart()
            from ..agents.pty import PtyAgent
            if isinstance(self.agent, PtyAgent):
                threading.Thread(target=self.agent.reader_loop, daemon=True).start()

    def shutdown(self):
        """关闭 Agent"""
        if self.agent and hasattr(self.agent, 'shutdown'):
            self.agent.shutdown()

    # ── 命令处理 ──

    def answer(self, text):
        """用户回复 Agent"""
        if self.agent:
            # 如果状态是 pending (意味着刚才在等待提问)，切回 running
            self.resume_running()
            self.agent.send(text)
        else:
            self._add_log("error", "Agent 未运行，无法回复")

    def handle_command(self, cmd):
        """处理 TUI 转发的 /command"""
        cmd = cmd.strip()
        if not cmd:
            return

        parts = cmd.split()
        sub = parts[0]

        if sub == "advance":
            self.advance_stage()
        elif sub == "status":
            st = read_state(self.name)
            self._add_log("sw", f"stage={st.get('stage','?')} status={st.get('stage_status','?')}")
        elif sub == "restart":
            self._add_log("sw", "重启 Agent...")
            self.restart_agent()
        elif sub == "context":
            ctx = self.context_builder.build(self.name, self.stage, self.stage_idx)
            if ctx:
                preview = ctx[:300] + ("..." if len(ctx) > 300 else "")
                self._add_log("system", f"当前上下文预览:\n{preview}")
            else:
                self._add_log("sw", "无上下文")
        elif sub == "answer":
            text = " ".join(parts[1:])
            self.answer(text)
        else:
            self._add_log("sw", f"未知命令: /{sub}  (可用: /advance /status /restart /context /answer /q)")