"""事实包侧的同一个错位：子目录布局下 `collect_tests` 一个测试都没采到。

这是 `test_red_witness_subdir_layout.py` / `test_hook_subdir_layout.py` 的
**第三半**。前两侧（见证、钩子）在 `7ee1928` 里已接上
`resolve_pytest_root`，事实包这一侧没跟上，于是任务 `testNew` 现场是：

```text
[README Check] 验证文档: repo/testNew/README.md
  运行 pytest (repo/testNew/backend)...
    使用项目虚拟环境: .../repo/testNew/backend/venv/bin/python
      ==================== 9 passed, 17 warnings in 3.10s ====================
  [Objective Track] 客观轨判定...
    ❓ O2 tests: unavailable target_dir 下没有 pytest 语义的测试面
    ❌ O3 test_validity: fail collected=0（无测试面）
  ❌ 客观轨硬失败: O3
```

同一个 hook、同一份代码、同一次运行：shell 那侧报 9 passed，客观轨报
「无测试面」并硬拦。两边都没说谎 —— 只是**跑在不同的地方**。

这是 `welll` 那个坑的第五次出现（前四见 A0 的 2.9.14）。形状不变：
**判据假定 `target_dir` 就是项目根**。`resolve_pytest_root` 就是为消除这个
假定而存在的，它有两个消费方，第三个漏了。

为什么必须修事实包这一侧：`objective_check` 的 O2/O3 只读 `facts/tests.json`，
不自己跑 pytest。事实包采不到，O3 就恒为 `collected=0` 硬失败，而 O3 的
severity 是 high。agent 无从下手 —— 它的测试确实存在、确实全绿。

判据是行为：**子目录布局里的测试必须被事实包真实采到，且结论与钩子侧一致。**
"""

from pathlib import Path

import pytest

from sw_lib.workflow import fact_pack as fp
from sw_lib.workflow import red_witness as rw

_PASSING = "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n"
_FAILING = "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 3\n"


@pytest.fixture
def subdir_project(tmp_path):
    """`welll` / `testNew` 的形状：实现与测试都在 `backend/` 下。

    测试写 `from calc import add`，只有 cwd 在 `backend/` 时才成立 ——
    与 `testNew` 里 `from main import app` 同构。仓库根刻意不放任何
    pytest 配置，这正是现场的样子。
    """
    def _make(body=_PASSING, with_frontend=True):
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True, exist_ok=True)
        (backend / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8")
        (backend / "tests" / "test_calc.py").write_text(body, encoding="utf-8")
        if with_frontend:
            # testNew 里 backend/ 旁边是个 React 项目，探测不能选中它。
            (tmp_path / "frontend" / "src").mkdir(parents=True, exist_ok=True)
            (tmp_path / "frontend" / "package.json").write_text(
                '{"name": "f"}\n', encoding="utf-8")
        (tmp_path / "README.md").write_text("# proj\n", encoding="utf-8")
        return tmp_path
    return _make


def test_precondition_witness_side_resolves_backend(subdir_project):
    """前提：见证侧本来就认得 backend/。不成立的话本文件的判据全部无意义。"""
    target = subdir_project()
    assert rw.resolve_pytest_root(target) == target / "backend", \
        "前提不成立：resolve_pytest_root 没认出 backend/"


def test_subdir_tests_are_actually_collected(subdir_project):
    """子目录布局里的测试必须被采到。

    现场：`parse_status=no_test_surface`、`collected=null`、`ran=false`，
    而同一次运行里 shell 那侧报 9 passed。
    """
    target = subdir_project(_PASSING)
    facts = fp.collect_tests(str(target))

    assert facts["parse_status"] != "no_test_surface", \
        ("明明有 backend/tests/test_calc.py，事实包却报『无测试面』:\n"
         f"{facts}")
    assert facts["ran"] is True, f"pytest 根本没执行:\n{facts}"
    assert facts["collected"] == 1, \
        f"应采到 1 个测试，实际 collected={facts['collected']}:\n{facts}"
    assert facts["passed"] == 1, f"应有 1 passed:\n{facts}"


def test_subdir_failure_is_visible_to_facts(subdir_project):
    """失败也必须被采到 —— 否则修完「假 unavailable」会变成「放过坏代码」。

    `7ee1928` 记着这个陷阱：只修一侧会从「误拦好代码」翻成「放过坏代码」，
    比原 bug 更糟。所以绿与红两个方向都要锁。
    """
    target = subdir_project(_FAILING)
    facts = fp.collect_tests(str(target))

    assert facts["ran"] is True, f"pytest 根本没执行:\n{facts}"
    assert facts["collected"] == 1, f"应采到 1 个测试:\n{facts}"
    assert facts["failed"] == 1, \
        f"失败的测试必须被记为 failed，否则坏代码会过闸:\n{facts}"


def test_flat_layout_behavior_unchanged(tmp_path):
    """平铺布局的结论一律不变 —— 探测只在根上无测试时才往下找。

    这是「不许把判据放宽」的边界：修的是「看哪里」，不是「看多严」。
    """
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(_PASSING, encoding="utf-8")

    facts = fp.collect_tests(str(tmp_path))
    assert facts["ran"] is True, f"平铺布局被改坏了:\n{facts}"
    assert facts["collected"] == 1, f"平铺布局采数不对:\n{facts}"


def test_genuinely_empty_project_still_reports_no_surface(tmp_path):
    """真的没有测试时仍须报 `no_test_surface`，不得变成 0 passed。

    判据不能被放宽成恒真：`unavailable` 与 `pass` 必须继续分开（Q5）。
    """
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "frontend").mkdir()

    facts = fp.collect_tests(str(tmp_path))
    assert facts["parse_status"] == "no_test_surface", \
        f"没有测试面却没报出来:\n{facts}"
    assert facts["ran"] is False, f"不该跑却跑了:\n{facts}"
    assert facts["passed"] is None, \
        f"未跑必须留 None，报 0 passed 看起来像跑过了:\n{facts}"


