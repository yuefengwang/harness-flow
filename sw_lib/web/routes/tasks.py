"""
sw_lib.web.routes.tasks — 任务管理路由

提供 Dashboard 所需的全部 RESTful 端点与页面路由。
使用 Jinja2Templates 服务端渲染 HTML，HTMX 处理局部更新。
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ...core.service import _service, TaskError
from ...core.config import STAGES, STAGE_NAMES, TASKS

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


# ── Helper ──

def _get_task_list_data():
    """获取统一的任务列表数据"""
    active = _service.list_tasks(from_trash=False)
    trashed = _service.list_tasks(from_trash=True)
    return {
        "active_tasks": active,
        "trashed_tasks": trashed,
        "stage_labels": dict(zip(STAGES, STAGE_NAMES)),
    }


# ── Pages ──

@router.get("/", response_class=HTMLResponse)
async def dashboard_index(request: Request):
    """Dashboard 首页：任务看板"""
    data = _get_task_list_data()
    return templates.TemplateResponse("index.html", {"request": request, **data})


# ── HTMX partials (must be before dynamic /tasks/{name} to avoid capture) ──

@router.get("/tasks/table", response_class=HTMLResponse)
async def task_table_partial():
    """HTMX 局部刷新：任务列表表格"""
    return _task_table_html()


@router.get("/tasks/{name}", response_class=HTMLResponse)
async def task_detail(request: Request, name: str):
    """任务详情页"""
    try:
        state = _service.get_task_state(name)
    except TaskError:
        return HTMLResponse("任务不存在", status_code=404)

    task_dir = TASKS / name
    stage_files = []
    if task_dir.is_dir():
        for s in STAGES:
            sf = task_dir / f"{s}.md"
            stage_files.append({
                "stage": s,
                "label": STAGE_NAMES[STAGES.index(s)],
                "exists": sf.exists(),
            })

    log_content = ""
    log_file = task_dir / ".log"
    if log_file.exists():
        log_content = log_file.read_text(encoding="utf-8")[-5000:]

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "task": state,
        "stage_files": stage_files,
        "log_content": log_content,
        "stage_labels": dict(zip(STAGES, STAGE_NAMES)),
    })


# ── Task CRUD ──

@router.post("/tasks/create")
async def task_create(
    name: str = Form(...),
    task_type: str = Form("feature"),
    context: Optional[str] = Form(""),
):
    try:
        _service.create_task(name, task_type=task_type, context=context or "")
        return _task_table_html()
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


@router.post("/tasks/{name}/advance")
async def task_advance(name: str):
    try:
        state = _service.get_task_state(name)
        idx = int(state.get("stage_idx", 0))
        done, todo = _service.validate_stage(name)
        if todo:
            return HTMLResponse(
                f"<div class='error'>存在 {len(todo)} 个未完成项，无法推进</div>",
                status_code=400,
            )
        _service.advance_stage(name)
        return _task_table_html()
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


@router.post("/tasks/{name}/remove")
async def task_remove(name: str):
    try:
        _service.remove_task(name)
        return _task_table_html()
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


@router.post("/tasks/{name}/restore")
async def task_restore(name: str):
    try:
        _service.restore_task(name)
        return _task_table_html()
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


def _task_table_html() -> str:
    """渲染任务列表 HTML 片段"""
    data = _get_task_list_data()
    lines = []
    lines.append('<div id="task-list">')

    if data["active_tasks"]:
        lines.append("<h3>活跃任务</h3>")
        lines.append('<table class="task-table"><thead><tr>'
                     '<th>任务名</th><th>阶段</th><th>状态</th><th>更新时间</th><th>操作</th>'
                     '</tr></thead><tbody>')
        for t in data["active_tasks"]:
            stage_label = data["stage_labels"].get(t["stage"], t["stage"])
            status_class = "status-" + t.get("status", "unknown")
            lines.append(
                f'<tr>'
                f'<td><a href="/tasks/{t["id"]}">{t["id"]}</a></td>'
                f'<td>{stage_label}</td>'
                f'<td class="{status_class}">{t["status"]}</td>'
                f'<td>{t.get("updated_at", "")}</td>'
                f'<td class="actions">'
                f'<button hx-post="/tasks/{t["id"]}/advance" hx-target="#task-list" hx-swap="outerHTML">推进</button>'
                f'<button class="danger" hx-post="/tasks/{t["id"]}/remove" hx-target="#task-list" hx-swap="outerHTML" hx-confirm="确认移除任务 {t["id"]}?">删除</button>'
                f'</td>'
                f'</tr>'
            )
        lines.append("</tbody></table>")
    else:
        lines.append("<p class='empty'>暂无活跃任务</p>")

    if data["trashed_tasks"]:
        lines.append("<h3>回收站</h3>")
        lines.append('<table class="task-table"><thead><tr>'
                     '<th>任务名</th><th>阶段</th><th>移除时间</th><th>操作</th>'
                     '</tr></thead><tbody>')
        for t in data["trashed_tasks"]:
            lines.append(
                f'<tr class="trashed">'
                f'<td>{t["id"]}</td>'
                f'<td>{t["stage"]}</td>'
                f'<td>{t.get("removed_at", "")}</td>'
                f'<td class="actions">'
                f'<button hx-post="/tasks/{t["id"]}/restore" hx-target="#task-list" hx-swap="outerHTML">恢复</button>'
                f'</td>'
                f'</tr>'
            )
        lines.append("</tbody></table>")

    lines.append("</div>")
    return "\n".join(lines)
