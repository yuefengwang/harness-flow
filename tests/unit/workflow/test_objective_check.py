"""A6：客观轨 —— 纯程序判定，不含 LLM。

判据全部来自 A6 第 6 节的验收标准。三条最容易写反的：

1. **验收 1/2 的断言方向是「必须被判失败」。** 这是少见的「改造前绿、
   改造后红」：现在零测试的项目能通过测试门禁（`run_project_tests` 在无
   测试面时整段 `return 0`），所以「零测试必须 fail」这条断言在改造前是红的。
   若顺着直觉写成「改造前应该通过」，就固化了那个缺陷（A6 的 9.2 第一行）。

2. **`unavailable` 必须与 `pass` 区分，不能只断言「不是 fail」。**
   前者是「检查无法执行」，后者是「检查执行且通过」。二态化会让工具缺失
   静默算成通过 —— 那正是 A6 的 3.3 要禁的「找不到就跳过」。

3. **不 mock pytest 输出。** A6 的 9.1：本任务实现的是「检测测试是否有效」
   的检查，用 mock 掉的假输出测它，就是用要防的错误来验证防错机制。
   所以下面每个负样本都是真实目录 + 真实 pytest 运行。
"""

import json
import subprocess
import sys
import textwrap

import pytest

from sw_lib.workflow import objective_check as OC


# ── 真实负样本目录（A6 的 9.1：不接受 mock 掉 pytest 输出）──

def _proj(tmp_path, name, files):
    d = tmp_path / name
    d.mkdir()
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return d


@pytest.fixture
def empty_proj(tmp_path):
    """无任何测试文件 —— 当前会被判通过，这正是 O3 要拦的。"""
    return _proj(tmp_path, "empty", {"app.py": "def f():\n    return 1\n"})


@pytest.fixture
def all_skipped_proj(tmp_path):
    """全部 skip。退出码是 0，只有计数能区分它与真全绿。"""
    return _proj(tmp_path, "skipped", {
        "app.py": "def f():\n    return 1\n",
        "test_app.py": """
            import pytest

            @pytest.mark.skip(reason="wip")
            def test_a():
                assert False

            @pytest.mark.skip(reason="wip")
            def test_b():
                assert False
            """,
    })


@pytest.fixture
def good_proj(tmp_path):
    """一个**健康**的项目：有测试、有 README。

    README 是刻意补的。初版夹具漏了它，于是 O6 如实判 fail、
    `hard_fail` 为 True，而断言写的是 False —— 那是夹具与断言自相矛盾，
    不是实现的问题。按协议 1.2 声明重做夹具，
    而不是放宽实现或删掉那条断言。
    """
    return _proj(tmp_path, "good", {
        "app.py": "def f():\n    return 1\n",
        "README.md": "# good\n\n## Usage\n\n    python3 -c 'import app'\n",
        "test_app.py": """
            from app import f

            def test_f():
                assert f() == 1

            def test_f_again():
                assert f() == 1
            """,
    })


@pytest.fixture
def half_skipped_proj(tmp_path):
    """skip 比例超阈值 —— 期望 warn，不是 fail。"""
    return _proj(tmp_path, "half", {
        "app.py": "def f():\n    return 1\n",
        "test_app.py": """
            import pytest
            from app import f

            def test_ok():
                assert f() == 1

            @pytest.mark.skip(reason="wip")
            def test_s1():
                assert False

            @pytest.mark.skip(reason="wip")
            def test_s2():
                assert False
            """,
    })


# ── 验收 1：零测试被判失败（当前会通过）──

def test_zero_tests_is_hard_fail(empty_proj):
    """一个没有任何测试的项目必须被判 O3 失败。

    ⚠️ 断言方向：**必须 fail**。改造前 `run_project_tests` 在无测试面时
    整段 return 0，这条断言是红的 —— 那才是正确的 Red（A6 的 9.2）。
    """
    r = OC.run_checks(str(empty_proj))
    o3 = OC.find_check(r, "O3")

    assert o3["verdict"] == "fail", f"零测试项目未被判失败: {o3}"
    assert "collected=0" in o3.get("detail", ""), o3
    assert "O3" in r["hard_fail_ids"], r
    assert r["hard_fail"] is True


# ── 验收 2：全部 skip 被判失败 ──

def test_all_skipped_is_hard_fail(all_skipped_proj):
    """全部 skip 时 pytest 返回 0 —— 退出码判定的盲区（A6 的 3.1）。"""
    r = OC.run_checks(str(all_skipped_proj))
    o3 = OC.find_check(r, "O3")

    assert o3["verdict"] == "fail", f"全 skip 未被判失败: {o3}"
    assert "O3" in r["hard_fail_ids"], r


