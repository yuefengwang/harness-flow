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
from sw_lib.runnable import RerouteLimitExceeded
from sw_lib.core.service import TaskService, TaskError
from sw_lib.core.utils import now


# ── Helpers ──

def _make_review_md(task_name: str, route: str, evidence_rows: list = None):
    """Write a mock 04-review.md with given Route and optional Evidence rows."""
    task_dir = TASKS / task_name
    # Use lowercase for route to match internal logic
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


@pytest.fixture(autouse=True)
def mock_workflow_chain(monkeypatch):
    """Ensure WorkflowEngine._workflow_chain is a mock."""
    from sw_lib.runnable.base import StageOutput

    class _FakeStage:
        def __init__(self, stage, stage_idx): self.stage = stage; self.stage_idx = stage_idx

    mock_chain = MagicMock()
    mock_chain.invoke = MagicMock(return_value=StageOutput(
        task_name="test", stage="05-archive", raw_agent_output="",
        parsed={}, gate_passed=True, route="05-archive"))
    mock_chain._stage_map = {
        "01-brainstorming": _FakeStage("01-brainstorming", 0),
        "02-planning": _FakeStage("02-planning", 1),
        "03-coding": _FakeStage("03-coding", 2),
        "04-review": _FakeStage("04-review", 3),
        "05-archive": _FakeStage("05-archive", 4),
    }
    mock_chain._stage_order = list(mock_chain._stage_map.keys())
    mock_chain.max_reroute = 3
    monkeypatch.setattr(WorkflowEngine, '_workflow_chain', mock_chain)
    monkeypatch.setattr(WorkflowEngine, 'run_stage', lambda self: None)
    yield mock_chain


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


# ── Tests: WorkflowEngine.advance_stage() delegates to chain ──

class TestWorkflowEngineAdvanceStage:
    def test_advance_delegates_to_chain(self, reroute_task, agent_callbacks):
        """Engine calls chain.invoke() with correct StageInput."""
        from sw_lib.core.bootstrap import bootstrap
        bootstrap()
        _make_review_md(reroute_task, "05-Archive")
        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=True):
            with patch('sw_lib.core.engine._auto_check_gate'):
                result = engine.advance_stage()

        assert result is True

    def test_advance_returns_false_on_hook_failure(self, reroute_task, agent_callbacks):
        """When _validate_post_hooks fails, engine returns False before modifying state."""
        from sw_lib.core.bootstrap import bootstrap
        bootstrap()
        _make_review_md(reroute_task, "05-Archive")
        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=False):
            with patch('sw_lib.core.engine._auto_check_gate'):
                result = engine.advance_stage()

        assert result is False

    def test_advance_returns_false_on_post_hook_failure(self, reroute_task, agent_callbacks):
        """When _validate_post_hooks fails, engine returns False before calling chain."""
        from sw_lib.core.bootstrap import bootstrap
        bootstrap()
        _make_review_md(reroute_task, "05-Archive")
        callbacks = dict(agent_callbacks)
        callbacks["add_log"] = MagicMock()

        engine = WorkflowEngine(reroute_task, "04-review", 3, "", callbacks)
        with patch.object(engine, '_validate_post_hooks', return_value=False):
            with patch('sw_lib.core.engine._auto_check_gate'):
                result = engine.advance_stage()

        assert result is False


# ── Tests: TaskService.advance_stage() delegates to chain ──

