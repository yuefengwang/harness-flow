"""
Tests for workflow utilities: parse_route_field, extract_evidence_table,
inject_reroute_context, and auto_check_gate.
"""

import pytest
import re
from sw_lib.core.config import TASKS
from sw_lib.workflow.utils import (
    parse_route_field,
    extract_evidence_table,
    inject_reroute_context,
    _remove_old_reroute_blocks,
    auto_check_gate,
    parse_route_from_ai_output,
    _reset_gate_checkboxes,
)

# ── Helpers ──

def _make_review_md(task_name: str, route: str, evidence_rows: list = None):
    """Write a mock 04-review.md with given Route and optional Evidence rows."""
    task_dir = TASKS / task_name
    route = route.lower()
    lines = [
        "# 04-Review",
        "",
        "## Review Decision",
        f"- **Route**: `{route}`",
        "- **Reason**: Test",
        "",
    ]
    if evidence_rows:
        lines.append("### Reroute Evidence")
        lines.append("| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |")
        lines.append("|---|------|---------|---------|-------------|")
        for row in evidence_rows:
            lines.append(f"| {row} |")
        lines.append("")

    lines.extend([
        "## Gate",
        "- [ ] Full build: xxx",
    ])
    content = "\n".join(lines)
    (task_dir / "04-review.md").write_text(content, encoding="utf-8")


def _make_coding_md(task_name: str):
    """Write a basic 03-coding.md."""
    task_dir = TASKS / task_name
    content = "# 03-Coding\n\n## Gate\n- [ ] Code builds\n"
    (task_dir / "03-coding.md").write_text(content, encoding="utf-8")


# ── Tests ──

class TestParseRouteField:
    def test_parse_valid_route_archive(self, dummy_task):
        _make_review_md(dummy_task, "05-archive")
        assert parse_route_field(dummy_task) == "05-archive"

    def test_parse_valid_route_coding(self, dummy_task):
        _make_review_md(dummy_task, "03-coding")
        assert parse_route_field(dummy_task) == "03-coding"

    def test_parse_no_route_field(self, dummy_task):
        task_dir = TASKS / dummy_task
        (task_dir / "04-review.md").write_text("# 04-Review\n\nNo route here\n", encoding="utf-8")
        assert parse_route_field(dummy_task) is None

    def test_parse_empty_route(self, dummy_task):
        _make_review_md(dummy_task, "___")
        assert parse_route_field(dummy_task) is None

    def test_parse_invalid_route(self, dummy_task):
        _make_review_md(dummy_task, "Invalid-Route")
        assert parse_route_field(dummy_task) is None

class TestExtractEvidenceTable:
    def test_extract_with_data(self, dummy_task):
        _make_review_md(dummy_task, "03-coding", evidence_rows=[
            "1 | 缺少输入校验 | high | coding | src/api/users.py:42",
            "2 | 错误处理不完善 | medium | coding | src/api/orders.py:15",
        ])
        result = extract_evidence_table(dummy_task)
        assert result is not None
        assert "缺少输入校验" in result
        assert "错误处理不完善" in result
        assert "| # | 问题 |" in result

    def test_extract_all_placeholder_rows(self, dummy_task):
        _make_review_md(dummy_task, "03-coding", evidence_rows=[
            "1 | ___ | high | coding | ___",
        ])
        assert extract_evidence_table(dummy_task) is None

class TestInjectRerouteContext:
    def test_injection_logic(self, dummy_task):
        _make_coding_md(dummy_task)
        _make_review_md(dummy_task, "03-coding", evidence_rows=[
            "1 | Fix this | high | coding | file.py",
        ])
        
        inject_reroute_context(dummy_task, "03-coding")
        
        content = (TASKS / dummy_task / "03-coding.md").read_text(encoding="utf-8")
        assert "返工上下文" in content
        assert "Fix this" in content
        assert "## Gate" in content

class TestParseRouteFromAiOutput:
    def test_backtick_format(self):
        content = "## 🤖 AI Output\n`05-Archive`"
        assert parse_route_from_ai_output(content) == "05-archive"

    def test_natural_language(self):
        content = "## 🤖 AI Output\n建议路由：03-Coding"
        assert parse_route_from_ai_output(content) == "03-coding"

