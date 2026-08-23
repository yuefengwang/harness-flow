"""A0 第三层 D0-6：opencode 权限规则下发。

对应 docs/design/A0-state-integrity.md 的 D0-6。

**本文件测的是「规则形状正确」，不是「deny 真的阻止了写入」。**
依据 2.7.2 第 3 条：真实判定发生在工具执行路径，需真实 LLM 会话触发 write
才会走到；assert 端点实测一律返回 allow，不能用作判据。因此本层的有效性
状态是 ❓ 未验证，兑现「篡改可检出」的是 D0-5 的 HMAC 校验。

被测的四条纪律（2.7.3 / D0-6）：
1. 只用 allow / deny，绝不用 ask —— harness 不订阅 permission.asked，
   一旦产生 ask 会死等到 CHAT_TIMEOUT（1800 秒）。
2. deny 只覆盖 write / edit —— bash 无法按路径约束，不假装堵住了。
3. 规则在 POST /session 创建时一次性带入，禁止 PATCH（PATCH 是 merge，
   累积 + findLast 会让 deny 被后续 allow 覆盖）。
4. deny 必须排在 allow 之后 —— findLast 语义下后者优先。
"""

import pytest

from sw_lib.agents.opencode import OpenCodeAgent


def _rules(stage: str = "03-coding"):
    agent = object.__new__(OpenCodeAgent)
    agent.stage = stage
    return agent._permission_rules()


# ── 纪律 1：不得出现 ask ──

def test_no_ask_action_anywhere():
    """ask 会让 harness 死等 1800 秒 —— 比不设防更糟。"""
    for stage in ("01-brainstorming", "03-coding", "04-review", "05-archive"):
        rules = _rules(stage)
        assert rules, f"{stage} 未产生任何规则 —— 空规则集下 opencode 默认 ask"
        actions = {r["action"] for r in rules}
        assert "ask" not in actions, f"{stage} 产生了 ask 规则，会死等到超时"
        assert actions <= {"allow", "deny"}
        for r in rules:
            assert set(r) == {"permission", "pattern", "action"}, (
                f"规则字段与 PermissionV2.Rule 形态不一致: {r}")


# ── 纪律 4：deny 必须在 allow 之后（findLast 后者优先）──

def test_deny_rules_come_after_allow_for_same_permission():
    rules = _rules("03-coding")
    for perm in ("write", "edit"):
        idx = [i for i, r in enumerate(rules) if r["permission"] == perm]
        assert idx, f"03-coding 缺少 {perm} 规则"
        actions = [rules[i]["action"] for i in idx]
        assert "deny" in actions, f"{perm} 没有任何 deny 规则"
        last_allow = max((i for i in idx if rules[i]["action"] == "allow"),
                         default=-1)
        first_deny = min(i for i in idx if rules[i]["action"] == "deny")
        assert first_deny > last_allow, (
            f"{perm} 的 deny 排在 allow 之前 —— findLast 语义下会被覆盖")


# ── 纪律 2：deny 只覆盖 write / edit ──

def test_deny_only_targets_write_and_edit():
    """bash 无法按路径约束。给 bash 写 deny 规则是自欺。"""
    denied = {r["permission"] for r in _rules("03-coding")
              if r["action"] == "deny"}
    assert denied, "没有任何 deny 规则 —— 判据区未受保护"
    assert denied <= {"write", "edit"}, f"deny 覆盖了不可按路径约束的权限: {denied}"


def test_state_paths_are_denied_with_redundant_patterns():
    """pattern glob 语义未验证（2.7.2 第 2 条），因此冗余覆盖多种写法。"""
    patterns = {r["pattern"] for r in _rules("03-coding")
                if r["action"] == "deny"}
    joined = " ".join(patterns)
    assert ".state" in joined
    assert any("workspace" in p for p in patterns)
    assert any(p.startswith("**/") for p in patterns), (
        "缺少 **/ 前缀写法 —— glob 语义未验证时必须冗余覆盖")


# ── 阶段权限仍然生效（不得因加固而放宽）──

def test_readonly_stage_does_not_allow_write():
    """04-review 是只读阶段，不应出现 write allow。"""
    rules = _rules("04-review")
    assert rules, "04-review 未产生任何规则"
    allow_write = [r for r in rules
                   if r["permission"] == "write" and r["action"] == "allow"]
    assert not allow_write, f"只读阶段放开了 write: {allow_write}"
    assert any(r["permission"] == "read" and r["action"] == "allow"
               for r in rules), "只读阶段连 read 都没放开，agent 无法工作"


