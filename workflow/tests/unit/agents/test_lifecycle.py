import pytest
import os
import threading
import time
from sw_lib.agents.pty import PtyAgent

def test_start_agent_cat(dummy_task, agent_callbacks):
    agent = PtyAgent(agent_callbacks, dummy_task, "01-brainstorming", 0, "cat")
    try:
        agent.start()
        assert agent.agent_proc is not None
        assert agent._master_fd is not None
    finally:
        agent.shutdown()

def test_send_and_receive(dummy_task, agent_callbacks):
    agent = PtyAgent(agent_callbacks, dummy_task, "01-brainstorming", 0, "cat")
    logs = []
    # 注入会捕获日志的回调
    agent.callbacks["add_log"] = lambda s, m: logs.append((s, m))
    
    try:
        agent.start()
        # 启动读取线程
        threading.Thread(target=agent.reader_loop, daemon=True).start()
        
        agent.send("hello cat\n")
        # 给点时间让 reader 处理
        time.sleep(0.5)
        
        agent_msgs = [m for s, m in logs if s == "agent"]
        assert any("hello cat" in m for m in agent_msgs)
    finally:
        agent.shutdown()

def test_restart_agent(dummy_task, agent_callbacks):
    agent = PtyAgent(agent_callbacks, dummy_task, "01-brainstorming", 0, "cat")
    try:
        agent.start()
        pid1 = agent.agent_proc.pid
        agent.restart()
        pid2 = agent.agent_proc.pid
        assert pid1 != pid2
    finally:
        agent.shutdown()
