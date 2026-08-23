"""`python3 -m sw_lib.workflow.red_witness` 的 CLI 契约。

为什么要单测 CLI：见证的判定在 Python 里，但**调用方是 bash**
（`hooks/check_03-coding.sh` 与 `hooks/pre_check_03-coding.sh`）。
bash 只能看到退出码与 stdout，因此这两样就是接口本身 ——
改动 `check_gate` 内部随意，改 CLI 的字面输出会静默拆掉钩子。

实测起因：`check_03-coding.sh` 里已经写了
`red_witness "$TASK" --phase`，而 `main()` 当时只认一个位置参数，
未知参数被当成任务名 —— 钩子拿到的是空串，`= "none"` 的判断永假，
于是「未进入见证流程时仍由 run_project_tests 判定」这条分流从未生效。
这是一处沉默失效：全部用例照旧过，钩子行为却是错的。
"""

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from sw_lib.core.config import TASKS, is_mock_agent
from sw_lib.workflow import red_witness as rw

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _requires_real_agent_mode():
    """真实见证行为的前提：非 mock 模式（理由同 test_red_witness_gate.py）。"""
    if is_mock_agent():
        pytest.skip("mock 模式不执行真实见证；相关契约见 test_red_witness_mock_mode.py")


@pytest.fixture
def task(tmp_path):
    """建一个 03-coding 任务，target_dir 指向 tmp（CLI 走真实 .state 读写）。"""
    created = []

    def _make(name, files=None):
        target = tmp_path / name
        target.mkdir(parents=True, exist_ok=True)
        for rel, body in (files or {}).items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / ".state").write_text(json.dumps({
            "id": name, "stage": "03-coding", "stage_idx": 2,
            "stage_status": "running", "target_dir": str(target),
        }), encoding="utf-8")
        created.append(d)
        return name, target

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "sw_lib.workflow.red_witness", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


# ── --phase：钩子据此决定要不要把测试判定交回 run_project_tests ──

def test_phase_prints_none_for_fresh_task(task):
    """没有见证记录时 `--phase` 必须打印 none 且退出码 0。

    退出码也是契约：钩子用 `$(... --phase)` 取值，而脚本开头是 `set -e`,
    非零会让整个门禁直接中断。
    """
    name, _ = task("rw-cli-fresh")
    r = _cli(name, "--phase")

    assert r.returncode == 0, f"--phase 不该失败:\n{r.stdout}\n{r.stderr}"
    assert r.stdout.strip() == "none", repr(r.stdout)


def test_phase_prints_03a_after_begin(task):
    name, _ = task("rw-cli-03a")
    rw.begin_test_phase(name)
    r = _cli(name, "--phase")

    assert r.stdout.strip() == "03a", repr(r.stdout)


def test_phase_prints_03b_after_red(task):
    name, _ = task("rw-cli-03b")
    rw.record_red(name, rw.WitnessVerdict("ok", "", 1,
                                          failed_nodes=["t.py::test_a"]),
                  {"t.py": "a" * 64})
    r = _cli(name, "--phase")

    assert r.stdout.strip() == "03b", repr(r.stdout)


def test_phase_does_not_run_tests(task):
    """`--phase` 只读状态，绝不能跑测试。

    它在 `set -e` 的钩子里被调用两次；若它顺手跑一遍 pytest，
    03 阶段的测试就会被跑三遍，且 pre hook 的 30 秒超时必然被撞破
    （A2 的 5.1 那两个坑）。
    判据：即使 target_dir 里放着一个必然崩掉收集的测试，`--phase` 也照常返回。
    """
    name, _ = task("rw-cli-nopytest", {
        "test_boom.py": "import nosuchmodule_zzz\n",
    })
    r = _cli(name, "--phase")

    assert r.returncode == 0, f"--phase 疑似跑了测试:\n{r.stdout}\n{r.stderr}"
    assert r.stdout.strip() == "none", repr(r.stdout)
    assert "nosuchmodule_zzz" not in (r.stdout + r.stderr), \
        "--phase 收集了测试 —— 它应当只读 .state"


