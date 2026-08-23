"""_handle_settlement_choice must not blow up on the time module.

Regression: tui.py called time.sleep() in all three settlement branches
(A/B/C) while never importing `time`, so finishing a task raised
NameError: name 'time' is not defined and the panel never exited cleanly.
"""
from unittest.mock import patch

import pytest

import sw_lib.ui.tui as tui_mod
from sw_lib.ui.tui import MonitorTUI


@pytest.fixture
def panel(dummy_task, monkeypatch):
    """A TUI instance wired up just enough to run the settlement handler."""
    t = MonitorTUI.__new__(MonitorTUI)
    t.state = tui_mod.TUIState(name=dummy_task, stage="05-archive", stage_idx=4)
    t.running = True
    t.logs = []
    monkeypatch.setattr(MonitorTUI, "_add_log",
                        lambda self, src, msg: self.logs.append((src, msg)))
    # keep the suite fast; the branches only sleep for human readability.
    # patch time.sleep globally rather than via tui_mod, so a missing
    # `import time` in tui.py still surfaces as the original NameError.
    monkeypatch.setattr("time.sleep", lambda _s: None)
    return t


def test_tui_module_imports_time():
    """Guard the import itself — every settlement branch calls time.sleep()."""
    assert hasattr(tui_mod, "time"), "tui.py must import time"


@pytest.mark.parametrize("choice", ["A", "B", "C"])
def test_settlement_branches_do_not_raise(panel, choice):
    """A/B/C must all complete and stop the panel loop."""
    with patch("sw_lib.core.service._service.remove_task"):
        panel._handle_settlement_choice(choice)

    assert panel.running is False, f"choice {choice} must stop the loop"


def test_settlement_choice_is_case_insensitive(panel):
    """Lowercase input takes the same branch."""
    panel._handle_settlement_choice("b")
    assert panel.running is False


def test_unknown_choice_keeps_panel_running(panel):
    """An unrecognised key must not silently exit the panel."""
    panel._handle_settlement_choice("Z")
    assert panel.running is True
