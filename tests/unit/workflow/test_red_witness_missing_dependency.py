"""缺依赖导致的收集期报错，必须诊断为「缺依赖」而不是「造红」（任务 8090）。

现场：agent 在 03-coding 写完整套 BBS 应用，依赖声明在 `requirements.txt`
（Flask 等）。它建 venv 装依赖的那条命令跑了 7 分 22 秒被钩子超时砍掉，
`.venv` 没建成，agent 却没收到失败信号，交出「23 个测试全部通过」。

门禁跑 pytest，收集期 `ModuleNotFoundError: No module named 'flask'`，
退出码 2，判「造红」，并把 `--abandon-witness` 列为唯一出路。

**两条路通向同一堵墙。** 实测走了那条出路：放弃见证后 phase 抹回 `none`，
测试判定交回 `run_project_tests`，它跑同一份 pytest，撞同一个
ModuleNotFoundError。用户不存在能走通的下一步 —— 死锁。

与 A0 的 2.9.11（O6 死锁）**不同型**：那次是没有任何角色能满足判据，
这次判据能被满足（装上依赖就见证得到红），但**失败信息指错了方向**。
按 criterion-design 的失败信息三要件，缺的是第三条：一个真实角色在真实
阶段能执行的下一步。此处应为「装依赖」，而门禁说的是「改 import」。

同形先例在 `red_witness.py` 退出码 4 的分支里已经判过一次：conftest 塌了
被兜底说成「无测试」，把人推去补测试，而该补的是 `main.py`。本次是同一
形状的第二个实例 —— 退出码 2 的分支把所有收集期报错一律说成「造红」，
但「缺第三方依赖」与「自写模块 import 错了 / 语法错」性质不同，
需要的下一步动作也不同。

判据是**行为**：缺依赖时门禁要说出缺哪个包、怎么装；而真正的造红
（自写模块不存在）必须原样判「造红」，不许被这条新路放宽。

---

## 本文件的一条断言已按 DEV-PROTOCOL 1.2 显式重做（勿静默回退）

初版把「不许说造红」写成 `"造红" not in stdout`。**这个判据本身是错的**：
修复后的文案里有「这**不是**造红 —— 代码没问题，是环境里没装依赖」，
子串判定抓的是字面，不是语义，于是一句说对了的话被判成说错了。

重做后改断**造红诊断特有的那句指令**：「红必须是断言失败，不是 import 失败」。
它才是会把人推去改 import 的那句话，也正是缺依赖时不该出现的东西。

被改的是「怎么量」，不是「量什么」—— 契约一字未动：缺依赖不许被诊断成
造红、必须说出包名、必须给出能执行的装法。
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
    """理由同 test_red_witness_gate.py：mock 模式不执行真实见证。"""
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
    """走真实 pre hook 进入 03a —— 签名跨进程，不能在测试进程里写 .state。"""
    return subprocess.run(
        [str(HOOKS_DIR / "pre_check_03-coding.sh"), task_name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def task(tmp_path):
    created = []

    def _make(name, files, witness=True):
        target = tmp_path / name
        for rel, body in files.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        d = _make_task(name, target)
        created.append(d)
        if witness:
            _enter_witness_flow(name)
        return name, target

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


#: 「造红」诊断特有的那句指令。它是会把人推去改 import 的话 ——
#: 缺依赖时不该出现，真造红时必须出现。
#: 不用「造红」二字本身当标记：修复后的文案里有「这不是造红」，
#: 子串判定会把一句说对了的话判成说错了（见文件顶部的重做声明）。
_FAKE_RED_VERDICT = "红必须是断言失败，不是 import 失败"


#: 任务 8090 的形状：依赖在 requirements.txt 里声明了，但没装。
#: 用 `flask` 是刻意的 —— 本机实测未安装。
_MISSING_DEP = {
    "requirements.txt": "Flask==3.1.1\npytest==8.4.1\n",
    "app.py": "from flask import Flask\n\n\ndef create_app():\n    return Flask(__name__)\n",
    "tests/test_app.py": (
        "from app import create_app\n\n\n"
        "def test_app_is_created():\n    assert create_app() is not None\n"
    ),
}


# ── 前提：这个形状确实撞在退出码 2 上 ──

def test_missing_dependency_really_produces_collection_error(tmp_path):
    """前提自检：缺依赖走的确实是退出码 2 这条分支。

    不做这一步，后面的断言就可能在测别的东西 —— A2 的 10.3 要求
    pytest 的真实行为不得 mock。
    """
    proj = tmp_path / "p"
    for rel, body in _MISSING_DEP.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    exit_code, nodes = rw.run_tests(proj)
    assert exit_code == rw.EXIT_COLLECTION_ERROR, \
        f"前提不成立：缺依赖应为退出码 2，实际 {exit_code}"
    assert not nodes, f"收集期就塌了，不该有节点结果: {nodes}"


# ── 判据本体：缺依赖必须被诊断成缺依赖 ──

def test_missing_dependency_is_not_called_fake_red(task):
    """缺依赖不是「造红」。

    「造红」的意思是 agent 用一个 import 不存在的模块伪造红。缺依赖是
    环境没装好 —— 代码本身没问题。两者的下一步动作完全不同：
    前者改代码，后者装依赖。说错了，人就会去改本来没错的 import。
    """
    name, _ = task("rw-dep-not-fake-red", _MISSING_DEP)
    r = _run_hook(name)

    assert r.returncode != 0, f"缺依赖仍应拒绝（测试根本没跑）:\n{r.stdout}"
    assert _FAKE_RED_VERDICT not in r.stdout, \
        f"缺依赖被误诊为造红，会把人推去改本来没错的 import:\n{r.stdout}"


def test_missing_dependency_names_the_package(task):
    """必须说出缺的是哪个包 —— 「装依赖」四个字不够可操作。"""
    name, _ = task("rw-dep-names-pkg", _MISSING_DEP)
    r = _run_hook(name)

    assert "Flask" in r.stdout or "flask" in r.stdout, \
        f"没说出缺哪个包，用户仍得自己去翻 pytest 输出:\n{r.stdout}"


def test_missing_dependency_gives_an_executable_next_step(task):
    """必须给出一条能直接执行的装依赖命令（失败信息三要件的第三条）。

    这是死锁的解药：`--abandon-witness` 那条路解决不了缺依赖，
    因为放弃见证后常规门禁跑同一份 pytest、撞同一个 ModuleNotFoundError。
    """
    name, _ = task("rw-dep-next-step", _MISSING_DEP)
    r = _run_hook(name)

    assert "pip install" in r.stdout, \
        f"拒绝了却没给能执行的下一步，这正是 8090 死锁的形状:\n{r.stdout}"


# ── 边界：判据不得恒真，真正的造红必须原样被拦 ──

def test_genuine_fake_red_is_still_called_fake_red(task):
    """自写模块不存在 = 真正的造红，不许被缺依赖诊断放宽。

    这条是防止修复把判据改恒真：若「凡收集期报错都说缺依赖」，
    A2 的造红拦截就整个失效了。
    """
    name, _ = task("rw-dep-genuine-fake-red", {
        "test_y.py": "import nosuchmod_xyz123\n\n\ndef test_x():\n    assert True\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0, f"造红竟然过闸了:\n{r.stdout}"
    assert _FAKE_RED_VERDICT in r.stdout, \
        f"真正的造红没被识别，A2 的核心拦截失效:\n{r.stdout}"
    assert "pip install" not in r.stdout, \
        f"造红被误诊成缺依赖，会把人推去装一个根本不存在的包:\n{r.stdout}"


def test_declared_but_installed_dependency_is_not_reported_missing(task):
    """已装的依赖不算缺 —— 否则每个声明了依赖的项目都会被误诊。

    `pytest` 必然已安装（正在跑它）。声明它却把它报成缺失，
    说明判据在瞎报。
    """
    name, _ = task("rw-dep-installed-not-missing", {
        "requirements.txt": "pytest\n",
        "test_y.py": "import nosuchmod_xyz123\n\n\ndef test_x():\n    assert True\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0
    assert "pip install" not in r.stdout, \
        f"依赖已装却被报成缺失:\n{r.stdout}"
    assert _FAKE_RED_VERDICT in r.stdout, \
        f"依赖都装齐了，收集期报错就是造红:\n{r.stdout}"


# ── 同型清扫：conftest 缺依赖走的是退出码 4，同一形状的兄弟实例 ──

def test_missing_dependency_in_conftest_also_gets_install_hint(task):
    """依赖缺在 `conftest.py` 里时 pytest 以 4 退出，同样要指向装依赖。

    本机实测（见 commit body）：`tests/conftest.py` 写 `import flask` 而
    flask 未装 → 退出码 4，不是 2。退出码 4 的分支原先一律说
    「conftest.py 导入失败或命令行/配置有误」，把人推去改 conftest ——
    该装的是依赖。

    只修退出码 2 就留下这个兄弟实例，下次会以另一副面孔复发。
    """
    name, _ = task("rw-dep-conftest-rc4", {
        "requirements.txt": "Flask==3.1.1\n",
        "tests/conftest.py": "import flask\n",
        "tests/test_a.py": "def test_x():\n    assert True\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0, f"conftest 缺依赖竟然过闸了:\n{r.stdout}"
    assert "pip install" in r.stdout, \
        f"conftest 缺依赖没给装依赖的下一步（同一形状的兄弟实例）:\n{r.stdout}"
    assert "Flask" in r.stdout or "flask" in r.stdout, r.stdout


def test_conftest_error_without_missing_deps_keeps_old_diagnosis(task):
    """依赖齐全时，退出码 4 仍按「conftest 导入失败」诊断。

    边界的另一半：不能因为加了缺依赖这条路，就把所有 rc=4 都说成缺依赖。
    """
    name, _ = task("rw-dep-conftest-genuine", {
        "requirements.txt": "pytest\n",
        "tests/conftest.py": "from main import app\n",
        "tests/test_a.py": "def test_x():\n    assert True\n",
    })
    r = _run_hook(name)

    assert r.returncode != 0
    assert "pip install" not in r.stdout, \
        f"依赖齐全却被说成缺依赖:\n{r.stdout}"


# ── 两侧必须一起修：出路那一端也要给对下一步 ──
#
# `7ee1928` 记过这个陷阱：只修一侧会把「误拦」翻成「放过坏代码」。
# 这里的形状是另一种 —— 见证侧修好了，但 `--abandon-witness` 之后判定
# 交回 `run_project_tests`，那边仍只回显原始 pytest 输出。出路走到头
# 还是看不懂为什么失败，死锁只是从一堵墙挪到另一堵墙。

def _lib(snippet):
    """在 lib_run_tests.sh 的语境里跑一段 shell。"""
    return subprocess.run(
        ["bash", "-c", f". hooks/lib_run_tests.sh\n{snippet}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def test_run_project_tests_also_reports_missing_dependency(tmp_path):
    """常规门禁侧撞上缺依赖时，同样要指出缺哪个包、怎么装。

    这是 `--abandon-witness` 走到头的那一端。见证侧说清楚了、这边没说，
    用户放弃见证之后照样卡住 —— 8090 死锁的第二堵墙。
    """
    proj = tmp_path / "p"
    (proj / "tests").mkdir(parents=True)
    (proj / "requirements.txt").write_text("Flask==3.1.1\n", encoding="utf-8")
    (proj / "app.py").write_text(
        "from flask import Flask\n", encoding="utf-8")
    (proj / "tests" / "test_app.py").write_text(
        "from app import Flask\n\n\ndef test_x():\n    assert True\n",
        encoding="utf-8")

    r = _lib(f'run_project_tests "{proj}" "pytest 失败"')

    assert r.returncode != 0, f"缺依赖竟然过闸了:\n{r.stdout}"
    assert "pip install" in r.stdout, \
        f"常规门禁侧没给装依赖的下一步，出路走到头仍是死路:\n{r.stdout}"
    assert "Flask" in r.stdout or "flask" in r.stdout, r.stdout


def test_run_project_tests_normal_failure_has_no_install_hint(tmp_path):
    """普通的测试失败不该被扯上依赖 —— 判据不得恒真。"""
    proj = tmp_path / "p"
    (proj / "tests").mkdir(parents=True)
    (proj / "tests" / "test_a.py").write_text(
        "def test_boom():\n    assert 1 == 2\n", encoding="utf-8")

    r = _lib(f'run_project_tests "{proj}" "pytest 失败"')

    assert r.returncode != 0
    assert "pip install" not in r.stdout, \
        f"普通断言失败被误诊成缺依赖:\n{r.stdout}"
