"""歧义分数降级为参考：硬规则决定准出，自评不再一票否决。

## 用户拍板的原则

> 真正准确的准入规则应该是基于 evidence 的硬规则，硬规则起主导作用，
> agent 的主观判断作为参考，不能是决定性因素。

歧义分数是 **agent 自评**（自述），不是 harness 观测到的事件（证据）。
拿它当硬拦判据，等于把准出开关交给被判者 —— 它想不写就不写。

## 三个真实任务，同一句报错，三个不同根因（实测）

| 任务 | decisions | 产出区 | 分数 | 真实原因 |
|---|---|---|---|---|
| maybework | 3 | 175 字符 | 读不到 | **写了**「歧义分数达到 8」，正则不认「达到」二字 |
| 7090 | 5 | 360 字符 | 没写 | agent 在等用户回答方案 A，阶段根本没走完 |
| ppppp | 14 | 1985 字符 | 没写 | 问了 14 轮无收敛出口（2.9.10 已修上界，分数仍缺） |

三者都收到「❌ 产出区未给出歧义分数」并被硬拦。**同一句话，指向三个
完全不同的下一步**：改判据、回答问题、给收敛出口。这正是 2.9.16 判过的
「失败信息指错方向」在 01 阶段的实例。

## 本次定案（用户拍板）

1. **分数缺失 → 放行但留痕**。硬规则（产出区实质内容）已经把空转拦住了；
   分数读不到记 `unavailable`（❓），写进 `.state` 供下游报告，
   **不伪造成 ✅**。这与 A2 的 `--abandon-witness`、非 Python 栈让路
   同一条纪律：让路的同时如实记账。
2. **分数低于 8 → 提示但不硬拦，且要求 agent 继续提问**。
   用户原话：「低于分数 8 可以让 agent 自己再次补充提问，消除歧义」、
   「agent 认为歧义分数还很高，那就反复让他问，直到没有歧义」。
   所以低分的下一步是**继续澄清**，不是改数字，也不是卡死。
3. **正则认「达到 8」这类写法**。maybework 的 agent 没做错任何事，
   是判据太窄 —— 判据自己的缺陷不该由 agent 承担。

留痕是硬要求：放行而不记账等于把「没测到」洗成「测过了」，
那是 DEV-PROTOCOL 第 2 节明令禁止的二态化。
"""

import json
import shutil

import pytest

from sw_lib.core.config import TASKS
from sw_lib.workflow import output_check as oc
from sw_lib.workflow import stage_state as ss

_STAGE = "01-brainstorming"


def _mk(name, body):
    """建一个 01 阶段任务，把 body 写进围栏产出区（模拟 harness 落盘）。"""
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    nonce = "a1b2c3d4"
    (d / ".state").write_text(json.dumps({
        "id": name, "stage": _STAGE, "stage_idx": 0,
        "stage_status": "running",
        # decisions 非空：A13 第 1 步的完成性判据要求 01 至少有一轮问答。
        # 本文件测的是**自评**这一条，不是完成性 —— 夹具必须先满足
        # 前置的硬规则，否则红会来自另一条判据，测的就不是本形状了。
        "stages": {_STAGE: {
            "output_nonce": nonce,
            "decisions": {"需求范围？": {
                "answer": "基础电商", "decided_by": "user",
                "decided_at": "2026-09-03T12:00:00"}},
        }},
    }), encoding="utf-8")
    (d / f"{_STAGE}.md").write_text(
        f"# 01-Brainstorming\n\n## AI Output\n"
        f"<!-- sw:ai-output:start {nonce} -->\n{body}\n"
        f"<!-- sw:ai-output:end {nonce} -->\n",
        encoding="utf-8")
    return name


@pytest.fixture
def task():
    made = []

    def _make(name, body):
        made.append(name)
        return _mk(name, body)

    yield _make
    for n in made:
        shutil.rmtree(TASKS / n, ignore_errors=True)


#: 一段有实质内容的产出（> 80 字符），硬规则那一关必过。
#: 分数的有无由各用例自己拼上去。
_SUBSTANCE = (
    "头脑风暴阶段已完成。核心需求已明确：\n"
    "- 商品类型: 实物商品\n"
    "- 技术栈: React 18 + FastAPI\n"
    "- 功能范围: 基础电商（商品展示、购物车、订单管理）\n"
    "产出文件已回填至 01-brainstorming.md。\n"
)


# ── 1. 分数缺失：放行，但必须留痕 ──

def test_missing_score_does_not_block(task):
    """分数读不到不再硬拦 —— 它是自评，不是证据。

    硬规则（产出区实质内容）已经在这条之前把空转拦住了。
    任务 7090 / maybework / ppppp 都卡在这一句上，而三者的真实问题
    没有一个是「少了一个数字」。
    """
    name = task("amb-missing-passes", _SUBSTANCE)
    verdict = oc.check_output(name, _STAGE)

    assert verdict.ok, \
        "分数缺失仍在硬拦 —— 自评不该有一票否决权:\n" + "\n".join(verdict.lines)


def test_missing_score_is_recorded_as_unavailable(task):
    """放行必须留痕：`.state` 里记 `unavailable`，不伪造成通过。

    放行而不记账等于把「没测到」洗成「测过了」（DEV-PROTOCOL 第 2 节）。
    下游报告要靠这条记录把该项显示为 ❓ 而非 ✅。
    """
    name = task("amb-missing-recorded", _SUBSTANCE)
    oc.check_output(name, _STAGE)

    record = ss.read_ambiguity_record(name, _STAGE)
    assert record.get("status") == "unavailable", \
        f"分数缺失未被记账，下游无从判断这项没测到: {record}"
    assert record.get("reason"), "留痕必须说明为什么不可得"


