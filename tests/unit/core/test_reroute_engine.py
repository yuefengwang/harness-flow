"""
Tests for review reroute engine: parse_route_field, extract_evidence_table,
inject_reroute_context, WorkflowEngine.advance_stage() routing,
and TaskService.advance_stage() routing.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from sw_lib.core.config import TASKS, STAGES, HOOKS_DIR, ROOT
from sw_lib.core.state import read_state, write_state
from sw_lib.core.engine import (
    parse_route_field,
    extract_evidence_table,
    inject_reroute_context,
    _remove_old_reroute_blocks,
    _auto_check_gate,
    _parse_route_from_ai_output,
    _reset_gate_checkboxes,
    MAX_REROUTE,
    WorkflowEngine,
)
from sw_lib.core.service import TaskService, TaskError
from sw_lib.core.utils import now


# ── Helpers ──

def _make_review_md(task_name: str, route: str, evidence_rows: list = None):
    """Write a mock 04-review.md with given Route and optional Evidence rows."""
    task_dir = TASKS / task_name
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


@pytest.fixture
def reroute_task(dummy_task):
    """Extend dummy_task with review + coding templates for reroute tests."""
    task_dir = TASKS / dummy_task
    _make_coding_md(dummy_task)
    # Set stage to 04-review for advance_stage tests
    write_state(dummy_task, {
        "id": dummy_task,
        "stage": "04-review",
        "stage_idx": 3,
        "stage_status": "pending",
        "agent": "cat",
    })
    yield dummy_task
    # cleanup is handled by dummy_task fixture


# ── Tests: parse_route_field ──

class TestParseRouteField:
    def test_parse_valid_route_archive(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "05-Archive")
        assert parse_route_field(dummy_task) == "05-archive"

    def test_parse_valid_route_coding(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "03-Coding")
        assert parse_route_field(dummy_task) == "03-coding"

    def test_parse_valid_route_planning(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "02-Planning")
        assert parse_route_field(dummy_task) == "02-planning"

    def test_parse_valid_route_brainstorming(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "01-Brainstorming")
        assert parse_route_field(dummy_task) == "01-brainstorming"

    def test_parse_no_route_field(self, dummy_task):
        task_dir = TASKS / dummy_task
        (task_dir / "04-review.md").write_text("# 04-Review\n\nNo route here\n", encoding="utf-8")
        assert parse_route_field(dummy_task) is None

    def test_parse_empty_route(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "___")
        assert parse_route_field(dummy_task) is None

    def test_parse_invalid_route(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "Invalid-Route")
        assert parse_route_field(dummy_task) is None

    def test_parse_no_review_file(self, dummy_task):
        # No 04-review.md exists
        assert parse_route_field(dummy_task) is None

    def test_parse_backtick_variations(self, dummy_task):
        """Route value may have different quote styles."""
        task_dir = TASKS / dummy_task
        # Use standard backtick style
        content = "# 04-Review\n\n## Review Decision\n- **Route**: `03-Coding`\n"
        (task_dir / "04-review.md").write_text(content, encoding="utf-8")
        assert parse_route_field(dummy_task) == "03-coding"


# ── Tests: extract_evidence_table ──

class TestExtractEvidenceTable:
    def test_extract_with_data(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "03-Coding", evidence_rows=[
            "1 | 缺少输入校验 | high | coding | src/api/users.py:42",
            "2 | 错误处理不完善 | medium | coding | src/api/orders.py:15",
        ])
        result = extract_evidence_table(dummy_task)
        assert result is not None
        assert "缺少输入校验" in result
        assert "错误处理不完善" in result
        assert "| # | 问题 |" in result  # header

    def test_extract_no_evidence_section(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "05-Archive")  # no evidence table
        assert extract_evidence_table(dummy_task) is None

    def test_extract_all_placeholder_rows(self, dummy_task):
        """Rows with all ___ are treated as empty."""
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "03-Coding", evidence_rows=[
            "1 | ___ | high | coding | ___",
            "2 | ___ | medium | coding | ___",
        ])
        assert extract_evidence_table(dummy_task) is None

    def test_extract_mixed_rows(self, dummy_task):
        """Mix of real and placeholder — should still extract."""
        task_dir = TASKS / dummy_task
        _make_review_md(task_dir, "03-Coding", evidence_rows=[
            "1 | 真实问题 | high | coding | file.py:10",
            "2 | ___ | medium | coding | ___",
        ])
        result = extract_evidence_table(dummy_task)
        assert result is not None
        assert "真实问题" in result

    def test_extract_no_review_file(self, dummy_task):
        assert extract_evidence_table(dummy_task) is None


# ── Tests: _remove_old_reroute_blocks ──

class TestRemoveOldRerouteBlocks:
    def test_remove_single_block(self):
        content = (
            "> 🔄 **返工上下文（来自 04-Review）**\n"
            "> | 1 | issue | high | coding | file.py |\n"
            "> 请优先修复上述问题。\n"
            "\n"
            "# 03-Coding\n"
            "\n"
            "## Gate\n"
        )
        result = _remove_old_reroute_blocks(content)
        assert "返工上下文" not in result
        assert result.strip().startswith("# 03-Coding")

    def test_remove_no_block(self):
        content = "# 03-Coding\n\n## Gate\n"
        # _remove_old_reroute_blocks strips trailing newline
        assert _remove_old_reroute_blocks(content) == "# 03-Coding\n\n## Gate"

    def test_remove_multiple_blocks(self):
        content = (
            "> 🔄 **返工上下文（来自 04-Review）**\n"
            "> | 1 | first | high | coding | a.py |\n"
            "> \n"
            "> 🔄 **返工上下文（来自 04-Review）**\n"
            "> | 1 | second | high | coding | b.py |\n"
            "> 请优先修复上述问题。\n"
            "\n"
            "# 03-Coding\n"
        )
        result = _remove_old_reroute_blocks(content)
        assert "返工上下文" not in result
        assert "first" not in result
        assert "second" not in result
        assert result.strip().startswith("# 03-Coding")


# ── Tests: inject_reroute_context ──

class TestInjectRerouteContext:
    def test_inject_basic(self, dummy_task):
        task_dir = TASKS / dummy_task
        _make_review_md(dummy_task, "03-Coding", evidence_rows=[
            "1 | 测试问题 | high | coding | file.py:1",
        ])
        _make_coding_md(dummy_task)

        inject_reroute_context(dummy_task, "03-coding")

        target = task_dir / "03-coding.md"
        content = target.read_text(encoding="utf-8")
        assert "返工上下文（来自 04-Review）" in content
        assert "测试问题" in content

    def test_inject_idempotent(self, dummy_task):
        """Multiple injections should not create duplicate blocks."""
        task_dir = TASKS / dummy_task
        _make_review_md(dummy_task, "03-Coding", evidence_rows=[
            "1 | 问题 | high | coding | f.py",
        ])
        _make_coding_md(dummy_task)

        inject_reroute_context(dummy_task, "03-coding")
        inject_reroute_context(dummy_task, "03-coding")
        inject_reroute_context(dummy_task, "03-coding")

        target = task_dir / "03-coding.md"
        content = target.read_text(encoding="utf-8")
        # Should only have one inject block
        assert content.count("返工上下文（来自 04-Review）") == 1

    def test_inject_no_evidence(self, dummy_task):
        """No evidence table → no injection."""
        task_dir = TASKS / dummy_task
        _make_review_md(dummy_task, "05-Archive")  # no evidence
        _make_coding_md(dummy_task)

        inject_reroute_context(dummy_task, "03-coding")

        target = task_dir / "03-coding.md"
        content = target.read_text(encoding="utf-8")
        assert "返工上下文" not in content

    def test_inject_no_target_file(self, dummy_task):
        """If target stage .md doesn't exist, skip gracefully."""
        task_dir = TASKS / dummy_task
        _make_review_md(dummy_task, "03-Coding", evidence_rows=[
            "1 | 问题 | high | coding | f.py",
        ])
        # Don't create 03-coding.md
        inject_reroute_context(dummy_task, "03-coding")  # should not raise


