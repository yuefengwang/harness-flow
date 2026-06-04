"""Tests for WorkflowChain — multi-stage orchestration with reroute logic."""
import pytest
from unittest.mock import MagicMock, patch

from sw_lib.runnable.base import StageInput, StageOutput, StageRunnable
from sw_lib.runnable.chain import WorkflowChain, RerouteLimitExceeded
from sw_lib.runnable.executor import WorkflowExecutor


# ── Helpers ──

def _make_mock_stage(stage: str, stage_idx: int, parsed: dict = None,
                      route: str = None, gate_passed: bool = True):
    """Create a StageRunnable that returns a fixed StageOutput."""
    runnable = MagicMock(spec=StageRunnable)
    runnable.stage = stage
    runnable.stage_idx = stage_idx
    runnable.invoke = MagicMock(return_value=StageOutput(
        task_name="test-task",
        stage=stage,
        raw_agent_output=f"output from {stage}",
        parsed=parsed or {},
        gate_passed=gate_passed,
        route=route,
    ))
    return runnable


# ── Construction ──

class TestWorkflowChainConstruction:
    def test_chain_accepts_list_of_stages(self):
        """WorkflowChain constructed from a list of StageRunnables."""
        s1 = _make_mock_stage("01-brainstorming", 0)
        s2 = _make_mock_stage("02-planning", 1)
        chain = WorkflowChain([s1, s2])
        assert len(chain._stage_order) == 2

    def test_chain_default_max_reroute(self):
        """Default max_reroute is 3."""
        s1 = _make_mock_stage("01-brainstorming", 0)
        chain = WorkflowChain([s1])
        assert chain.max_reroute == 3

    def test_chain_custom_max_reroute(self):
        """max_reroute can be customized."""
        s1 = _make_mock_stage("01-brainstorming", 0)
        chain = WorkflowChain([s1], max_reroute=5)
        assert chain.max_reroute == 5

    def test_chain_satisfies_executor_protocol(self):
        """WorkflowChain satisfies WorkflowExecutor protocol (structural typing)."""
        s1 = _make_mock_stage("01-brainstorming", 0)
        chain = WorkflowChain([s1])
        # Protocol check: has invoke() with correct signature
        assert callable(chain.invoke)
        assert isinstance(chain, WorkflowExecutor)


# ── Linear Execution ──

class TestLinearExecution:
    def test_single_stage(self):
        """Chain with one stage invokes it and returns output."""
        s1 = _make_mock_stage("01-brainstorming", 0, parsed={"score": 8})
        chain = WorkflowChain([s1])

        output = chain.invoke(StageInput(task_name="t", stage="01-brainstorming", stage_idx=0))

        assert output.stage == "01-brainstorming"
        assert output.parsed == {"score": 8}
        s1.invoke.assert_called_once()

    def test_two_stages_sequential(self):
        """Chain invokes stages in order, passing previous output."""
        s1 = _make_mock_stage("01-brainstorming", 0, parsed={"result": "brainstorm"})
        s2 = _make_mock_stage("02-planning", 1, parsed={"result": "plan"})
        chain = WorkflowChain([s1, s2])

        output = chain.invoke(StageInput(task_name="t", stage="01-brainstorming", stage_idx=0))

        assert s1.invoke.called
        assert s2.invoke.called
        assert output.stage == "02-planning"
        assert output.parsed == {"result": "plan"}

    def test_three_stages_output_propagation(self):
        """Each stage receives previous stage's parsed output."""
        s1 = _make_mock_stage("01-brainstorming", 0, parsed={"v": 1})
        s2 = _make_mock_stage("02-planning", 1, parsed={"v": 2})
        s3 = _make_mock_stage("03-coding", 2, parsed={"v": 3})
        chain = WorkflowChain([s1, s2, s3])

        chain.invoke(StageInput(task_name="t", stage="01-brainstorming", stage_idx=0))

        # s2 received s1's output
        s2_input = s2.invoke.call_args[0][0]
        assert s2_input.previous_output == {"v": 1}
        # s3 received s2's output
        s3_input = s3.invoke.call_args[0][0]
        assert s3_input.previous_output == {"v": 2}

    def test_chain_starts_from_middle(self):
        """Chain can start from a non-first stage."""
        s1 = _make_mock_stage("01-brainstorming", 0)
        s2 = _make_mock_stage("02-planning", 1, parsed={"x": 1})
        s3 = _make_mock_stage("03-coding", 2)
        chain = WorkflowChain([s1, s2, s3])

        output = chain.invoke(StageInput(task_name="t", stage="02-planning", stage_idx=1))

        # s1 should NOT be called
        s1.invoke.assert_not_called()
        assert s2.invoke.called
        assert s3.invoke.called
        assert output.stage == "03-coding"


