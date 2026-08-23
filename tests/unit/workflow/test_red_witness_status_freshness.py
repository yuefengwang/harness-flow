"""A2 的 R2/R3 三态：`status` 必须如实反映当前结论，不能是陈旧残留。

`status` 是下游 A3 / A6（O4）/ A10 读的那个字段 —— 它决定报告里显示
✅ 还是 ❓。因此它的**过时**和它的**错值**一样危险。

实施期实测出的缺口：`mark_unavailable` 会写 `status = "unavailable"`，
而 `record_red` / `record_green` 从不清它。真实序列是这样的：

    返工轮次（phase=none）→ 记 unavailable
    → 下一轮 pre hook 进入 03a → 见证到真实的红 → 转绿
    → status 仍是 "unavailable"

于是一次货真价实的见证在下游被报成「未验证」。方向上它是「把做到了的
说成没做到」，不像反方向那样危险，但它同样会让报告失去信息量 ——
如果 ❓ 既可能是真没见证也可能是陈旧残留，那这个字段就没法用了。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS, is_mock_agent
from sw_lib.workflow import red_witness as rw

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _requires_real_agent_mode():
    if is_mock_agent():
        pytest.skip("mock 模式不执行真实见证；契约见 test_red_witness_mock_mode.py")


def _make_task(name, target_dir):
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({
        "id": name, "stage": "03-coding", "stage_idx": 2,
        "stage_status": "running", "target_dir": str(target_dir),
    }), encoding="utf-8")
    return d


def _run_hook(task_name):
    return subprocess.run(
        ["bash", str(HOOKS_DIR / "check_03-coding.sh"), task_name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def _enter_witness_flow(task_name):
    return subprocess.run(
        [str(HOOKS_DIR / "pre_check_03-coding.sh"), task_name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def task(tmp_path):
    created = []

    def _make(name, files):
        target = tmp_path / name
        for rel, body in files.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        created.append(_make_task(name, target))
        return name, target

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def test_status_is_cleared_when_red_is_witnessed(task):
    """先记过 `unavailable` 的任务，见证到红之后 status 不得还是 unavailable。

    这是 04→03 返工的真实序列：返工轮 phase=none 记下 unavailable，
    下一轮 pre hook 才把它推进 03a。
    """
    name, target = task("rw-status-red", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _run_hook(name)                      # phase=none → 记 unavailable
    assert rw.read_witness(name).get("status") == "unavailable", "前提不成立"

    _enter_witness_flow(name)
    r = _run_hook(name)                  # 03a 见证真实的红
    assert r.returncode == 0, f"有效的红被拒:\n{r.stdout}"

    record = rw.read_witness(name)
    assert record.get("witnessed_at"), "前提不成立：未见证"
    assert record.get("status") != "unavailable", (
        f"见证到红之后 status 仍是陈旧的 unavailable: {record}")


def test_status_is_ok_after_green(task):
    """转绿后 status 必须是 ok —— 下游据此报 ✅ 而不是 ❓。"""
    name, target = task("rw-status-green", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _run_hook(name)
    _enter_witness_flow(name)
    _run_hook(name)

    (target / "impl.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    r = _run_hook(name)
    assert r.returncode == 0, f"转绿被拒:\n{r.stdout}"

    record = rw.read_witness(name)
    assert record.get("green_at"), "前提不成立：未转绿"
    assert record.get("status") == "ok", (
        f"已见证并转绿，status 却不是 ok: {record}")
    assert not record.get("unavailable_reason"), \
        "status 已 ok，却还留着 unavailable 的原因串"


def test_unavailable_still_wins_when_nothing_was_witnessed(task):
    """反向保险：没见证过的任务不许因为这次修正被写成 ok。

    清理陈旧值的正确方向是「见证发生时才转 ok」，
    而不是「把 unavailable 一律去掉」—— 后者会把 ❓ 变成假 ✅。
    """
    name, _ = task("rw-status-none", {
        "test_ok.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
        "mod.py": "def f():\n    return 1\n",
    })
    r = _run_hook(name)

    assert r.returncode == 0, r.stdout
    record = rw.read_witness(name)
    assert record.get("status") == "unavailable", record
    assert not record.get("witnessed_at"), "未见证却写了 witnessed_at"


def test_rewitness_clears_stale_green_status(task):
    """回退到 03a 后，status 不得还停在上一轮的 ok。

    否则「已转绿」这个结论会在测试被解冻、红尚未重新见证的窗口里继续有效。
    """
    name, target = task("rw-status-rewitness", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    _run_hook(name)
    (target / "impl.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _run_hook(name)
    assert rw.read_witness(name).get("status") == "ok", "前提不成立：未转绿"

    subprocess.run(["python3", "-m", "sw_lib.workflow.red_witness", name,
                    "--rewitness", "断言的期望值写错了"],
                   cwd=str(ROOT), capture_output=True, text=True, timeout=60)

    record = rw.read_witness(name)
    assert record.get("status") != "ok", (
        f"回退到 03a 后 status 仍宣称 ok: {record}")
