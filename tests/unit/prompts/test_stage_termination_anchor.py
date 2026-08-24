"""prompt 必须给 agent 一个**可执行的终止动作**，而不只是描述性的收尾要求。

## 现场

任务 `ppppp`：`15:02:00 → 15:07:58` 之间 14 轮 `question`，13 条 decisions
全部落盘，产出区与模板区**全空**。它不是死循环 bug —— 每一轮都在等真人回答、
每一次回答都被记下来了。它是**没有出口**。

## 根因

提交 `c0b7338` 把 01 prompt 的第 4 条从

    **必须回填模板**：用 `write_file` 写回 workspace/tasks/{task}/01-brainstorming.md

改成

    **产出直接写在回复正文里**，不要试图写任务记账文件

当初改它的理由是对的：`_permission_rules()` 对 `workspace/**` 的 write/edit
一律 deny，那条指令的成功率恒为 0（A0 的 2.9.7 第 2 条）。
但顺手删掉的东西比修掉的更重要 —— 那是 agent 唯一**可执行**的终止动作，
而且模板只有 3 个问题槽位，天然带边界。

换上的替代物是一段没有工具调用、没有数量锚点的描述性文字。
「把产出写在正文里」无法被 agent 判定为「已完成」：它每说一句话都在正文里，
于是「我说完了吗」这个问题没有可执行的答案，只能继续问。

## 为什么不是「加轮次上限」

上限会把「消除歧义」变成「凑够数」，且恰好在最需要澄清的复杂任务上最先失效。
正确的收敛条件是**语义**的：`hook-01-01` 的歧义分数达标（见
`tests/unit/workflow/test_ambiguity_gate.py`）。本文件负责的是另一半 ——
达标之后，agent 得有一个**动作**可做。

## 判据形状

三条，缺任何一条这次修复就退回文案层面：

1. prompt 里确有落盘指令，且**指向本阶段文件**；
2. 那条路径在**真实权限语义**下解析为 `allow`（不是「规则表里有一条 allow」——
   `findLast` 语义下后面任何命中的 deny 都会盖掉它）；
3. 指令用的工具名是 agent **真能调用**的原生名（`write` / `edit`），
   不是 harness 抽象名 `write_file`（A0 的 2.9.7 第 1 条：模型照抄抽象名
   会先撞一次 `invalid tool`）。

## ⚠️ 判据重做（DEV-PROTOCOL 1.2）

初版的 `_prompt()` 读的是 **YAML 模板原文**，于是断言 `01-brainstorming.md`
这个字面量。它有两个毛病：

* 模板里的路径改成 `{stage_file}` 占位符之后判据就红了 —— 而那次改动
  恰恰是**修好了**参照系问题（见下）。判据反过来惩罚正确的修法。
* 更根本的是：agent 看到的不是模板，是 `build()` 渲染并拼上全局规则、
  工具说明、hooks 全文之后的**完整 prompt**。拿模板当判据，等于测了一份
  没有人读过的文本。

现改为断言渲染后的完整 prompt。附带收益：hooks 全文也被 `_read_hook_rules`
内联进 prompt，于是 `hooks/*.md` 里的工具名错误同样会被本文件的第 3 类判据
抓到 —— `test_prompt_tool_contract.py` 只扫模板与 `system.yaml`，
从来没覆盖 hooks（`hook-01-02` 的 `ask_user` 因此一直漏着）。

## 为什么落点要给绝对路径

agent 的 cwd 是 `repo/<task>`（`OpenCodeAgent._default_workdir`），
而阶段文件在 harness 根下的 `workspace/tasks/<task>/`。给相对路径它会在
cwd 下拼一层，落到不存在的位置 —— 任务 `helloworld` 就这么连打 8 次空处
（`test_prompt_path_frame.py`）。所以 prompt 注入的是绝对路径
（`builder.build` 的 `stage_file`），权限规则两种写法都放行。
"""

import re

import pytest

from sw_lib.agents.opencode import TOOL_MAP, _MANAGED_TOOLS, OpenCodeAgent
from sw_lib.core.config import ROOT, TASKS
from sw_lib.core.state import write_state
from sw_lib.prompts import PromptBuilder, PromptRegistry

TPL_DIR = ROOT / "sw_lib" / "prompts" / "templates"

#: 需要有落盘终止动作的阶段。04/05 不在其列：04-review 的 write 开关是关的
#: （reviewer 不该改被审对象），05-archive 由 harness 归档。
_WRITING_STAGES = ("01-brainstorming", "02-planning", "03-coding")

