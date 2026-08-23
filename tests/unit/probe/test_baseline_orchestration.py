"""B0 的 3.1 / 3.5：采集编排与人工标注字段。

编排层要保证的是「归档如实反映采到了什么」——
包括**没采到**的部分：`reviewer_mentioned_defect` 默认必须是 `None`
（待人工标注），不能默认 `False`，否则一份从未标注的基线会看起来
像是「已确认 reviewer 没提到缺陷」。
"""

import pytest

from sw_lib.probe import baseline as bl


@pytest.fixture(autouse=True)
def _isolate_probe_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "PROBE_DIR", tmp_path / "probe")


def _survival(surviving=True):
    return {"surviving": surviving, "captured_by_tests": not surviving,
            "status": "ok", "exit_code": 0 if surviving else 1}


# ── 样本记录 ──

def test_sample_records_verdict_from_route_targets():
    s = bl.build_sample("M2-1", "src/auth.py", "M2", _survival(),
                        ["05-archive"] * 3)

    assert s["verdict"] == "not_detected", s
    assert s["runs"] == 3
    assert s["route_targets"] == ["05-archive"] * 3
    assert s["surviving"] is True


def test_manual_annotation_defaults_to_none_not_false():
    """`reviewer_mentioned_defect` 默认 `None`（待标注），不是 `False`。

    B0 的 3.5 定案人工标注。默认 `False` 会让一份从未标注的基线
    看起来像「已确认 reviewer 没提到缺陷」—— 那是伪造的结论。
    """
    s = bl.build_sample("M2-1", "src/auth.py", "M2", _survival(),
                        ["05-archive"] * 3)

    assert s["reviewer_mentioned_defect"] is None, (
        f"未标注就不能有结论: {s}")
    assert s.get("annotation_pending") is True, s


def test_manual_annotation_is_kept_when_provided():
    s = bl.build_sample("M2-1", "src/auth.py", "M2", _survival(),
                        ["05-archive"] * 3, reviewer_mentioned_defect=False)

    assert s["reviewer_mentioned_defect"] is False
    assert s.get("annotation_pending") is False


def test_captured_mutation_is_recorded_but_marked_non_surviving():
    """被测试抓住的变异要留在归档里（可追溯），但标明不存活。

    不能直接丢掉：A11 需要知道哪些算子在这个项目上不存活，
    否则它会重复尝试同一批无效样本。
    """
    s = bl.build_sample("M1-1", "src/auth.py", "M1", _survival(False), [])

    assert s["surviving"] is False
    assert s["verdict"] == "unavailable", "没跑 review 就没有 Route 判定"


# ── 顶层编排 ──

def test_capture_includes_precheck_and_env():
    """归档必须自带前置检查结论与环境指纹（验收 1、2）。"""
    result = bl.capture(samples=[])

    assert result.get("baseline_purity"), result
    assert result.get("purity_detail"), result
    assert result["env"].get("git_sha"), result["env"]
    assert "git_dirty" in result["env"], result["env"]


def test_capture_with_no_samples_reports_null_rate():
    """零样本时检出率是 `None`（未测量），不是 `0.0`。"""
    result = bl.capture(samples=[])

    assert result["detection_rate"] is None, result
    assert result["sample_count"] == 0


def test_capture_computes_rate_over_surviving_samples():
    """⚠️ 全未检出 → `0.0`。**这是对照组的期望结果**（9.4）。"""
    samples = [
        bl.build_sample("M2-1", "a.py", "M2", _survival(), ["05-archive"] * 3),
        bl.build_sample("M2-2", "b.py", "M2", _survival(), ["05-archive"] * 3),
    ]

    result = bl.capture(samples=samples)

    assert result["detection_rate"] == 0.0, (
        "对照组注入存活变异后本应毫无反应；"
        "若这里非 0，先怀疑采集脚本而不是去『修』基线")
    assert result["sample_count"] == 2


def test_capture_marks_partial_when_interrupted():
    """中断续采要标 `partial: true`（R4）—— 部分数据不能冒充完整基线。"""
    result = bl.capture(samples=[], partial=True)

    assert result.get("partial") is True, result
