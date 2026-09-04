"""任务 `44444` 的 03 门禁死锁 —— 用 MockAgent 走生产路径复现。

## 现场（`workspace/tasks/44444/.log`，逐条可查）

63 次工具调用，涉及 test 的只有第 3 次：

    bash  mkdir -p backend/routers backend/tests frontend/src   ← 空目录
    ...   （此后 60 次调用再没碰过 tests/）
    bash  cd backend && uvicorn main:app ...                    ← 手工起服务
    bash  curl -s localhost:8000/api/posts | head               ← 手工验证
    产出  「后端 API 所有接口测试通过」                          ← 它没说谎

准出被拦：

    ❌ 无测试：03a 阶段必须先写测试文件（test_*.py / *_test.py / tests/）

**这是死锁的一半**：判据是对的（红绿流程确实没走），但那句话的括号里
写着 `tests/`，而 agent **确实建了** `tests/`。按字面看它已满足要求。
另一半是指令：03 的 system_prompt 从未要求过先写测试
（见 `tests/unit/prompts/test_03_red_green_instruction.py`）。

## 为什么用 MockAgent 而不是手搓夹具

`harness-criterion-design` 的红绿第 1 步：复现的必须是「agent 做了什么」，
不是「我对文件系统做了什么」。这里至少两处只有走生产路径才对：
`_write_sample_project` 的落盘顺序（先实现、后空目录，与 44444 一致），
以及 `.state` 的 `target_dir` 解析（`_resolve_target_dir` 与门禁同源）。

## 判据的边界

**结论不改** —— 拦是对的。放宽会让「建个空目录」变成绕过红见证的捷径，
那比原来的 bug 更糟。改的只有**说法**：说出目录的真实路径，
并说清「目录建了不算写测试」。所以本文件同时守住反面：
空目录仍须 rc≠0，写了真测试才放行。
"""

import os
import shutil
import subprocess

import pytest

from sw_lib.agents.mock import BUG_44444_ENV, MockAgent
from sw_lib.core.config import ROOT, TASKS, TPLS
from sw_lib.core.state import read_state, write_state
from sw_lib.workflow import red_witness as rw

_STAGE = "03-coding"
_TASK = "pytest-bug44444"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """零延时 + 屏蔽 sw_log。测试自己不该变成慢的那一环。"""
    monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
    monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                        lambda name, msg, src="sw": None)


