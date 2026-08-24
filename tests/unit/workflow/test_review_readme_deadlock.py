"""任务 qqqq 的现场：O6 缺 README 拦下 04，而**没有任何角色能创建它**。

## 现场

`16:20:10` 与 `16:21:39` 两次 `/advance`，输出逐字相同：

```text
❌ repo/qqqq/ 下无 README.md
❌ 客观轨硬失败: O6
   这些是程序判定的事实，不是意见 —— 修掉再推进。
error | 硬校验未通过，必须满足所有条件才能推进
```

Gate 四项全签、Route 已定 `05-archive`、pytest 2 passed —— 唯一拦路的是 O6。
用户重试一次，得到一模一样的结果，因为**重试不改变任何输入**。

## 死锁的三段链条

1. **03 从没被要求写 README。** 03 是唯一同时拥有 `write_file` 与 `run_command`
   的阶段，它的 prompt 列了产出落点（`Red-Green` / `Implementation Notes` /
   `Files Touched`），通篇不提 README。hooks 里 README 只出现在 04 与 05。
2. **04 的 reviewer 没有 `write_file`。**（`config.roles.reviewer.tools` 只有
   `list_files` / `read_file` / `run_command` / `ask_user`）
3. **O6 在 04 是硬阻断**（`severity=high`，`objective_check._readme_check`）。

于是：要求在 04 兑现，能力只在 03 存在，而 03 已经过去了。
这不是「agent 偷懒」，是**流程设计里没有人负责这件事**。

> **判例**：一条判据的**兑现阶段**必须与**能力所在阶段**一致，否则它不是
> 质量门禁，是死锁。同类形状已在 A0 的 2.9.7 出现过（prompt 让写、硬层禁写：
> 要求与能力错位），那次错在权限，这次错在阶段。

## 用户拍板的修法

**给 04 的 reviewer `write_file` 权限。** 这与「审查者不修改被审对象」有张力，
所以边界必须写死在测试里：能写文档与自己的阶段文件，**不能**改判据区
（`.state` / `facts/`），也不能改 `design_critic` 的只读性（A8 的 3.6 ——
那是主观轨审查者，与 04 的单角色回落 reviewer 是两回事）。

同时补另外两处，否则「能写」仍然不等于「知道要写」：
03 的 prompt 与 hooks 明说 README 是 03 的交付物；O6 的失败文案给出下一步。
"""

import json
import re
import shutil

import pytest

from sw_lib.agents.opencode import OpenCodeAgent
from sw_lib.core.config import ROOT, TASKS, get_tools_for_stage
from sw_lib.core.state import write_state
from sw_lib.prompts import PromptBuilder, PromptRegistry
from sw_lib.workflow import fact_pack

TPL_DIR = ROOT / "sw_lib" / "prompts" / "templates"


def _verdict(rules, permission: str, path: str) -> str:
    """复刻 opencode 的求值语义：星号 → `.*`（带 `s`），`findLast` 胜出。"""
    verdict = "ask"
    for r in rules:
        if r["permission"] != permission:
            continue
        pat = "^" + re.escape(r["pattern"]).replace(r"\*", ".*") + "$"
        if re.match(pat, path, re.S):
            verdict = r["action"]
    return verdict


def _rules(stage: str, task: str = "pytest-deadlock", role_id=None):
    agent = object.__new__(OpenCodeAgent)
    agent.stage = stage
    agent.name = task
    agent.role_id = role_id
    agent._witness_phase = None
    return agent._permission_rules()


# ── 1. 能力必须存在于兑现判据的那个阶段 ──

def test_review_stage_can_write_files():
    """04 必须能写文件 —— 否则 O6 是一道没人能过的关。

    ⚠️ 这条与 `test_stage_file_writable.test_readonly_stage_still_cannot_write_stage_file`
    的初版结论相反，属**判据重做**（DEV-PROTOCOL 1.2）：那条测试把「04 只读」
    当成不变量，而任务 `qqqq` 证明它与 O6 直接矛盾。用户拍板放开写权限。
    """
    tools = get_tools_for_stage("04-review")

    assert "write_file" in tools, (
        f"04-review 没有 write_file，O6 要求的 README 无人能创建 —— "
        f"任务 qqqq 因此在两次 /advance 之间原地卡死。当前工具: {tools}")


