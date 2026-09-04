"""03 的 prompt 必须在开头就要求先写测试 —— 任务 `44444` 的死锁。

## 现场

`workspace/tasks/44444/.log`：63 次工具调用，涉及 test 的只有一次 ——
`mkdir -p backend/routers backend/tests frontend/src` 建了个**空目录**，
此后再没碰过。agent 全程用 `bash` 起 uvicorn、手工验证接口，然后产出里写
「后端 API 所有接口测试通过」。它没说谎，它真的验证过，只是用的不是 pytest。

准出时门禁拦下：

    ❌ 无测试：03a 阶段必须先写测试文件（test_*.py / *_test.py / tests/）

判据是对的 —— 红绿流程确实没走。**缺的是指令。**

## 根因：判据要求的动作，指令从未要求过

`sw_lib/prompts/templates/03-coding.yaml` 有 1400 字讲记账格式、README
要求、围栏保护，**没有一句要求先写测试**。它只说 `## Red-Green` 那一节要写
「可复现的真实命令」—— 那是**记账格式**要求，不是**动作**要求。

「先写测试」四个字确实在 prompt 里，位置是 28903 字符中的第 **26707 位
（92% 处）**，来自 `hooks/03-coding.md` 的自动注入。段落顺序：

           0  全局项目规范 (INSTRUCTIONS.md)   17747 字符，占 61%
       17747  阶段 system_prompt                 只有 839 字符
       19526  前一阶段产出
       23398  当前阶段模板
       26118  强制规则 (03-coding)             ← 「先写测试」在这里

agent 读到 92% 处才第一次被告知红绿顺序，而它对「这阶段要干什么」的理解
早在读 stage system_prompt 时就成型了。

## 形状

S2（阶段错位）的变体：**指令与硬约束各自独立，互不校验**。
形状库 S2 实例 1 记的是「prompt 说用 write_file 回填模板，权限层
deny workspace/**」—— 同一个病：一侧要求的东西，另一侧从不确认它被说过。

harness 有一条硬判据守着红见证，却没有任何机制确认「这条判据要求的动作，
agent 被告知过没有」。

## 判据的设计

不检查字符串出现位置（那会把判据绑死在文案排版上），而是检查
**stage system_prompt 自身**是否包含红绿顺序的要求 —— 那是 agent 理解
「本阶段要干什么」的主要来源，也是唯一由本阶段独占的段落。
"""

import pytest

from sw_lib.core.config import ROOT
from sw_lib.prompts.builder import PromptBuilder
from sw_lib.prompts.registry import PromptRegistry

_STAGE = "03-coding"


def _registry():
    return PromptRegistry(ROOT / "sw_lib" / "prompts" / "templates")


def _stage_system_prompt() -> str:
    """只取 03 自己的 system_prompt，不含注入的全局规范与 hook 文档。

    这是本文件的判据落点：hook 文档在 92% 处提过红绿，但那来自另一个
    文件的自动注入。**本阶段自己的指令**里有没有说，是另一回事。
    """
    tpl = _registry().get(_STAGE)
    return tpl["system_prompt"]


# ── 主判据：动作要求必须在本阶段的指令里 ──

def test_stage_prompt_requires_writing_tests_first():
    """03 的 system_prompt 必须明确要求「先写测试」。

    任务 44444 的 agent 读完这段之后直接开始写实现 —— 因为这段
    从来没说过要先写测试。它不是不听话，是没被告知。
    """
    text = _stage_system_prompt()

    assert "先写测试" in text or "测试先于实现" in text, (
        "03 的 system_prompt 没有要求先写测试 —— 而准出判据（red_witness）"
        "硬要求它。指令与判据各自独立，互不校验（形状 S2）:\n"
        f"{text[:400]}")


def test_stage_prompt_names_the_red_green_order():
    """必须说清顺序，不只是「要写测试」。

    「写测试」和「先写测试再写实现」是两件事。44444 的 agent 若在实现
    之后补测试，红见证同样失败（退出码 0，没有断言先失败过）——
    顺序本身就是判据的一部分。
    """
    text = _stage_system_prompt()

    assert "实现" in text and ("→" in text or "再写" in text or "之后" in text), (
        "03 的 system_prompt 没说清测试与实现的先后 —— "
        f"补测试与先写测试对红见证是两种结果:\n{text[:400]}")


def test_stage_prompt_says_import_error_is_not_red():
    """必须说明「造红」会被拒 —— 这是最容易踩的坑。

    引用不存在的模块得到 ImportError 看起来也是红，但 harness 判它为
    造红并拒绝。agent 不被提前告知，就会用最自然的方式踩进去，
    然后收到一句它无法理解的拒绝。
    """
    text = _stage_system_prompt()

    assert "断言" in text, (
        "03 的 system_prompt 没说清「红必须是断言失败」—— "
        f"ImportError 不算红，这条不说 agent 必然会踩:\n{text[:400]}")


def test_red_green_requirement_appears_early_in_full_prompt():
    """在完整 prompt 里，红绿要求必须出现在前半部分。

    44444 的现场：第一次提到「先写测试」是在 92% 处（26707/28903），
    来自 hook 文档的注入。这条判据守住「它不能只在末尾出现」。

    阈值取 50%：stage system_prompt 起点在 61% 处（前面是 17747 字符的
    全局规范），所以要过这条，红绿要求必须进入更靠前的段落 ——
    也就是必须写进 stage 自己的指令里，而不是只靠 hook 文档兜底。
    """
    builder = PromptBuilder(_registry())
    prompt = builder.build("pytest-rg-instruction", _STAGE, 2) or ""
    assert prompt, "prompt 生成失败"

    positions = [prompt.find(kw) for kw in ("先写测试", "测试先于实现")]
    positions = [p for p in positions if p >= 0]
    assert positions, "完整 prompt 里根本没提「先写测试」"

    first = min(positions)
    ratio = first / len(prompt)
    assert ratio < 0.5, (
        f"红绿要求第一次出现在 {ratio:.0%} 处（第 {first} 字符，"
        f"全长 {len(prompt)}）—— agent 读到那里时对本阶段的理解早已成型。"
        "任务 44444 就是这样：92% 处才第一次看到。")


# ── 边界：判据不得恒真 ──

def test_criterion_would_catch_the_44444_shape():
    """判据必须能抓住 44444 那份 prompt —— 否则它什么都没守住。

    直接拿当时的文案（不含红绿要求的版本）验一次：判据必须报红。
    这条守的是「判据不恒真」—— 一个对任何文案都通过的检查等于没有。
    """
    shape_44444 = (
        "你是 Harness-Flow 平台的 AI Agent。\n"
        "产出落点：代码改在目标仓库里，本阶段的记账则用 `write` 工具回填。\n"
        "  - `## Red-Green`：**Verify cmd** 要写成可复现的真实命令\n"
        "交付物里还有一份 README.md。\n"
        "请开始 编码 阶段的工作。\n"
    )

    assert "先写测试" not in shape_44444 and "测试先于实现" not in shape_44444, \
        "44444 的文案样本被改错了 —— 它本该是「没有红绿要求」的那一版"
