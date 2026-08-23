"""A2 的 3.4「允许的例外」：显式回退到 03a 重新见证。

`request_rewitness()` 早就实现了，但**没有任何生产调用点** —— 只有测试在调。
也就是说设计里那条唯一合法的出路在真实流程中不存在：agent 在 03b 发现
测试写错了，能做的只有静默改测试，而那恰好是 F4 要拦的行为。门禁会拒绝，
然后就卡死在那里，没有下一步。

拦住一条路而不给出替代路径，等于把人推向绕过机制。所以这里补两样东西：

1. CLI 入口 `--rewitness`（人的显式动作，留痕并计数）；
2. 门禁拒绝时把这条出路**打印出来** —— 出路存在但没人知道，等于不存在。

同时要确保它不是冻结机制的后门：回退必须留痕、必须重新见证到红，
不能变成「一键清哈希继续走」。
"""

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS, is_mock_agent
from sw_lib.workflow import red_witness as rw

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _requires_real_agent_mode():
    """真实见证行为的前提：非 mock 模式（理由同 test_red_witness_gate.py）。"""
    if is_mock_agent():
        pytest.skip("mock 模式不执行真实见证；相关契约见 test_red_witness_mock_mode.py")


@pytest.fixture
def task(tmp_path):
    created = []

    def _make(name, files):
        target = tmp_path / name
        for rel, body in files.items():
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


def _run_hook(name):
    return subprocess.run(
        ["bash", str(HOOKS_DIR / "check_03-coding.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def _enter_witness_flow(name):
    return subprocess.run(
        [str(HOOKS_DIR / "pre_check_03-coding.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "sw_lib.workflow.red_witness", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


# ── 门禁必须告知出路 ──

def test_hash_rejection_tells_user_how_to_rewitness(task):
    """哈希校验拒绝时，必须告知「怎么合法地改测试」。

    只说「你改了测试，拒绝」会把人逼向两条路：反复试探，或者干脆
    改回去硬凑 —— 而测试可能真的写错了。任务 T2 的教训是同一条：
    门禁必须给出可操作的下一步。
    """
    name, target = task("rw-rewit-hint", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0, "03a 见证失败，后续无意义"

    (target / "test_y.py").write_text(
        "from impl import f\n\n\ndef test_f():\n    assert f() == 3\n",
        encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode != 0
    assert "--rewitness" in r.stdout, \
        f"拒绝时没告诉用户合法的回退方式:\n{r.stdout}"


# ── CLI 入口 ──

def test_rewitness_cli_returns_to_03a_and_clears_freeze(task):
    """`--rewitness` 把 phase 退回 03a 并清掉冻结哈希。

    哈希必须一并清掉：否则回到 03a 改测试，立刻又撞上旧哈希 ——
    出路名存实亡。
    """
    name, target = task("rw-rewit-cli", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0
    assert rw.read_phase(name) == "03b"

    r = _cli(name, "--rewitness", "测试断言写错了")

    assert r.returncode == 0, f"--rewitness 失败:\n{r.stdout}\n{r.stderr}"
    record = rw.read_witness(name)
    assert rw.read_phase(name) == "03a", record
    assert record.get("test_files") == {}, f"冻结哈希未清:\n{record}"
    assert record.get("rewitness_count") == 1, record
    assert record.get("rewitness_reason") == "测试断言写错了", record


def test_rewitness_requires_a_reason(task):
    """回退必须带理由 —— 无理由的回退就是静默改测试换个说法。"""
    name, _ = task("rw-rewit-noreason", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0

    r = _cli(name, "--rewitness")

    assert r.returncode != 0, "无理由的回退被接受了"
    assert rw.read_phase(name) == "03b", "被拒绝的回退竟然改了 phase"


def test_rewitness_keeps_failed_nodes_monotonic(task):
    """回退**不得**清掉 `failed_nodes` —— 判据集是单调的（A2 的 4.1）。

    这是回退与「重置」的分界：测试文件可以重写，但已经见证过的 bug
    不能因为一次回退就从判据集里消失，否则同一个 bug 可以反复出现。
    """
    name, _ = task("rw-rewit-mono", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0
    witnessed = rw.read_witness(name)["failed_nodes"]
    assert witnessed == ["test_y.py::test_f"]

    assert _cli(name, "--rewitness", "写错了").returncode == 0

    assert rw.read_witness(name)["failed_nodes"] == witnessed, \
        "回退把判据集清空了 —— 同一个 bug 可以再犯一次"


def test_rewitness_still_requires_a_real_red(task):
    """回退不是后门：回到 03a 后仍必须重新见证到**真实的红**。

    若回退后能拿着「已经全绿的测试」直接过闸，那 `--rewitness` 就成了
    「一键清哈希」—— 冻结机制被自己的例外条款掏空。
    """
    name, target = task("rw-rewit-noback", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0
    assert _cli(name, "--rewitness", "重写测试").returncode == 0

    # 回到 03a，但把实现也写对了 —— 测试是绿的，见证不到红
    (target / "impl.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"回退后拿全绿的测试过闸了 —— --rewitness 成了冻结机制的后门:\n{r.stdout}"


def test_rewitness_then_new_red_refreezes(task):
    """完整的合法路径：回退 → 改测试 → 重新见证红 → 新哈希被冻结。"""
    name, target = task("rw-rewit-full", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0
    old_hash = dict(rw.read_witness(name)["test_files"])

    assert _cli(name, "--rewitness", "断言值写错").returncode == 0
    (target / "test_y.py").write_text(
        "from impl import f\n\n\ndef test_f():\n    assert f() == 42\n",
        encoding="utf-8")

    r = _run_hook(name)

    assert r.returncode == 0, f"回退后的合法重新见证被拒:\n{r.stdout}"
    record = rw.read_witness(name)
    assert rw.read_phase(name) == "03b", record
    assert record["test_files"] != old_hash, "新测试文件没有被重新冻结"
    assert record["rewitness_count"] == 1, record