class TestTaskServiceAdvanceStage:
    def test_service_delegates_to_chain(self, reroute_task):
        """Service calls chain.invoke() and returns updated state."""
        from sw_lib.core.bootstrap import bootstrap
        bootstrap()
        _make_review_md(reroute_task, "05-Archive")

        # Mock chain to route to archive
        saved = WorkflowEngine._workflow_chain
        mock_chain = MagicMock()
        from sw_lib.runnable.base import StageOutput
        mock_chain.invoke = MagicMock(return_value=StageOutput(
            task_name=reroute_task, stage="05-archive", raw_agent_output="",
            parsed={}, gate_passed=True))
        mock_chain._stage_map = saved._stage_map
        WorkflowEngine._workflow_chain = mock_chain

        try:
            svc = TaskService()
            with patch('sw_lib.core.engine._auto_check_gate'):
                result = svc.advance_stage(reroute_task)

            assert result["stage"] == "05-archive"
        finally:
            WorkflowEngine._workflow_chain = saved

    def test_service_last_stage(self, reroute_task):
        """Last stage → marked Finished."""
        write_state(reroute_task, {
            "id": reroute_task, "stage": "05-archive", "stage_idx": 4,
            "stage_status": "pending", "agent": "cat",
        })

        svc = TaskService()
        result = svc.advance_stage(reroute_task)
        assert result["stage_status"] == "Finished"

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
        assert "`05-archive`" in content, (
            f"模板 Route 字段应被回填为 05-Archive: {content}"
        )

    def test_route_auto_filled_from_ai_output_coding(self, dummy_task):
        """模板 Route=___，AI Output 写返工到 03-Coding → 应自动回填"""
        _make_real_world_review_md(dummy_task, "03-Coding")

        _auto_check_gate(dummy_task, "04-review")

        content = (TASKS / dummy_task / "04-review.md").read_text(encoding="utf-8")
        assert "`___`" not in content
        assert "`03-coding`" in content

    def test_route_already_filled_not_overwritten(self, dummy_task):
        """模板 Route 已填写时，不应被覆盖"""
        task_dir = TASKS / dummy_task
        _make_review_md(dummy_task, "05-Archive")
        # 确保 AI Output 不存在
        content_before = (task_dir / "04-review.md").read_text(encoding="utf-8")
        assert "`05-archive`" in content_before

        _auto_check_gate(dummy_task, "04-review")

        content_after = (task_dir / "04-review.md").read_text(encoding="utf-8")
        assert "`05-archive`" in content_after, "已填写的 Route 不应被修改"

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
        assert _parse_route_from_ai_output(content) == "05-archive"

    def test_arrow_format(self, dummy_task):
        """Route → 03-Coding - arrow format"""
        content = self._make_content("Route → 03-Coding")
        assert _parse_route_from_ai_output(content) == "03-coding"

    def test_reroute_to_format(self, dummy_task):
        """应返工至 02-Planning - reroute directive"""
        content = self._make_content("应返工至 02-Planning")
        assert _parse_route_from_ai_output(content) == "02-planning"

    def test_routing_decision_as_format(self, dummy_task):
        """路由决策为 01-Brainstorming - decision with 为"""
        content = self._make_content("路由决策为 01-Brainstorming")
        assert _parse_route_from_ai_output(content) == "01-brainstorming"

    def test_route_without_backtick(self, dummy_task):
        """Route: 05-Archive - no backticks, half-width colon"""
        content = self._make_content("Route: 05-Archive")
        assert _parse_route_from_ai_output(content) == "05-archive"

    def test_fullwidth_colon(self, dummy_task):
        """路由：05-Archive - full-width colon"""
        content = self._make_content("路由：05-Archive")
        assert _parse_route_from_ai_output(content) == "05-archive"

    def test_route_decision_colon(self, dummy_task):
        """路由决策：03-Coding with colon"""
        content = self._make_content("路由决策：03-Coding")
        assert _parse_route_from_ai_output(content) == "03-coding"

    def test_no_ai_output_returns_none(self, dummy_task):
        """No AI Output section → None"""
        content = "# 04-Review\n\n## Gate\n"
        assert _parse_route_from_ai_output(content) is None


# ── Tests: _auto_check_gate auto-fills Reroute Evidence ──

