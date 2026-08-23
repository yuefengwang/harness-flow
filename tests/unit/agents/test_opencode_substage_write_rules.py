"""A2 的 3.3 硬层：按 03a / 03b 子阶段下发 write / edit deny 规则。

设计要求「03a 拒绝写非测试文件，03b 拒绝写测试文件」，判定依据是路径
（`test_*.py` / `*_test.py` / `tests/` 下）。落点是
`opencode.py` 的 `_permission_rules()`（A0 的 2.5 已否掉 `WriteFileTool`：
`Toolbox` 只被 `agents/gemini.py` 引用，改它对实际运行的 agent 无效）。

**本文件测的是「规则形状正确」，不是「deny 真的阻止了写入」。**
承自 `test_opencode_permission_rules.py` 的定性：依据 A0 的 2.7.2，
真实判定发生在工具执行路径，assert 端点一律返回 allow，不能用作判据。
所以硬层的有效性是 ❓ 未验证 —— 兑现「改测试可检出」的是 A2 的哈希冻结
（3.3 末尾明确：哈希校验是**长期主防线**，不是 A0 未就绪时的临时措施）。

那为什么还要做这一层：它把「不该写」变成在**写的那一刻**就被拒，
而哈希校验要等到准出。早一步反馈能省掉一整轮返工。
"""

import pytest

from sw_lib.agents.opencode import OpenCodeAgent
from sw_lib.workflow import red_witness as rw


def _rules(stage="03-coding", phase=None):
    """构造规则表。`phase` 为 None 表示未进入见证流程。"""
    agent = object.__new__(OpenCodeAgent)
    agent.stage = stage
    agent.name = "rw-perm-probe"
    agent._witness_phase = phase
    return agent._permission_rules()


def _denied(rules, perm="write"):
    return [r["pattern"] for r in rules
            if r["permission"] == perm and r["action"] == "deny"]


# ── 纪律沿用：任何子阶段都不得产生 ask ──

@pytest.mark.parametrize("phase", [None, rw.PHASE_TEST, rw.PHASE_IMPL])
def test_no_ask_in_any_substage(phase):
    """ask 会让 harness 死等到 CHAT_TIMEOUT —— 子阶段规则不得破这条纪律。"""
    rules = _rules(phase=phase)
    actions = {r["action"] for r in rules}
    assert "ask" not in actions, f"phase={phase} 产生了 ask 规则"
    assert actions <= {"allow", "deny"}
    for r in rules:
        assert set(r) == {"permission", "pattern", "action"}, r


# ── 03a：只写测试 ──

def test_03a_denies_writing_implementation_files():
    """03a 的语义是「只写测试」，实现文件必须被拒。"""
    denied = _denied(_rules(phase=rw.PHASE_TEST))
    assert any("*.py" in p for p in denied), \
        f"03a 未拒绝写实现文件:\n{denied}"


def test_03a_still_allows_writing_test_files():
    """但测试文件必须写得进去 —— 否则 03a 无事可做。

    `findLast` 后者优先：测试路径的 allow 必须排在实现路径的 deny **之后**，
    否则 deny 会把测试文件一起挡掉，03a 直接死锁。
    """
    rules = _rules(phase=rw.PHASE_TEST)
    idx = [i for i, r in enumerate(rules) if r["permission"] == "write"]
    test_allows = [i for i in idx
                   if rules[i]["action"] == "allow"
                   and ("test" in rules[i]["pattern"]
                        or "tests" in rules[i]["pattern"])]
    assert test_allows, f"03a 没有任何允许写测试文件的规则:\n{rules}"

    broad_denies = [i for i in idx
                    if rules[i]["action"] == "deny"
                    and "test" not in rules[i]["pattern"]]
    assert broad_denies, "03a 缺少对非测试文件的 deny"
    assert max(test_allows) > max(broad_denies), \
        "测试路径的 allow 排在 deny 之前 —— findLast 下会被覆盖，03a 会死锁"


# ── 03b：禁改测试 ──

def test_03b_denies_writing_test_files():
    """03b 的语义是「禁止改测试」（A2 的 3.1）。"""
    denied = _denied(_rules(phase=rw.PHASE_IMPL))
    assert any("test" in p for p in denied), \
        f"03b 未拒绝写测试文件:\n{denied}"


def _final_action(rules, path, perm="write"):
    r"""按 opencode 的真实语义求值：把 pattern 编译成正则后取最后一条匹配。

    服务端实测行为（A0 的 2.7.2 反编译确认）：
    `replace(/\*/g, ".*")` + `new RegExp("^" + l + "$", "s")`，
    `s` 标志让 `*` **跨斜杠**。因此这里必须按同样的规则求值 ——
    照字面猜 pattern 会把 `**/test_*.py` 误判成「覆盖实现文件」，
    那是在测我对 glob 的想象，而不是 opencode 的行为。
    """
    import re

    action = None
    for r in rules:
        if r["permission"] != perm:
            continue
        regex = "^" + r["pattern"].replace("*", ".*") + "$"
        if re.match(regex, path, re.S):
            action = r["action"]      # findLast：后者优先
    return action


