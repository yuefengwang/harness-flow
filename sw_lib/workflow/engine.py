"""sw_lib.workflow.engine — WorkflowEngine abstraction.

Phase 3: Web must drive the workflow through an abstract engine interface
instead of reaching into the LangGraph executor's private attributes
(``executor.active_stage.active_agent``). This module defines the
:class:`WorkflowEngine` ABC and its concrete LangGraph-backed implementation.

The concrete class is the ONLY place allowed to touch executor internals;
the Web layer depends solely on the ABC.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..core.state import read_state
from ..core.utils import sw_log


class WorkflowEngine(ABC):
    """Abstract workflow engine driven by the Web layer."""

    @abstractmethod
    def start_stage(self, stage_input: Any) -> None:
        """Start executing a stage in the background (non-blocking)."""

    @abstractmethod
    def submit_answer(self, task_name: str, text: str) -> None:
        """Forward a user answer to the running stage."""

    @abstractmethod
    def submit_command(self, task_name: str, cmd: str) -> None:
        """Forward a command (e.g. "status") to the running stage."""

    @abstractmethod
    def shutdown(self, task_name: str) -> None:
        """Shut down any agent running for the given task."""

    @abstractmethod
    def get_status(self, task_name: str) -> str:
        """Return the run status for the task (``idle``/``running``/...)."""


class LangGraphWorkflowEngine(WorkflowEngine):
    """LangGraph-backed engine.

    This is the single allowed owner of executor-internal access. The Web
    layer never sees ``executor.active_stage.active_agent`` directly.
    """

    def __init__(self) -> None:
        self._executor = None  # resolved lazily after bootstrap()

    def _get_executor(self):
        from .runtime import WorkflowRuntime
        if self._executor is None:
            # Lazily bootstrap the core engine on first use. Web (and any other
            # consumer) reuses the same engine instead of re-wiring it; bootstrap()
            # is idempotent so repeated calls are safe.
            if WorkflowRuntime._executor is None:
                from ..core.bootstrap import bootstrap
                bootstrap()
            self._executor = WorkflowRuntime.get_executor()
        return self._executor

    def start_stage(self, stage_input: Any) -> None:
        # Started on a daemon thread by the caller (WebEngineSession); here we
        # just expose the executor entry point.
        self._get_executor().invoke(stage_input)

    def submit_answer(self, task_name: str, text: str) -> None:
        self._get_executor().answer(text)

    def submit_command(self, task_name: str, cmd: str) -> None:
        self._get_executor().handle_command(cmd)

    def shutdown(self, task_name: str) -> None:
        executor = self._get_executor()
        try:
            stage = executor.active_stage
            if stage and stage.active_agent:
                stage.active_agent.shutdown()
        except Exception:
            sw_log(task_name, "[web] shutdown failed", "error")

    def get_status(self, task_name: str) -> str:
        # Status is sourced from persisted state (event-friendly), NOT from the
        # live agent object. Falls back to "idle" when no status is recorded.
        st = read_state(task_name)
        if st and st.get("agent_status"):
            return st["agent_status"]
        return "idle"
