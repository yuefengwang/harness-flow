"""A2 第 5 节：03 阶段门禁按 `red_witness.phase` 分流。

这批用例**实跑真实钩子**（`bash hooks/check_03-coding.sh`），断行为而不是
断脚本文本 —— 脚本形状能写出无数种，能过闸和不能过闸才是要锁住的契约
（承自 `test_hook_pytest_invocation.py` 的做法）。

复用 `lib_run_tests.sh` 是硬要求（A2 的 5.3）：src-layout 的 PYTHONPATH
与项目自带 `.venv` 是任务 T2 / T3 真实踩过的坑，不复用会重现
「门禁与 agent 对同一份代码给出相反结论」。
"""

import json
import shutil
import subprocess
import sys
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
    """走真实的 pre hook 进入 03a，而不是在测试进程里调 `begin_test_phase`。

    两个理由，都是实测踩出来的：

    1. **签名跨进程。** 测试进程的密钥被 `conftest._isolate_evidence_key`
       重定向到 tmp，而钩子是独立子进程、用真实 `config/.evidence_key`。
       在测试进程里写 `.state` 会让钩子把它判成 `tampered`，
       红的原因就变成了密钥不一致，而不是被测的行为。
    2. **这才是生产路径。** phase 的唯一来源是
       `hooks/pre_check_03-coding.sh`（`base.py` 的 `_run_pre_hooks` 调用它）。
       从这里进入，顺带验证了那条接线真的通。
    """
    return subprocess.run(
        [str(HOOKS_DIR / "pre_check_03-coding.sh"), task_name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def task(tmp_path):
    """建一个带 target_dir 的 03-coding 任务并进入 03a，用完清理。"""
    created = []

    def _make(name, files, witness=True):
        target = tmp_path / name
        for rel, body in files.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        d = _make_task(name, target)
        created.append(d)
        # 默认显式进入见证流程：本文件测的全是 03a / 03b 的判定，
        # 而 phase 不再有「无记录即 03a」的默认值 —— 那条设计已被实测推翻
        # （见 test_red_witness_phase_explicit.py 的模块 docstring）。
        if witness:
            _enter_witness_flow(name)
        return name, target

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


# ── 03a：见证红 ──

def test_03a_valid_red_passes_and_records(task):
    """验收 1：写了失败测试（退出码 1）可准出，failed_nodes 非空。"""
    name, _ = task("rw-gate-red", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    r = _run_hook(name)

    assert r.returncode == 0, f"有效的红被拒:\n{r.stdout}\n{r.stderr}"
    record = rw.read_witness(name)
    assert record.get("failed_nodes") == ["test_y.py::test_f"], record
    assert record.get("phase") == "03b", "见证后未推进到 03b"
    assert "test_y.py" in record.get("test_files", {}), "测试文件未被冻结"


def test_03a_collection_error_is_rejected(task):
    """验收 2：ImportError（退出码 2）被拒，提示「造红」。"""
    name, _ = task("rw-gate-fakered", {
        "test_y.py": "import nosuchmod_zz\n\n\ndef test_x():\n    assert True\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0, f"造红竟然过闸了:\n{r.stdout}"
    assert "造红" in r.stdout, r.stdout
    assert rw.read_phase(name) == "03a", "造红之后 phase 竟然前进了"


def test_03a_no_tests_is_rejected(task):
    """验收 3：无测试（退出码 5）被拒。"""
    name, _ = task("rw-gate-notests", {"impl.py": "def f():\n    return 1\n"})
    r = _run_hook(name)

    assert r.returncode != 0, f"没有测试竟然过闸了:\n{r.stdout}"
    assert "无测试" in r.stdout or "测试文件" in r.stdout, r.stdout


def test_03a_all_passing_is_rejected(task):
    """验收 4：测试全通过时无法见证红。

    **本用例已按 DEV-PROTOCOL 1.2 显式重做**（原文用「实现已落盘 → 全绿」
    这一形态，它现在由 `_check_impl_first_bypass` 自动让路并记 unavailable，
    见 test_red_witness_post_hoc.py 与 A2 的 10.6）。

    重做后靶子换成**自证测试**（不依赖任何被测代码）：验收 4 要守的是
    「全绿不等于见证到红」，这一点未变；被降级的只是 R4 那一支成因 ——
    `bash` 绕过我们确实拦不住，于是改为事后如实记录而非拒绝。
    """
    name, _ = task("rw-gate-allpass", {
        "test_y.py": "def test_f():\n    assert 1 == 1\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0, f"全部通过却见证到了红:\n{r.stdout}"
    assert "未失败" in r.stdout or "无法见证" in r.stdout, r.stdout


def test_03a_impl_first_is_let_through_as_unavailable(task):
    """R4 的补充面：实现先落盘时让路，但**必须**记成 unavailable。

    与上一条成对存在，防止「降级」被读成「全绿都能过」：
    同样是退出码 0，有实现文件的让路、无实现文件的拒绝，两条都钉住。
    """
    name, _ = task("rw-gate-implfirst", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 1\n",
        "impl.py": "def f():\n    return 1\n",
    })
    r = _run_hook(name)

    assert r.returncode == 0, f"实现已落盘的 03a 仍被卡死:\n{r.stdout}"
    assert "unavailable" in r.stdout, r.stdout
    assert "impl.py" in r.stdout, f"让路时没指名实现文件:\n{r.stdout}"


def test_03a_all_skipped_is_rejected(task):
    """验收 5：全部 skip（退出码 **0**）被拒。"""
    name, _ = task("rw-gate-skip", {
        "test_y.py": "import pytest\n\n"
                     "@pytest.mark.skip(reason='x')\n"
                     "def test_s():\n    assert False\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0, f"全 skip 竟然过闸了:\n{r.stdout}"
    assert "skip" in r.stdout.lower(), r.stdout


# ── 03b：哈希校验 + 转绿 ──

def test_03b_green_with_untouched_tests_passes(task):
    """验收 7：实现正确、测试未改动时通过，green_nodes 覆盖 failed_nodes。"""
    name, target = task("rw-gate-green", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    assert _run_hook(name).returncode == 0, "03a 见证失败，后续无意义"

    (target / "impl.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode == 0, f"正确的实现被拒:\n{r.stdout}\n{r.stderr}"
    record = rw.read_witness(name)
    assert record.get("green_nodes") == ["test_y.py::test_f"], record
    assert record.get("failed_nodes") == ["test_y.py::test_f"], \
        "判据集在转绿后被覆盖（A2 的 4.1 要求它永久保留）"


def test_03b_modified_test_file_is_rejected(task):
    """验收 6：03b 改测试文件被拒，且提示具体文件名。"""
    name, target = task("rw-gate-tamper", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    assert _run_hook(name).returncode == 0

    # 改测试让它过 —— 最常见的自欺路径（A2 的 F4）
    (target / "test_y.py").write_text(
        "def test_f():\n    assert True\n", encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode != 0, f"改了测试却过闸了:\n{r.stdout}"
    assert "test_y.py" in r.stdout, f"未指出被改动的文件:\n{r.stdout}"


def test_03b_bypass_write_is_still_detected(task):
    """验收 10（有效性）：用 `python -c` 改测试文件，03b 仍能检出。

    A0 的验收 14 已证明 argv 前缀校验拦不住解释器写文件，
    因此这条验证的是准出时的哈希比对 —— 长期主防线（A2 的 3.3 末尾）。
    """
    name, target = task("rw-gate-bypass", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    assert _run_hook(name).returncode == 0

    victim = target / "test_y.py"
    subprocess.run(
        [sys.executable, "-c",
         f"open({str(victim)!r}, 'w').write('def test_f():\\n    assert True\\n')"],
        check=True, capture_output=True)
    r = _run_hook(name)

    assert r.returncode != 0, f"绕过写入未被检出 —— 主防线失守:\n{r.stdout}"
    assert "test_y.py" in r.stdout, r.stdout


def test_03b_skipping_witnessed_node_is_rejected(task):
    """A2 的 10.4 指定的红：已见证节点变成 skipped 时，03b 必须拒绝。

    构造方式是**从实现侧**让测试被跳过（`impl.py` 里调 `pytest.skip()`），
    而不是去改测试文件。这样：

    * 测试文件哈希完好 → 哈希校验放行，判据被单独隔出来；
    * 退出码是 **0** → 只看退出码的实现会当成「全部转绿」而放行。

    这也是 agent 在 03b 唯一还走得通的假绿路径（测试文件已被冻结），
    因此它比「改测试标 skip」更贴近真实威胁。
    本机实测该形态：`exit_code=0`，节点 outcome 为 `skipped`。
    """
    name, target = task("rw-gate-skipgreen", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    assert _run_hook(name).returncode == 0, "03a 见证失败，后续无意义"
    frozen_before = dict(rw.read_witness(name)["test_files"])

    (target / "impl.py").write_text(
        "import pytest\n\n\ndef f():\n    pytest.skip('环境不支持')\n",
        encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"已见证节点变成 skipped 却算转绿 —— 实现只看了退出码:\n{r.stdout}"
    assert "skip" in r.stdout.lower(), \
        f"拒绝理由未指出 skip 不是绿:\n{r.stdout}"
    assert rw.read_witness(name)["test_files"] == frozen_before, \
        "测试文件未被改动，冻结哈希不该变"


# ── 复用 lib_run_tests.sh 的既有能力（A2 的 5.3）──

def test_src_layout_project_is_witnessed(task):
    """src-layout（包未安装）必须能见证 —— 任务 T2 卡死的形状。"""
    name, _ = task("rw-gate-src", {
        "src/mypkg/__init__.py": "def f():\n    return 1\n",
        "tests/test_it.py": "from mypkg import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    })
    r = _run_hook(name)

    assert r.returncode == 0, f"src-layout 项目见证失败:\n{r.stdout}\n{r.stderr}"
    assert rw.read_witness(name).get("failed_nodes") == \
        ["tests/test_it.py::test_f"], rw.read_witness(name)


def test_project_venv_interpreter_is_used(task, tmp_path):
    """依赖装在项目 `.venv` 里时必须用那个解释器（任务 T3）。"""
    name, target = task("rw-gate-venv", {
        "app.py": "import onlyinvenv\n\n\ndef f():\n    return onlyinvenv.VALUE\n",
        "test_app.py": "from app import f\n\n\ndef test_f():\n    assert f() == 43\n",
    })
    site = target / ".venv" / "site"
    site.mkdir(parents=True)
    (site / "onlyinvenv.py").write_text("VALUE = 42\n", encoding="utf-8")
    bindir = target / ".venv" / "bin"
    bindir.mkdir(parents=True)
    shim = bindir / "python"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{site}:$PYTHONPATH"\n'
        f'exec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)

    r = _run_hook(name)
    # 用 venv 解释器才能 import onlyinvenv，于是红是断言失败（退出码 1）；
    # 用 harness 的 python3 会变成 collection error（退出码 2）→ 判为造红。
    assert r.returncode == 0, f"未使用项目 venv，见证被判为造红:\n{r.stdout}"
    assert rw.read_witness(name).get("exit_code") == 1, rw.read_witness(name)
