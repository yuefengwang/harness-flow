"""sw_lib.workflow.runtime — Global workflow runtime manager.

Centralizes access to the active LangGraph executor.
"""

from typing import Optional, List, Dict, Any
from .base import StageRunnable
from .graph import build_harness_graph, LangGraphAdapter
from .utils import (
    auto_check_gate,
    parse_route_field,
    inject_reroute_context,
    _reset_gate_checkboxes,
)
from ..core.config import MAX_REROUTE, STAGES
from ..core.state import read_state, write_state, upsert_task_summary
from ..core.utils import now, sw_log

class WorkflowRuntime:
    """Manages the lifecycle of the workflow graph."""
    
    _executor: Optional[LangGraphAdapter] = None

    @classmethod
    def initialize(cls, stages: List[StageRunnable]):
        """Initialize the global executor with provided stages."""
        graph = build_harness_graph(stages, max_reroute=MAX_REROUTE)
        cls._executor = LangGraphAdapter(graph, stages, max_reroute=MAX_REROUTE)

    @classmethod
    def get_executor(cls) -> LangGraphAdapter:
        """Retrieve the global executor. Raises if not initialized."""
        if cls._executor is None:
            raise RuntimeError("WorkflowRuntime not initialized. Call bootstrap() first.")
        return cls._executor

    @classmethod
    def advance(cls, name: str) -> Dict[str, Any]:
        """Single source of truth for stage routing.

        Determines the next stage via the canonical STAGES chain (and the
        Review-stage Route field) and writes the updated state. This replaces
        the old service-side reach into the executor's private ``_stage_map`` /
        ``_stage_order`` attributes.

        Must be called after ``bootstrap()`` so that the graph (and its
        conditional edges) are consistent with the STAGES definition used here.
        """
        st = read_state(name)
        if st is None:
            raise RuntimeError(f"Task not found: {name}")
        idx = int(st.get("stage_idx", 0))
        cur_stage = STAGES[idx]

        # Terminal stage -> finished
        if idx >= len(STAGES) - 1:
            st["stage_status"] = "Finished"
            st["updated_at"] = now()
            write_state(name, st)
            upsert_task_summary(name, stage_status="Finished")
            sw_log(name, "🏁 任务已完成 (Finished)", "sw")
            return st

        auto_check_gate(name, cur_stage)

        next_stage = cur_stage
        next_idx = idx
        is_reroute = False

        # 1. Review-stage explicit routing takes priority
        if cur_stage == "04-review":
            target = parse_route_field(name)
            if target and target in STAGES:
                next_stage = target
                next_idx = STAGES.index(target)
                if next_idx < idx:
                    is_reroute = True

        # 2. Linear progression along the canonical STAGES chain
        if next_stage == cur_stage:
            if idx + 1 < len(STAGES):
                next_stage = STAGES[idx + 1]
                next_idx = idx + 1

        if next_stage == cur_stage:
            # Nowhere to go -> finished
            st["stage_status"] = "Finished"
            st["updated_at"] = now()
            write_state(name, st)
            upsert_task_summary(name, stage_status="Finished")
            return st

        # 3. Reroute context injection + gate reset for the target stage
        if is_reroute:
            inject_reroute_context(name, next_stage)
        _reset_gate_checkboxes(name, next_stage)

        st["stage"] = next_stage
        st["stage_idx"] = next_idx
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        write_state(name, st)
        upsert_task_summary(name, stage=next_stage, stage_idx=next_idx,
                            stage_status="pending")
        sw_log(name, f"advanced to {next_stage} (via chain logic)", "sw")
        return st
