"""Tests for stage output schemas — Pydantic validation."""
import pytest
from pydantic import ValidationError

from sw_lib.output.schema import AgentResult
from sw_lib.output.stages import (
    BrainstormingOutput, DecisionPoint,
    PlanningOutput, TaskItem,
    CodingOutput,
    ReviewOutput, ReviewFinding,
    ArchiveOutput,
)


class TestAgentResult:
    def test_minimal_construction(self):
        r = AgentResult(content="hello")
        assert r.content == "hello"
        assert r.parsed is None
        assert r.tool_calls == []
        assert not r.is_empty

    def test_is_empty(self):
        assert AgentResult(content="").is_empty
        assert AgentResult(content="  ").is_empty
        assert not AgentResult(content="x").is_empty

    def test_with_parsed_model(self):
        parsed = BrainstormingOutput(task_name="t", ambiguity_score=7, goal="x")
        r = AgentResult(content="raw", parsed=parsed)
        assert r.parsed.ambiguity_score == 7


class TestBrainstormingOutput:
    def test_valid(self):
        o = BrainstormingOutput(task_name="t", ambiguity_score=8, goal="build app")
        assert o.ambiguity_score == 8

    def test_score_bounds(self):
        with pytest.raises(ValidationError):
            BrainstormingOutput(task_name="t", ambiguity_score=15, goal="x")
        with pytest.raises(ValidationError):
            BrainstormingOutput(task_name="t", ambiguity_score=-1, goal="x")

    def test_with_decisions(self):
        o = BrainstormingOutput(task_name="t", ambiguity_score=5, goal="x",
                                 decisions=[DecisionPoint(
                                     topic="i18n", chosen="A. i18n supported",
                                     rationale="Global product")])
        assert o.decisions[0].topic == "i18n"


class TestPlanningOutput:
    def test_with_tasks(self):
        o = PlanningOutput(task_name="t", tasks=[
            TaskItem(id=1, name="setup project", deps=[], verify_cmd="npm test")
        ])
        assert o.tasks[0].name == "setup project"


class TestReviewOutput:
    def test_default_route(self):
        o = ReviewOutput(task_name="t")
        assert o.route == "05-Archive"

    def test_reroute_to_coding(self):
        o = ReviewOutput(task_name="t", passed=False, route="03-Coding",
                          reason="Tests fail")
        assert o.route == "03-Coding"
        assert not o.passed

    def test_with_findings(self):
        o = ReviewOutput(task_name="t", passed=False, route="03-Coding",
                          findings=[ReviewFinding(
                              id=1, severity="high", category="security",
                              location="auth.py", description="No input validation")])
        assert o.findings[0].severity == "high"

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            ReviewFinding(id=1, severity="critical", category="x",
                           location="x", description="x")


class TestCodingOutput:
    def test_defaults(self):
        o = CodingOutput(task_name="t")
        assert o.build_passes is False
        assert o.test_passes is False

    def test_with_files(self):
        o = CodingOutput(task_name="t", build_passes=True, test_passes=True,
                          files_changed=["src/app.py"])
        assert o.build_passes
        assert o.files_changed == ["src/app.py"]


class TestArchiveOutput:
    def test_defaults(self):
        o = ArchiveOutput(task_name="t")
        assert o.summary == ""
        assert o.patterns_to_promote == []
