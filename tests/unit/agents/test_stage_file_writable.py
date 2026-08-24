"""让 agent 能回填阶段模板，同时判据一样不放开。

## 为什么要改

`c0b7338` 删掉了 01 prompt 里那条「用 `write_file` 回填模板」的指令 ——
理由成立：`_permission_rules()` 对 `workspace/**` 的 write/edit 一律 deny，
那条指令的成功率恒为 0（A0 的 2.9.7 第 2 条）。

但删掉它之后，agent 失去了**唯一可执行的终止动作**。任务 `ppppp` 的现场：

    15:02:00 → 15:07:58，14 轮 `question`，13 条 decisions 全部落盘，
    产出区与模板区全空。agent 没卡死、没重复提问，它在正常地一直问下去。

「收集完所有必要信息后再给出方案」里的「所有必要信息」由 agent 自行判断，
而它没有任何可执行的收敛判据 —— 每答完一轮都能再想出一个更细的维度
（出差类型 → 审批级数 → 通知方式 → 数据备份 → UI 组件库 → 数据库设计）。
`hook-01-02` 只写「≥3 questions」，**只有下界没有上界**。

## 为什么不是加轮次上限

上限会把「消除歧义」变成「凑够数」—— 复杂需求本该多问，
而一刀切的上限会在最需要澄清的任务上最先失效（用户拍板：方向不对）。

## 定案：那段 deny 保护的是一块没人当判据用的区域

真正必须防的是三样：`.state`（Gate 签署 / decisions / 证据签名的真相源，
有 HMAC）、`facts/`（04 的事实包）、**围栏区**（sw 写的 nonce 界定
「哪段是 agent 说的」）。

而 `01-brainstorming.md` 的**模板区**（Ambiguity Score / Clarifying Questions
/ Pre-mortem / ADR）一样都不是：`check_01` 读 `.state` 拿 Gate、读围栏区判
产出，模板区在任何判据里都不出现。也就是说 `workspace/**` 那段 deny 拦住的
是一块无人当判据的区域，代价却是砍掉了 agent 的终止动作。

因此精确放行阶段文件（`workspace/tasks/<task>/<stage>.md`），
其余判据目标继续 deny。改动的边界由本文件钉住。
"""

import pytest

from sw_lib.agents.opencode import OpenCodeAgent


def _rules(stage: str = "01-brainstorming", task: str = "demo"):
    agent = object.__new__(OpenCodeAgent)
    agent.stage = stage
    agent.name = task
    return agent._permission_rules()


def _verdict(rules, permission: str, path: str) -> str:
    """按 opencode 的求值语义判某条路径的最终结论。

    服务端把 pattern 里的星号编译成 `.*` 并带 `s` 标志（已反编译确认），
    然后 `findLast` —— **最后一条命中的规则胜出**。这里复刻同一套语义，
    因为「规则表里有一条 allow」不等于「这条路径真的被放行」：
    后面任何一条命中的 deny 都会把它盖掉。只断言「存在某条规则」的测试
    会漏掉顺序错误，而顺序正是这套规则最容易写错的地方（纪律 4）。
    """
    import re

    verdict = "ask"       # opencode 无匹配时的默认
    for r in rules:
        if r["permission"] != permission:
            continue
        pat = "^" + re.escape(r["pattern"]).replace(r"\*", ".*") + "$"
        if re.match(pat, path, re.S):
            verdict = r["action"]
    return verdict


# ── 放行：agent 必须能回填自己这一阶段的模板 ──

@pytest.mark.parametrize("stage", ["01-brainstorming", "02-planning"])
def test_agent_can_write_its_own_stage_file(stage):
    """阶段文件必须可写 —— 否则 agent 没有任何终止动作可执行。

    这是任务 `ppppp` 那 14 轮提问的直接根因：能做的只有继续问。
    """
    rules = _rules(stage=stage, task="ppppp")
    path = f"workspace/tasks/ppppp/{stage}.md"

    assert _verdict(rules, "write", path) == "allow", \
        f"{path} 不可写 —— agent 无法回填模板，也就没有收敛出口"


def test_absolute_path_to_stage_file_is_also_allowed():
    """绝对路径同样要放行 —— agent 的 cwd 是 repo/<task>，它很可能写绝对路径。

    只放行相对写法等于放行了一半：模型两种都会用，命中不了就又变成
    「指令成功率恒为 0」，与被删掉那条指令同一个坑。
    """
    rules = _rules(task="ppppp")
    path = "/Users/x/harness-flow/workspace/tasks/ppppp/01-brainstorming.md"

    assert _verdict(rules, "write", path) == "allow", \
        f"绝对路径写法未被放行:\n{path}"


def test_edit_permission_matches_write():
    """`edit` 与 `write` 必须同步放行 —— 回填模板天然是「改已有文件」。

    只放行 write 会让 agent 用 edit 时撞 deny，然后换 write 重试 ——
    多一轮无谓摩擦，且日志里会留下一次看起来像越权的失败。
    """
    rules = _rules(task="ppppp")
    path = "workspace/tasks/ppppp/01-brainstorming.md"

    assert _verdict(rules, "edit", path) == "allow", path


# ── 不放开：判据目标一律仍然 deny ──

