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

    # 配置校验（A14）。必须在建图**之前** —— 建完一张错的图再报错，
    # 那张图已经进了 WorkflowRuntime，后续调用方拿到的是半成品。
    #
    # 这里是 `assert_config_valid` 的**生产调用点**。改造前它只被测试调用
    # （形状 S7），于是 `validate_config` 里 6 条 error 级校验从未在真实
    # 启动路径上执行过：配置指向不存在的角色、审查者 kind 写错、
    # 04 阶段无任何审查者，全部静默通过。
    from .config import assert_config_valid
    assert_config_valid()

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

    def factory(stage=stage, task_name=None, role_id=None):
        # role_id 非空时按角色解析 agent/model（A4 的 3.7，A5 的 4.3 消费此签名）。
        agent_type = resolve_agent_type(stage, "", role_id=role_id)
        model = resolve_agent_model(stage, "", role_id=role_id)
        callbacks = {}  # StageRunnable overrides these in _run_agent
        stage_idx = STAGES.index(stage) if stage in STAGES else 0
        return AgentFactory.create(agent_type, callbacks, task_name or "", stage,
                                   stage_idx, model, role_id=role_id)

    return factory
