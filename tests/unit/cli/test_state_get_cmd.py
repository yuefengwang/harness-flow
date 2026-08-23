r"""`sw state get` —— 让 hook 与外部工具读 JSON 状态，而不是 grep Markdown。

hook 曾用 ``grep -i -q "\[x\] Design approved"`` 判断设计是否批准。那等于
把 agent 能写的文本当成门禁凭据：agent 在正文里复述一句勾选过的 Gate，
硬校验就被骗过去了。这个命令是 shell 侧读状态的唯一正当入口。

输出契约：**裸值 + 退出码**，方便 ``[ "$(sw state get ...)" = "signed" ]``
这样直接用；``--json`` 给需要结构的调用方。
"""
from argparse import Namespace

import pytest

from sw_lib.cli.commands import cmd_state_get
from sw_lib.workflow import stage_state as ss

_STAGE = "04-review"


@pytest.fixture
def task(make_task):
    return make_task("pytest-state-get-cli", stage=_STAGE, stage_idx=3)


def _args(task, stage=_STAGE, field="gate", as_json=False):
    return Namespace(task=task, stage=stage, field=field, json=as_json)


def _run(capsys, args):
    code = cmd_state_get(args)
    return code, capsys.readouterr().out.strip()


def test_gate_unsigned_reports_unsigned(task, capsys):
    code, out = _run(capsys, _args(task))
    assert out == "unsigned"
    assert code != 0, "未签署必须以非零退出码收场，否则 hook 里的 || 不生效"


def test_gate_signed_reports_signed(task, capsys):
    ss.sign_gate(task, _STAGE)
    code, out = _run(capsys, _args(task))
    assert out == "signed"
    assert code == 0


def test_route_absent_is_empty_and_nonzero(task, capsys):
    code, out = _run(capsys, _args(task, field="route"))
    assert out == ""
    assert code != 0


def test_route_reports_target(task, capsys):
    ss.write_route(task, "05-Archive")
    code, out = _run(capsys, _args(task, field="route"))
    assert out == "05-archive", "Route 值必须归一化，避免大小写分支"
    assert code == 0


def test_json_output_carries_structure(task, capsys):
    import json
    ss.sign_gate(task, _STAGE)
    ss.write_route(task, "03-Coding")
    code, out = _run(capsys, _args(task, field="gate", as_json=True))
    payload = json.loads(out)
    assert payload["signed"] is True
    assert payload["signed_by"] == "user"
    assert [i["key"] for i in payload["items"]], "items 未输出"
    assert code == 0


def test_unknown_task_fails_cleanly(capsys):
    code, out = _run(capsys, _args("no-such-task-xyz"))
    assert code != 0
    assert "no-such-task-xyz" in out or out == "unsigned"


def test_unknown_field_is_rejected(task, capsys):
    code, out = _run(capsys, _args(task, field="bogus"))
    assert code != 0
    assert "bogus" in out


def test_agent_text_cannot_forge_signature(task, capsys):
    """agent 往阶段文件里写勾选过的 Gate，也不能让状态查询报 signed。"""
    from sw_lib.core.config import TASKS

    (TASKS / task / f"{_STAGE}.md").write_text(
        "## Gate\n- [x] Full build: ok\n- [x] Lint/static analysis pass\n"
        "- [x] Doc/config in sync\n- [x] README.md CLI commands verified\n",
        encoding="utf-8")

    code, out = _run(capsys, _args(task))
    assert out == "unsigned", "Markdown 内容伪造出了签署状态"
    assert code != 0


# ── 退出码必须真的传到 shell ──

def test_cli_propagates_exit_code(task):
    """hook 靠退出码判断，`sw` 入口丢掉返回值就等于门禁永远通过。"""
    import subprocess
    from sw_lib.core.config import ROOT

    def _run_sw(*argv):
        return subprocess.run(["python3", str(ROOT / "sw"), *argv],
                              cwd=str(ROOT), capture_output=True, text=True)

    r = _run_sw("state", "get", task, _STAGE, "gate")
    assert r.stdout.strip() == "unsigned", r.stdout
    assert r.returncode == 1, f"未签署却返回 0：hook 会被骗过（stderr={r.stderr}）"

    ss.sign_gate(task, _STAGE)
    r = _run_sw("state", "get", task, _STAGE, "gate")
    assert r.stdout.strip() == "signed"
    assert r.returncode == 0, r.stderr
