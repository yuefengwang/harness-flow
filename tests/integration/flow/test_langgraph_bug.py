import threading
import time
from sw_lib.core.bootstrap import bootstrap
from sw_lib.workflow.base import StageInput
from sw_lib.core.service import _service
from sw_lib.core.state import read_state

bootstrap()

task_name = "test-bug-repro"
try:
    _service.create_task(task_name, task_type="feature")
except Exception:
    pass

executor = _service.get_task_state(task_name) # Just to check it exists
from sw_lib.workflow.runtime import WorkflowRuntime
executor = WorkflowRuntime.get_executor()

print("--- First invoke ---")
stage_input = StageInput(
    task_name=task_name,
    stage="01-brainstorming",
    stage_idx=0,
    metadata={}
)
res = executor.invoke(stage_input)
print("First invoke returned:", res.stage)

print("--- Advance stage ---")
# Simulate auto_check_gate passing
from sw_lib.core.config import TASKS
(TASKS / task_name / "01-brainstorming.md").write_text("## Gate\n- [x] OK", encoding="utf-8")
_service.advance_stage(task_name)
st = read_state(task_name)
print("State after advance:", st["stage"])

print("--- Second invoke ---")
stage_input2 = StageInput(
    task_name=task_name,
    stage=st["stage"],
    stage_idx=st["stage_idx"],
    metadata={}
)
res2 = executor.invoke(stage_input2)
print("Second invoke returned:", res2.stage)