class TestRemoveOldRerouteBlocks:
    def test_remove_blocks(self):
        content = (
            "> 🔄 **返工上下文（来自 04-Review）**\n"
            "| Evidence |\n"
            "> 请优先修复上述问题。\n\n"
            "Original content"
        )
        cleaned = _remove_old_reroute_blocks(content)
        assert cleaned.strip() == "Original content"

class TestResetGateCheckboxes:
    def test_reset_logic(self, dummy_task):
        task_dir = TASKS / dummy_task
        path = task_dir / "test.md"
        path.write_text("## Gate\n- [x] Done\n", encoding="utf-8")
        _reset_gate_checkboxes(dummy_task, "test")
        assert "- [ ] Done" in path.read_text(encoding="utf-8")

class TestAutoCheckGate:
    def test_auto_check_basic(self, dummy_task):
        _make_coding_md(dummy_task)
        auto_check_gate(dummy_task, "03-coding")
        content = (TASKS / dummy_task / "03-coding.md").read_text(encoding="utf-8")
        assert "- [x] Code builds" in content

    def test_auto_backfill_route(self, dummy_task):
        task_dir = TASKS / dummy_task
        content = (
            "# 04-Review\n\n"
            "## Review Decision\n"
            "- **Route**: `___`\n\n"
            "## 🤖 AI Output\n"
            "建议路由：05-Archive\n"
            "## Gate\n"
            "- [ ] Check 1\n"
        )
        (task_dir / "04-review.md").write_text(content, encoding="utf-8")
        
        auto_check_gate(dummy_task, "04-review")
        
        new_content = (task_dir / "04-review.md").read_text(encoding="utf-8")
        assert "- **Route**: `05-archive`" in new_content
        assert "- [x] Check 1" in new_content

class TestAutoCheckGateEvidenceAutoFill:
    def test_autofill(self, dummy_task):
        content = (
            "### Reroute Evidence\n"
            "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
            "|---|------|---------|---------|-------------|\n"
            "| 1 | ___ | high | coding | ___ |\n\n"
            "## 🤖 AI Output\n"
            "| Gate | 状态 | 说明 |\n"
            "|---|---|---|\n"
            "| Lint | ❌ | Fails\n"
        )
        from sw_lib.workflow.utils import _auto_fill_evidence_from_ai_output
        new_content = _auto_fill_evidence_from_ai_output(content)
        assert "Lint" in new_content
        assert "审查发现: ❌" in new_content

class TestStageComplianceReviewRoute:
    """Detailed validation for 04-review Route field in check_stage_compliance."""

    def _make_04_review_md(self, task_name, route_value="___"):
        task_dir = TASKS / task_name
        content = (
            "# 04-Review\n\n"
            "## Review Decision\n"
            f"- **Route**: `{route_value}`\n"
            "- **Reason**: Test\n\n"
            "## Gate\n"
            "- [x] All checks passed\n"
        )
        (task_dir / "04-review.md").write_text(content, encoding="utf-8")

    def test_route_empty(self, dummy_task):
        self._make_04_review_md(dummy_task, "___")
        from sw_lib.workflow.utils import check_stage_compliance
        done, todo = check_stage_compliance(dummy_task, "04-review", 3)
        assert any("未填写" in item for item in todo)

    def test_route_valid(self, dummy_task):
        for val in ["05-archive", "03-coding", "02-planning", "01-brainstorming"]:
            self._make_04_review_md(dummy_task, val)
            from sw_lib.workflow.utils import check_stage_compliance
            done, todo = check_stage_compliance(dummy_task, "04-review", 3)
            assert not todo
            assert any(val in item.lower() for item in done)

    def test_route_invalid(self, dummy_task):
        self._make_04_review_md(dummy_task, "Invalid-Route")
        from sw_lib.workflow.utils import check_stage_compliance
        done, todo = check_stage_compliance(dummy_task, "04-review", 3)
        assert any("无效" in item or "缺少有效的" in item for item in todo)

    def test_no_route_field(self, dummy_task):
        task_dir = TASKS / dummy_task
        (task_dir / "04-review.md").write_text(
            "# 04-Review\n\n## Gate\n- [x] OK\n", encoding="utf-8"
        )
        from sw_lib.workflow.utils import check_stage_compliance
        done, todo = check_stage_compliance(dummy_task, "04-review", 3)
        assert any("缺少" in item for item in todo)
