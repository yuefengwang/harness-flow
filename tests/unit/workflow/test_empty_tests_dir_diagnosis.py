"""空的 `tests/` 目录：失败信息必须指对方向 —— 任务 `44444` 的另一半。

## 现场

44444 的 agent 第 3 次工具调用是
`mkdir -p backend/routers backend/tests frontend/src`，
建了 `backend/tests/` **空目录**，此后再没碰过它。

这个空目录有一个反直觉的后果：它**触发**了 `_has_pytest_surface`
（`_TEST_DIR_NAMES` 命中 `tests`），于是判据认定「这是 Python 项目，
该写测试却没写」并拒绝。

若 agent 连目录都不建，`_has_pytest_surface` 在 44444 那个布局下仍会
因为 `backend/*.py` 命中最后那条 rglob 分支 —— 结论一样。
但**判据说出来的话应该不一样**：

* 「一个 .py 都没有」 → 这个栈可能不用 pytest，让路（`_check_unsupported_stack`）
* 「建了空的 tests/」  → 意图明显是要写测试，但没写 —— 这是最该被说清的一种

现在两种情形都只得到同一句「无测试：03a 阶段必须先写测试文件
（test_*.py / *_test.py / tests/）」。那句话的括号里写着 `tests/`，
而 agent **确实建了** `tests/` —— 它按字面看已经满足了要求。
失败信息与现场自相矛盾，这是 2.9.16 判过的「失败信息指错方向」。

## 判据的边界

不改变**结论**（拦，是对的），只改变**说法**：命中空测试目录时，
明确说出「目录在这里，但里面没有测试文件」，并给出目录的真实路径。

结论不变很重要 —— 放宽它就会让「建个空目录」变成绕过红见证的捷径。
"""

import shutil

import pytest

from sw_lib.core.config import TASKS
from sw_lib.workflow import red_witness as rw

_TASK = "pytest-emptytests"


@pytest.fixture
def target(tmp_path):
    """一个 44444 形态的项目：backend/ 有源码，backend/tests/ 是空目录。"""
    root = tmp_path / "repo"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (backend / "models.py").write_text("class User: pass\n", encoding="utf-8")
    (backend / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (backend / "tests").mkdir()          # ← 空目录，44444 的 mkdir -p 留下的
    return root


# ── 前提自检：这确实是 44444 的形状 ──

def test_shape_has_empty_tests_dir_and_no_test_files(target):
    """前提：有测试目录、目录是空的、项目有 Python 源码。"""
    assert (target / "backend" / "tests").is_dir()
    assert not list((target / "backend" / "tests").iterdir()), "夹具的 tests/ 不空"
    assert not rw.hash_test_files(target), "夹具里竟然有测试文件"
    assert rw._has_pytest_surface(target), \
        "这个形状不该走非 Python 让路 —— 它有 .py 也有 tests/"


# ── 主判据：说法要指对方向 ──

def test_empty_test_dir_is_named_in_the_message(target):
    """失败信息必须说出「测试目录存在但是空的」，并给出路径。

    44444 收到的是「必须先写测试文件（test_*.py / *_test.py / tests/）」——
    而它**确实建了** tests/。按字面看它已经满足要求，判据却拒绝它。
    """
    dirs = rw.empty_test_dirs(target)

    assert dirs, "没检出空测试目录 —— 44444 的现场识别不出来"
    assert any("tests" in d for d in dirs), dirs
    assert any("backend" in d for d in dirs), \
        f"没给出真实路径，agent 不知道说的是哪个目录: {dirs}"


def test_no_test_dir_at_all_is_not_reported_as_empty(tmp_path):
    """没有测试目录时不得报「空目录」—— 那是另一种情形。

    两种下一步不同：没建目录该「写测试」，建了空目录该「往里面写文件」。
    混成一句话就是 44444 收到的那种自相矛盾的提示。
    """
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")

    assert rw.empty_test_dirs(root) == [], \
        "没有测试目录却报了空目录"


def test_test_dir_with_files_is_not_reported_as_empty(tmp_path):
    """目录里有测试文件时不得报空 —— 判据不能恒真。"""
    root = tmp_path / "repo"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_smoke.py").write_text("def test_ok():\n    assert True\n",
                                         encoding="utf-8")

    assert rw.empty_test_dirs(root) == [], \
        "目录里有测试文件，却被报成空目录"


def test_dir_with_only_pycache_counts_as_empty(tmp_path):
    """只有 `__pycache__` 的目录仍算空 —— 那不是 agent 写的测试。

    与 `hash_test_files` 的 `_IGNORED_DIRS` 保持一致：判据两侧
    对「什么算测试文件」必须同一套语义，否则一边说有一边说没有。
    """
    root = tmp_path / "repo"
    tests = root / "tests"
    (tests / "__pycache__").mkdir(parents=True)
    (tests / "__pycache__" / "x.pyc").write_bytes(b"\x00")

    assert rw.empty_test_dirs(root), \
        "只有 __pycache__ 的目录该算空 —— 里面没有任何测试"


# ── 门禁输出：这条信息真的到达用户 ──

def test_gate_message_mentions_the_empty_dir(target, monkeypatch):
    """`_check_03a` 的拒绝文案里必须出现空目录的路径。

    判据算得对但没说出来，等于没修 —— 用户看到的还是那句
    自相矛盾的「请写 tests/」。
    """
    created = TASKS / _TASK
    shutil.rmtree(created, ignore_errors=True)
    created.mkdir(parents=True, exist_ok=True)
    try:
        from sw_lib.core.state import write_state
        write_state(_TASK, {"id": _TASK, "stage": "03-coding",
                            "stage_idx": 2, "stage_status": "running"})

        result = rw._check_03a(_TASK, target)

        assert not result.ok, "空测试目录竟然过闸了 —— 结论不该被放宽"
        text = "\n".join(result.lines)
        assert "tests" in text and "空" in text, \
            f"拒绝文案没说清「目录在但是空的」:\n{text}"
    finally:
        shutil.rmtree(created, ignore_errors=True)
