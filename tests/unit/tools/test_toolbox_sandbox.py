"""Unit tests for Toolbox sandbox (Phase 0, P0-2): command whitelist + path escape fix.

这些测试只验证「校验逻辑」与「安全边界」，不实际执行破坏性命令；
run_command 的真实执行用例用 mock subprocess 隔离。
"""
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sw_lib.tools.toolbox import (
    RunCommandTool, WriteFileTool, DEFAULT_ALLOWED_COMMANDS, Toolbox,
)


# ── 白名单校验（纯字符串逻辑，不执行命令）──

def test_whitelist_allows_echo():
    t = RunCommandTool()
    assert t._check_whitelist("echo hello") is None


def test_whitelist_allows_git():
    t = RunCommandTool()
    assert t._check_whitelist("git status") is None


def test_whitelist_allows_python():
    t = RunCommandTool()
    assert t._check_whitelist("python -c 'print(1)'") is None


def test_whitelist_denies_rm():
    t = RunCommandTool()
    res = t._check_whitelist("rm -rf /")
    assert res is not None
    assert "rm" in res


def test_whitelist_denies_path_prefix_bypass():
    """绝对路径前缀尝试绕过白名单（如 /usr/bin/rm）必须被拦。"""
    t = RunCommandTool()
    res = t._check_whitelist("/usr/bin/rm -rf /tmp/x")
    assert res is not None
    assert "不在允许" in res or "rm" in res


def test_whitelist_empty_command_denied():
    t = RunCommandTool()
    assert t._check_whitelist("") is not None


def test_whitelist_handles_quoted_exe():
    """exe 取 basename 匹配，路径前缀不影响白名单判定。"""
    t = RunCommandTool()
    # 带绝对路径前缀的白名单命令仍按 basename 放行
    assert t._check_whitelist("/usr/bin/git status") is None
    # 带绝对路径前缀的非白名单命令被拒
    assert t._check_whitelist("/bin/evil scrape") is not None


def test_whitelist_unknown_command_denied():
    t = RunCommandTool()
    res = t._check_whitelist("supersecretcmd foo")
    assert res is not None


# ── run_command 行为（mock subprocess，不真实执行）──

def test_run_command_uses_shell_false():
    """run_command 必须以参数列表方式调用，禁止 shell=True。"""
    t = RunCommandTool()
    with patch("sw_lib.tools.toolbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        t("echo hello", cwd=None)
        # 断言 subprocess.run 收到的是列表而非字符串，且 shell=False
        args, kwargs = mock_run.call_args
        assert isinstance(args[0], list), "命令必须以参数列表传入"
        assert kwargs.get("shell") is False, "禁止 shell=True"


def test_run_command_denied_reaches_subprocess():
    """白名单拒绝的命令不应调用 subprocess。"""
    t = RunCommandTool()
    with patch("sw_lib.tools.toolbox.subprocess.run") as mock_run:
        res = t("rm -rf /")
        mock_run.assert_not_called()
        assert "rm" in res


def test_run_command_state_file_protected():
    """restricted 默认开启时，禁止改 .state / STATUS.json / sw advance。"""
    t = RunCommandTool()
    with patch("sw_lib.tools.toolbox.subprocess.run") as mock_run:
        res = t("echo x > .state")
        mock_run.assert_not_called()
        assert "禁止" in res


def test_run_command_restricted_false_still_whitelisted():
    """restricted=False 仅放宽状态文件检查，不降级白名单。"""
    t = RunCommandTool()
    with patch("sw_lib.tools.toolbox.subprocess.run") as mock_run:
        res = t("rm -rf /", restricted=False)
        mock_run.assert_not_called()
        assert "rm" in res  # 仍被白名单拦


# ── _safe_path 路径逃逸修复 ──

def test_safe_path_blocks_parent_escape(tmp_path):
    """_safe_path 应阻止访问项目根之外的路径。"""
    from sw_lib.core.config import ROOT
    t = WriteFileTool()
    # 用真实 ROOT 解析，构造一个明显逃逸的路径
    evil = "../etc/passwd"
    try:
        res = t._safe_path(evil)
        # 若未抛异常，结果必须仍在 ROOT 内
        assert ROOT.resolve() in res.resolve().parents or res.resolve() == ROOT.resolve()
    except PermissionError:
        pass  # 期望行为：拒绝逃逸


def test_safe_path_allows_inside_root(tmp_path):
    from sw_lib.core.config import ROOT
    t = WriteFileTool()
    res = t._safe_path("repo/foo.txt")
    assert ROOT.resolve() in res.resolve().parents


# ── Toolbox 组装仍正常 ──

def test_toolbox_registers_five_tools():
    tb = Toolbox("dummy", "01-brainstorming", {})
    assert set(tb._all_tools.keys()) == {
        "list_files", "read_file", "write_file", "run_command", "ask_user"
    }


def test_allowed_commands_is_set():
    assert isinstance(DEFAULT_ALLOWED_COMMANDS, set)
    assert "git" in DEFAULT_ALLOWED_COMMANDS
    assert "rm" not in DEFAULT_ALLOWED_COMMANDS
