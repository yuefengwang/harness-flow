import threading
from typing import List, Dict, Any, Optional
from langgraph.graph import StateGraph, END, START

from .base import StageRunnable, StageInput, StageOutput
from .state import WorkflowState
from . import review_graph as _rg


from ..core.config import MAX_REROUTE

REVIEW_STAGE = "04-review"


def create_stage_node(stage_runnable: StageRunnable):
    """Factory to wrap a StageRunnable as a LangGraph node.
    
    Converts (state: WorkflowState, config: RunnableConfig) -> updates: dict.
    """
    from langchain_core.runnables import RunnableConfig

    def node_func(state: WorkflowState, config: RunnableConfig) -> Dict[str, Any]:
        # 1. Map state to StageInput
        # Extract transient callbacks and adapter from config
        conf = config.get("configurable", {})
        callbacks = conf.get("callbacks", {})
        adapter = conf.get("adapter")
        
        if adapter:
            adapter.active_stage = stage_runnable

        try:
            stage_input = StageInput(
                task_name=state["task_name"],
                stage=stage_runnable.stage,
                stage_idx=stage_runnable.stage_idx,
                previous_output=state["last_output"],
                metadata={
                    "callbacks": callbacks,
                    "reroute_count": state["reroute_count"]
                }
            )

            # 2. Execute existing logic (Hooks -> Agent -> Parser -> Gate)
            output: StageOutput = stage_runnable.invoke(stage_input)

            # 3. Return state updates
            res = {
                "last_output": output.parsed,
                "history_outputs": [output.parsed] if output.parsed else [],
                "next_route": output.route,
                "current_stage": output.stage,
                "stage_idx": stage_runnable.stage_idx,
                "gate_passed": output.gate_passed
            }

            # 4. 如果是归档阶段且门禁通过，触发结算回调
            if stage_runnable.stage == "05-archive" and output.gate_passed:
                if "on_settlement" in callbacks:
                    callbacks["on_settlement"]()
                    
            return res
        finally:
            if adapter:
                adapter.active_stage = None
    
    return node_func


