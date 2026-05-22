"""Tests for StageValidator: 04-review Route field validation."""

from pathlib import Path

from sw_lib.core.config import TASKS
from sw_lib.core.state import StageValidator


def _make_04_review_md(task_name: str, route_value: str = "___"):
    """Helper: create 04-review.md with a given Route value."""
    task_dir = TASKS / task_name
    content = (
        "# 04-Review\n"
        "\n"
        "## Review Decision\n"
        f"- **Route**: `{route_value}`\n"
        "- **Reason**: Test\n"
        "\n"
        "## Gate\n"
        "- [x] All checks passed\n"
    )
    (task_dir / "04-review.md").write_text(content, encoding="utf-8")


class TestStateValidatorReviewRoute:
    """StageValidator.check for 04-review: Route field validation."""

    def test_route_empty(self, dummy_task):
        """Route=___ → todo: '未填写'"""
        _make_04_review_md(dummy_task, "___")
        done, todo = StageValidator.check(TASKS / dummy_task, "04-review", 3)
        assert any("未填写" in item for item in todo), (
            f"expected Route todo, got done={done} todo={todo}"
        )

    def test_route_valid_archive(self, dummy_task):
        """Route=05-Archive → done: route valid"""
        _make_04_review_md(dummy_task, "05-Archive")
        done, todo = StageValidator.check(TASKS / dummy_task, "04-review", 3)
        assert any("05-Archive" in item for item in done), (
            f"expected Route done, got done={done} todo={todo}"
        )

    def test_route_valid_coding(self, dummy_task):
        """Route=03-Coding → done: route valid"""
        _make_04_review_md(dummy_task, "03-Coding")
        done, todo = StageValidator.check(TASKS / dummy_task, "04-review", 3)
        assert any("03-Coding" in item for item in done)

    def test_route_valid_planning(self, dummy_task):
        """Route=02-Planning → done: route valid"""
        _make_04_review_md(dummy_task, "02-Planning")
        done, todo = StageValidator.check(TASKS / dummy_task, "04-review", 3)
        assert any("02-Planning" in item for item in done)

    def test_route_valid_brainstorming(self, dummy_task):
        """Route=01-Brainstorming → done: route valid"""
        _make_04_review_md(dummy_task, "01-Brainstorming")
        done, todo = StageValidator.check(TASKS / dummy_task, "04-review", 3)
        assert any("01-Brainstorming" in item for item in done)

    def test_route_invalid(self, dummy_task):
        """Route=Invalid-Route → todo: '无效'"""
        _make_04_review_md(dummy_task, "Invalid-Route")
        done, todo = StageValidator.check(TASKS / dummy_task, "04-review", 3)
        assert any("无效" in item for item in todo), (
            f"expected Route invalid todo, got done={done} todo={todo}"
        )

    def test_no_route_field(self, dummy_task):
        """No **Route** field at all → todo: '缺少'"""
        task_dir = TASKS / dummy_task
        (task_dir / "04-review.md").write_text(
            "# 04-Review\n\n## Review Decision\n- **Something**: else\n\n## Gate\n- [x] OK\n",
            encoding="utf-8",
        )
        done, todo = StageValidator.check(TASKS / dummy_task, "04-review", 3)
        assert any("缺少" in item for item in todo), (
            f"expected Route missing todo, got done={done} todo={todo}"
        )
