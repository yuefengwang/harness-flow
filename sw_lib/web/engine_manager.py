"""
sw_lib.web.engine_manager — Web 会话引擎管理

管理 WorkflowEngine 的 Web 适配会话，提供线程安全的 deque 桥接 SSE 事件流。
"""

import asyncio
import time
import threading
from collections import deque
from typing import Dict, Optional, Any, Callable

from ..core.engine import WorkflowEngine
from ..core.state import read_state
from ..core.utils import now, sw_log


class WebEngineSession:

    def __init__(self, task_name: str, engine: WorkflowEngine):
        self.task_name = task_name
        self.engine = engine
        self._events: deque = deque()
        self._lock = threading.Lock()
        self._background_thread: Optional[threading.Thread] = None
        self._alive = True
        self._pending_questions: Optional[list] = None
        self._pending_res_queue: Optional[asyncio.Queue] = None

        self._install_web_callbacks()

    def _install_web_callbacks(self):
        self.engine.callbacks = {
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
        sw_log(self.task_name, f"[web] {source}: {msg[:200]}", source)

    def _web_is_running(self) -> bool:
        return self._alive

    def _web_on_ask_user(self, questions: list, res_queue):
        self._pending_questions = questions
        async_res_queue: asyncio.Queue = asyncio.Queue()
        self._pending_res_queue = async_res_queue

        self._push_event({"type": "question", "questions": questions, "q_idx": 0})

        def _bridge_answers():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                answers = loop.run_until_complete(async_res_queue.get())
                loop.close()
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
        self._background_thread = threading.Thread(
            target=self.engine.run_stage, daemon=True,
        )
        self._background_thread.start()

    def submit_answer(self, text: str):
        self.engine.answer(text)

    def submit_command(self, cmd: str):
        self.engine.handle_command(cmd)

    def destroy(self):
        self._alive = False
        try:
            self.engine.shutdown()
        except Exception:
            pass

    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def status(self) -> str:
        if self.engine and self.engine.agent:
            return self.engine.agent.status
        return "idle"

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
        return cls._instance

    def create_session(self, task_name: str, stage: str, stage_idx: int, agent_name: str) -> WebEngineSession:
        self.destroy_session(task_name)

        from ..core.service import _service
        _service.get_task_state(task_name)

        callbacks_placeholder: Dict[str, Callable] = {}
        engine = WorkflowEngine(
            name=task_name, stage=stage, stage_idx=stage_idx,
            agent_name=agent_name, callbacks=callbacks_placeholder,
        )

        session = WebEngineSession(task_name, engine)
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
