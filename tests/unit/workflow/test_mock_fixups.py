"""Mock 模式的模板回填：只补 agent 本该写进模板正文的内容。

这个模块历史上叫「gate fixups」，做的是全局 `[ ]` -> `[x]` 加 Route 占位符
归一化 —— 因为当时门禁凭据就存在 Markdown 的复选框里。现在 Gate 签署与 Route
决策都存 `.state`（见 docs/design-json-state-source.md），改文件不再有任何
效力，那部分逻辑随之退场。

剩下的唯一职责是填 01 的 `- **Chosen**: ___`：那是**内容检查**（用户拍板的
方案有没有被记录进产出），真实 agent 会自己写，MockAgent 不会，所以在 mock
模式下补上，让全流程能跑通。
"""
import pytest
from sw_lib.workflow.mock_fixups import apply_mock_template_fixups


@pytest.fixture
def mock_on(monkeypatch):
    monkeypatch.setattr("sw_lib.workflow.mock_fixups.is_mock_agent", lambda: True)


@pytest.fixture
def mock_off(monkeypatch):
    monkeypatch.setattr("sw_lib.workflow.mock_fixups.is_mock_agent", lambda: False)


_CHOICES = ("## Clarifying Questions (3)\n"
            "1. **Topic**: ___\n"
            "   - [ ] A: ___ — Pros/Cons\n"
            "   - [ ] B: ___ — Pros/Cons\n"
            "   - **Chosen**: ___\n")


class TestChoiceGroupFixups:
    def test_chosen_placeholder_filled_when_mock(self, mock_on):
        out = apply_mock_template_fixups(_CHOICES, "01-brainstorming")
        assert "- **Chosen**: ___" not in out, "占位符未填写"
        assert "- **Chosen**: A" in out

    def test_untouched_when_not_mock(self, mock_off):
        assert apply_mock_template_fixups(_CHOICES, "01-brainstorming") == _CHOICES

    def test_other_stages_untouched(self, mock_on):
        content = "# Planning\n- [ ] todo\n"
        assert apply_mock_template_fixups(content, "02-planning") == content


class TestGateAndRouteAreNotTouched:
    """Gate 与 Route 现在存 .state，mock 也不许改文件里的这些东西。"""

    def test_gate_checkboxes_not_filled(self, mock_on):
        content = "# Planning\n\n## Gate\n- [ ] Tests pass\n"
        out = apply_mock_template_fixups(content, "02-planning")
        assert "- [ ] Tests pass" in out, "改 Markdown 的 Gate 是无效动作，不该发生"

    def test_route_placeholder_not_normalized(self, mock_on):
        content = "# Review\n- **Route**: `___`\n"
        out = apply_mock_template_fixups(content, "04-review")
        assert "- **Route**: `___`" in out, "Route 决策只能写 .state"

    def test_ai_output_region_untouched(self, mock_on):
        content = ("## 🤖 AI Output\n"
                   "<!-- sw:ai-output:start abcd1234 -->\n"
                   "1. [ ] 定义数据模型\n"
                   "- **Chosen**: ___\n"
                   "<!-- sw:ai-output:end abcd1234 -->\n")
        out = apply_mock_template_fixups(content, "01-brainstorming")
        assert out == content, "产出区内的文本是 agent 原话，不得改写"
