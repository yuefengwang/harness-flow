"""全局规范不得压在阶段指令前面 —— 44444 那个形状的另外四个实例。

## 由来

任务 `44444` 的 03 阶段，「先写测试」这句话出现在 28903 字符的第 26707 位
（92% 处），而 `{global_rules}` 展开的 17747 字符全局规范占据了开头 61%。
阶段自己的指令只有 839 字符，被埋在中间。

修 03 的时候只改了 `03-coding.yaml` 一个文件。按 same-shape-sweep 扫一遍
才发现**五个阶段的 yaml 全都是 `{global_rules}` 打头**（S10「修了一半」，
判例 2.9.9 第 2 条同形状：模板只有 01 修了落盘指令，02 漏了）。

## 判据落在哪

不落在「每个 yaml 都要把占位符挪到末尾」—— 那是五份各自维护的约定，
下一个新阶段照旧会写在开头。落点是**渲染结果里的位置关系**：
阶段指令的首句必须早于全局规范出现。

`{global_rules}` 的位置由 `PromptBuilder` 统一决定（`_STAGE_GLOBAL_RULES_TAIL`），
yaml 里写不写都不改变结论 —— 这样这个形状就没有第六个实例可长。
"""

import shutil

import pytest

from sw_lib.core.config import ROOT, STAGES, TASKS
from sw_lib.prompts.builder import PromptBuilder
from sw_lib.prompts.registry import PromptRegistry

_TASK = "pytest-globalrules-order"

#: 全局规范段的起始标记，由 `_read_global_rules()` 产出。
_GLOBAL_MARK = "=== 全局项目规范"


@pytest.fixture
def builder():
    return PromptBuilder(PromptRegistry(ROOT / "sw_lib" / "prompts" / "templates"))


@pytest.fixture
def task():
    created = TASKS / _TASK
    shutil.rmtree(created, ignore_errors=True)
    created.mkdir(parents=True, exist_ok=True)
    try:
        yield _TASK
    finally:
        shutil.rmtree(created, ignore_errors=True)


def _rendered(builder, task, stage):
    idx = STAGES.index(stage)
    out = builder.build(task, stage, idx)
    assert out, f"{stage} 未产出 prompt"
    return out


# ── 前提自检：全局规范确实被注入，且确实很长 ──

def test_global_rules_are_injected_and_large(builder, task):
    """前提：这段东西真的在 prompt 里，而且体量足以埋掉阶段指令。

    如果哪天 `_read_global_rules()` 改成不注入了，本文件其余判据会变成
    恒真（形状 S5）。这条自检负责在那时候把它们叫醒。
    """
    prompt = _rendered(builder, task, "03-coding")
    assert _GLOBAL_MARK in prompt, "全局规范没被注入 —— 其余判据已恒真，请重写本文件"

    start = prompt.index(_GLOBAL_MARK)
    tail = prompt[start:]
    assert len(tail) > 3000, (
        f"全局规范只有 {len(tail)} 字符，不再构成「埋掉阶段指令」的量级；"
        "本文件的前提已变，请复核判据是否仍有意义")


# ── 主判据：五个阶段都要过 ──

@pytest.mark.parametrize("stage", STAGES)
def test_stage_instruction_precedes_global_rules(builder, task, stage):
    """阶段指令必须出现在全局规范**之前**。

    模型对长 prompt 的注意力偏向开头。让 17747 字符的 harness 内部规范
    打头，等于把本阶段真正要做的事推到中后段 —— 44444 就是这么丢掉
    「先写测试」的。
    """
    prompt = _rendered(builder, task, stage)
    if _GLOBAL_MARK not in prompt:
        pytest.skip("本环境未注入全局规范")

    anchor = "你是 Harness-Flow 平台的 AI Agent"
    assert anchor in prompt, f"{stage} 的阶段指令缺少锚点 {anchor!r}，判据落点失效"

    assert prompt.index(anchor) < prompt.index(_GLOBAL_MARK), (
        f"{stage}: 全局规范排在阶段指令之前 —— 阶段指令被埋在 "
        f"{prompt.index(anchor)}/{len(prompt)} 处。"
        "这是任务 44444 的形状（S10 修了一半）")


@pytest.mark.parametrize("stage", STAGES)
def test_stage_prompt_starts_with_stage_identity(builder, task, stage):
    """prompt 的**开头**就要说清「你是谁、在哪个阶段」。

    只断言「早于全局规范」还不够：两段都挪到末尾也能满足它。
    开头 500 字符内必须出现阶段标识。
    """
    prompt = _rendered(builder, task, stage)
    head = prompt[:500]
    assert stage in head, (
        f"{stage}: 开头 500 字符里没有阶段标识，agent 要读很久才知道自己在做什么:\n"
        f"{head[:200]}")


# ── 位置由 builder 统一决定，不靠各 yaml 自觉 ──

@pytest.mark.parametrize("stage", STAGES)
def test_yaml_need_not_place_the_placeholder(stage):
    """yaml 里不再需要写 `{global_rules}` —— 位置由 builder 统一负责。

    这条是防复发的关键：只要位置还由五份 yaml 各自决定，
    第六个阶段照旧会把它写在开头。
    """
    tpl = PromptRegistry(ROOT / "sw_lib" / "prompts" / "templates").get(stage)
    assert "{global_rules}" not in tpl["system_prompt"], (
        f"{stage} 的 yaml 仍自己摆放 {{global_rules}} —— "
        "位置应交给 PromptBuilder 统一决定，否则同形状会长出第六个实例")