# ── Tests: _reset_gate_checkboxes ──

class TestResetGateCheckboxes:
    def test_reset_gate_section(self, dummy_task):
        """[x] in Gate section should become [ ]."""
        task_dir = TASKS / dummy_task
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [x] All questions answered\n"
            "\n"
            "## 🤖 AI Output\n"
            "- [x] Some output checkbox\n",
            encoding="utf-8",
        )

        _reset_gate_checkboxes(dummy_task, "01-brainstorming")

        content = (task_dir / "01-brainstorming.md").read_text(encoding="utf-8")
        gate_section = content.split("## Gate")[1].split("##")[0] if "## Gate" in content else ""
        assert "[x]" not in gate_section, f"Gate section still has [x]: {gate_section}"
        # AI Output section should retain its [x]
        assert "[x] Some output checkbox" in content, "AI Output [x] should not be reset"

    def test_reset_no_gate_section(self, dummy_task):
        """No ## Gate section → no crash."""
        task_dir = TASKS / dummy_task
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\nSome content\n", encoding="utf-8"
        )
        _reset_gate_checkboxes(dummy_task, "01-brainstorming")  # should not raise

    def test_reset_no_file(self, dummy_task):
        """No file exists → no crash."""
        task_dir = TASKS / dummy_task
        _reset_gate_checkboxes(dummy_task, "01-brainstorming")  # should not raise

    def test_reset_non_brainstorming_untouched(self, dummy_task):
        """Non-brainstorming stage md should not be created or modified."""
        task_dir = TASKS / dummy_task
        _reset_gate_checkboxes(dummy_task, "03-coding")  # no file, should not crash


