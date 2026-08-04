import time
from sw_lib.core.bootstrap import bootstrap
from sw_lib.workflow.base import StageInput
from sw_lib.core.service import _service
from sw_lib.core.state import read_state
from sw_lib.core.config import TASKS

bootstrap()
task_name = "test-mock-output2"
try:
    _service.create_task(task_name, task_type="feature")
except Exception:
    pass

from sw_lib.workflow.runtime import WorkflowRuntime
executor = WorkflowRuntime.get_executor()

def on_log(source, msg):
    print(f"[{source}] {msg}")

# Run 01
stage_input = StageInput(
    task_name=task_name, stage="01-brainstorming", stage_idx=0,
    metadata={"callbacks": {"add_log": on_log}}
)
executor.invoke(stage_input)
print("--- 01 finished ---")

# Advance to 02
(TASKS / task_name / "01-brainstorming.md").write_text("## Gate\n- [x] OK", encoding="utf-8")
_service.advance_stage(task_name)
st = read_state(task_name)
print("State is now:", st["stage"])

# Run 02
stage_input2 = StageInput(
    task_name=task_name, stage=st["stage"], stage_idx=st["stage_idx"],
    metadata={"callbacks": {"add_log": on_log}}
)
executor.invoke(stage_input2)
