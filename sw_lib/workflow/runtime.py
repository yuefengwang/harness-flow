"""sw_lib.workflow.runtime — Global workflow runtime manager.

Centralizes access to the active LangGraph executor.
"""

from typing import Optional, List, Dict, Any
from .base import StageRunnable
from .graph import build_harness_graph, LangGraphAdapter
from .utils import (
    inject_reroute_context,
)
from . import stage_state as ss
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

        next_stage = cur_stage
        next_idx = idx
        is_reroute = False

        # 1. Review 阶段的显式路由优先。读 `.state` 而不是 04-review.md：
        #    推进是不可逆动作（改 stage_idx、注入返工上下文、重置门禁），
        #    只能采纳用户的决定。agent 在正文里写 **Route**: `xxx` 不算决定。
        if cur_stage == "04-review":
            target = ss.read_route(name)
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
        # 目标阶段的门禁必须回到未签署：带着上一轮的签名会让它直接放行。
        ss.reset_gate(name, next_stage)
        ss.render_gate_section(name, next_stage)

        # 返工时必须作废这一轮的 Route，否则它会一直生效：返工到 03 再回到 04
        # 时，上一轮的 target 仍在 .state 里，read_route 立刻返回旧值 ——
        # 推进无视用户的新选择直接推回 03，而 TUI 也因为「Route 已有值」不再
        # 显示 A/B/C 路由面板，用户连改的入口都没有，只能在 03↔04 之间无限
        # 循环（任务 T3）。
        #
        # 只作废返工的那种：前进到 05-archive 是终点，不会再回到 04，
        # 留着它才能让事后重跑 04 的硬校验、翻状态复盘都看到决策本身。
        if is_reroute:
            ss.reset_route(name)

        # reset_gate / reset_route 刚刚写过盘，`st` 是函数入口读的旧快照 ——
        # 直接拿它写回去会把重置结果整体覆盖掉。重读一次再改字段。
        st = read_state(name) or st
        st["stage"] = next_stage
        st["stage_idx"] = next_idx
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        write_state(name, st)
        upsert_task_summary(name, stage=next_stage, stage_idx=next_idx,
                            stage_status="pending")
        sw_log(name, f"advanced to {next_stage} (via chain logic)", "sw")
        return st