# ── Tests: WorkflowEngine.advance_stage() routing ──

class TestWorkflowEngineAdvanceStage:
    def test_advance_normal_non_review(self, reroute_task, agent_callbacks):
        """Non-review stage still does linear +1 advance."""
        # Create engine at 03-coding (idx=2)
        write_state(reroute_task, {
            "id": reroute_task,
            "stage": "03-coding",
            "stage_idx": 2,
            "stage_status": "running",
            "agent": "cat",
        })
        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()
        callbacks["on_settlement"] = MagicMock()

        # Need to mock _validate_post_hooks to return True
        engine = WorkflowEngine(reroute_task, "03-coding", 2, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch.object(engine, '_create_agent'):
                with patch.object(engine, 'run_stage'):
                    result = engine.advance_stage()

        assert result is True
        st = read_state(reroute_task)
        assert st["stage"] == "04-review"
        assert st["stage_idx"] == 3

    def test_advance_review_route_to_archive(self, reroute_task, agent_callbacks):
        """04-review + Route=05-Archive → advance to 05-archive."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "05-Archive")

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()
        callbacks["on_settlement"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch.object(engine, '_create_agent'):
                with patch.object(engine, 'run_stage'):
                    result = engine.advance_stage()

        assert result is True
        st = read_state(reroute_task)
        assert st["stage"] == "05-archive"
        assert st["stage_idx"] == 4

    def test_advance_review_route_to_coding(self, reroute_task, agent_callbacks):
        """04-review + Route=03-Coding → reroute to 03-coding with context injection."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "03-Coding", evidence_rows=[
            "1 | 输入校验缺失 | high | coding | src/api/users.py:42",
        ])
        _make_coding_md(reroute_task)

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()
        callbacks["on_settlement"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch.object(engine, '_create_agent'):
                with patch.object(engine, 'run_stage'):
                    result = engine.advance_stage()

        assert result is True
        st = read_state(reroute_task)
        assert st["stage"] == "03-coding"
        assert st["stage_idx"] == 2
        assert st["reroute_count"] == 1
        assert len(st["reroute_history"]) == 1
        assert st["reroute_history"][0]["from"] == "04-review"
        assert st["reroute_history"][0]["to"] == "03-coding"

        # Verify context injection
        coding_content = (task_dir / "03-coding.md").read_text(encoding="utf-8")
        assert "返工上下文（来自 04-Review）" in coding_content
        assert "输入校验缺失" in coding_content

    def test_advance_review_route_to_planning(self, reroute_task, agent_callbacks):
        """04-review + Route=02-Planning → reroute to 02-planning."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "02-Planning", evidence_rows=[
            "1 | 架构设计不合理 | high | planning | docs/arch.md",
        ])
        # Create 02-planning.md
        (task_dir / "02-planning.md").write_text("# 02-Planning\n\n## Gate\n", encoding="utf-8")

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch.object(engine, '_create_agent'):
                with patch.object(engine, 'run_stage'):
                    result = engine.advance_stage()

        assert result is True
        st = read_state(reroute_task)
        assert st["stage"] == "02-planning"
        assert st["stage_idx"] == 1

    def test_advance_review_route_to_brainstorming(self, reroute_task, agent_callbacks):
        """04-review + Route=01-Brainstorming → reroute to 01-brainstorming."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "01-Brainstorming", evidence_rows=[
            "1 | 需求不明确 | high | brainstorming | 需求文档",
        ])
        (task_dir / "01-brainstorming.md").write_text("# 01-Brainstorming\n\n## Gate\n", encoding="utf-8")

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch.object(engine, '_create_agent'):
                with patch.object(engine, 'run_stage'):
                    result = engine.advance_stage()

        assert result is True
        st = read_state(reroute_task)
        assert st["stage"] == "01-brainstorming"
        assert st["stage_idx"] == 0

    def test_advance_reroute_to_brainstorming_resets_gate(self, reroute_task, agent_callbacks):
        """Reroute to brainstorming should reset [x]→[ ] in Gate section."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "01-Brainstorming", evidence_rows=[
            "1 | 需求不明确 | high | brainstorming | 需求文档",
        ])
        (task_dir / "01-brainstorming.md").write_text(
            "# 01-Brainstorming\n\n"
            "## Gate\n"
            "- [x] Design approved\n"
            "- [x] All questions answered\n"
            "\n"
            "## 🤖 AI Output\n"
            "- [x] Some output checkbox\n",
            encoding="utf-8",
        )

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()
        callbacks["on_settlement"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch.object(engine, '_create_agent'):
                with patch.object(engine, 'run_stage'):
                    result = engine.advance_stage()

        assert result is True
        content = (task_dir / "01-brainstorming.md").read_text(encoding="utf-8")
        gate_section = content.split("## Gate")[1].split("##")[0]
        assert "[x]" not in gate_section, f"Gate section still has [x]: {gate_section}"
        assert "[x] Some output checkbox" in content, "AI Output [x] should not be reset"

    def test_advance_review_invalid_route(self, reroute_task, agent_callbacks):
        """Invalid route → advance returns False, state unchanged."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "Invalid-Route")

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            result = engine.advance_stage()

        assert result is False
        st = read_state(reroute_task)
        assert st["stage"] == "04-review"  # unchanged

    def test_advance_review_empty_route(self, reroute_task, agent_callbacks):
        """Empty route (___) → advance returns False, gate blocks."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "___")

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            result = engine.advance_stage()

        assert result is False
        st = read_state(reroute_task)
        assert st["stage"] == "04-review"  # unchanged — gate blocked

    def test_advance_reroute_exceeds_max(self, reroute_task, agent_callbacks):
        """reroute_count > MAX_REROUTE → blocked."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "03-Coding", evidence_rows=[
            "1 | 问题 | high | coding | f.py",
        ])
        _make_coding_md(reroute_task)

        # Pre-set reroute_count above threshold
        write_state(reroute_task, {
            "id": reroute_task,
            "stage": "04-review",
            "stage_idx": 3,
            "stage_status": "pending",
            "agent": "cat",
            "reroute_count": MAX_REROUTE + 1,  # already exceeded
        })

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            result = engine.advance_stage()

        assert result is False
        st = read_state(reroute_task)
        assert st["stage_status"] == "blocked"

    def test_advance_reroute_count_tracking(self, reroute_task, agent_callbacks):
        """Multiple reroutes increment reroute_count."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "03-Coding", evidence_rows=[
            "1 | 问题 | high | coding | f.py",
        ])
        _make_coding_md(reroute_task)

        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()
        callbacks["on_settlement"] = MagicMock()

        # First reroute
        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch.object(engine, '_create_agent'):
                with patch.object(engine, 'run_stage'):
                    engine.advance_stage()

        st = read_state(reroute_task)
        assert st["reroute_count"] == 1

        # Simulate second pass: change stage back to 04-review and advance again
        write_state(reroute_task, {
            "id": reroute_task,
            "stage": "04-review",
            "stage_idx": 3,
            "stage_status": "pending",
            "agent": "cat",
            "reroute_count": st["reroute_count"],
            "reroute_history": st["reroute_history"],
        })

        engine2 = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine2, '_validate_post_hooks', return_value=True):
            with patch.object(engine2, '_create_agent'):
                with patch.object(engine2, 'run_stage'):
                    engine2.advance_stage()

        st2 = read_state(reroute_task)
        assert st2["reroute_count"] == 2
        assert len(st2["reroute_history"]) == 2


# ── Tests: TaskService.advance_stage() routing ──

class TestTaskServiceAdvanceStage:
    def test_service_review_route_to_archive(self, reroute_task):
        """TaskService: 04-review + Route=05-Archive → advance to 05-archive."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "05-Archive")

        svc = TaskService()
        with patch('sw_lib.core.engine._auto_check_gate'):
            result = svc.advance_stage(reroute_task)

        assert result["stage"] == "05-archive"
        assert result["stage_idx"] == 4

    def test_service_review_route_to_coding(self, reroute_task):
        """TaskService: 04-review + Route=03-Coding → reroute to 03-coding."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "03-Coding", evidence_rows=[
            "1 | 测试 | high | coding | f.py",
        ])
        _make_coding_md(reroute_task)

        svc = TaskService()
        with patch('sw_lib.core.engine._auto_check_gate'):
            result = svc.advance_stage(reroute_task)

        assert result["stage"] == "03-coding"
        assert result["stage_idx"] == 2
        assert result["reroute_count"] == 1
        assert len(result["reroute_history"]) == 1

    def test_service_review_invalid_route(self, reroute_task):
        """TaskService: invalid route → TaskError."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "Invalid")

        svc = TaskService()
        with patch('sw_lib.core.engine._auto_check_gate'):
            with pytest.raises(TaskError, match="Route 字段无效"):
                svc.advance_stage(reroute_task)

    def test_service_review_exceeds_max_reroute(self, reroute_task):
        """TaskService: reroute_count > MAX_REROUTE → TaskError + blocked."""
        task_dir = TASKS / reroute_task
        _make_review_md(reroute_task, "03-Coding", evidence_rows=[
            "1 | 问题 | high | coding | f.py",
        ])
        _make_coding_md(reroute_task)

        # Pre-set reroute_count above threshold
        write_state(reroute_task, {
            "id": reroute_task,
            "stage": "04-review",
            "stage_idx": 3,
            "stage_status": "pending",
            "agent": "cat",
            "reroute_count": MAX_REROUTE + 1,
        })

        svc = TaskService()
        with patch('sw_lib.core.engine._auto_check_gate'):
            with pytest.raises(TaskError, match=f"返工已超过 {MAX_REROUTE} 次"):
                svc.advance_stage(reroute_task)

        st = read_state(reroute_task)
        assert st["stage_status"] == "blocked"

    def test_service_non_review_linear(self, reroute_task):
        """TaskService: non-review stage → linear +1 advance."""
        write_state(reroute_task, {
            "id": reroute_task,
            "stage": "03-coding",
            "stage_idx": 2,
            "stage_status": "running",
            "agent": "cat",
        })

        svc = TaskService()
        with patch('sw_lib.core.engine._auto_check_gate'):
            result = svc.advance_stage(reroute_task)

        assert result["stage"] == "04-review"
        assert result["stage_idx"] == 3

    def test_service_last_stage(self, reroute_task):
        """TaskService: last stage → marked Finished."""
        write_state(reroute_task, {
            "id": reroute_task,
            "stage": "05-archive",
            "stage_idx": 4,
            "stage_status": "pending",
            "agent": "cat",
        })

        svc = TaskService()
        result = svc.advance_stage(reroute_task)
        assert result["stage_status"] == "Finished"


