from sw_lib.agents.pty import PtyAgent

def test_start_agent_cat(dummy_task, agent_callbacks):
    agent = PtyAgent(agent_callbacks, dummy_task, "01-brainstorming", 0, "cat")
    try:
        agent.start()
        assert agent.agent_proc is not None
    finally:
        agent.shutdown()
