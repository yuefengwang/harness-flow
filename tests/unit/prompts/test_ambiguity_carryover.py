"""01 的自评结论必须传到 02 —— 放行了不等于没事发生。

A13 把歧义自评从硬拦降级为「放行 + 记账」。降级之后立刻出现一个新缺口：
**记了账没人读，就等于没记。** 那是形状库 S7（判据存在、无人调用）的
第八次，而且这一次是修复自己引入的 —— 与 2.9.16 的教训同型：
「修复引入的新代码也要过形状库」。

01 放行时若自评是 `unavailable` / `below_threshold`，说明需求**可能**
还带着未澄清的假设。02 是第一个消费这份需求的阶段，它必须被告知，
否则「不带假设进入下一阶段」这条规则在降级之后就只剩一句空话。

注入的是 harness 从 `.state` 读出的**记录**（自己写的），
不是 agent 的自述 —— 这与 A13 的原则一致：证据流向下游，自述留在原地。
"""

import json
import shutil

import pytest

from sw_lib.core.config import TASKS
from sw_lib.prompts.builder import PromptBuilder
from sw_lib.prompts.registry import PromptRegistry
from sw_lib.core.config import ROOT
from sw_lib.workflow import stage_state as ss

_TASK = "pytest-amb-carryover"


def _builder():
    return PromptBuilder(PromptRegistry(ROOT / "sw_lib" / "prompts" / "templates"))


@pytest.fixture
def task():
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({
        "id": _TASK, "stage": "02-planning", "stage_idx": 1,
        "stage_status": "running", "stages": {},
    }), encoding="utf-8")
    (d / "01-brainstorming.md").write_text(
        "# 01\n\n需求已梳理，技术栈 React + FastAPI。\n", encoding="utf-8")
    yield _TASK
    shutil.rmtree(d, ignore_errors=True)


def test_unavailable_score_is_carried_into_02_prompt(task):
    """01 的自评读不到 → 02 的 prompt 必须说这件事。

    否则 02 会以为需求已经澄清完毕 —— 而事实是**没人验证过**。
    """
    ss.record_ambiguity(task, "01-brainstorming", ss.AMBIGUITY_UNAVAILABLE,
                        reason="产出区中未找到 0-10 的歧义自评")

    prompt = _builder().build(task, "02-planning", 1) or ""

    assert "歧义" in prompt, \
        "01 的自评状态没有传到 02 —— 记了账无人读，等于没记（S7）"


def test_below_threshold_is_carried_with_the_number(task):
    """低分要带着数字过去，02 才知道有多不确定。"""
    ss.record_ambiguity(task, "01-brainstorming", ss.AMBIGUITY_BELOW,
                        score=4, reason="自评 4 低于目标 8")

    prompt = _builder().build(task, "02-planning", 1) or ""

    assert "4" in prompt and "歧义" in prompt, \
        f"低分未传到 02:\n{prompt[-600:]}"


def test_ok_score_does_not_add_noise(task):
    """自评达标时不该在 prompt 里加警告 —— 判据不得对正常情况喊话。

    这条守着「提示不泛滥」：每个阶段都挂一句无差别警告，
    等于没有警告（A2 的失败信息纪律）。
    """
    ss.record_ambiguity(task, "01-brainstorming", ss.AMBIGUITY_OK, score=9)

    prompt = _builder().build(task, "02-planning", 1) or ""

    assert "未澄清" not in prompt, \
        f"自评达标却仍在警告 —— 提示泛滥:\n{prompt[-400:]}"
