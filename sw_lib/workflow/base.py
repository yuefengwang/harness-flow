"""sw_lib.workflow.base — HarnessRunnable, StageRunnable, StageInput/StageOutput.

Provides the foundation for HarnessFlow's composable workflow architecture.
Each stage is a StageRunnable; orchestration is handled by LangGraph.
"""

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.config import TASKS, STAGES, HOOKS_DIR
from .mock_fixups import apply_mock_template_fixups
from . import stage_state
from ..core.state import read_state, write_state
from ..core.utils import sw_log, now


# ── Data models ──

@dataclass
class StageInput:
    """Cross-stage context envelope.

    Passed into each StageRunnable.invoke(). Carries the task identity,
    previous stage's structured output, and execution metadata.
    """
    task_name: str
    stage: str
    stage_idx: int
    previous_output: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageOutput:
    """Result of a single stage execution.

    Contains raw agent output (for file persistence), parsed structured data
    (for cross-stage type-safe passing), gate validation result, and optional
    routing directive (populated by review stages).
    """
    task_name: str
    stage: str
    raw_agent_output: str
    parsed: Optional[Dict[str, Any]] = None
    gate_passed: bool = False
    route: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── Base Runnable ──

class HarnessRunnable(ABC):
    """Minimal execution unit in HarnessFlow's workflow graph.

    Corresponds to LangChain's Runnable concept but adds HarnessFlow-specific
    lifecycle hooks (pre/post validation) and stage state management.
    """

    @abstractmethod
    def invoke(self, input: StageInput) -> StageOutput:
        """Execute this workflow synchronously."""
        ...

    @abstractmethod
    async def ainvoke(self, input: StageInput) -> StageOutput:
        """Execute this workflow asynchronously (for Web Dashboard)."""
        ...



# ── Stage Runnable ──

