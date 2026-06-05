from typing import List, Dict, Any, Optional, Callable
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver

from .base import StageRunnable, StageInput, StageOutput
from .state import WorkflowState


def create_stage_node(runnable: StageRunnable):
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
            adapter.active_stage = runnable
            
        try:
            stage_input = StageInput(
                task_name=state["task_name"],
                stage=runnable.stage,
                stage_idx=runnable.stage_idx,
                previous_output=state["last_output"],
                metadata={
                    "callbacks": callbacks,
                    "reroute_count": state["reroute_count"]
                }
            )
            
            # 2. Execute existing logic (Hooks -> Agent -> Parser -> Gate)
            output: StageOutput = runnable.invoke(stage_input)
            
            # 3. Return state updates
            return {
                "last_output": output.parsed,
                "history_outputs": [output.parsed] if output.parsed else [],
                "next_route": output.route,
                "current_stage": output.stage,
                "stage_idx": runnable.stage_idx,
                "gate_passed": output.gate_passed
            }
        finally:
            if adapter:
                adapter.active_stage = None
    
    return node_func


from .checkpoint import FileCheckpointSaver

def build_harness_graph(stages: List[StageRunnable], max_reroute: int = 3):
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
    
    # 2. Linear edges (01 -> 02 -> 03 -> 04)
    # 05 is handled by ending the graph or special edge.
    for i in range(len(stage_names) - 1):
        src = stage_names[i]
        dst = stage_names[i+1]
        
        # Review has custom routing, don't add automatic forward edge to 05
        if src == "04-review":
            continue
            
        workflow.add_edge(src, dst)
    
    # 3. Routing logic for Review stage
    def review_router(state: WorkflowState):
        if not state["gate_passed"]:
            return END
            
        route = state.get("next_route")
        
        # Resolve target index to see if it's a reroute back
        if route:
            route_lower = route.lower()
            target_node = None
            for name in stage_names:
                if name.lower() == route_lower:
                    target_node = name
                    break
            
            if target_node:
                target_idx = stage_names.index(target_node)
                review_idx = stage_names.index("04-review")
                
                if target_idx < review_idx:
                    if state["reroute_count"] >= max_reroute:
                        return "05-archive"
                    return target_node
        
        # Default: proceed to archive
        return "05-archive"

    # Add conditional edges from review
    workflow.add_conditional_edges(
        "04-review",
        review_router,
        {name: name for name in stage_names} | {END: END}
    )
    
    workflow.add_edge("05-archive", END)
    
    # 4. Entry point: route to the current_stage set in state
    def entry_router(state: WorkflowState):
        current = state["current_stage"].lower()
        for name in stage_names:
            if name.lower() == current:
                return name
        return stage_names[0]
        
    workflow.add_conditional_edges(START, entry_router, {name: name for name in stage_names})
    
    return workflow.compile(checkpointer=FileCheckpointSaver())


class LangGraphAdapter:
    """Adapts a CompiledGraph to the WorkflowExecutor protocol.
    
    Enables drop-in replacement of WorkflowChain.
    """
    def __init__(self, graph, stages: List[StageRunnable], max_reroute: int = 3):
        self._graph = graph
        self.max_reroute = max_reroute
        self.active_stage: Optional[StageRunnable] = None # For TUI compatibility
        
        # Compatibility with TaskService.advance_stage
        self._stage_map = {s.stage: s for s in stages}
        self._stage_order = [s.stage for s in stages]

    def invoke(self, input: StageInput) -> StageOutput:
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
        
        # Run the graph
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
        else:
            # Fallback or error log? 
            # In TUI/Web we might want to log that no agent is active.
            pass

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
