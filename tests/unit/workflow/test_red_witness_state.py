"""A2 第 4 节：`red_witness` 子树的读写，以及与 A0 的接口。

纪律（承自 A0 的 9.1）：**绕过受控入口只用于制造失败。**
凡断言「通过」的用例一律走 `update_state`；只有构造篡改样本时才直接改
`.state`，否则测的就不是真实写入路径。
"""

import json

import pytest

from sw_lib.core import evidence as ev
from sw_lib.core import state as state_mod
from sw_lib.workflow import red_witness as rw


@pytest.fixture(autouse=True)
def _isolate_key(tmp_path, monkeypatch):
    """密钥重定向到 tmp 并清 lru_cache（A0 的 2.9.3 踩过的顺序依赖假绿）。"""
    monkeypatch.setattr(ev, "KEY_PATH", tmp_path / ".evidence_key")
    ev.get_key.cache_clear()
    yield
    ev.get_key.cache_clear()


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "TASKS", tmp_path)
    name = "t-rw"
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    state_mod.write_state(name, {"id": name, "stage": "03-coding",
                                 "stage_idx": 2, "stage_status": "running"})
    return name


# ── phase 默认值 ──

def test_missing_record_is_none_not_03a(iso):
    """无记录时 phase 是 ``none``，**不是** ``03a``。

    ⚠️ 这条用例是对前一版 `test_missing_record_defaults_to_03a` 的
    **显式重做**（DEV-PROTOCOL 1.2：发现测试写错必须声明并回到步 1，
    不得在实现之后静默修改）。前一版直接编码了 A2 原文 R1
    「无记录时视为 03a」，而实测证明那条设计是错的：

    从测试结果反推子阶段不可能 —— 「测试红」既可能是「03a 刚写完测试」，
    也可能是「03b 实现没写对」，正确处置恰好相反（放行 / 拦住）。
    按 03a 默认会推翻既有契约「失败的测试不许过闸」
    （实测 `test_hook_pytest_invocation.py::test_real_failure_still_blocks` 转红），
    并让存量任务永久卡死（实测 9 条既有用例转红，含整条 04↔03 返工链路）。

    改法：phase 只由 `begin_test_phase()` 显式写入，生产入口是
    `hooks/pre_check_03-coding.sh`。详见 test_red_witness_phase_explicit.py。
    """
    assert rw.read_phase(iso) == "none"


def test_phase_becomes_03a_only_after_explicit_begin(iso):
    """上一条的对照：显式进入之后才是 03a —— 默认值不是把它焊死在 none。"""
    rw.begin_test_phase(iso)
    assert rw.read_phase(iso) == "03a"


def test_phase_advances_to_03b_after_witness(iso):
    """见证到有效的红之后，phase 必须推进到 03b。"""
    verdict = rw.WitnessVerdict("ok", "", 1,
                                failed_nodes=["test_y.py::test_a"])
    rw.record_red(iso, verdict, {"test_y.py": "a" * 64})

    assert rw.read_phase(iso) == "03b"
    record = rw.read_witness(iso)
    assert record["failed_nodes"] == ["test_y.py::test_a"]
    assert record["exit_code"] == 1
    assert record["test_files"] == {"test_y.py": "a" * 64}
    assert record["witnessed_at"], "见证时间戳缺失"


# ── 与 A0 的接口：走受控入口 + 纳入签名 ──

def test_record_red_is_signed(iso):
    """A2 与 A0 的接口：写入后签名必须是 valid。"""
    rw.record_red(iso, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "b" * 64})

    st = state_mod.read_state(iso)
    assert "red_witness" in st
    assert ev.verify_evidence(st).status == "valid"


def test_tampered_witness_is_detected(iso):
    """验收（有效性类）：绕过受控入口改 red_witness 必须被检出为 tampered。

    这里刻意绕过 —— 不绕过就造不出篡改样本（A0 的 9.1）。
    """
    rw.record_red(iso, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "c" * 64})

    sf = state_mod.state_path(iso)
    raw = json.loads(sf.read_text(encoding="utf-8"))
    raw["red_witness"]["test_files"] = {}          # 把冻结哈希抹掉
    sf.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert ev.verify_evidence(state_mod.read_state(iso)).status == "tampered"


def test_writer_does_not_call_write_state_directly():
    """判据类写入必须走 `update_state`（A0 验收 20 的同构约束）。

    源码断言：裸 `write_state` 在并发下会丢更新，而丢掉的是判据。
    """
    import inspect
    src = inspect.getsource(rw)
    assert "write_state(" not in src, \
        "red_witness 出现了裸 write_state 调用 —— 判据写入必须走 update_state"


# ── 转绿记录与单调性 ──

def test_record_green_keeps_failed_nodes(iso):
    """A2 的 4.1：`failed_nodes` 一旦见证并转绿，永久留在判据集里。"""
    rw.record_red(iso, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "d" * 64})
    rw.record_green(iso, rw.WitnessVerdict("ok", "", 0,
                                           passed_nodes=["t.py::a"]))

    record = rw.read_witness(iso)
    assert record["failed_nodes"] == ["t.py::a"], "判据集被转绿覆盖掉了"
    assert record["green_nodes"] == ["t.py::a"]
    assert record["green_at"], "转绿时间戳缺失"
    assert ev.verify_evidence(state_mod.read_state(iso)).status == "valid"


def test_rewitness_increments_counter_and_returns_to_03a(iso):
    """A2 的 3.4「允许的例外」：显式回退到 03a 重做，且次数留痕。"""
    rw.record_red(iso, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "e" * 64})
    rw.request_rewitness(iso, reason="测试写错了")

    assert rw.read_phase(iso) == "03a"
    assert rw.read_witness(iso)["rewitness_count"] == 1


def test_append_node_keeps_set_monotonic(iso):
    """A9 接口：反例返工后转绿时追加进 failed_nodes，且不重复。"""
    rw.record_red(iso, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "f" * 64})
    rw.append_witnessed_nodes(iso, ["t.py::b", "t.py::a"])

    assert rw.read_witness(iso)["failed_nodes"] == ["t.py::a", "t.py::b"]


# ── mock 模式 ──

def test_mock_mode_writes_synthetic_record_with_same_shape(iso, monkeypatch):
    """A2 第 7 节：mock 下写合成记录，但**保留结构**，下游读到的形状一致。"""
    monkeypatch.setattr(rw, "is_mock_agent", lambda: True)
    rw.ensure_mock_witness(iso)

    record = rw.read_witness(iso)
    assert record["mock"] is True
    for key in ("phase", "exit_code", "failed_nodes", "test_files",
                "green_nodes"):
        assert key in record, f"合成记录缺字段 {key}，下游会取不到"
