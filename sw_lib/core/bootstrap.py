"""Bootstrap — wires LangChain and LangGraph modules into the runtime.

Call bootstrap() once at CLI startup to activate the new workflow architecture.
"""

from pathlib import Path
from typing import Optional

from ..workflow import StageRunnable, GateValidator
from ..prompts import PromptRegistry, PromptBuilder
from ..output.parser import StageOutputParser
from ..output.stages import (
    BrainstormingOutput, PlanningOutput, CodingOutput,
    ReviewOutput, ArchiveOutput,
)

# Module-level cache — only bootstrap once
from ..workflow.graph import LangGraphAdapter
_executor: Optional[LangGraphAdapter] = None


def bootstrap(templates_dir: Optional[Path] = None):
    """Activate the new workflow architecture. Idempotent — safe to call multiple times.

    Args:
        templates_dir: path to prompt YAML templates. Defaults to
                       sw_lib/prompts/templates/ relative to this file.
    """
    global _executor
    if _executor is not None:
        return  # already bootstrapped

    if templates_dir is None:
        templates_dir = Path(__file__).resolve().parent.parent / "prompts" / "templates"

    registry = PromptRegistry(templates_dir)
    prompt_builder = PromptBuilder(registry)

    stages = [
        StageRunnable(
            stage="01-brainstorming", stage_idx=0,
            context_builder=prompt_builder,
            output_parser=StageOutputParser(BrainstormingOutput),
            gate_validator=GateValidator(run_hook_script=False),
            agent_factory=_make_agent_factory("01-brainstorming"),
        ),
        StageRunnable(
            stage="02-planning", stage_idx=1,
            context_builder=prompt_builder,
            output_parser=StageOutputParser(PlanningOutput),
            gate_validator=GateValidator(run_hook_script=False),
            agent_factory=_make_agent_factory("02-planning"),
        ),
        StageRunnable(
            stage="03-coding", stage_idx=2,
            context_builder=prompt_builder,
            output_parser=StageOutputParser(CodingOutput),
            gate_validator=GateValidator(run_hook_script=False),
            agent_factory=_make_agent_factory("03-coding"),
        ),
        StageRunnable(
            stage="04-review", stage_idx=3,
            context_builder=prompt_builder,
            output_parser=StageOutputParser(ReviewOutput),
            gate_validator=GateValidator(run_hook_script=False),
            agent_factory=_make_agent_factory("04-review"),
        ),
        StageRunnable(
            stage="05-archive", stage_idx=4,
            context_builder=prompt_builder,
            output_parser=StageOutputParser(ArchiveOutput),
            gate_validator=GateValidator(run_hook_script=False),
            agent_factory=_make_agent_factory("05-archive"),
        ),
    ]

    from ..workflow.runtime import WorkflowRuntime
    WorkflowRuntime.initialize(stages)
    _executor = WorkflowRuntime.get_executor()


def _make_agent_factory(stage: str):
    """Create an agent factory that delegates to the engine's existing agent creation.

    The agent factory receives (stage, task_name) and creates an agent
    via the engine's _create_agent mechanism. Since StageRunnable doesn't
    have access to the engine, we use a lazy proxy.
    """
    from ..agents.base import AgentFactory
    from ..core.config import resolve_agent_type, resolve_agent_model, STAGES

    def factory(stage=stage, task_name=None):
        agent_type = resolve_agent_type(stage, "")
        model = resolve_agent_model(stage, "")
        callbacks = {}  # StageRunnable overrides these in _run_agent
        stage_idx = STAGES.index(stage) if stage in STAGES else 0
        return AgentFactory.create(agent_type, callbacks, task_name or "", stage, stage_idx, model)

    return factory
