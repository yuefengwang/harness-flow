import pytest
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state
from sw_lib.core.engine import ContextBuilder


@pytest.fixture(autouse=True)
def ensure_context_builder():
    """Ensure ContextBuilder._prompt_builder is set for all tests.
    Does NOT override if already set by bootstrap()."""
    saved = ContextBuilder._prompt_builder
    if ContextBuilder._prompt_builder is None:
        ContextBuilder._prompt_builder = MagicMock()
        ContextBuilder._prompt_builder.build = MagicMock(return_value=(
            "你是 Harness-Flow 平台的 AI Agent。\n"
            "Brainstorming 头脑风暴\n"
            "=== 编排规则 ===\n"
            "=== 强制规则 ===\n"
            "=== 项目信息 ===\n"
            "=== 前一阶段产出 ===\n"
            "=== 当前阶段模板 ===\n"
            "=== 任务需求 ===\n"
            "Harness-Flow AI Agent" * 3
        ))
    yield
    if saved is None:
        ContextBuilder._prompt_builder = None
    else:
        ContextBuilder._prompt_builder = saved

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
