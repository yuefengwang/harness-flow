"""阶段文件可写之后：围栏与 Gate 的篡改必须被检出。

放行 `workspace/tasks/<task>/<stage>.md` 的写权限（见
tests/unit/agents/test_stage_file_writable.py）解开了 agent 的收敛出口，
但同时把两样东西暴露给了它：

* **围栏区** —— `<!-- sw:ai-output:start <nonce> -->` 界定「哪段是 agent
  说的」。`check_01` 的实质内容判据落在围栏内，agent 若能自己写围栏，
  就能把「我写了一段话」伪造成「harness 记录了我的产出」；
* **Gate 区** —— 模板末尾那几个复选框。真相源在 `.state`（有 HMAC），
  文件只是映像，但**人是看文件的**。伪造的 `[x] Design approved`
  会让用户以为门禁已过。

因此这次放行必须配一条事后校验：模板正文随便改，这两处一动就检出。
思路与 A2 那次「不拦 bash、改事后检测」同源，但强度不同 ——
那次判据在 agent 能碰的地方，这次真相源在 `.state` 里，agent 动不了。

三条边界（缺一条放行就是个洞）：

1. **围栏 nonce 必须与 `.state` 记的一致** —— agent 猜不到 nonce，
   编一个就会被抓住；连围栏一起删掉同样算篡改。
2. **Gate 勾选必须与 `.state` 的签署状态一致** —— 文件里勾了而
   `.state` 没签署，是伪造。
3. **模板正文的改动一律放行** —— 那正是我们要它做的事。
   校验不得因为「文件被改过」就拒绝，否则等于把放行又收回去。
"""

import json
import shutil

import pytest

from sw_lib.core.config import TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.output_check import check_output

_STAGE = "01-brainstorming"


@pytest.fixture
def task():
    """一个带真实围栏产出区的 01 阶段任务。"""
    created = []

    #: 默认产出：分数取达标值（9）。本文件测的是**围栏与 Gate 的篡改**，
    #: 分数只是让产出过得了内容判据的填充料 —— 若取 7 分，所有用例都会
    #: 挂在歧义分数那条判据上，而那不是本文件的辖区
    #: （见 `test_ambiguity_gate.py`）。
    def _make(name="pytest-tamper", output="歧义分数：9。已澄清全部关键点。" * 12):
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)
        write_state(name, {"id": name, "stage": _STAGE, "stage_idx": 0,
                           "stage_status": "running"})
        # A13 第 1 步的完成性判据要求 01 至少有一轮问答。本文件测的是
        # 围栏与 Gate 的篡改，问答记录只是让产出过得了前置硬规则的填充料
        # —— 理由与上面那段「分数取 9」的注释相同。
        ss.record_decision(name, _STAGE, "需求范围？", "已确认")
        tpl = (TPLS / f"{_STAGE}.md").read_text(encoding="utf-8")
        nonce = ss.issue_output_nonce(name, _STAGE)
        path = d / f"{_STAGE}.md"
        path.write_text(tpl + "\n" + ss.render_output_block(nonce, output),
                        encoding="utf-8")
        return name, path, nonce

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


# ── 前提：正常情形必须放行 ──

def test_intact_file_passes(task):
    """未被篡改的文件照常过闸 —— 校验不是新增一道拦路。"""
    name, _, _ = task()

    verdict = check_output(name, _STAGE)

    assert verdict.ok, verdict.lines


def test_filled_template_still_passes(task):
    """**agent 回填模板正文之后仍须过闸** —— 那正是这次放行的目的。

    这条是整件事的意义所在：若「文件被改过」就拒绝，等于把刚放开的
    写权限又收回去，agent 依旧没有可执行的终止动作。
    """
    name, path, _ = task()
    content = path.read_text(encoding="utf-8")
    content = content.replace("Score: [0-10] | Goal: ___",
                              "Score: 8 | Goal: 交付企业出差申请与审批 MVP")
    content = content.replace("1. **Topic**: ___", "1. **Topic**: 使用场景")
    path.write_text(content, encoding="utf-8")

    verdict = check_output(name, _STAGE)

    assert verdict.ok, f"回填模板之后被拒绝了 —— 放行等于没放:\n{verdict.lines}"


# ── 边界 1：围栏 nonce 篡改 ──

def test_forged_nonce_is_detected(task):
    """agent 自己编一个 nonce 写围栏 → 必须检出。

    这是放行之后最直接的攻击：伪造围栏就等于伪造「harness 记录了我的产出」，
    而实质内容判据正是落在围栏内的。
    """
    name, path, nonce = task()
    content = path.read_text(encoding="utf-8").replace(nonce, "deadbeef1234")
    path.write_text(content, encoding="utf-8")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, "伪造的 nonce 没被检出"
    joined = "\n".join(verdict.lines)
    assert "nonce" in joined or "围栏" in joined, joined