# ── Helper: real-world 04-review.md with unfilled Route ──

def _make_real_world_review_md(task_name: str, route_in_ai_output: str):
    """模拟真实场景的 04-review.md：模板 Route 为 ___，AI Output 中写了路由决策。"""
    task_dir = TASKS / task_name
    # 模板部分 — Route 未填写
    template = (
        "# 04-Review\n"
        "\n"
        "> Hooks: `hooks/04-review.md`\n"
        "\n"
        "## Zero-Memory Review\n"
        "Is the diff self-explanatory? Yes\n"
        "\n"
        "## Impact Analysis\n"
        "- **Side effects**: None\n"
        "- **Regression tests**: All pass\n"
        "\n"
        "## Security\n"
        "- [x] No hardcoded secrets\n"
        "- [x] Input validated\n"
        "- [x] Access control OK\n"
        "\n"
        "## Review Decision\n"
        "- **Route**: `___` (05-Archive / 03-Coding / 02-Planning / 01-Brainstorming)\n"
        "- **Reason**: ___\n"
        "\n"
        "### Reroute Evidence (仅在返工时填写)\n"
        "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
        "|---|------|---------|---------|-------------|\n"
        "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n"
        "\n"
        "## Gate\n"
        "- [ ] Full build: `pytest`\n"
        "- [ ] Lint/static analysis pass\n"
        "- [ ] Doc/config in sync\n"
        "\n"
    )
    # AI Output 模拟 — agent 在输出中写了 Route 决策
    ai_output = (
        "\n## 🤖 AI Output\n"
        "\n"
        "## 审查结论\n"
        "\n"
        f"**门禁状态：全部通过** 🟢\n"
        f"**建议路由：{route_in_ai_output}（正常归档）**\n"
        "\n"
        "- ✅ 构建通过\n"
        "- ✅ 测试绿色\n"
        "- ✅ 安全合规\n"
        "\n"
        "## Gate\n"
        "- [x] Full build: `pytest`\n"
        "- [x] Lint/static analysis pass\n"
        "- [x] Doc/config in sync\n"
    )
    content = template + ai_output
    (task_dir / "04-review.md").write_text(content, encoding="utf-8")


