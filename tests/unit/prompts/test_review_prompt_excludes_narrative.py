"""A3 的验收 17 在 prompt 层的落点：04 阶段不读 `03-coding.md`。

这是 C1（上下文污染）的直接判据。改造前 `_read_previous_stage` 对全部阶段
一律读上一阶段的 md —— 04 因此读到「我已完成全部实现，测试都过了」这类
自述，复核的对象成了叙述而不是事实。

其余四个阶段照旧读上一阶段：它们之间传递的是设计意图，不是待核验的声明。
"""

import pytest
from pathlib import Path

from sw_lib.core.state import write_state
from sw_lib.prompts import PromptBuilder, PromptRegistry


SENTINEL = "SENTINEL-4f7ab2-i-finished-everything-tests-all-green"


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    monkeypatch.setattr("sw_lib.core.config.TASKS", tasks_root, raising=False)
    monkeypatch.setattr("sw_lib.core.state.TASKS", tasks_root, raising=False)
    monkeypatch.setattr("sw_lib.prompts.builder.TASKS", tasks_root, raising=False)

    task = "a3prompt"
    task_dir = tasks_root / task
    task_dir.mkdir()
    write_state(task, {"id": task, "stage": "04-review", "stage_idx": 3,
                       "stage_status": "running", "target_dir": str(tmp_path / "repo")})
    (task_dir / "03-coding.md").write_text(
        "# 03-Coding\n\n## Implementation Notes\n"
        f"- {SENTINEL}\n", encoding="utf-8")
    (task_dir / "02-planning.md").write_text(
        "# 02-Planning\n\n## Task DAG\n- [x] **Task 1**: `建索引`\n", encoding="utf-8")
    return task, task_dir


def _builder():
    templates = (Path(__file__).resolve().parents[3]
                 / "sw_lib" / "prompts" / "templates")
    return PromptBuilder(PromptRegistry(templates))


def test_review_prompt_excludes_coding_narrative(task_env):
    task, _ = task_env

    prompt = _builder().build(task, "04-review", 3) or ""

    assert SENTINEL not in prompt, \
        "04 的 prompt 含 03-coding.md 的自述 —— C1 未被消除"


def test_review_prompt_still_has_substance(task_env):
    """排除自述不等于交白卷：04 仍须拿到可审的输入。

    只断言「哨兵不在」会被一个返回 None 的实现骗过 —— 那样 04 什么都看不到。
    """
    task, _ = task_env

    prompt = _builder().build(task, "04-review", 3) or ""

    assert len(prompt) > 200
    assert "04-review" in prompt


def test_other_stages_still_read_previous_output(task_env):
    """单调性：03 仍应读到 02 的规划产出。"""
    task, _ = task_env

    prompt = _builder().build(task, "03-coding", 2) or ""

    assert "建索引" in prompt


def test_review_prompt_prefers_facts_when_present(task_env):
    """事实包存在时须被注入 —— 否则 04 拿不到替代自述的那份输入。"""
    task, task_dir = task_env
    facts = task_dir / "facts"
    facts.mkdir()
    (facts / "diff.stat").write_text(" src/a.py | 3 +++\n", encoding="utf-8")
    (facts / "tests.json").write_text('{"passed": 2, "parse_status": "parsed"}',
                                      encoding="utf-8")
    (facts / "spec.md").write_text("# 需求与决策事实\n（用户未提供上下文）\n",
                                   encoding="utf-8")

    prompt = _builder().build(task, "04-review", 3) or ""

    assert "src/a.py" in prompt
    assert "需求与决策事实" in prompt
    assert SENTINEL not in prompt
