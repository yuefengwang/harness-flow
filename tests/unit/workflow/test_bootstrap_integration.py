"""Integration test: full workflow lifecycle with bootstrap activated.

Tests that all components are correctly wired and the global executor
is properly initialized.
"""
import pytest
from unittest.mock import MagicMock, patch

from sw_lib.core.bootstrap import bootstrap
from sw_lib.workflow.runtime import WorkflowRuntime
from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import StageInput


@pytest.fixture(autouse=True)
def ensure_bootstrapped():
    """Activate modules before each test."""
    bootstrap()
    yield


class TestBootstrapWiring:
    """Verify bootstrap() correctly wires all components."""

    def test_executor_is_available(self):
        """WorkflowRuntime.get_executor() is available after bootstrap."""
        executor = WorkflowRuntime.get_executor()
        assert executor is not None
        from sw_lib.workflow.graph import LangGraphAdapter
        assert isinstance(executor, LangGraphAdapter)

    def test_executor_has_all_five_stages(self):
        """Executor contains all 5 workflow stages."""
        executor = WorkflowRuntime.get_executor()
        for stage in ["01-brainstorming", "02-planning", "03-coding",
                       "04-review", "05-archive"]:
            assert stage in executor._stage_map

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
            # Get the real prompt builder from a stage workflow
            executor = WorkflowRuntime.get_executor()
            stage_runnable = executor._stage_map["01-brainstorming"]
            builder = stage_runnable.context_builder
            
            for stage, idx in [("01-brainstorming", 0), ("02-planning", 1),
                                ("03-coding", 2), ("04-review", 3), ("05-archive", 4)]:
                output = builder.build(task_name, stage, idx)
                assert output is not None, f"Failed for {stage}"
                assert len(output) > 50, f"Output too short for {stage}"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)


class TestStageRunnableIntegration:
    """Verify StageRunnable works with real task templates."""

    @pytest.fixture(autouse=True)
    def _fast_stage_timeouts(self):
        """把阶段等待压到毫秒级。

        StageRunnable 的多轮循环要等 /advance 信号（生产上由用户触发），
        测试里没人触发，用生产默认值会白等 300s+600s，整个 suite 卡死。
        """
        from sw_lib.workflow.base import StageRunnable
        first, multi = StageRunnable.FIRST_RESPONSE_TIMEOUT, StageRunnable.MULTI_TURN_TIMEOUT
        StageRunnable.FIRST_RESPONSE_TIMEOUT = 0.3
        StageRunnable.MULTI_TURN_TIMEOUT = 0.3
        yield
        StageRunnable.FIRST_RESPONSE_TIMEOUT = first
        StageRunnable.MULTI_TURN_TIMEOUT = multi

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
            "# 01-Brainstorming\n\n## Gate\n- [ ] Approved\n", encoding="utf-8"
        )
        # 签署走 .state（门禁判定不再读 Markdown 复选框）
        from sw_lib.workflow import stage_state as ss
        ss.sign_gate(task_name, "01-brainstorming")

        try:
            executor = WorkflowRuntime.get_executor()
            stage = executor._stage_map["01-brainstorming"]
            
            # Mock agent factory to return a mock agent
            with patch.object(stage, 'agent_factory') as mock_factory:
                mock_agent = MagicMock()
                mock_agent.callbacks = {}
                
                # Make the send method complete the agent immediately to avoid hanging
                def mock_send(*args, **kwargs):
                    if "on_complete" in mock_agent.callbacks:
                        mock_agent.callbacks["on_complete"]()
                mock_agent.send.side_effect = mock_send
                
                mock_factory.return_value = mock_agent
                
                # Mock _on_agent_log and _collect_agent_output to return AI Output
                with patch.object(stage, '_collect_agent_output', return_value="## AI Output\nDone"):
                    output = stage.invoke(StageInput(task_name=task_name, stage="01-brainstorming", stage_idx=0))
                    assert output.gate_passed is True
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_stage_runnable_gate_fails_with_unchecked(self):
        """Gate fails if checkboxes are not all filled."""
        task_name = "gate-fail-test"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending",
        })
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n## Gate\n- [ ] Unchecked\n", encoding="utf-8"
        )

        try:
            executor = WorkflowRuntime.get_executor()
            stage = executor._stage_map["01-brainstorming"]
            
            with patch.object(stage, 'agent_factory') as mock_factory:
                mock_agent = MagicMock()
                mock_agent.callbacks = {}
                
                def mock_send(*args, **kwargs):
                    if "on_complete" in mock_agent.callbacks:
                        mock_agent.callbacks["on_complete"]()
                mock_agent.send.side_effect = mock_send
                
                mock_factory.return_value = mock_agent
                with patch.object(stage, '_collect_agent_output', return_value="Done"):
                    output = stage.invoke(StageInput(task_name=task_name, stage="01-brainstorming", stage_idx=0))
                    assert output.gate_passed is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)