#: 阶段名 → stage_idx，`build()` 需要。
_STAGE_IDX = {"01-brainstorming": 0, "02-planning": 1, "03-coding": 2}

#: 探针任务名。用回收前缀，避免 `OpenCodeAgent` 构造时在 `repo/` 留孤儿目录。
_TASK = "pytest-anchor"


@pytest.fixture(autouse=True)
def probe_task():
    """建一个真任务目录，让 `build()` 走完整渲染路径。"""
    import shutil

    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {"id": _TASK, "stage": "01-brainstorming",
                        "stage_idx": 0, "stage_status": "pending",
                        "agent": "mock", "target_dir": f"repo/{_TASK}"})
    yield
    shutil.rmtree(d, ignore_errors=True)


def _prompt(stage: str) -> str:
    """agent 实际收到的完整 prompt（含全局规则、工具说明、hooks 全文）。"""
    builder = PromptBuilder(PromptRegistry(TPL_DIR))
    text = builder.build(task_name=_TASK, stage=stage,
                         stage_idx=_STAGE_IDX[stage])
    assert text, f"{stage} 的 prompt 渲染为空 —— 判据会空转"
    return text


class _Probe:
    """够 `_permission_rules()` 用的最小 agent，不起 opencode 进程。"""

    role_id = None
    _witness_phase = None

    def __init__(self, stage: str, name: str = "probe-task"):
        self.stage = stage
        self.name = name

    def _tool_switches(self):
        return OpenCodeAgent._tool_switches(self)

    def _allowed_tool_names(self):
        return OpenCodeAgent._allowed_tool_names(self)

    def _substage_write_rules(self):
        return OpenCodeAgent._substage_write_rules(self)

    def _witness_substage(self):
        return OpenCodeAgent._witness_substage(self)

    def _stage_file_write_rules(self):
        return OpenCodeAgent._stage_file_write_rules(self)

    def rules(self):
        return OpenCodeAgent._permission_rules(self)


def _verdict(rules, permission: str, path: str) -> str:
    """复刻 opencode 的求值语义：星号 → `.*`（带 `s` 标志），`findLast` 胜出。

    与 `tests/unit/agents/test_stage_file_writable.py` 同一套语义。
    「表里有 allow」不等于「这条路径可写」—— 顺序正是最容易写错的地方。
    """
    verdict = "ask"
    for r in rules:
        if r["permission"] != permission:
            continue
        pat = "^" + re.escape(r["pattern"]).replace(r"\*", ".*") + "$"
        if re.match(pat, path, re.S):
            verdict = r["action"]
    return verdict


# ── 1. 终止动作必须存在 ──

def test_01_prompt_has_an_executable_termination_action():
    """01 prompt 必须让 agent 把产出**写进阶段文件**。

    `ppppp` 的 14 轮就是这条指令被删之后的直接后果：agent 手里只剩
    `question` 一个可执行动作，「说完了」不是动作。
    """
    text = _prompt("01-brainstorming")

    target = str(TASKS / _TASK / "01-brainstorming.md")
    assert target in text, (
        "01 prompt 没有指向阶段文件的落盘指令 —— agent 没有可执行的终止动作，"
        f"只能一直 question（任务 ppppp：14 轮、零产出）。期望路径: {target}")


def test_01_prompt_convergence_is_semantic_not_a_round_cap():
    """收敛条件必须指向**歧义分数**，而不是问题轮数上限。

    轮次上限把「消除歧义」变成「凑够数」，在最需要澄清的任务上最先失效。
    分数是 `hook-01-01` 早就写明的语义判据，本轮已接线为真校验。
    """
    text = _prompt("01-brainstorming")

    assert "歧义分数" in text, \
        "01 prompt 没有提歧义分数 —— 收敛条件仍然只是「我觉得问够了」"


@pytest.mark.parametrize("stage", _WRITING_STAGES)
def test_writing_stages_declare_where_output_goes(stage):
    """能写盘的阶段都要说明产出落在哪。

    02 的 prompt 至今只有一句「请开始规划阶段的工作」（A0 的 2.9.9 第 2 条
    登记的未修项）—— 任务 `helloworld2` 的 agent 于是把 DAG 写进了
    `repo/helloworld2/PLAN.md`，一个没有任何判据会去读的地方。
    """
    text = _prompt(stage)

    target = str(TASKS / _TASK / f"{stage}.md")
    assert target in text, (
        f"{stage} 的 prompt 没说产出该落在哪 —— agent 只能自己挑地方，"
        f"挑中的地方没有任何判据会读（helloworld2 把 DAG 写进了 PLAN.md）。"
        f"期望路径: {target}")


