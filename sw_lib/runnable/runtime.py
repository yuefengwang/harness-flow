"""sw_lib.runnable.runtime — Global workflow runtime manager.

Centralizes access to the active LangGraph executor.
"""

from typing import Optional, List
from .base import StageRunnable
from .graph import build_harness_graph, LangGraphAdapter
from ..core.config import MAX_REROUTE

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
