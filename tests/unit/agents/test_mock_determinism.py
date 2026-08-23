"""MockAgent 的确定性保证 —— e2e 的可复现性全靠这几条。

e2e 已经砍掉真实 agent 模式（driver 只跑 `sw init --mock`），前提是 MockAgent
的行为完全由测试决定。这里锁住三件事：

1. **场景收尾标记**：每个阶段恰好一条 `SCENARIO_DONE_MARKER`。driver 靠数它
   判断「这一轮说完了」。原先是猜「日志静默 3 秒」，会在脚本自带的 sleep(2)
   里误判，三轮挂一轮。标记没了不会有人立刻发现 —— 症状是 e2e 超时，看起来
   像流程 bug，所以必须由测试守着。
2. **评审路由可由环境变量固定**：driver 用 fork 子进程跑 sw init，改不到父
   进程的 _manager，只能靠环境变量。config.yaml 里那份 review_route 现在是
   02-Planning，继承它 e2e 会走返工分支。
3. **节奏可归零**：拟真打字速度对流程回归没有价值，只是让每轮多花几十秒。
"""
import queue
import time

import pytest

from sw_lib.agents.mock import SCENARIO_DONE_MARKER, MockAgent


def _make_agent(stage: str, stage_idx: int, logs: list, **kw) -> MockAgent:
    callbacks = {
        "add_log": lambda src, msg: logs.append((src, msg)),
        "is_running": lambda: True,
    }
    callbacks.update(kw)
    return MockAgent(callbacks, "pytest-mock-determinism", stage, stage_idx)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """所有用例都跑 0 延时，免得测试自己变成慢的那一环。"""
    monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")


class TestScenarioDoneMarker:
    def test_marker_written_once_per_stage(self, monkeypatch):
        """每跑完一个场景恰好写一条标记 —— driver 按次数等第 N 条。"""
        written = []
        monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                            lambda name, msg, src="sw": written.append(msg))

        agent = _make_agent("02-planning", 1, [])
        agent.running = True
        agent._run_scenario()

        assert written.count(SCENARIO_DONE_MARKER) == 1

    def test_marker_is_the_last_thing_written(self, monkeypatch):
        """标记必须最后写：driver 见到它就按 A，此时脚本不能还在输出。"""
        logs = []
        agent = _make_agent("02-planning", 1, logs)
        agent.running = True

        # 记录标记落地时 agent 已经输出了多少行，跑完再比总行数。
        logs_at_marker = []

        def spy(name, msg, src="sw"):
            if msg == SCENARIO_DONE_MARKER:
                logs_at_marker.append(len(logs))

        monkeypatch.setattr("sw_lib.agents.mock.sw_log", spy)
        agent._run_scenario()

        assert logs_at_marker, "标记没写出来"
        assert logs_at_marker[0] == len(logs), \
            "标记之后还有 agent 输出，driver 会在脚本说完前就按 A"

    def test_marker_not_sent_to_tui_log_lines(self, monkeypatch):
        """标记只进 .log，不进 TUI 的 log_lines。

        走 _add_log 会让它成为「最新的 agent 消息块」，干扰 extract_options
        的选项探测。
        """
        monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                            lambda name, msg, src="sw": None)
        logs = []
        agent = _make_agent("02-planning", 1, logs)
        agent.running = True
        agent._run_scenario()

        assert all(SCENARIO_DONE_MARKER not in msg for _, msg in logs)

    def test_no_marker_when_shutdown_midway(self, monkeypatch):
        """被 shutdown 打断时不写标记 —— 那一轮并没有正常说完。"""
        written = []
        monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                            lambda name, msg, src="sw": written.append(msg))
        agent = _make_agent("02-planning", 1, [])
        agent.running = False  # 模拟已被关闭

        agent._run_scenario()

        assert SCENARIO_DONE_MARKER not in written


class TestReviewRouteOverride:
    def _run_review(self, logs) -> str:
        agent = _make_agent("04-review", 3, logs)
        agent.running = True
        agent._scenario_review()
        return "\n".join(msg for _, msg in logs)

    def test_env_var_pins_route(self, monkeypatch):
        monkeypatch.setenv("SW_MOCK_REVIEW_ROUTE", "05-Archive")
        logs = []
        out = self._run_review(logs)
        assert "建议路由: 05-Archive" in out

    def test_env_var_beats_config(self, monkeypatch):
        """环境变量必须压过 config.yaml —— 否则编辑配置会改变测试含义。"""
        from sw_lib.core.config import _manager
        monkeypatch.setattr(_manager.config.mock_agent, "review_route",
                            "02-Planning", raising=False)
        monkeypatch.setenv("SW_MOCK_REVIEW_ROUTE", "05-Archive")

        logs = []
        out = self._run_review(logs)

        assert "建议路由: 05-Archive" in out, "config.yaml 压过了环境变量"

    def test_reroute_route_still_works(self, monkeypatch):
        """返工分支也要能被固定 —— 返工链路的 e2e 需要它。"""
        monkeypatch.setenv("SW_MOCK_REVIEW_ROUTE", "03-Coding")
        logs = []
        out = self._run_review(logs)
        assert "建议路由: 03-Coding" in out
        assert "Reroute Evidence" in out


class TestPacing:
    def test_zero_delay_makes_pause_free(self, monkeypatch):
        monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
        agent = _make_agent("02-planning", 1, [])

        t0 = time.monotonic()
        agent._pause(2)
        assert time.monotonic() - t0 < 0.1, "delay=0 时仍在真的 sleep"

    def test_env_var_overrides_config_delay(self, monkeypatch):
        monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
        agent = _make_agent("02-planning", 1, [])
        agent._config["response_delay"] = 5.0

        assert agent.response_delay == 0.0

    def test_scenario_runs_fast_at_zero_delay(self, monkeypatch):
        """整个场景在零延时下应当近乎瞬时 —— 这是 e2e 能跑快的前提。"""
        monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                            lambda name, msg, src="sw": None)
        monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
        agent = _make_agent("02-planning", 1, [])
        agent.running = True

        t0 = time.monotonic()
        agent._run_scenario()
        assert time.monotonic() - t0 < 0.5

    def test_invalid_delay_falls_back(self, monkeypatch):
        """脏值不能让 agent 崩 —— 回落到配置值。"""
        monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "not-a-number")
        agent = _make_agent("02-planning", 1, [])
        agent._config["response_delay"] = 1.5

        assert agent.response_delay == 1.5


class TestAskStillReachesUser:
    def test_questions_are_not_auto_answered(self, monkeypatch):
        """确定性不等于跳过交互：提问仍要走 on_ask_user 回调。

        e2e 的价值之一就是覆盖「用户回答提问」这条路，如果 mock 自己代答了，
        那一段就没被测到。
        """
        monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                            lambda name, msg, src="sw": None)
        asked = []

        def on_ask_user(questions, res_queue: queue.Queue):
            asked.append(questions)
            res_queue.put(["B. 暂时不需要，仅限中文"])

        logs = []
        agent = _make_agent("01-brainstorming", 0, logs,
                            on_ask_user=on_ask_user)
        agent.running = True
        agent._run_scenario()

        assert len(asked) == 2, f"应当提问 2 轮，实际 {len(asked)}"
