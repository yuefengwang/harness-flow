"""
sw_lib.web.routes.console — Agent Console 路由

提供 Agent 控制台页面、SSE 事件流及引擎控制端点。
"""

import json
from pathlib import Path

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from ..templating import SafeJinja2Templates

from ...core.config import STAGES, STAGE_NAMES, TASKS
from ...core.service import _service, TaskError
from ..engine_manager import WebEngineManager

router = APIRouter()
templates = SafeJinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

_engine_manager = WebEngineManager.get_instance()


@router.get("/tasks/{name}/console", response_class=HTMLResponse)
async def console_page(request: Request, name: str):
    try:
        st = _service.get_task_state(name)
    except TaskError:
        return HTMLResponse("任务不存在", status_code=404)

    return templates.TemplateResponse("console.html", {
        "request": request,
        "task": st,
        "stage_labels": dict(zip(STAGES, STAGE_NAMES)),
    })


@router.get("/tasks/{name}/log")
async def task_log(name: str, after_line: int = Query(0, alias="after_line")):
    """返回指定行号之后的日志行（结构化 JSON），用于前端增量拉取"""
    task_dir = TASKS / name
    log_file = task_dir / ".log"
    if not log_file.exists():
        return JSONResponse({"lines": [], "total_lines": 0})

    raw = log_file.read_text(encoding="utf-8")
    all_lines = raw.splitlines()
    total = len(all_lines)

    new_lines = all_lines[after_line:] if after_line < total else []
    parsed = [_parse_log_line(ln) for ln in new_lines]

    return JSONResponse({"lines": parsed, "total_lines": total})


def _parse_log_line(line: str) -> dict:
    """将 [2026-05-21T10:39:53] sw    | task created ... 格式解析为结构化日志"""
    import re
    result = {"raw": line}
    m = re.match(
        r"^\[([^\]]+)\]\s+(\w+)\s*\|\s*(.*)",
        line
    )
    if m:
        result["ts"] = m.group(1)
        result["source"] = m.group(2)
        result["msg"] = m.group(3)
    return result


_STAGE_LABELS = dict(zip(STAGES, STAGE_NAMES))


@router.get("/tasks/{name}/state")
async def task_state(name: str):
    try:
        st = _service.get_task_state(name)
    except TaskError:
        return JSONResponse({"error": "任务不存在"}, status_code=404)

    stage = st.get("stage", "")
    return JSONResponse({
        "stage": stage,
        "stage_label": _STAGE_LABELS.get(stage, stage),
        "stage_status": st.get("stage_status", "pending"),
        "agent_status": st.get("agent_status", "idle"),
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
