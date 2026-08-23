"""A3 的验收 4-7 / 13：pytest 结果的结构化采集。

**真实运行 pytest 于临时目录，不 mock 输出**（A3 的 9.1 第 3 条）。
`pytest-json-report` 不可依赖（2.6 实测未安装，且 hook 跑在用户仓库的
解释器上），所以解析摘要行 —— 解析失败记 `unparsed` 并保留原文，**不猜数字**。
"""

import json

import pytest

from sw_lib.workflow import fact_pack as FP


def _write(dir_, name, body):
    (dir_ / name).write_text(body, encoding="utf-8")


# ── 验收 4：六个字段齐备 ──

def test_tests_json_has_six_fields(tmp_path):
    _write(tmp_path, "test_ok.py", "def test_a():\n    assert True\n")

    result = FP.collect_tests(str(tmp_path))

    for key in ("collected", "passed", "failed", "skipped",
                "exit_code", "parse_status"):
        assert key in result, f"tests.json 缺字段 {key}"


def test_normal_suite_counts(tmp_path):
    _write(tmp_path, "test_mix.py",
           "import pytest\n"
           "def test_a():\n    assert True\n"
           "def test_b():\n    assert True\n"
           "@pytest.mark.skip\ndef test_c():\n    pass\n")

    result = FP.collect_tests(str(tmp_path))

    assert result["exit_code"] == 0
    assert result["passed"] == 2
    assert result["skipped"] == 1
    assert result["parse_status"] == "parsed"


# ── 验收 5：零测试的退出码是 5，不是 0 ──

def test_zero_tests_exit_code_is_five(tmp_path):
    """A3 的 2.3 修正了 A6 的 3.1 那处断言。

    按 A6 原文会写成 `exit_code == 0` —— 那条测试本身是错的。
    实测：零测试 pytest 返回 5（no tests ran）。
    """
    (tmp_path / "src").mkdir()
    _write(tmp_path / "src", "app.py", "x = 1\n")
    _write(tmp_path, "pytest.ini", "[pytest]\n")

    result = FP.collect_tests(str(tmp_path))

    assert result["exit_code"] == 5
    assert result["collected"] == 0


# ── 验收 6：全 skip ──

def test_all_skipped_suite(tmp_path):
    _write(tmp_path, "test_skip.py",
           "import pytest\n"
           "@pytest.mark.skip\ndef test_a():\n    pass\n"
           "@pytest.mark.skip\ndef test_b():\n    pass\n")

    result = FP.collect_tests(str(tmp_path))

    assert result["exit_code"] == 0
    assert result["skipped"] == result["collected"] == 2
    # 全 skip 是 A6 的 O3 盲区之一：退出码 0 但一条也没真跑。
    assert result["passed"] == 0


# ── 验收 7：摘要不可解析时不猜数字 ──

def test_unparsable_summary_keeps_raw_and_nulls_numbers():
    result = FP.parse_pytest_summary("完全不是 pytest 的输出\n乱码乱码\n", 0)

    assert result["parse_status"] == "unparsed"
    for key in ("collected", "passed", "failed", "skipped"):
        assert result[key] is None, \
            f"{key} 被猜成了 {result[key]!r} —— 0 会被下游当成真实事实"
    assert "乱码乱码" in result["raw"]


def test_parse_handles_failed_summary():
    result = FP.parse_pytest_summary("1 failed, 2 passed in 0.12s\n", 1)

    assert result["parse_status"] == "parsed"
    assert result["failed"] == 1
    assert result["passed"] == 2
    assert result["exit_code"] == 1


def test_parse_records_no_tests_ran():
    result = FP.parse_pytest_summary("no tests ran in 0.01s\n", 5)

    assert result["parse_status"] == "parsed"
    assert result["collected"] == 0


# ── 验收 13：pytest 不可用时记 unavailable，不抛异常 ──

def test_pytest_unavailable_is_recorded_not_raised(tmp_path, monkeypatch):
    _write(tmp_path, "test_ok.py", "def test_a():\n    assert True\n")
    monkeypatch.setattr(FP, "_project_python", lambda d: "/nonexistent/python-xyz")

    result = FP.collect_tests(str(tmp_path))

    # 「某项事实不可得」记 unavailable，不硬失败（A3 的 3.9 分界线）。
    # unavailable 进 A6 的三态，**绝不计为通过**。
    assert result["parse_status"] == "unavailable"
    assert result["passed"] is None


def test_no_test_surface_is_not_silently_passed(tmp_path):
    """A3 的 2.4：现状是无测试文件就整段 `return 0`（通过），pytest 压根没跑。

    「有没有跑过」这件事本身要落盘，不能只留一个退出码。
    """
    _write(tmp_path, "readme.md", "# nothing here\n")

    result = FP.collect_tests(str(tmp_path))

    assert result["parse_status"] != "parsed" or result["collected"] == 0
    assert result.get("ran") is False
    assert result["passed"] is None, "无测试面时不得报 0 passed —— 那像是跑过了"


def test_pytest_version_is_recorded(tmp_path):
    """R2：摘要行格式随版本变化，版本号须落盘便于日后定位。"""
    _write(tmp_path, "test_ok.py", "def test_a():\n    assert True\n")

    result = FP.collect_tests(str(tmp_path))

    assert result.get("pytest_version")
