"""A2 的 R1：存量任务与返工轮次不得被见证机制堵死。

设计原文写的处置是「无记录时视为 03a，首次进入即开始见证；
不对历史任务追溯」。实施时发现这条**对已经写完实现的存量任务是死路** ——
它们的测试本来就是绿的，永远见证不到红，于是 03 阶段永久无法准出。

实测形态（引入见证后跑全量，9 条既有用例转红）：

    ❌ 未能见证有效的红（退出码 0）
       测试未失败（1 个全部通过），无法见证红。

被堵死的包括 `test_hook_pytest_invocation.py`、`test_hook_empty_output_gate.py`
与 04↔03 返工链路 —— 也就是**返工回到 03 的每一轮**都会被拦。
这不是测试写得不对，是机制会把正常的返工路径堵住。

**处置（定案，与本文件初版不同）**：不在 03a 内部「放宽」，而是新增
`phase == none` 这一态 —— 它表示「本任务未进入见证流程」。此时门禁：

* 如实记 `unavailable` + 原因（A2 的 R2/R3：不算通过，但也不能假装见证过）；
* **不接管测试执行** —— 能不能过闸仍由既有的 `run_project_tests` 决定。

初版把放宽写在 03a 内部（「无记录且测试全绿即放行」），实测有两个问题：
它要从测试结果反推子阶段（不可能，见 test_red_witness_phase_explicit.py），
且它顺手接管了测试判定，于是「无测试」「测试失败」这些既有契约
全被见证机制重新定义了一遍 —— 越界。

因此本文件中原先断言「无测试仍要拦」「真失败仍要拦」的两条用例已按
DEV-PROTOCOL 1.2 **显式重做**：那两条契约的归属方是
`has_code_output` / `run_project_tests`，见证机制只需保证不把它们放开。
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
    """本文件测的是**真实**见证行为，mock 模式下前提不成立。

    mock 下 `check_gate` 只写合成记录、不执行见证（A2 第 7 节），
    因此「见证到红」「哈希冻结」这类断言在 mock 下必然失败 ——
    那不是回归，是前提不同。跳过并说明原因，而不是让它红着
    （DEV-PROTOCOL 第 2 节：无法验证记 ❓，且必须说明为什么）。

    mock 模式自身的契约由 test_red_witness_mock_mode.py 覆盖。
    """
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


def _run_hook(task_name):
    return subprocess.run(
        ["bash", str(HOOKS_DIR / "check_03-coding.sh"), task_name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def _enter_witness_flow(task_name):
    """走真实 pre hook 进入 03a。

    不在测试进程里直接调 `begin_test_phase`：测试进程的签名密钥被
    conftest 重定向到 tmp，而钩子子进程用真实密钥 —— 那样写出的 `.state`
    会被钩子判成 `tampered`，红的原因就不是被测行为了。
    """
    return subprocess.run(
        [str(HOOKS_DIR / "pre_check_03-coding.sh"), task_name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


def test_legacy_task_with_green_tests_is_not_blocked(task):
    """存量任务（实现与测试都已写好、测试全绿）必须能准出。

    这是 9 条既有用例的共同形状。堵死它们等于让机制无法上线。
    """
    name, _ = task("rw-legacy-green", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    })
    r = _run_hook(name)

    assert r.returncode == 0, f"存量任务被见证机制堵死:\n{r.stdout}"


def test_legacy_pass_is_marked_unavailable_not_witnessed(task):
    """放行**不等于**见证过 —— 必须如实标注（A2 的 R2/R3 三态）。

    若把这种放行记成「已见证转绿」，下游 A6/A10 会读到一份假证据。
    """
    name, _ = task("rw-legacy-mark", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    })
    r = _run_hook(name)
    assert r.returncode == 0, r.stdout

    record = rw.read_witness(name)
    assert record.get("status") == "unavailable", \
        f"未如实标注为 unavailable: {record}"
    assert not record.get("green_at"), \
        "从未见证过红，却记了转绿时间戳 —— 这是一份假证据"
    assert not record.get("failed_nodes"), \
        f"从未见证过红，failed_nodes 却非空: {record.get('failed_nodes')}"
    assert "未见证" in r.stdout or "unavailable" in r.stdout.lower(), \
        f"放行时没有如实告知用户见证未发生:\n{r.stdout}"


def test_real_failure_still_blocks_after_relaxation(task):
    """放宽存量路径不得把真失败一起放开。

    ⚠️ 本条是对初版的**显式重做**（DEV-PROTOCOL 1.2）。初版断言
    「测试红着且无见证记录时应按 03a 见证并放行」，那是错的：
    未进入见证流程时，「测试红」只能解释为「实现坏了」，必须拦住 ——
    否则既有契约 `test_real_failure_still_blocks` 被推翻。

    现在的判据：`unavailable` 只表示见证未发生，不改变过闸结论；
    失败的测试由 `run_project_tests` 拦下。
    """
    name, _ = task("rw-legacy-red", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"未进入见证流程时失败的测试竟然过闸了:\n{r.stdout}"
    record = rw.read_witness(name)
    assert not record.get("failed_nodes"), \
        f"从未见证过红，却记下了判据节点（假证据）: {record.get('failed_nodes')}"
    assert not record.get("green_at"), "从未见证过红，却记了转绿时间戳"


def test_witnessed_task_still_enforces_hash_freeze(task):
    """已经见证过的任务不受这条放宽影响 —— 冻结仍然生效。

    这里必须先走 pre hook 显式进入 03a，因为 phase 不再有「无记录即 03a」
    的默认值。也正好验证：`unavailable` 这条放宽不会渗进已见证的任务。
    """
    name, target = task("rw-legacy-frozen", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0

    (target / "test_mod.py").write_text(
        "def test_f():\n    assert True\n", encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"见证过的任务被存量放宽路径绕过了哈希校验:\n{r.stdout}"
    assert "test_mod.py" in r.stdout, r.stdout


def test_no_tests_outside_witness_flow_is_not_reinterpreted(task):
    """未进入见证流程、项目里没有测试 → 见证机制**不接管**这个判定。

    ⚠️ 本条是对初版 `test_no_tests_at_all_is_still_rejected` 的
    **显式重做**（DEV-PROTOCOL 1.2）。初版要求「没有测试就拦」，
    与既有契约直接冲突：`test_hook_empty_output_gate.py` 的
    `test_hidden_source_file_counts_as_output` 要求「目录里只有
    `.env.example` 也算真实产出，必须放行」，而它显然没有测试。
    引入见证后那条既有用例实测转红 —— 是新机制越界，不是老契约错了。

    「有产出但没测试」归 `has_code_output` / `run_project_tests` 管。
    见证机制在此只需如实说明「见证未发生」，并把结论交回去。
    真正要拦的是**已进入 03a 却没写测试**，那条在
    test_red_witness_phase_explicit.py 里。
    """
    name, _ = task("rw-legacy-notests", {"mod.py": "def f():\n    return 1\n"})
    r = _run_hook(name)

    assert r.returncode == 0, f"见证机制越界拦了无测试的存量项目:\n{r.stdout}"
    assert rw.read_witness(name).get("status") == "unavailable", \
        rw.read_witness(name)