def test_phase_of_unknown_task_is_none(task):
    """未知任务不得让钩子中断 —— 打印 none 并返回 0。"""
    r = _cli("rw-cli-no-such-task-zzz", "--phase")

    assert r.returncode == 0, f"未知任务让 --phase 失败了:\n{r.stderr}"
    assert r.stdout.strip() == "none", repr(r.stdout)


# ── --begin：pre hook 用它显式进入 03a ──

def test_begin_sets_phase_to_03a(task):
    """`--begin` 显式进入见证流程，这是 phase 唯一的来源。"""
    name, _ = task("rw-cli-begin")
    assert rw.read_phase(name) == "none"

    r = _cli(name, "--begin")

    assert r.returncode == 0, f"--begin 失败:\n{r.stdout}\n{r.stderr}"
    assert rw.read_phase(name) == "03a", rw.read_witness(name)


def test_begin_is_idempotent_after_witness(task):
    """已见证过的任务再 `--begin` 不得退回 03a、不得抹掉冻结哈希。

    pre hook 每次进入 03 阶段都会调它，返工轮次也会 —— 不幂等的话，
    「改测试让它过」就重新变成一条免费路径。
    """
    name, _ = task("rw-cli-begin-idem")
    rw.record_red(name, rw.WitnessVerdict("ok", "", 1,
                                          failed_nodes=["t.py::test_a"]),
                  {"t.py": "a" * 64})

    r = _cli(name, "--begin")

    assert r.returncode == 0, r.stderr
    assert rw.read_phase(name) == "03b", "已见证的任务被退回 03a"
    assert rw.read_witness(name)["test_files"] == {"t.py": "a" * 64}, \
        "冻结哈希被 --begin 抹掉"


def test_unavailable_does_not_silently_enter_03a(task):
    """记 `unavailable` 不得把 phase 写成 03a —— 那是「进入见证流程」的语义。

    实测隐患：`mark_unavailable` 里曾有 `setdefault("phase", PHASE_TEST)`，
    于是存量任务第一次过闸后 phase 就变成 03a，**下一轮**门禁按 03a 判定，
    要求「测试必须是红的」—— 存量任务的测试是绿的，于是永久卡死。
    更糟的是它把「未见证」伪装成「已进入见证流程」，phase 显式化的前提被破坏。
    """
    name, _ = task("rw-cli-unavail")
    rw.mark_unavailable(name, "存量任务")

    assert rw.read_phase(name) == "none", \
        f"记 unavailable 时被静默推进了 phase: {rw.read_witness(name)}"
    assert _cli(name, "--phase").stdout.strip() == "none"


def test_unavailable_then_begin_still_enters_03a(task):
    """但存量任务后续被显式 `--begin` 时，仍应正常进入 03a。

    上一条是「不许偷偷进」，这条是「该进的时候进得去」——
    否则修法就变成了把 phase 焊死在 none。
    """
    name, _ = task("rw-cli-unavail-begin")
    rw.mark_unavailable(name, "存量任务")

    assert _cli(name, "--begin").returncode == 0
    assert rw.read_phase(name) == "03a", rw.read_witness(name)


def test_unknown_flag_is_rejected_not_treated_as_task(task):
    """未知参数必须报错，不能被当成任务名静默吞掉。

    这正是 `--phase` 那次沉默失效的成因：位置参数解析把 `--phase`
    当成了任务名，钩子读到空串却一切「正常」。
    """
    name, _ = task("rw-cli-badflag")
    r = _cli(name, "--no-such-flag")

    assert r.returncode != 0, f"未知参数被静默接受:\n{r.stdout}"
    assert "--no-such-flag" in (r.stdout + r.stderr), \
        f"报错未指出是哪个参数:\n{r.stdout}\n{r.stderr}"
