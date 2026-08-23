"""阶段在 agent 启动失败时必须立即收尾，而不是白等超时。

回归背景：opencode 因 CLI 参数错误启动失败时，StageRunnable 仍会继续
`agent.send()` 并等满 300s + 600s。TUI 在这整段时间里毫无反馈 —— 表现就是
"启动阶段 01-brainstorming" 之后彻底没动静。
"""
import time
from unittest.mock import MagicMock


from sw_lib.workflow.base import StageInput, StageRunnable


def _stage(agent, **kw):
    return StageRunnable(
        stage="01-brainstorming", stage_idx=0,
        context_builder=MagicMock(), output_parser=None,
        gate_validator=MagicMock(),
        agent_factory=lambda stage=None, task_name=None: agent,
        **kw,
    )


def _agent(status_after_start="idle", fire_complete=True):
    a = MagicMock()
    a.callbacks = {}
    a.status = "idle"

    def start():
        a.status = status_after_start
    a.start.side_effect = start

    def send(*args, **kwargs):
        if fire_complete and "on_complete" in a.callbacks:
            a.callbacks["on_complete"]()
    a.send.side_effect = send
    return a


def _input():
    return StageInput(task_name="probe", stage="01-brainstorming", stage_idx=0,
                      metadata={"callbacks": {"add_log": lambda s, m: None}})


def test_failed_start_returns_immediately():
    agent = _agent(status_after_start="error")
    sr = _stage(agent)
    sr.MULTI_TURN_TIMEOUT = 30.0
    sr.FIRST_RESPONSE_TIMEOUT = 30.0

    t0 = time.time()
    sr._run_agent("ctx", _input())
    elapsed = time.time() - t0

    assert elapsed < 2.0, f"启动失败却等了 {elapsed:.1f}s"
    agent.send.assert_not_called()   # 启动失败就不该再发消息
    agent.shutdown.assert_called_once()
    assert sr.active_agent is None


def test_failed_start_reports_error_to_ui():
    """启动失败必须给 UI 一条 error 日志。

    这是"没动静"问题的核心：旧实现既不上报也不返回，用户只看到界面卡住。
    （不断言 _agent_finalized —— 正常路径也会置位，无法区分回归。）
    """
    agent = _agent(status_after_start="error")
    logs = []
    sr = _stage(agent)
    sr.FIRST_RESPONSE_TIMEOUT = 30.0
    sr.MULTI_TURN_TIMEOUT = 30.0
    inp = StageInput(task_name="probe", stage="01-brainstorming", stage_idx=0,
                     metadata={"callbacks": {"add_log": lambda s, m: logs.append((s, m))}})
    sr._run_agent("ctx", inp)

    assert sr._agent_finalized.is_set()
    errors = [m for s, m in logs if s == "error"]
    assert errors, "启动失败却没有向 UI 上报任何 error 日志"
    assert "启动失败" in errors[-1]


def test_send_error_without_output_returns_early():
    """send() 把 agent 打成 error 且无产出时，不必再挂满多轮超时。"""
    agent = _agent(status_after_start="idle")

    def send(*args, **kwargs):
        agent.status = "error"
        if "on_complete" in agent.callbacks:
            agent.callbacks["on_complete"]()
    agent.send.side_effect = send

    sr = _stage(agent)
    sr.FIRST_RESPONSE_TIMEOUT = 5.0
    sr.MULTI_TURN_TIMEOUT = 30.0

    t0 = time.time()
    sr._run_agent("ctx", _input())
    elapsed = time.time() - t0

    assert elapsed < 3.0, f"首轮失败却等了 {elapsed:.1f}s"
    assert sr.active_agent is None


def test_healthy_agent_still_waits_for_advance():
    """健康 agent 不受影响：仍会保持多轮会话直到超时或 /advance。"""
    agent = _agent(status_after_start="idle")
    sr = _stage(agent)
    sr.FIRST_RESPONSE_TIMEOUT = 0.3
    sr.MULTI_TURN_TIMEOUT = 0.3

    t0 = time.time()
    sr._run_agent("ctx", _input())
    elapsed = time.time() - t0

    agent.send.assert_called_once()
    assert elapsed >= 0.3, "健康 agent 应保持会话，不该立刻返回"
