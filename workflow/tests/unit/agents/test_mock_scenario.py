import pytest
import time
import queue
from sw_lib.agents.mock import MockAgent

def test_mock_agent_scenario_flow(dummy_task):
    logs = []
    questions_received = []
    complete_called = False
    
    # 定义 Mock 响应队列
    res_queue = queue.Queue()
    
    def on_ask_user(questions, q):
        questions_received.append(questions)
        # 模拟 TUI: 自动回答第一个选项
        q.put(["选项 A"])

    def on_complete():
        nonlocal complete_called
        complete_called = True

    callbacks = {
        "add_log": lambda s, m: logs.append((s, m)),
        "is_running": lambda: True,
        "on_ask_user": on_ask_user,
        "on_complete": on_complete
    }

    # 测试 Brainstorming 场景
    agent = MockAgent(callbacks, dummy_task, "01-brainstorming", 0)
    agent.start()
    
    # 给点时间让场景运行（包括提问和输出）
    # 场景中有 sleep，所以我们需要等待
    timeout = 10
    start_time = time.time()
    while time.time() - start_time < timeout:
        if complete_called:
            break
        time.sleep(0.1)
    
    assert complete_called, "MockAgent scenario should finish"
    assert len(questions_received) > 0, "Should have asked a question"
    
    # 检查日志中是否有 AI Output 和 Gate
    all_msgs = " ".join([m for s, m in logs])
    assert "## 🤖 AI Output" in all_msgs
    assert "## Gate" in all_msgs
    assert "[x] Design approved" in all_msgs
    
    agent.shutdown()

def test_mock_agent_generic_scenario(dummy_task):
    complete_called = False
    callbacks = {
        "add_log": lambda s, m: None,
        "is_running": lambda: True,
        "on_complete": lambda: setattr(pytest, "complete_called", True) # 简单 hack
    }
    
    # 修改 callbacks 以捕获 complete
    def set_complete():
        nonlocal complete_called
        complete_called = True
    callbacks["on_complete"] = set_complete

    # 测试 Coding 阶段
    agent = MockAgent(callbacks, dummy_task, "03-coding", 2)
    agent.start()
    
    timeout = 5
    start_time = time.time()
    while time.time() - start_time < timeout:
        if complete_called:
            break
        time.sleep(0.1)
    
    assert complete_called
    agent.shutdown()
