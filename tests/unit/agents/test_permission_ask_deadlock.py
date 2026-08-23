"""ask 死锁：非工具类权限缺底规则 → opencode 默认 ask → harness 死等。

真实事故（任务 `newtask`，2026-08-23 18:18:35）：
agent 执行 `glob {'pattern': '**/hooks/*.md', 'path': '<harness 根>'}`
越出 workdir，opencode 服务端日志：

```text
evaluated permission=external_directory pattern=/Users/.../harness-flow/* action.action=ask
asking id=per_02e211792001EsemGvLhCVHHtF permission=external_directory
```

此后再无任何日志，UI 停在「Agent 正在处理中」，直到 CHAT_TIMEOUT(1800s)
服务端自行退出。26 分钟里没有任何错误、没有任何提示。

`opencode.py` 的纪律第 1 条已写明「每种权限都要显式给底规则」，
但 `_MANAGED_TOOLS` 只列了 9 个**工具**权限，
`external_directory` 是**路径域**权限，不在其中 —— 纪律写对了，覆盖面漏了。
"""

import inspect

import pytest

from sw_lib.agents.opencode import OpenCodeAgent


def _rules(stage: str = "02-planning"):
    agent = object.__new__(OpenCodeAgent)
    agent.stage = stage
    return agent._permission_rules()


STAGES = ("01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive")


# ── 修 1：非工具类权限必须有显式底规则 ──

def test_external_directory_has_explicit_base_rule():
    """`external_directory` 必须有显式底规则。

    这是 newtask 卡死 26 分钟的直接原因。opencode 无匹配规则时默认 ask，
    而 ask 对 harness 等于死锁。
    """
    for stage in STAGES:
        perms = {r["permission"] for r in _rules(stage)}
        assert "external_directory" in perms, (
            f"{stage} 缺少 external_directory 底规则 —— "
            f"opencode 无匹配时默认 ask，harness 会死等到 CHAT_TIMEOUT 超时")


def test_every_known_ask_capable_permission_is_covered():
    """opencode 会 ask 的权限**全集**都要有底规则。

    只补 external_directory 是治这一次的症：任何一种未覆盖的权限
    都会以完全相同的方式再卡一次，且同样没有任何报错。
    """
    from sw_lib.agents.opencode import _ASK_CAPABLE_PERMISSIONS

    assert "external_directory" in _ASK_CAPABLE_PERMISSIONS, (
        "全集里应包含真实事故中出现的那个权限")
    for stage in STAGES:
        perms = {r["permission"] for r in _rules(stage)}
        missing = sorted(set(_ASK_CAPABLE_PERMISSIONS) - perms)
        assert not missing, f"{stage} 未覆盖的会 ask 的权限: {missing}"


def test_still_no_ask_action_after_adding_new_permissions():
    """新增底规则本身不得引入 ask（沿用既有纪律 1，防止修复引入回归）。"""
    for stage in STAGES:
        actions = {r["action"] for r in _rules(stage)}
        assert actions <= {"allow", "deny"}, f"{stage} 出现非 allow/deny: {actions}"


def test_external_directory_allows_harness_root_for_reading():
    """底规则的取值：允许而非拒绝。

    权衡记录在案 —— agent 越界读 harness 目录的**需求**（找阶段规范）
    已由修 3 的 prompt 注入满足，但 deny 会让越界读直接失败并可能触发
    agent 反复试探；allow 只是「不阻断读」，判据保护仍由
    write/edit 的 deny + evidence HMAC 兑现（与 bash 同理）。
    """
    for stage in STAGES:
        rules = [r for r in _rules(stage) if r["permission"] == "external_directory"]
        assert rules, f"{stage} 无 external_directory 规则"
        assert rules[-1]["action"] == "allow", (
            f"{stage} 的 external_directory 兜底应为 allow，实际 {rules[-1]}")


# ── 修 2：ask 必须可观测，不得伪装成「处理中」 ──

def test_transport_subscribes_permission_asked():
    """transport 的事件泵必须处理 `permission.asked`。

    26 分钟静默的根本原因是这个事件没人接。底规则是第一道防线，
    但只要 opencode 将来新增一种权限，防线就会被绕过 ——
    可观测性是**兜底**，两者都要。

    【测试缺陷更正 · 显式声明】本条与下一条最初写的是 `start_event_stream`，
    而真实方法名是 `start_events`，因此首轮的红是 AttributeError 形态 ——
    按 DEV-PROTOCOL 1.3 不算有效 Red。已更正为真实方法名。
    更正后的断言在实现前确实为红，证据：
    `git show HEAD:sw_lib/agents/transport.py | grep -c permission.asked` → 0，
    `... | grep -c on_permission` → 0。
    """
    from sw_lib.agents import transport as tp

    src = inspect.getsource(tp.OpenCodeTransport.start_events)
    assert "permission.asked" in src, (
        "事件泵未订阅 permission.asked —— ask 发生时 harness 完全无感知")


def test_permission_asked_has_callback_hook():
    """必须有 `on_permission` 回调参数，而不是只在内部打个日志。"""
    sig = inspect.signature(
        __import__("sw_lib.agents.transport", fromlist=["x"])
        .OpenCodeTransport.start_events)
    assert "on_permission" in sig.parameters, (
        f"start_events 缺少 on_permission 参数: {list(sig.parameters)}")


def test_agent_reports_permission_wait_to_user():
    """OpenCodeAgent 必须把「正在等待权限审批」告知用户。

    对应用户确认的可观测性缺陷：一个「等待外部审批」的状态
    必须显式呈现，而不是伪装成「正在工作」。
    """
    src = inspect.getsource(OpenCodeAgent)
    assert "_on_permission" in src, "OpenCodeAgent 未处理权限审批事件"
    # 必须有面向用户的提示文案，而不是静默处理
    assert "权限" in src, "缺少面向用户的权限等待提示"


def test_permission_wait_message_is_actionable():
    """提示必须说清「在等什么」，否则用户仍然只能猜。

    反例就是这次的 UI：「Agent 正在处理中」—— 技术上没说错，
    但把「等待审批」和「正在计算」混成了同一个状态。
    """
    import re

    src = inspect.getsource(OpenCodeAgent)
    m = re.search(r"_on_permission.*?(?=\n    def |\n\n    #)", src, re.S)
    assert m, "未找到 _on_permission 实现"
    body = m.group(0)
    # 应把权限名/模式带给用户，而不是只说「等待中」
    assert "permission" in body or "perm" in body, (
        "权限等待提示应包含具体权限名，否则用户无从判断")
