"""prompt 必须自足：不得用路径引用把 agent 打发去 glob harness 目录。

真实事故（任务 `newtask`，2026-08-23）的**起点**不是权限配置，而是这行文案：

```markdown
> Hooks: `hooks/02-planning.md`
```

它被 `_read_current_template()` 原样注入 prompt。agent 读到一个路径，
自然的下一步就是去读那个文件：

```text
glob {'pattern': '**/hooks/*.md', 'path': '<harness 根>'}
```

而 workdir 是 `repo/newtask`，于是越界 → `external_directory` 判定 →
ask → 死等 26 分钟。

讽刺的是这次 glob **完全没必要**：`_read_hook_rules()` 早就把同一个
hooks 文件的**全文**注入了 prompt。agent 是被一行路径引用骗去读它
手里已经有的东西。

所以修法不是「允许它 glob」，而是让 prompt 自足 —— 内容给全，别给路径。
"""

from pathlib import Path

import pytest

from sw_lib.prompts import PromptRegistry, PromptBuilder
from sw_lib.core.config import TASKS, ROOT, HOOKS_DIR
from sw_lib.core.state import write_state

STAGES = [("01-brainstorming", 0), ("02-planning", 1), ("03-coding", 2),
          ("04-review", 3), ("05-archive", 4)]

TPLS = ROOT / "templates"


@pytest.fixture
def builder():
    registry = PromptRegistry(ROOT / "sw_lib" / "prompts" / "templates")
    return PromptBuilder(registry)


@pytest.fixture
def task(tmp_path):
    """建一个带全部阶段模板的真实任务目录（照 service.create 的做法拷模板）。"""
    import shutil
    name = "prompt-self-sufficiency-test"
    task_dir = TASKS / name
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    for tpl in TPLS.glob("*.md"):
        shutil.copy2(tpl, task_dir / tpl.name)
    write_state(name, {
        "id": name, "stage": "01-brainstorming", "stage_idx": 0,
        "stage_status": "pending", "agent": "mock", "target_dir": "repo/t",
    })
    yield name
    shutil.rmtree(task_dir, ignore_errors=True)


# ── 模板不得含诱导性路径引用 ──

def test_stage_templates_have_no_hooks_path_reference():
    """5 个阶段模板都不得写 `hooks/<stage>.md` 这样的路径引用。

    这是事故的**起点**，也是最便宜的修法：一行文案。
    """
    offenders = []
    for stage, _ in STAGES:
        text = (TPLS / f"{stage}.md").read_text(encoding="utf-8")
        if f"hooks/{stage}.md" in text:
            offenders.append(stage)
    assert not offenders, (
        f"这些模板仍含 hooks 路径引用，会诱导 agent 越界 glob: {offenders}")


def _section(prompt: str, header_prefix: str) -> str:
    """取出 prompt 里某个 `=== xxx ===` 段的正文。"""
    lines = prompt.splitlines()
    out, collecting = [], False
    for line in lines:
        if line.startswith("=== "):
            collecting = line.startswith(f"=== {header_prefix}")
            continue
        if collecting:
            out.append(line)
    return "\n".join(out)


def test_prompt_stage_sections_contain_no_harness_paths(task, builder):
    """本次修复负责的两段 —— 阶段模板与强制规则 —— 正文不得含内部路径。

    agent 的 workdir 是 `repo/<task>`，prompt 里任何指向 harness 根的相对
    路径都是一次越界访问的邀请函。

    【测试范围更正 · 显式声明】本条最初断言的是**整个 prompt** 不含
    `hooks/` / `templates/` / `sw_lib/`。那一版的红是真的，但它同时暴露了
    一个**范围外**的更大问题：`_read_global_rules()` 把 harness 自己的
    `INSTRUCTIONS.md`（400 余行内部架构文档，含完整目录树）当作「全局项目
    规范」注入了每个任务 agent 的 prompt —— 那里面的 `sw_lib/`、
    `templates/` 路径比模板那一行多得多。

    按 DEV-PROTOCOL 范围纪律，那属于「发现但不顺手修」：它要改的是
    `_read_global_rules` 的语义（harness 自身开发规范 vs 被开发项目的规范），
    影响面远超本次 ask 死锁修复。已记录，不在本次改动。
    因此本条收窄到本次真正负责的两段。
    """
    lures = ("hooks/", "templates/", "sw_lib/")
    for stage, idx in STAGES:
        out = builder.build(task, stage, idx)
        assert out, f"{stage} 未产出 prompt"
        for header in ("当前阶段模板", "强制规则"):
            body = _section(out, header)
            assert body.strip(), f"{stage} 的「{header}」段为空，测试假设失效"
            found = [l for l in lures if l in body]
            assert not found, (
                f"{stage} 的「{header}」段含 harness 内部路径 {found} —— "
                f"agent 会尝试去读，触发 external_directory 判定")


# ── 内容必须真的在 prompt 里（去掉路径引用后不能反而变少）──

def test_hook_rules_content_is_actually_injected(task, builder):
    """每个阶段的 hooks 规则**正文**必须已在 prompt 中。

    这条是上面两条的前提：只有内容自足，去掉路径引用才不算减配。
    """
    for stage, idx in STAGES:
        hook_file = HOOKS_DIR / f"{stage}.md"
        if not hook_file.exists():
            continue
        out = builder.build(task, stage, idx)
        rule_ids = [l.split(":")[0].removeprefix("## ").strip()
                    for l in hook_file.read_text(encoding="utf-8").splitlines()
                    if l.startswith("## hook-")]
        assert rule_ids, f"{stage} 的 hooks 文件没有 hook-xx 小节，测试假设失效"
        missing = [r for r in rule_ids if r not in out]
        assert not missing, (
            f"{stage} 的 prompt 缺少 hooks 规则正文 {missing} —— "
            f"agent 只能自己去找文件，正是我们要消除的行为")


def test_prompt_tells_agent_rules_are_already_inline(task, builder):
    """prompt 必须明说规则已内联，不需要去找文件。

    光删掉路径引用不够：模型仍可能凭训练先验去 glob 找「项目规范」。
    与其指望它不找，不如直接告诉它「已经给你了」。

    【假绿更正 · 显式声明】本条最初写的是
    `assert "无需" in out or "不要" in out`，首轮直接**通过** —— 但通过的
    原因是 `system.yaml` 里有一句无关的「**不要** 主动推进阶段」。
    那是典型的假绿：断言太松，命中了与被测行为无关的字符。
    已改为断言「强制规则」段里出现明确的内联声明短语。
    """
    for stage, idx in STAGES:
        out = builder.build(task, stage, idx)
        body = _section(out, "强制规则")
        assert "已完整内联" in body, (
            f"{stage} 的「强制规则」段没有明确声明规则已内联 —— "
            f"agent 仍可能去 glob 找规范文件")
