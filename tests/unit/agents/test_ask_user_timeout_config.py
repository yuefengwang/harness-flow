"""等真人回答的上限必须可配置，且默认给足思考时间。

## 用户现场

> 「sw init 启动的任务，如果出现 ask user 的场景，把超时时间设置成 30 分钟吧，
> 这个应当是一个参数，我常常会看不到。」

`QUESTION_TIMEOUT` 原为 **180s**。用户离开工位、切到别的窗口、或者根本没注意
到 TUI 弹了问题，3 分钟一到 harness 就 `reject_question()` 让 agent
「自行决定」—— 而 01-brainstorming 整个阶段的意义就是**消除歧义**。
agent 自行决定等于把用户的决策换成它的猜测，这比慢更糟。

## 为什么不能只改一个数字

等人回答处在一条超时链的最内层，每层都靠「比外层早醒」换取一次主动处置：

    StageRunnable.FIRST_RESPONSE_TIMEOUT   等 agent 首轮回复
      └── OpenCodeTransport.CHAT_TIMEOUT   单轮 POST /message（真实 HTTP 超时）
            ├── IDLE_TIMEOUT               空闲判据（多久没动静算卡住）
            └── QUESTION_TIMEOUT           等真人回答

把 `QUESTION_TIMEOUT` 单独抬到 1800s 是**假的**：HTTP 请求会在
`CHAT_TIMEOUT`(900s) 先断，`reject_question` 那段补救逻辑退化成死代码，
用户在第 15 分钟回答，回答已经无处可投。这正是
`test_timeout_hierarchy.py` 通篇在守的不变式。

## 关键判断：等人期间不该被空闲判据计时

`IDLE_TIMEOUT` 问的是「agent 是不是卡住了」，判据是有没有新的工具调用。
而 agent 等人回答时**本来就零活动** —— 那是在等人，不是卡住。
transport 的注释里早已写下这个顾虑（IDLE_TIMEOUT 的「下界 B」），
当时的处置是让 `IDLE_TIMEOUT`(300s) > `QUESTION_TIMEOUT`(180s) ——
靠数值大小掩盖语义混淆。等人上限一旦抬到 30 分钟，这个障眼法就破了。

所以正确的修法是把等人期从空闲计时里**摘出去**，而不是把 `IDLE_TIMEOUT`
一路抬到 30 分钟以上 —— 后者会让真正的死锁多沉默 25 分钟，
把用户的一个便利换成故障暴露能力的整体退化。
"""

import pytest

from sw_lib.agents.opencode import OpenCodeAgent
from sw_lib.agents.transport import OpenCodeTransport
from sw_lib.core import config as cfg
from sw_lib.workflow.base import StageRunnable

#: 用户明确要求的默认值（分钟）。
_WANTED_MINUTES = 30


# ── 参数化：默认值与可配置性 ──

def test_ask_user_timeout_is_configurable():
    """必须存在一个可配置项，而不是散在代码里的字面量。

    用户的原话是「这个应当是一个参数」—— 每次调都要改源码的常量不算参数。
    """
    assert hasattr(cfg, "ask_user_timeout"), (
        "config 没有暴露 ask_user_timeout —— 等人上限仍是硬编码常量")


def test_default_ask_user_timeout_is_thirty_minutes():
    """默认 30 分钟 —— 用户明确要求的值。"""
    assert cfg.ask_user_timeout() == pytest.approx(_WANTED_MINUTES * 60), (
        f"默认等人上限为 {cfg.ask_user_timeout()}s，"
        f"用户要求 {_WANTED_MINUTES} 分钟（{_WANTED_MINUTES * 60}s）")


def test_agent_uses_the_configured_value(monkeypatch):
    """agent 真的读这个配置，而不是自己那份常量。

    「配置项存在但无人消费」是本项目反复出现的形状（A0 的 2.9.8 记了六例）。
    改配置后 agent 的等待上限必须跟着变，否则参数是装饰。
    """
    monkeypatch.setattr(cfg, "ask_user_timeout", lambda: 111.0)
    monkeypatch.setattr("sw_lib.agents.opencode.ask_user_timeout",
                        lambda: 111.0, raising=False)

    agent = OpenCodeAgent.__new__(OpenCodeAgent)
    assert agent.question_timeout == pytest.approx(111.0), (
        f"agent 的等人上限是 {agent.question_timeout}，没跟着配置走")


