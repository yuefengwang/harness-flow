"""get_active_from_status must ignore stale STATUS.json entries.

Regression: STATUS.json is only a cache. When a task directory is removed
outside the CLI (test residue, manual rm), its entry survives. The resolver
used to return such a name, and every caller that immediately reads .state
(`sw status`, `sw advance`, `sw answer`) died with "无法读取任务状态".
"""
import json

import pytest

from sw_lib.core import state as state_mod


@pytest.fixture
def status_env(tmp_path, monkeypatch):
    """Point TASKS/STATUS at a temp workspace."""
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    status = tmp_path / "STATUS.json"
    monkeypatch.setattr(state_mod, "TASKS", tasks)
    monkeypatch.setattr(state_mod, "STATUS", status)
    return tasks, status


def _write_status(status, entries):
    status.write_text(json.dumps({"project": "", "updated_at": "", "tasks": entries}))


def _make_live_task(tasks, name, stage_status="running"):
    d = tasks / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({"id": name, "stage_status": stage_status}))


def test_stale_entry_is_skipped(status_env):
    """Entry without a backing .state must not be returned."""
    tasks, status = status_env
    _write_status(status, {
        "ghost-task": {"stage_status": "running", "updated_at": "2026-01-02"},
    })

    assert state_mod.get_active_from_status() is None


def test_live_task_wins_over_newer_stale_entry(status_env):
    """A stale entry with a newer timestamp must not shadow a live task."""
    tasks, status = status_env
    _make_live_task(tasks, "real-task")
    _write_status(status, {
        "ghost-task": {"stage_status": "running", "updated_at": "2099-12-31"},
        "real-task": {"stage_status": "running", "updated_at": "2026-01-01"},
    })

    assert state_mod.get_active_from_status() == "real-task"


def test_resolved_name_is_always_readable(status_env):
    """Whatever is returned must survive an immediate read_state()."""
    tasks, status = status_env
    _make_live_task(tasks, "real-task")
    _write_status(status, {
        "ghost-task": {"stage_status": "pending", "updated_at": "2099-01-01"},
        "real-task": {"stage_status": "running", "updated_at": "2026-01-01"},
    })

    name = state_mod.get_active_from_status()
    assert name is not None
    assert state_mod.read_state(name), "resolver returned an unreadable task"


def test_inactive_status_still_filtered(status_env):
    """Finished tasks are not active even when their .state exists."""
    tasks, status = status_env
    _make_live_task(tasks, "done-task", stage_status="Finished")
    _write_status(status, {
        "done-task": {"stage_status": "Finished", "updated_at": "2026-01-01"},
    })

    assert state_mod.get_active_from_status() is None
