from unittest.mock import MagicMock
from sw_lib.workflow import StageRunnable, StageInput, StageOutput, LangGraphAdapter
from sw_lib.workflow.graph import build_harness_graph

class TestGraphRoutingLogic:
    """验证 LangGraph 的核心路由逻辑是否符合预期（包括返工和正常推进）。"""

    def _make_mock_stage(self, stage, idx):
        s = MagicMock(spec=StageRunnable)
        s.stage = stage
        s.stage_idx = idx
        # 默认返回成功且不返工
        s.invoke.return_value = StageOutput(
            task_name="t", stage=stage, raw_agent_output="ok",
            parsed={}, gate_passed=True, route=None
        )
        return s

    def test_one_stage_per_invoke(self, dummy_task):
        """Each invoke() runs exactly one stage (TUI handles transitions)."""
        stages = [self._make_mock_stage(name, i) for i, name in enumerate([
            "01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"
        ])]
        
        graph = build_harness_graph(stages)
        adapter = LangGraphAdapter(graph, stages)
        
        # Invoke stage 1 — runs only 01-brainstorming
        inp = StageInput(task_name=dummy_task, stage="01-brainstorming", stage_idx=0)
        res = adapter.invoke(inp)
        assert res.stage == "01-brainstorming"
        assert stages[0].invoke.call_count == 1
        assert stages[1].invoke.call_count == 0  # Stage 2 not touched

        # Invoke stage 2 — runs only 02-planning
        inp = StageInput(task_name=dummy_task, stage="02-planning", stage_idx=1)
        res = adapter.invoke(inp)
        assert res.stage == "02-planning"
        assert stages[0].invoke.call_count == 1
        assert stages[1].invoke.call_count == 1
        assert stages[2].invoke.call_count == 0  # Stage 3 not touched

    def test_rerouting_handled_by_tui(self, dummy_task):
        """Graph no longer handles rerouting — TUI does it via advance_stage()."""
        stages = [self._make_mock_stage(name, i) for i, name in enumerate([
            "01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"
        ])]
        
        graph = build_harness_graph(stages)
        adapter = LangGraphAdapter(graph, stages)
        
        # Invoke stage 4 (review) — runs only 04-review, no auto-reroute
        inp = StageInput(task_name=dummy_task, stage="04-review", stage_idx=3)
        res = adapter.invoke(inp)
        assert res.stage == "04-review"
        assert stages[2].invoke.call_count == 0  # 03-coding not auto-run

    def test_gate_passed_returned_in_output(self, dummy_task):
        """gate_passed is returned in StageOutput; TUI uses it for advancement."""
        stages = [self._make_mock_stage(name, i) for i, name in enumerate([
            "01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"
        ])]
        
        # Stage 2 gate fails
        stages[1].invoke.return_value = StageOutput(
            task_name="t", stage="02-planning", raw_agent_output="fail",
            parsed={}, gate_passed=False
        )
        
        graph = build_harness_graph(stages)
        adapter = LangGraphAdapter(graph, stages)
        
        # Invoke stage 2 — runs it, returns gate_passed=False
        inp = StageInput(task_name=dummy_task, stage="02-planning", stage_idx=1)
        res = adapter.invoke(inp)
        assert res.stage == "02-planning"
        assert res.gate_passed is False
        assert stages[2].invoke.call_count == 0  # Stage 3 not touched


class TestLangGraphAdapterAnswer:
    """Tests for LangGraphAdapter.answer() — user message routing."""

    def test_answer_warns_when_no_active_agent(self, monkeypatch):
        """answer() must write a sw_log warning when no agent is active,
        not silently drop user input."""
        from sw_lib.core.utils import sw_log
        import sw_lib.core.utils
        logs = []
        monkeypatch.setattr(sw_lib.core.utils, 'sw_log', lambda s, m, lvl: logs.append((s, m)))

        adapter = LangGraphAdapter.__new__(LangGraphAdapter)
        adapter.active_stage = None

        adapter.answer("hello")
        assert len(logs) > 0, "answer() must log a warning when no agent is active"

    def test_answer_forwards_to_active_agent(self):
        """answer() must forward text to the active agent's send()."""
        adapter = LangGraphAdapter.__new__(LangGraphAdapter)
        mock_agent = MagicMock()
        mock_agent.name = "test-agent"
        mock_stage = MagicMock()
        mock_stage.active_agent = mock_agent
        adapter.active_stage = mock_stage

        adapter.answer("hello")
        mock_agent.send.assert_called_once_with("hello")
