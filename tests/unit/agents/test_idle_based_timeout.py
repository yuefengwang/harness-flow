"""超时应当衡量「多久没动静」，而不是「一共用了多久」。

`CHAT_TIMEOUT` 是**墙上时钟**上限：单轮 POST /message 超过它就断，
不管 agent 当时在不在干活。这个形状已经造成两次同类事故：

* 任务 `ttt`（已记录在 transport.CHAT_TIMEOUT 的注释里）：300s 砍掉了
  step 0..8 稳步推进中的 agent，距上次活动仅 34 秒。处置是 300 → 900。
* 任务 `helloworld`（本轮）：900s 又砍了一次。实测时间线 ——

      +895s  🔧 todowrite    ← 最后一次工具调用
      +900s  ❌ CHAT_TIMEOUT 断开

  全程 39 次工具调用，**最大间隔 92 秒**，被砍时距上次活动 5 秒。
  它在写第 5 个测试文件，6 个规划任务完成了 4 个。

两次都是「正在正常干活被砍」，两次的处置都是把常数调大一倍。这条路走不通：
真实编码阶段的时长取决于任务规模，没有一个常数能同时满足
「大任务别误杀」与「死锁别静默太久」。

`test_timeout_hierarchy.py` 的第一条判据已经就此留下教训 ——
它原本写 `== 300.0`，把一个待标定的工程取值锁成硬相等，于是
「锁住了一个错误的值，还让错误显得已被测试覆盖」。所以本文件**不锁数值**，
锁的是机制：**超时判据必须包含空闲维度**。

有了空闲判据，两种情形才第一次可区分：
  - 死锁：0 次工具调用、长时间无增长 → 该断，且能很快断。
  - 干活：工具调用持续出现 → 不该断，无论累计多久。
"""

import time

import pytest

from sw_lib.agents.transport import OpenCodeTransport


def test_transport_exposes_an_idle_timeout():
    """必须存在「空闲上限」这个独立概念。

    只有墙上时钟上限时，harness 无法表达「它还在动，再等等」。
    """
    assert hasattr(OpenCodeTransport, "IDLE_TIMEOUT"), (
        "transport 没有 IDLE_TIMEOUT —— 超时只能按墙上时钟判，"
        "于是「正在干活」与「已经死锁」无法区分（任务 ttt / helloworld 两次误杀）")


def test_idle_timeout_is_shorter_than_wall_clock_timeout():
    """空闲上限必须显著短于墙上时钟上限。

    这才是它的价值：死锁能在几分钟内暴露，而不必等满整个 CHAT_TIMEOUT。
    F11 那 26 分钟的沉默正是因为只有墙上时钟这一层。
    """
    assert OpenCodeTransport.IDLE_TIMEOUT < OpenCodeTransport.CHAT_TIMEOUT, (
        f"IDLE_TIMEOUT({OpenCodeTransport.IDLE_TIMEOUT}) 必须 < "
        f"CHAT_TIMEOUT({OpenCodeTransport.CHAT_TIMEOUT})，否则它不产生任何新信息")


def test_idle_timeout_tolerates_observed_gaps():
    """空闲上限必须容得下实测的工具调用间隔。

    helloworld 实测最大间隔 92 秒（模型在跑 pytest、等 LLM 往返）。
    取值若低于它，正常干活会被判成空闲 —— 把一次误杀换成另一次误杀。
    判据取「至少是实测最大间隔的两倍」，留出模型变慢的余量。
    """
    observed_max_gap = 92.0
    assert OpenCodeTransport.IDLE_TIMEOUT >= observed_max_gap * 2, (
        f"IDLE_TIMEOUT({OpenCodeTransport.IDLE_TIMEOUT}) 不足实测最大间隔"
        f"（{observed_max_gap}s）的两倍 —— 正常干活会被误判为空闲")


def test_activity_timestamp_is_tracked():
    """必须记录「最后一次活动的时间」，否则空闲无从计算。"""
    t = OpenCodeTransport(verbose=False)
    assert hasattr(t, "last_activity_at"), \
        "没有 last_activity_at，空闲时长无法计算"
    assert isinstance(t.last_activity_at, float), \
        f"last_activity_at 应是时间戳，实为 {type(t.last_activity_at)}"


def test_activity_timestamp_advances_on_tool_observation():
    """观察到工具调用必须刷新活动时间。

    这是「它还在动」的唯一客观信号 —— 与 A6 的客观轨同一条思路：
    判据取 harness 自己观测到的事实，不取 agent 的自述。
    """
    t = OpenCodeTransport(verbose=False)
    before = t.last_activity_at
    time.sleep(0.01)
    t.note_activity("bash")
    assert t.last_activity_at > before, \
        "note_activity 没有推进活动时间戳"