def test_monorepo_two_candidates_stays_at_root(tmp_path):
    """两个候选子目录时留在仓库根 —— 与见证侧的收窄规则保持一致。"""
    for name in ("svc_a", "svc_b"):
        d = tmp_path / name
        d.mkdir()
        (d / "test_x.py").write_text("def test_x():\n    assert True\n",
                                     encoding="utf-8")

    assert rw.resolve_pytest_root(tmp_path) == tmp_path, \
        "前提不成立：见证侧在 monorepo 上就没留在根"
    facts = fp.collect_tests(str(tmp_path))
    # 根上没有测试面，两个候选谁都不选 —— 结论与见证侧同源。
    assert facts["parse_status"] == "no_test_surface", \
        f"monorepo 上事实包与见证侧结论不一致:\n{facts}"


def test_project_python_finds_subdir_venv(subdir_project):
    """`backend/venv` 的解释器必须被选中。

    `testNew` 的 fastapi 只装在 `repo/testNew/backend/venv` 里。钩子侧已经
    用对了（日志里打印了那个路径），事实包侧只看 `target_dir`，连解释器都
    对不上 —— 即便测试面认出来了，收集期仍会 ModuleNotFoundError。
    """
    target = subdir_project()
    bindir = target / "backend" / "venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    py = bindir / "python"
    py.write_text('#!/bin/sh\nexec python3 "$@"\n', encoding="utf-8")
    py.chmod(0o755)

    chosen = fp._project_python(str(target))
    assert chosen is not None, "没找到 backend/venv 里的解释器"
    assert Path(chosen) == py, \
        f"选的不是 backend/venv/bin/python，而是 {chosen}"


def test_pythonpath_follows_project_root(subdir_project):
    """src-layout 的 PYTHONPATH 要相对项目根算，不是相对 target_dir。

    `PYTHONPATH=src` 是相对 cwd 的，而 cwd 将是 `backend/` —— 要看的是
    `backend/src` 而不是 `<repo>/src`。见证侧 `_pytest_env` 已经这么做了。
    """
    target = subdir_project()
    (target / "backend" / "src").mkdir()

    assert fp._pytest_pythonpath(Path(target)) == "src", \
        "backend/src 存在却没给出 PYTHONPATH=src"


