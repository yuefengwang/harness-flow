"""A3 遗留的第二层：claims 在 **mock 链路**上也必须真的填上内容。

上一轮把 `record_claims()` 接进了 03 准出，单元测试与真实 LLM 链路都验过。
但 e2e（只跑 MockAgent）实测落盘的是 `{'task_ids': [], 'verify_cmd': '',
'files_touched': []}` —— 字段在、内容空。

空 claims 与「没有 claims」在下游是同一件事：验收 18 的 claims-vs-diff
对照仍然一次都不会触发。而 e2e 的检查只断言了字段存在，
于是这条空转路径能一路绿到底 —— 这正是「单元全绿 != 机制接通」的又一例。

根因：MockAgent 的 03 场景只输出「实现摘要 / 产出文件」两节，
不产出模板要求的 `## Task` / `**Verify cmd**` / `## Files Touched`。
mock 是 e2e 的唯一驱动，它不声明，claims 就永远是空的。

判据是**内容非空且与实际写入的文件一致**，不是「字段存在」。
"""

import pytest

from sw_lib.workflow import fact_pack as FP


def _mock(task, stage="03-coding", stage_idx=2):
    from sw_lib.agents.mock import MockAgent
    texts = []
    cb = {"add_log": lambda s, m: texts.append(m), "is_running": lambda: True}
    a = MockAgent(cb, task, stage, stage_idx)
    a.running = True
    return a, texts


def test_mock_coding_output_declares_task_id():
    """mock 的 03 产出必须带可解析的 task id。"""
    a, texts = _mock("rw-claims-e2e-a")
    a._scenario_coding()
    claims = FP.extract_claims("\n".join(texts))
    assert claims["task_ids"], (
        f"mock 03 产出里没有可解析的 task id，claims 会是空的：{claims}")


def test_mock_coding_output_declares_verify_cmd():
    """必须带 verify_cmd —— 它是 03 准出「怎么验的」那一栏。"""
    a, texts = _mock("rw-claims-e2e-b")
    a._scenario_coding()
    claims = FP.extract_claims("\n".join(texts))
    assert claims["verify_cmd"], (
        f"mock 03 产出里没有 Verify cmd：{claims}")


def test_mock_coding_declares_files_it_actually_wrote(tmp_path, monkeypatch):
    """files_touched 必须与真实写入的文件一致 —— 这是对照的核心字段。

    声明一批没写过的文件比不声明更糟：对照会通过，而它对照的是假数据。
    """
    import sw_lib.core.state as S
    import sw_lib.agents.mock as M

    task = "rw-claims-e2e-c"
    target = tmp_path / "proj"
    target.mkdir()
    monkeypatch.setattr(M, "read_state", lambda n: {"target_dir": str(target)},
                        raising=False)

    a, texts = _mock(task)
    monkeypatch.setattr(a, "_resolve_target_dir", lambda: target)
    a._scenario_coding()

    claims = FP.extract_claims("\n".join(texts))
    declared = set(claims["files_touched"])
    assert declared, f"没有声明任何文件：{claims}"

    actual = {p.name for p in target.iterdir() if p.is_file()}
    missing = actual - {d.split("/")[-1] for d in declared}
    assert not missing, (
        f"写了但没声明（漏报）：{missing}。声明={declared} 实际={actual}")


def test_declared_files_are_not_fabricated(tmp_path, monkeypatch):
    """反向：不得声明没写过的文件（多报）。"""
    import sw_lib.agents.mock as M

    target = tmp_path / "proj2"
    target.mkdir()
    a, texts = _mock("rw-claims-e2e-d")
    monkeypatch.setattr(a, "_resolve_target_dir", lambda: target)
    a._scenario_coding()

    claims = FP.extract_claims("\n".join(texts))
    actual = {p.name for p in target.iterdir() if p.is_file()}
    fabricated = {d.split("/")[-1] for d in claims["files_touched"]} - actual
    assert not fabricated, (
        f"声明了没写过的文件（多报）：{fabricated}，实际={actual}")
