import pytest
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state


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

