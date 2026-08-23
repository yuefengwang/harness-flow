"""remove-all CLI 层：确认闸门与参数路由。

重点在闸门：批量删除误触不可挽回，所以「没确认就不动手」比功能本身更重要。
"""
from argparse import Namespace
from unittest.mock import patch

import pytest

import sw_lib.cli.commands as cmds


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SW_YES", raising=False)
    monkeypatch.delenv("SW_NON_INTERACTIVE", raising=False)


def _args(purge=False):
    return Namespace(purge=purge)


def _fake_tasks(*names):
    return [{"id": n, "stage": "01-brainstorming", "status": "pending"}
            for n in names]


def test_declining_confirmation_removes_nothing(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    with patch.object(cmds._service, "list_tasks", return_value=_fake_tasks("a", "b")), \
         patch.object(cmds._service, "remove_all_tasks") as rm:
        cmds.cmd_remove_all(_args())
    rm.assert_not_called()
    assert "已取消" in capsys.readouterr().out


def test_confirmation_yes_proceeds(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    with patch.object(cmds._service, "list_tasks", return_value=_fake_tasks("a")), \
         patch.object(cmds._service, "remove_all_tasks",
                      return_value=[("a", "")]) as rm:
        cmds.cmd_remove_all(_args())
    rm.assert_called_once_with(purge=False)


def test_yes_flag_skips_prompt(monkeypatch):
    monkeypatch.setenv("SW_YES", "1")

    def _boom(*_a, **_k):
        raise AssertionError("--yes 下不应再询问")

    monkeypatch.setattr("builtins.input", _boom)
    with patch.object(cmds._service, "list_tasks", return_value=_fake_tasks("a")), \
         patch.object(cmds._service, "remove_all_tasks",
                      return_value=[("a", "")]) as rm:
        cmds.cmd_remove_all(_args())
    rm.assert_called_once_with(purge=False)


def test_non_interactive_without_yes_refuses(monkeypatch):
    """非交互下没人能回答，必须拒绝而不是默认执行。"""
    monkeypatch.setenv("SW_NON_INTERACTIVE", "1")
    with patch.object(cmds._service, "list_tasks", return_value=_fake_tasks("a")), \
         patch.object(cmds._service, "remove_all_tasks") as rm:
        cmds.cmd_remove_all(_args())
    rm.assert_not_called()


def test_eof_on_prompt_aborts(monkeypatch):
    """管道里跑、stdin 直接 EOF 时不能当成同意。"""
    def _eof(*_a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    with patch.object(cmds._service, "list_tasks", return_value=_fake_tasks("a")), \
         patch.object(cmds._service, "remove_all_tasks") as rm:
        cmds.cmd_remove_all(_args())
    rm.assert_not_called()


def test_empty_workspace_needs_no_confirmation(monkeypatch):
    def _boom(*_a):
        raise AssertionError("无任务时不该询问")

    monkeypatch.setattr("builtins.input", _boom)
    with patch.object(cmds._service, "list_tasks", return_value=[]), \
         patch.object(cmds._service, "remove_all_tasks") as rm:
        cmds.cmd_remove_all(_args())
    rm.assert_not_called()


def test_purge_flag_forwarded(monkeypatch):
    monkeypatch.setenv("SW_YES", "1")
    with patch.object(cmds._service, "list_tasks", return_value=_fake_tasks("a")), \
         patch.object(cmds._service, "remove_all_tasks",
                      return_value=[("a", "")]) as rm:
        cmds.cmd_remove_all(_args(purge=True))
    rm.assert_called_once_with(purge=True)


def test_purge_lists_trash_contents(monkeypatch, capsys):
    """--purge 会删掉回收站里的东西，确认前必须一并列出。"""
    monkeypatch.setattr("builtins.input", lambda *_: "n")

    def _list(from_trash=False):
        return _fake_tasks("ghost") if from_trash else _fake_tasks("live")

    with patch.object(cmds._service, "list_tasks", side_effect=_list), \
         patch.object(cmds._service, "remove_all_tasks"):
        cmds.cmd_remove_all(_args(purge=True))
    out = capsys.readouterr().out
    assert "ghost" in out and "live" in out


def test_partial_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("SW_YES", "1")
    with patch.object(cmds._service, "list_tasks", return_value=_fake_tasks("a", "b")), \
         patch.object(cmds._service, "remove_all_tasks",
                      return_value=[("a", ""), ("b", "boom")]):
        with pytest.raises(SystemExit) as ei:
            cmds.cmd_remove_all(_args())
    assert ei.value.code == 1
    assert "boom" in capsys.readouterr().out


# ── purge-trash ──

def _fake_trashed(*names):
    return [{"id": n, "stage": "N/A", "status": "N/A",
             "removed_at": "2026-08-22T09:00:00"} for n in names]


def test_purge_trash_declining_removes_nothing(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    with patch.object(cmds._service, "list_tasks",
                      return_value=_fake_trashed("fly", "ghost")), \
         patch.object(cmds._service, "purge_trash") as pg:
        cmds.cmd_purge_trash(Namespace())
    pg.assert_not_called()
    assert "已取消" in capsys.readouterr().out


def test_purge_trash_confirmed_purges(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    calls = []

    def _list(from_trash=False):
        calls.append(from_trash)
        # 第一次列出待删，清空后复查应为空
        return _fake_trashed("fly") if len(calls) == 1 else []

    with patch.object(cmds._service, "list_tasks", side_effect=_list), \
         patch.object(cmds._service, "purge_trash", return_value=["fly"]) as pg:
        cmds.cmd_purge_trash(Namespace())
    pg.assert_called_once_with()
    assert "物理删除 1 个任务" in capsys.readouterr().out


def test_purge_trash_empty_needs_no_confirmation(monkeypatch, capsys):
    def _boom(*_a):
        raise AssertionError("回收站为空时不该询问")

    monkeypatch.setattr("builtins.input", _boom)
    with patch.object(cmds._service, "list_tasks", return_value=[]), \
         patch.object(cmds._service, "purge_trash") as pg:
        cmds.cmd_purge_trash(Namespace())
    pg.assert_not_called()
    assert "回收站已是空的" in capsys.readouterr().out


def test_purge_trash_lists_only_trash(monkeypatch, capsys):
    """必须查回收站，不能误列活跃任务。"""
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    seen = {}

    def _list(from_trash=False):
        seen["from_trash"] = from_trash
        return _fake_trashed("fly")

    with patch.object(cmds._service, "list_tasks", side_effect=_list), \
         patch.object(cmds._service, "purge_trash"):
        cmds.cmd_purge_trash(Namespace())
    assert seen["from_trash"] is True


def test_purge_trash_non_interactive_refuses(monkeypatch):
    monkeypatch.setenv("SW_NON_INTERACTIVE", "1")
    with patch.object(cmds._service, "list_tasks",
                      return_value=_fake_trashed("fly")), \
         patch.object(cmds._service, "purge_trash") as pg:
        cmds.cmd_purge_trash(Namespace())
    pg.assert_not_called()


def test_purge_trash_reports_leftovers(monkeypatch, capsys):
    """purge_trash 用 ignore_errors=True，删不掉会静默；
    必须复查残留并以非零码退出，否则用户以为清干净了。"""
    monkeypatch.setenv("SW_YES", "1")
    calls = []

    def _list(from_trash=False):
        calls.append(from_trash)
        return _fake_trashed("stubborn")  # 清空后仍在

    with patch.object(cmds._service, "list_tasks", side_effect=_list), \
         patch.object(cmds._service, "purge_trash", return_value=[]):
        with pytest.raises(SystemExit) as ei:
            cmds.cmd_purge_trash(Namespace())
    assert ei.value.code == 1
    assert "未能删除: stubborn" in capsys.readouterr().out
