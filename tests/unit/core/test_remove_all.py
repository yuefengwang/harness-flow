"""remove-all 命令：批量移除所有任务。

全程隔离到 tmp_path —— 这是唯一会成批删任务的代码路径，测试绝不能碰
真实 workspace/tasks。
"""
import pytest

import sw_lib.core.service as service_mod
import sw_lib.core.state as state_mod
from sw_lib.core.service import TaskService, TaskError


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """把 TASKS / TRASH / STATUS 全部改指到 tmp_path。"""
    tasks = tmp_path / "tasks"
    trash = tasks / ".trash"
    tasks.mkdir(parents=True)
    monkeypatch.setattr(service_mod, "TASKS", tasks)
    monkeypatch.setattr(service_mod, "TRASH", trash)
    monkeypatch.setattr(state_mod, "TASKS", tasks)
    monkeypatch.setattr(state_mod, "STATUS", tmp_path / "STATUS.json")
    return tasks, trash


def _make_task(tasks, name, stage="01-brainstorming"):
    d = tasks / name
    d.mkdir(parents=True, exist_ok=True)
    state_mod.write_state(name, {
        "id": name, "stage": stage, "stage_idx": 0, "stage_status": "pending",
    })
    return d


def test_remove_all_moves_every_task_to_trash(isolated_workspace):
    tasks, trash = isolated_workspace
    for n in ("alpha", "beta", "gamma"):
        _make_task(tasks, n)

    results = TaskService().remove_all_tasks()

    assert sorted(n for n, _ in results) == ["alpha", "beta", "gamma"]
    assert all(why == "" for _, why in results)
    for n in ("alpha", "beta", "gamma"):
        assert not (tasks / n).exists(), f"{n} 仍在活跃目录"
        assert (trash / n).is_dir(), f"{n} 没进回收站"


def test_remove_all_is_restorable(isolated_workspace):
    """默认语义必须可恢复，否则和 --purge 没区别。"""
    tasks, trash = isolated_workspace
    _make_task(tasks, "alpha")
    svc = TaskService()
    svc.remove_all_tasks()
    svc.restore_task("alpha")
    assert (tasks / "alpha").is_dir()
    assert not (trash / "alpha").exists()


def test_remove_all_on_empty_workspace_is_noop(isolated_workspace):
    assert TaskService().remove_all_tasks() == []


def test_remove_all_skips_trash_dir(isolated_workspace):
    """.trash 是 TASKS 的子目录，不能被当成任务递归移动到自己里面。"""
    tasks, trash = isolated_workspace
    trash.mkdir(parents=True, exist_ok=True)
    _make_task(tasks, "alpha")

    results = TaskService().remove_all_tasks()

    assert [n for n, _ in results] == ["alpha"]
    assert not (trash / ".trash").exists()


def test_remove_all_isolates_failures(isolated_workspace, monkeypatch):
    """一个任务删不掉，其余仍要删干净，并把失败原因带回来。"""
    tasks, trash = isolated_workspace
    for n in ("alpha", "beta", "gamma"):
        _make_task(tasks, n)

    svc = TaskService()
    real_remove = svc.remove_task

    def flaky(name):
        if name == "beta":
            raise TaskError("boom")
        return real_remove(name)

    monkeypatch.setattr(svc, "remove_task", flaky)
    results = svc.remove_all_tasks()

    assert dict(results)["beta"] == "boom"
    assert (trash / "alpha").is_dir() and (trash / "gamma").is_dir()


def test_purge_removes_trash_permanently(isolated_workspace):
    tasks, trash = isolated_workspace
    _make_task(tasks, "alpha")

    TaskService().remove_all_tasks(purge=True)

    assert not (tasks / "alpha").exists()
    assert not (trash / "alpha").exists(), "purge 后回收站仍有残留"


def test_purge_also_clears_preexisting_trash(isolated_workspace):
    """--purge 要连之前 remove 进回收站的旧任务一起清掉。"""
    tasks, trash = isolated_workspace
    _make_task(tasks, "old")
    svc = TaskService()
    svc.remove_task("old")
    assert (trash / "old").is_dir()

    svc.remove_all_tasks(purge=True)
    assert not (trash / "old").exists()


def test_purge_clears_status_entries(isolated_workspace):
    """STATUS.json 残留会让 sw status 拿到幽灵任务。"""
    tasks, _ = isolated_workspace
    _make_task(tasks, "alpha")
    state_mod.upsert_task_summary("alpha", stage="01-brainstorming",
                                 stage_status="running")

    TaskService().remove_all_tasks(purge=True)

    assert "alpha" not in state_mod._load_task_summary()["tasks"]
    assert state_mod.get_active_from_status() is None


def test_purge_trash_clears_status_for_trashed_only_tasks(isolated_workspace):
    """回收站里的任务可能带着 STATUS 条目（例如手工放进去的残留），
    purge_trash 必须自己负责清掉，不能依赖 remove_task 早先清过。"""
    tasks, trash = isolated_workspace
    trash.mkdir(parents=True, exist_ok=True)
    (trash / "ghost").mkdir()
    state_mod.upsert_task_summary("ghost", stage="01-brainstorming",
                                 stage_status="running")

    purged = TaskService().purge_trash()

    assert purged == ["ghost"]
    assert not (trash / "ghost").exists()
    assert "ghost" not in state_mod._load_task_summary()["tasks"]
