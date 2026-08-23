"""`sw init --yes` must skip the wizard's final confirmation.

Regression background: main.py's --yes only exported SW_YES, and the sole
reader (core.utils.prompt_yn) had no callers left, so the documented flag
was silently inert and the wizard still blocked on Confirm.ask.
"""
from unittest.mock import patch

import pytest

from sw_lib.ui.init_ui import InitializationUI


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    """Drive run() with every earlier interactive step stubbed out."""
    monkeypatch.setattr("sw_lib.ui.init_ui.TASKS", tmp_path / "tasks")

    answers = iter(["self", "yes-flag-task", "feature"])
    monkeypatch.setattr("sw_lib.ui.init_ui.Prompt.ask",
                        lambda *a, **k: next(answers))
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(lambda: "ctx")})())

    ui = InitializationUI()
    monkeypatch.setattr(ui.console, "clear", lambda: None)
    monkeypatch.setattr(ui.console, "print", lambda *a, **k: None)
    return ui


def test_yes_env_skips_confirm_prompt(wizard, monkeypatch):
    """SW_YES=1 must return data without ever consulting Confirm.ask."""
    monkeypatch.setenv("SW_YES", "1")

    with patch("sw_lib.ui.init_ui.Confirm.ask",
               side_effect=AssertionError("Confirm.ask must not run under --yes")) as m:
        result = wizard.run()

    assert result is not None
    assert result["name"] == "yes-flag-task"
    assert result["context"] == "ctx"
    m.assert_not_called()


def test_without_yes_confirm_is_consulted(wizard, monkeypatch):
    """Without SW_YES the interactive confirmation still gates creation."""
    monkeypatch.delenv("SW_YES", raising=False)

    with patch("sw_lib.ui.init_ui.Confirm.ask", return_value=False) as m:
        result = wizard.run()

    m.assert_called_once()
    assert result is None


def test_confirm_accept_returns_data(wizard, monkeypatch):
    """Accepting the prompt returns the collected data unchanged."""
    monkeypatch.delenv("SW_YES", raising=False)

    with patch("sw_lib.ui.init_ui.Confirm.ask", return_value=True):
        result = wizard.run()

    assert result["name"] == "yes-flag-task"
    assert result["type"] == "feature"
