import pytest
import shutil
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state, remove_task_summary
from sw_lib.web.engine_manager import WebEngineManager


def _snapshot_workspace():
    """记录当前 workspace/repo 里已存在的名字。"""
    from sw_lib.core.config import STATUS

    trash = TASKS / ".trash"
    repo = TASKS.parent.parent / "repo"

    def _dirs(base):
        if not base.is_dir():
            return set()
        return {p.name for p in base.iterdir() if p.is_dir()}

    status_names = set()
    if STATUS.exists():
        import json
        try:
            status_names = set(json.loads(STATUS.read_text(encoding="utf-8")).get("tasks", {}))
        except Exception:
            pass

    return {
        "tasks": _dirs(TASKS) - {".trash"},
        "trash": _dirs(trash),
        "repo": _dirs(repo),
        "status": status_names,
    }


@pytest.fixture(scope="session", autouse=True)
def _reap_workspace_residue():
    """兜底清理测试在真实 workspace 里留下的残渣。

    大部分用例自己会 rmtree，但清理散落在二十多个文件里，删目录和删
    STATUS.json 条目是两个必须手工配对的动作，漏一半就留下孤儿条目 ——
    历史上已经积到十几条。理想做法是所有用例都隔离到 tmp_path（见
    tests/unit/core/test_remove_all.py 的 isolated_workspace），但 TASKS
    被二十多个模块在导入期各自绑定，逐个 monkeypatch 反而更容易漏。

    这里只做差集清理：跑测试前拍快照，结束后删掉新增出来的名字。
    预先存在的任务（用户的真实任务）一律不碰。
    """
    before = _snapshot_workspace()
    yield
    after = _snapshot_workspace()

    trash = TASKS / ".trash"
    repo = TASKS.parent.parent / "repo"
    for base, key in ((TASKS, "tasks"), (trash, "trash"), (repo, "repo")):
        for name in sorted(after[key] - before[key]):
            shutil.rmtree(base / name, ignore_errors=True)

    new_entries = after["status"] - before["status"]
    for name in sorted(new_entries):
        remove_task_summary(name)


@pytest.fixture(autouse=True)
def _reset_web_engine_manager():
    """WebEngineManager is a process-wide singleton; clear its session registry
    between tests so create_session/get_session assertions don't see stale
    state left by a previous test."""
    yield
    mgr = WebEngineManager._instance
    if mgr is not None:
        mgr._sessions.clear()


@pytest.fixture
def dummy_task():
    """创建一个临时任务并在测试结束后清理"""
    name = "pytest-dummy-task"
    task_dir = TASKS / name
    task_dir.mkdir(parents=True, exist_ok=True)
    
    write_state(name, {
        "id": name,
        "stage": "01-brainstorming",
        "stage_idx": 0,
        "stage_status": "pending",
        "agent": "cat"
    })
    
    yield name
    
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    # 删目录不会自动清 STATUS.json 条目，漏了就留孤儿。
    remove_task_summary(name)
    # OpenCodeAgent 会为任务在 repo/<name> 下建工作目录，一并清理，
    # 否则 repo/ 里会长期堆积 pytest-dummy-task 之类的空目录。
    repo_dir = TASKS.parent.parent / "repo" / name
    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)


# ── Gate/Route 状态源辅助 ──
#
# 门禁判定读 .state 的 stages 字段（docs/design-json-state-source.md），
# 不再解析 Markdown。因此测试要表达「Gate 已签署」这个前提时，必须显式写
# JSON —— 在阶段文件里写 `- [x]` 是无效的，那正是本次设计要废除的语义。

@pytest.fixture
def make_task():
    """建任务目录 + .state，并返回一个 (name) -> None 的注册器。

    自动清理创建过的任务。用于需要真实 .state 的门禁测试。
    """
    created = []

    def _make(name, stage="01-brainstorming", stage_idx=0, **extra):
        d = TASKS / name
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": name, "stage": stage, "stage_idx": stage_idx,
            "stage_status": "running",
        }
        payload.update(extra)
        write_state(name, payload)
        created.append(name)
        return name

    yield _make

    for name in created:
        shutil.rmtree(TASKS / name, ignore_errors=True)
        remove_task_summary(name)


@pytest.fixture
def sign_gate():
    """签署指定阶段的 Gate（写 .state）。

    返回 (task, stage) -> bool。测试若只想验证「非门禁项不该拦路」，
    需要先用它把 Gate 签掉，否则待办里必然留着未签署的门禁项。
    """
    from sw_lib.workflow import stage_state as ss

    def _sign(task, stage, by="user"):
        return ss.sign_gate(task, stage, by=by)

    return _sign

@pytest.fixture
def agent_callbacks():
    """提供 Agent 基础回调"""
    return {
        "add_log": lambda s, m: None,
        "is_running": lambda: True
    }

@pytest.fixture
def workflow_state():
    """提供一个初始化的 WorkflowState 字典 (用于 LangGraph 测试)"""
    return {
        "task_name": "test-task",
        "current_stage": "01-brainstorming",
        "stage_idx": 0,
        "history_outputs": [],
        "last_output": None,
        "next_route": None,
        "reroute_count": 0,
        "gate_passed": False
    }
