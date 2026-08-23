"""
sw_lib.web.routes.tasks — 任务管理路由

提供 Dashboard 所需的全部 RESTful 端点与页面路由。
使用 Jinja2Templates 服务端渲染 HTML，HTMX 处理局部更新。
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from ..templating import SafeJinja2Templates

from ...core.service import _service, TaskError
from ...core.config import STAGES, STAGE_NAMES, TASKS
from ...core.deploy_orchestrator import DeployOrchestrator
import threading
import json
import asyncio
from ..cloudflared import stop_tunnel as stop_cloudflared_tunnel

router = APIRouter()
templates = SafeJinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


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

    deploy_log = ""
    deploy_log_file = task_dir / ".deploy_log"
    if deploy_log_file.exists():
        deploy_log = deploy_log_file.read_text(encoding="utf-8")[-5000:]

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "task": state,
        "stage_files": stage_files,
        "log_content": log_content,
        "deploy_log": deploy_log,
        "deploy_url": state.get("deploy_url", ""),
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
        return HTMLResponse(content=_task_table_html())
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


@router.post("/tasks/{name}/advance")
async def task_advance(name: str):
    try:
        _done, todo = _service.validate_stage(name)
        if todo:
            return HTMLResponse(
                f"<div class='error'>存在 {len(todo)} 个未完成项，无法推进</div>",
                status_code=400,
            )
        _service.advance_stage(name)
        return HTMLResponse(content=_task_table_html())
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


@router.post("/tasks/{name}/remove")
async def task_remove(name: str):
    try:
        stop_cloudflared_tunnel(name)
        _service.remove_task(name)
        return HTMLResponse(content=_task_table_html())
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


@router.post("/tasks/{name}/restore")
async def task_restore(name: str):
    try:
        _service.restore_task(name)
        return HTMLResponse(content=_task_table_html())
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)


@router.post("/tasks/{name}/deploy")
async def task_deploy(name: str):
    try:
        st = _service.deploy_task(name)
    except TaskError as e:
        return HTMLResponse(f"<div class='error'>{e}</div>", status_code=400)

    target_dir = st.get("target_dir", "")

    def _run_deploy():
        orchestrator = DeployOrchestrator(
            name=name,
            target_dir=target_dir,
            log_callback=lambda msg: None,
        )
        orchestrator.run()

    threading.Thread(target=_run_deploy, daemon=True).start()
    return HTMLResponse(content=_task_table_html())


@router.get("/tasks/{name}/deploy/sse")
async def task_deploy_sse(name: str, request: Request):
    """SSE 端点：实时推送部署日志（JSON 格式事件）"""
    from fastapi.responses import StreamingResponse

    deploy_log = TASKS / name / ".deploy_log"

    async def event_generator():
        last_size = 0
        if deploy_log.exists():
            last_size = deploy_log.stat().st_size
            # 发送初始内容
            content = deploy_log.read_text(encoding="utf-8")
            if content:
                for line in content.splitlines():
                    yield f"data: {json.dumps({'type': 'log', 'text': line})}\n\n"

        # 轮询新内容直到部署完成
        while True:
            if await request.is_disconnected():
                break
            try:
                if deploy_log.exists():
                    current_size = deploy_log.stat().st_size
                    if current_size > last_size:
                        with open(deploy_log, "r") as f:
                            f.seek(last_size)
                            new_data = f.read()
                            if new_data:
                                for line in new_data.splitlines():
                                    yield f"data: {json.dumps({'type': 'log', 'text': line})}\n\n"
                            last_size = f.tell()

                # 检查部署是否完成
                st = _service.get_task_state(name)
                ds = st.get("deploy_status", "")
                if ds in ("deployed", "deploy_failed"):
                    url = st.get("deploy_url", "")
                    yield f"data: {json.dumps({'type': 'done', 'status': ds, 'url': url})}\n\n"
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tasks/{name}/deploy/status")
async def task_deploy_status(name: str):
    """返回当前部署状态"""
    try:
        st = _service.get_task_state(name)
    except TaskError:
        return HTMLResponse("任务不存在", status_code=404)

    return {
        "status": st.get("deploy_status", "idle"),
        "url": st.get("deploy_url", ""),
        "step": st.get("deploy_step", ""),
        "task_name": name,
    }


def _task_table_html() -> str:
    """渲染任务列表 HTML 片段"""
    data = _get_task_list_data()
    lines = []
    lines.append('<div id="task-list"'
                 ' hx-get="/tasks/table"'
                 ' hx-trigger="every 2s"'
                 ' hx-swap="outerHTML">')

    if data["active_tasks"]:
        lines.append("<h3>活跃任务</h3>")
        lines.append('<table class="task-table"><thead><tr>'
                     '<th>任务名</th><th>阶段</th><th>状态</th><th>更新时间</th><th>操作</th>'
                     '</tr></thead><tbody>')
        for t in data["active_tasks"]:
            stage_label = data["stage_labels"].get(t["stage"], t["stage"])
            status_class = "status-" + t.get("status", "unknown")
            actions_html = '<td class="actions">'
            actions_html += f'<a href="/tasks/{t["id"]}/console" class="btn">对话</a> '
            if t.get("status") == "Finished":
                ds = t.get("deploy_status", "idle")
                if ds == "idle":
                    actions_html += f'<button class="btn-success" hx-post="/tasks/{t["id"]}/deploy" hx-target="#task-list" hx-swap="outerHTML">部署</button>'
                elif ds == "deploying":
                    actions_html += '<span class="status-deploying">部署中...</span>'
                elif ds == "deployed":
                    actions_html += '<span class="status-deployed">已部署</span> '
                    if t.get("deploy_url"):
                        actions_html += f'<a href="{t["deploy_url"]}" target="_blank" class="deploy-url">{t["deploy_url"]}</a> '
                elif ds == "deployed_unhealthy":
                    actions_html += '<span class="status-unhealthy">服务异常</span> '
                    if t.get("deploy_url"):
                        actions_html += f'<a href="{t["deploy_url"]}" target="_blank" class="deploy-url">{t["deploy_url"]}</a> '
                    actions_html += f'<button class="btn-success" hx-post="/tasks/{t["id"]}/deploy" hx-target="#task-list" hx-swap="outerHTML">重新部署</button>'
                elif ds == "deploy_failed":
                    actions_html += '<span class="status-failed">部署失败</span> '
                    actions_html += f'<button class="btn-success" hx-post="/tasks/{t["id"]}/deploy" hx-target="#task-list" hx-swap="outerHTML">重试</button>'
            actions_html += f'<button class="danger" hx-post="/tasks/{t["id"]}/remove" hx-target="#task-list" hx-swap="outerHTML" hx-confirm="确认移除任务 {t["id"]}?">删除</button>'
            actions_html += '</td>'
            lines.append(
                f'<tr>'
                f'<td><a href="/tasks/{t["id"]}">{t["id"]}</a></td>'
                f'<td>{stage_label}</td>'
                f'<td class="{status_class}">{t["status"]}</td>'
                f'<td>{t.get("updated_at", "")}</td>'
                f'{actions_html}'
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
