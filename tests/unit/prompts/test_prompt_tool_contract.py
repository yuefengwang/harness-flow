"""prompt 里说的工具名与写入许可，必须与硬层实际生效的一致。

任务 `helloworld` 的现场暴露了两处「指令与约束互相矛盾」：

1. **工具名不存在。** 各阶段 prompt 写「**必须**使用 `ask_user` 工具」，
   而 opencode 侧真实工具名是 `question`（`TOOL_MAP` 做映射）。模型照
   prompt 调用，先撞一次
   `invalid {'tool': 'ask_user', 'error': "...unavailable tool..."}`
   才改用 `question` —— 白费一轮，且日志里凭空多出 error。
   01 阶段的 `bash` 同理：analyst 角色没有 `run_command`，prompt 却
   没有任何地方说明本阶段能用什么。

2. **prompt 要求做的事，硬层明确禁止。** 01 的 prompt 写「**必须回填模板**：
   用 `write_file` 把结论写回 `workspace/tasks/{task}/01-brainstorming.md`」，
   而 `_permission_rules()` 的 `guarded` 列表对 `write`/`edit` 把
   `workspace/**` 与 `**/workspace/**` 一律 deny。这条指令的成功率是 0，
   不是偶发失败 —— agent 老实执行、无声无息地写不进去，然后照常宣布完成。

根因是结构性的：**指令（prompt YAML）与约束（permission 规则）由两套
独立代码维护，彼此不校验**。没有任何测试会发现这对矛盾。这与 A2 反复
踩到的「单元全绿但机制没接通」同源，只是这次两端都是我们自己写的规格。

所以判据不是「文案里别写错字」，而是**交叉校验**：
prompt 提到的每个工具名，必须是 agent 真能调用的；
prompt 要求写入的每个路径，必须不在硬层的禁写名单里。
"""

import re

import pytest

from sw_lib.agents.opencode import TOOL_MAP, _MANAGED_TOOLS, OpenCodeAgent
from sw_lib.core.config import ROOT, STAGES, get_tools_for_stage
from sw_lib.prompts import PromptRegistry

TPL_DIR = ROOT / "sw_lib" / "prompts" / "templates"

