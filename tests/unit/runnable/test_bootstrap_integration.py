"""Integration test: full workflow lifecycle with bootstrap activated.

Tests that all three phases are correctly wired together and
the WorkflowEngine delegates to the new modules.
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

from sw_lib.core.bootstrap import bootstrap
from sw_lib.core.engine import ContextBuilder, WorkflowEngine, _auto_check_gate
from sw_lib.core.config import TASKS, STAGES, STAGE_NAMES
from sw_lib.core.state import write_state, read_state
from sw_lib.runnable import StageRunnable, WorkflowChain, StageInput, WorkflowExecutor
from sw_lib.prompts import PromptRegistry, PromptBuilder


@pytest.fixture(autouse=True)
def ensure_bootstrapped():
    """Activate Phase 1-3 modules before each test."""
    bootstrap()
    yield
    # Don't reset — keep bootstrap active for all tests


class TestBootstrapWiring:
    """Verify bootstrap() correctly wires all components."""

    def test_context_builder_delegates_to_prompt_builder(self):
        """ContextBuilder._prompt_builder is set after bootstrap."""
        assert ContextBuilder._prompt_builder is not None
        assert isinstance(ContextBuilder._prompt_builder, PromptBuilder)

    def test_workflow_engine_has_chain(self):
        """WorkflowEngine._workflow_chain is set after bootstrap."""
        assert WorkflowEngine._workflow_chain is not None
        assert isinstance(WorkflowEngine._workflow_chain, WorkflowChain)

    def test_chain_satisfies_executor_protocol(self):
        """The chain implements WorkflowExecutor protocol."""
        chain = WorkflowEngine._workflow_chain
        assert isinstance(chain, WorkflowExecutor)

    def test_chain_has_all_five_stages(self):
        """Chain contains all 5 workflow stages."""
        chain = WorkflowEngine._workflow_chain
        for stage in ["01-brainstorming", "02-planning", "03-coding",
                       "04-review", "05-archive"]:
            assert stage in chain._stage_map

    def test_prompt_builder_can_build_all_stages(self):
        """PromptBuilder can build prompts for all 5 stages."""
        task_name = "boot-test-task"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock", "target_dir": "repo/t",
        })

        try:
            for stage, idx in [("01-brainstorming", 0), ("02-planning", 1),
                                ("03-coding", 2), ("04-review", 3), ("05-archive", 4)]:
                output = ContextBuilder.build(task_name, stage, idx)
                assert output is not None, f"Failed for {stage}"
                assert len(output) > 50, f"Output too short for {stage}"
                # Should NOT be the legacy string-concatenation output
                # (bootstrap means it goes through PromptBuilder)
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_workflow_engine_advance_uses_chain_routing(self):
        """advance_stage() reads from chain._stage_order for linear advance."""
        task_name = "chain-advance-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)

        write_state(task_name, {
            "id": task_name, "stage": "03-coding", "stage_idx": 2,
            "stage_status": "running", "agent": "mock",
        })
        (task_dir / "03-coding.md").write_text(
            "# 03-Coding\n\n## Gate\n- [x] Code builds\n",
            encoding="utf-8",
        )

        try:
            callbacks = {"add_log": MagicMock(), "is_running": lambda: True}
            engine = WorkflowEngine(task_name, "03-coding", 2, "", callbacks)

            with patch.object(engine, '_validate_post_hooks', return_value=True):
                with patch.object(engine, 'run_stage'):
                    with patch('sw_lib.core.engine._auto_check_gate'):
                        result = engine.advance_stage()

            assert result is True
            # Verify state advanced to 04-review (next in chain)
            st = read_state(task_name)
            assert st["stage"] == "04-review"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

class TestStageRunnableIntegration:
    """Verify StageRunnable works with real task templates and bootstrap."""

    def test_stage_runnable_with_real_gate(self):
        """StageRunnable with actual GateValidator checks template checkboxes."""
        task_name = "gate-integration-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock", "target_dir": "repo/t",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [x] Ready\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.runnable.gate import GateValidator
            from sw_lib.runnable.base import StageRunnable, StageInput

            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=MagicMock(),
                output_parser=MagicMock(),
                gate_validator=GateValidator(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value="AI output"):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            assert output.gate_passed is True
            assert output.stage == "01-brainstorming"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_stage_runnable_gate_fails_with_unchecked(self):
        """Gate fails when a checkbox is not filled."""
        task_name = "gate-fail-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n## Gate\n- [ ] Design approved\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.runnable.gate import GateValidator
            from sw_lib.runnable.base import StageRunnable, StageInput

            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=MagicMock(),
                output_parser=MagicMock(),
                gate_validator=GateValidator(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value="output"):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            assert output.gate_passed is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)


class TestChainRerouteWithEngine:
    """Verify reroute works end-to-end with the engine."""

    def test_review_reroute_through_chain(self):
        """Chain correctly reroutes from review back to coding."""
        task_name = "reroute-chain-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "04-review", "stage_idx": 3,
            "stage_status": "running", "agent": "mock",
        })
        (task_dir / "04-review.md").write_text(
            "# 04-Review\n\n## Gate\n- [x] Build passes\n",
            encoding="utf-8",
        )
        (task_dir / "03-coding.md").write_text(
            "# 03-Coding\n\n## Gate\n- [x] Code builds\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.runnable import StageRunnable, WorkflowChain
            from sw_lib.runnable.base import StageInput, StageOutput

            # Create mock stages that simulate reroute
            coding = MagicMock(spec=StageRunnable)
            coding.stage = "03-coding"
            coding.stage_idx = 2
            coding.invoke = MagicMock(side_effect=[
                StageOutput(task_name="t", stage="03-coding", raw_agent_output="",
                             parsed={}, gate_passed=True),
                StageOutput(task_name="t", stage="03-coding", raw_agent_output="",
                             parsed={"fixed": True}, gate_passed=True),
            ])

            review = MagicMock(spec=StageRunnable)
            review.stage = "04-review"
            review.stage_idx = 3
            review.invoke = MagicMock(side_effect=[
                StageOutput(task_name="t", stage="04-review", raw_agent_output="",
                             parsed={}, gate_passed=False, route="03-coding"),
                StageOutput(task_name="t", stage="04-review", raw_agent_output="",
                             parsed={}, gate_passed=True, route="05-archive"),
            ])

            archive = MagicMock(spec=StageRunnable)
            archive.stage = "05-archive"
            archive.stage_idx = 4
            archive.invoke = MagicMock(return_value=StageOutput(
                task_name="t", stage="05-archive", raw_agent_output="",
                parsed={"done": True}, gate_passed=True))

            chain = WorkflowChain([
                MagicMock(stage="01-brainstorming", stage_idx=0, spec=StageRunnable),
                MagicMock(stage="02-planning", stage_idx=1, spec=StageRunnable),
                coding, review, archive,
            ])

            result = chain.invoke(StageInput(
                task_name=task_name, stage="03-coding", stage_idx=2))

            # Coding should be called twice (initial + after reroute)
            assert coding.invoke.call_count == 2
            # Review should be called twice
            assert review.invoke.call_count == 2
            # Archive should be called once
            assert archive.invoke.call_count == 1
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)


class TestOutputIntegration:
    """Verify StageRunnable output persistence with bootstrap."""

    def test_save_stage_output_preserves_gate_section(self):
        """Output extraction saves AI output while preserving Gate section."""
        task_name = "save-output-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock", "target_dir": "repo/t",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Gate\n"
            "- [x] Design approved\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.runnable import StageRunnable, StageInput

            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=MagicMock(),
                output_parser=MagicMock(),
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value="## AI OUTPUT\n\nAnalysis result"):
                    stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            content = (task_dir / "01-brainstorming.md").read_text(encoding="utf-8")
            assert "## 🤖 AI Output" in content
            assert "Analysis result" in content
            assert "## Gate" in content
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_stage_output_saves_with_existing_ai_output(self):
        """When AI Output section already exists, it gets replaced."""
        task_name = "save-replace-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## 🤖 AI Output\n\nOld output\n\n"
            "## Gate\n- [x] Done\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.runnable import StageRunnable, StageInput

            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=MagicMock(),
                output_parser=MagicMock(),
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value="New output"):
                    stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            content = (task_dir / "01-brainstorming.md").read_text(encoding="utf-8")
            assert "New output" in content
            assert "Old output" not in content
            assert "## Gate" in content
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)


class TestParserIntegrationWithRunnable:
    """Verify StageOutputParser integration with StageRunnable."""

    def test_parser_called_with_real_schema(self):
        """StageRunnable._parse_output uses the real parser when provided."""
        task_name = "parser-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n## Gate\n- [x] Done\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.output.parser import StageOutputParser
            from sw_lib.output.stages import BrainstormingOutput
            from sw_lib.runnable import StageRunnable, StageInput

            parser = StageOutputParser(BrainstormingOutput)
            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=MagicMock(),
                output_parser=parser,
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            import json
            agent_output = json.dumps({
                "task_name": task_name,
                "ambiguity_score": 8,
                "goal": "build app",
            })

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value=agent_output):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            assert output.parsed is not None
            assert output.parsed["ambiguity_score"] == 8
            assert output.parsed["goal"] == "build app"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_parser_fallback_when_invalid_json(self):
        """When parser fails, output still has raw content."""
        task_name = "parser-fallback-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n## Gate\n- [x] Done\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.output.parser import StageOutputParser
            from sw_lib.output.stages import BrainstormingOutput
            from sw_lib.runnable import StageRunnable, StageInput

            parser = StageOutputParser(BrainstormingOutput)
            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=MagicMock(),
                output_parser=parser,
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value="not valid json at all"):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            # Should not raise — fallback to {"raw": text}
            assert output.parsed is not None
            assert output.parsed.get("raw") == "not valid json at all"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)


class TestEdgeCases:
    """Test boundary conditions that might break integration."""

    def test_empty_agent_output_does_not_crash(self):
        """Empty agent output should not crash the StageRunnable."""
        task_name = "empty-output-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n## Gate\n- [x] Done\n",
            encoding="utf-8",
        )

        try:
            from sw_lib.runnable import StageRunnable, StageInput

            stage = StageRunnable(
                "01-brainstorming", 0,
                context_builder=MagicMock(),
                output_parser=MagicMock(),
                gate_validator=MagicMock(),
                agent_factory=MagicMock(),
            )

            with patch.object(stage, '_run_pre_hooks'):
                with patch.object(stage, '_run_agent', return_value=""):
                    output = stage.invoke(StageInput(
                        task_name=task_name, stage="01-brainstorming", stage_idx=0,
                    ))

            assert output.raw_agent_output == ""
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_bootstrap_idempotent(self):
        """Calling bootstrap() multiple times does not break anything."""
        from sw_lib.core.bootstrap import bootstrap
        bootstrap()
        bootstrap()
        bootstrap()
        assert ContextBuilder._prompt_builder is not None
        assert WorkflowEngine._workflow_chain is not None

    def test_prompt_builder_handles_missing_task_dir(self):
        """PromptBuilder.build() works even when task dir doesn't exist."""
        output = ContextBuilder.build("nonexistent-xyz-123", "01-brainstorming", 0)
        assert output is not None
        assert "Harness-Flow" in output or "AI Agent" in output
