"""A2 的 5.1：`hooks/pre_check_03-coding.sh` 是 phase 的唯一生产来源。

在这个文件出现之前，`begin_test_phase()` 没有任何生产调用点 ——
也就是说门禁里的 03a / 03b 分流在真实流程中**永远走不到**，
只有测试自己调过它。整套见证机制会以「unavailable」的形态静默常驻。

挂载点是 `base.py:479` 的 `_run_pre_hooks()`，它有两个坑（A2 已核实）：
失败被吞（`check=False` + `except: pass`）、超时仅 30 秒。
因此这个脚本只许做一件事：设 phase。跑测试留给 post 侧。
"""

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS, is_mock_agent
from sw_lib.workflow import red_witness as rw

ROOT = Path(__file__).resolve().parents[3]
PRE_HOOK = HOOKS_DIR / "pre_check_03-coding.sh"


@pytest.fixture(autouse=True)
def _requires_real_agent_mode():
    """真实见证行为的前提：非 mock 模式（理由同 test_red_witness_gate.py）。"""
    if is_mock_agent():
        pytest.skip("mock 模式不执行真实见证；相关契约见 test_red_witness_mock_mode.py")


@pytest.fixture
def task(tmp_path):
    created = []

    def _make(name, stage="03-coding", files=None):
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
            "id": name, "stage": stage, "stage_idx": 2,
            "stage_status": "running", "target_dir": str(target),
        }), encoding="utf-8")
        created.append(d)
        return name, target

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def _run_pre(name, timeout=30):
    """按 `_run_pre_hooks` 的真实方式调用：直接 exec 脚本，不加 `bash`。"""
    return subprocess.run(
        [str(PRE_HOOK), name], cwd=str(ROOT),
        capture_output=True, text=True, timeout=timeout,
    )


def test_pre_hook_exists_and_is_executable():
    """`_run_pre_hooks` 用 `[str(pre_script), task_name]` 直接 exec。

    少了执行位就是 PermissionError —— 而异常被 `except: pass` 吞掉，
    phase 永远不会被设上，且不留任何痕迹。这条锁的就是那种沉默失效。
    """
    assert PRE_HOOK.exists(), f"{PRE_HOOK} 不存在：phase 没有生产来源"
    mode = PRE_HOOK.stat().st_mode
    assert mode & stat.S_IXUSR, f"{PRE_HOOK} 缺少执行位（mode={oct(mode)}）"


def test_pre_hook_sets_phase_to_03a(task):
    """新任务进入 03-coding 时，phase 被设为 03a。"""
    name, _ = task("rw-pre-fresh")
    assert rw.read_phase(name) == "none"

    r = _run_pre(name)

    assert r.returncode == 0, f"pre hook 失败:\n{r.stdout}\n{r.stderr}"
    assert rw.read_phase(name) == "03a", rw.read_witness(name)


def test_pre_hook_is_idempotent_for_witnessed_task(task):
    """返工回到 03 时不得把已见证的任务退回 03a、不得抹掉冻结哈希。

    `_run_pre_hooks` 在**每次** invoke 开头执行，04→03 返工的每一轮都会调。
    不幂等就等于每轮返工都清一次冻结，「改测试让它过」重新畅通。
    """
    name, _ = task("rw-pre-idem")
    rw.record_red(name, rw.WitnessVerdict("ok", "", 1,
                                          failed_nodes=["t.py::test_a"]),
                  {"t.py": "a" * 64})

    r = _run_pre(name)

    assert r.returncode == 0, r.stderr
    assert rw.read_phase(name) == "03b", "已见证的任务被退回 03a"
    assert rw.read_witness(name)["test_files"] == {"t.py": "a" * 64}, \
        "冻结哈希被 pre hook 抹掉"


def test_pre_hook_does_not_run_tests(task):
    """pre hook 不许跑测试：失败被吞 + 30 秒超时（A2 的 5.1）。

    判据用时间而不是「输出里没有 pytest 字样」—— 后者靠重定向就能糊过去。
    放一个 `time.sleep(20)` 的测试进去：只要脚本真的跑了 pytest，
    耗时就会明显超过阈值。
    """
    name, _ = task("rw-pre-notests", files={
        "test_slow.py": "import time\n\n\ndef test_slow():\n"
                        "    time.sleep(20)\n    assert False\n",
    })

    started = time.monotonic()
    r = _run_pre(name)
    elapsed = time.monotonic() - started

    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert elapsed < 10, \
        f"pre hook 耗时 {elapsed:.1f}s —— 它疑似跑了测试，30 秒超时会被撞破"


def test_pre_hook_tolerates_unknown_task():
    """未知任务不得让脚本崩掉。

    失败虽被吞，但它会在 stderr 留一堆噪音，且掩盖真实问题。
    """
    r = _run_pre("rw-pre-no-such-task-zzz")
    assert r.returncode == 0, f"未知任务让 pre hook 失败:\n{r.stderr}"


def test_pre_hook_respects_kill_switch(task, monkeypatch):
    """开关关闭时 pre hook 不得设 phase（A2 第 11 节的回滚要求）。

    否则关掉开关后 `.state` 里仍长出 03a，而 post 侧已不再见证 ——
    留下一份「进入了见证流程却永不推进」的状态，`_stay_for_impl` 会误判。
    """
    name, _ = task("rw-pre-switch")
    env = dict(os.environ, HARNESS_RED_WITNESS="0")

    r = subprocess.run([str(PRE_HOOK), name], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=30)

    assert r.returncode == 0, r.stderr
    assert rw.read_phase(name) == "none", \
        f"开关关闭时仍设了 phase: {rw.read_witness(name)}"