def test_review_can_write_readme_in_target_dir():
    """具体到 O6 要的那个文件：`repo/<task>/README.md` 必须可写。

    「角色有 write_file」与「这条路径真的放行」是两件事 ——
    `findLast` 语义下后面任何命中的 deny 都会把它盖掉。
    """
    rules = _rules("04-review")

    for path in ("repo/pytest-deadlock/README.md",
                 str(ROOT / "repo" / "pytest-deadlock" / "README.md")):
        assert _verdict(rules, "write", path) == "allow", \
            f"O6 要求的 README 路径不可写: {path}"


def test_review_can_write_its_own_stage_file():
    """04 的阶段文件也要放行 —— 与 01/02/03 一致。

    放开写权限之后再把阶段文件单独禁掉，只会让 agent 撞一次 deny 再换路，
    没有任何保护收益（判据在 `.state`，不在这个文件里）。
    """
    rules = _rules("04-review")

    assert _verdict(rules, "write",
                    "workspace/tasks/pytest-deadlock/04-review.md") == "allow", \
        "04 的阶段文件仍不可写"


# ── 2. 放开写权限不得溢出到判据区 ──

def test_review_still_cannot_write_criteria_targets():
    """判据区必须仍然 deny —— 这是放开写权限的前提。

    reviewer 能改 `.state` 就等于能给自己签 Gate、改 red_witness 的
    `unavailable`、改 claims。那不是「审查」，是自证。
    """
    rules = _rules("04-review")

    for path in ("workspace/tasks/pytest-deadlock/.state",
                 "workspace/tasks/pytest-deadlock/facts/tests.json",
                 "STATUS.json",
                 "config/.evidence_key"):
        assert _verdict(rules, "write", path) == "deny", \
            f"放开 04 的写权限溢出到了判据目标: {path}"


def test_design_critic_stays_read_only():
    """A8 的 3.6：`design_critic` 仍须纯只读。

    它与 04 的单角色回落 reviewer 是两个不同角色。给 reviewer 放权
    **不得**顺手把主观轨审查者也放开 —— 那条声明有独立的理由
    （审查者不改被审对象），且 A8/A9 尚未实现，此时放开无人会发现。
    """
    tools = get_tools_for_stage("04-review", role_id="design_critic")

    assert "write_file" not in tools, \
        f"design_critic 拿到了写权限，A8 的 3.6 纯只读声明被破坏: {tools}"
    assert "run_command" not in tools, \
        f"design_critic 拿到了 run_command: {tools}"


# ── 3. 「能写」不等于「知道要写」 ──

def test_coding_stage_is_told_to_write_readme():
    """README 必须在 **03** 就被要求 —— 那是能力所在的阶段。

    只给 04 放权而不在 03 提要求，结果是每个任务都要在 04 补一次文档，
    而 04 的角色定位是审查。判据落在 03 才让「兑现阶段 = 能力阶段」成立。
    """
    import shutil as _sh

    name = "pytest-deadlock-prompt"
    d = TASKS / name
    _sh.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    try:
        write_state(name, {"id": name, "stage": "03-coding", "stage_idx": 2,
                           "stage_status": "pending", "agent": "mock",
                           "target_dir": f"repo/{name}"})
        builder = PromptBuilder(PromptRegistry(TPL_DIR))
        text = builder.build(task_name=name, stage="03-coding", stage_idx=2)

        assert text, "prompt 渲染为空"
        assert "README" in text, (
            "03 的 prompt 不提 README，而 O6 在 04 硬阻断 —— "
            "要求与能力错位（任务 qqqq 的死锁）")
    finally:
        _sh.rmtree(d, ignore_errors=True)


def test_readme_failure_names_the_next_step():
    """O6 失败必须给出下一步 —— 否则用户只会原地重试。

    任务 `qqqq` 的两次 `/advance` 输出逐字相同，因为文案只说了「修掉再推进」，
    没说谁去修、修什么。A2 的 10.6 已判过同型：拦住一条路不给替代路径。
    """
    from sw_lib.workflow.objective_check import _readme_check

    target = ROOT / "repo" / "pytest-deadlock-absent"
    shutil.rmtree(target, ignore_errors=True)

    verdict = _readme_check(target)

    assert verdict["verdict"] == "fail", "前提不成立：不存在的目录应判 fail"
    blob = json.dumps(verdict, ensure_ascii=False)
    assert "README.md" in blob, blob
    assert any(k in blob for k in ("创建", "补", "下一步", "写")), (
        f"O6 的失败结论没告诉任何人该怎么办 —— 用户只能原地重试:\n{blob}")


