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
    # 仅当 AI Output 中明确包含 Route 决策时才回填；找不到则保持 ___ 让门禁拦截
    if stage == "04-review" and "`___`" in content:
        route_from_output = _parse_route_from_ai_output(content)
        if route_from_output:
            content = content.replace(
                "- **Route**: `___`",
                f"- **Route**: `{route_from_output}`",
                1
            )
            # 如果是返工路由，同时自动回填 Reroute Evidence 表
            if route_from_output != "05-Archive":
                content = _auto_fill_evidence_from_ai_output(content)

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


def _auto_fill_evidence_from_ai_output(content: str) -> str:
    """从 AI Output 的审查结论表格中提取未通过的项，自动填入 Reroute Evidence 表。

    只在 Evidence 表的数据行全为占位符 (___) 时才会自动填充，
    已有人工内容时不做修改。
    """
    import re

    # 找到 Reroute Evidence 段
    ev_start = content.find("### Reroute Evidence")
    if ev_start < 0:
        return content
    # 找到下一个 ## 标题作为结束
    next_section = content.find("\n##", ev_start + 10)
    ev_end = next_section if next_section > ev_start else len(content)
    ev_section = content[ev_start:ev_end]

    # 检查数据行是否全为占位符
    data_lines = [l for l in ev_section.splitlines()
                  if l.strip().startswith("|") and l.strip().count("|") >= 5]
    data_lines = data_lines[2:]  # 跳过表头和分隔行
    has_real = any("___" not in l for l in data_lines)
    if has_real:
        return content  # 已有真实数据，不覆盖

    # 从 AI Output 提取未通过的审查项
    marker = "## 🤖 AI Output"
    ai_idx = content.find(marker)
    if ai_idx < 0:
        return content
    ai_section = content[ai_idx + len(marker):]
    # 截断到下一个 H2 标题（但排除 ### 子标题）
    ai_end = ai_section.find("\n## ")
    if ai_end > 0:
        ai_section = ai_section[:ai_end]
    elif ai_section.startswith("\n## "):
        ai_section = ""

    issues: list = []
    saw_table_header = False
    # 识别形如 | 门禁 | 状态 | 或 | 项目 | 结论 | 的审查表格
    for line in ai_section.splitlines():
        stripped = line.strip()
        if "|" not in stripped:
            continue
        # 检测表头行
        if not saw_table_header and ("门禁" in stripped or "状态" in stripped
                                      or "项目" in stripped or "Gate" in stripped):
            saw_table_header = True
            continue
        if stripped.startswith("|---"):
            continue
        if not saw_table_header:
            continue
        # 数据行：找 ⚠️ ❌ ⛔ 或非 ✅ 的状态
        if any(sym in stripped for sym in ('⚠️', '❌', '⛔', '⚠', '⛔')):
            parts = [p.strip() for p in stripped.split("|")]
            if len(parts) >= 3:
                gate_name = parts[1]
                status = parts[2] if len(parts) > 2 else ""
                issues.append((gate_name, status))

    if not issues:
        # 无明确问题行 → 不做任何改动
        return content

    # 用第一个问题构造 evidence 行
    gate_name, note = issues[0]
    row = f"| 1 | {gate_name} | medium | coding | 审查发现: {note} |"

    # 替换第一个占位符数据行 (| 1 | ___ | ...)
    pattern = r'(\|\s*)1(\s*\|\s*)___(\s*\|.*)'
    if re.search(pattern, content):
        content = re.sub(pattern, row, content, count=1)
    else:
        # 后备：在表头之后插入新行
        ev_idx = content.find("|---|------|---------|---------|-------------|")
        if ev_idx > 0:
            insert_pos = content.index("\n", ev_idx) + 1
            content = content[:insert_pos] + row + "\n" + content[insert_pos:]

    return content


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
    """Agent 上下文构建器 — 委托给 PromptBuilder（Phase 2）。

    _prompt_builder 在 bootstrap 时自动设置。
    """

    _prompt_builder = None

    @staticmethod
    def build(task_name: str, stage: str, stage_idx: int) -> Optional[str]:
        return ContextBuilder._prompt_builder.build(
            task_name=task_name, stage=stage, stage_idx=stage_idx,
        )


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

    Phase 1-3 集成：当 _workflow_chain 类变量被设置时，advance_stage() 会
    委托给 WorkflowChain，而非使用旧版硬编码逻辑。
    """

    _workflow_chain = None  # 类级变量：WorkflowChain 实例

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
            # Route agent text output and reasoning into output_lines for save_stage_output()
            "on_text": lambda t: self._add_log("agent", t),
            "on_reasoning": lambda t: self._add_log("agent", t),
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

        self.save_stage_output()
        _auto_check_gate(self.name, self.stage)
        if not self._validate_post_hooks():
            self._add_log("error", "后置 Hooks 验证未通过，无法推进")
            return False

        from ..runnable.base import StageInput

        # 用 chain 确定下一阶段（利用其路由逻辑），但只执行一步
        chain = WorkflowEngine._workflow_chain
        next_stage = self.stage
        next_idx = self.stage_idx

        try:
            # 线性推进：下一阶段 = stage_order 中当前阶段的下一个
            if self.stage in chain._stage_order:
                cur = chain._stage_order.index(self.stage)
                if cur + 1 < len(chain._stage_order):
                    next_stage = chain._stage_order[cur + 1]
                    s = chain._stage_map[next_stage]
                    next_idx = s.stage_idx
            # Review 阶段：从 state 读取 Route 决策
            if self.stage == "04-review":
                target = parse_route_field(self.name)
                if target and target in chain._stage_map:
                    next_stage = target
                    next_idx = chain._stage_map[target].stage_idx
        except (ValueError, IndexError, KeyError):
            pass  # fall through: stay on current stage

        if next_stage == self.stage:
            st = read_state(self.name)
            st["stage_status"] = "Finished"
            st["updated_at"] = now()
            write_state(self.name, st)
            upsert_task_summary(self.name, stage_status="Finished")
            sw_log(self.name, "🏁 任务已完成 (Finished)", "sw")
            self._add_log("sw", "🏁 任务所有阶段已完成！")
            if "on_settlement" in self.callbacks:
                self.callbacks["on_settlement"]()
            return True

        # 更新状态
        st = read_state(self.name)
        st["stage"] = next_stage
        st["stage_idx"] = next_idx
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        write_state(self.name, st)
        upsert_task_summary(self.name, stage=next_stage, stage_idx=next_idx, stage_status="pending")

        if self.agent and hasattr(self.agent, 'shutdown'):
            self.agent.shutdown()

        self.stage = next_stage
        self.stage_idx = next_idx
        self._add_log("sw", f"阶段推进 → {next_stage}")
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
            return self.advance_stage()
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