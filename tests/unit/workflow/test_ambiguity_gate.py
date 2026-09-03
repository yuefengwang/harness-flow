"""`hook-01-01` 的歧义分数：从装饰数字变成真判据。

## 现状

`hooks/01-brainstorming.md` 写着：

    hook-01-01: Ambiguity Score
    - Rule: Score < 8 → block Planning entry. **不带假设进入下一阶段**。

**没有任何地方读这个分数。** 全仓库检索 `ambiguity`：只有
`output/stages.py` 里一个 `BrainstormingOutput.ambiguity_score` 字段定义，
零消费方。于是「Score < 8 → block」在真实流程中不存在 ——
这是「判据存在、无人调用」的第七例（前六见 A0 的 2.9.8 与 A2 的 10.6）。

## 为什么它才是正确的收敛条件

任务 `ppppp` 问了 14 轮（`hook-01-02` 只写「≥3 questions」，无上界）。
加轮次上限会把「消除歧义」变成「凑够数」—— 复杂需求本该多问，
一刀切的上限恰好在最需要澄清的任务上最先失效（用户已否决该方向）。

而歧义分数本来就是**语义**收敛条件：问到分数达标就该停。
`hook-01-01` 早就这么写了，只是从未接线。接上之后：

* agent 有了可执行的收敛判据 —— 不再是「我觉得信息够了吗」这种没有
  终止条件的自问，而是「分数到 8 了吗」；
* 门禁有了实质判据 —— 而不是只验「产出区有 80 字符」。

判据落在**产出区**（围栏内），与 `check_output` 同源：模板区不算数
（那是 agent 可写的区域，A0 的 2.9.7 已判过「拿可写区当判据」的错）。

## 三条边界

1. **分数不可得时不放行、也不编造** —— 记 ❓ 并说明为什么（DEV-PROTOCOL
   第 2 节）。写 `except: return 0` 会让「解析失败」变成「歧义极高」，
   反过来 `return 10` 更糟：那是把未验证说成已达标。
2. **阈值不得为了让存量变绿而放宽**（A6 的 9.3 同款纪律）。
3. **拒绝必须给下一步** —— 只说「分数不够」会把人逼向瞎改文案
   （A2 的 10.6 第六行判例）。
"""

import shutil

import pytest

from sw_lib.core.config import TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.output_check import (AMBIGUITY_THRESHOLD, check_output,
                                          read_ambiguity_score)

_STAGE = "01-brainstorming"

#: 一段够长的正文，避免撞上「实质内容不足 80 字符」那条判据 ——
#: 本文件测的是分数，不是长度。
_FILLER = "已就使用场景、技术架构与部署环境与用户确认，结论如上所述。" * 6


@pytest.fixture
def task():
    created = []

    def _make(output, name="pytest-ambiguity"):
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)
        write_state(name, {"id": name, "stage": _STAGE, "stage_idx": 0,
                           "stage_status": "running"})
        tpl = (TPLS / f"{_STAGE}.md").read_text(encoding="utf-8")
        nonce = ss.issue_output_nonce(name, _STAGE)
        (d / f"{_STAGE}.md").write_text(
            tpl + "\n" + ss.render_output_block(nonce, output), encoding="utf-8")
        return name

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


# ── 解析：认得 agent 实际会写的几种形态 ──

@pytest.mark.parametrize("text,expected", [
    ("歧义分数：9", 9),
    ("歧义分数: 8/10", 8),
    ("Ambiguity Score: 7", 7),
    ("Score: 10 | Goal: 做个应用", 10),
    ("**歧义分数**：6 分", 6),
    ("- 歧义分数 (0-10): 5", 5),
])
def test_parses_common_forms(task, text, expected):
    """分数解析必须覆盖 agent 实际会用的写法。

    只认一种格式等于没接线：模型不会照抄我们脑子里的格式，
    认不出就一律走「不可得」，那条路径会把所有任务都拦住。
    """
    name = task(f"{text}\n\n{_FILLER}")

    assert read_ambiguity_score(name, _STAGE) == expected


def test_returns_none_when_absent(task):
    """没写分数 → None，**不是** 0。

    0 意味着「歧义极高」，那是一个具体结论；拿不到数字是另一回事。
    混同两者就是「不猜数字」这条纪律的反面（A6 的第 3 条）。
    """
    name = task(f"这里没有任何分数。\n\n{_FILLER}")

    assert read_ambiguity_score(name, _STAGE) is None


def test_ignores_out_of_range_numbers(task):
    """超出 0-10 的数字不算分数 —— 那是 agent 写错了，不该被当真。"""
    name = task(f"歧义分数：42\n\n{_FILLER}")

    assert read_ambiguity_score(name, _STAGE) is None


def test_reads_from_output_region_not_template(task):
    """分数必须从**产出区**读，不能从模板区读。

    模板区现在是 agent 可写的（本轮放行），拿它当判据等于让 agent 自己
    给自己打分 —— A0 的 2.9.7 已判过同一个错（`check_02` 拿模板自带标题
    当判据，恒真）。
    """
    name = task(f"这里没有分数。\n\n{_FILLER}")
    path = TASKS / name / f"{_STAGE}.md"
    # 只在模板区写一个达标分数，产出区仍然没有
    content = path.read_text(encoding="utf-8")
    content = content.replace("Score: [0-10] | Goal: ___",
                              "Score: 10 | Goal: 自己给自己打满分")
    path.write_text(content, encoding="utf-8")

    assert read_ambiguity_score(name, _STAGE) is None, \
        "分数被从模板区读出来了 —— agent 可以自己给自己打分"


