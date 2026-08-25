"""子目录布局：agent 报全绿，红见证报「造红」—— 任务 `welll` 的现场。

## 现场

`workspace/tasks/welll/.log` 与用户贴出的 TUI：

```text
agent 🔧 bash {'command': 'cd backend && source venv/bin/activate && python -m pytest tests/test_main.py -v'}
agent 商城应用开发完成。已完成所有7个任务：
      **后端 (17个测试全部通过)**
sw    [Red Witness] 见证 03 阶段红绿流程...
sw    ❌ 未能见证有效的红（退出码 2）
sw       造红：收集期就报错（ImportError / SyntaxError），断言从未被执行。
ERR   硬校验未通过，必须满足所有条件才能推进
```

本机实测这份现场代码（`repo/welll`）：

| 怎么跑 | 结果 |
|---|---|
| `cd backend && ./venv/bin/python -m pytest tests -q`（agent 的跑法） | **17 passed** |
| `cd repo/welll && python3 -m pytest -q`（红见证的跑法） | **3 errors**，退出码 2 |

`ModuleNotFoundError: No module named 'main'`。两边都没说谎 ——
**它们跑在不同的工作目录里。**

## 根因：判据只认顶层布局

agent 的项目是 `backend/` 子目录布局：实现与测试都在 `backend/` 下，
测试写 `from main import app`，依赖装进 `backend/venv`。而

* `red_witness.run_tests` 固定在 `target_dir`（仓库根）跑 pytest；
* `_project_python` 只找 `target/.venv` 与 `target/venv`，看不见
  `backend/venv`。

于是收集期 ImportError → 退出码 2 → `classify_exit_code` 判「造红」。
而「造红」是被**硬拦**的：`_bypass_files` 的事后让路只在退出码 0 时生效
（那是刻意收窄的，见其 docstring），退出码 2 属于「agent 做错了、有自救
办法」的一类。可这一次 agent 没做错 —— 是我们跑错了地方。

## 这是同一个坑的第四次出现

`lib_run_tests.sh` 与 `red_witness.py` 里已经记着三次：

* 任务 T2：src-layout 未加 `PYTHONPATH=src`，agent 报 25 passed、门禁报失败；
* 任务 T3：依赖装在 `repo/T3/.venv`，门禁用 harness 的 python3 报缺 pandas；
* 任务 helloworld：`_has_pytest_surface` 两侧判据错位，20 个测试一个没跑。

三次的处置都是「在顶层再补一种探测」。这次的形状说明补的方向不够：
**问题不是少认了一种标记，而是判据假定项目根就是 target_dir。**

> 判例：当 harness 与 agent 对同一份代码给出相反结论时，先问「我们是不是
> 在不同的地方执行」，而不是先怀疑代码。

## 判据形态

复现走 MockAgent（`SW_MOCK_CODING_SUBDIR_LAYOUT=1`）而不是手拼文件树：
要复现的是 agent 真实会产出的布局。mock 写出的测试**本身是好的**
（`cd backend` 跑全绿），所以测出来的红只可能来自布局，
不会与「代码有 bug」混淆。
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sw_lib.agents.mock import MockAgent
from sw_lib.core.config import TASKS
from sw_lib.workflow import red_witness as rw

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def subdir_project(tmp_path, monkeypatch):
    """用 MockAgent 写出 `welll` 那种子目录布局，返回项目根。"""
    monkeypatch.setenv("SW_MOCK_CODING_SUBDIR_LAYOUT", "1")
    agent = MockAgent.__new__(MockAgent)
    agent.name = "pytest-rw-subdir"
    target = tmp_path / "repo" / "pytest-rw-subdir"
    written = agent._write_subdir_project(target)
    assert written, "mock 没写出任何文件"
    return target


def _pytest_rc(cwd: Path, args=("-q",)) -> int:
    """在 cwd 里裸跑一次 pytest，返回退出码。"""
    proc = subprocess.run(
        ["python3", "-m", "pytest", *args],
        cwd=str(cwd), capture_output=True, text=True, check=False,
    )
    return proc.returncode


# ── 前提自检：复现的确是 welll 的形状 ──

def test_project_is_green_when_run_from_its_own_root(subdir_project):
    """前提：这份代码本身是好的 —— 在 `backend/` 里跑全绿。

    若这条不成立，下面的红就分不清是「布局问题」还是「代码有 bug」，
    整个复现失去意义。
    """
    rc = _pytest_rc(subdir_project / "backend", ("tests", "-q"))
    assert rc == 0, (
        f"前提不成立：mock 写出的项目在自己的根目录里也跑不绿（rc={rc}）")


def test_same_code_fails_collection_from_the_repo_root(subdir_project):
    """前提：同一份代码在仓库根跑就是 collection error。

    这正是 `welll` 两侧结论相反的机械成因。
    """
    rc = _pytest_rc(subdir_project)
    assert rc == rw.EXIT_COLLECTION_ERROR, (
        f"前提不成立：在仓库根跑得到 rc={rc}，"
        f"而 welll 现场是 {rw.EXIT_COLLECTION_ERROR}（造红）")


# ── 主判据：见证必须在项目真正的根目录跑 ──

def test_witness_finds_the_real_project_root(subdir_project):
    """见证必须能认出 `backend/` 才是这个项目的 pytest 根。

    这是修复的核心：判据不能假定 `target_dir` 就是项目根。
    """
    root = rw.resolve_pytest_root(subdir_project)

    assert root == subdir_project / "backend", (
        f"探测到的 pytest 根是 {root}，应为 {subdir_project / 'backend'}")


def test_witness_does_not_report_fabricated_red(subdir_project):
    """同一份代码，见证的结论必须与 agent 一致 —— 全绿，不是「造红」。

    这条就是 `welll` 卡住的那一刻。
    """
    exit_code, nodes = rw._run_in_target(subdir_project)

    assert exit_code != rw.EXIT_COLLECTION_ERROR, (
        f"见证仍报造红（退出码 {exit_code}）—— "
        f"而 agent 在 backend/ 里跑是全绿的，两侧结论相反")
    assert exit_code == rw.EXIT_ALL_PASSED, (
        f"退出码 {exit_code}，期望 0（全部通过）")
    assert any(o == "passed" for o in nodes.values()), (
        f"没有采集到 passed 节点: {nodes}")


def test_flat_layout_still_runs_at_target_dir(tmp_path, monkeypatch):
    """顶层平铺布局不得受影响 —— 探测是叠加，不是替换。

    e2e 的主路径就是平铺布局（`mocknote.py` + `test_mocknote.py` 在根）。
    探测若把它也改到某个子目录去跑，会把既有正路弄坏。
    """
    monkeypatch.delenv("SW_MOCK_CODING_SUBDIR_LAYOUT", raising=False)
    agent = MockAgent.__new__(MockAgent)
    agent.name = "pytest-rw-flat"
    target = tmp_path / "repo" / "pytest-rw-flat"
    agent._write_sample_project(target)

    assert rw.resolve_pytest_root(target) == target, \
        "平铺布局的 pytest 根被改到了别处"

    exit_code, nodes = rw._run_in_target(target)
    assert exit_code == rw.EXIT_ALL_PASSED, f"平铺布局跑不绿了: rc={exit_code}"
    assert any(o == "passed" for o in nodes.values())


# ── venv 也要在子目录里被找到 ──

def test_project_python_finds_venv_in_subdirectory(subdir_project):
    """`backend/venv` 里的解释器必须被找到。

    `welll` 的依赖全装在 `backend/venv`（agent 建的），而
    `_project_python` 原先只看 `target/.venv` 与 `target/venv` ——
    连解释器都没对上，这是同一次误判的第二层成因（任务 T3 的教训只落了一半）。
    """
    fake = subdir_project / "backend" / "venv" / "bin"
    fake.mkdir(parents=True, exist_ok=True)
    py = fake / "python"
    py.write_text("#!/bin/sh\nexec python3 \"$@\"\n", encoding="utf-8")
    py.chmod(0o755)

    found = rw._project_python(subdir_project)

    assert found == str(py), (
        f"没找到子目录里的 venv 解释器（得到 {found}）—— "
        f"依赖装在那里，用 harness 的 python3 会 ModuleNotFoundError")


def test_project_python_is_absolute_even_for_relative_target(subdir_project,
                                                             monkeypatch):
    """解释器路径必须是绝对的 —— 相对路径在切了 cwd 之后就失效了。

    ⚠️ 这条判据是**事后补的**（DEV-PROTOCOL 1.2：不静默改测试，要写明）。
    补的原因是新旧行为逐任务对照时发现的实测回归：

    ```text
    task        old_rc  new_rc  new_root
    welll            2      -1  repo/welll/backend   <== 变化
    newworld         2      -1  repo/newworld/backend
    ```

    `-1` 是 `unavailable`（解释器起不来），比原来的「造红」更糟：从误拦
    变成了测不了。成因是 `target_dir` 为相对路径（`.state` 里存的就是
    `repo/welll`）时，`_project_python` 回的也是相对路径
    `repo/welll/backend/venv/bin/python`，而 `run_tests` 的 cwd 现在是
    `repo/welll/backend` —— 那个相对路径在新 cwd 下不存在。

    这是一条**既有隐患**（cwd == target_dir 时同样会踩），本轮把 cwd 挪到
    子目录后它才必然暴露。`lib_run_tests.sh` 早就显式处理过同一件事
    （「相对路径要在 cd 之前转成绝对路径」），Python 侧漏了。
    """
    fake = subdir_project / "backend" / "venv" / "bin"
    fake.mkdir(parents=True, exist_ok=True)
    py = fake / "python"
    py.write_text("#!/bin/sh\nexec python3 \"$@\"\n", encoding="utf-8")
    py.chmod(0o755)

    monkeypatch.chdir(subdir_project.parent.parent)
    relative = Path("repo") / subdir_project.name
    assert not relative.is_absolute(), "前提不成立：这里要的是相对路径"

    found = rw._project_python(relative)

    assert found is not None, "相对 target 下连解释器都没找到"
    assert Path(found).is_absolute(), (
        f"解释器路径是相对的（{found}）—— run_tests 会 cd 到项目根，"
        f"到那里这个相对路径就不存在了，结果是 unavailable（rc=-1）")

    exit_code, nodes = rw._run_in_target(relative)
    assert exit_code == rw.EXIT_ALL_PASSED, (
        f"相对 target 跑出 rc={exit_code}（-1 = 解释器起不来）")
    assert any(o == "passed" for o in nodes.values())


# ── 反向判据：不许因此放宽「造红」的判定 ──

def test_genuine_import_error_is_still_fabricated_red(tmp_path):
    """真正的 import 错误仍必须判「造红」。

    修的是「我们跑错了地方」，不是「以后 import 错误都放过」。
    一个在自己根目录里也 import 失败的项目，红依然是造出来的。
    """
    target = tmp_path / "broken"
    (target / "tests").mkdir(parents=True)
    (target / "tests" / "test_x.py").write_text(
        "import no_such_module_anywhere\n\n\ndef test_x():\n    assert True\n",
        encoding="utf-8")

    exit_code, _ = rw._run_in_target(target)

    assert exit_code == rw.EXIT_COLLECTION_ERROR, (
        f"真的 import 错误没被判成造红（退出码 {exit_code}）—— "
        f"判据被放宽了")


def test_multiple_candidate_roots_prefers_the_one_that_collects(tmp_path):
    """有多个子目录时，选真正能收集到测试的那个。

    `welll` 里除了 `backend/` 还有 `frontend/`（npm 项目，没有 pytest）。
    探测若按字母序取第一个就会选中 `frontend/` —— 那里跑 pytest
    得到退出码 5（no tests ran），照样过不了闸。
    """
    target = tmp_path / "multi"
    (target / "frontend" / "src").mkdir(parents=True)
    (target / "frontend" / "package.json").write_text(
        '{"name": "f", "scripts": {}}\n', encoding="utf-8")
    (target / "backend" / "tests").mkdir(parents=True)
    (target / "backend" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (target / "backend" / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n",
        encoding="utf-8")

    root = rw.resolve_pytest_root(target)

    assert root == target / "backend", (
        f"探测选了 {root}，应选真正有 pytest 测试的 backend/")


# ── 真实现场：welll 的代码必须能过 ──

def test_conftest_import_error_is_not_called_no_tests(tmp_path):
    """conftest 导入失败（退出码 4）不得被说成「无测试」。

    ⚠️ 这条判据也是**事后补的**（DEV-PROTOCOL 1.2）。新旧行为逐任务对照时
    `repo/newworld` 从退出码 2 变成 4：

    ```text
    task        old_rc  new_rc  new_root
    newworld         2       4  repo/newworld/backend
    ```

    4 是对的 —— 它的 `backend/tests/conftest.py` 写 `from main import app`，
    而 `backend/main.py` 根本不存在（agent 没写完）。拦下它没问题，
    但 `classify_exit_code(4, {})` 的理由是「无测试：未采集到任何测试节点
    结果」，与事实不符：测试文件有 3 个，是 conftest 塌了导致一个也没跑。

    误导性的拒绝理由会把人推向错误的下一步（去补测试，而不是去补 main.py）。
    任务 T2 的教训就是「门禁不给准确的下一步，用户只能猜」。
    """
    target = tmp_path / "conftest-boom"
    (target / "tests").mkdir(parents=True)
    (target / "tests" / "conftest.py").write_text(
        "from main import app  # main.py 不存在\n", encoding="utf-8")
    (target / "tests" / "test_x.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8")

    exit_code, nodes = rw._run_in_target(target)
    assert exit_code not in (rw.EXIT_ALL_PASSED, rw.EXIT_ASSERTION_FAILED), (
        f"前提不成立：conftest 塌了却拿到 rc={exit_code}")

    verdict = rw.classify_exit_code(exit_code, nodes)
    assert not verdict.ok, "conftest 塌了必须拦下"
    assert "无测试" not in verdict.reason, (
        f"退出码 {exit_code} 被说成「无测试」，而实际是 conftest / 收集期塌了："
        f"\n  {verdict.reason}")
    assert ("conftest" in verdict.reason or "收集" in verdict.reason
            or "配置" in verdict.reason), (
        f"拒绝理由没说清成因，用户无从下手：\n  {verdict.reason}")


def test_real_welll_project_is_witnessed_as_green():
    """拿 `repo/welll` 的现场代码当样本，见证不得再报造红。

    比 mock 更有说服力：这是原物，17 个测试、`backend/venv` 里装着依赖。
    只读，不改它的任何文件。
    """
    target = _ROOT / "repo" / "welll"
    if not (target / "backend" / "tests").is_dir():
        pytest.skip("welll 现场已不在，跳过（不影响其余判据）")

    root = rw.resolve_pytest_root(target)
    assert root == target / "backend", f"探测到的根是 {root}"

    exit_code, nodes = rw._run_in_target(target)
    assert exit_code != rw.EXIT_COLLECTION_ERROR, (
        f"welll 的现场代码仍被判成造红（退出码 {exit_code}）")
    passed = sum(1 for o in nodes.values() if o == "passed")
    assert passed >= 17, (
        f"只采集到 {passed} 个 passed 节点，agent 实测是 17 个")
