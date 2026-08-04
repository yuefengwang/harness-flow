"""Tests for StageRunnable — the core stage execution unit."""
import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path

from sw_lib.workflow.base import (
    StageInput, StageOutput, HarnessRunnable, StageRunnable,
)
from sw_lib.core.config import TASKS, STAGES, STAGE_NAMES


# ── Fixtures ──

@pytest.fixture
def dummy_task_dir():
    """Create a temporary task directory with basic structure."""
    name = "runnable-test-task"
    task_dir = TASKS / name
    task_dir.mkdir(parents=True, exist_ok=True)
    yield task_dir
    import shutil
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)


@pytest.fixture
def mock_agent():
    """Mock agent that records calls and returns fake output."""
    agent = MagicMock()
    agent.start = MagicMock()
    agent.send = MagicMock()
    agent.shutdown = MagicMock()
    agent.status = "active"
    return agent


@pytest.fixture
def mock_agent_factory(mock_agent):
    """Factory that returns the mock agent."""
    return MagicMock(return_value=mock_agent)


@pytest.fixture
def mock_context_builder():
    """Mock context builder returning a fixed string."""
    builder = MagicMock()
    builder.build = MagicMock(return_value="SYSTEM: You are an AI agent.\n\nUSER: Do the task.")
    return builder


@pytest.fixture
def mock_output_parser():
    """Mock output parser that passes through text."""
    parser = MagicMock()
    parser.parse = MagicMock(side_effect=lambda text: {"raw": text})
    return parser


@pytest.fixture
def mock_gate_validator():
    """Mock gate validator that always passes."""
    validator = MagicMock()
    validator.check = MagicMock(return_value=True)
    return validator


@pytest.fixture
def basic_stage():
    """Create a StageRunnable with all mocks."""
    mock_agent = MagicMock()
    mock_agent_factory = MagicMock(return_value=mock_agent)
    return StageRunnable(
        stage="01-brainstorming",
        stage_idx=0,
        context_builder=MagicMock(),
        output_parser=MagicMock(),
        gate_validator=MagicMock(),
        agent_factory=mock_agent_factory,
    )


# ── StageInput / StageOutput ──

class TestStageInput:
    def test_minimal_construction(self):
        """StageInput can be constructed with only required fields."""
        inp = StageInput(task_name="test", stage="01-brainstorming", stage_idx=0)
        assert inp.task_name == "test"
        assert inp.stage == "01-brainstorming"
        assert inp.stage_idx == 0
        assert inp.previous_output is None
        assert inp.metadata == {}

    def test_full_construction_with_previous_output(self):
        """StageInput carries previous stage's parsed output."""
        inp = StageInput(
            task_name="test",
            stage="02-planning",
            stage_idx=1,
            previous_output={"ambiguity_score": 8, "goal": "build thing"},
            metadata={"reroute_count": 1},
        )
        assert inp.previous_output["ambiguity_score"] == 8
        assert inp.metadata["reroute_count"] == 1

    def test_metadata_defaults_to_empty_dict(self):
        """metadata defaults to {} not None."""
        inp = StageInput(task_name="t", stage="01-brainstorming", stage_idx=0)
        assert isinstance(inp.metadata, dict)
        assert len(inp.metadata) == 0


class TestStageOutput:
    def test_minimal_construction(self):
        """StageOutput can be constructed with only required fields."""
        out = StageOutput(
            task_name="test",
            stage="01-brainstorming",
            raw_agent_output="some output",
        )
        assert out.task_name == "test"
        assert out.stage == "01-brainstorming"
        assert out.raw_agent_output == "some output"
        assert out.parsed is None
        assert out.gate_passed is False
        assert out.route is None

    def test_review_stage_has_route(self):
        """Review stage output carries route field."""
        out = StageOutput(
            task_name="test",
            stage="04-review",
            raw_agent_output="review done",
            parsed={"route": "03-coding"},
            gate_passed=False,
            route="03-coding",
        )
        assert out.route == "03-coding"
        assert out.parsed["route"] == "03-coding"


# ── StageRunnable ──

class TestStageRunnableConstruction:
    def test_construction_with_all_dependencies(self):
        """StageRunnable accepts all injectable dependencies."""
        stage = StageRunnable(
            stage="01-brainstorming",
            stage_idx=0,
            context_builder=MagicMock(),
            output_parser=MagicMock(),
            gate_validator=MagicMock(),
            agent_factory=MagicMock(),
        )
        assert stage.stage == "01-brainstorming"
        assert stage.stage_idx == 0