# ── Tests: _auto_check_gate auto-fills Route ──

class TestAutoCheckGateRouteAutoFill:
    """验证 _auto_check_gate 能自动从 AI Output 解析 Route 决决策并回填模板字段。"""

    def test_route_auto_filled_from_ai_output_archive(self, dummy_task):
        """模板 Route=___，AI Output 写 '建议路由：05-Archive' → 应自动回填"""
        _make_real_world_review_md(dummy_task, "05-Archive")

        _auto_check_gate(dummy_task, "04-review")

        content = (TASKS / dummy_task / "04-review.md").read_text(encoding="utf-8")
        assert "`___`" not in content, (
            f"模板 Route 字段应被自动填写，但仍然为 ___: {content}"
        )
        assert "`05-Archive`" in content, (
            f"模板 Route 字段应被回填为 05-Archive: {content}"
        )

    def test_route_auto_filled_from_ai_output_coding(self, dummy_task):
        """模板 Route=___，AI Output 写返工到 03-Coding → 应自动回填"""
        _make_real_world_review_md(dummy_task, "03-Coding")

        _auto_check_gate(dummy_task, "04-review")

        content = (TASKS / dummy_task / "04-review.md").read_text(encoding="utf-8")
        assert "`___`" not in content
        assert "`03-Coding`" in content

    def test_route_already_filled_not_overwritten(self, dummy_task):
        """模板 Route 已填写时，不应被覆盖"""
        task_dir = TASKS / dummy_task
        _make_review_md(dummy_task, "05-Archive")
        # 确保 AI Output 不存在
        content_before = (task_dir / "04-review.md").read_text(encoding="utf-8")
        assert "`05-Archive`" in content_before

        _auto_check_gate(dummy_task, "04-review")

        content_after = (task_dir / "04-review.md").read_text(encoding="utf-8")
        assert "`05-Archive`" in content_after, "已填写的 Route 不应被修改"

    def test_no_ai_output_section_not_modified(self, dummy_task):
        """无 AI Output section 时 Route ___ 保持不动，由门禁拦截"""
        task_dir = TASKS / dummy_task
        content = (
            "# 04-Review\n"
            "## Review Decision\n"
            "- **Route**: `___` (05-Archive / 03-Coding / 02-Planning / 01-Brainstorming)\n"
            "- **Reason**: test\n"
            "## Gate\n"
            "- [ ] Full build: xxx\n"
        )
        (task_dir / "04-review.md").write_text(content, encoding="utf-8")

        _auto_check_gate(dummy_task, "04-review")

        result = (task_dir / "04-review.md").read_text(encoding="utf-8")
        assert "`___`" in result, "无 AI Output 时，Route 应保持 ___ 等待用户选择"