# ── Routing (Review → Reroute) ──

class TestRouting:
    def test_review_routes_to_coding(self):
        """Review with route="03-coding" sends chain back to coding once, then stops."""
        coding = MagicMock(spec=StageRunnable)
        coding.stage = "03-coding"
        coding.stage_idx = 2
        coding.invoke = MagicMock(return_value=StageOutput(
            task_name="t", stage="03-coding", raw_agent_output="ok",
            parsed={}, gate_passed=True))

        review = MagicMock(spec=StageRunnable)
        review.stage = "04-review"
        review.stage_idx = 3
        call_count = [0]
        def review_output(input):
            call_count[0] += 1
            if call_count[0] == 1:
                return StageOutput(task_name="t", stage="04-review",
                                    raw_agent_output="r1", parsed={},
                                    gate_passed=False, route="03-coding")
            return StageOutput(task_name="t", stage="04-review",
                                raw_agent_output="r2", parsed={},
                                gate_passed=True, route="05-archive")
        review.invoke = MagicMock(side_effect=review_output)

        archive = _make_mock_stage("05-archive", 4, parsed={"done": True})

        chain = WorkflowChain([_make_mock_stage("01-brainstorming", 0),
                                _make_mock_stage("02-planning", 1),
                                coding, review, archive])

        chain.invoke(StageInput(task_name="t", stage="03-coding", stage_idx=2))

        # coding called twice: initial + after reroute
        assert coding.invoke.call_count == 2
        # review called twice: initial + after coding's second pass
        assert review.invoke.call_count == 2
        assert archive.invoke.call_count == 1

    def test_review_routes_to_coding_then_re_enters(self):
        """After reroute back to coding, coding runs again then re-enters review."""
        # First pass: coding returns ok, review returns route=coding
        coding = MagicMock(spec=StageRunnable)
        coding.stage = "03-coding"
        coding.stage_idx = 2
        coding.invoke = MagicMock(side_effect=[
            StageOutput(task_name="t", stage="03-coding", raw_agent_output="first pass",
                         parsed={"done": False}, gate_passed=True),
            StageOutput(task_name="t", stage="03-coding", raw_agent_output="second pass",
                         parsed={"done": True}, gate_passed=True),
        ])

        review = MagicMock(spec=StageRunnable)
        review.stage = "04-review"
        review.stage_idx = 3
        review.invoke = MagicMock(side_effect=[
            StageOutput(task_name="t", stage="04-review", raw_agent_output="review1",
                         parsed={}, gate_passed=False, route="03-coding"),
            StageOutput(task_name="t", stage="04-review", raw_agent_output="review2",
                         parsed={}, gate_passed=True, route="05-archive"),
        ])

        archive = _make_mock_stage("05-archive", 4, parsed={"final": True})

        chain = WorkflowChain([_make_mock_stage("01-brainstorming", 0),
                                _make_mock_stage("02-planning", 1),
                                coding, review, archive])

        output = chain.invoke(StageInput(task_name="t", stage="03-coding", stage_idx=2))

        assert coding.invoke.call_count == 2
        assert review.invoke.call_count == 2
        assert archive.invoke.call_count == 1
        assert output.stage == "05-archive"
        assert output.parsed == {"final": True}

    def test_review_to_archive_no_reroute(self):
        """Review with route="05-archive" proceeds to archive normally."""
        s_r = _make_mock_stage("04-review", 3, parsed={}, route="05-archive")
        s_a = _make_mock_stage("05-archive", 4, parsed={"done": True})

        chain = WorkflowChain([_make_mock_stage("01-brainstorming", 0),
                                _make_mock_stage("02-planning", 1),
                                _make_mock_stage("03-coding", 2),
                                s_r, s_a])

        output = chain.invoke(StageInput(task_name="t", stage="04-review", stage_idx=3))

        assert s_a.invoke.called
        assert output.stage == "05-archive"

    def test_max_reroute_exceeded_raises(self):
        """Exceeding max_reroute raises RerouteLimitExceeded."""
        coding = _make_mock_stage("03-coding", 2, parsed={}, gate_passed=True)
        review = _make_mock_stage("04-review", 3, parsed={}, route="03-coding")

        chain = WorkflowChain([_make_mock_stage("01-brainstorming", 0),
                                _make_mock_stage("02-planning", 1),
                                coding, review],
                               max_reroute=1)

        # First pass: coding → review (route=coding) → reroute_count=1 → coding → review (route=coding)
        # → reroute_count=2 > max_reroute=1 → raises
        review.invoke = MagicMock(return_value=StageOutput(
            task_name="t", stage="04-review", raw_agent_output="r",
            parsed={}, route="03-coding",
        ))

        with pytest.raises(RerouteLimitExceeded, match="返工已超过 1 次"):
            chain.invoke(StageInput(task_name="t", stage="03-coding", stage_idx=2))

    def test_no_review_no_reroute(self):
        """Without review stage, chain just runs linearly with no reroute."""
        s1 = _make_mock_stage("01-brainstorming", 0, parsed={})
        s2 = _make_mock_stage("02-planning", 1, parsed={})
        chain = WorkflowChain([s1, s2])

        output = chain.invoke(StageInput(task_name="t", stage="01-brainstorming", stage_idx=0))

        assert output.stage == "02-planning"


