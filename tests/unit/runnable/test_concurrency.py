import threading
import time
import pytest
from unittest.mock import MagicMock
from sw_lib.runnable import StageRunnable, StageInput, LangGraphAdapter
from sw_lib.runnable.graph import build_harness_graph

def test_adapter_concurrency_locking(dummy_task):
    """验证 LangGraphAdapter 对同一个任务的并发调用会排队执行。"""
    
    execution_order = []
    
    def slow_stage_invoke(stage_input):
        execution_order.append(f"start_{stage_input.stage}")
        time.sleep(1) # 模拟耗时操作
        execution_order.append(f"end_{stage_input.stage}")
        from sw_lib.runnable import StageOutput
        return StageOutput(
            task_name=stage_input.task_name,
            stage=stage_input.stage,
            raw_agent_output="done",
            gate_passed=True
        )

    # 1. 创建两个 Mock 阶段
    s1 = MagicMock(spec=StageRunnable)
    s1.stage = "01-brainstorming"
    s1.stage_idx = 0
    s1.invoke.side_effect = slow_stage_invoke
    
    # 模拟其他阶段，减少干扰
    other_stages = []
    for i, name in enumerate(["02-planning", "03-coding", "04-review", "05-archive"], 1):
        s = MagicMock(spec=StageRunnable)
        s.stage = name
        s.stage_idx = i
        s.invoke.return_value = MagicMock(stage=name, gate_passed=True, route=None, parsed={})
        other_stages.append(s)
        
    stages = [s1] + other_stages
    graph = build_harness_graph(stages)
    adapter = LangGraphAdapter(graph, stages)
    
    # 2. 同时启动两个 invoke
    def run_invoke(stage_name):
        input = StageInput(task_name=dummy_task, stage=stage_name, stage_idx=0)
        adapter.invoke(input)

    t1 = threading.Thread(target=run_invoke, args=("01-brainstorming",))
    t2 = threading.Thread(target=run_invoke, args=("01-brainstorming",))
    
    t1.start()
    time.sleep(0.1) # 确保 t1 先拿到锁
    t2.start()
    
    t1.join()
    t2.join()
    
    # 3. 验证执行顺序：应该是串行的 (start1, end1, start2, end2)
    # 而非并发的 (start1, start2, end1, end2)
    expected_serial = ["start_01-brainstorming", "end_01-brainstorming", 
                       "start_01-brainstorming", "end_01-brainstorming"]
    
    # 注意：LangGraph 可能因为 entry_router 自动跳转到后续阶段
    # 我们只关注 01-brainstorming 的执行日志
    filtered_order = [x for x in execution_order if "01-brainstorming" in x]
    assert filtered_order == expected_serial