@pytest.mark.parametrize("stage", _WRITING_STAGES)
def test_declared_path_is_absolute(stage):
    """落点必须是**绝对路径** —— agent 的 cwd 不是 harness 根。

    给 `workspace/tasks/<task>/x.md` 这种 harness 相对写法，agent 会在
    `repo/<task>/` 下拼一层，落到不存在的位置然后反复探路
    （任务 `helloworld` 实测 8 次 read/glob 打空）。
    """
    text = _prompt(stage)
    target = str(TASKS / _TASK / f"{stage}.md")

    assert target.startswith("/"), "前提不成立：TASKS 不是绝对路径"
    idx = text.find(target)
    assert idx > 0, f"{stage} 的 prompt 里找不到绝对落点 {target}"


# ── 2. 指令指向的路径必须真的可写 ──

@pytest.mark.parametrize("stage", _WRITING_STAGES)
def test_declared_output_path_is_actually_writable(stage):
    """prompt 让写的那条路径，硬层必须真的放行。

    这是本轮修复与 `c0b7338` 之前那版的**唯一实质差别**：当时同样有这条
    指令，但 `workspace/**` 的 deny 让它成功率恒为 0，agent 老实执行、
    无声写不进去，然后照常宣布完成（A0 的 2.9.7 第 2 条）。

    判据按真实语义解析，而不是查「规则表里有没有 allow」。
    """
    task = "anchor-probe"
    rules = _Probe(stage, task).rules()
    path = f"workspace/tasks/{task}/{stage}.md"

    assert _verdict(rules, "write", path) == "allow", (
        f"prompt 要求写 {path}，硬层却不放行 —— 指令成功率恒为 0，"
        f"与被删掉那条 write_file 指令同一个坑")


def test_criteria_targets_remain_denied():
    """放行阶段文件不得把判据区一起放开。

    `.state` 里有 Gate 与 output_nonce（受 HMAC 覆盖），是本轮
    「事后校验篡改」能成立的根据。它必须仍然写不得。
    """
    task = "anchor-probe"
    rules = _Probe("01-brainstorming", task).rules()

    for path in (f"workspace/tasks/{task}/.state",
                 f"workspace/tasks/{task}/facts/plan.json",
                 "STATUS.json"):
        assert _verdict(rules, "write", path) == "deny", \
            f"判据目标 {path} 被放开了 —— 事后校验的真相源就没了"


# ── 3. 工具名必须是 agent 真能调用的 ──

def test_termination_action_names_a_callable_tool():
    """落盘指令必须用原生工具名（`write` / `edit`），不能用抽象名。

    `write_file` 是 harness 内部词汇（`TOOL_MAP` 的键），agent 调它会拿到
    `invalid tool` —— 与 `ask_user` 完全同型（A0 的 2.9.7 第 1 条）。
    终止动作调不通，等于没有终止动作。
    """
    text = _prompt("01-brainstorming")
    native = set(TOOL_MAP["write_file"]) | set(_MANAGED_TOOLS)

    backticked = set(re.findall(r"`([a-z][a-z0-9_]{2,})`", text))
    abstract = {t for t in backticked if t in TOOL_MAP}

    assert not abstract, (
        f"01 prompt 把抽象名 {sorted(abstract)} 当成可调用工具 —— "
        f"agent 只能调 {sorted(native)}")

    assert backticked & set(TOOL_MAP["write_file"]), (
        f"01 prompt 没提任何可用来落盘的原生工具 "
        f"{sorted(TOOL_MAP['write_file'])}，只说了「写文件」这件事:\n{text}")


# ── 4. 不得诱导 agent 去动围栏与 Gate ──

def test_prompt_warns_against_touching_fence_and_gate():
    """放开写权限之后，必须明说哪两处不是它的。

    围栏（`<!-- sw:ai-output:... -->`）与 `## Gate` 一动就会被
    `output_check.check_tamper` 检出。不提前说清，agent 会「顺手整理格式」
    然后在准出时被拦 —— 那是我们没说，不是它违规。
    """
    text = _prompt("01-brainstorming")

    assert "sw:ai-output" in text, \
        "prompt 没提围栏标记不可改 —— agent 会顺手改格式然后被判篡改"
    assert "Gate" in text, \
        "prompt 没提 Gate 不可自行勾选 —— 那是用户的批准动作"
