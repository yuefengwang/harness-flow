"""全局标志必须在子命令前后都生效。

现场问题：help 文本写的是 `./sw remove-all [--purge] [--yes]`，但 `--yes` 是
定义在**主** parser 上的全局标志。argparse 把子命令之后的它归进
`parse_known_args` 的 `unknown` 里静默丢弃 —— 于是用户照文档敲命令，
`SW_YES` 不被设置，破坏性操作的确认闸门仍然拦住他，而且没有任何提示说明原因。

`--yes` 关系到破坏性操作能否执行，静默丢弃是最坏的失败方式：用户以为自己
已经确认过了。
"""
import os
import shutil
import subprocess
import json

import pytest

from sw_lib.core.config import ROOT, TRASH

_TRASHED = "pytest-global-flags-trashed"


@pytest.fixture
def trashed_task():
    """在回收站里放一个任务，好让确认闸门真的被触达。

    回收站为空时 `purge-trash` 会提前返回「已是空的」，闸门根本走不到 ——
    那样的用例看着绿，其实什么都没验证。
    """
    d = TRASH / _TRASHED
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({
        "id": _TRASHED, "stage": "01-brainstorming", "stage_idx": 0,
        "stage_status": "pending", "removed_at": "2026-08-22T00:00:00",
    }), encoding="utf-8")
    yield _TRASHED
    shutil.rmtree(d, ignore_errors=True)


def _run(*argv, env=None):
    e = os.environ.copy()
    e["SW_NON_INTERACTIVE"] = "1"
    e.pop("SW_YES", None)
    if env:
        e.update(env)
    return subprocess.run(["python3", str(ROOT / "sw"), *argv],
                          cwd=str(ROOT), capture_output=True, text=True, env=e)


# ── --yes 的位置无关性 ──

@pytest.mark.parametrize("argv", [
    ("--yes", "purge-trash"),
    ("purge-trash", "--yes"),
    ("-y", "purge-trash"),
    ("purge-trash", "-y"),
])
def test_yes_is_honored_in_any_position(argv, trashed_task):
    """--yes 无论放在子命令前还是后，都必须放行破坏性操作。"""
    r = _run(*argv)
    out = r.stdout + r.stderr
    assert "请显式加 --yes" not in out, \
        f"{argv}: --yes 被静默丢弃\n{out}"
    assert "回收站已清空" in out, f"{argv}: 闸门未放行\n{out}"
    assert not (TRASH / _TRASHED).exists(), f"{argv}: 任务未被真正删除"


@pytest.mark.parametrize("argv", [
    ("--non-interactive", "list"),
    ("list", "--non-interactive"),
])
def test_non_interactive_is_honored_in_any_position(argv):
    r = _run(*argv)
    assert r.returncode == 0, r.stdout + r.stderr


def test_missing_yes_still_blocks_destructive_op(trashed_task):
    """没给 --yes 时闸门必须照常拦住 —— 修位置问题不能顺手放开闸门。"""
    r = _run("purge-trash")
    out = r.stdout + r.stderr
    assert "请显式加 --yes" in out, out
    assert (TRASH / _TRASHED).exists(), "闸门未放行却删掉了任务"


# ── 未知参数不该被静默吞掉 ──

def test_unknown_flag_is_reported():
    """拼错的标志必须报出来，而不是当作没写过。

    `--purge` 拼成 `--purg` 时用户期待的是「删干净了」，静默忽略会让他以为
    操作已生效。
    """
    r = _run("purge-trash", "--purg")
    out = r.stdout + r.stderr
    assert "--purg" in out, f"未知参数被静默忽略\n{out}"
    assert r.returncode != 0, "未知参数应以非零退出码收场"


def test_help_text_matches_actual_usage():
    """help 里写的用法必须真的能用。

    文档与实现不符比缺文档更糟：用户照着敲，失败了还以为是自己的问题。
    """
    r = _run("help")
    assert r.returncode == 0, r.stderr
    usage = r.stdout
    assert "remove-all" in usage
    # 文档若声明子命令后可加 --yes，那它就必须被接受（由上面的用例保证）
    for line in usage.splitlines():
        if "remove-all" in line and "--yes" in line:
            probe = _run("remove-all", "--yes")
            assert "请显式加 --yes" not in probe.stdout + probe.stderr, \
                "help 声明了 remove-all --yes，实际却不生效"
            break
