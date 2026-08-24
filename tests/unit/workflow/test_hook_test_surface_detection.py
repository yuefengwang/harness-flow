"""`run_project_tests` 必须能看见只放在 `tests/` 下的测试。

现场 bug（任务 `helloworld`）：`repo/helloworld` 的布局是

    src/{models,database,main}.py
    tests/test_*.py          ← 测试**只**在这里
    （没有 pytest.ini，没有 pyproject.toml，没有顶层 test_*.py）

`run_project_tests` 的测试面判据是 `pytest.ini` / `pyproject.toml` /
顶层 `test_*.py` 三者之一，**都不命中**，于是它打印
「ⓘ 未发现 pytest 测试面（未执行 pytest，非『通过』）」并返回 0 ——
一个失败的测试套件就这样过闸了。

这是一条**既有缺口**，不是本轮改动引入的，但本轮必须修：A2 的所有让路
路径（`--abandon-witness`、非 Python 栈、存量任务）都把测试判定
「交回 `run_project_tests`」。如果交回的对象根本不跑测试，让路就从
「不接管判定」变成了「取消判定」—— 10.6 第二/七行那个洞的第三种形态。

`red_witness._has_pytest_surface` 的 docstring 里写着「`tests/` 目录与
setup.py / setup.cfg ... 在那边是靠 pyproject 命中的」—— 这个假设对
helloworld 这类没有 pyproject 的项目不成立，两侧判据由此错位：
见证侧认得 `tests/`，钩子侧认不得。

判据是行为：**失败的测试必须让钩子非零退出**。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS

ROOT = Path(__file__).resolve().parents[3]

_FAILING = "def test_boom():\n    assert 1 == 2\n"
_PASSING = "def test_ok():\n    assert 1 == 1\n"


def _lib(snippet):
    """在 lib_run_tests.sh 的语境里跑一段 shell，返回 CompletedProcess。"""
    return subprocess.run(
        ["bash", "-c", f". hooks/lib_run_tests.sh\n{snippet}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


@pytest.fixture
def project(tmp_path):
    """只在 tests/ 下放测试，刻意不给 pytest.ini / pyproject.toml。"""
    def _make(body=_FAILING, with_src=True):
        if with_src:
            src = tmp_path / "src"
            src.mkdir(exist_ok=True)
            (src / "__init__.py").write_text("", encoding="utf-8")
            (src / "models.py").write_text(
                "def amount():\n    return 100\n", encoding="utf-8")
        tests = tmp_path / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_models.py").write_text(body, encoding="utf-8")
        return tmp_path
    return _make


# ── 直接锁 run_project_tests 的行为 ──

def test_failing_tests_under_tests_dir_are_not_silently_skipped(project):
    """只在 `tests/` 下的失败测试必须被跑到并拦下。

    这是 helloworld 的现场形状。此前 run_project_tests 直接打印
    「未发现 pytest 测试面」并返回 0。
    """
    target = project(_FAILING)
    r = _lib(f'run_project_tests "{target}" "pytest 失败"; echo "exit=$?"')

    assert "exit=0" not in r.stdout, \
        f"tests/ 下的失败测试被静默跳过了:\n{r.stdout}"
    assert "未发现 pytest 测试面" not in r.stdout, \
        f"明明有 tests/test_models.py，却报告没有测试面:\n{r.stdout}"


def test_passing_tests_under_tests_dir_are_actually_run(project):
    """全绿时也必须真的跑过 —— 「没跑」与「跑过且绿」不能混同（A3 的 2.4）。"""
    target = project(_PASSING)
    r = _lib(f'run_project_tests "{target}" "pytest 失败"; echo "exit=$?"')

    assert "exit=0" in r.stdout, f"全绿项目被拦下了:\n{r.stdout}"
    assert "passed" in r.stdout, \
        f"没有 passed 计数，说明 pytest 根本没执行:\n{r.stdout}"


def test_surface_detection_agrees_with_red_witness(project):
    """两侧判据必须一致：见证侧认得的测试面，钩子侧也要认得。

    错位的后果就是让路路径把判定交给一个不跑测试的函数。
    """
    from sw_lib.workflow.red_witness import _has_pytest_surface

    target = project(_FAILING)
    assert _has_pytest_surface(target), "前提不成立：见证侧就不认这个布局"

    r = _lib(f'run_project_tests "{target}" "pytest 失败"; echo "exit=$?"')
    assert "未发现 pytest 测试面" not in r.stdout, \
        ("见证侧认得 tests/、钩子侧不认 —— 两侧判据错位，"
         f"让路路径会把判定交给一个空转的函数:\n{r.stdout}")


def test_genuinely_testless_project_still_reports_no_surface(tmp_path):
    """真的没有测试时，仍必须如实说「没跑」而不是「通过」。

    修测试面判据不能反过来把「无测试」说成「已执行」。
    """
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    r = _lib(f'run_project_tests "{tmp_path}" "pytest 失败"; echo "exit=$?"')

    assert "exit=0" in r.stdout, r.stdout
    assert "未发现 pytest 测试面" in r.stdout, \
        f"没有测试却没有如实说明:\n{r.stdout}"


# ── 端到端：经由 03 门禁 ──

def _make_task(name, target_dir):
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({
        "id": name, "stage": "03-coding", "stage_idx": 2,
        "stage_status": "running", "target_dir": str(target_dir),
    }), encoding="utf-8")
    return d


def test_03_gate_blocks_failing_tests_dir_layout(project):
    """经由真实钩子：`tests/`-only 布局的失败测试不许过 03 闸。

    未进入见证流程（phase=none）时判定由 run_project_tests 负责，
    这条正是「失败的测试不许过闸」在该布局下的实例。
    """
    target = project(_FAILING)
    name = "rw-surface-gate"
    d = _make_task(name, target)
    try:
        r = subprocess.run(
            ["bash", str(HOOKS_DIR / "check_03-coding.sh"), name],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        )
        assert r.returncode != 0, \
            f"tests/-only 布局的失败测试过了 03 闸:\n{r.stdout}"
    finally:
        shutil.rmtree(d, ignore_errors=True)
