"""B0 的 3.2 / 验收 4、5、11：归档落盘与**不可覆盖**保护。

这份归档无法重新生成（B0 第 10 节）。因此本文件里最重要的不是
「能不能写出去」，而是「能不能防住被写坏」：

- 验收 4：落盘后只读，重复运行不覆盖；
- 验收 11：对同一日期重复采集时报错或另建文件，**不静默合并**；
- 验收 5：mock 下 `detection_rate` 是 `null` 而不是 `0.0`。

最后一条的陷阱在 9.3 写明：断言 `not detection_rate` 会让 `0.0` 也通过，
而 0.0 与 None 恰好是本任务最需要区分的一对。
"""

import json
import os
import stat
from pathlib import Path

import pytest

from sw_lib.probe import baseline as bl


@pytest.fixture(autouse=True)
def _isolate_probe_dir(tmp_path, monkeypatch):
    """全程 tmp_path。

    B0 的 9.2 第 3 条：A0 实施期曾用真实路径探针截断
    `workspace/tasks/T1/.state`，而本任务的破坏面更大 ——
    它既改源码又跑流程，且产物**严禁删除**。
    """
    monkeypatch.setattr(bl, "PROBE_DIR", tmp_path / "probe")


def _payload(**over):
    base = {
        "captured_at": "2026-08-23T23:00:00",
        "baseline_purity": "review_side_clean",
        "env": {"mode": "real"},
        "samples": [{"id": "M2-1", "surviving": True,
                     "verdict": "not_detected"}],
        "detection_rate": 0.0,
        "sample_count": 1,
    }
    base.update(over)
    return base


# ── 验收 4：只读 ──

def test_archive_is_written_and_marked_read_only():
    path = bl.write_archive(_payload())

    assert path is not None and Path(path).exists(), "归档没落盘"
    mode = Path(path).stat().st_mode
    assert not (mode & stat.S_IWUSR), (
        "归档必须是只读 —— 它不可重新生成，任何后续写入都是破坏")


def test_archive_records_read_only_flag_in_content():
    path = bl.write_archive(_payload())
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    assert data.get("read_only") is True, data


# ── 验收 11：不可覆盖（保护一份无法重做的数据）──

def test_second_capture_same_day_does_not_overwrite():
    """同一天再采一次，**不得覆盖**已有文件（验收 11）。

    只断言「文件存在」是不够的 —— 覆盖之后它也存在。
    这里比对内容：第一份必须逐字不变。
    """
    first = Path(bl.write_archive(_payload(sample_count=1)))
    original = first.read_bytes()

    second = bl.write_archive(_payload(sample_count=999))

    assert first.read_bytes() == original, "第一份基线被覆盖了 —— 数据已永久丢失"
    assert second is not None and Path(second) != first, \
        "第二次采集应另建文件而不是静默合并"


def test_second_capture_creates_a_distinct_file():
    """另建的文件必须能被识别为第二次采集，而不是替换第一次。"""
    first = Path(bl.write_archive(_payload()))
    second = Path(bl.write_archive(_payload()))

    assert first.exists() and second.exists()
    assert first.name != second.name


def test_write_archive_never_silently_merges():
    """两次采集的样本不得被合并进同一份文件。

    静默合并比覆盖更隐蔽：文件还在、样本变多了，
    但那份数据已经不对应任何一个确定的采集时点。
    """
    first = Path(bl.write_archive(_payload(
        samples=[{"id": "a", "surviving": True, "verdict": "not_detected"}])))
    bl.write_archive(_payload(
        samples=[{"id": "b", "surviving": True, "verdict": "not_detected"}]))

    data = json.loads(first.read_text(encoding="utf-8"))
    ids = [s["id"] for s in data["samples"]]
    assert ids == ["a"], f"第一份归档被混入了后一次的样本: {ids}"


# ── 验收 5：mock 下是 null 而非 0.0 ──

def test_mock_mode_writes_null_detection_rate(monkeypatch):
    """mock 下 `detection_rate` 必须是 `None`（写进 JSON 即 null）。

    ⚠️ 不能断言 `not rate` —— `0.0` 也满足那个条件，
    而 0.0（测量为零）与 null（未测量）正是本任务最需要区分的一对（9.3）。
    """
    monkeypatch.setattr(bl, "is_mock_agent", lambda: True)

    path = bl.write_archive(_payload(detection_rate=0.0))
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    assert data["detection_rate"] is None, (
        f"mock 下必须是 null 而不是 {data['detection_rate']!r} —— "
        f"mock 采到的是夹具而非能力")
    assert data["env"]["mode"] == "mock", data["env"]


def test_real_mode_keeps_zero_detection_rate(monkeypatch):
    """真实模式下 `0.0` 必须被原样保留 —— 它是有效的测量结果。

    与上一条构成对照：不能为了修 mock 那条而把 0.0 也变成 null。
    """
    monkeypatch.setattr(bl, "is_mock_agent", lambda: False)

    path = bl.write_archive(_payload(detection_rate=0.0))
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    assert data["detection_rate"] == 0.0, (
        "真实模式的 0.0 是预期结果，不得被改写成 null")


# ── 归档内容的完整性 ──

def test_archive_keeps_purity_and_env(monkeypatch):
    """污染标注与环境指纹必须在归档里 —— 那是事后无法补证的部分。"""
    monkeypatch.setattr(bl, "is_mock_agent", lambda: False)
    path = bl.write_archive(_payload())
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    assert data["baseline_purity"] == "review_side_clean"
    assert data["env"].get("mode") == "real"
    assert data.get("captured_at"), "缺采集时刻"