def test_hook_actually_prints_the_next_step():
    """那句下一步必须**真的打到用户眼前**。

    `check_04-review.sh` 原先是 `detail or reason`，于是同时有两者的项
    （O6 正是如此）永远只显示 detail —— 「缺 README」说了是什么，
    没说谁去修。改了 `reason` 而打印逻辑吃掉它，等于没改。

    这是本项目的常见形状：**产出了正确的信息，但没有接到出口**。
    判据取「hook 脚本里存在打印 reason 的分支」，锁行为不锁文案。
    """
    script = (ROOT / "hooks" / "check_04-review.sh").read_text(encoding="utf-8")

    assert 'c.get("reason")' in script, \
        "hook 脚本没有单独取 reason —— 下一步提示不会被打印"
    # 关键是不能只有 `detail or reason` 这一处：那个表达式在 detail 非空时
    # 永远短路，reason 拿不到出口。
    assert script.count('c.get("reason")') >= 2, (
        "reason 只出现在 `detail or reason` 里 —— O6 同时有 detail 与 reason，"
        "该表达式会短路掉 reason，下一步提示永远不显示")


# ── 4. claims：声明就在模板区，却被判为「未声明」 ──

def test_claims_are_extracted_from_template_region_too():
    """agent 把文件清单填进模板区的 `## Files Touched` 时也要认。

    任务 `qqqq` 的 03 产出里三个文件名**写得明明白白**，
    但 `_record_coding_claims` 只解析围栏内的产出区，于是 claims 为 null、
    O5 记 `unavailable`（A0 的 2.9.9 第 3 条）。

    模板区现在是 agent 的正式落点（上一轮放行 + prompt 明确要求回填），
    继续只读围栏区等于让它按要求做事、然后judge它没做。
    """
    body = (
        "# 03-Coding\n\n"
        "## Task\n`qqqq`: Hello World FastAPI Service\n\n"
        "## Red-Green\n"
        "- **Verify cmd**: `python3 -m pytest test_main.py -v`\n\n"
        "## Files Touched\n"
        "- main.py\n- test_main.py\n- requirements.txt\n\n"
        "## 🤖 AI Output\n"
        "<!-- sw:ai-output:start deadbeef -->\n"
        "编码阶段已完成。创建了 FastAPI Hello World 服务。\n"
        "<!-- sw:ai-output:end deadbeef -->\n"
    )

    claims = fact_pack.extract_claims_from_stage_file(body)

    assert claims["files_touched"] == ["main.py", "test_main.py",
                                       "requirements.txt"], claims
    assert claims["verify_cmd"] == "python3 -m pytest test_main.py -v", claims
    assert claims["task_ids"] == ["qqqq"], claims


def test_output_region_claims_win_over_template():
    """围栏区有声明时以它为准 —— 模板区只是回落。

    围栏区是 harness 自己落盘的、nonce 不可预测，可信度高于模板区
    （模板区 agent 可任意改写）。顺序不能反：否则 agent 在模板区写一份
    好看的清单、在产出里写另一份，我们会取到前者。
    """
    body = (
        "## Files Touched\n- template_only.py\n\n"
        "## 🤖 AI Output\n"
        "<!-- sw:ai-output:start deadbeef -->\n"
        "## Files Touched\n- real.py\n"
        "<!-- sw:ai-output:end deadbeef -->\n"
    )

    claims = fact_pack.extract_claims_from_stage_file(body)

    assert claims["files_touched"] == ["real.py"], \
        f"模板区的声明盖掉了围栏内的真实产出: {claims}"


def test_placeholder_template_does_not_become_a_claim():
    """模板未回填时不得把占位符当成声明。

    这是「读模板区」这条回落的代价，必须堵住：`- ___` 变成一个叫 `___`
    的文件名，O5 会去 diff 里找它、找不到，然后报「声明了不存在的文件」——
    一个纯粹由我们自己制造的假阳性。
    """
    body = (
        "## Red-Green\n- **Verify cmd**: `___`\n\n"
        "## Files Touched\n"
        "<!-- 逐条列出本次改动的文件路径。 -->\n"
        "- ___\n\n"
        "## 🤖 AI Output\n"
        "<!-- sw:ai-output:start deadbeef -->\n"
        "什么都没干。\n"
        "<!-- sw:ai-output:end deadbeef -->\n"
    )

    claims = fact_pack.extract_claims_from_stage_file(body)

    assert claims["files_touched"] == [], claims
    assert claims["verify_cmd"] == "", claims
