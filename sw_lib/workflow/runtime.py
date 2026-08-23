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
from ..core.state import (read_state, write_state, upsert_task_summary,
                          raise_if_corrupted, update_state, NO_CHANGE)
from ..core.utils import now, sw_log


def _stay_for_impl(name: str) -> bool:
    """03-coding 是否应停在本阶段做实现（即刚见证完红，phase 仍是 03a）。

    读的是 `red_witness.phase`，而钩子在准出时已把它推到 03b ——
    因此「phase 还是 03a」意味着见证尚未完成（开关关闭、mock、
    或存量任务首次进入），这时不该额外拦一道：真正的拦截在钩子里。

    换言之本函数只回答一个问题：**已经见证到红、但实现还没做**。
    """
    try:
        from . import red_witness as rw
    except Exception:
        return False
    try:
        record = rw.read_witness(name)
        if not record or record.get("mock"):
            return False
        # 见证过红（有 witnessed_at 与判据节点）但还没转绿 → 停下做实现
        if not record.get("witnessed_at") or not record.get("failed_nodes"):
            return False
        return not record.get("green_at")
    except Exception:
        return False


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

    @staticmethod
    def set_stage_status(name: str, status: str) -> Dict[str, Any]:
        """只改 `stage_status` / `updated_at`，走受控入口。

        为什么需要它：`base.py` / `graph.py` / `tui.py` 原本各自
        `read_state` → 改这两个字段 → `write_state`。实测（15 次交错）
        这会**抹掉用户刚签的 Gate** —— 它们读到签署前的旧快照，
        改完整体写回，签署凭空消失。判据丢失比状态显示错误严重得多。

        `runtime.py` 里那句"直接拿旧快照写回去会把重置结果整体覆盖掉"的
        注释说明这个坑早被踩过，但当时只在那一处就地重读绕过去了。
        """
        def mutate(state):
            if not state:
                return NO_CHANGE
            state["stage_status"] = status
            state["updated_at"] = now()
            return state

        return update_state(name, mutate)

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
        # A0/D0-1：损坏的 .state 在**入口**就拒绝。
        # 否则一路走到 write_state 才抛裸 ValueError —— 数据虽保住了，
        # 但用户看到的是深处崩栈，而非"哪个文件坏了、怎么修"。
        raise_if_corrupted(st, name)
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

        # 0. 03-coding 的子阶段（A2 的 3.2）。03a 见证完红之后要留在本阶段
        #    做实现，不能沿 STAGES 链走掉 —— 否则实现阶段被整个跳过，
        #    red_witness 沦为装饰。03a / 03b 是 `.state` 里的子状态，
        #    **不进 STAGES**：那个常量被 TUI / Web / entry_router 多处依赖。
        if cur_stage == "03-coding" and _stay_for_impl(name):
            ss.reset_gate(name, cur_stage)
            ss.render_gate_section(name, cur_stage)
            st = read_state(name) or st
            st["stage_status"] = "pending"
            st["updated_at"] = now()
            write_state(name, st)
            upsert_task_summary(name, stage_status="pending")
            sw_log(name, "03a 已见证红 → 留在 03-coding 进入实现（03b）", "sw")
            return st

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

        # 进入 04 就重新生成事实包（A3 的 4.2：每次进入都重新生成）。
        # 放在状态写盘之后：生成依赖 target_dir 与基线，而失败不该阻断推进 ——
        # 但**必须留痕**。事实包缺失时 prompt 会注入显式的「缺失」说明
        # （builder 的 _read_fact_pack），04 因此拿不到可据以判通过的输入，
        # 而不是静默回落到读 03-coding.md 的自述。
        if next_stage == "04-review":
            cls._generate_fact_pack(name)

        return st

    @staticmethod
    def _generate_fact_pack(name: str) -> None:
        """为 04 阶段生成事实包。失败只记日志，不抛。

        为什么不抛：推进已经落盘，抛出去会让调用方看到「推进失败」而状态
        已经变了。为什么不静默：C1 的解法依赖这份数据，缺了它审查就退回
        自问自答 —— 所以失败必须出现在任务日志里，且 prompt 侧会显式声明缺失。
        """
        try:
            from .fact_pack import FactPackError, generate

            pack = generate(name)
        except Exception as exc:   # noqa: BLE001 —— 生成失败不得阻断推进
            sw_log(name, f"⚠️ 事实包生成失败: {exc}", "error")
            return
        if pack.warnings:
            for w in pack.warnings:
                sw_log(name, f"⚠️ 事实包: {w}", "sw")
        else:
            sw_log(name, "事实包已生成（facts/）", "sw")