def test_missing_score_output_says_it_is_not_a_pass(task):
    """门禁输出要明说这项是 ❓ 而不是 ✅ —— 三态不得二态化。"""
    name = task("amb-missing-says-unknown", _SUBSTANCE)
    verdict = oc.check_output(name, _STAGE)
    text = "\n".join(verdict.lines)

    assert "❓" in text, f"放行了却没说这项未测到:\n{text}"


# ── 2. 分数低于阈值：不硬拦，但要把 agent 推回去继续提问 ──

def test_low_score_does_not_block(task):
    """低分不再硬拦。

    用户拍板：低于 8 让 agent 自己再补充提问，而不是卡死流程。
    """
    name = task("amb-low-passes", _SUBSTANCE + "\n歧义分数：4\n")
    verdict = oc.check_output(name, _STAGE)

    assert verdict.ok, \
        "低分仍在硬拦:\n" + "\n".join(verdict.lines)


def test_low_score_tells_agent_to_keep_asking(task):
    """低分的下一步是**继续用 question 澄清**，不是改数字。

    失败信息三要件的第三条：一个真实角色在真实阶段能执行的下一步。
    01 阶段的 agent 有 `question` 工具，这条路走得通。
    """
    name = task("amb-low-next-step", _SUBSTANCE + "\n歧义分数：4\n")
    verdict = oc.check_output(name, _STAGE)
    text = "\n".join(verdict.lines)

    assert "question" in text, \
        f"低分没给出「继续提问」这条下一步:\n{text}"
    assert "4" in text and "8" in text, \
        f"没说清当前分数与目标分数:\n{text}"


def test_low_score_is_recorded_for_downstream(task):
    """低分同样留痕，供下游报告与 A9 仲裁使用。"""
    name = task("amb-low-recorded", _SUBSTANCE + "\n歧义分数：4\n")
    oc.check_output(name, _STAGE)

    record = ss.read_ambiguity_record(name, _STAGE)
    assert record.get("score") == 4, record
    assert record.get("status") == "below_threshold", record


# ── 3. 达标分数：照旧通过并留痕 ──

def test_passing_score_is_recorded(task):
    """达标时也要记 —— 只在失败时记账会让「通过」变成没有证据的状态。"""
    name = task("amb-ok-recorded", _SUBSTANCE + "\n歧义分数：9\n")
    verdict = oc.check_output(name, _STAGE)

    assert verdict.ok
    record = ss.read_ambiguity_record(name, _STAGE)
    assert record.get("score") == 9, record
    assert record.get("status") == "ok", record


# ── 4. 判据不得因此变恒真：硬规则仍然拦得住空转 ──

def test_empty_output_is_still_blocked(task):
    """产出区空转仍必须拦下 —— 放宽的是自评，不是硬规则。

    这条是本次改动的边界：若「分数不拦了」顺手把空转也放过，
    就是 helloworld 那次「判据恒真」的重演。
    """
    name = task("amb-empty-still-blocked", "___\n")
    verdict = oc.check_output(name, _STAGE)

    assert not verdict.ok, \
        "空产出竟然过闸了 —— 硬规则被一起放宽了:\n" + "\n".join(verdict.lines)


def test_placeholder_only_output_is_still_blocked(task):
    """只有占位符也照拦。"""
    name = task("amb-placeholder-blocked", "TODO\nFIXME\n___\n")
    verdict = oc.check_output(name, _STAGE)

    assert not verdict.ok, "占位符产出过闸了"


# ── 5. maybework 的真实写法：判据自己的缺陷不该由 agent 承担 ──

def test_score_written_with_chinese_verb_is_read(task):
    """`歧义分数达到 8` 必须读得到 —— 任务 maybework 的真实产出。

    实测：`_SCORE_PATTERNS` 允许关键词后跟 `：` / 空格 / markdown 强调符，
    唯独不允许跟中文动词，「达到」两个字卡在中间。agent 没做错任何事，
    是判据太窄。
    """
    name = task("amb-chinese-verb", _SUBSTANCE + "\n歧义分数达到 8\n")

    assert oc.read_ambiguity_score(name, _STAGE) == 8, \
        "「歧义分数达到 8」读不到 —— maybework 就是因此被硬拦的"


@pytest.mark.parametrize("text,expected", [
    ("歧义分数达到 8", 8),
    ("歧义分数达到8", 8),
    ("歧义分数已达到 9", 9),
    ("歧义分数为 7", 7),
    ("歧义分数是 6", 6),
    ("歧义分数：8", 8),
    ("**歧义分数**：6 分", 6),
    ("Ambiguity Score: 7", 7),
])
def test_score_forms_that_must_be_recognized(task, text, expected):
    """agent 不会照抄我们脑子里的格式。这些都是自然的中文写法。"""
    name = task(f"amb-form-{abs(hash(text)) % 10000}", _SUBSTANCE + f"\n{text}\n")
    assert oc.read_ambiguity_score(name, _STAGE) == expected, \
        f"{text!r} 读不出 {expected}"


@pytest.mark.parametrize("text", [
    "歧义分数一节里提到 3 个风险",
    "歧义分数部分列出了 5 条待澄清项",
])
def test_prose_mentioning_score_is_not_mistaken_for_one(task, text):
    """放宽不得放到把叙述句当成分数 —— 那会让判据读到假数字。"""
    name = task(f"amb-prose-{abs(hash(text)) % 10000}", _SUBSTANCE + f"\n{text}\n")
    assert oc.read_ambiguity_score(name, _STAGE) is None, \
        f"{text!r} 被误读成分数"