# ── 门禁：分数真的参与准出判定 ──

# ⚠️ **显式改判（DEV-PROTOCOL 1.2）**，不是静默改断言。
#
# 本节原有两条测试断言「低分硬拦」「缺分硬拦」。A13 推翻了这个契约：
# 歧义分数是 agent **自评**，硬规则才决定准出，自评只作参考。
# 推翻的依据是三个真实任务（maybework / 7090 / ppppp）收到同一句
# 「❌ 未给出歧义分数」，而三者的真实根因各不相同 —— 判据把三种病
# 报成同一种，指错了方向。判例见 A0 的 2.9.17。
#
# 改判的是**结论方向**（拦 → 放行 + 记账），不是判据的存在性：
# 下面两条仍然断言这一项**不得被伪造成 ✅**，留痕必须落到 `.state`。
# 若哪天有人把记账也去掉，这两条会红。

def test_score_below_threshold_passes_but_is_recorded(task):
    """低于阈值**不再硬拦**，但必须记账为 below_threshold 且给出下一步。

    （原 `test_score_below_threshold_blocks`，A13 改判。）
    """
    name = task(f"歧义分数：4\n\n{_FILLER}")

    verdict = check_output(name, _STAGE)

    assert verdict.ok, \
        "自评仍在硬拦 —— 被判者写不写决定过不过闸:\n" + "\n".join(verdict.lines)
    joined = "\n".join(verdict.lines)
    assert "歧义" in joined, joined
    assert "❓" in joined, f"低分被伪造成通过，没有 ❓ 标记:\n{joined}"

    record = ss.read_ambiguity_record(name, _STAGE)
    assert record.get("status") == "below_threshold", record
    assert record.get("score") == 4, record


def test_score_at_threshold_passes(task):
    """恰好达到阈值放行 —— 边界是「< 阈值才拦」，不是「≤」。"""
    name = task(f"歧义分数：{AMBIGUITY_THRESHOLD}\n\n{_FILLER}")

    assert check_output(name, _STAGE).ok


def test_rejection_tells_the_agent_what_to_do(task):
    """拒绝必须给下一步 —— 否则人只能瞎改文案去凑分数。

    A2 的 10.6 第六行判例：拦住一条路而不给替代路径，等于把人推向绕过机制。
    这里正确的下一步是「继续用 question 澄清」，不是「把数字改大」。
    """
    name = task(f"歧义分数：3\n\n{_FILLER}")

    joined = "\n".join(check_output(name, _STAGE).lines)

    assert "question" in joined or "澄清" in joined, \
        f"拒绝没告诉 agent 该怎么办:\n{joined}"


def test_missing_score_is_unavailable_not_pass(task):
    """分数不可得 → **放行但记 `unavailable`**，绝不伪造成 ✅。

    （A13 改判：原断言是「拒绝」。）改的是拦不拦，没改三态纪律 ——
    「放行」与「达标」必须在 `.state` 和输出里都能分辨，
    否则就是把「没测到」洗成「测过了」（DEV-PROTOCOL 第 2 节）。
    """
    name = task(f"我完成了需求分析，结论如上。\n\n{_FILLER}")

    verdict = check_output(name, _STAGE)

    assert verdict.ok, "缺分仍在硬拦 —— 不写分数不该卡死用户"
    joined = "\n".join(verdict.lines)
    assert "歧义分数" in joined, joined
    assert "❓" in joined, f"缺分被伪造成通过:\n{joined}"

    record = ss.read_ambiguity_record(name, _STAGE)
    assert record.get("status") == "unavailable", record


def test_threshold_matches_documented_hook():
    """阈值必须与 `hooks/01-brainstorming.md` 写的一致。

    文档说 8、代码用 5 是最坏的情形：两边各自都「有」判据，
    合起来没人知道真实标准是什么。这类漂移正是本轮要根治的形状。
    """
    from pathlib import Path

    text = Path("hooks/01-brainstorming.md").read_text(encoding="utf-8")
    assert f"< {AMBIGUITY_THRESHOLD}" in text, \
        f"hook 文档与代码阈值 {AMBIGUITY_THRESHOLD} 不一致:\n{text[:400]}"


# ── 不得影响其它阶段 ──

def test_other_stages_are_not_subject_to_ambiguity_gate(task):
    """歧义分数只管 01 —— 02/03 没有这个概念，不能被顺手拦住。"""
    name = task(f"这里没有分数。\n\n{_FILLER}", name="pytest-ambiguity-02")
    d = TASKS / name
    tpl = (TPLS / "02-planning.md").read_text(encoding="utf-8")
    nonce = ss.issue_output_nonce(name, "02-planning")
    (d / "02-planning.md").write_text(
        tpl + "\n" + ss.render_output_block(nonce, _FILLER), encoding="utf-8")

    assert check_output(name, "02-planning").ok, \
        "02 阶段被歧义分数判据拦住了"