def test_invalid_config_falls_back_to_default(monkeypatch):
    """配置写坏了要回落默认值，不能崩、也不能变成 0。

    0 意味着「问都不问就 reject」—— 一个手抖写错的 YAML 不该让
    整个澄清机制静默失效。
    """
    for bad in ("", "abc", None, -5, 0):
        monkeypatch.setattr(cfg._manager.config, "ask_user_timeout", bad,
                            raising=False)
        got = cfg.ask_user_timeout()
        assert got > 0, f"配置为 {bad!r} 时得到 {got}，等人上限不得为非正数"


def test_instance_override_still_wins():
    """实例上显式设的 `QUESTION_TIMEOUT` 必须仍然生效。

    【新增判据 · 显式声明（DEV-PROTOCOL 1.2）】本条在测试文件冻结之后补入，
    原因是转绿过程中撞上了一个**真实缺陷**，而非为了让实现变绿而放宽判据：

    `agent.QUESTION_TIMEOUT = 0.05` 是测试压缩等待的标准手法
    （`test_opencode.py::test_question_timeout_rejects_instead_of_hanging`
    就这么写）。把等人上限改成无条件读配置之后，那种覆写**静默失效** ——
    那条本该秒级完成的测试真的开始等 30 分钟，`tests/unit` 从 5 分钟涨到
    15 分钟以上仍未结束。

    这正是「参数化」最容易引入的伤害：一个看起来只是「换了取值来源」的改动，
    悄悄取消了所有既有覆写点。所以逃逸口必须存在，且必须被判据钉住 ——
    否则下一次重构会再把它弄丢，而症状是测试变慢而不是变红，极难归因。
    """
    agent = OpenCodeAgent.__new__(OpenCodeAgent)
    agent.QUESTION_TIMEOUT = 0.05

    assert agent.question_timeout == pytest.approx(0.05), (
        f"实例覆写被忽略（得到 {agent.question_timeout}）—— "
        f"测试里压缩等待的手法会失效，症状是套件变慢而不是变红")


# ── 超时层级：抬高等人上限不得让外层先断 ──

def test_chat_timeout_can_accommodate_waiting_for_a_human():
    """单轮 HTTP 上限必须容得下等人 + 处置余量。

    否则用户在第 20 分钟认真作答，POST /message 早已断开 ——
    回答无处可投，而 UI 只会显示一个没有原因的失败。
    """
    need = cfg.ask_user_timeout() + 60.0
    assert OpenCodeTransport.CHAT_TIMEOUT >= need, (
        f"CHAT_TIMEOUT({OpenCodeTransport.CHAT_TIMEOUT}) 容不下等人 "
        f"{cfg.ask_user_timeout()}s + 60s 处置余量 —— "
        f"reject_question 分支会变成死代码")


def test_stage_timeouts_still_outrank_chat_timeout():
    """抬高 CHAT_TIMEOUT 之后，阶段层仍必须比它晚醒。

    这是 `test_timeout_hierarchy.py` 的既有不变式，本轮最容易踩坏它：
    阶段层先放弃，transport 那句可读的报错就永远到不了用户面前。
    """
    assert StageRunnable.FIRST_RESPONSE_TIMEOUT > OpenCodeTransport.CHAT_TIMEOUT, (
        f"FIRST_RESPONSE_TIMEOUT({StageRunnable.FIRST_RESPONSE_TIMEOUT}) 必须 > "
        f"CHAT_TIMEOUT({OpenCodeTransport.CHAT_TIMEOUT})")
    assert StageRunnable.MULTI_TURN_TIMEOUT > StageRunnable.FIRST_RESPONSE_TIMEOUT, (
        f"MULTI_TURN_TIMEOUT({StageRunnable.MULTI_TURN_TIMEOUT}) 必须 > "
        f"FIRST_RESPONSE_TIMEOUT({StageRunnable.FIRST_RESPONSE_TIMEOUT})")


# ── 等人期间不算空闲 ──

def test_transport_can_suspend_idle_accounting():
    """必须能显式声明「现在是在等人，别算空闲」。

    没有这个开关，等人上限一抬到 30 分钟就与 IDLE_TIMEOUT 冲突：
    要么把 IDLE_TIMEOUT 也抬到 30 分钟以上（真死锁多沉默 25 分钟），
    要么等人途中被判成卡住。两条都是拿故障暴露能力换便利。
    """
    t = OpenCodeTransport(verbose=False)
    assert hasattr(t, "waiting_for_human"), (
        "transport 无法表达「正在等人」—— 空闲判据会把等人误判成卡住")


