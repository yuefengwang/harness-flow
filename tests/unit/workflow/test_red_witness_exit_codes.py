"""A2 第 2 节：退出码分类必须建立在**真实 pytest 行为**上。

纪律（A2 的 10.3）：这批用例一律在临时目录里真实执行 pytest。
若 mock 掉 pytest 调用、直接喂造好的退出码，验证的就是自己的分支逻辑，
而不是「pytest 真的这样表现」—— 那是假绿。

本机实测（2026-08-23，pytest 9.1.1 / python3.12）确认的五种形态：

    断言失败 → 1 ；collection error → 2 ；无测试 → 5 ；
    全 skip → 0 ；全通过 → 0

最后两条退出码相同，这正是 A2 的 10.4 指出的最容易的假绿：
只看退出码会把「全 skip」当成转绿。
"""

import pytest

from sw_lib.workflow import red_witness as rw


def _write(dir_path, name, body):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(body, encoding="utf-8")
    return dir_path


@pytest.fixture
def run_pytest():
    """在给定目录里真实跑一次 pytest，返回 (exit_code, nodes)。"""
    def _run(dir_path):
        return rw.run_tests(dir_path)
    return _run


# ── 有效的红：退出码 1，且能取到失败节点 id ──

def test_assertion_failure_is_valid_red(tmp_path, run_pytest):
    """验收 1：断言失败（退出码 1）是唯一有效的红。"""
    proj = _write(tmp_path / "p", "test_y.py",
                  "def test_fail_a():\n    assert 1 == 2\n")
    exit_code, nodes = run_pytest(proj)

    assert exit_code == rw.EXIT_ASSERTION_FAILED, \
        f"真实 pytest 的断言失败应为退出码 1，实际 {exit_code}"

    verdict = rw.classify_exit_code(exit_code, nodes)
    assert verdict.ok, f"断言失败竟未被判为有效的红: {verdict.reason}"
    assert verdict.failed_nodes == ["test_y.py::test_fail_a"], \
        f"失败节点 id 提取错误: {verdict.failed_nodes}"


def test_failed_node_ids_include_class_and_param(tmp_path, run_pytest):
    """节点 id 必须是完整形态 —— 类方法与参数化都要带上。

    只断言「列表非空」是 A2 的 10.2 列出的假绿：格式错了照样通过。
    """
    proj = _write(tmp_path / "p", "test_y.py",
                  "import pytest\n\n"
                  "class TestG:\n"
                  "    def test_in_class(self):\n"
                  "        assert 0\n\n"
                  "@pytest.mark.parametrize('n', [1, 2])\n"
                  "def test_param(n):\n"
                  "    assert n == 1\n")
    exit_code, nodes = run_pytest(proj)
    verdict = rw.classify_exit_code(exit_code, nodes)

    assert set(verdict.failed_nodes) == {
        "test_y.py::TestG::test_in_class",
        "test_y.py::test_param[2]",
    }, f"节点 id 形态不对: {verdict.failed_nodes}"
    assert verdict.passed_nodes == ["test_y.py::test_param[1]"], \
        f"通过节点未被记录: {verdict.passed_nodes}"


# ── 无效的红 ──

def test_collection_error_is_rejected_as_fake_red(tmp_path, run_pytest):
    """验收 2：ImportError（退出码 2）是「造红」，必须拒绝。

    这是 A2 的 1.1 说的根本问题：一个引用不存在模块的空测试也会「红」。
    把它当成有效的红，实现只要让 import 成功就能转绿，断言从未被检验。
    """
    proj = _write(tmp_path / "p", "test_y.py",
                  "import nosuchmodule_xyz\n\n"
                  "def test_x():\n    assert True\n")
    exit_code, nodes = run_pytest(proj)

    assert exit_code == rw.EXIT_COLLECTION_ERROR, \
        f"collection error 应为退出码 2，实际 {exit_code}"

    verdict = rw.classify_exit_code(exit_code, nodes)
    assert not verdict.ok, "造红（ImportError）竟被当成有效的红"
    assert "造红" in verdict.reason, f"拒绝理由未指出造红: {verdict.reason}"


def test_no_tests_collected_is_rejected(tmp_path, run_pytest):
    """验收 3：收集到 0 个测试（退出码 5）不算红。"""
    proj = _write(tmp_path / "p", "notatest.py", "# 什么测试都没有\n")
    exit_code, nodes = run_pytest(proj)

    assert exit_code == rw.EXIT_NO_TESTS, \
        f"无测试应为退出码 5，实际 {exit_code}"

    verdict = rw.classify_exit_code(exit_code, nodes)
    assert not verdict.ok, "没有任何测试竟被当成有效的红"
    assert "无测试" in verdict.reason, verdict.reason


def test_all_passed_cannot_witness_red(tmp_path, run_pytest):
    """验收 4：测试全部通过时无法见证红。

    这条同时覆盖 A2 的 R4 —— agent 在 03a 就把实现写了，
    导致测试直接绿，退出码 0 被拒即可覆盖，无需额外检测。
    """
    proj = _write(tmp_path / "p", "test_y.py", "def test_p():\n    assert True\n")
    exit_code, nodes = run_pytest(proj)

    assert exit_code == rw.EXIT_ALL_PASSED
    verdict = rw.classify_exit_code(exit_code, nodes)
    assert not verdict.ok, "测试全部通过却见证到了红"
    assert "未失败" in verdict.reason or "无法见证" in verdict.reason, verdict.reason


def test_all_skipped_is_rejected_despite_exit_zero(tmp_path, run_pytest):
    """验收 5：全部 skip 的退出码是 **0**，必须被识别为「无红可见证」。

    A2 的 10.4：这是最容易出的假绿。只看退出码的实现会把它归到
    「测试通过」（03a 拒绝，正确）—— 但同一份判定在 03b 会当成
    「全部转绿」而放行（错误）。因此判定必须看每个节点的 outcome。
    """
    proj = _write(tmp_path / "p", "test_y.py",
                  "import pytest\n\n"
                  "@pytest.mark.skip(reason='x')\n"
                  "def test_s():\n    assert False\n")
    exit_code, nodes = run_pytest(proj)

    assert exit_code == 0, f"全 skip 的退出码应为 0，实际 {exit_code}"
    assert nodes == {"test_y.py::test_s": "skipped"}, \
        f"skip 的节点结果未被采集（文本解析拿不到节点 id）: {nodes}"

    verdict = rw.classify_exit_code(exit_code, nodes)
    assert not verdict.ok, "全部 skip 竟被当成有效的红"
    assert "skip" in verdict.reason.lower(), verdict.reason