def test_idle_seconds_reflects_elapsed_since_activity():
    """`idle_seconds` 必须反映距上次活动的时长，而非会话总时长。"""
    t = OpenCodeTransport(verbose=False)
    t.note_activity("bash")
    time.sleep(0.05)
    idle = t.idle_seconds()
    assert 0.0 <= idle < 5.0, f"idle_seconds 异常: {idle}"

    # 把活动时间往前拨，模拟长时间无动静
    t._last_activity_at -= 100.0
    assert t.idle_seconds() >= 100.0, \
        f"活动时间前移后 idle_seconds 未随之增长: {t.idle_seconds()}"


def test_is_idle_distinguishes_working_from_stuck():
    """`is_idle()` 必须能区分「在干活」与「卡住」。

    这是整条改动的目的：两次误杀都源于 harness 无法表达这个区别。
    """
    t = OpenCodeTransport(verbose=False)
    t.note_activity("bash")
    assert not t.is_idle(), "刚有活动却被判为空闲"

    t._last_activity_at -= (OpenCodeTransport.IDLE_TIMEOUT + 1.0)
    assert t.is_idle(), "超过 IDLE_TIMEOUT 无活动却未被判为空闲"


def test_timeout_hierarchy_still_holds():
    """既有层级不变式不得被这次改动推翻。

    IDLE_TIMEOUT 是新增的一层，不是替换 —— 原有的
    QUESTION_TIMEOUT < CHAT_TIMEOUT < FIRST_RESPONSE_TIMEOUT 仍须成立。
    """
    from sw_lib.agents.opencode import OpenCodeAgent
    from sw_lib.workflow.base import StageRunnable

    assert OpenCodeAgent.QUESTION_TIMEOUT < OpenCodeTransport.CHAT_TIMEOUT
    assert StageRunnable.FIRST_RESPONSE_TIMEOUT > OpenCodeTransport.CHAT_TIMEOUT


def test_idle_timeout_leaves_question_wait_intact():
    """等真人回答不得被空闲判据误杀。

    `question` 期间 agent 本来就不动 —— 那是在等人，不是卡住。
    IDLE_TIMEOUT 必须大于 QUESTION_TIMEOUT，否则用户还在读选项，
    会话就被判空闲断掉。helloworld 里用户思考了 65 秒（10:03:29 → 10:04:34）。

    ⚠️ 这条判据保留，但它已**不是**该保证的主要来源。它靠的是数值大小
    （300 > 180），而那只是在掩盖语义混淆：等人与空闲是两件事，
    靠常数的相对大小碰巧不冲突。等人上限改为可配置、默认 30 分钟之后，
    这个障眼法必然破 —— 真正的保证是 `waiting_for_human()` 显式挂起空闲
    计时，见下一条与 `test_ask_user_timeout_config.py`。

    仍留着它的理由：`QUESTION_TIMEOUT` 现在是**兜底**取值（配置不可读时
    才用），兜底路径下这条数值关系依然应当成立。
    """
    from sw_lib.agents.opencode import OpenCodeAgent

    assert OpenCodeTransport.IDLE_TIMEOUT > OpenCodeAgent.QUESTION_TIMEOUT, (
        f"IDLE_TIMEOUT({OpenCodeTransport.IDLE_TIMEOUT}) 必须 > "
        f"QUESTION_TIMEOUT({OpenCodeAgent.QUESTION_TIMEOUT})，"
        f"否则等真人回答期间会被判成空闲")


def test_configured_human_wait_may_exceed_idle_timeout_safely():
    """等人上限**允许**超过空闲上限 —— 前提是那段时间不计入空闲。

    这是上一条判据的继任者。用户要求等人 30 分钟，而空闲上限是 5 分钟：
    两个数字直接冲突，靠调大 IDLE_TIMEOUT 来消解会让真死锁多沉默 25 分钟。
    正解是把等人期从空闲计时里摘出去，于是两者不再需要互相迁就。
    """
    from sw_lib.core.config import ask_user_timeout

    t = OpenCodeTransport(verbose=False)
    # 前提：配置的等人上限确实超过了空闲上限，否则这条判据在空转
    assert ask_user_timeout() > OpenCodeTransport.IDLE_TIMEOUT, (
        "前提不成立：等人上限未超过空闲上限，本条判据测不到冲突")

    t._last_activity_at -= (ask_user_timeout() + 60)
    with t.waiting_for_human("等用户回答"):
        assert not t.is_idle(), (
            "等人 30 分钟被判成空闲 —— 空闲计时没有被挂起，"
            "两个语义仍挤在同一个判据里")
