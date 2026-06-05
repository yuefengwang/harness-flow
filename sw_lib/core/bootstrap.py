"""Bootstrap — wires Phase 1/2/3 modules into the running WorkflowEngine.

Call bootstrap() once at CLI startup to activate the new workflow chain.
Without bootstrap(), the system falls back to the original WorkflowEngine logic.
"""

from pathlib import Path
from typing import Optional

from ..runnable import StageRunnable, GateValidator
from ..runnable.base import StageInput
from ..prompts import PromptRegistry, PromptBuilder
from ..output.parser import StageOutputParser
from ..output.stages import (
    BrainstormingOutput, PlanningOutput, CodingOutput,
    ReviewOutput, ArchiveOutput,
)
from ..core.engine import ContextBuilder, WorkflowEngine
from ..core.config import STAGES, STAGE_NAMES

# Module-level cache — only bootstrap once
from ..runnable.graph import LangGraphAdapter
_chain: Optional[LangGraphAdapter] = None


def bootstrap(templates_dir: Optional[Path] = None):
    """Activate the new workflow chain. Idempotent — safe to call multiple times.

    After calling this:
    - ContextBuilder delegates to PromptBuilder (Phase 2)
    - WorkflowEngine.advance_stage() delegates to WorkflowChain (Phase 1)
    - StageRunnable uses StageOutputParser (Phase 3)

    Args:
        templates_dir: path to prompt YAML templates. Defaults to
                       sw_lib/prompts/templates/ relative to this file.
    """
    global _chain
    if _chain is not None:
        return  # already bootstrapped

    if templates_dir is None:
        templates_dir = Path(__file__).resolve().parent.parent / "prompts" / "templates"

    registry = PromptRegistry(templates_dir)
    prompt_builder = PromptBuilder(registry)
    ContextBuilder._prompt_builder = prompt_builder

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

    from ..runnable.runtime import WorkflowRuntime
    WorkflowRuntime.initialize(stages)
    # 暂时保持兼容，直到 UI/Service 全部迁移
    WorkflowEngine._workflow_chain = WorkflowRuntime.get_executor()


def _make_agent_factory(stage: str):
    """Create an agent factory that delegates to the engine's existing agent creation.

    The agent factory receives (stage, task_name) and creates an agent
    via the engine's _create_agent mechanism. Since StageRunnable doesn't
    have access to the engine, we use a lazy proxy.
    """
    from ..agents.base import AgentFactory
    from ..core.config import resolve_agent_type, resolve_agent_model

    def factory(stage=stage, task_name=None):
        # StageRunnable passes task_name=None; the agent_factory callback
        # from the engine will resolve it.
        agent_type = resolve_agent_type(stage, "")
        model = resolve_agent_model(stage, "")
        callbacks = {}  # StageRunnable overrides these in _run_agent
        return AgentFactory.create(agent_type, callbacks, task_name or "", stage, 0, model)

    return factory
