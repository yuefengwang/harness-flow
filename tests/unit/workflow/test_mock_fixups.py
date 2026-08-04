"""Phase 2 red tests: Mock gate fixups extracted from StageRunnable into a
pure, independently-testable function.

The mock-mode post-processing (`[ ]` -> `[x]` outside the AI Output region,
and review Route placeholder normalization) must live in
`sw_lib.workflow.mock_fixups.apply_mock_gate_fixups`, NOT inside the production
StageRunnable class.

Run with: pytest tests/unit/workflow/test_mock_fixups.py -v
"""
import pytest
from sw_lib.workflow.mock_fixups import apply_mock_gate_fixups


@pytest.fixture
def mock_on(monkeypatch):
    monkeypatch.setattr("sw_lib.workflow.mock_fixups.is_mock_agent", lambda: True)


@pytest.fixture
def mock_off(monkeypatch):
    monkeypatch.setattr("sw_lib.workflow.mock_fixups.is_mock_agent", lambda: False)


_AI_REGION = "\n## 🤖 AI Output\n[draft task] 1. [ ] 定义数据模型\n## Gate"


class TestMockFixupsPlanning:
    def test_gate_checkboxes_filled_when_mock(self, mock_on):
        content = "# Planning\n- [ ] todo\n" + _AI_REGION + "\n- [ ] gate_item\n"
        out = apply_mock_gate_fixups(content, "02-planning")
        assert "- [x] todo" in out
        assert "- [x] gate_item" in out

    def test_ai_output_region_untouched_when_mock(self, mock_on):
        content = "# Planning\n- [ ] todo\n" + _AI_REGION + "\n- [ ] gate_item\n"
        out = apply_mock_gate_fixups(content, "02-planning")
        # the [ ] inside the AI Output region must NOT be filled
        assert "[draft task] 1. [ ] 定义数据模型" in out

    def test_checkboxes_untouched_when_not_mock(self, mock_off):
        content = "# Planning\n- [ ] todo\n" + _AI_REGION + "\n- [ ] gate_item\n"
        assert apply_mock_gate_fixups(content, "02-planning") == content


class TestMockFixupsReview:
    def test_route_placeholder_normalized_when_mock(self, mock_on):
        content = "# Review\n- **Route**: `___`\n" + _AI_REGION + "\n"
        out = apply_mock_gate_fixups(content, "04-review")
        assert "- **Route**: `05-Archive`" in out

    def test_route_placeholder_untouched_when_not_mock(self, mock_off):
        content = "# Review\n- **Route**: `___`\n" + _AI_REGION + "\n"
        assert apply_mock_gate_fixups(content, "04-review") == content


class TestMockFixupsNonMockMode:
    def test_no_mutation_when_off(self, mock_off):
        content = "# X\n- [ ] a\n- **Route**: `___`\n" + _AI_REGION + "\n"
        assert apply_mock_gate_fixups(content, "02-planning") == content
