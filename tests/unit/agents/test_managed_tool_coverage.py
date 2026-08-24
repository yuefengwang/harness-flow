"""权限矩阵必须覆盖 agent 实际能调用的工具面。

`_MANAGED_TOOLS` 是「需要显式下发 allow/deny 规则的 opencode 工具全集」，
它决定了 `_tool_switches()` 会为哪些工具产生规则。未列出的工具**按 opencode
默认处理** —— 也就是 harness 完全不管。

任务 `helloworld` 的日志里出现了三个不在这份名单里的工具：

    🔧 todowrite {...}                 ← 出现 9 次
    🔧 skill {'name': 'using-superpowers'}
    🔧 invalid {'tool': 'bash', ...}

`todowrite` 与 `skill` 是 opencode 自带/由用户全局 `.agents/` 配置引入的。
`skill` 尤其值得注意：它能加载任意技能包，等于一个 harness 既不知道存在、
也无法约束的能力入口。

这不是「必须把它们关掉」—— 有些（如 `todowrite`）无害甚至有用。真正的问题是
**harness 对自己的约束面缺乏自知**：A4 的 1.3 已声明「只解析权限、不实施」，
A0 的 2.9.5 又实测 `bash` 可绕过路径 deny。在这个基础上，如果连「有哪些
工具存在」都不掌握，那「纯只读的 design_critic」这类声明就更没有依据。

判据分两层：
1. 已知会出现的工具必须被显式登记（要么托管、要么明确记为「有意不管」）；
2. 登记表必须与 `TOOL_MAP` 的值域自洽 —— 映射目标必须都在托管名单里，
   否则配了权限也不生效。
"""

import pytest

from sw_lib.agents.opencode import (TOOL_MAP, _MANAGED_TOOLS,
                                    _UNMANAGED_TOOLS, OpenCodeAgent)


def test_tool_map_targets_are_all_managed():
    """`TOOL_MAP` 的每个映射目标都必须在 `_MANAGED_TOOLS` 里。

    否则 `roles[].tools` 里配了它、`_tool_switches()` 却不会为它产生规则，
    权限配置静默失效 —— 「改了等于白改」。
    """
    targets = {t for names in TOOL_MAP.values() for t in names}
    missing = sorted(targets - set(_MANAGED_TOOLS))
    assert not missing, (
        f"TOOL_MAP 把工具映射到 {missing}，但它们不在 _MANAGED_TOOLS 里 —— "
        f"权限规则不会覆盖它们，配置静默失效。")


def test_switches_cover_every_managed_tool():
    """`_tool_switches()` 必须为每个托管工具给出显式开关。

    纪律 1：每种权限都要显式给底规则。漏一个就等于交给服务端默认值，
    而默认值可能是 `ask` —— 任务 newtask 因此卡死 26 分钟。
    """
    agent = _FakeAgent()
    switches = OpenCodeAgent._tool_switches(agent)
    for tool in _MANAGED_TOOLS:
        assert tool in switches, f"{tool} 没有显式开关"


#: helloworld 日志里真实出现过、且**不在** `_MANAGED_TOOLS` 里的工具。
#: 这份名单是现场证据，不是猜测。
_SEEN_IN_THE_WILD = ("todowrite", "skill", "invalid")


@pytest.mark.parametrize("tool", _SEEN_IN_THE_WILD)
def test_wild_tools_are_explicitly_accounted_for(tool):
    """实际出现过的工具必须被显式登记 —— 托管，或明确声明「有意不管」。

    判据不要求把它们关掉，只要求**harness 知道它们存在**。
    沉默地不知道，与知情地放行，是两种完全不同的状态：
    前者让「只读角色」这类声明失去依据。
    """
    accounted = set(_MANAGED_TOOLS) | set(_UNMANAGED_TOOLS)
    assert tool in accounted, (
        f"工具 `{tool}` 在真实运行里出现过，但既不在 _MANAGED_TOOLS "
        f"也不在 _UNMANAGED_TOOLS 里 —— harness 对它的存在毫无记录。\n"
        f"请显式登记：要托管就加进 _MANAGED_TOOLS，"
        f"有意不管就加进 _UNMANAGED_TOOLS 并写明理由。")


def test_unmanaged_list_documents_why():
    """`_UNMANAGED_TOOLS` 必须带解释，否则它退化成一张豁免清单。

    判据取「模块源码里该常量附近有中文说明」这种保守形状 ——
    锁住「有理由」而不锁具体措辞。
    """
    import inspect

    from sw_lib.agents import opencode

    src = inspect.getsource(opencode)
    idx = src.find("_UNMANAGED_TOOLS")
    assert idx > 0, "找不到 _UNMANAGED_TOOLS 定义"
    # 取定义前 1200 字符作为注释区
    context = src[max(0, idx - 1200):idx]
    assert "#" in context and any("\u4e00" <= ch <= "\u9fff" for ch in context), (
        "_UNMANAGED_TOOLS 缺少说明为什么不管这些工具 —— "
        "没有理由的豁免清单会越长越随意。")


def test_skill_is_not_silently_managed_as_readonly():
    """`skill` 若被登记为不管，必须在说明里点出它的风险。

    它能加载任意技能包，是一个能力入口。放行可以，但必须是**知情**的
    放行 —— 这与 A4 的 1.3「只解析不实施」同一条诚实性要求。
    """
    import inspect

    from sw_lib.agents import opencode

    src = inspect.getsource(opencode)
    if "skill" not in _UNMANAGED_TOOLS:
        pytest.skip("skill 已被托管，本条不适用")

    idx = src.find("_UNMANAGED_TOOLS")
    context = src[max(0, idx - 1200):idx]
    assert "skill" in context, (
        "skill 被列为不托管，但说明里没提它 —— "
        "一个能加载任意技能包的入口，至少要写明这是知情的选择。")


class _FakeAgent:
    """只提供 `_tool_switches` 需要的那点东西，不起 opencode 进程。"""
    stage = "01-brainstorming"
    role_id = None

    def _allowed_tool_names(self):
        return list(TOOL_MAP)
