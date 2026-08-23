from sw_lib.core.service import TaskService
from sw_lib.core.state import write_state
from sw_lib.core.config import TASKS

def test_service_advance_last_stage(dummy_task):
    """TaskService: last stage → marked Finished."""
    write_state(dummy_task, {
        "id": dummy_task,
        "stage": "05-archive",
        "stage_idx": 4,
        "stage_status": "pending",
        "agent": "cat",
    })

    svc = TaskService()
    result = svc.advance_stage(dummy_task)
    assert result["stage_status"] == "Finished"

def test_service_delegates_to_runtime(dummy_task):
    """Service determines next stage using WorkflowRuntime logic."""
    from sw_lib.core.bootstrap import bootstrap
    bootstrap()
    
    # 模拟在 01 阶段且门禁已通过
    task_dir = TASKS / dummy_task
    (task_dir / "01-brainstorming.md").write_text("## Gate\n- [x] OK\n", encoding="utf-8")
    
    write_state(dummy_task, {
        "id": dummy_task,
        "stage": "01-brainstorming",
        "stage_idx": 0,
        "stage_status": "pending",
    })

    svc = TaskService()
    result = svc.advance_stage(dummy_task)
    
    # 应线性推进到 02
    assert result["stage"] == "02-planning"
    assert result["stage_idx"] == 1
