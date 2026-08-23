"""命令语义：名字必须与行为一致。

起因（用户 2026-08-23）：「我记得有一个 monitor 命令是可以继续一个任务的？」
—— 记忆没错，但当时并存着一个叫 `resume` 的命令，它什么都不 resume，
只打印三行状态然后叫你去跑 `monitor`。一个叫「恢复」的命令不恢复任何东西，
这是纯粹的命名债，读文档的人一定会先试错一次。

用户确认的真实语义分工（这三个命令本身设计合理，只是 resume 名不副实）：

    init      起任务，任务在**后台**跑
    monitor   进入实时页面，观看/介入任务执行流程
    resume    恢复被 remove 到回收站的任务   ← 用户期望的语义
    restore   恢复被 remove 到回收站的任务   ← 已有实现

后两者是同一件事，所以 `resume` 应当是 `restore` 的**别名**而非另一套逻辑：
两份实现必然漂移，而回收站恢复涉及移目录 + 改 STATUS.json，漂移的代价是
数据不一致。仓库里 `remove-all` / `purge-trash` 已有同样的别名先例。
"""

import inspect

import pytest

from sw_lib.cli import commands as C
from sw_lib.cli import main as M


# ── resume 必须真的做恢复，而不是打印状态 ──

def test_resume_is_an_alias_of_restore():
    """`resume` 与 `restore` 必须走**同一个**实现。

    不接受「各写一份」：回收站恢复要移目录、清 removed_at、重建
    STATUS.json 条目，两份实现漂移就是数据不一致。
    """
    assert hasattr(C, "cmd_resume"), "cmd_resume 不存在"
    assert C.cmd_resume is C.cmd_restore, (
        "resume 与 restore 不是同一实现 —— 两份逻辑必然漂移")


def test_resume_actually_restores_from_trash():
    """resume 的实现体必须调用 restore_task，而不是只 print。

    旧实现的函数体是 get_task_state + 三行 print，末尾还写着
    「操作: ./sw monitor (运行) 或 ./sw advance (推进)」——
    它是个状态速查，被错误地命名成了 resume。
    """
    src = inspect.getsource(C.cmd_resume)
    assert "restore_task" in src, (
        "cmd_resume 未调用 restore_task —— 它没有真的恢复任何东西")


def test_resume_is_registered_in_parser():
    """CLI 必须仍然接受 `sw resume`（用户记得这个名字，不能删）。"""
    parser = M.build_parser() if hasattr(M, "build_parser") else None
    if parser is None:
        pytest.skip("main 未暴露 build_parser，改由 dispatch 测试覆盖")
    names = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            names |= set(action.choices)
    assert "resume" in names and "restore" in names, (
        f"resume/restore 未同时注册: {sorted(names)}")


# ── monitor 的语义不得被这次改动波及 ──

def test_monitor_still_launches_tui():
    """monitor 仍是「进入实时页面观看后台任务」，不是「启动任务」。"""
    src = inspect.getsource(C.cmd_monitor)
    assert "MonitorTUI" in src, "cmd_monitor 不再拉起 TUI"


def test_help_text_describes_resume_as_trash_recovery():
    """帮助文本必须把 resume 归到「任务管理」而非「任务生命周期」。

    旧帮助把 `./sw resume --name=<id>` 列在生命周期区、紧跟 advance 之后，
    读者自然理解成「继续执行」。位置本身就是一种错误说明。
    """
    doc = C.__doc__ or ""
    assert "resume" in doc, "帮助文本未提及 resume"
    life = doc.split("任务生命周期:")[1].split("任务管理:")[0]
    assert "resume" not in life, (
        "resume 仍列在「任务生命周期」区 —— 位置暗示它能继续执行任务")
    mgmt = doc.split("任务管理:")[1]
    assert "resume" in mgmt, "resume 应列在「任务管理」区（与 restore 同组）"