#: prompt 正文里出现的 `xxx` 形式的候选工具名。只收下划线/字母数字，
#: 且必须像工具名（不含空格、点、斜杠）。
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]{2,})`")

#: harness 侧的抽象工具名（写在 config 的 roles[].tools 里）。
_HARNESS_TOOL_NAMES = frozenset(TOOL_MAP)

#: opencode 侧的真实工具名。
_NATIVE_TOOL_NAMES = frozenset(_MANAGED_TOOLS) | {
    t for names in TOOL_MAP.values() for t in names
}

#: 两侧合法名字的并集 —— 用于识别「这个词是不是在说某个工具」。
_VALID_TOOL_NAMES = _HARNESS_TOOL_NAMES | _NATIVE_TOOL_NAMES

#: ⚠️ 本文件的第一版判据写错了，这里是**显式重做**（DEV-PROTOCOL 1.2）。
#:
#: 初版断言「prompt 提到的工具名 ∈ 抽象名 ∪ 原生名」。它对 `ask_user`
#: 恒为真 —— `ask_user` 正是 `TOOL_MAP` 的键，所以判据绿着，而现场
#: 那个 `invalid tool 'ask_user'` 照旧发生。
#:
#: 真正的分界是：**agent 只能调用 opencode 原生名**。抽象名只是 harness
#: 内部用来配 `roles[].tools` 的词汇，写进 prompt 就会被模型当成可调用的
#: 工具。所以判据必须是「prompt 里作为**可调用工具**提到的名字，
#: 必须是原生名」。
_AGENT_CALLABLE = _NATIVE_TOOL_NAMES

#: 反引号里可能出现但**不是**工具名的词（阶段名、字段名、命令等）。
#: 这份名单只用于降噪，不得用来豁免真正的工具名错误。
_NOT_TOOLS = frozenset({
    "advance", "task_name", "stage", "stage_name", "global_rules",
    "red_witness", "rewitness", "abandon", "none", "pytest", "python",
    "conventional", "commits", "readme", "workspace", "tasks", "state",
    "facts", "diff", "patch", "numstat", "stat", "json", "yaml", "sha256",
    "verify", "cmd", "passed", "failed", "skipped", "unavailable",
    "reroute", "route", "gate", "claims", "spec", "plan", "tests",
    "coding", "review", "archive", "planning", "brainstorming",
})


def _prompt_texts():
    """所有阶段模板 + system.yaml 的正文。"""
    registry = PromptRegistry(TPL_DIR)
    out = {}
    for stage in STAGES:
        tpl = registry.get(stage)
        out[stage] = tpl["system_prompt"]
    out["system.yaml"] = registry.get_system_rules()
    return out


#: 渲染探针用的任务名。回收前缀，避免在 `repo/` 留孤儿目录。
_RENDER_TASK = "pytest-tool-contract"


def _rendered_prompt(stage: str) -> str:
    """`build()` 渲染后的完整 prompt —— agent 实际收到的那份文本。

    模板里的落点是 `{stage_file}` 占位符，扫原文一条也抓不到。
    判据必须看渲染结果，否则就是在测一份没有人读过的文本。
    """
    import shutil

    from sw_lib.core.config import TASKS
    from sw_lib.core.state import write_state
    from sw_lib.prompts import PromptBuilder

    d = TASKS / _RENDER_TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    try:
        write_state(_RENDER_TASK, {
            "id": _RENDER_TASK, "stage": stage,
            "stage_idx": STAGES.index(stage), "stage_status": "pending",
            "agent": "mock", "target_dir": f"repo/{_RENDER_TASK}",
        })
        builder = PromptBuilder(PromptRegistry(TPL_DIR))
        return builder.build(task_name=_RENDER_TASK, stage=stage,
                             stage_idx=STAGES.index(stage)) or ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _mentioned_tools(text):
    """正文里被反引号括起、且看起来像工具名的词。"""
    found = set()
    for word in _BACKTICKED.findall(text):
        if word in _NOT_TOOLS:
            continue
        # 只关心「疑似工具」：出现在已知工具名集合里，或以 _file/_files/_user
        # /_command 结尾（harness 抽象工具的命名习惯），或恰好是 opencode 原生名。
        if word in _VALID_TOOL_NAMES or word.endswith(
                ("_file", "_files", "_user", "_command")):
            found.add(word)
    return found


# ── 1. prompt 提到的工具必须真实存在 ──

@pytest.mark.parametrize("source", sorted(_prompt_texts()))
def test_prompt_only_names_agent_callable_tools(source):
    """prompt 里当作「可调用工具」提到的名字，必须是 opencode 原生名。

    这是本文件的判据核心，也是初版写错的地方（见上方 `_AGENT_CALLABLE`
    的重做说明）：`ask_user` 是 harness 抽象名，agent **调不到**，
    模型照 prompt 调用只会拿到
    `invalid {'tool': 'ask_user', ...unavailable tool...}`。

    抽象名出现在 `config/config.yaml` 的 `roles[].tools` 里是正确的；
    出现在 **prompt** 里就是 bug。
    """
    text = _prompt_texts()[source]
    offenders = sorted(t for t in _mentioned_tools(text)
                       if t not in _AGENT_CALLABLE)
    assert not offenders, (
        f"{source} 的 prompt 把 {offenders} 当成可调用工具，但 agent 只能调用"
        f" opencode 原生名 {sorted(_AGENT_CALLABLE)}。\n"
        f"抽象名→原生名的映射是 TOOL_MAP，prompt 必须写映射后的名字，"
        f"否则模型照此调用会撞 invalid tool（任务 helloworld 现场）。")


# ── 2. prompt 不得要求 agent 写硬层禁写的路径 ──

def _verdict(rules, permission: str, path: str) -> str:
    """复刻 opencode 的权限求值：星号 → `.*`（带 `s` 标志），`findLast` 胜出。

    与 `tests/unit/agents/test_stage_file_writable.py` 同一套语义。
    「规则表里有一条 allow」不等于「这条路径可写」—— 后面任何命中的 deny
    都会盖掉它，而顺序正是这套规则最容易写错的地方（纪律 4）。
    """
    verdict = "ask"
    for r in rules:
        if r["permission"] != permission:
            continue
        pat = "^" + re.escape(r["pattern"]).replace(r"\*", ".*") + "$"
        if re.match(pat, path, re.S):
            verdict = r["action"]
    return verdict


def _guarded_write_patterns():
    """硬层对 write/edit 的禁写名单，取自实际生效的规则生成器。

    刻意不复制常量，而是从 `_permission_rules()` 的产出里反查 ——
    复制一份就又多了一个会各自漂移的副本，正是本文件要防的事。
    """
    from sw_lib.agents.opencode import OpenCodeAgent

    rules = OpenCodeAgent._permission_rules(_FakeAgent())
    return {r["pattern"] for r in rules
            if r["permission"] in ("write", "edit") and r["action"] == "deny"}


class _FakeAgent:
    """只提供 `_permission_rules` 需要的那几个属性，不起 opencode 进程。"""
    stage = "01-brainstorming"
    role_id = None
    name = "prompt-contract-probe"
    _witness_phase = None

    def _tool_switches(self):
        return {t: True for t in _MANAGED_TOOLS}

    def _allowed_tool_names(self):
        return list(_HARNESS_TOOL_NAMES)

    def _substage_write_rules(self):
        return []

    def _stage_file_write_rules(self):
        """借用**生产实现**，不自己伪造一份。

        这里曾经是 `return []`（当时生产侧还没有这个方法）。写成假实现
        会让本文件的判据看着一套规则、agent 实际跑另一套 ——
        那正是本文件存在的理由（指令与约束各自维护、彼此不校验）。
        """
        from sw_lib.agents.opencode import OpenCodeAgent

        return OpenCodeAgent._stage_file_write_rules(self)


def test_guarded_patterns_are_discoverable():
    """前提自检：能真的取到硬层禁写名单，否则下一条判据是空转。

    空循环假绿在本项目已撞到过（A2 的 probe 用例），这里显式守卫。
    """
    patterns = _guarded_write_patterns()
    assert patterns, "取不到 write/edit 的 deny 名单，下面的判据会空转"
    assert any("workspace" in p for p in patterns), \
        f"禁写名单里没有 workspace —— 判据前提已变，请复核: {sorted(patterns)}"


@pytest.mark.parametrize("source", sorted(_prompt_texts()))
def test_prompt_does_not_order_writes_into_guarded_paths(source):
    """prompt 不得命令 agent 写入硬层禁写的路径。

    01 的「必须回填 `workspace/tasks/{task_name}/01-brainstorming.md`」
    与 `guarded` 里的 `workspace/**` 直接冲突，成功率恒为 0。
    agent 收不到任何否定信号，于是「以为自己写了」。

    ⚠️ **判据重做**（DEV-PROTOCOL 1.2）。第二版是「写入动词 + `workspace/`
    的文本黑名单」，它建立在一个已经不成立的前提上：`workspace/**` **整段**
    deny。现在硬层是逐路径求值 —— 阶段文件
    （`workspace/tasks/<task>/<stage>.md`）被精确放行，判据区（`.state` /
    `facts/` / `STATUS.json`）仍然 deny。

    继续用文本黑名单会把**正确的**落盘指令判成违规，而那条指令正是 agent
    唯一可执行的终止动作（任务 `ppppp`：删掉它之后 14 轮 question、零产出）。

    所以判据改为：抽出 prompt 里提到的每条 `workspace/...` 路径，用**真实的
    权限求值语义**（`findLast`）判它可不可写。这样两个方向都守得住 ——
    prompt 让写判据区会红，prompt 让写阶段文件不会红。
    """
    text = _prompt_texts()[source]

    # 写入动词与路径之间允许换行：01 的真实文案里两者恰好跨行，
    # 单行匹配会让判据绿着而 bug 还在（这是第二版修掉的坑，保留该行为）。
    stage = source if source in STAGES else "01-brainstorming"

    # 扫**渲染后**的文本：模板里落点是 `{stage_file}` 占位符，
    # 扫原文会一条也抓不到 —— 那种「全 skip」的绿是空转，本项目已撞过多次。
    if source in STAGES:
        text = _rendered_prompt(stage)

    hits = re.findall(
        r"(?:write_file|write|edit|回填|写回|写入)[\s\S]{0,160}?"
        r"([^\s`'\"]*workspace/tasks/[^\s`'\")）]+)", text)
    if not hits:
        pytest.skip(f"{source} 的 prompt 没有指向 workspace 的写入指令")

    probe = _FakeAgent()
    probe.stage = stage
    # 放行是**按任务精确匹配**的（`_stage_file_write_rules` 只放行本任务本阶段），
    # 所以探针的任务名必须与渲染时用的一致，否则判据会假红。
    probe.name = _RENDER_TASK
    rules = OpenCodeAgent._permission_rules(probe)

    for raw in hits:
        # 渲染后是绝对路径；`system.yaml` 里可能仍有占位符写法，一并代入。
        path = raw.replace("{task_name}", _RENDER_TASK).rstrip(".,;：")
        assert _verdict(rules, "write", path) == "allow", (
            f"{source} 的 prompt 命令 agent 写 {path}，硬层却 deny —— "
            f"这条指令的成功率是 0，agent 收不到否定信号，"
            f"于是「以为自己写了」（A0 的 2.9.7 第 2 条）")


def test_write_instruction_probe_is_not_vacuous():
    """前提自检：上一条判据必须真的检到了落盘指令。

    若三个可写阶段全部 skip，那条判据就是空转 —— 一个「全 skip 的绿」
    在本项目已经出现过多次（A2 的 probe 用例、空循环假绿）。
    这里把空转本身变成红。
    """
    found = {}
    for stage in ("01-brainstorming", "02-planning", "03-coding"):
        text = _rendered_prompt(stage)
        found[stage] = bool(re.search(
            r"(?:write|edit|回填|写回|写入)[\s\S]{0,160}?\S*workspace/tasks/",
            text))

    missing = sorted(s for s, ok in found.items() if not ok)
    assert not missing, (
        f"{missing} 的 prompt 里没有可被检出的落盘指令 —— "
        f"上一条判据对这些阶段是空转，且 agent 没有终止动作可执行")


# ── 3. prompt 必须如实告知本阶段可用的工具 ──

@pytest.mark.parametrize("stage", STAGES)
def test_prompt_declares_available_tools(stage):
    """每个阶段的 prompt 必须列出该阶段实际可用的工具。

    01 阶段的 analyst 没有 `run_command`，模型不知道，于是尝试 `bash`
    并撞 invalid。工具面是运行时按角色算出来的，prompt 不说，模型只能猜。

    判据是「prompt 里出现了本阶段工具清单」而非某个固定措辞 ——
    锁行为不锁文案。
    """
    from sw_lib.prompts import PromptBuilder

    allowed = set(get_tools_for_stage(stage))
    assert allowed, f"前提不成立：{stage} 解析不到任何工具"

    registry = PromptRegistry(TPL_DIR)
    builder = PromptBuilder(registry)
    text = builder.describe_tools(stage, role_id=None)

    for tool in allowed:
        assert tool in text, (
            f"{stage} 可用工具含 `{tool}`，但 prompt 的工具说明里没提它:\n{text}")


@pytest.mark.parametrize("stage", STAGES)
def test_prompt_tool_declaration_excludes_unavailable_tools(stage):
    """工具说明不得列出本阶段**不**可用的工具。

    多列比不列更糟：模型会去调一个必然被 deny 的工具，然后在
    「被拒绝」和「我理解错了」之间乱猜。
    """
    from sw_lib.prompts import PromptBuilder

    allowed = set(get_tools_for_stage(stage))
    missing = _HARNESS_TOOL_NAMES - allowed
    if not missing:
        pytest.skip(f"{stage} 拿到了全部工具，无可验证的排除项")

    registry = PromptRegistry(TPL_DIR)
    builder = PromptBuilder(registry)
    text = builder.describe_tools(stage, role_id=None)

    # 工具说明是一个独立区段，取它到下一个空行为止，避免误伤别处文案。
    for tool in missing:
        assert tool not in text, (
            f"{stage} 不可用的工具 `{tool}` 出现在工具说明里 —— "
            f"agent 会去调它然后被 deny:\n{text}")


def test_describe_tools_is_wired_into_build():
    """工具说明必须真的进了 prompt，而不只是有个能调的函数。

    这条是本项目累计撞过四次的「机制没接通」：函数写好了、单元测试绿了，
    但没有任何生产调用点。判据取「build() 的产出里含工具说明」。
    """
    import shutil

    from sw_lib.core.state import write_state
    from sw_lib.core.config import TASKS
    from sw_lib.prompts import PromptBuilder

    name = "prompt-tool-contract-wired"
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    try:
        write_state(name, {
            "id": name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock", "target_dir": "repo/t",
        })
        registry = PromptRegistry(TPL_DIR)
        builder = PromptBuilder(registry)
        prompt = builder.build(task_name=name, stage="01-brainstorming",
                               stage_idx=0)

        assert prompt, "build 返回空"
        declared = builder.describe_tools("01-brainstorming", role_id=None)
        assert declared.strip(), \
            "describe_tools 返回空 —— 工具说明没有任何内容可注入"
        # 取说明里的首行特征串，确认它出现在完整 prompt 中
        marker = declared.strip().splitlines()[0]
        assert marker in prompt, (
            "describe_tools 没有被 build() 接进 prompt —— "
            f"又一次「机制没接通」。\n说明首行: {marker!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