class StageRunnable(HarnessRunnable):
    """Standard stage execution unit.

    Encapsulates the full stage lifecycle:
    pre-hooks → build prompt → run agent → parse output → gate check → save.

    All five stages (brainstorming, planning, coding, review, archive) use
    this same class. The review stage is NOT special-cased—its routing
    behaviour is handled by LangGraph conditional edges reading output.route.
    """

    # 阶段等待上限（类属性，便于测试覆写；生产默认与旧行为一致）
    FIRST_RESPONSE_TIMEOUT = 300.0   # 等待 agent 首轮回复
    MULTI_TURN_TIMEOUT = 600.0       # 多轮会话总时长（等 /advance）

    def __init__(
        self,
        stage: str,
        stage_idx: int,
        context_builder,
        output_parser,
        gate_validator,
        agent_factory: Callable[..., Any],
    ):
        self.stage = stage
        self.stage_idx = stage_idx
        self.context_builder = context_builder
        self.output_parser = output_parser
        self.gate_validator = gate_validator
        self.agent_factory = agent_factory

        self._agent_output_lines: List[Tuple[str, str]] = []
        self._agent_text_buffer: List[str] = []
        self._agent_complete = threading.Event()
        self.active_agent: Optional[Any] = None
        self._current_task_name: Optional[str] = None
        self._saved_callbacks: Dict[str, Any] = {}

        # Multi-turn agent support (Direction A)
        self._stage_done = threading.Event()        # TUI signals "wrap up"
        self._agent_finalized = threading.Event()   # agent confirms shutdown
        self._invoke_done = threading.Event()       # invoke() fully complete

    # ── Public API ──

    def flush_output(self, task_name: str) -> bool:
        """把 agent 目前为止的产出落盘，不关闭 agent。返回是否写入。

        多轮模式下 _save_stage_output 只在 _run_agent 返回后执行，而它要等
        _stage_done —— 那是校验通过后才置位的。所以校验读到的会是还没写入
        产出的文件：agent 明明已经给出结论，用户却被告知「N 个待填项未完成」，
        且无从修改。/advance 在校验前调用本方法打破这个闭环。
        """
        output = self._collect_agent_output()
        if not output.strip():
            return False
        try:
            self._save_stage_output(task_name, output)
            return True
        except Exception:
            return False

    def invoke(self, input: StageInput) -> StageOutput:
        """Execute the stage: hooks → agent → parse → gate → save.

        CRITICAL: This method MUST always update state to idle/error and save
        output, even when parsing or gate checks fail. Otherwise the TUI and
        driver will deadlock waiting for a stage_status that never changes.
        """
        self._current_task_name = input.task_name
        self._saved_callbacks = input.metadata.get("callbacks", {}).copy()
        self._run_pre_hooks(input.task_name)

        self._invoke_done.clear()
        context = self.context_builder.build(
            task_name=input.task_name,
            stage=self.stage,
            stage_idx=self.stage_idx,
            previous_output=input.previous_output,
        )

        # 1. Update state to 'running'
        # 走受控入口：裸 read→改→write 会抹掉并发签署的 Gate（A0/R1 实测）。
        from .runtime import WorkflowRuntime
        WorkflowRuntime.set_stage_status(input.task_name, "running")

        # 1.5 播种门禁定义。必须在 agent 启动之前完成：用户可能在 agent
        #     说完之前就按 [A] 签署。
        self._seed_stage_gate(input.task_name)

        # 2. Record injected context to .input (for offline playback/debugging)
        task_dir = TASKS / input.task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        input_file = task_dir / ".input"
        with open(input_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{now()}] system | === 启动运行: {self.stage} ===\n")
            f.write(str(context))
            f.write(f"\n[{now()}] system | --- END ---\n")

        raw_output = self._run_agent(context, input)
        
        # 3. Mark stage idle (agent done). Must happen before parse/gate/save
        #    so the TUI stops showing "Agent working..." even if later steps fail.
        try:
            from .runtime import WorkflowRuntime
            WorkflowRuntime.set_stage_status(input.task_name, "idle")
        except Exception:
            pass
        
        # 4. Parse, save, gate check — save MUST run before gate check so
        #    MockAgent's auto-[x] and Route fill-in take effect before validation.
        parsed = None
        gate_passed = False
        
        try:
            parsed = self._parse_output(raw_output)
        except Exception as e:
            # Fallback: keep raw text as a plain dict
            parsed = {"raw": raw_output, "_parse_error": str(e)}
        
        try:
            self._save_stage_output(input.task_name, raw_output)
        except Exception:
            pass

        try:
            gate_passed = self.gate_validator.check(input.task_name, self.stage)
        except Exception as e:
            sw_log(input.task_name, f"gate check failed: {e}", "error")

        parsed_dict = parsed.model_dump() if hasattr(parsed, 'model_dump') else parsed
        route = parsed_dict.get("route") if isinstance(parsed_dict, dict) else None

        # 4. If archive stage, perform physical archival
        if self.stage == "05-archive":
            self._perform_archival(input.task_name)

        try:
            return StageOutput(
                task_name=input.task_name,
                stage=self.stage,
                raw_agent_output=raw_output,
                parsed=parsed_dict,
                gate_passed=gate_passed,
                route=route,
            )
        finally:
            self._invoke_done.set()

    async def ainvoke(self, input: StageInput) -> StageOutput:
        """Async invoke delegates to sync invoke for now."""
        import asyncio
        return await asyncio.to_thread(self.invoke, input)

    # ── Internal: Archival ──

    def _perform_archival(self, task_name: str):
        """将任务的所有阶段产出复制到 docs/history 目录下，区分开发类型。"""
        import shutil
        from pathlib import Path
        from ..core.config import ROOT

        st = read_state(task_name)
        target_dir = st.get("target_dir", "")

        # 1. 判定开发类别 (Self vs Third-party)
        category = "external"
        if target_dir == ".":
            category = "self"
        else:
            # 优先从 target_dir 提取项目名 (假设结构包含 repo/)
            p = Path(target_dir)
            if "repo" in p.parts:
                idx = p.parts.index("repo")
                if idx + 1 < len(p.parts):
                    category = f"repo/{p.parts[idx+1]}"
                else:
                    category = "repo"
            elif target_dir.startswith("repo/"):
                parts = target_dir.split("/")
                if len(parts) > 1:
                    category = f"repo/{parts[1]}"
                else:
                    category = "repo"

        history_dir = ROOT / "docs" / "history" / category / task_name
        history_dir.mkdir(parents=True, exist_ok=True)

        task_dir = TASKS / task_name
        sw_log(task_name, f"正在归档产出到 {history_dir}...", "sw")

        copied_count = 0
        for stage in STAGES:
            stage_file = task_dir / f"{stage}.md"
            if stage_file.exists():
                shutil.copy2(stage_file, history_dir / f"{stage}.md")
                copied_count += 1

        # 复制 .log 文件作为执行记录
        log_file = task_dir / ".log"
        if log_file.exists():
            shutil.copy2(log_file, history_dir / "execution.log")

        sw_log(task_name, f"归档完成，共复制 {copied_count} 个文档。", "sw")
    # ── Internal: Agent lifecycle ──

    def _run_agent(self, context: str, input: StageInput) -> str:
        """Create agent, start it, send context. Multi-turn: keep agent alive
        until _stage_done is signaled (via /advance), then finalize."""
        agent = self.agent_factory(
            stage=self.stage,
            task_name=input.task_name,
        )
        self.active_agent = agent

        self._agent_output_lines.clear()
        self._agent_text_buffer.clear()
        self._agent_complete.clear()
        self._stage_done.clear()
        self._agent_finalized.clear()

        original_callbacks = agent.callbacks.copy() if hasattr(agent, "callbacks") else {}
        injected_callbacks = input.metadata.get("callbacks", {})
        original_callbacks.update(injected_callbacks)
        
        def composed_add_log(source: str, msg: str):
            self._on_agent_log(source, msg)
            if "add_log" in original_callbacks:
                original_callbacks["add_log"](source, msg)
                
        def composed_on_complete():
            self._agent_complete.set()
            if "on_complete" in original_callbacks:
                original_callbacks["on_complete"]()

        def composed_on_text(text: str):
            self._agent_text_buffer.append(text)
            composed_add_log("agent", text)
            if "on_text" in original_callbacks:
                original_callbacks["on_text"](text)

        new_callbacks = original_callbacks.copy()
        new_callbacks["add_log"] = composed_add_log
        new_callbacks["on_text"] = composed_on_text
        new_callbacks["on_complete"] = composed_on_complete
        if "is_running" not in new_callbacks:
            new_callbacks["is_running"] = lambda: bool(self.active_agent)

        agent.callbacks = new_callbacks

        agent.start()

        # Agent 启动失败时立即收尾：继续往下走只会白等 300s+600s 超时，
        # 而 UI 在这段时间里完全没有反馈（这正是 opencode 启动失败时的表现）。
        if getattr(agent, "status", None) == "error":
            composed_add_log("error", f"{self.stage} agent 启动失败，阶段中止")
            try:
                agent.shutdown()
            finally:
                agent.callbacks = original_callbacks
                self.active_agent = None
                self._agent_finalized.set()
            return self._collect_agent_output()

        if hasattr(agent, 'send'):
            agent.send(context, is_system=True)

        # PtyAgent requires reader_loop thread
        from ..agents.pty import PtyAgent
        if isinstance(agent, PtyAgent):
            threading.Thread(target=agent.reader_loop, daemon=True).start()

        # First response
        try:
            self._agent_complete.wait(timeout=self.FIRST_RESPONSE_TIMEOUT)
        except Exception:
            pass

        # 首轮就失败（如发送被拒），同样不必再挂满多轮超时
        if getattr(agent, "status", None) == "error" and not self._agent_text_buffer:
            composed_add_log("error", f"{self.stage} agent 未产出内容，阶段中止")
            try:
                agent.shutdown()
            finally:
                agent.callbacks = original_callbacks
                self.active_agent = None
                self._agent_finalized.set()
            return self._collect_agent_output()

        # Multi-turn loop: agent stays alive for conversation
        try:
            deadline = time.monotonic() + self.MULTI_TURN_TIMEOUT
            while not self._stage_done.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._agent_complete.wait(timeout=min(0.5, remaining))
                if self._agent_complete.is_set():
                    self._agent_complete.clear()
        finally:
            agent.shutdown()
            agent.callbacks = original_callbacks
            self.active_agent = None
            self._agent_finalized.set()

        return self._collect_agent_output()

    def _on_agent_log(self, source: str, msg: str):
        """Callback: capture agent log lines."""
        self._agent_output_lines.append((source, msg))

    def _collect_agent_output(self) -> str:
        """Extract agent-originated text from collected on_text callbacks and log lines."""
        text_output = "".join(self._agent_text_buffer)
        if text_output:
            return text_output
        agent_lines = [msg for src, msg in self._agent_output_lines if src == "agent"]
        return "\n".join(agent_lines) if agent_lines else ""

    # ── Internal: Parsing ──

    def _parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        """Parse agent output through the configured output_parser."""
        if self.output_parser and hasattr(self.output_parser, 'parse'):
            try:
                return self.output_parser.parse(raw_output)
            except Exception:
                return {"raw": raw_output}
        return {"raw": raw_output}

    # ── Internal: Hooks ──

    def _run_pre_hooks(self, task_name: str):
        """Run pre-stage hook script if it exists. Non-fatal on failure."""
        import subprocess
        pre_script = HOOKS_DIR / f"pre_check_{self.stage}.sh"
        if pre_script.exists():
            try:
                subprocess.run(
                    [str(pre_script), task_name],
                    cwd=str(HOOKS_DIR.parent),
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
            except Exception:
                pass

    # ── Internal: Output persistence ──

    def _seed_stage_gate(self, task_name: str):
        """把本阶段的门禁定义落进 `.state`，并渲染出可读的 Gate 区。

        `read_gate` 在没有记录时会回退模板定义，所以判定不依赖这一步；播种是
        为了让 `.state` 自描述 —— hook、web、`sw state get` 读状态就够，不必
        反过来猜模板长什么样。旧任务（模板加 Gate 之前建的）也在这里获得
        可签署的门禁项，不需要再往 Markdown 里补区块。
        """
        try:
            stage_state.seed_gate(task_name, self.stage)
            stage_state.render_gate_section(task_name, self.stage)
        except Exception:
            # 播种失败不该挡住阶段执行：判定仍可回退模板定义
            pass

    def _save_stage_output(self, task_name: str, output: str):
        """把 agent 产出写进 {stage}.md，用 nonce 围栏界定产出区。

        产出区的边界由 sw 写入的 ``<!-- sw:ai-output:start <nonce> -->`` 决定，
        nonce 存在 ``.state`` 里、agent 看不到也猜不到。这样多轮 flush 能精确
        替换上一次的产出，而不需要靠「找下一个 ``## Gate``」来猜产出区在哪 ——
        后者会被 agent 正文里复述的 ``## Gate`` 骗到，导致模板 Gate 区每次
        flush 追加一份、待办数量越推进越多（见 docs/design-json-state-source.md）。
        """
        if not output.strip():
            return

        task_dir = TASKS / task_name
        stage_file = task_dir / f"{self.stage}.md"

        existing = ""
        if stage_file.exists():
            existing = stage_file.read_text(encoding="utf-8")

        nonce = stage_state.issue_output_nonce(task_name, self.stage)
        block = stage_state.render_output_block(nonce, output)

        region = stage_state.split_output_region(existing)
        if region is not None:
            before, after = region
            tail = after.lstrip("\n")
            new_content = before.rstrip("\n") + "\n\n" + block + (f"\n{tail}" if tail else "")
        else:
            # 首次写入：此时文件里还没有围栏外的 agent 文本，模板 Gate 的位置
            # 是确定的，可以安全地把产出插在它之前。
            gate_pos = existing.rfind("\n## Gate")
            if gate_pos >= 0:
                head, tail = existing[:gate_pos], existing[gate_pos + 1:]
                new_content = head.rstrip("\n") + "\n\n" + block + "\n" + tail
            else:
                new_content = existing.rstrip("\n") + "\n\n" + block

        stage_file.write_text(new_content, encoding="utf-8")

        # Mock 模式的模板回填已抽离到 mock_fixups（生产路径不感知 mock）
        fixed = apply_mock_template_fixups(new_content, self.stage)
        if fixed != new_content:
            stage_file.write_text(fixed, encoding="utf-8")
