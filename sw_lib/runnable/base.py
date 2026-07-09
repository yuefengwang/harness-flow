"""sw_lib.runnable.base — HarnessRunnable, StageRunnable, StageInput/StageOutput.

Provides the foundation for HarnessFlow's composable workflow architecture.
Each stage is a StageRunnable; orchestration is handled by LangGraph.
"""

import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.config import TASKS, STAGES, STAGE_NAMES, HOOKS_DIR, is_mock_agent
from ..core.state import read_state, write_state
from ..core.utils import sw_log, now


# ── Exceptions ──

class RerouteLimitExceeded(Exception):
    """Raised when the workflow loops back more than max_reroute times."""
    def __init__(self, message: str, output: 'StageOutput'):
        super().__init__(message)
        self.output = output


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
        """Execute this runnable synchronously."""
        ...

    @abstractmethod
    async def ainvoke(self, input: StageInput) -> StageOutput:
        """Execute this runnable asynchronously (for Web Dashboard)."""
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
        st = read_state(input.task_name)
        if st:
            st["stage_status"] = "running"
            st["updated_at"] = now()
            write_state(input.task_name, st)

        # 2. Record injected context to .input (for offline playback/debugging)
        task_dir = TASKS / input.task_name
        input_file = task_dir / ".input"
        with open(input_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{now()}] system | === 启动运行: {self.stage} ===\n")
            f.write(str(context))
            f.write(f"\n[{now()}] system | --- END ---\n")

        raw_output = self._run_agent(context, input)
        
        # 3. Mark stage idle (agent done). Must happen before parse/gate/save
        #    so the TUI stops showing "Agent working..." even if later steps fail.
        try:
            st = read_state(input.task_name)
            if st:
                st["stage_status"] = "idle"
                st["updated_at"] = now()
                write_state(input.task_name, st)
        except Exception:
            pass
        
        # 4. Parse, save, gate check — save MUST run before gate check so
        #    MockAgent's auto-[x] and Route fill-in take effect before validation.
        parsed = None
        gate_passed = False
        parse_error = None
        
        try:
            parsed = self._parse_output(raw_output)
        except Exception as e:
            parse_error = str(e)
            # Fallback: keep raw text as a plain dict
            parsed = {"raw": raw_output, "_parse_error": str(e)}
        
        try:
            self._save_stage_output(input.task_name, raw_output)
        except Exception:
            pass

        try:
            gate_passed = self.gate_validator.check(input.task_name, self.stage)
        except Exception as e:
            pass

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
        from ..core.state import read_state

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
        if hasattr(agent, 'send'):
            agent.send(context, is_system=True)

        # PtyAgent requires reader_loop thread
        from ..agents.pty import PtyAgent
        if isinstance(agent, PtyAgent):
            threading.Thread(target=agent.reader_loop, daemon=True).start()

        # First response
        try:
            self._agent_complete.wait(timeout=300)
        except:
            pass

        # Multi-turn loop: agent stays alive for conversation
        try:
            deadline = time.monotonic() + 600  # total 10min timeout
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

    def _save_stage_output(self, task_name: str, output: str):
        """Save agent output to {stage}.md under the task directory."""
        if not output.strip():
            return

        task_dir = TASKS / task_name
        stage_file = task_dir / f"{self.stage}.md"

        existing = ""
        if stage_file.exists():
            existing = stage_file.read_text(encoding="utf-8")

        ai_marker = "\n\n## 🤖 AI Output\n"
        gate_marker = "\n## Gate"

        if ai_marker in existing:
            parts = existing.split(ai_marker, 1)
            after = parts[1] if len(parts) > 1 else ""
            gate_pos = after.find(gate_marker)
            if gate_pos >= 0:
                preserved = after[gate_pos:]
            elif after.startswith("## Gate"):
                preserved = after
            else:
                preserved = ""
            new_content = parts[0] + ai_marker + output + ("\n" + preserved if preserved else "")
        elif gate_marker in existing:
            parts = existing.split(gate_marker, 1)
            new_content = parts[0] + ai_marker + output + gate_marker + parts[1]
        else:
            new_content = existing + ai_marker + output

        stage_file.write_text(new_content, encoding="utf-8")

        if is_mock_agent():
            content = stage_file.read_text(encoding="utf-8")
            # 只保留 AI Output 区域（## 🤖 AI Output → ## Gate 之间）不替换，
            # 模板区和 Gate 区的 [ ] → [x] 确保校验通过
            ai_mrkr = "\n## 🤖 AI Output\n"
            gate_mrkr = "\n## Gate"
            ai_pos = content.find(ai_mrkr)
            if ai_pos >= 0:
                gate_pos = content.find(gate_mrkr, ai_pos + len(ai_mrkr))
                if gate_pos >= 0:
                    before = content[:ai_pos].replace("[ ]", "[x]")
                    if self.stage == "04-review":
                        before = before.replace("- **Route**: `___`", "- **Route**: `05-Archive`", 1)
                    output_area = content[ai_pos:gate_pos]
                    gate_area = content[gate_pos:].replace("[ ]", "[x]")
                    content = before + output_area + gate_area
                else:
                    content = content.replace("[ ]", "[x]")
            else:
                content = content.replace("[ ]", "[x]")
            stage_file.write_text(content, encoding="utf-8")
