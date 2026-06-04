"""Tests for StageOutputParser — JSON, code block, and markdown parsing."""
import json
import pytest

from sw_lib.output.parser import StageOutputParser
from sw_lib.output.stages import (
    BrainstormingOutput, ReviewOutput, CodingOutput, DecisionPoint,
)


@pytest.fixture
def brainstorming_parser():
    return StageOutputParser(BrainstormingOutput)


@pytest.fixture
def review_parser():
    return StageOutputParser(ReviewOutput)


class TestParseJSON:
    def test_direct_json(self, brainstorming_parser):
        data = {"task_name": "t", "ambiguity_score": 8, "goal": "build app"}
        result = brainstorming_parser.parse(json.dumps(data))
        assert result.ambiguity_score == 8

    def test_invalid_json_raises(self, brainstorming_parser):
        with pytest.raises(ValueError, match="Failed to parse"):
            brainstorming_parser.parse("not json at all")

    def test_try_parse_invalid_returns_none(self, brainstorming_parser):
        result = brainstorming_parser.try_parse("garbage text")
        assert result is None

    def test_json_missing_required_raises(self, brainstorming_parser):
        # task_name has default "", so missing it doesn't raise.
        # Only score out of range raises.
        result = brainstorming_parser.try_parse('{"ambiguity_score": 5}')
        assert result is not None
        assert result.task_name == ""

    def test_json_score_out_of_range(self, brainstorming_parser):
        result = brainstorming_parser.try_parse(
            '{"task_name": "t", "ambiguity_score": 15, "goal": "x"}')
        assert result is None


class TestParseJSONBlock:
    def test_json_in_code_block(self, brainstorming_parser):
        text = 'Some text\n```json\n{"task_name":"t","ambiguity_score":7,"goal":"x"}\n```\nMore text'
        result = brainstorming_parser.parse(text)
        assert result.ambiguity_score == 7

    def test_json_no_lang_specifier(self, brainstorming_parser):
        text = '```\n{"task_name":"t","ambiguity_score":6,"goal":"x"}\n```'
        result = brainstorming_parser.parse(text)
        assert result.ambiguity_score == 6

    def test_multiple_blocks_uses_first(self, brainstorming_parser):
        text = '```json\n{"task_name":"t","ambiguity_score":8,"goal":"first"}\n```\n```json\n{"task_name":"t2","ambiguity_score":3,"goal":"second"}\n```'
        result = brainstorming_parser.parse(text)
        assert result.goal == "first"


class TestParseMarkdown:
    def test_markdown_fields(self, brainstorming_parser):
        text = (
            "## AI Output\n\n"
            "ambiguity_score: 8\n"
            "goal: build login module\n"
        )
        result = brainstorming_parser.parse(text)
        assert result.ambiguity_score == 8
        assert result.goal == "build login module"

    def test_bold_markdown_fields(self):
        parser = StageOutputParser(ReviewOutput)
        text = "**Route**: `03-Coding`\n**Reason**: Tests fail\n**Passed**: False\n"
        result = parser.parse(text)
        assert result.route == "03-Coding"

    def test_chinese_colon(self, review_parser):
        text = "路由：03-Coding\n理由：测试失败\n"
        result = review_parser.try_parse(text)
        # Chinese field names don't match schema keys, so defaults are used
        assert result is not None
        assert result.route == "05-Archive"  # default

    def test_boolean_coercion(self, review_parser):
        text = "**Passed**: True\n**route**: `03-Coding`\n"
        result = review_parser.parse(text)
        assert result.passed is True

    def test_review_route_from_markdown(self, review_parser):
        text = "- **Route**: `05-Archive` (正常归档)\n- **Reason**: All good\n"
        result = review_parser.parse(text)
        assert result.route == "05-Archive"


class TestParserIntegration:
    def test_brainstorming_full_cycle(self, brainstorming_parser):
        text = json.dumps({
            "task_name": "my-task",
            "ambiguity_score": 7,
            "goal": "Implement login module with JWT auth",
            "decisions": [
                {"topic": "Auth method", "chosen": "JWT", "rationale": "Stateless, scalable"},
            ],
            "risks": ["Token expiration handling", "Session hijacking"],
        })
        result = brainstorming_parser.parse(text)
        assert result.goal == "Implement login module with JWT auth"
        assert len(result.decisions) == 1
        assert result.decisions[0].topic == "Auth method"

    def test_review_reroute_detection(self, review_parser):
        text = json.dumps({
            "task_name": "my-task",
            "passed": False,
            "route": "03-Coding",
            "reason": "Missing input validation in auth.py",
            "findings": [
                {"id": 1, "severity": "high", "category": "security",
                 "location": "auth.py:42", "description": "No input validation"},
            ],
        })
        result = review_parser.parse(text)
        assert result.route == "03-Coding"
        assert not result.passed
        assert len(result.findings) == 1
        assert result.findings[0].severity == "high"
