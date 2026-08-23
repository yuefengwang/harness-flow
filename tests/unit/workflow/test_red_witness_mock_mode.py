"""A2 第 7 节：mock 模式的合成见证不得放开既有的测试门禁。

设计原文写的是「mock 模式下写入合成 `red_witness`，**跳过真实测试执行与
哈希校验**」。照字面实现后，实测在 mock 模式下跑全量出现 20 条红，其中

    tests/unit/workflow/test_hook_pytest_invocation.py
        ::test_real_failure_still_blocks[check_03-coding.sh]

是致命的一条：**失败的测试在 mock 模式下能过闸。**

成因是「跳过」的范围被理解得太宽 —— 合成见证顺手接管了整个 03 门禁并
直接放行，于是 `run_project_tests` 也被跳过了。而 mock 的意图只是
「不要求红绿流程真的走过一遍」，从来不是「不要求测试通过」。

这个洞很难被发现：mock 是 CI 主力，一旦它把测试门禁放开，
CI 会对任何坏实现都报绿。因此这里单独一个文件锁住它。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS
from sw_lib.workflow import red_witness as rw

ROOT = Path(__file__).resolve().parents[3]


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


@pytest.fixture
def mock_mode():
    """让钩子子进程看到 mock 模式，用完**务必**还原。

    钩子是独立进程、读的是 `config/config.yaml` 里的 `mock_agent.enabled`，
    而 `config.py:121` 把这个路径硬编码了 —— 没有环境变量入口，
    `monkeypatch` 也够不到子进程。为了一条测试去给生产代码加一个
    配置覆盖入口，代价比收益大，所以这里改真实文件再还原。

    还原放在 `try/finally` 里：中途失败若把仓库留在 mock 开启的状态上，
    用户下一次真实运行会静默走 MockAgent —— 这种故障几乎不可能被联想到
    是测试留下的。
    """
    cfg = ROOT / "config" / "config.yaml"
    original = cfg.read_text(encoding="utf-8")
    # 已经开着就什么都不改（有人真的在 mock 下跑 CI）。断言「必须找到
    # enabled: false」会让这个文件在 mock 模式下整体 error —— 那是把
    # 「前提已满足」当成了故障。
    if "  mock_agent:\n    enabled: false" in original:
        cfg.write_text(
            original.replace("  mock_agent:\n    enabled: false",
                             "  mock_agent:\n    enabled: true", 1),
            encoding="utf-8")
    try:
        yield cfg
    finally:
        cfg.write_text(original, encoding="utf-8")


def _run_hook(name):
    return subprocess.run(
        ["bash", str(HOOKS_DIR / "check_03-coding.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def test_mock_mode_does_not_let_failing_tests_pass(task, mock_mode):
    """mock 模式下失败的测试仍必须被拦住。

    这正是实测转红的那条既有契约。mock 免的是「红绿流程要真的走过」，
    不是「测试要通过」。
    """
    name, _ = task("rw-mock-fail", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"mock 模式下失败的测试过闸了 —— CI 会对坏实现报绿:\n{r.stdout}"


def test_mock_mode_passes_green_tests(task, mock_mode):
    """对照：mock 模式下测试是绿的就该放行，不能连正路一起堵上。"""
    name, _ = task("rw-mock-green", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    })
    r = _run_hook(name)

    assert r.returncode == 0, f"mock 模式下正常项目被堵:\n{r.stdout}"


def test_mock_witness_keeps_field_shape(task, mock_mode):
    """合成记录必须保留字段形状，且标注 `mock: true`（A2 第 7 节）。

    下游 A3 / A6 / A10 直接读这些字段。形状不一致的话，
    mock 会掩盖下游的字段缺失 —— CI 全绿而真实模式一跑就崩。
    """
    name, _ = task("rw-mock-shape", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    })
    assert _run_hook(name).returncode == 0

    record = rw.read_witness(name)
    assert record.get("mock") is True, f"未标注 mock: {record}"
    for key in ("phase", "witnessed_at", "exit_code", "failed_nodes",
                "test_files", "green_at", "green_nodes", "rewitness_count"):
        assert key in record, f"合成记录缺字段 {key}: {record}"


def test_mock_record_is_distinguishable_from_real_witness(task, mock_mode):
    """合成记录不得被下游误读成「真的见证过」。

    A2 的 R2/R3：没做到的事不能记成做到了。`mock: true` 是唯一的区分标记，
    下游据此把该项报为 ❓ 而不是 ✅。
    """
    name, _ = task("rw-mock-honest", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    })
    assert _run_hook(name).returncode == 0

    record = rw.read_witness(name)
    assert record.get("mock") is True
    assert not record.get("failed_nodes"), \
        "合成记录编造了判据节点 —— 那会被 A6 的 O4 当成真实判据"