# ── Error Handling ──

class TestErrorHandling:
    def test_unknown_start_stage_raises(self):
        """Starting from a stage not in the chain raises ValueError."""
        s1 = _make_mock_stage("01-brainstorming", 0)
        chain = WorkflowChain([s1])

        with pytest.raises(ValueError, match="not in chain"):
            chain.invoke(StageInput(task_name="t", stage="99-unknown", stage_idx=99))

    def test_unknown_route_target_raises(self):
        """Routing to a stage not in the chain raises ValueError."""
        review = _make_mock_stage("04-review", 3, parsed={}, route="99-unknown")
        chain = WorkflowChain([_make_mock_stage("01-brainstorming", 0), review])

        with pytest.raises(ValueError, match="Unknown route target"):
            chain.invoke(StageInput(task_name="t", stage="04-review", stage_idx=3))

    def test_empty_chain_raises(self):
        """Empty chain raises ValueError on construction."""
        with pytest.raises(ValueError, match="requires at least one"):
            WorkflowChain([])

    def test_route_future_stage_proceeds_normally(self):
        """If route points to a future (higher index) stage, advance normally."""
        s1 = _make_mock_stage("01-brainstorming", 0, parsed={}, route="03-coding")
        s2 = _make_mock_stage("03-coding", 2, parsed={"done": True})
        chain = WorkflowChain([s1, s2])

        output = chain.invoke(StageInput(task_name="t", stage="01-brainstorming", stage_idx=0))

        # route to future → just moves forward
        assert output.stage == "03-coding"
        assert output.parsed == {"done": True}


# ── Metadata propagation ──

class TestMetadataPropagation:
    def test_reroute_count_in_metadata(self):
        """During reroute, metadata.reroute_count is available to the stage."""
        coding = MagicMock(spec=StageRunnable)
        coding.stage = "03-coding"
        coding.stage_idx = 2
        coding.invoke = MagicMock(return_value=StageOutput(
            task_name="t", stage="03-coding", raw_agent_output="ok",
            parsed={}, gate_passed=True))

        review = MagicMock(spec=StageRunnable)
        review.stage = "04-review"
        review.stage_idx = 3
        call_count = [0]

        def review_side_effect(input):
            call_count[0] += 1
            if call_count[0] == 1:
                return StageOutput(task_name="t", stage="04-review",
                                    raw_agent_output="r1", parsed={},
                                    route="03-coding")
            return StageOutput(task_name="t", stage="04-review",
                                raw_agent_output="r2", parsed={},
                                route="05-archive")
        review.invoke = MagicMock(side_effect=review_side_effect)

        chain = WorkflowChain([_make_mock_stage("01-brainstorming", 0),
                                _make_mock_stage("02-planning", 1),
                                coding, review,
                                _make_mock_stage("05-archive", 4)])

        chain.invoke(StageInput(task_name="t", stage="03-coding", stage_idx=2))

        # Second coding call should have reroute_count=1
        second_coding_input = coding.invoke.call_args_list[1][0][0]
        assert second_coding_input.metadata["reroute_count"] == 1
