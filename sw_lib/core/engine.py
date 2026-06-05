"""
sw_lib.engine — 工作流自动化编排引擎 (SHIM).

DEPRECATED: 核心编排逻辑已迁移至 sw_lib.runnable。
此模块保留 WorkflowEngine 类以维持 UI 层的兼容性。
"""

import threading
from typing import Optional, List, Dict, Callable, Any, Tuple

MAX_REROUTE = 3

from .state import read_state

def _auto_check_gate(task_name: str, stage: str):
    from ..runnable.utils import auto_check_gate
    return auto_check_gate(task_name, stage)

def _parse_route_from_ai_output(content: str) -> Optional[str]:
    from ..runnable.utils import parse_route_from_ai_output
    return parse_route_from_ai_output(content)

def parse_route_field(task_name: str) -> Optional[str]:
    from ..runnable.utils import parse_route_field
    return parse_route_field(task_name)

def extract_evidence_table(task_name: str) -> Optional[str]:
    from ..runnable.utils import extract_evidence_table
    return extract_evidence_table(task_name)

def inject_reroute_context(task_name: str, target_stage: str):
    from ..runnable.utils import inject_reroute_context
    return inject_reroute_context(task_name, target_stage)

def _remove_old_reroute_blocks(content: str) -> str:
    from ..runnable.utils import _remove_old_reroute_blocks
    return _remove_old_reroute_blocks(content)

def _reset_gate_checkboxes(task_name: str, stage: str):
    from ..runnable.utils import _reset_gate_checkboxes
    return _reset_gate_checkboxes(task_name, stage)

class ContextBuilder:
    """Agent 上下文构建器 (SHIM)。委托给 PromptBuilder。"""
    _prompt_builder = None
    @staticmethod
    def build(task_name: str, stage: str, stage_idx: int) -> Optional[str]:
        if ContextBuilder._prompt_builder:
            return ContextBuilder._prompt_builder.build(
                task_name=task_name, stage=stage, stage_idx=stage_idx
            )
        return None

class OutputExtractor:
    """Agent 产出提取器 (SHIM)。功能已由 StageOutputParser 接管。"""
    def extract_and_save(self, name, stage, lines, log_callback):
        return True

class WorkflowEngine:
    """
    工作流引擎 (SHIM)。
    
    DEPRECATED: 请使用 sw_lib.runnable.runtime.WorkflowRuntime 获取执行器。
    """
    _workflow_chain = None

    def __init__(self, name: str, stage: str, stage_idx: int, agent_name: str, callbacks: Dict[str, Callable]):
        self.name = name
        self.stage = stage
        self.stage_idx = stage_idx
        self.agent_name = agent_name
        self.callbacks = callbacks
        self.agent: Optional[Any] = None
        self._output_lock = threading.Lock()

    @property
    def model_name(self) -> str:
        from .config import resolve_agent_model
        return resolve_agent_model(self.stage, self.agent_name)

    def _add_log(self, source: str, msg: str):
        if "add_log" in self.callbacks:
            self.callbacks["add_log"](source, msg)

    def _validate_pre_hooks(self):
        return True

    def _validate_post_hooks(self):
        return True

    def _build_context(self, name=None, stage=None, idx=None):
        return ContextBuilder.build(
            task_name=name or self.name,
            stage=stage or self.stage,
            stage_idx=idx if idx is not None else self.stage_idx
        )

    def advance_stage(self):
        if not self._validate_post_hooks():
            return False
            
        from .service import _service
        _service.advance_stage(self.name)
        return True

    def answer(self, text):
        from ..runnable.runtime import WorkflowRuntime
        WorkflowRuntime.get_executor().answer(text)

    def handle_command(self, cmd):
        cmd = cmd.strip()
        if cmd == "status":
            st = read_state(self.name)
            msg = f"Graph status (via shim). stage={st.get('stage', 'unknown')}"
            self._add_log("sw", msg)
            
        from ..runnable.runtime import WorkflowRuntime
        WorkflowRuntime.get_executor().handle_command(cmd)

    def shutdown(self):
        from ..runnable.runtime import WorkflowRuntime
        executor = WorkflowRuntime.get_executor()
        if executor.active_stage and executor.active_stage.active_agent:
            executor.active_stage.active_agent.shutdown()

    def resume_running(self):
        # 兼容性空实现，LangGraphAdapter 内部已处理
        pass

    def run_stage(self):
        # 兼容性空实现，UI 现在直接调用 executor.invoke
        pass

    def save_stage_output(self):
        return True
