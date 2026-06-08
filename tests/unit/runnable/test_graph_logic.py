import pytest
from unittest.mock import MagicMock
from sw_lib.runnable import StageRunnable, StageInput, StageOutput, LangGraphAdapter
from sw_lib.runnable.graph import build_harness_graph

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

    def test_linear_progression(self, dummy_task):
        """01 -> 02 -> 03 -> 04 -> 05 线性推进"""
        stages = [self._make_mock_stage(name, i) for i, name in enumerate([
            "01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"
        ])]
        
        graph = build_harness_graph(stages)
        adapter = LangGraphAdapter(graph, stages)
        
        inp = StageInput(task_name=dummy_task, stage="01-brainstorming", stage_idx=0)
        res = adapter.invoke(inp)
        
        assert res.stage == "05-archive"
        for s in stages:
            assert s.invoke.call_count == 1

    def test_review_reroute_to_coding(self, dummy_task):
        """04-review -> 03-coding -> 04-review -> 05-archive"""
        stages = [self._make_mock_stage(name, i) for i, name in enumerate([
            "01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"
        ])]
        
        # 模拟第一次 review 决定返工到 coding
        stages[3].invoke.side_effect = [
            StageOutput(task_name="t", stage="04-review", raw_agent_output="r1",
                         parsed={}, gate_passed=True, route="03-coding"),
            StageOutput(task_name="t", stage="04-review", raw_agent_output="r2",
                         parsed={}, gate_passed=True, route="05-archive"),
        ]
        
        graph = build_harness_graph(stages)
        adapter = LangGraphAdapter(graph, stages)
        
        inp = StageInput(task_name=dummy_task, stage="01-brainstorming", stage_idx=0)
        res = adapter.invoke(inp)
        
        assert res.stage == "05-archive"
        assert stages[2].invoke.call_count == 2 # coding 执行了两次
        assert stages[3].invoke.call_count == 2 # review 执行了两次

    def test_max_reroute_limit(self, dummy_task):
        """超过 MAX_REROUTE 后强行推进到 archive"""
        from sw_lib.core.config import MAX_REROUTE
        stages = [self._make_mock_stage(name, i) for i, name in enumerate([
            "01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"
        ])]
        
        # 模拟始终决定返工
        stages[3].invoke.return_value = StageOutput(
            task_name="t", stage="04-review", raw_agent_output="r",
            parsed={}, gate_passed=True, route="03-coding"
        )
        
        # 将 reroute_count 设为即将达到上限
        inp = StageInput(
            task_name=dummy_task, stage="04-review", stage_idx=3,
            metadata={"reroute_count": MAX_REROUTE}
        )
        
        graph = build_harness_graph(stages)
        adapter = LangGraphAdapter(graph, stages)
        res = adapter.invoke(inp)
        
        # 应该被 review_router 强行转到 archive
        assert res.stage == "05-archive"

    def test_gate_failure_pauses_execution(self, dummy_task):
        """门禁失败时应停止执行（END）"""
        stages = [self._make_mock_stage(name, i) for i, name in enumerate([
            "01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"
        ])]
        
        # 模拟 02 阶段门禁不通过
        stages[1].invoke.return_value = StageOutput(
            task_name="t", stage="02-planning", raw_agent_output="fail",
            parsed={}, gate_passed=False
        )
        
        graph = build_harness_graph(stages)
        adapter = LangGraphAdapter(graph, stages)
        
        inp = StageInput(task_name=dummy_task, stage="01-brainstorming", stage_idx=0)
        res = adapter.invoke(inp)
        
        # 执行应停在 02-planning
        assert res.stage == "02-planning"
        assert res.gate_passed is False
        assert stages[2].invoke.call_count == 0 # 03 没被执行
