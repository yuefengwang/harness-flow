from typing import Annotated, TypedDict, List, Dict, Any, Optional
from operator import add

class WorkflowState(TypedDict):
    """Harness-Flow global workflow state.
    
    This replaces the implicit state passed between StageRunnables.
    """
    
    # Task identity
    task_name: str
    
    # Execution control
    current_stage: str
    stage_idx: int
    
    # History of parsed outputs from each stage
    # Annotated with add reducer to append new outputs
    history_outputs: Annotated[List[Dict[str, Any]], add]
    
    # The most recent parsed output
    last_output: Optional[Dict[str, Any]]
    
    # Routing decision from the agent (e.g. route: "03-coding")
    next_route: Optional[str]
    
    # Counter for rework loops
    reroute_count: int
    
    # Flag to indicate if the current stage passed its gate
    gate_passed: bool

