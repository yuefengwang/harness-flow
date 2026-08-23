"""超时层级不变式：内层等待上限必须**严格小于**外层，且留出处置余量。

把单轮对话上限从 1800s 降到 300s 不是改一个数字。这些超时构成一条链，
每一层都靠「比外层早醒」来换取一次**主动处置**的机会：

    StageRunnable.FIRST_RESPONSE_TIMEOUT   等 agent 首轮回复
      └── OpenCodeTransport.CHAT_TIMEOUT   单轮 POST /message
            ├── OpenCodeAgent.QUESTION_TIMEOUT     等真人回答
            └── OpenCodeAgent.PERMISSION_TIMEOUT   等权限审批

内层若比外层晚醒，它的超时分支就永远执行不到 —— 该分支里的
`reject_question()` / 告警日志全部变成死代码，故障重新退化成「很慢」。
这正是 F11 那 26 分钟的形态。

本文件锁的是**关系**而非具体数值，所以将来调参不会让它失效。
"""

import pytest

from sw_lib.agents.transport import OpenCodeTransport
from sw_lib.agents.opencode import OpenCodeAgent
from sw_lib.workflow.base import StageRunnable


def test_chat_timeout_is_bounded_on_both_sides():
    """单轮对话上限必须双向有界。

    【断言更正 · 显式声明】本条原为 `== 300.0`，把一个**待标定的工程取值**
    写成了硬相等。它随即造成真实损害：任务 ttt 的 agent 在 300s 时仍在
    正常推进（step 8），被 abort 误杀，而这条测试全程绿灯 —— 它锁住了
    一个错误的值，还让错误显得「已被测试覆盖」。

    教训：超时这类取值该锁的是**关系与区间**，不是某个具体数字。
    数值下界的依据（实测工作量）记在 transport.CHAT_TIMEOUT 的注释里。
    """
    assert 900.0 <= OpenCodeTransport.CHAT_TIMEOUT < 1800.0, (
        f"CHAT_TIMEOUT={OpenCodeTransport.CHAT_TIMEOUT} 越界："
        f"下界 900s 由 ttt 实测工作量决定，上界 1800s 是 F11 里"
        f"让死锁静默 26 分钟的那个值")


def test_question_timeout_strictly_below_chat_timeout():
    """等人回答必须比整轮请求**先**超时，否则 reject 分支是死代码。

    `_on_question_asked` 在 queue.Empty 时会 `reject_question()` 让 agent
    自行决定。若 QUESTION_TIMEOUT >= CHAT_TIMEOUT，服务端先断，这段
    补救逻辑永远不会运行。
    """
    assert OpenCodeAgent.QUESTION_TIMEOUT < OpenCodeTransport.CHAT_TIMEOUT, (
        f"QUESTION_TIMEOUT({OpenCodeAgent.QUESTION_TIMEOUT}) 必须 < "
        f"CHAT_TIMEOUT({OpenCodeTransport.CHAT_TIMEOUT})，否则超时后无法主动 reject")


def test_question_timeout_leaves_room_to_act():
    """余量不能只有一瞬间 —— reject 要发 HTTP，还要让 agent 收尾回话。

    取 CHAT_TIMEOUT 的 80% 为上界：1500/1800 恰是 83%，实测偏紧；
    降到 300s 后若照抄比例只剩 50s 余量，因此这里锁的是「留够」而非比例。
    """
    margin = OpenCodeTransport.CHAT_TIMEOUT - OpenCodeAgent.QUESTION_TIMEOUT
    assert margin >= 60.0, (
        f"QUESTION_TIMEOUT 距 CHAT_TIMEOUT 仅 {margin}s —— "
        f"不足以完成 reject + agent 收尾")


def test_permission_timeout_below_question_timeout():
    """权限审批的等待应短于 question。

    question 是 agent 主动问人，值得等真人；权限 ask 是它无意踩到边界，
    harness 自动应答，没有等待真人的理由。
    """
    assert OpenCodeAgent.PERMISSION_TIMEOUT < OpenCodeAgent.QUESTION_TIMEOUT, (
        f"PERMISSION_TIMEOUT({OpenCodeAgent.PERMISSION_TIMEOUT}) 应 < "
        f"QUESTION_TIMEOUT({OpenCodeAgent.QUESTION_TIMEOUT})")


def test_stage_first_response_timeout_exceeds_chat_timeout():
    """阶段等待必须**严格大于**单轮对话上限。

    这条是降 CHAT_TIMEOUT 时最容易踩的坑：`FIRST_RESPONSE_TIMEOUT` 原本
    就是 300.0。若 CHAT_TIMEOUT 也取 300.0，两者同时到期形成竞态 ——
    StageRunnable 可能在 transport 抛出可读错误**之前**先放弃，
    于是 UI 拿到的是一个没有原因的空结果，而真正的报错被丢掉。
    """
    assert StageRunnable.FIRST_RESPONSE_TIMEOUT > OpenCodeTransport.CHAT_TIMEOUT, (
        f"FIRST_RESPONSE_TIMEOUT({StageRunnable.FIRST_RESPONSE_TIMEOUT}) 必须 > "
        f"CHAT_TIMEOUT({OpenCodeTransport.CHAT_TIMEOUT})，"
        f"否则阶段层先放弃，transport 的错误信息永远到不了用户面前")


def test_multi_turn_timeout_exceeds_first_response():
    """多轮总时长必须大于首轮等待（原有关系，防止本次调整把它弄反）。"""
    assert StageRunnable.MULTI_TURN_TIMEOUT > StageRunnable.FIRST_RESPONSE_TIMEOUT, (
        f"MULTI_TURN_TIMEOUT({StageRunnable.MULTI_TURN_TIMEOUT}) 应 > "
        f"FIRST_RESPONSE_TIMEOUT({StageRunnable.FIRST_RESPONSE_TIMEOUT})")