def test_03b_allows_writing_implementation_files():
    """03b 要写实现，实现文件不能被拒 —— 否则实现阶段无事可做。

    ⚠️ 本条是对初版的**显式重做**（DEV-PROTOCOL 1.2）。初版用
    「pattern 以 `*.py` 结尾」来挑出「实现文件规则」，结果把
    `**/test_*.py` 也算进去了，于是断言的是一条不存在的契约。
    现在改为按真实路径求值，判据是「`impl.py` 的最终判定是 allow」。
    """
    rules = _rules(phase=rw.PHASE_IMPL)

    for impl_path in ("impl.py", "src/mypkg/core.py", "app.py"):
        assert _final_action(rules, impl_path) != "deny", \
            f"03b 把实现文件 {impl_path} 拒了 —— 实现阶段无法写代码"

    for test_path in ("test_y.py", "tests/test_it.py", "src/pkg/thing_test.py"):
        assert _final_action(rules, test_path) == "deny", \
            f"03b 未拒绝测试文件 {test_path}"


def test_03a_write_rules_resolve_correctly_by_path():
    """03a 的对照：按真实路径求值，测试文件可写、实现文件被拒。

    这条锁的是 `findLast` 的顺序。初版实现若把 allow 写在 deny 之前，
    测试文件会被一起挡掉 —— 03a 直接死锁，而按 pattern 字面检查发现不了。
    """
    rules = _rules(phase=rw.PHASE_TEST)

    for test_path in ("test_y.py", "tests/test_it.py", "src/pkg/thing_test.py"):
        assert _final_action(rules, test_path) == "allow", \
            f"03a 不允许写测试文件 {test_path} —— 该阶段无事可做"

    for impl_path in ("impl.py", "src/mypkg/core.py"):
        assert _final_action(rules, impl_path) == "deny", \
            f"03a 允许写实现文件 {impl_path} —— 「只写测试」没有被约束"


def test_evidence_paths_stay_denied_under_path_resolution():
    """判据区在按路径求值下仍是 deny —— 子阶段规则不得把它盖掉。

    `findLast` 后者优先，而子阶段规则追加在最后。03a 那条
    `allow tests/**` 若写得太宽，`workspace/tasks/x/.state` 这类路径
    有可能被重新放开，A0 的判据保护就被自己的下游规则掏空了。
    """
    for phase in (None, rw.PHASE_TEST, rw.PHASE_IMPL):
        rules = _rules(phase=phase)
        for perm in ("write", "edit"):
            for guarded_path in (".state",
                                 "workspace/tasks/demo/.state",
                                 "workspace/STATUS.json",
                                 "config/.evidence_key"):
                assert _final_action(rules, guarded_path, perm) == "deny", \
                    f"phase={phase} 下 {perm} {guarded_path} 不是 deny"


def test_03b_test_deny_covers_both_write_and_edit():
    """`edit` 与 `write` 都要覆盖：只堵 write 时 edit 仍能改测试。"""
    for perm in ("write", "edit"):
        denied = _denied(_rules(phase=rw.PHASE_IMPL), perm)
        assert any("test" in p for p in denied), \
            f"03b 的 {perm} 未覆盖测试路径:\n{denied}"


# ── 未进入见证流程：不得改变既有行为 ──

def test_no_substage_rules_when_not_in_witness_flow():
    """`phase == none` 时规则表必须与引入子阶段之前一致。

    存量任务、返工轮次、开关关闭都落在这一态。这里多下发一条 deny，
    就会把「见证机制只是不观测」变成「见证机制悄悄改了 agent 的写权限」。
    """
    baseline = _rules(phase=None)
    denied = _denied(baseline)
    assert not any("test" in p for p in denied), \
        f"未进入见证流程却下发了测试路径 deny:\n{denied}"
    # 判据区的既有 deny（.state / workspace 等）必须还在
    assert any(".state" in p for p in denied), \
        f"判据区的既有 deny 被子阶段改动弄丢了:\n{denied}"


def test_other_stages_are_unaffected():
    """03 之外的阶段不受影响 —— 子阶段是 03-coding 内部的概念。"""
    for stage in ("01-brainstorming", "02-planning", "04-review", "05-archive"):
        denied = _denied(_rules(stage=stage, phase=rw.PHASE_IMPL))
        assert not any("test" in p for p in denied), \
            f"{stage} 被下发了 03b 的测试 deny:\n{denied}"


def test_guarded_evidence_paths_survive_in_every_substage():
    """判据区（`.state` / `workspace/**` / 密钥）在任何子阶段都必须禁写。

    子阶段规则是追加在后面的，而 `findLast` 是后者优先 ——
    一条写宽了的 allow 就能把 A0 的判据保护整个盖掉。
    """
    for phase in (None, rw.PHASE_TEST, rw.PHASE_IMPL):
        rules = _rules(phase=phase)
        for perm in ("write", "edit"):
            idx = [i for i, r in enumerate(rules) if r["permission"] == perm]
            for target in (".state", "workspace/**", "**/.evidence_key"):
                matched = [i for i in idx if rules[i]["pattern"] == target]
                assert matched, f"phase={phase} 丢了 {perm} {target} 的规则"
                assert rules[max(matched)]["action"] == "deny", \
                    f"phase={phase} 的 {perm} {target} 最终判定不是 deny"
                # 后面不能有更宽的 allow 把它盖掉
                later_broad = [i for i in idx
                               if i > max(matched)
                               and rules[i]["action"] == "allow"
                               and rules[i]["pattern"] in ("*", "**")]
                assert not later_broad, (
                    f"phase={phase} 的 {perm} 判据 deny 之后又出现了通配 allow "
                    f"—— findLast 下判据保护被盖掉")
