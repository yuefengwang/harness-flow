"""钩子侧的同一个错位：子目录布局下 `run_project_tests` 一个测试都不跑。

这是 `test_red_witness_subdir_layout.py` 的另一半。见证侧修好之后，
`repo/welll` 在钩子侧的实测是：

```text
$ . hooks/lib_run_tests.sh && _has_pytest_surface repo/welll
surface=NO
$ run_project_tests repo/welll "pytest 失败"
    ⓘ 未发现 pytest 测试面（未执行 pytest，非『通过』）
rc=0
```

17 个测试，一个没跑，退出码 0。

为什么这一侧不能不修：A2 的所有让路路径（`--abandon-witness` / 非 Python
栈 / 存量任务 / 实现先落盘）都把测试判定「交回 `run_project_tests`」。
交回的对象要是认不出测试面、直接返回 0，让路就从「不接管判定」变成
「取消判定」—— 那正是任务 helloworld 那个洞（见
`test_hook_test_surface_detection.py`）的第二种形态：那次错的是「只在
`tests/` 下」，这次错的是「只在子目录下」。

`red_witness._has_pytest_surface` 的 docstring 明写「判据必须与
`lib_run_tests.sh` 保持一致」。本轮见证侧新增了 `resolve_pytest_root`，
若钩子侧不跟上，两侧会第二次错位。

判据是行为：**子目录布局里失败的测试必须让钩子非零退出。**
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS

ROOT = Path(__file__).resolve().parents[3]

_FAILING = "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 3\n"
_PASSING = "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n"


def _lib(snippet):
    """在 lib_run_tests.sh 的语境里跑一段 shell。"""
    return subprocess.run(
        ["bash", "-c", f". hooks/lib_run_tests.sh\n{snippet}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )


@pytest.fixture
def subdir_project(tmp_path):
    """`welll` 的形状：实现与测试都在 `backend/` 下，仓库根无 pytest 配置。

    测试写 `from calc import add` —— 只有 cwd 在 `backend/` 时才成立，
    这正是 `from main import app` 的同构形态。
    """
    def _make(body=_FAILING, with_frontend=True):
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True, exist_ok=True)
        (backend / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8")
        (backend / "tests" / "test_calc.py").write_text(body, encoding="utf-8")
        if with_frontend:
            # welll 里 backend/ 旁边还有个 npm 项目，探测不能选中它。
            (tmp_path / "frontend" / "src").mkdir(parents=True, exist_ok=True)
            (tmp_path / "frontend" / "package.json").write_text(
                '{"name": "f"}\n', encoding="utf-8")
        (tmp_path / "README.md").write_text("# proj\n", encoding="utf-8")
        return tmp_path
    return _make


# ── 直接锁 run_project_tests 的行为 ──

def test_failing_tests_in_subdir_are_not_silently_skipped(subdir_project):
    """子目录布局里的失败测试必须被跑到并拦下。

    现场：`repo/welll` 报 `surface=NO`、rc=0，17 个测试一个没跑。
    """
    target = subdir_project(_FAILING)
    r = _lib(f'run_project_tests "{target}" "pytest 失败"; echo "exit=$?"')

    assert "exit=0" not in r.stdout, \
        f"子目录里的失败测试被静默跳过了:\n{r.stdout}"
    assert "未发现 pytest 测试面" not in r.stdout, \
        f"明明有 backend/tests/test_calc.py，却报告没有测试面:\n{r.stdout}"


def test_passing_tests_in_subdir_are_actually_run(subdir_project):
    """全绿时必须真的跑过 —— 「没跑」与「跑过且绿」不能混同（A3 的 2.4）。"""
    target = subdir_project(_PASSING)
    r = _lib(f'run_project_tests "{target}" "pytest 失败"; echo "exit=$?"')

    assert "exit=0" in r.stdout, f"全绿项目被拦下了:\n{r.stdout}"
    assert "passed" in r.stdout, \
        f"没有 passed 计数，说明 pytest 根本没执行:\n{r.stdout}"


def test_surface_detection_agrees_with_red_witness(subdir_project):
    """两侧判据必须一致 —— 这是 `_has_pytest_surface` docstring 里的硬要求。

    见证侧认得子目录布局（`resolve_pytest_root` 返回 `backend/`），
    钩子侧也必须认得，否则让路路径会把判定交给一个空转的函数。
    """
    from sw_lib.workflow import red_witness as rw

    target = subdir_project(_FAILING)
    assert rw.resolve_pytest_root(target) == target / "backend", \
        "前提不成立：见证侧就没认出 backend/"

    r = _lib(f'run_project_tests "{target}" "pytest 失败"; echo "exit=$?"')
    assert "未发现 pytest 测试面" not in r.stdout, \
        ("见证侧认得 backend/、钩子侧不认 —— 两侧判据第二次错位:\n"
         f"{r.stdout}")


def test_venv_in_subdir_is_used(subdir_project):
    """`backend/venv` 里的解释器必须被选中。

    `welll` 的依赖（fastapi 等）只装在那里，用 harness 的 python3 会
    ModuleNotFoundError —— 又一次「门禁与 agent 结论相反」。
    """
    target = subdir_project(_PASSING)
    bindir = target / "backend" / "venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    py = bindir / "python"
    py.write_text("#!/bin/sh\nexec python3 \"$@\"\n", encoding="utf-8")
    py.chmod(0o755)

    r = _lib(f'run_project_tests "{target}" "pytest 失败"; echo "exit=$?"')

    assert "使用项目虚拟环境" in r.stdout, \
        f"没用上 backend/venv 的解释器:\n{r.stdout}"
    assert str(py) in r.stdout, \
        f"用的不是 backend/venv/bin/python:\n{r.stdout}"


def test_flat_layout_is_unaffected(tmp_path):
    """平铺布局不得受影响 —— 探测是叠加，不是替换。"""
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(_PASSING, encoding="utf-8")

    r = _lib(f'run_project_tests "{tmp_path}" "pytest 失败"; echo "exit=$?"')

    assert "exit=0" in r.stdout, f"平铺布局跑不绿了:\n{r.stdout}"
    assert "passed" in r.stdout, f"平铺布局没真的跑:\n{r.stdout}"


def test_genuinely_testless_project_still_reports_no_surface(tmp_path):
    """真的没有测试时仍要如实说「没跑」，不能反过来把无测试说成已执行。"""
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        '{"name": "f"}\n', encoding="utf-8")
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")

    r = _lib(f'run_project_tests "{tmp_path}" "pytest 失败"; echo "exit=$?"')

    assert "exit=0" in r.stdout, r.stdout
    assert "未发现 pytest 测试面" in r.stdout, \
        f"没有测试却没有如实说明:\n{r.stdout}"


# ── 端到端：经由真实的 03 门禁 ──

def _make_task(name, target_dir):
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({
        "id": name, "stage": "03-coding", "stage_idx": 2,
        "stage_status": "running", "target_dir": str(target_dir),
    }), encoding="utf-8")
    return d


def test_03_gate_blocks_failing_tests_in_subdir_layout(subdir_project):
    """经由真实钩子：子目录布局的失败测试不许过 03 闸。

    这一条是整轮修复的底线。见证侧不再误判「造红」之后，若钩子侧仍认不出
    测试面，结果会从「误拦好代码」翻成「放过坏代码」—— 那比原来的 bug 更糟。
    """
    target = subdir_project(_FAILING)
    name = "rw-subdir-gate"
    d = _make_task(name, target)
    try:
        r = subprocess.run(
            ["bash", str(HOOKS_DIR / "check_03-coding.sh"), name],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
        )
        assert r.returncode != 0, \
            f"子目录布局的失败测试过了 03 闸:\n{r.stdout}\n{r.stderr}"
    finally:
        shutil.rmtree(d, ignore_errors=True)
