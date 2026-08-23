import threading
from typing import List, Dict, Any, Optional
from langgraph.graph import StateGraph, END, START

from .base import StageRunnable, StageInput, StageOutput
from .state import WorkflowState


from ..core.config import MAX_REROUTE

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
        workflow.add_node(s.stage, create_stage_node(s))
        stage_names.append(s.stage)
    
    # 2. All stages go to END — TUI handles stage transitions.
    #    Each LangGraphAdapter.invoke() runs exactly one stage.
    for name in stage_names:
        workflow.add_edge(name, END)

    
    # 4. Entry point: route to the current_stage set in state
    def entry_router(state: WorkflowState):
        current = state["current_stage"].lower()
        for name in stage_names:
            if name.lower() == current:
                return name
        return stage_names[0]
        
    workflow.add_conditional_edges(START, entry_router, {name: name for name in stage_names})
    
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
            from ..core.state import read_state, write_state
            from ..core.utils import now
            st = read_state(task_name)
            if st and st.get("stage_status") == "pending":
                st["stage_status"] = "running"
                st["updated_at"] = now()
                write_state(task_name, st)
            
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
