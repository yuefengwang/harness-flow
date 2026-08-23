"""B0 的 3.4 / 3.1：存活性判定、重复采样三态、检出率。

⚠️⚠️ **本文件的期望方向与直觉相反** ⚠️⚠️

对照组是改造前的 harness：04-review 只有单 reviewer。注入一个存活变异后
它**应当毫无反应**，把任务放行到 05-archive。所以：

    route_targets == ["05-archive"] * 3   → 未检出 → 基线正确
    detection_rate == 0.0                 → 成功，不是失败

B0 的 9.4 专门警告过：直觉会让人写「注入缺陷应被检出」，那条断言在
对照组必然红，于是实施者会以为采集脚本坏了而去「修」它 ——
最终把基线采成一个假的高分，A11 的对比彻底失去意义。
**在 A11 里写错还能重跑，在这里写错就没有第二次。**
"""

import subprocess
from pathlib import Path

import pytest

from sw_lib.probe import baseline as bl
from sw_lib.probe import injector as inj

IMPL = '''def threshold_ok(value, limit):
    """超过上限即拒绝。"""
    if value < limit:
        return True
    return False
'''

# 故意只覆盖「明显小于」这一种情形 —— 边界值 value == limit 没有测到，
# 所以 `<` → `<=` 的变异能存活。这正是 M2 的用途（A11 的 2.2 实测）。
TEST_LOOSE = '''from impl import threshold_ok


def test_below():
    assert threshold_ok(1, 10) is True


def test_above():
    assert threshold_ok(20, 10) is False
'''

# 覆盖了边界 —— 同一个变异会被抓住，因此**不是**存活变异。
TEST_TIGHT = TEST_LOOSE + '''

def test_boundary():
    assert threshold_ok(10, 10) is False
'''


def _project(tmp_path, test_body, name="p"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "impl.py").write_text(IMPL, encoding="utf-8")
    (d / "test_impl.py").write_text(test_body, encoding="utf-8")
    return d


# ── 第 3 步：存活性判定 ──

def test_mutation_surviving_when_tests_miss_the_boundary(tmp_path):
    """现有测试没覆盖边界 → 变异存活 → 可进入基线观测。"""
    proj = _project(tmp_path, TEST_LOOSE)
    impl = proj / "impl.py"
    site = inj.find_sites(IMPL, "M2")[0]
    backup = inj.inject(impl, site)

    verdict = bl.is_surviving(proj)

    assert verdict.get("surviving") is True, (
        f"边界未覆盖，变异本应存活: {verdict}")
    assert verdict.get("captured_by_tests") is False, verdict
    inj.revert(backup)


def test_mutation_captured_when_tests_cover_the_boundary(tmp_path):
    """测试覆盖了边界 → 变异被抓 → **不计入**基线分母。

    这条与上一条构成对照：判定必须真的跑测试，而不是猜。
    """
    proj = _project(tmp_path, TEST_TIGHT)
    impl = proj / "impl.py"
    backup = inj.inject(impl, inj.find_sites(IMPL, "M2")[0])

    verdict = bl.is_surviving(proj)

    assert verdict.get("surviving") is False, (
        f"边界已覆盖，变异本应被抓住: {verdict}")
    assert verdict.get("captured_by_tests") is True, verdict
    inj.revert(backup)


def test_survival_check_reports_unavailable_when_no_tests(tmp_path):
    """没有测试时不能报「存活」—— 那是「未测量」（DEV-PROTOCOL 三态）。

    无测试的项目里任何变异都「不被捕获」，若记成存活，
    基线的分母会被一堆无意义样本灌大。
    """
    d = tmp_path / "empty"
    d.mkdir()
    (d / "impl.py").write_text(IMPL, encoding="utf-8")

    verdict = bl.is_surviving(d)

    assert verdict.get("surviving") is None, (
        f"无测试时必须是三态的 ❓ 而非 True: {verdict}")
    assert verdict.get("status") == "unavailable", verdict


# ── 第 4 步：重复采样的三态（3.4）──

def test_three_archive_runs_are_not_detected():
    """**对照组的典型结果**：3 次全 05-archive = 未检出。

    ⚠️ 这是期望值。缺陷被放过，说明基线如实记录了改造前的无能。
    """
    assert bl.classify_runs(["05-archive"] * 3) == "not_detected"


def test_three_non_archive_runs_are_detected():
    """3 次全部返工 = 检出（对照组不该出现，但判定要能表达）。"""
    assert bl.classify_runs(["03-coding"] * 3) == "detected"


def test_mixed_runs_are_unstable_not_two_state():
    """1-2 次命中 → `unstable`，**不得**归入任何一侧（3.4）。

    LLM 采样波动会让「偶然发现」看起来像能力。二态化会把这种波动
    直接记成检出，虚高整个基线。
    """
    assert bl.classify_runs(["05-archive", "03-coding", "05-archive"]) == \
        "unstable"
    assert bl.classify_runs(["03-coding", "03-coding", "05-archive"]) == \
        "unstable"


def test_empty_runs_are_unavailable_not_not_detected():
    """一次都没跑成 → `unavailable`，不是「未检出」。

    「没测」与「测了但没发现」是两件事，而本任务的正确结果恰好是后者
    —— 两者混淆会让一次失败的采集看起来像一份成功的基线。
    """
    assert bl.classify_runs([]) == "unavailable"


# ── 检出率（3.2 / 验收 7）──

def test_detection_rate_is_zero_for_all_missed_samples():
    """⚠️ **`0.0` 是预期结果，不是失败**（9.3 / 9.4）。"""
    samples = [
        {"id": "M2-1", "surviving": True, "verdict": "not_detected"},
        {"id": "M2-2", "surviving": True, "verdict": "not_detected"},
    ]

    assert bl.detection_rate(samples) == 0.0


def test_unstable_samples_are_excluded_from_the_rate():
    """`unstable` 既不计入分子也不计入分母 —— 否则三态白设了。"""
    samples = [
        {"id": "a", "surviving": True, "verdict": "not_detected"},
        {"id": "b", "surviving": True, "verdict": "unstable"},
        {"id": "c", "surviving": True, "verdict": "detected"},
    ]

    # 分母只有 a 与 c，分子只有 c
    assert bl.detection_rate(samples) == 0.5


def test_captured_mutations_are_excluded_from_the_denominator():
    """被现有测试抓住的变异不进分母（验收 8：M1 的教训）。

    它们检验的是测试套件而非 reviewer，留在分母里会稀释检出率。
    """
    samples = [
        {"id": "a", "surviving": True, "verdict": "not_detected"},
        {"id": "m1", "surviving": False, "verdict": "detected"},
    ]

    assert bl.detection_rate(samples) == 0.0


def test_detection_rate_is_none_when_nothing_measurable():
    """没有可用样本时是 `None`（未测量），**不是** `0.0`（测量为零）。

    这一对区分是本任务最需要守住的：正确结果恰好是 0.0，
    若「没测」也写 0.0，那份基线就无法自证它到底测过没有。
    """
    assert bl.detection_rate([]) is None
    assert bl.detection_rate([{"id": "x", "surviving": True,
                               "verdict": "unavailable"}]) is None
