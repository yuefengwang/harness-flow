import pytest
import queue
from sw_lib.agents.mock import MockAgent

def test_mock_agent_empty_ask_repro(dummy_task):
    """
    尝试重现 list index out of range 错误。
    场景：如果 _ask 返回了空列表 []，则 answers[0] 会报错。
    """
    def on_ask_user(questions, res_q):
        # 模拟 Bug：直接放入空列表，或者不放入内容（超时）
        res_q.put([]) 

    callbacks = {
        "add_log": lambda s, m: None,
        "is_running": lambda: True,
        "on_ask_user": on_ask_user,
        "on_complete": lambda: None
    }

    agent = MockAgent(callbacks, dummy_task, "01-brainstorming", 0)
    agent.running = True # 开启运行状态以支持阻塞读取
    # 我们直接手动运行场景的一部分，看是否报错
    questions = [{"question": "test"}]
    
    # 修复后的行为：不应抛出 IndexError，而是返回默认值
    answers = agent._ask(questions)
    assert len(answers) == len(questions)
    assert "默认回复" in answers[0]

def test_mock_agent_invalid_input_handling(dummy_task):
    """
    测试 MockAgent 在收到非预期回复时的健壮性。
    """
    callbacks = {
        "add_log": lambda s, m: None,
        "is_running": lambda: True,
        "on_ask_user": lambda q, r: r.put(["非法回复"]), # 模拟返回了非选项内容
        "on_complete": lambda: None
    }
    
    agent = MockAgent(callbacks, dummy_task, "01-brainstorming", 0)
    # 应该能够处理至少 1 个回答，即使内容不对也不应崩溃
    # 真正的崩溃通常源于 answers 为空
    try:
        agent._scenario_brainstorming()
    except IndexError:
        pytest.fail("MockAgent crashed with IndexError on brainstorming scenario")
    except Exception:
        pass # 其他错误（如模型不存在）在此测试中不关心