def test_switches_and_rules_agree_on_enabled_tools():
    """规则表与 _tool_switches 必须同源，否则两套判断会漂移。"""
    agent = object.__new__(OpenCodeAgent)
    agent.stage = "03-coding"
    switches = agent._tool_switches()
    allowed = {r["permission"] for r in agent._permission_rules()
               if r["action"] == "allow"}
    assert allowed, "规则表没有任何 allow —— 与开关必然漂移"

    for perm, on in switches.items():
        if perm in allowed:
            assert on, f"{perm} 有 allow 规则但开关是关的"


# ── 纪律 3：规则随 POST /session 带入，不用 PATCH ──

def test_transport_sends_rules_on_session_create():
    """PATCH 是 merge，累积会让 deny 被后续 allow 覆盖（2.7.2 第 1 条实测）。"""
    from unittest.mock import MagicMock, patch as mpatch
    from sw_lib.agents.transport import OpenCodeTransport

    tr = OpenCodeTransport(directory="/tmp")
    tr._server_url = "http://127.0.0.1:1"
    rules = [{"permission": "write", "pattern": "*", "action": "allow"}]
    tr.set_permission_rules(rules)

    with mpatch("sw_lib.agents.transport.requests.post") as post:
        post.return_value = MagicMock(
            status_code=200, json=lambda: {"id": "s1"},
            raise_for_status=lambda: None)
        tr.ensure_session()

        _, kwargs = post.call_args
        body = kwargs.get("json") or {}
        assert body.get("permission") == rules, (
            f"规则未随 POST /session 带入，实际 body: {body}")


def test_transport_never_patches_session_permission():
    """整个 transport 不得存在 PATCH /session 的权限下发路径。"""
    import inspect
    from sw_lib.agents import transport

    src = inspect.getsource(transport)
    assert "requests.patch" not in src, (
        "出现了 PATCH 调用 —— merge 语义会让规则累积，deny 被后续 allow 覆盖")


# ── 实测发现的致命缺陷：消息级 tools 会整体覆盖 session 权限 ──

def test_send_message_does_not_carry_tools_when_rules_are_set():
    """**这条源自一次真实实测，它证明 D0-6 原实现完全失效。**

    opencode 二进制 `SessionPrompt.prompt` 中：

        for (let [Q, C] of Object.entries(t.tools ?? {}))
            R.push({permission: Q, action: C ? "allow" : "deny", pattern: "*"});
        if (R.length > 0) O.permission = R, yield* o.setPermission({...});

    `O.permission = R` 是**整体赋值而非追加**。实测（真实 serve 1.18.20）：

        创建 session 携带 31 条规则  → GET 回读 31 条
        发一条带 tools 的消息        → GET 回读 **9 条**
        其中路径级 deny 剩余         → **0 条**

    即：首条消息就把全部路径 deny 抹掉，只留 `pattern:"*"` 的粗粒度规则。
    这与 2.7.2 第 1 条（PATCH 累积）是**两个不同的坑**，方向都是让 deny 失效。

    处置：规则已在 `POST /session` 一次性带入，消息里不再重复发 `tools`。
    `tools` 在 opencode 侧**只**被翻译成权限规则（无其他作用），因此不发不丢功能。
    """
    from unittest.mock import MagicMock, patch as mpatch
    from sw_lib.agents.transport import OpenCodeTransport

    tr = OpenCodeTransport(directory="/tmp")
    tr._server_url = "http://127.0.0.1:1"
    tr._session_id = "s1"
    tr.set_tools({"write": True, "bash": True})
    tr.set_permission_rules(
        [{"permission": "write", "pattern": "**/.state", "action": "deny"}])

    with mpatch("sw_lib.agents.transport.requests.post") as post:
        post.return_value = MagicMock(
            status_code=200, json=lambda: {"info": {}, "parts": []},
            raise_for_status=lambda: None)
        tr.send_message("hi")

        body = post.call_args.kwargs["json"]
        assert "tools" not in body, (
            "消息仍携带 tools —— 服务端会用它整体覆盖 session 权限，"
            f"把路径 deny 全部抹掉。实际 body 键: {sorted(body)}")


def test_write_and_edit_both_denied_because_write_tool_checks_edit():
    """**write 与 edit 必须同时 deny —— 这不是冗余，是必要条件。**

    真实模型实测（各 3 次，见
    tests/integration/test_opencode_permission_live.py）：

        只 deny write → write 工具 status=completed，**写入成功**
        只 deny edit  → write 工具 status=error，被拦 2/2

    服务端错误文本里列出的相关规则也是 `edit` 的。即 opencode 的 `write`
    工具查的是 **edit** 权限。谁要是觉得"两条重复"删掉 edit，防护立刻归零。
    """
    rules = _rules("03-coding")
    for pattern in (".state", "**/.state"):
        for perm in ("write", "edit"):
            assert any(r["permission"] == perm and r["pattern"] == pattern
                       and r["action"] == "deny" for r in rules), (
                f"缺少 {perm}:{pattern} 的 deny —— write 工具查 edit 权限，"
                f"少任何一条都可能让写入放行")
