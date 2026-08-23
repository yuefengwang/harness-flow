"""phase 必须是**显式状态**，不能从测试结果反推。

这条约束是实施期实测撞出来的，值得单独固化。

起初 `_check_03a` 的判定是「测试红 → 见证成功；测试绿 → 拒绝」，
于是既有契约 `test_real_failure_still_blocks`（「失败的测试不许过闸」）
与新契约「失败的测试正是有效的红」**对同一个输入给出相反期望**，
实测该用例转红：

    ❌ 失败的测试竟然过闸了:
       ✅ 已见证有效的红（退出码 1）

两条都对，冲突不在断言而在设计：**从测试结果反推子阶段是不可能的** ——
「测试红」既可能是「03a 刚写完测试」，也可能是「03b 实现没写对」，
两者的正确处置恰好相反（放行 / 拦住）。

因此 phase 必须由 harness 显式记录：`.state` 里没有见证记录时，
03 阶段只有在**明确进入见证流程**后才按 03a 判定。
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

    def _make(name, files, witness_phase=None):
        target = tmp_path / name
        for rel, body in files.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": name, "stage": "03-coding", "stage_idx": 2,
            "stage_status": "running", "target_dir": str(target),
        }
        (d / ".state").write_text(json.dumps(payload), encoding="utf-8")
        if witness_phase:
            _enter_witness_flow(name)
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
    """走真实 pre hook 进入 03a —— 这是 phase 在生产中的唯一来源。

    实测踩到的坑：在测试进程里直接调 `begin_test_phase()` 会写出一份用
    **tmp 密钥**签名的 `.state`（conftest 的 `_isolate_evidence_key` 把
    `KEY_PATH` 重定向了），而钩子是独立子进程、读真实 `config/.evidence_key`，
    于是每条用例都红在

        ❌ 证据签名校验未通过（tampered）

    上 —— 红的原因是密钥不一致，与被测的 phase 分流毫无关系。
    走 pre hook 让写入与校验落在同一个密钥域内，顺带验证了那条接线真的通。
    """
    return subprocess.run(
        [str(HOOKS_DIR / "pre_check_03-coding.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


def test_failing_tests_block_when_not_in_witness_flow(task):
    """未进入见证流程时，失败的测试仍必须拦住（既有契约不得被推翻）。

    这正是 `test_real_failure_still_blocks` 表达的契约。
    """
    name, _ = task("rw-phase-nowitness", {
        "src/mypkg/__init__.py": "def f():\n    return 1\n",
        "tests/test_it.py": "from mypkg import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    })
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"未进入见证流程时失败的测试竟然过闸了:\n{r.stdout}"


def test_failing_tests_are_valid_red_inside_witness_flow(task):
    """已显式进入见证流程（03a）时，同样的失败测试就是有效的红。

    输入完全相同、期望相反 —— 区别只在 phase 是否被显式设定。
    这就是为什么 phase 不能靠反推。
    """
    name, _ = task("rw-phase-witness", {
        "src/mypkg/__init__.py": "def f():\n    return 1\n",
        "tests/test_it.py": "from mypkg import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    }, witness_phase="03a")
    r = _run_hook(name)

    assert r.returncode == 0, f"见证流程内的有效红被拒:\n{r.stdout}"
    assert rw.read_witness(name).get("failed_nodes") == \
        ["tests/test_it.py::test_f"], rw.read_witness(name)


def test_begin_test_phase_is_idempotent(task):
    """重复进入 03a 不得清掉已有的见证记录。

    pre hook 每次启动阶段都会调它；若不幂等，03b 返工重跑会把冻结哈希抹掉，
    「改测试让它过」就重新变成一条免费路径。
    """
    name, _ = task("rw-phase-idem", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    }, witness_phase="03a")
    assert _run_hook(name).returncode == 0
    frozen = dict(rw.read_witness(name)["test_files"])
    assert rw.read_phase(name) == "03b"

    _enter_witness_flow(name)

    assert rw.read_phase(name) == "03b", "重复进入 03a 把已见证的记录退回去了"
    assert rw.read_witness(name)["test_files"] == frozen, "冻结哈希被抹掉"


def test_green_tests_without_witness_flow_pass(task):
    """未进入见证流程且测试全绿 → 放行（存量任务、返工轮次）。"""
    name, _ = task("rw-phase-green", {
        "mod.py": "def f():\n    return 1\n",
        "test_mod.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    })
    r = _run_hook(name)
    assert r.returncode == 0, f"存量任务被堵死:\n{r.stdout}"


def test_no_tests_without_witness_flow_is_not_blocked(task):
    """未进入见证流程、且项目里根本没有测试 → 不由见证机制拦。

    「有产出但没测试」由既有的 has_code_output / run_project_tests 负责，
    见证机制不该越界接管它（实测曾误拦 `.env.example` 那条既有用例）。
    """
    name, _ = task("rw-phase-notests", {".env.example": "KEY=\n"})
    r = _run_hook(name)
    assert r.returncode == 0, f"见证机制越界拦了无测试的项目:\n{r.stdout}"


def test_no_tests_inside_witness_flow_is_rejected(task):
    """但**已进入 03a** 却没写测试，必须拦 —— 那是 03a 的本职。"""
    name, _ = task("rw-phase-notests-in", {"mod.py": "def f():\n    return 1\n"},
                   witness_phase="03a")
    r = _run_hook(name)
    assert r.returncode != 0, f"03a 阶段没写测试却过闸了:\n{r.stdout}"
    assert "无测试" in r.stdout or "测试文件" in r.stdout, r.stdout
