from sw_lib.core.bootstrap import bootstrap
from sw_lib.workflow.base import StageInput
from sw_lib.core.service import _service

bootstrap()
task_name = "test-mock-output"
try:
    _service.create_task(task_name, task_type="feature")
except Exception:
    pass

from sw_lib.workflow.runtime import WorkflowRuntime
executor = WorkflowRuntime.get_executor()

def on_log(source, msg):
    print(f"[{source}] {msg}")

stage_input = StageInput(
    task_name=task_name, stage="03-coding", stage_idx=2,
    metadata={"callbacks": {"add_log": on_log}}
)
executor.invoke(stage_input)