@pytest.mark.parametrize("path", [
    "workspace/tasks/ppppp/.state",
    "/abs/harness-flow/workspace/tasks/ppppp/.state",
    "workspace/tasks/ppppp/.state.lock",
    "workspace/STATUS.json",
    "workspace/tasks/ppppp/facts/manifest.json",
    "workspace/tasks/ppppp/facts/tests.json",
    "config/.evidence_key",
])
def test_evidence_targets_stay_denied(path):
    """判据的真相源一样都不许写。

    放行阶段文件**不得**顺手放开这些 —— `.state` 存着 Gate 签署、decisions
    与 `_evidence_sig`，`facts/` 是 04 的审查输入。它们才是那段 deny 的目的。
    """
    rules = _rules(task="ppppp")

    for perm in ("write", "edit"):
        assert _verdict(rules, perm, path) == "deny", \
            f"{perm} 放开了判据目标: {path}"


def test_other_tasks_stage_files_are_denied():
    """只放行**本任务**的阶段文件，别人的不行。

    并行跑多个任务时，A 任务的 agent 改 B 任务的产出会让 B 的审查读到
    一份没人负责的文本 —— 而且极难追溯是谁写的。
    """
    rules = _rules(task="ppppp")

    assert _verdict(rules, "write",
                    "workspace/tasks/other/01-brainstorming.md") == "deny", \
        "放行范围溢出到了其它任务的阶段文件"


def test_non_stage_files_under_task_dir_stay_denied():
    """任务目录下的其它文件仍然 deny —— 放行的是模板，不是整个目录。

    `.log` / `.input` / `.context` 都不是 agent 该写的东西；
    尤其 `.log` 是事故追溯的依据，可写等于可篡改现场。
    """
    rules = _rules(task="ppppp")

    for name in (".log", ".input", ".context"):
        assert _verdict(rules, "write",
                        f"workspace/tasks/ppppp/{name}") == "deny", \
            f"放行溢出到了 {name}"


def test_review_stage_file_is_writable_but_criteria_are_not():
    """04-review 的阶段文件可写，判据目标仍然不可写。

    ⚠️ **判据重做**（DEV-PROTOCOL 1.2）。本函数初版叫
    `test_readonly_stage_still_cannot_write_stage_file`，断言 04 不可写，
    理由是「审查者不修改被审对象」。任务 `qqqq` 推翻了那个前提：客观轨 O6
    要求 `repo/<task>/README.md` 存在，而 04 没有写权限、03 的 prompt 又不提
    README —— 要求在 04 兑现、能力只在 03 存在，两次 `/advance` 输出逐字相同
    （A0 的 2.9.11）。用户拍板给 reviewer `write_file`。

    「审查者不修改被审对象」这条纪律并没有废掉，只是换了兑现方式：
    从「整个阶段不能写」收窄成「写不到判据」。真正会让审查失去意义的是
    reviewer 能改 `.state`（自己签 Gate、改 red_witness 的 unavailable），
    那部分仍然 deny。
    """
    rules = _rules(stage="04-review", task="ppppp")

    assert _verdict(rules, "write",
                    "workspace/tasks/ppppp/04-review.md") == "allow", \
        "04 的阶段文件不可写 —— 与 01/02/03 不一致，且 O6 无人能过"

    for path in ("workspace/tasks/ppppp/.state",
                 "workspace/tasks/ppppp/facts/tests.json"):
        assert _verdict(rules, "write", path) == "deny", \
            f"04 的写权限溢出到判据目标: {path}"


# ── 既有纪律不得被这次改动破坏 ──

def test_no_ask_action_introduced():
    """纪律 1：绝不产生 ask（会死等到 CHAT_TIMEOUT）。"""
    for stage in ("01-brainstorming", "02-planning", "03-coding", "04-review"):
        actions = {r["action"] for r in _rules(stage=stage)}
        assert "ask" not in actions, f"{stage} 产生了 ask 规则"


def test_path_scoped_deny_still_only_covers_write_and_edit():
    """纪律 2：**路径级** deny 只针对 write / edit —— bash 无法按路径约束。

    判据取「pattern 不是 `*` 的 deny」：工具开关本身也会产生
    `{bash, *, deny}`（01 阶段未授权 run_command），那是「整个工具关掉」，
    与「按路径约束」是两件事。混在一起断言会把正确行为判成违规。
    """
    denied = {r["permission"] for r in _rules()
              if r["action"] == "deny" and r["pattern"] != "*"}
    assert denied, "没有任何路径级 deny —— 判据区未受保护"
    assert denied <= {"write", "edit"}, \
        f"路径级 deny 覆盖了不可按路径约束的权限: {denied}"


def test_rules_are_stable_across_calls():
    """同一 agent 两次取规则必须一致 —— 规则表是 POST /session 的一次性输入。"""
    a = _rules(task="ppppp")
    b = _rules(task="ppppp")
    assert a == b


def test_task_without_name_does_not_crash_or_overshare():
    """`name` 缺失时不得放行任何 workspace 路径。

    读不到任务名就拼不出精确 pattern。此时**必须**回落到全段 deny，
    绝不能因为拼不出来就放行 `workspace/**` —— 那是把失败方向选反了。
    """
    agent = object.__new__(OpenCodeAgent)
    agent.stage = "01-brainstorming"
    rules = agent._permission_rules()

    assert _verdict(rules, "write",
                    "workspace/tasks/anything/01-brainstorming.md") == "deny", \
        "任务名缺失时放行了 workspace 路径"