# ── Tests: _parse_route_from_ai_output expanded patterns ──

class TestParseRouteFromAiOutput:
    """Verify _parse_route_from_ai_output handles various natural-language formats."""

    def _make_content(self, route_text: str) -> str:
        return (
            "# 04-Review\n"
            "## Review Decision\n"
            "- **Route**: `___`\n"
            "\n"
            "## 🤖 AI Output\n"
            f"{route_text}\n"
            "\n"
            "## Gate\n"
            "- [ ] Full build\n"
        )

    def test_backtick_format(self, dummy_task):
        """`05-Archive` - existing backtick format"""
        content = self._make_content("门禁通过，路由：`05-Archive`")
        assert _parse_route_from_ai_output(content) == "05-Archive"

    def test_arrow_format(self, dummy_task):
        """Route → 03-Coding - arrow format"""
        content = self._make_content("Route → 03-Coding")
        assert _parse_route_from_ai_output(content) == "03-Coding"

    def test_reroute_to_format(self, dummy_task):
        """应返工至 02-Planning - reroute directive"""
        content = self._make_content("应返工至 02-Planning")
        assert _parse_route_from_ai_output(content) == "02-Planning"

    def test_routing_decision_as_format(self, dummy_task):
        """路由决策为 01-Brainstorming - decision with 为"""
        content = self._make_content("路由决策为 01-Brainstorming")
        assert _parse_route_from_ai_output(content) == "01-Brainstorming"

    def test_route_without_backtick(self, dummy_task):
        """Route: 05-Archive - no backticks, half-width colon"""
        content = self._make_content("Route: 05-Archive")
        assert _parse_route_from_ai_output(content) == "05-Archive"

    def test_fullwidth_colon(self, dummy_task):
        """路由：05-Archive - full-width colon"""
        content = self._make_content("路由：05-Archive")
        assert _parse_route_from_ai_output(content) == "05-Archive"

    def test_route_decision_colon(self, dummy_task):
        """路由决策：03-Coding with colon"""
        content = self._make_content("路由决策：03-Coding")
        assert _parse_route_from_ai_output(content) == "03-Coding"

    def test_no_ai_output_returns_none(self, dummy_task):
        """No AI Output section → None"""
        content = "# 04-Review\n\n## Gate\n"
        assert _parse_route_from_ai_output(content) is None