def _add_review_subgraph(workflow, stage_runnable: StageRunnable):
    """把 04-review 展开为 prepare -> fan-out -> arbiter 的子图（A5 的 3.1）。

    每个主观审查者跑在**克隆出来的** StageRunnable 上，因为原实例持有可变的
    输出缓冲与 threading 事件，共用会让并行分支互相踩。

    单个审查者失败**不得**中断其余分支：实测 langgraph 的 fan-out 里
    一个分支抛异常会让整图崩、其余结果全丢（A5 的 3.4）。所以这里在
    节点内部兜住异常，记 `status: error` 而不是当成「无发现」。
    """
    from langchain_core.runnables import RunnableConfig

    def prepare(state: WorkflowState) -> Dict[str, Any]:
        # 新一轮审查开始，清掉上一轮的残留登记。
        _rg.reset_active_roles()
        return {}

    def subjective(payload: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
        role_id = payload.get("role_id")
        runner = _rg.clone_stage_for_role(stage_runnable, role_id)
        node = create_stage_node(runner)
        state = {
            "task_name": payload.get("task_name"),
            "current_stage": REVIEW_STAGE,
            "stage_idx": payload.get("stage_idx", 3),
            "last_output": payload.get("last_output"),
            "reroute_count": payload.get("reroute_count", 0),
            "history_outputs": [],
            "next_route": None,
            "gate_passed": False,
        }
        # 登记本分支，让显示层能拿到「全部在跑的角色」而不是只有最后一个
        # 覆盖 adapter.active_stage 的赢家（A5 的 R2）。
        _rg.register_active_role(role_id)
        try:
            res = node(state, config)
        except BaseException as exc:  # noqa: BLE001
            return {"review_findings": [{
                "role_id": role_id,
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }]}
        finally:
            _rg.unregister_active_role(role_id)

        finding = {
            "role_id": role_id,
            "status": "ok",
            "parsed": res.get("last_output"),
            "gate_passed": res.get("gate_passed"),
            "route": res.get("next_route"),
        }
        # 单角色回落路径必须把结果送回主状态，否则 StageOutput 会退化为空
        # —— 那会让既有的 TUI 推进逻辑读不到 route / gate_passed（验收 7）。
        update: Dict[str, Any] = {"review_findings": [finding]}
        if role_id is None or _is_sole_reviewer(role_id):
            update.update({
                "last_output": res.get("last_output"),
                "history_outputs": res.get("history_outputs", []),
                "next_route": res.get("next_route"),
                "current_stage": res.get("current_stage", REVIEW_STAGE),
                "stage_idx": res.get("stage_idx", 3),
                "gate_passed": res.get("gate_passed", False),
            })
        return update

    def objective(payload: Dict[str, Any]) -> Dict[str, Any]:
        # 客观轨的判定实现属 A6；此处只占位，失败不吞（A5 的 3.4）。
        return {"objective_result": {"status": "not_implemented", "owner": "A6"}}

    def arbiter(state: WorkflowState) -> Dict[str, Any]:
        # 仲裁逻辑属 A9。此处只是 fan-in 汇聚点，不改判定。
        return {}

    workflow.add_node(_rg.PREPARE_NODE, prepare)
    workflow.add_node(_rg.SUBJECTIVE_NODE, subjective, input_schema=dict)
    workflow.add_node(_rg.OBJECTIVE_NODE, objective, input_schema=dict)
    workflow.add_node(_rg.ARBITER_NODE, arbiter)

    workflow.add_conditional_edges(
        _rg.PREPARE_NODE, _rg.dispatch_reviewers,
        [_rg.SUBJECTIVE_NODE, _rg.OBJECTIVE_NODE, _rg.ARBITER_NODE])
    workflow.add_edge(_rg.SUBJECTIVE_NODE, _rg.ARBITER_NODE)
    workflow.add_edge(_rg.OBJECTIVE_NODE, _rg.ARBITER_NODE)
    workflow.add_edge(_rg.ARBITER_NODE, END)


def _is_sole_reviewer(role_id: str) -> bool:
    """该角色是否是唯一审查者。

    唯一时结果必须回填主状态（last_output / route / gate_passed），
    否则 StageOutput 退化为空，TUI 读不到推进依据（验收 7）。
    与 `StageRunnable._scoping_role_id` 用同一个判据：少于 2 个主观审查者
    就算单审查者路径。
    """
    from ..core.config import resolve_review_config
    return len(resolve_review_config().subjective) < 2


def build_harness_graph(stages: List[StageRunnable], max_reroute: int = MAX_REROUTE):
    """Constructs the LangGraph for Harness-Flow.
    
    Args:
        stages: List of StageRunnables in logical order.
        max_reroute: Maximum allowed rework loops.
    """
    workflow = StateGraph(WorkflowState)
    
    # 1. Add all Stage nodes
    stage_names = []
    for s in stages:
        if s.stage == REVIEW_STAGE:
            # 04-review 内部展开为 fan-out / fan-in 子图（A5 的 4.1）。
            # 其余阶段结构不变，仍是一阶段一 invoke。
            _add_review_subgraph(workflow, s)
        else:
            workflow.add_node(s.stage, create_stage_node(s))
        stage_names.append(s.stage)
    
    # 2. All stages go to END — TUI handles stage transitions.
    #    Each LangGraphAdapter.invoke() runs exactly one stage.
    for name in stage_names:
        if name != REVIEW_STAGE:
            workflow.add_edge(name, END)

    
    # 4. Entry point: route to the current_stage set in state
    def entry_router(state: WorkflowState):
        current = state["current_stage"].lower()
        for name in stage_names:
            if name.lower() == current:
                # 04-review 的入口是子图的 prepare 节点，不是同名阶段节点
                # —— 后者已不存在（A5 的 4.1）。
                return _rg.PREPARE_NODE if name == REVIEW_STAGE else name
        return stage_names[0]

    entry_targets = {
        (_rg.PREPARE_NODE if name == REVIEW_STAGE else name):
            (_rg.PREPARE_NODE if name == REVIEW_STAGE else name)
        for name in stage_names
    }
    workflow.add_conditional_edges(START, entry_router, entry_targets)
    
    # One-stage-per-invoke: no checkpointer needed since TUI manages transitions.
    return workflow.compile()


class LangGraphAdapter:
    """Adapts a CompiledGraph to a runnable workflow executor.

    Provides the core execution logic using LangGraph StateGraph.
    """
    def __init__(self, graph, stages: List[StageRunnable], max_reroute: int = MAX_REROUTE):
        self._graph = graph
        self.max_reroute = max_reroute
        self.active_stage: Optional[StageRunnable] = None
        self._current_task_name: Optional[str] = None
        
        self._stage_map = {s.stage: s for s in stages}
        self._task_locks: Dict[str, threading.RLock] = {}
        self._global_lock = threading.Lock()

    def invoke(self, input: StageInput) -> StageOutput:
        # Get or create per-task lock
        with self._global_lock:
            if input.task_name not in self._task_locks:
                self._task_locks[input.task_name] = threading.RLock()
            lock = self._task_locks[input.task_name]

        with lock:
            # Initial state (serializable only)
            state: WorkflowState = {
                "task_name": input.task_name,
                "current_stage": input.stage,
                "stage_idx": input.stage_idx,
                "history_outputs": [],
                "last_output": input.previous_output,
                "next_route": None,
                "reroute_count": input.metadata.get("reroute_count", 0),
                "gate_passed": False
            }
            
            # Pass non-serializable callbacks and metadata through config
            config = {
                "configurable": {
                    "thread_id": input.task_name,
                    "adapter": self,
                    "callbacks": input.metadata.get("callbacks", {}),
                    "metadata": {k: v for k, v in input.metadata.items() if k != "callbacks"}
                }
            }
            
            self._current_task_name = input.task_name
            final_state = self._graph.invoke(state, config)
            
            # Convert back to StageOutput
            return StageOutput(
                task_name=final_state["task_name"],
                stage=final_state["current_stage"],
                raw_agent_output="", # Graph doesn't keep raw text by default in State
                parsed=final_state["last_output"],
                gate_passed=final_state["gate_passed"],
                route=final_state["next_route"]
            )

    def answer(self, text: str):
        """User reply to the active agent."""
        if self.active_stage and self.active_stage.active_agent:
            # Resume state if it was waiting
            task_name = self.active_stage.active_agent.name
            from ..core.state import read_state
            st = read_state(task_name)
            if st and st.get("stage_status") == "pending":
                # 受控入口，避免覆盖并发写入的 Gate/Route（A0/R1）
                from .runtime import WorkflowRuntime
                WorkflowRuntime.set_stage_status(task_name, "running")
            
            if hasattr(self.active_stage.active_agent, 'send'):
                self.active_stage.active_agent.send(text)
        elif self.active_stage:
            # Agent completed but stage exists — restart with user's reply
            self._restart_agent_with_message(text)
        else:
            from ..core.utils import sw_log
            task_name = getattr(self, '_current_task_name', None)
            if task_name:
                sw_log(task_name, "当前没有活跃的 Agent。输入 /advance 推进到下一阶段。", "sw")
            else:
                sw_log("sw", "当前没有活跃的阶段。请先创建一个任务。", "error")

    def _restart_agent_with_message(self, text: str):
        stage = self.active_stage
        task_name = getattr(stage, '_current_task_name', None)
        if not task_name:
            return

        from ..core.utils import sw_log
        sw_log(task_name, f"Agent 已完成，重启以处理你的回复...", "sw")

        from ..core.config import TASKS
        task_dir = TASKS / task_name
        stage_file = task_dir / f"{stage.stage}.md"
        existing_output = ""
        if stage_file.exists():
            existing_output = stage_file.read_text(encoding="utf-8", errors="replace")

        followup = (
            f"当前阶段: {stage.stage}\n"
            f"已有产出：\n\n{existing_output}\n\n"
            f"用户回复: {text}\n\n"
            f"请根据用户的反馈更新上述产出。"
        )

        agent = stage.agent_factory(stage=stage.stage, task_name=task_name)
        if hasattr(agent, "callbacks") and hasattr(stage, '_saved_callbacks'):
            agent.callbacks.update(stage._saved_callbacks)

        stage.active_agent = agent
        agent.start()
        if hasattr(agent, 'send'):
            agent.send(followup, is_system=True)

    def handle_command(self, cmd: str):
        """Handle /commands (e.g. /advance, /status)."""
        cmd = cmd.strip()
        if not cmd:
            return
            
        if cmd == "status":
            from ..core.utils import sw_log
            if self.active_stage:
                sw_log(self.active_stage.active_agent.name if self.active_stage.active_agent else "unknown",
                       f"Graph active. current_stage={self.active_stage.stage}", "sw")
            else:
                sw_log("sw", f"Graph idle. stage=unknown", "sw")
        elif cmd == "advance":
            pass # Usually handled by service

    async def ainvoke(self, input: StageInput) -> StageOutput:
        import asyncio
        return await asyncio.to_thread(self.invoke, input)
