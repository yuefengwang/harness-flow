"""Tests for GateValidator — wraps existing hook scripts."""
from unittest.mock import MagicMock, patch

from sw_lib.workflow.gate import GateValidator
from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss


def _sign(task_name, stage, stage_idx=0, route=None):
    """建 .state 并签署 Gate（必要时写 Route）。

    门禁判定读 .state（docs/design-json-state-source.md），阶段文件里的
    `- [x]` 不再有效力。期望「门禁通过」的用例必须显式签署。
    """
    write_state(task_name, {"id": task_name, "stage": stage,
                            "stage_idx": stage_idx, "stage_status": "running"})
    ss.sign_gate(task_name, stage)
    if route:
        ss.write_route(task_name, route)


class TestGateValidatorCheck:
    def test_passes_when_checkboxes_all_filled(self):
        """Gate passes when all checkboxes in Gate section are [x]."""
        task_name = "gate-test-pass"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [x] Ready for Planning\n",
            encoding="utf-8",
        )

        _sign(task_name, "01-brainstorming", 0)
        try:
            validator = GateValidator()
            result = validator.check(task_name, "01-brainstorming")
            assert result is True
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_fails_when_checkbox_not_filled(self):
        """Gate fails when a checkbox is [ ] (not filled)."""
        task_name = "gate-test-fail"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [ ] Ready for Planning\n",
            encoding="utf-8",
        )

        try:
            validator = GateValidator()
            result = validator.check(task_name, "01-brainstorming")
            assert result is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_fails_when_template_missing(self):
        """Gate fails when the stage template file doesn't exist."""
        validator = GateValidator()
        result = validator.check("nonexistent-task", "01-brainstorming")
        assert result is False

    def test_fails_when_no_gate_section(self):
        """Gate fails when template has no ## Gate section."""
        task_name = "gate-test-no-section"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\nJust some content.\n",
            encoding="utf-8",
        )

        try:
            validator = GateValidator()
            result = validator.check(task_name, "01-brainstorming")
            assert result is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_empty_gate_section_fails(self):
        """Gate fails when ## Gate section has no checkboxes at all."""
        task_name = "gate-test-empty"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Gate\n"
            "Nothing here.\n",
            encoding="utf-8",
        )

        try:
            validator = GateValidator()
            result = validator.check(task_name, "01-brainstorming")
            assert result is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_review_stage_checks_route_filled(self):
        """For review stage, Route must also be filled."""
        task_name = "gate-test-review"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "04-review.md").write_text(
            "# 04-Review\n\n"
            "## Review Decision\n"
            "- **Route**: `___` (05-Archive / 03-Coding ...)\n"
            "\n"
            "## Gate\n"
            "- [x] Full build passes\n",
            encoding="utf-8",
        )

        try:
            validator = GateValidator()
            # Route is ___ -> should fail
            result = validator.check(task_name, "04-review")
            assert result is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_review_stage_passes_with_route_filled(self):
        """Review stage passes when route is filled and gate checkboxes are [x]."""
        task_name = "gate-test-review-ok"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "04-review.md").write_text(
            "# 04-Review\n\n"
            "## Review Decision\n"
            "- **Route**: `05-Archive` (05-Archive / 03-Coding ...)\n"
            "\n"
            "## Gate\n"
            "- [x] Full build passes\n",
            encoding="utf-8",
        )

        _sign(task_name, "04-review", 3, route="05-Archive")
        try:
            validator = GateValidator()
            result = validator.check(task_name, "04-review")
            assert result is True
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_hook_script_runs_on_check(self):
        """GateValidator optionally runs the hook shell script."""
        task_name = "gate-test-hook"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "03-coding.md").write_text(
            "# 03-Coding\n\n"
            "## Gate\n"
            "- [x] Code builds & tests pass\n",
            encoding="utf-8",
        )

        _sign(task_name, "03-coding", 2)
        try:
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                validator = GateValidator(run_hook_script=True)
                result = validator.check(task_name, "03-coding")
                assert result is True
                mock_run.assert_called_once()
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_hook_script_failure_blocks_gate(self):
        """If hook script returns non-zero, gate fails even if checkboxes are filled."""
        task_name = "gate-test-hook-fail"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "03-coding.md").write_text(
            "# 03-Coding\n\n"
            "## Gate\n"
            "- [x] Code builds & tests pass\n",
            encoding="utf-8",
        )

        try:
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr="BUILD FAILED")
                validator = GateValidator(run_hook_script=True)
                result = validator.check(task_name, "03-coding")
                assert result is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_fails_when_choice_options_all_unchecked(self):
        """01-brainstorming where ALL choice options in a group are [ ] should NOT pass.
        
        Choice group = A/B options under a clarifying question.
        Current bug: all [ ] in a group are ignored, allowing advance with empty template.
        """
        task_name = "gate-test-choice-all-unchecked"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Clarifying Questions\n"
            "1. **Topic**: ___\n"
            "   - [ ] A: Option A — Pros\n"
            "   - [ ] B: Option B — Pros\n"
            "   - **Chosen**: ___\n"
            "\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [x] Ready for Planning\n",
            encoding="utf-8",
        )

        try:
            validator = GateValidator()
            result = validator.check(task_name, "01-brainstorming")
            # Should FAIL — no choice has been selected in the group
            assert result is False, "Gate must fail when choice group has no [x]"
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_passes_when_one_choice_in_group_checked(self):
        """Choice group passes when at least one option is [x]."""
        task_name = "gate-test-choice-one-checked"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Clarifying Questions\n"
            "1. **Topic**: ___\n"
            "   - [x] A: Option A — Pros\n"
            "   - [ ] B: Option B — Pros\n"
            "   - **Chosen**: ___\n"
            "\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [x] Ready for Planning\n",
            encoding="utf-8",
        )

        _sign(task_name, "01-brainstorming", 0)
        try:
            validator = GateValidator()
            result = validator.check(task_name, "01-brainstorming")
            assert result is True
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_fails_when_multiple_choice_groups_all_unchecked(self):
        """Multiple choice groups, all unchecked — should fail."""
        task_name = "gate-test-multi-choice-all-unchecked"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Clarifying Questions\n"
            "1. **Topic**: ___\n"
            "   - [ ] A: Opt1A — Pros\n"
            "   - [ ] B: Opt1B — Pros\n"
            "   - **Chosen**: ___\n"
            "2. **Topic**: ___\n"
            "   - [ ] A: Opt2A — Pros\n"
            "   - [ ] B: Opt2B — Pros\n"
            "   - **Chosen**: ___\n"
            "\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [x] Ready for Planning\n",
            encoding="utf-8",
        )

        try:
            validator = GateValidator()
            result = validator.check(task_name, "01-brainstorming")
            assert result is False
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)

    def test_hook_script_not_run_by_default(self):
        """By default, hook scripts are NOT run (gate is template-only)."""
        task_name = "gate-test-no-hook"
        task_dir = TASKS / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n## Gate\n- [x] Done\n",
            encoding="utf-8",
        )

        _sign(task_name, "01-brainstorming", 0)
        try:
            with patch('subprocess.run') as mock_run:
                validator = GateValidator()  # default: run_hook_script=False
                result = validator.check(task_name, "01-brainstorming")
                assert result is True
                mock_run.assert_not_called()
        finally:
            import shutil
            shutil.rmtree(task_dir, ignore_errors=True)
