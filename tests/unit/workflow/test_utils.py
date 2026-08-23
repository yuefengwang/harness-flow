"""workflow utils 的内容解析工具：证据表提取、返工上下文注入、路由意图识别。

注意这些函数**都不是**门禁判定的一部分 —— 判定读 `.state`（见 stage_state）。
这里解析的是 agent 产出的内容：证据表是给人看的记录，路由意图识别只在自动
模式下用来推测 agent 的结论，最终仍要写进 `.state` 才算决定。
"""

from sw_lib.core.config import TASKS
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.utils import (
    extract_evidence_table,
    inject_reroute_context,
    _remove_old_reroute_blocks,
    parse_route_from_ai_output,
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

class TestStageComplianceReviewRoute:
    """04-review 的 Route 判定 —— 现在读 .state，不解析 Markdown。

    Route 是路由决策（机器要据此选下一个阶段），属于状态而非产出，因此搬进
    JSON。agent 在 04-review.md 里写 `- **Route**: xxx` 不再有任何效力，
    这消除了「模板区与 AI Output 正文两个 Route 值冲突」那类歧义。
    """

    def _prepare(self, task_name):
        """写一个 Gate 已签署的 04-review，只留 Route 待验。"""
        (TASKS / task_name / "04-review.md").write_text(
            "# 04-Review\n\n## Review Decision\n- **Reason**: Test\n",
            encoding="utf-8")
        ss.sign_gate(task_name, "04-review")

    def test_route_unset_blocks(self, dummy_task):
        self._prepare(dummy_task)
        from sw_lib.workflow.utils import check_stage_compliance
        _, todo = check_stage_compliance(dummy_task, "04-review", 3)
        assert any("Route" in item and "尚未填写" in item for item in todo), todo

    def test_route_valid(self, dummy_task):
        from sw_lib.workflow.utils import check_stage_compliance
        for val in ["05-archive", "03-coding", "02-planning", "01-brainstorming"]:
            self._prepare(dummy_task)
            assert ss.write_route(dummy_task, val) is True
            done, todo = check_stage_compliance(dummy_task, "04-review", 3)
            assert todo == [], f"{val}: {todo}"
            assert any(val in item.lower() for item in done), done

    def test_invalid_route_is_rejected_at_write_time(self, dummy_task):
        """非法值在写入时就被拒，不会进入状态 —— 比事后校验更早拦住。"""
        self._prepare(dummy_task)
        assert ss.write_route(dummy_task, "Invalid-Route") is False
        from sw_lib.workflow.utils import check_stage_compliance
        _, todo = check_stage_compliance(dummy_task, "04-review", 3)
        assert any("Route" in item for item in todo), todo

    def test_markdown_route_has_no_effect(self, dummy_task):
        """agent 在 Markdown 里写 Route 不算决策。"""
        (TASKS / dummy_task / "04-review.md").write_text(
            "# 04-Review\n\n- **Route**: `05-Archive`\n", encoding="utf-8")
        ss.sign_gate(dummy_task, "04-review")
        from sw_lib.workflow.utils import check_stage_compliance
        _, todo = check_stage_compliance(dummy_task, "04-review", 3)
        assert any("Route" in item for item in todo), \
            "Markdown 里的 Route 被误当成决策"