def test_pythonpath_ignores_repo_root_src(subdir_project):
    """反向：`<repo>/src` 存在但项目根是 `backend/` 时不该误报。

    ⚠️ 本条是从上一个测试里**拆出来的**，按 DEV-PROTOCOL 1.2 显式声明重做。
    初版把正反两个断言写在同一个测试里，而 `subdir_project` 复用同一个
    `tmp_path`：第二次调用返回的是同一目录，第一次建的 `backend/src` 仍在，
    于是「项目根下无 src」这个前提被测试自己破坏了。红的原因是判据写错，
    不是实现错 —— 单独复现时 `_pytest_pythonpath` 返回的正是 None。
    拆成独立测试各拿一份 tmp_path，前提才成立。
    """
    target = subdir_project(with_frontend=False)
    (target / "src").mkdir()

    assert not (target / "backend" / "src").is_dir(), \
        "前提不成立：项目根下不该有 src/"
    assert fp._pytest_pythonpath(Path(target)) is None, \
        "项目根是 backend/，却拿仓库根的 src/ 当依据"

def test_project_python_is_absolute(subdir_project):
    """解释器路径必须绝对 —— 相对路径在 `cd` 到项目根之后就不存在了。

    这条是 `repo/testNew` 真实任务上实测出来的第二层错位，单元测试原先照不到
    （fixture 用的是 tmp_path 绝对路径，而 `.state` 里存的 target_dir 是
    `repo/testNew` 这种**相对**路径）。现场：

    ```text
    python: repo/testNew/backend/venv/bin/python
    raw:    无法执行 repo/testNew/backend/venv/bin/python -m pytest --version
    parse_status: unavailable
    ```

    解释器**找对了**，但子进程 cwd 是 `backend/`，相对路径到那里失效，于是
    版本探测失败、整包记 `unavailable`。见证侧 `_project_python` 的 docstring
    早已写明「返回绝对路径」并实测过后果「比原来的误判更糟」，事实包这侧漏了。

    绝对化必须用 `os.path.abspath` 而非 `Path.resolve()`：venv 的
    `bin/python` 是指向 `python3.12` 的符号链接，`resolve()` 会解成真身，
    site-packages 整个失效。
    """
    target = subdir_project()
    bindir = target / "backend" / "venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    real = bindir / "python3.12"
    real.write_text('#!/bin/sh\nexec python3 "$@"\n', encoding="utf-8")
    real.chmod(0o755)
    link = bindir / "python"
    link.symlink_to("python3.12")

    # 用相对路径调用，复现 `.state` 里 target_dir 的真实形态。
    import os
    cwd = os.getcwd()
    try:
        os.chdir(target.parent)
        chosen = fp._project_python(target.name)
    finally:
        os.chdir(cwd)

    assert chosen is not None, "相对 target_dir 下没找到解释器"
    assert os.path.isabs(chosen), \
        f"解释器路径必须绝对，否则 cd 到项目根后失效: {chosen}"
    assert Path(chosen).name == "python", \
        f"符号链接被解成真身，venv 的 site-packages 会失效: {chosen}"


def test_relative_target_dir_collects_tests(subdir_project):
    """相对 target_dir 下必须真正采到测试 —— 这是 `testNew` 现场的完整形状。

    比 `test_subdir_tests_are_actually_collected` 严一层：那条用绝对 tmp_path，
    照不到相对路径这层。真实链路里 `.state` 存的是 `repo/testNew`。
    """
    import os
    target = subdir_project(_PASSING)
    bindir = target / "backend" / "venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    py = bindir / "python"
    py.write_text('#!/bin/sh\nexec python3 "$@"\n', encoding="utf-8")
    py.chmod(0o755)

    cwd = os.getcwd()
    try:
        os.chdir(target.parent)
        facts = fp.collect_tests(target.name)
    finally:
        os.chdir(cwd)

    assert facts["parse_status"] != "unavailable", \
        f"相对 target_dir 下整包记 unavailable:\n{facts}"
    assert facts["ran"] is True, f"pytest 没跑:\n{facts}"
    assert facts["collected"] == 1, \
        f"应采到 1 个测试，实际 {facts['collected']}:\n{facts}"