class TestAutoCheckGateEvidenceAutoFill:
    """验证 _auto_check_gate 在回填 Route 的同时自动回填 Reroute Evidence。"""

    def _make_review_with_gate_table(self, task_name: str, route_in_output: str,
                                      gate_rows: list):
        """创建包含门禁表格的 AI Output 04-review.md，模拟真实场景。"""
        task_dir = TASKS / task_name
        # 模板部分 — Route 和 Evidence 均为占位符
        template = (
            "# 04-Review\n\n"
            "## Review Decision\n"
            "- **Route**: `___` (05-Archive / 03-Coding / 02-Planning / 01-Brainstorming)\n"
            "- **Reason**: ___\n"
            "\n"
            "### Reroute Evidence (仅在返工时填写)\n"
            "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
            "|---|------|---------|---------|-------------|\n"
            "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n"
            "| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n"
            "\n"
            "## Gate\n"
            "- [ ] Full build: `pytest`\n"
        )
        # AI Output — 包含审查结论表格
        ai_lines = [
            "\n## 🤖 AI Output\n\n",
            "### 审查结论\n\n",
            "| 门禁 | 状态 |\n",
            "|------|------|\n",
        ]
        for gate, status in gate_rows:
            ai_lines.append(f"| {gate} | {status} |\n")
        ai_lines.extend([
            "\n",
            f"**建议路由：{route_in_output}（因上述门禁未通过需返工）**\n",
        ])
        content = template + "".join(ai_lines)
        (task_dir / "04-review.md").write_text(content, encoding="utf-8")

    def test_reroute_evidence_auto_filled_from_failed_gates(self, dummy_task):
        """Route 回填 coding + 有两个门禁未通过 → 自动填充 Evidence 第一行"""
        self._make_review_with_gate_table(dummy_task, "03-Coding", [
            ("交付物一致性", "⚠️ README 缺文档"),
            ("文档内容校验", "⚠️ 新 CLI 参数未入 README"),
        ])

        _auto_check_gate(dummy_task, "04-review")

        content = (TASKS / dummy_task / "04-review.md").read_text(encoding="utf-8")
        assert "`03-coding`" in content, "Route 应被回填"

        # Evidence 表中至少有一行不含 ___
        evidence_section = content.split("### Reroute Evidence")[1].split("## Gate")[0]
        data_rows = [l for l in evidence_section.splitlines()
                     if l.strip().startswith("|") and "---" not in l]
        # 跳表头（分隔行已在过滤中移除）
        data_rows = data_rows[1:]
        non_placeholder_rows = [r for r in data_rows if "___" not in r]
        assert len(non_placeholder_rows) >= 1, (
            f"Evidence 表应有至少一行非占位符: {evidence_section}"
        )

    def test_reroute_evidence_not_overwritten_if_already_filled(self, dummy_task):
        """Evidence 表已填写时不被覆盖"""
        task_dir = TASKS / dummy_task
        content = (
            "# 04-Review\n\n"
            "## Review Decision\n"
            "- **Route**: `03-Coding`\n"
            "\n"
            "### Reroute Evidence\n"
            "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
            "|---|------|---------|---------|-------------|\n"
            "| 1 | 已填问题 | high | coding | file.py:10 |\n"
            "\n"
            "## Gate\n"
            "- [ ] Full build\n"
        )
        (task_dir / "04-review.md").write_text(content, encoding="utf-8")

        _auto_check_gate(dummy_task, "04-review")

        result = (task_dir / "04-review.md").read_text(encoding="utf-8")
        assert "已填问题" in result, "已填的 Evidence 不应被覆盖"

    def test_archive_route_does_not_fill_evidence(self, dummy_task):
        """Route=05-Archive 时不应自动填 Evidence"""
        task_dir = TASKS / dummy_task
        content = (
            "# 04-Review\n\n"
            "## Review Decision\n"
            "- **Route**: `___`\n"
            "\n"
            "### Reroute Evidence (仅在返工时填写)\n"
            "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
            "|---|------|---------|---------|-------------|\n"
            "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n"
            "\n"
            "## 🤖 AI Output\n\n"
            "**建议路由：05-Archive（正常归档）**\n"
            "\n"
            "## Gate\n"
            "- [ ] Full build\n"
        )
        (task_dir / "04-review.md").write_text(content, encoding="utf-8")

        _auto_check_gate(dummy_task, "04-review")

        result = (task_dir / "04-review.md").read_text(encoding="utf-8")
        # Evidence 行仍保持占位符（归档不需要返工证据）
        data_rows = result.split("### Reroute Evidence")[1].split("## Gate")[0]
        assert "| 1 | ___" in data_rows or data_rows.strip() == "", "Archive 不应填 evidence"