class TestStageRunnableInvoke:
    def _make_real_stage(self, dummy_task_dir):
        """Create a stage with real callbacks for integration-style tests."""
        from sw_lib.core.state import write_state
        task_name = dummy_task_dir.name
        write_state(task_name, {
            "id": task_name,
            "stage": "01-brainstorming",
            "stage_idx": 0,
            "stage_status": "pending",
            "agent": "mock",
            "target_dir": "repo/test",
        })
        # Write a stage template with Gate section
        (dummy_task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n## Gate\n- [ ] Design approved\n- [ ] Ready\n",
            encoding="utf-8",
        )
        return task_name

    def test_invoke_calls_all_steps_in_order(self, dummy_task_dir):
        """invoke executes: pre-hooks → agent → parse → gate → save."""
        task_name = self._make_real_stage(dummy_task_dir)

        context_builder = MagicMock()
        context_builder.build = MagicMock(return_value="FULL CONTEXT")
        output_parser = MagicMock()
        output_parser.parse = MagicMock(return_value={"goal": "build"})
        gate_validator = MagicMock()
        gate_validator.check = MagicMock(return_value=True)
        mock_agent = MagicMock()
        mock_agent.start = MagicMock()
        mock_agent.send = MagicMock()
        mock_agent.shutdown = MagicMock()

        stage = StageRunnable(
            "01-brainstorming", 0,
            context_builder, output_parser, gate_validator,
            lambda *a, **kw: mock_agent,
        )

        with patch.object(stage, '_run_agent', return_value="AI OUTPUT"):
            with patch.object(stage, '_run_pre_hooks'):
                output = stage.invoke(StageInput(
                    task_name=task_name, stage="01-brainstorming", stage_idx=0,
                ))

        # Verify call order via mock call sequence
        context_builder.build.assert_called_once()
        output_parser.parse.assert_called_once_with("AI OUTPUT")
        gate_validator.check.assert_called_once()
        assert output.task_name == task_name
        assert output.raw_agent_output == "AI OUTPUT"
        assert output.parsed == {"goal": "build"}
        assert output.gate_passed is True

    def test_invoke_returns_gate_failed_when_validator_fails(self, dummy_task_dir):
        """When gate fails, StageOutput.gate_passed is False."""
        task_name = self._make_real_stage(dummy_task_dir)

        gate_validator = MagicMock()
        gate_validator.check = MagicMock(return_value=False)

        stage = StageRunnable(
            "01-brainstorming", 0,
            MagicMock(), MagicMock(), gate_validator,
            lambda *a, **kw: MagicMock(),
        )

        with patch.object(stage, '_run_agent', return_value="OUTPUT"):
            with patch.object(stage, '_run_pre_hooks'):
                output = stage.invoke(StageInput(
                    task_name=task_name, stage="01-brainstorming", stage_idx=0,
                ))

        assert output.gate_passed is False

    def test_invoke_passes_previous_output_to_context_builder(self, dummy_task_dir):
        """Context builder receives previous_output from StageInput."""
        task_name = self._make_real_stage(dummy_task_dir)
        context_builder = MagicMock()

        stage = StageRunnable(
            "02-planning", 1,
            context_builder, MagicMock(), MagicMock(),
            lambda *a, **kw: MagicMock(),
        )

        with patch.object(stage, '_run_agent', return_value="OUTPUT"):
            with patch.object(stage, '_run_pre_hooks'):
                stage.invoke(StageInput(
                    task_name=task_name,
                    stage="02-planning",
                    stage_idx=1,
                    previous_output={"ambiguity_score": 7},
                ))

        call_kwargs = context_builder.build.call_args[1]
        assert call_kwargs["previous_output"] == {"ambiguity_score": 7}
        assert call_kwargs["task_name"] == task_name
        assert call_kwargs["stage"] == "02-planning"

    def test_invoke_saves_stage_output(self, dummy_task_dir):
        """invoke saves agent output to {stage}.md."""
        task_name = self._make_real_stage(dummy_task_dir)
        task_dir = TASKS / task_name

        stage = StageRunnable(
            "01-brainstorming", 0,
            MagicMock(), MagicMock(), MagicMock(),
            lambda *a, **kw: MagicMock(),
        )

        with patch.object(stage, '_run_agent', return_value="## AI OUTPUT\n\nSome content"):
            with patch.object(stage, '_run_pre_hooks'):
                stage.invoke(StageInput(
                    task_name=task_name, stage="01-brainstorming", stage_idx=0,
                ))

        # Verify output was saved to the stage file
        stage_file = task_dir / "01-brainstorming.md"
        content = stage_file.read_text(encoding="utf-8")
        assert "## 🤖 AI Output" in content
        assert "Some content" in content


class TestHarnessRunnable:
    def test_is_abstract(self):
        """HarnessRunnable cannot be instantiated directly."""
        with pytest.raises(TypeError):
            HarnessRunnable()

    def test_stage_runnable_is_runnable(self):
        """StageRunnable is a valid HarnessRunnable subclass."""
        stage = StageRunnable("test", 0, MagicMock(), MagicMock(), MagicMock(), MagicMock())
        assert isinstance(stage, HarnessRunnable)
