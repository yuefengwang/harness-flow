"""超时不得砍掉正在干活的 agent，也不得丢弃它已经落盘的产出。

真实事故（任务 `ttt`，03-coding，2026-08-23 11:41:44 → 11:46:44）：

```text
agent 🔧 write {'filePath': '.../repo/ttt/src/twosum/twosum.py', ...}
agent 🔧 write {'filePath': '.../repo/ttt/tests/test_twosum.py', ...}
ERR   opencode 响应超时: http://127.0.0.1:4096/session/ses_.../message
ERR   03-coding agent 未产出内容，阶段中止
```

服务端日志显示它**完全没有卡住**：step 0..8 稳步推进，11:45:57 还在下一步，
11:46:10 刚派出子 agent（`task` 工具），11:46:44 被 `CHAT_TIMEOUT` abort ——
距上一次活动仅 34 秒。整整 300.0 秒，正是上一轮把 1800 降到 300 的直接后果。

两个独立的 bug，分别对应下面两组用例：

B1  300s 标定错了。它是**故障暴露延迟**上限（F11 的教训），但同时也是
    **单轮工作时长**上限。一个 agent 串行跑 9 步、每步含一次 LLM 往返、
    还会派子 agent 串行嵌套，5 分钟是正常工作量，不是异常。

B2  更严重：agent 的产出**已经写进磁盘了**，harness 却报「未产出内容」
    并丢弃整个阶段。判据只看聊天文本缓冲区，不看真实文件系统副作用 ——
    这正是 docs/self-verification-gaps.md 命题 B 的形态：
    判据与真实产出脱节，于是「做完了」被判成「什么都没做」。
"""

import time

import pytest

from sw_lib.agents.transport import OpenCodeTransport
from sw_lib.workflow.base import StageRunnable


# ── B1：超时标定必须容纳真实工作量 ──

def test_chat_timeout_accommodates_multi_step_agent_work():
    """单轮上限必须容纳「串行多步 + 子 agent 嵌套」的真实工作量。

    事故实测：9 步中最慢的三步各约 60-90 秒（含子 agent），
    到 300 秒时它仍在正常推进。取 900s 为下界 —— 既显著大于实测工作量，
    又远小于原来那个把死锁藏了 26 分钟的 1800s。
    """
    assert OpenCodeTransport.CHAT_TIMEOUT >= 900.0, (
        f"CHAT_TIMEOUT={OpenCodeTransport.CHAT_TIMEOUT}s 会砍掉正在干活的 agent："
        f"任务 ttt 实测 300s 时仍在 step 8 正常推进")


def test_chat_timeout_still_bounded_well_below_original():
    """但不许退回 1800s —— 那是 F11 里让死锁静默 26 分钟的那个值。

    这条与上一条一起构成**双向**约束：既不能短到砍掉正常工作，
    也不能长到把故障藏起来。修 bug 不等于把改动整个回滚。
    """
    assert OpenCodeTransport.CHAT_TIMEOUT < 1800.0, (
        "CHAT_TIMEOUT 回退到 1800s 会让死锁重新静默 26 分钟（F11）")


def test_timeout_hierarchy_survives_the_fix():
    """调大 CHAT_TIMEOUT 后，阶段层仍须比它晚醒。

    上一轮已经踩过一次：FIRST_RESPONSE_TIMEOUT 若不大于 CHAT_TIMEOUT，
    阶段层先放弃，transport 那句可读的报错永远到不了用户面前。
    """
    assert StageRunnable.FIRST_RESPONSE_TIMEOUT > OpenCodeTransport.CHAT_TIMEOUT, (
        f"FIRST_RESPONSE_TIMEOUT({StageRunnable.FIRST_RESPONSE_TIMEOUT}) 必须 > "
        f"CHAT_TIMEOUT({OpenCodeTransport.CHAT_TIMEOUT})")
    assert StageRunnable.MULTI_TURN_TIMEOUT > StageRunnable.FIRST_RESPONSE_TIMEOUT


# ── B2：超时不得丢弃已落盘的产出 ──

def _runnable():
    """造一个只用于探针的 StageRunnable（不跑真实 agent）。"""
    r = object.__new__(StageRunnable)
    r.stage = "03-coding"
    r.stage_idx = 2
    r._agent_output_lines = []
    r._agent_text_buffer = []
    return r


def test_tool_side_effects_are_recorded_not_only_chat_text():
    """agent 调用 write/edit 必须被记入产出证据，而不只有聊天文本。

    事故里 agent 写了 2 个文件，`_agent_text_buffer` 却是空的（超时发生在
    它开口总结之前），于是 `_collect_agent_output()` 返回空串，
    阶段被判定为「未产出内容」。

    真实产出在文件系统里，判据却只看聊天缓冲区 —— 这是判据与产出脱节。
    """
    r = _runnable()
    # 用真实 __init__ 验证属性是否被初始化（探针用 object.__new__ 绕过了它）
    import inspect
    src = inspect.getsource(StageRunnable.__init__)
    assert "_agent_tool_writes" in src, (
        "StageRunnable 未记录 agent 的写盘工具调用 —— "
        "超时时无法区分「什么都没做」和「做完了但没来得及说」")


def test_collect_output_reports_writes_when_text_is_empty():
    """文本为空但有写盘记录时，产出不得为空串。

    否则 `invoke()` 里 `if not output.strip(): return False` 会把
    一个**已经改了磁盘**的阶段当作彻底失败丢掉。
    """
    r = _runnable()
    r._agent_tool_writes = ["src/twosum/twosum.py", "tests/test_twosum.py"]
    out = r._collect_agent_output()
    assert out.strip(), (
        "有写盘记录时 _collect_agent_output() 仍返回空 —— 阶段产出被丢弃")
    assert "twosum.py" in out, (
        f"产出未提及被写的文件，用户无法得知 agent 做了什么: {out!r}")


def test_timeout_message_distinguishes_hang_from_work_in_progress():
    """超时报错必须说清「它当时在干活」，而不是笼统一句响应超时。

    事故 UI 只有 `opencode 响应超时` + `未产出内容，阶段中止` 两行，
    读者无从判断是死锁还是被误杀 —— 我自己也是翻服务端日志才确认的。
    """
    import inspect
    src = inspect.getsource(OpenCodeTransport.send_message)
    assert "CHAT_TIMEOUT" in src, (
        "超时报错未带上限数值，用户无法判断是否该调大")
    assert "工具调用" in src or "已完成" in src or "已写" in src, (
        "超时报错未提示 agent 当时的进展 —— "
        "无法区分「卡死」与「正在干活被砍」")