@pytest.fixture
def task(tmp_path):
    """建任务：真实模板 + target_dir 指向 tmp（不污染 repo/）。"""
    created = TASKS / _TASK
    shutil.rmtree(created, ignore_errors=True)
    created.mkdir(parents=True, exist_ok=True)

    target = tmp_path / "repo44444"
    write_state(_TASK, {
        "id": _TASK, "stage": _STAGE, "stage_idx": 2,
        "stage_status": "running", "target_dir": str(target),
    })
    tpl = TPLS / f"{_STAGE}.md"
    if tpl.exists():
        (created / f"{_STAGE}.md").write_text(
            tpl.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        yield target
    finally:
        shutil.rmtree(created, ignore_errors=True)


def _run_mock(monkeypatch, empty_tests: bool):
    """跑一轮 mock 的 03 场景，返回 agent 说出来的文本。"""
    monkeypatch.setenv(BUG_44444_ENV, "1" if empty_tests else "0")
    monkeypatch.delenv("SW_MOCK_CODING_SUBDIR_LAYOUT", raising=False)
    logs = []
    agent = MockAgent(
        {"add_log": lambda src, msg: logs.append((src, msg)),
         "is_running": lambda: True},
        _TASK, _STAGE, 2,
    )
    agent.running = True
    agent._run_scenario()
    return "\n".join(msg for src, msg in logs if src == "agent")


# ── 前提自检：复现的确实是 44444 的形状 ──

def test_repro_matches_the_44444_shape(task, monkeypatch):
    """前提：实现落了盘、tests/ 建了、里面一个测试都没有。

    四条同时成立才是 44444。少一条测的就是别的东西 ——
    尤其「有 .py 实现」这条：没有它，判据会走非 Python 让路那条分支。
    """
    _run_mock(monkeypatch, empty_tests=True)

    assert (task / "backend" / "calc.py").is_file(), "实现没落盘"
    assert (task / "backend" / "tests").is_dir(), "空 tests/ 没建 —— 复现不出那句话"
    assert not rw.hash_test_files(task), "复现里竟然有测试文件"
    assert rw._has_pytest_surface(task), \
        "这个形状不该走非 Python 让路 —— 它有 .py 也有 tests/"


def test_default_scenario_still_writes_tests(task, monkeypatch):
    """默认形态不变：正常场景仍然写测试。复现开关默认关闭。"""
    _run_mock(monkeypatch, empty_tests=False)

    assert rw.hash_test_files(task), \
        "默认形态没写测试 —— 开关默认值反了，e2e 主路径会被带偏"


# ── 主判据：那句话必须指对方向 ──

def test_gate_names_the_empty_dir_not_ask_for_it_again(task, monkeypatch):
    """**这就是 44444 收到的那一刻。** 拒绝文案必须说出空目录的路径。

    修复前它收到的是「必须先写测试文件（test_*.py / *_test.py / tests/）」——
    要求它做一件它已经做过的事。agent 无从知道下一步是什么。
    """
    _run_mock(monkeypatch, empty_tests=True)
    rw.begin_test_phase(_TASK)

    result = rw._check_03a(_TASK, task)
    text = "\n".join(result.lines)

    assert not result.ok, "空 tests/ 过闸了 —— 结论不该被放宽"
    assert "backend/tests" in text, \
        f"没说出空目录的真实路径，agent 不知道说的是哪个目录:\n{text}"
    assert "空" in text, f"没说清「目录在但是空的」:\n{text}"


def test_gate_says_manual_verification_does_not_count(task, monkeypatch):
    """必须说清「bash 手工验证不算」—— 44444 正是这么走偏的。

    它 curl 过每一个接口，结论是真的。不说这句，
    下一个 agent 还会认为自己已经「测试通过」。
    """
    _run_mock(monkeypatch, empty_tests=True)
    rw.begin_test_phase(_TASK)

    text = "\n".join(rw._check_03a(_TASK, task).lines)

    assert "pytest" in text, f"没说清判据认的是 pytest:\n{text}"


def test_hook_script_rejects_the_44444_shape(task, monkeypatch):
    """走**钩子脚本**再验一次 —— 单元绿不等于机制接通（本仓库已六次）。

    `check_03-coding.sh` 是 TUI 真正调用的那一个，
    它的退出码才决定「用户敲 /advance 能不能推进」。
    这里期望的是**拒绝**（rc≠0）：结论不变，变的是文案。

    进入 03a 必须走**真实的 pre hook**，不能在进程内调 `begin_test_phase`：
    测试进程的证据密钥被 conftest 重定向到 tmp，进程内写 `.state`
    会让钩子判成 `tampered`，红的原因就不再是被测行为
    （同 `test_red_witness_non_python` 的处置）。
    """
    _run_mock(monkeypatch, empty_tests=True)
    subprocess.run([str(ROOT / "hooks" / "pre_check_03-coding.sh"), _TASK],
                   cwd=str(ROOT), capture_output=True, text=True, check=False)

    res = subprocess.run(
        [str(ROOT / "hooks" / "check_03-coding.sh"), _TASK],
        cwd=str(ROOT), capture_output=True, text=True, check=False)

    assert res.returncode != 0, (
        "空 tests/ 通过了钩子 —— 红见证被绕过了:\n" f"{res.stdout}\n{res.stderr}")
    assert "backend/tests" in res.stdout, (
        "钩子输出里没有空目录的路径 —— 判据算对了但没说出来，"
        f"用户看到的还是那句自相矛盾的话:\n{res.stdout}")


# ── 反面：放行的路仍然通，硬规则原样 ──

def test_writing_a_real_test_changes_the_verdict(task, monkeypatch):
    """往那个空目录里写一个真测试，判据就不再报「空目录」。

    这条守住判据不恒真，也守住文案给出的下一步**真的有效** ——
    照它说的做，就能走出去。
    """
    _run_mock(monkeypatch, empty_tests=True)
    (task / "backend" / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 5\n",
        encoding="utf-8")

    assert rw.empty_test_dirs(task) == [], \
        "写了测试文件之后仍报空目录 —— 文案给的下一步是走不通的"
    assert rw.hash_test_files(task), "测试文件没被判据看见"
