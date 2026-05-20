"""
sw_lib.web.routes.console — Agent Console 路由

提供 Agent 控制台页面、SSE 事件流及引擎控制端点。
"""

import json
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ...core.config import STAGES, STAGE_NAMES, TASKS
from ...core.service import _service, TaskError
from ..engine_manager import WebEngineManager

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

_engine_manager = WebEngineManager.get_instance()


@router.get("/tasks/{name}/console", response_class=HTMLResponse)
async def console_page(request: Request, name: str):
    try:
        st = _service.get_task_state(name)
    except TaskError:
        return HTMLResponse("任务不存在", status_code=404)

    task_dir = TASKS / name
    log_content = ""
    log_file = task_dir / ".log"
    if log_file.exists():
        log_content = log_file.read_text(encoding="utf-8")[-10000:]

    return templates.TemplateResponse("console.html", {
        "request": request,
        "task": st,
        "log_content": log_content,
        "stage_labels": dict(zip(STAGES, STAGE_NAMES)),
    })


@router.get("/tasks/{name}/sse")
async def console_sse(name: str):
    session = _engine_manager.get_session(name)
    if not session:
        return HTMLResponse("会话不存在，请先启动引擎", status_code=404)

    async def event_stream():
        while session.is_alive:
            event = await session.get_event()
            if event is None:
                try:
                    yield f": keepalive\n\n"
                except Exception:
                    break
                continue
            data = json.dumps(event, ensure_ascii=False)
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tasks/{name}/engine/start")
async def engine_start(name: str):
    try:
        st = _service.get_task_state(name)
    except TaskError:
        return HTMLResponse("任务不存在", status_code=404)

    stage = st.get("stage", STAGES[0])
    stage_idx = int(st.get("stage_idx", 0))
    agent = st.get("agent", "N/A")

    session = _engine_manager.create_session(name, stage, stage_idx, agent)
    session.start_engine()

    return HTMLResponse(
        f'<div class="success">引擎已启动: {stage}</div>'
    )


@router.post("/tasks/{name}/engine/answer")
async def engine_answer(name: str, text: str = Form(...)):
    session = _engine_manager.get_session(name)
    if not session:
        return HTMLResponse("会话不存在", status_code=404)
    session.submit_answer(text)
    return HTMLResponse('<div class="success">已发送</div>')


@router.post("/tasks/{name}/engine/command")
async def engine_command(name: str, cmd: str = Form(...)):
    session = _engine_manager.get_session(name)
    if not session:
        return HTMLResponse("会话不存在", status_code=404)
    session.submit_command(cmd)
    return HTMLResponse(f'<div class="success">命令已执行: {cmd}</div>')
