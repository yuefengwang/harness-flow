"""A2 的 R2：非 Python 项目不支持 pytest 语义，见证必须让路而不是卡死。

设计原文：「第一批只支持 pytest。npm 项目跳过见证并在事实包标注
`red_witness: unavailable`（不算通过，对应 A6 三态）」。

这批用例是实施期实测出缺口后补的（见 A2 的 10.6 第七行）：
纯 npm 项目在 03a 会撞上「无测试：必须先写测试文件」的硬拒绝，
而 `hash_test_files` 只认 `.py` —— agent 写多少 `*.test.js` 都不会被看见，
于是 03 阶段**永久无法准出**。这不是「跳过见证」，是把整条路堵死。

两条契约缺一不可：

1. 见证让路（放行 + 记 `unavailable`），否则任务卡死；
2. 让路之后 phase 必须是 `none`，测试判定交回 `run_project_tests` ——
   否则 npm 项目里失败的测试会过闸，重演 10.6 第二行那个洞。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS, is_mock_agent
from sw_lib.workflow import red_witness as rw

ROOT = Path(__file__).resolve().parents[3]

_FAILING_JS = 'process.exit(1);\n'
_PASSING_JS = 'process.exit(0);\n'


def _pkg(script="node test.js"):
    return json.dumps({"name": "p", "version": "1.0.0",
                       "scripts": {"test": script}}) + "\n"


@pytest.fixture(autouse=True)
def _requires_real_agent_mode():
    """mock 模式下 `check_gate` 只写合成记录，本文件的前提不成立。"""
    if is_mock_agent():
        pytest.skip("mock 模式不执行真实见证；契约见 test_red_witness_mock_mode.py")


@pytest.fixture(autouse=True)
def _requires_npm():
    """npm 缺失时判据不可得 —— 记 ❓ 跳过，不假装通过（DEV-PROTOCOL 第 2 节）。"""
    if shutil.which("npm") is None:
        pytest.skip("本机无 npm，无法验证非 Python 项目路径")


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


def _phase_cli(task_name):
    r = subprocess.run(
        ["python3", "-m", "sw_lib.workflow.red_witness", task_name, "--phase"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    return r.stdout.strip()


@pytest.fixture
def task(tmp_path):
    """建一个非 Python 项目的 03-coding 任务并走真实 pre hook 进入见证流程。"""
    created = []

    def _make(name, files):
        target = tmp_path / name
        for rel, body in files.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        created.append(_make_task(name, target))
        # 必须走真实 pre hook：测试进程的证据密钥被 conftest 重定向到 tmp，
        # 在进程内调 begin_test_phase 会让钩子把 .state 判成 tampered。
        subprocess.run([str(HOOKS_DIR / "pre_check_03-coding.sh"), name],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        return name, target

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def test_npm_project_is_not_blocked_by_missing_pytest_files(task):
    """R2：纯 npm 项目不得被「无测试」拒绝 —— 那会让 03 永久无法准出。

    它的测试是 `*.test.js`，`hash_test_files` 永远返空，
    因此这条路上不存在「补上测试就能过」的自救办法。
    """
    name, _ = task("rw-npm-passing", {
        "package.json": _pkg(),
        "test.js": _PASSING_JS,
        "index.js": "module.exports = () => 1;\n",
    })
    r = _run_hook(name)

    assert r.returncode == 0, f"npm 项目被见证机制堵死:\n{r.stdout}\n{r.stderr}"


def test_npm_project_is_recorded_unavailable_not_witnessed(task):
    """放行不等于见证过：必须留下 `unavailable` + 原因（A2 的 R2 三态）。"""
    name, _ = task("rw-npm-unavail", {
        "package.json": _pkg(),
        "test.js": _PASSING_JS,
        "index.js": "module.exports = () => 1;\n",
    })
    _run_hook(name)
    record = rw.read_witness(name)

    assert record.get("status") == "unavailable", record
    assert record.get("unavailable_reason"), "unavailable 必须带原因"
    assert not record.get("witnessed_at"), "从未见证却写了 witnessed_at"
    assert not record.get("green_at"), "从未转绿却写了 green_at"


def test_npm_project_phase_falls_back_to_none(task):
    """让路后 phase 必须是 `none`，钩子才会把测试判定交回 run_project_tests。

    留在 `03a` 会让钩子跳过 `run_project_tests` —— npm 的失败测试就此过闸，
    重演 10.6 第二行那个「mock 下失败的测试能过闸」的洞。
    """
    name, _ = task("rw-npm-phase", {
        "package.json": _pkg(),
        "test.js": _PASSING_JS,
    })
    _run_hook(name)

    assert _phase_cli(name) == "none", "非 Python 项目仍停在见证子阶段"


def test_npm_failing_tests_still_block(task):
    """让路**不等于**放水：npm 测试真的失败时必须仍然拦住。

    这是本文件最重要的一条 —— 「跳过见证」的正确含义是
    「红绿流程未被观测」，不是「测试结果不再重要」。
    """
    name, _ = task("rw-npm-failing", {
        "package.json": _pkg(),
        "test.js": _FAILING_JS,
    })
    r = _run_hook(name)

    assert r.returncode != 0, f"npm 测试失败竟然过闸了:\n{r.stdout}"
    # 拦住的**理由**也是契约：必须是「npm test 失败」，不是「没写 pytest 文件」。
    # 只断言退出码非 0 的话，当前那个「无测试」硬拒绝也会让这条变绿 ——
    # 拿一个卡死当成拦截力，正是 DEV-PROTOCOL 1.1 说的假绿。
    assert "无测试" not in r.stdout, (
        f"拦住的理由是缺 pytest 文件而不是 npm 测试失败:\n{r.stdout}")
    assert "npm" in r.stdout, f"未见 npm test 的执行痕迹:\n{r.stdout}"


def test_mixed_project_with_python_tests_is_still_witnessed(task):
    """混合项目里有 Python 测试时，见证照常进行 —— 让路只针对「没有 pytest 判据」。"""
    name, _ = task("rw-npm-mixed", {
        "package.json": _pkg(),
        "test.js": _PASSING_JS,
        "test_impl.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    r = _run_hook(name)

    assert r.returncode == 0, f"混合项目的有效红被拒:\n{r.stdout}"
    record = rw.read_witness(name)
    assert record.get("failed_nodes") == ["test_impl.py::test_f"], record
    assert record.get("phase") == "03b", "混合项目未进入 03b"