def test_all_skipped_exit_code_really_is_zero(all_skipped_proj):
    """钉住盲区本身：全 skip 的退出码确实是 0。

    这条不测被测代码，测的是「为什么不能只看退出码」这个前提仍然成立。
    前提哪天变了（pytest 改行为），上面那条测试的理由就得重写。
    """
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          cwd=str(all_skipped_proj),
                          capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"全 skip 的退出码不再是 0（实为 {proc.returncode}），"
        f"O3 的立论前提需要重新检查")


def test_zero_tests_exit_code_is_five(tmp_path):
    """零测试的退出码是 5，不是 0（A3 实测修正了 A6 原文的断言）。

    A6 的 3.1 原文称两种情况均返回 0。按原文写成 `== 0` 的话，
    测试本身就是错的。
    """
    d = _proj(tmp_path, "notests", {"conftest.py": "", "app.py": "x = 1\n"})
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          cwd=str(d), capture_output=True, text=True)
    assert proc.returncode == 5, (
        f"零测试的退出码应为 5（no tests ran），实为 {proc.returncode}")


# ── 正样本：不得误判 ──

def test_healthy_suite_passes(good_proj):
    r = OC.run_checks(str(good_proj))
    o3 = OC.find_check(r, "O3")

    assert o3["verdict"] == "pass", f"正常套件被误判: {o3}"
    assert r["hard_fail"] is False, r
    assert r["hard_fail_ids"] == [], r


def test_high_skip_ratio_warns_not_fails(half_skipped_proj):
    """skip 超阈值是 warn。fail 会让存量任务大面积挂（A6 的 R1/9.3）。"""
    r = OC.run_checks(str(half_skipped_proj))
    o3 = OC.find_check(r, "O3")

    assert o3["verdict"] == "warn", f"高 skip 比例应 warn 而非 {o3['verdict']}: {o3}"
    assert "O3" not in r["hard_fail_ids"], "warn 不得进 hard_fail"


# ── 验收 3：严重性不依赖 Route（消除循环依赖）──

def test_readme_verdict_identical_across_routes(tmp_path):
    """同一 README 缺失状态，在两种 Route 下判定必须一致（A6 的 3.2）。

    改造前 `check_04-review.sh:153` 是
    `if [ "$ROUTE_VAL" = "05-archive" ] && [ "$README_ERRORS" -gt 0 ]` ——
    Route 决定严重性、严重性又决定 Route，一个闭环。
    只测一种 Route 测不出这个循环。
    """
    d = _proj(tmp_path, "noreadme", {
        "app.py": "x = 1\n",
        "test_app.py": "def test_x():\n    assert True\n",
    })

    archive = OC.run_checks(str(d), route="05-archive")
    rework = OC.run_checks(str(d), route="03-coding")

    a = OC.find_check(archive, "O6")
    b = OC.find_check(rework, "O6")
    assert a["verdict"] == b["verdict"], (
        f"README 判定随 Route 变化：05-archive={a['verdict']} "
        f"03-coding={b['verdict']} —— 循环依赖仍在")
    assert a["verdict"] == "fail", "缺 README 应按最严标准判 fail"


def test_route_is_not_an_input_to_severity(tmp_path):
    """更强的判据：不传 route 与传任意 route，结果完全一致。"""
    d = _proj(tmp_path, "noreadme2", {
        "app.py": "x = 1\n",
        "test_app.py": "def test_x():\n    assert True\n",
    })

    base = OC.run_checks(str(d))
    for route in ("05-archive", "03-coding", "02-planning", None, "garbage"):
        r = OC.run_checks(str(d), route=route)
        assert r["hard_fail_ids"] == base["hard_fail_ids"], (
            f"route={route!r} 改变了判定结果：{r['hard_fail_ids']} "
            f"vs {base['hard_fail_ids']}")


# ── 验收 4：unavailable 不算通过 ──

def test_coverage_unavailable_when_tool_missing(good_proj, monkeypatch):
    """diff-cover 未安装时 O8 记 unavailable，不计通过、不阻断（验收 4）。"""
    monkeypatch.setattr(OC, "_which", lambda name: None)
    r = OC.run_checks(str(good_proj))
    o8 = OC.find_check(r, "O8")

    assert o8["verdict"] == "unavailable", (
        f"工具缺失应记 unavailable 而非 {o8['verdict']}: {o8}")
    assert o8.get("reason"), "unavailable 必须说明为什么无法执行"
    assert "O8" not in r["hard_fail_ids"], "unavailable 不得阻断"


def test_unavailable_is_not_counted_as_pass(good_proj, monkeypatch):
    """unavailable 与 pass 必须可区分 —— 只断言「不是 fail」会漏掉这点。"""
    monkeypatch.setattr(OC, "_which", lambda name: None)
    r = OC.run_checks(str(good_proj))

    assert r["counts"]["pass"] == len(
        [c for c in r["checks"] if c["verdict"] == "pass"])
    assert OC.find_check(r, "O8")["verdict"] != "pass", (
        "工具缺失被算成了通过 —— 这正是 A6 的 3.3 要禁的「找不到就跳过」")


