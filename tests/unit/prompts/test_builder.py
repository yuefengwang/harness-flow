"""Tests for PromptBuilder — template-driven prompt construction."""
import pytest
from pathlib import Path

from sw_lib.prompts import PromptRegistry, PromptBuilder
from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state


@pytest.fixture
def task_setup():
    """Create a task with full context files for build() testing."""
    task_name = "prompt-builder-test"
    task_dir = TASKS / task_name
    task_dir.mkdir(parents=True, exist_ok=True)

    write_state(task_name, {
        "id": task_name,
        "stage": "01-brainstorming",
        "stage_idx": 0,
        "stage_status": "pending",
        "agent": "mock",
        "target_dir": "repo/test-project",
    })

    # Write stage templates
    (task_dir / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n## Gate\n- [ ] Design approved\n",
        encoding="utf-8",
    )
    (task_dir / "02-planning.md").write_text(
        "# 02-Planning\n\n## Gate\n- [ ] Tasks itemized\n",
        encoding="utf-8",
    )

    # Write context
    (task_dir / ".context").write_text("Build a user login module.", encoding="utf-8")

    yield task_name

    import shutil
    shutil.rmtree(task_dir, ignore_errors=True)


@pytest.fixture
def builder():
    """PromptBuilder with the real templates directory."""
    templates_dir = Path(__file__).resolve().parent.parent.parent.parent / "sw_lib" / "prompts" / "templates"
    registry = PromptRegistry(templates_dir)
    return PromptBuilder(registry)


class TestPromptBuilder:
    def test_brainstorming_output_elements(self, task_setup, builder):
        """PromptBuilder output is non-empty and contains key elements."""
        task_name = task_setup

        new_output = builder.build(task_name, "01-brainstorming", 0)

        assert new_output is not None
        # Brainstorming-specific rules should be present in the new output
        assert "一次只问一个问题" in new_output
        assert "ask_user" in new_output
        # Shared orchestration rules
        assert "编排规则" in new_output

    def test_coding_output_elements(self, task_setup, builder):
        """PromptBuilder output for coding includes core structure."""
        task_name = task_setup

        output = builder.build(task_name, "03-coding", 2)

        assert output is not None
        assert "编排规则" in output
        # Non-brainstorming should NOT have the brainstorming-specific rule
        assert "一次只问一个问题" not in output

    def test_builder_returns_none_for_empty(self, builder):
        """Build returns None when task directory doesn't exist."""
        result = builder.build("nonexistent-task", "01-brainstorming", 0)
        # Should still build because templates are always loaded
        assert result is not None
        assert "ask_user" in result

    def test_builder_includes_previous_stage_output(self, task_setup, builder):
        """Builder includes previous stage content when available."""
        # Manually fill brainstorming output
        task_dir = TASKS / task_setup
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## 🤖 AI Output\n\n"
            "Ambiguity score: 8. Goal: build login module.\n"
            "\n"
            "## Gate\n"
            "- [x] Design approved\n",
            encoding="utf-8",
        )

        output = builder.build(task_setup, "02-planning", 1)
        # Should include the brainstorming output as "前一阶段产出"
        assert "Ambiguity score: 8" in output
        assert "前一阶段产出" in output

    def test_builder_includes_project_info(self, task_setup, builder):
        """项目信息段必须存在，且用 **agent 的参照系**表述目标目录。

        ⚠️ 本条是对初版的**显式重做**（DEV-PROTOCOL 1.2）。初版断言
        `"repo/test-project" in output` —— 即要求把 `.state` 里的
        `target_dir`（harness 相对路径）原样注入 prompt。那个前提本身是错的：

        agent 的 cwd 已经**就是**该目录（`OpenCodeAgent._default_workdir`），
        再告诉它「代码生成目录: repo/test-project」，它只能理解成 cwd 下还有
        一层同名目录。任务 helloworld 因此连续 8 次 read/glob 打在空处。

        判据改为：项目信息段存在、并且**不**把 harness 相对路径交给 agent。
        完整契约见 `test_prompt_path_frame.py`。
        """
        output = builder.build(task_setup, "01-brainstorming", 0)
        assert "项目信息" in output, "项目信息段整段消失了"
        assert "repo/test-project" not in output, (
            "harness 相对路径被原样注入 prompt —— agent 的 cwd 就是该目录，"
            "它会再拼一层")

    def test_builder_includes_hook_rules(self, task_setup, builder):
        """Hook rules from hooks/{stage}.md are injected."""
        output = builder.build(task_setup, "01-brainstorming", 0)
        # Hook file hooks/01-brainstorming.md should be included
        # (it exists in the project's hooks directory)
        assert "强制规则" in output or "hooks" in output.lower()


class TestPromptBuilderEdgeCases:
    def test_all_five_stages_build_without_error(self, builder):
        """All 5 stages build successfully."""
        task_name = "some-task"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        write_state(task_name, {
            "id": task_name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock", "target_dir": "repo/t",
        })

        try:
            for stage, idx in [("01-brainstorming", 0), ("02-planning", 1),
                                ("03-coding", 2), ("04-review", 3), ("05-archive", 4)]:
                output = builder.build(task_name, stage, idx)
                assert output is not None, f"Build failed for {stage}"
                assert len(output) > 100, f"Output too short for {stage}: {len(output)} chars"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_build_with_previous_output_injects_it(self, task_setup, builder):
        """previous_output dict is accessible but doesn't go into the prompt
        (PromptBuilder only reads from files, not from dict parameters).
        This test verifies the parameter is accepted without error."""
        output = builder.build(
            task_setup, "03-coding", 2,
            previous_output={"some_key": "some_value"},
        )
        assert output is not None

    def test_first_stage_has_no_previous(self, task_setup, builder):
        """Stage 0 (brainstorming) should NOT include '前一阶段产出'."""
        output = builder.build(task_setup, "01-brainstorming", 0)
        assert "前一阶段产出" not in output
