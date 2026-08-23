"""A3 的第 7 步接线：进入 04 时事实包被真实生成。

单元测试全绿不等于机制接通 —— A2 曾 1053 条全绿而 e2e 一跑就连出两个跨进程
bug。本文件盯的就是「generate() 有没有真的被调用」这一环。
"""

import pytest

from sw_lib.workflow import runtime as RT


def test_advance_into_review_triggers_fact_pack(monkeypatch, make_task, sign_gate):
    calls = []
    monkeypatch.setattr(RT.WorkflowRuntime, "_generate_fact_pack",
                        staticmethod(lambda name: calls.append(name)))

    task = make_task("pytest-a3wire", stage="03-coding", stage_idx=2)
    sign_gate(task, "03-coding")
    RT.WorkflowRuntime.advance(task)

    assert calls == [task], "推进到 04 未触发事实包生成"


def test_advance_into_other_stages_does_not_trigger(monkeypatch, make_task, sign_gate):
    calls = []
    monkeypatch.setattr(RT.WorkflowRuntime, "_generate_fact_pack",
                        staticmethod(lambda name: calls.append(name)))

    task = make_task("pytest-a3wire2", stage="01-brainstorming", stage_idx=0)
    sign_gate(task, "01-brainstorming")
    RT.WorkflowRuntime.advance(task)

    assert calls == []


def test_fact_pack_failure_does_not_block_advance(monkeypatch, make_task, sign_gate):
    """生成失败不阻断推进，但必须留痕。

    推进已经落盘，抛出去会让调用方看到「推进失败」而状态已经变了。
    """
    logged = []
    monkeypatch.setattr(RT, "sw_log",
                        lambda name, msg, kind="sw": logged.append((kind, msg)))

    def boom(task):
        raise RuntimeError("基线缺失")

    monkeypatch.setattr("sw_lib.workflow.fact_pack.generate", boom)

    task = make_task("pytest-a3wire3", stage="03-coding", stage_idx=2)
    sign_gate(task, "03-coding")
    st = RT.WorkflowRuntime.advance(task)

    assert st["stage"] == "04-review"
    assert any("事实包生成失败" in msg for _, msg in logged), \
        "生成失败被静默 —— C1 的解法依赖这份数据，缺了必须可见"