def test_idle_does_not_accumulate_while_waiting_for_human(monkeypatch):
    """等人期间 `is_idle()` 必须恒为 False，无论过了多久。

    用户想 25 分钟是他的权利；agent 零活动是这个状态的**预期**表现，
    不是故障征兆。
    """
    t = OpenCodeTransport(verbose=False)
    # 把上次活动推到远古：不做特殊处理的话这必然被判成空闲
    t._last_activity_at -= (OpenCodeTransport.IDLE_TIMEOUT + 3600)

    assert t.is_idle(), "前提不成立：这个时间差本应被判为空闲"

    with t.waiting_for_human("等用户回答 3 个问题"):
        assert not t.is_idle(), (
            "等人期间被判成空闲 —— 空闲判据把「在等人」当成了「卡住」")


def test_idle_accounting_resumes_after_the_human_answers():
    """人答完之后，空闲判据必须重新生效。

    挂起若不可逆，`IDLE_TIMEOUT` 这一层从此失效 ——
    F11 那 26 分钟的静默会以另一种形式回来。
    """
    t = OpenCodeTransport(verbose=False)
    with t.waiting_for_human():
        pass

    t._last_activity_at -= (OpenCodeTransport.IDLE_TIMEOUT + 3600)
    assert t.is_idle(), "等人结束后空闲判据没有恢复 —— 死锁将不再可检出"


def test_answering_counts_as_activity():
    """等人结束时要刷新活动时间戳。

    否则用户答完的那一刻，距「上次活动」已经是 30 分钟前，
    紧接着的第一次空闲判定会立刻把一个刚被唤醒的会话判成卡住。
    """
    t = OpenCodeTransport(verbose=False)
    t._last_activity_at -= (OpenCodeTransport.IDLE_TIMEOUT + 3600)

    with t.waiting_for_human():
        pass

    assert t.idle_seconds() < 5.0, (
        f"等人结束后 idle_seconds()={t.idle_seconds():.0f} —— "
        f"活动时间戳没被刷新，下一次判定会误杀")


def test_nested_waits_do_not_resume_early():
    """嵌套等待（多个问题串行）不得让内层提前恢复计时。

    agent 连问三轮时会有多次进出。若用布尔标志而非计数，
    内层退出就把外层的挂起一起解除了。
    """
    t = OpenCodeTransport(verbose=False)
    t._last_activity_at -= (OpenCodeTransport.IDLE_TIMEOUT + 3600)

    with t.waiting_for_human():
        with t.waiting_for_human():
            assert not t.is_idle()
        assert not t.is_idle(), (
            "内层等待退出后外层的挂起被一起解除 —— 用了布尔而非计数")


def test_exception_inside_wait_still_restores_accounting():
    """等人过程中抛异常，空闲判据也必须恢复。

    `reject_question` 那条路径本身就可能失败；挂起状态泄漏会让
    这个会话此后永远不被判为卡住。
    """
    t = OpenCodeTransport(verbose=False)
    with pytest.raises(RuntimeError):
        with t.waiting_for_human():
            raise RuntimeError("boom")

    t._last_activity_at -= (OpenCodeTransport.IDLE_TIMEOUT + 3600)
    assert t.is_idle(), "异常路径泄漏了挂起状态 —— 空闲判据永久失效"


# ── 等人期间的超时诊断不得误报 ──

def test_timeout_diagnosis_distinguishes_waiting_from_stuck():
    """真的超时了，报错要说清「在等人」而不是「疑似卡住」。

    `send_message` 的超时诊断按 `idle_seconds()` 分岔。等人期间那个值被
    冻结，若文案仍照旧只有「卡住 / 在推进」两种，用户会收到一句
    与事实相反的诊断 —— A6 的第 3 条：不猜，说不知道也比猜错好。
    """
    t = OpenCodeTransport(verbose=False)
    with t.waiting_for_human("等用户回答"):
        assert t.waiting_reason, (
            "等人期间没有可用于诊断的说明 —— 超时文案只能在"
            "「卡住」与「在推进」之间二选一，两者都不对")
