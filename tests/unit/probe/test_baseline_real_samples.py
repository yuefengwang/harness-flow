"""B0 验收 7：改造前的「未检出」已被真实采集到，不再是零样本。

B0 交付时只落了一份零样本指纹（`samples: []`、`detection_rate: null`），
把最核心的验收 7 悬在「❓ 未验证」。本文件钉住那一步已经补上：
磁盘上必须存在一份**有真实样本**的基线。

为什么要有这条测试：`detection_rate == 0.0` 与 `null` 看起来都像
「没检出」，但前者是测量结果、后者是根本没测（B0 的 9.3）。
一份零样本归档能让整套设计**看起来**有对照组而实际没有 ——
而这份数据不可重采，发现得越晚越无法补救。

⚠️ 本文件的断言方向是「期望未检出」。detection_rate 为 0.0 是**成功**。
若有人把它改成断言非 0，那正是 B0 的 9.4 警告的那种破坏。
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROBE_DIR = ROOT / "workspace" / "probe"


def _archives():
    return sorted(PROBE_DIR.glob("baseline-*.json"))


def _with_samples():
    """挑出有真实样本的那些归档。"""
    out = []
    for p in _archives():
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if r.get("samples"):
            out.append((p, r))
    return out


def _require_samples():
    """取样本，取不到就直接红。

    ⚠️ 这个守卫不是冗余。少了它，下面每条 `for path, r in _with_samples()`
    在零样本状态下都是**空循环**：一个断言都不执行就"通过"。
    那种绿测不出任何东西 —— 与协议 1.1 说的「让 import 成功就能转绿」
    是同一种假绿，而本文件要防的恰恰是「基线看起来有对照组而实际没有」。
    """
    got = _with_samples()
    assert got, (
        "workspace/probe 下没有任何带样本的基线。零样本归档只钉住了采集时刻的"
        "环境指纹，验收 7（改造前的未检出）仍未被观测 —— 而该观测不可重采。")
    return got


def test_a_baseline_with_real_samples_exists():
    """必须至少有一份带真实样本的基线 —— 零样本不算对照组。"""
    got = _with_samples()
    assert got, (
        "workspace/probe 下没有任何带样本的基线。零样本归档只钉住了采集时刻的"
        "环境指纹，验收 7（改造前的未检出）仍未被观测 —— 而该观测不可重采。")


def test_samples_come_from_real_mode_not_mock():
    """mock 下 reviewer 对任何变异给同样输出，采到的是夹具而非能力（B0 第 4 节）。"""
    for path, r in _require_samples():
        assert r.get("env", {}).get("mode") == "real", (
            f"{path.name} 的样本不是 real 模式采集的，不构成有效基线")


def test_detection_rate_is_measured_zero_not_null():
    """0.0 与 null 必须区分：前者是「测了、没检出」，后者是「没测」。

    这一对区分是 B0 最需要守住的东西（9.3）。
    """
    for path, r in _require_samples():
        rate = r.get("detection_rate")
        assert rate is not None, (
            f"{path.name} 有样本却没有检出率 —— 无法自证测过没有")
        assert rate == 0.0, (
            f"{path.name} 检出率为 {rate}，非 0。按 B0 的 9.4：先怀疑采集脚本，"
            f"不得通过调整注入方式或 prompt 让基线『变好看』。")


def test_every_sample_is_surviving_and_screened():
    """只有存活变异才有资格进观测（验收 8）—— 被测试抓住的检验的是套件。"""
    for path, r in _require_samples():
        for s in r["samples"]:
            assert s.get("surviving") is True, (
                f"{path.name} 的 {s.get('id')} 未存活却进了观测")
            assert s.get("operator") != "M1", (
                "M1 会被现有测试抓住，刻意排除在基线之外（A11 的 2.1）")


def test_annotation_is_done_not_pending():
    """`reviewer_mentioned_defect` 是人工标注项，不得留空（B0 的 3.5）。

    默认 None 会让一份从未标注的基线看起来像「已确认 reviewer 没提到缺陷」,
    那是伪造出来的结论。
    """
    for path, r in _require_samples():
        for s in r["samples"]:
            assert s.get("annotation_pending") is False, (
                f"{path.name} 的 {s.get('id')} 人工标注未完成")
            assert isinstance(s.get("reviewer_mentioned_defect"), bool), (
                f"{path.name} 的 {s.get('id')} 缺人工标注结论")


def test_review_outputs_are_kept_in_repo():
    """人工标注的依据必须随归档留在仓库里，不能只在 /tmp。

    标注是一句人给的结论；没有原文就无从复核，而这份数据不可重采。
    """
    for path, r in _require_samples():
        for s in r["samples"]:
            outs = s.get("review_outputs") or []
            assert outs, f"{path.name} 的 {s.get('id')} 没有留审查产出"
            for rel in outs:
                assert not rel.startswith("/tmp"), (
                    f"{rel} 指向 /tmp —— 重启即失效，标注将无从复核")
                assert (ROOT / rel).exists(), f"{rel} 不存在于仓库"


def test_capture_context_records_the_sandbox_sha():
    """必须记下采集实际跑在哪个代码状态上。

    落盘时的工作树已含 A3/A4/A5，而采集跑在 fc6a201 的副本上（审查侧原貌）。
    只记落盘时的 sha 会让这份数据声称自己采自一个它并未运行过的状态。
    """
    for path, r in _require_samples():
        ctx = r.get("capture_context") or {}
        sha = ctx.get("sandbox_from_sha")
        assert sha and len(sha) >= 7, (
            f"{path.name} 未记录采集所用的沙盒 sha，无法判断对照组是否纯净")
        assert ctx.get("injector_meta_verified") is True, (
            "未经元测试的注入器不得用于基线采集（B0 的 9.1）")


def test_unstable_verdict_is_preserved_not_binarized():
    """三次结果不一致必须记为 unstable，不得二态化（B0 的 3.4）。

    LLM 采样波动会让「偶然发现」看起来像能力。本次采集实际就撞上了：
    M2-1 三轮给出 None / 05-archive / 03-coding 三种结果。
    """
    verdicts = {s.get("verdict") for _, r in _require_samples()
                for s in r["samples"]}
    assert verdicts, "没有样本可判"
    assert verdicts <= {"not_detected", "detected", "unstable", "unavailable"}, \
        f"出现未知判定态: {verdicts}"
