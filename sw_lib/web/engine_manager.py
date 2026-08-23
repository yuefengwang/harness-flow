"""
sw_lib.web.engine_manager — Web session management for the workflow runtime.

Manages WebEngineSession instances that provide thread-safe deque bridging for SSE event streams.
"""

import asyncio
import time
import threading
from collections import deque
from typing import Dict, Optional, Any

from ..workflow.engine import WorkflowEngine, LangGraphWorkflowEngine
from ..core.state import read_state
from ..core.utils import now, sw_log


class WebEngineSession:

    def __init__(self, task_name: str, stage: str, stage_idx: int, agent_name: str,
                 engine: Optional[WorkflowEngine] = None):
        self.task_name = task_name
        self._events: deque = deque()
        self._lock = threading.Lock()
        self._background_thread: Optional[threading.Thread] = None
        self._alive = True
        self._pending_questions: Optional[list] = None
        self._pending_res_queue: Optional[Any] = None
        # Phase 3: Web drives the workflow exclusively through the engine
        # abstraction; it never touches executor.active_stage.active_agent.
        self.engine: WorkflowEngine = engine or LangGraphWorkflowEngine()

        self.callbacks = {
            "add_log": self._web_add_log,
            "is_running": self._web_is_running,
            "on_ask_user": self._web_on_ask_user,
            "on_settlement": self._web_on_settlement,
        }

    def _push_event(self, event: dict):
        with self._lock:
            self._events.append(event)

    def _web_add_log(self, source: str, msg: str):
        self._push_event({"type": "log", "source": source, "msg": msg, "ts": now()})
        sw_log(self.task_name, f"[web] {source}: {msg[:1000]}", source)

    def _web_is_running(self) -> bool:
        return self._alive

    def _web_on_ask_user(self, questions: list, res_queue):
        import queue
        self._pending_questions = questions
        # Use standard thread-safe queue instead of asyncio to avoid loop issues in threads
        sync_res_queue = queue.Queue()
        self._pending_res_queue = sync_res_queue

        self._push_event({"type": "question", "questions": questions, "q_idx": 0})

        def _bridge_answers():
            try:
                answers = sync_res_queue.get()
            except Exception:
                answers = [""] * len(questions)
            res_queue.put(answers)

        t = threading.Thread(target=_bridge_answers, daemon=True)
        t.start()

    def _web_on_settlement(self):
        st = read_state(self.task_name)
        self._push_event({
            "type": "settlement",
            "stage": st.get("stage", ""),
            "stage_status": st.get("stage_status", ""),
        })

    def start_engine(self):
        if not self._alive:
            return

        from ..workflow.base import StageInput
        st = read_state(self.task_name)
        stage_input = StageInput(
            task_name=self.task_name,
            stage=st.get("stage", "01-brainstorming"),
            stage_idx=int(st.get("stage_idx", 0)),
            metadata={"callbacks": self.callbacks}
        )
        self._background_thread = threading.Thread(
            target=self.engine.start_stage, args=(stage_input,), daemon=True,
        )
        self._background_thread.start()

    def submit_answer(self, text: str):
        if self._pending_res_queue:
            # We are waiting for a question
            answers = [text] * max(1, len(self._pending_questions))
            self._record_decisions(self._pending_questions, text)
            self._pending_res_queue.put(answers)

            self._pending_questions = []
            self._pending_res_queue = None

            # Resume executor active agent state if possible
            self.engine.submit_answer(self.task_name, text)
        else:
            self.engine.submit_answer(self.task_name, text)

    def submit_command(self, cmd: str):
        self.engine.submit_command(self.task_name, cmd)

    def _record_decisions(self, questions, text: str):
        """把用户答复记进 .state，与 TUI 走同一套拍板状态源。

        Web 与 TUI 是同一个工作流的两个前端，判定依据必须一致；只在 TUI 侧
        记录会让同一个 bug 在 Web 上原样复现（任务 T1）。
        失败一律吞掉：这在提交答复的主流程上，抛异常会让页面拿到 500。
        """
        try:
            from ..workflow import stage_state as ss
            stage = read_state(self.task_name).get("stage", "")
            if not stage:
                return
            for idx, q in enumerate(questions or []):
                label = ""
                if isinstance(q, dict):
                    label = (q.get("question") or q.get("header") or "").strip()
                if not label:
                    label = f"question_{idx + 1}"
                ss.record_decision(self.task_name, stage,
                                   question=label, answer=text, by="user")
        except Exception as e:
            self._web_add_log("sw", f"⚠️ 拍板记录写入失败: {e}")

    def destroy(self):
        self._alive = False
        try:
            self.engine.shutdown(self.task_name)
        except Exception:
            pass

    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def status(self) -> str:
        # Sourced from the engine abstraction (state-backed), never from the
        # live agent's private status attribute.
        return self.engine.get_status(self.task_name)

    async def get_event(self) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + 30.0
        while self._alive:
            with self._lock:
                if self._events:
                    return self._events.popleft()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(0.05, remaining))
        return None

    async def get_all_events(self) -> list:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events


class WebEngineManager:

    _instance: Optional["WebEngineManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: Dict[str, WebEngineSession] = {}
            cls._instance._engine: WorkflowEngine = LangGraphWorkflowEngine()
        return cls._instance

    def create_session(self, task_name: str, stage: str, stage_idx: int, agent_name: str) -> WebEngineSession:
        self.destroy_session(task_name)

        from ..core.service import _service
        _service.get_task_state(task_name)

        session = WebEngineSession(task_name, stage, stage_idx, agent_name,
                                   engine=self._engine)
        self._sessions[task_name] = session
        sw_log(task_name, "[web] session created", "sw")
        return session

    def get_session(self, task_name: str) -> Optional[WebEngineSession]:
        return self._sessions.get(task_name)

    def destroy_session(self, task_name: str):
        session = self._sessions.pop(task_name, None)
        if session:
            session.destroy()
            sw_log(task_name, "[web] session destroyed", "sw")

    def is_alive(self, task_name: str) -> bool:
        session = self.get_session(task_name)
        return session is not None and session.is_alive

    @classmethod
    def get_instance(cls) -> "WebEngineManager":
        return cls()


_engine_manager = WebEngineManager()
