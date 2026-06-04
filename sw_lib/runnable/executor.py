"""WorkflowExecutor protocol — LangGraph-compatible interface.

Defines the contract that any workflow executor (WorkflowChain today,
LangGraph StateGraph tomorrow) must satisfy.
"""

from typing import Protocol, runtime_checkable

from .base import StageInput, StageOutput


@runtime_checkable
class WorkflowExecutor(Protocol):
    """Protocol for workflow executors.

    Structural typing: any object with invoke(StageInput) -> StageOutput
    satisfies this protocol. No explicit inheritance required.

    Current implementation: WorkflowChain
    Future implementation: LangGraphAdapter wrapping a compiled StateGraph
    """

    def invoke(self, input: StageInput) -> StageOutput:
        """Execute the workflow until termination or loop limit."""
        ...

    async def ainvoke(self, input: StageInput) -> StageOutput:
        """Async version for Web Dashboard."""
        ...
