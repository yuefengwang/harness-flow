import pytest
from sw_lib.core.service import TaskService, TaskError
from sw_lib.core.config import TASKS, TRASH

def test_create_task_trash_collision_logic(dummy_task):
    """
    验证：当任务在回收站时，默认报错，但开启 allow_trash_collision 后应成功。
    """
    service = TaskService()
    # dummy_task 已经在 TASKS 中了，我们先把它移到回收站
    service.remove_task(dummy_task)
    assert (TRASH / dummy_task).exists()
    
    # 1. 再次创建（不带 allow 标志），预期报错
    with pytest.raises(TaskError) as exc:
        service.create_task(dummy_task)
    assert "已在回收站" in str(exc.value)

    # 2. 开启 allow 标志，预期成功
    clean_name = service.create_task(dummy_task, allow_trash_collision=True)
    assert clean_name == dummy_task
    assert (TASKS / dummy_task).exists()
