"""Integration test: StageRunnable with PromptBuilder + StageOutputParser."""
import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from sw_lib.workflow.base import StageRunnable, StageInput
from sw_lib.prompts import PromptRegistry, PromptBuilder
from sw_lib.output.parser import StageOutputParser
from sw_lib.output.stages import BrainstormingOutput, ReviewOutput, CodingOutput
from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state


def _setup_task(task_name: str):
    task_dir = TASKS / task_name
    task_dir.mkdir(parents=True, exist_ok=True)
    write_state(task_name, {
        "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
        "stage_status": "pending", "agent": "mock", "target_dir": "repo/test",
    })
    (task_dir / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n## Gate\n- [ ] Design approved\n",
        encoding="utf-8",
    )
    return task_dir


class TestPhase3Integration:
    def test_brainstorming_with_prompt_and_parser(self):
        """Brainstorming StageRunnable uses PromptBuilder + StageOutputParser."""
        task_name = "phase3-brainstorming"
        task_dir = _setup_task(task_name)

        try:
            templates_dir = Path(__file__).resolve().parent.parent.parent.parent / "sw_lib" / "prompts" / "templates"
            registry = PromptRegistry(templates_dir)
            builder = PromptBuilder(registry)
            parser = StageOutputParser(BrainstormingOutput)

            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=builder,
                output_parser=parser,
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value=json.dumps({
                    "task_name": task_name,
                    "ambiguity_score": 8,
                    "goal": "build login module",
                    "risks": ["token expiry"],
                })):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            assert output.parsed is not None
            assert output.parsed["ambiguity_score"] == 8
            assert output.parsed["goal"] == "build login module"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_review_reroute_with_parser(self):
        """Review stage correctly parses route from agent output."""
        task_name = "phase3-review"
        task_dir = _setup_task(task_name)
        (task_dir / "03-coding.md").write_text(
            "# 03-Coding\n\n## Gate\n- [x] Code builds\n",
            encoding="utf-8",
        )
        (task_dir / "04-review.md").write_text(
            "# 04-Review\n\n"
            "## Review Decision\n"
            "- **Route**: `___`\n"
            "\n"
            "## Gate\n"
            "- [x] Full build passes\n",
            encoding="utf-8",
        )

        try:
            parser = StageOutputParser(ReviewOutput)

            stage = StageRunnable(
                "04-review", 3,
                context_builder=MagicMock(),
                output_parser=parser,
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value=json.dumps({
                    "task_name": task_name,
                    "passed": False,
                    "route": "03-Coding",
                    "reason": "Missing input validation",
                })):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="04-review", stage_idx=3,
                    ))

            assert output.route == "03-Coding"
            assert output.parsed["passed"] is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_parser_fallback_when_output_is_markdown(self):
        """When agent outputs markdown instead of JSON, parser falls back gracefully."""
        task_name = "phase3-fallback"
        task_dir = _setup_task(task_name)

        try:
            parser = StageOutputParser(CodingOutput)

            stage = StageRunnable(
                "03-coding", 2,
                context_builder=MagicMock(),
                output_parser=parser,
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            # Agent outputs plain markdown with key-value pairs
            agent_output = (
                "## Coding Complete\n\n"
                "pattern: Repository\n"
                "decisions: Used SQLAlchemy ORM\n"
                "build_passes: True\n"
                "test_passes: True\n"
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value=agent_output):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="03-coding", stage_idx=2,
                    ))

            assert output.parsed is not None
            assert output.parsed["pattern"] == "Repository"
            assert output.parsed["build_passes"] is True
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)
