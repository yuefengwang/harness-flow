"""WorkflowChain — sequential stage orchestrator with reroute support.

Satisfies the WorkflowExecutor protocol. Future LangGraph replacement
will implement the same interface.
"""

from typing import Dict, List

from .base import HarnessRunnable, StageInput, StageOutput, StageRunnable


class RerouteLimitExceeded(Exception):
    """Raised when reroute count exceeds max_reroute."""
    def __init__(self, message: str, output: StageOutput = None):
        super().__init__(message)
        self.output = output


class WorkflowChain(HarnessRunnable):
    """Sequential multi-stage executor with review-reroute logic.

    Routes between stages based on StageOutput.route. When a stage
    sets route to a previous stage, the chain loops back.

    This routing logic will eventually be handled by LangGraph's
    conditional edges. The chain provides the same single-entry/
    single-exit semantics.
    """

    def __init__(self, stages: List[StageRunnable], max_reroute: int = 3):
        if not stages:
            raise ValueError("WorkflowChain requires at least one stage")
        self._stage_map: Dict[str, StageRunnable] = {s.stage: s for s in stages}
        self._stage_order: List[str] = [s.stage for s in stages]
        self.max_reroute = max_reroute
        self.active_stage: Optional[StageRunnable] = None

    def invoke(self, input: StageInput) -> StageOutput:
        """Execute stages in order, handling reroute loops.

        Raises:
            ValueError: start stage not in chain, or unknown route target
            RerouteLimitExceeded: reroute loop exceeded max_reroute
        """
        current_stage = input.stage
        if current_stage not in self._stage_map:
            raise ValueError(f"Stage '{current_stage}' is not in chain. Available: {self._stage_order}")

        current_idx = self._stage_order.index(current_stage)
        reroute_count = input.metadata.get("reroute_count", 0)
        previous_output = input.previous_output
        last_output: StageOutput = None

        try:
            while current_idx < len(self._stage_order):
                stage_key = self._stage_order[current_idx]
                stage = self._stage_map[stage_key]
                self.active_stage = stage

                stage_metadata = input.metadata.copy()
                stage_metadata["reroute_count"] = reroute_count
                stage_input = StageInput(
                    task_name=input.task_name,
                    stage=stage_key,
                    stage_idx=stage.stage_idx,
                    previous_output=previous_output,
                    metadata=stage_metadata,
                )
                output = stage.invoke(stage_input)
                last_output = output
                previous_output = output.parsed

                # If reroute is requested, follow it regardless of gate status
                if output.route and self._is_reroute(output.route, current_idx):
                    reroute_count += 1
                    if reroute_count > self.max_reroute:
                        raise RerouteLimitExceeded(
                            f"返工已超过 {self.max_reroute} 次，需人工介入", output=output
                        )
                    current_idx = self._resolve_route(output.route)
                    continue

                # If gate failed and no reroute back, pause execution and return
                if not output.gate_passed:
                    break

                current_idx += 1
        finally:
            self.active_stage = None

        return last_output

    async def ainvoke(self, input: StageInput) -> StageOutput:
        import asyncio
        return await asyncio.to_thread(self.invoke, input)

    def _is_reroute(self, route: str, current_idx: int) -> bool:
        """Check if the route points back to a previous stage.

        Raises ValueError for unknown route targets.
        """
        try:
            target_idx = self._resolve_route(route)
            return target_idx < current_idx
        except ValueError:
            raise

    def _resolve_route(self, route: str) -> int:
        """Map a route string like '03-coding' to its index in stage_order."""
        for stage in self._stage_map:
            if stage == route or stage.lower() == route.lower():
                return self._stage_order.index(stage)
        raise ValueError(f"Unknown route target: '{route}'. Available: {self._stage_order}")