def test_removed_fence_is_detected(task):
    """把围栏整段删掉同样算篡改，不能当成「本轮没产出」放过。

    两者的处置不同：真的没产出该提示重跑本阶段；围栏被删是 agent 动了
    不该动的地方，必须指出来 —— 否则删围栏就成了「让判据消失」的办法。
    """
    name, path, nonce = task()
    start, end = ss.fence_markers(nonce)
    content = path.read_text(encoding="utf-8").replace(start, "").replace(end, "")
    path.write_text(content, encoding="utf-8")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, "围栏被删除后仍然过闸"
    # 只断言 not ok 不够：实测它此前「不 ok」的理由是「空转」——
    # 而 `.state` 里明明记着 nonce，说明围栏曾经存在、是被删掉的。
    # 两者的下一步动作不同（重跑 vs 追究改了什么），必须分开说。
    joined = "\n".join(verdict.lines)
    assert "篡改" in joined or "围栏" in joined, \
        f"围栏被删却报成空转，用户会去重跑而不是追查:\n{joined}"


def test_extra_fence_with_wrong_nonce_is_detected(task):
    """在正确围栏之外**另加**一段假围栏 → 检出。

    `split_output_region` 取第一个 start，agent 若把假围栏写在文件更靠前的
    位置，就能让判据读它那一段。
    """
    name, path, _ = task()
    content = path.read_text(encoding="utf-8")
    fake = "<!-- sw:ai-output:start 0badc0de -->\n伪造内容\n<!-- sw:ai-output:end 0badc0de -->\n"
    path.write_text(fake + content, encoding="utf-8")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, "文件开头的假围栏没被检出"
    # 同上：实测此前的拒绝理由是「内容不足 80 字符」—— 那只是因为假围栏里
    # 恰好写得短。写满 100 字废话就能过闸，判据形同虚设。
    joined = "\n".join(verdict.lines)
    assert "nonce" in joined or "篡改" in joined or "围栏" in joined, \
        f"假围栏靠「内容太少」被挡住，写长一点就能过:\n{joined}"


# ── 边界 2：Gate 伪造 ──

def test_forged_gate_checkbox_is_detected(task):
    """文件里勾了 Gate 而 `.state` 未签署 → 必须检出。

    `.state` 才是真相源，判定不会被骗（`check_01` 读 `sw state get`）。
    但**人看的是文件** —— 一个伪造的 `[x] Design approved` 会让用户以为
    门禁已经过了。校验的目的是让「显示层的谎」也无处可藏。
    """
    name, path, _ = task()
    content = path.read_text(encoding="utf-8")
    content = content.replace("- [ ] Design approved", "- [x] Design approved")
    path.write_text(content, encoding="utf-8")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, "伪造的 Gate 勾选没被检出"
    joined = "\n".join(verdict.lines)
    assert "Gate" in joined, joined


def test_signed_gate_rendered_by_sw_is_not_flagged(task):
    """`.state` 里确实签署了 → 文件里的 `[x]` 是 sw 渲染的，不算伪造。

    否则正常签署流程会被自己的校验拦下 —— 那比不校验更糟。
    """
    name, path, _ = task()
    ss.sign_gate(name, _STAGE, by="tester")
    ss.render_gate_section(name, _STAGE)

    verdict = check_output(name, _STAGE)

    assert verdict.ok, f"合法签署被判成伪造:\n{verdict.lines}"


# ── 边界 3：校验本身不得越界 ──

def test_prose_edits_outside_gate_are_free(task):
    """模板区的任意散文改动都不该被判违规。

    校验只盯围栏与 Gate 两处。把「文件内容变了」当违规等于回到 deny。
    """
    name, path, _ = task()
    content = path.read_text(encoding="utf-8")
    content = content.replace("## Pre-mortem",
                              "## Pre-mortem\n\n这一段是 agent 自己加的说明文字。")
    path.write_text(content, encoding="utf-8")

    assert check_output(name, _STAGE).ok


def test_missing_output_region_still_reports_idle_not_tamper(task):
    """完全没有产出区（真的空转）仍报「空转」，不报「篡改」。

    两种情形的下一步动作不同：空转该重跑本阶段，篡改该追究 agent 改了什么。
    混成一句话会让用户不知道该做什么（任务 T2 的教训）。
    """
    name = "pytest-tamper-empty"
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    try:
        write_state(name, {"id": name, "stage": _STAGE, "stage_idx": 0,
                           "stage_status": "running"})
        (d / f"{_STAGE}.md").write_text(
            (TPLS / f"{_STAGE}.md").read_text(encoding="utf-8"), encoding="utf-8")

        verdict = check_output(name, _STAGE)

        assert not verdict.ok
        joined = "\n".join(verdict.lines)
        assert "空转" in joined, joined
        assert "篡改" not in joined, f"空转被误报成篡改:\n{joined}"
    finally:
        shutil.rmtree(d, ignore_errors=True)
