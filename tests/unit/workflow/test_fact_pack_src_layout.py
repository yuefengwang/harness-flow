"""A3 采集层的 src-layout 漏洞 —— 由 A6 上线暴露出来。

`lib_run_tests.sh` 的 `_pytest_pythonpath()` 在 `target_dir/src` 存在时
加 `PYTHONPATH=src`，理由写在它的注释里（任务 T2）：源码在 src/ 下但包未安装时
pytest 收集期就 ModuleNotFoundError，而 agent 自己是用 PYTHONPATH=src 跑通的，
hook 不加就会得出**与 agent 相反的结论**。

`collect_tests()` 复刻了 `_project_python()` 却漏了这一条。改造前没人发现，
因为它的产出只落进事实包给人看；A6 的 O2 开始**消费**它做硬判定之后，
同一个 hook 里出现了自相矛盾的两行：

    运行 pytest (...)  ============ 1 passed ============
    ❌ O2 tests: fail 1 failed / 1 collected

这正是 T2 那个「门禁与 agent 对同一份代码给出相反结论」的重演，
只不过这次两个结论在同一次 hook 执行里。
"""

import textwrap

import pytest

from sw_lib.workflow import fact_pack as FP


@pytest.fixture
def src_layout_proj(tmp_path):
    """src-layout：包在 src/ 下且未安装，测试从顶层 import 它。"""
    d = tmp_path / "proj"
    (d / "src" / "mylib").mkdir(parents=True)
    (d / "src" / "mylib" / "__init__.py").write_text(
        "def hello():\n    return 'hi'\n", encoding="utf-8")
    (d / "test_lib.py").write_text(textwrap.dedent("""
        from mylib import hello

        def test_hello():
            assert hello() == 'hi'
        """), encoding="utf-8")
    return d


def test_src_layout_tests_are_collected(src_layout_proj):
    """src-layout 项目的测试必须能被采集到并判为通过。

    漏 PYTHONPATH=src 时这里会是 `failed=1`（收集期 ModuleNotFoundError）。
    """
    facts = FP.collect_tests(str(src_layout_proj))

    assert facts["parse_status"] == "parsed", facts
    assert facts["failed"] == 0, (
        f"src-layout 项目被误判失败 —— 采集层漏了 PYTHONPATH=src: {facts}")
    assert facts["passed"] == 1, facts


def test_agreement_with_the_shell_hook(src_layout_proj):
    """采集层与 shell 的判据必须一致。

    两处对「怎么跑 pytest」的判断分歧，就会让 hook 自己跟自己矛盾。
    """
    import subprocess
    import sys

    facts = FP.collect_tests(str(src_layout_proj))
    # shell 那侧的等效做法：PYTHONPATH=src
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(src_layout_proj), capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": "src"})

    shell_ok = proc.returncode == 0
    facts_ok = facts.get("failed") == 0
    assert shell_ok == facts_ok, (
        f"采集层与 shell 结论相反：shell_ok={shell_ok} facts={facts}")


def test_objective_track_agrees_too(src_layout_proj):
    """接线层：O2 也必须判通过 —— 它消费的正是上面那份采集结果。"""
    from sw_lib.workflow import objective_check as OC

    r = OC.run_checks(str(src_layout_proj))
    o2 = OC.find_check(r, "O2")
    assert o2["verdict"] == "pass", f"O2 误判 src-layout 项目: {o2}"
    assert "O2" not in r["hard_fail_ids"]