def test_missing_target_dir_is_unavailable_not_pass(tmp_path):
    """target_dir 不存在时不得静默通过（A6 的 3.3）。"""
    r = OC.run_checks(str(tmp_path / "nope"))

    assert r["hard_fail"] is True, "前提缺失却报通过"
    verdicts = {c["id"]: c["verdict"] for c in r["checks"]}
    assert "pass" not in verdicts.values(), (
        f"target_dir 不存在却有检查报 pass: {verdicts}")


# ── 验收 5：越界改动被 O5 捕获 ──

def test_out_of_scope_change_detected(tmp_path):
    """target_dir 外的改动必须被 O5 捕获（验收 5）。"""
    d = _proj(tmp_path, "scoped", {
        "app.py": "x = 1\n",
        "test_app.py": "def test_x():\n    assert True\n",
        "README.md": "# scoped\n\nusage\n",
    })
    r = OC.run_checks(str(d), files_touched=[
        "scoped/app.py", "../../etc/passwd", "/tmp/elsewhere.py"])
    o5 = OC.find_check(r, "O5")

    assert o5["verdict"] == "fail", f"越界改动未被捕获: {o5}"
    assert "O5" in r["hard_fail_ids"]


def test_in_scope_changes_pass(tmp_path):
    d = _proj(tmp_path, "scoped2", {
        "app.py": "x = 1\n",
        "test_app.py": "def test_x():\n    assert True\n",
        "README.md": "# scoped2\n\nusage\n",
    })
    r = OC.run_checks(str(d), files_touched=["app.py", "test_app.py"])
    assert OC.find_check(r, "O5")["verdict"] == "pass"


def test_no_declared_files_is_unavailable_not_pass(good_proj):
    """没有声明文件时 O5 无从判定 —— 记 unavailable，不算通过。"""
    r = OC.run_checks(str(good_proj), files_touched=None)
    assert OC.find_check(r, "O5")["verdict"] == "unavailable"


# ── 验收 6：hard_fail_ids 与实际失败项一致 ──

def test_hard_fail_ids_match_actual_failures(empty_proj):
    r = OC.run_checks(str(empty_proj))
    actual = sorted(c["id"] for c in r["checks"]
                    if c["verdict"] == "fail" and c.get("severity") == "high")

    assert sorted(r["hard_fail_ids"]) == actual, (
        f"hard_fail_ids={r['hard_fail_ids']} 与实际高危失败项 {actual} 不一致")
    assert r["hard_fail"] is (len(actual) > 0)


def test_output_is_json_serializable(good_proj):
    """产出要交给 A9 消费，必须能过 JSON。"""
    json.dumps(OC.run_checks(str(good_proj)))


def test_no_natural_language_conclusion(good_proj):
    """客观轨不产出自然语言结论（A6 第 4 节的输出契约）。

    一旦它开始写「建议返工」，A9 就会去读那句话而不是读结构化字段，
    仲裁又变成读散文。
    """
    r = OC.run_checks(str(good_proj))
    assert "route" not in r, "客观轨不得给出 Route —— 那是 A9 的职责"
    assert "conclusion" not in r
    assert "recommendation" not in r


# ── 验收 7：零 LLM 调用 ──

def test_zero_llm_calls(good_proj, monkeypatch):
    """客观轨执行过程零 LLM 调用（验收 7）。"""
    calls = []

    import sw_lib.agents.opencode as OCA
    monkeypatch.setattr(OCA.OpenCodeAgent, "send",
                        lambda self, *a, **k: calls.append(a), raising=False)

    OC.run_checks(str(good_proj))
    assert calls == [], f"客观轨调用了 LLM: {calls}"


def test_the_llm_counter_itself_works(good_proj, monkeypatch):
    """验证计数器有效 —— 人为注入一次调用必须能被它抓到。

    A6 的 9.2 最后一行：从不验证计数器本身有效，是「零 LLM 调用」
    这条断言最常见的假绿形态。
    """
    calls = []
    import sw_lib.agents.opencode as OCA
    monkeypatch.setattr(OCA.OpenCodeAgent, "send",
                        lambda self, *a, **k: calls.append(a), raising=False)

    OCA.OpenCodeAgent.send(object(), "人为注入")
    assert calls, "计数器抓不到已知的调用 —— 它本身是坏的，上一条断言无意义"


def test_module_imports_no_agent(good_proj):
    """源码级：客观轨模块不得 import 任何 agent。"""
    import inspect
    src = inspect.getsource(OC)
    for bad in ("opencode", "OpenCodeAgent", "MockAgent", "agents."):
        assert bad not in src, f"客观轨引用了 agent: {bad}"
