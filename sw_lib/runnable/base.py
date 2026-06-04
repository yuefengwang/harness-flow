"""sw_lib.runnable.base — HarnessRunnable, StageRunnable, StageInput/StageOutput.

Provides the foundation for HarnessFlow's composable workflow architecture.
Each stage is a StageRunnable; orchestration is handled by WorkflowChain.
"""

import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.config import TASKS, STAGES, STAGE_NAMES, HOOKS_DIR
from ..core.state import read_state
from ..core.utils import sw_log


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

    def pipe(self, next_runnable: "HarnessRunnable") -> "HarnessRunnable":
        """Chain composition: self | next."""
        from .chain import WorkflowChain
        return WorkflowChain([self, next_runnable])


# ── Stage Runnable ──

class StageRunnable(HarnessRunnable):
    """Standard stage execution unit.

    Encapsulates the full stage lifecycle:
    pre-hooks → build prompt → run agent → parse output → gate check → save.

    All five stages (brainstorming, planning, coding, review, archive) use
    this same class. The review stage is NOT special-cased—its routing
    behaviour is handled by WorkflowChain reading output.route.
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
        self._agent_complete = threading.Event()

    # ── Public API ──

    def invoke(self, input: StageInput) -> StageOutput:
        """Execute the stage: hooks → agent → parse → gate → save."""
        self._run_pre_hooks(input.task_name)

        context = self.context_builder.build(
            task_name=input.task_name,
            stage=self.stage,
            stage_idx=self.stage_idx,
            previous_output=input.previous_output,
        )

        raw_output = self._run_agent(context)
        parsed = self._parse_output(raw_output)
        gate_passed = self.gate_validator.check(input.task_name, self.stage)
        self._save_stage_output(input.task_name, raw_output)

        parsed_dict = parsed.model_dump() if parsed and hasattr(parsed, 'model_dump') else parsed
        route = parsed_dict.get("route") if isinstance(parsed_dict, dict) else None

        return StageOutput(
            task_name=input.task_name,
            stage=self.stage,
            raw_agent_output=raw_output,
            parsed=parsed_dict,
            gate_passed=gate_passed,
            route=route,
        )

    async def ainvoke(self, input: StageInput) -> StageOutput:
        """Async invoke delegates to sync invoke for now."""
        import asyncio
        return await asyncio.to_thread(self.invoke, input)

    # ── Internal: Agent lifecycle ──

    def _run_agent(self, context: str) -> str:
        """Create agent, start it, send context, collect output, shutdown."""
        agent = self.agent_factory(
            stage=self.stage,
            task_name=None,  # will be resolved by factory from callbacks
        )

        self._agent_output_lines.clear()
        self._agent_complete.clear()

        original_callbacks = agent.callbacks
        agent.callbacks = {
            "add_log": self._on_agent_log,
            "is_running": lambda: True,
            "on_complete": lambda: self._agent_complete.set(),
        }

        agent.start()
        if hasattr(agent, 'send'):
            agent.send(context, is_system=True)

        self._agent_complete.wait(timeout=300)
        agent.shutdown()

        agent.callbacks = original_callbacks
        return self._collect_agent_output()

    def _on_agent_log(self, source: str, msg: str):
        """Callback: capture agent log lines."""
        self._agent_output_lines.append((source, msg))

    def _collect_agent_output(self) -> str:
        """Extract agent-originated text from collected log lines."""
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
