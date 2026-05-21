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
from ...core.config import STAGES, STAGE_NAMES, TASKS, resolve_agent_type, resolve_agent_model
import threading
from sw_lib.agents.base import AgentFactory

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
        state = _service.get_task_state(name)
        idx = int(state.get("stage_idx", 0))
        done, todo = _service.validate_stage(name)
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
    threading.Thread(
        target=_run_deploy_agent,
        args=(name, target_dir),
        daemon=True,
    ).start()
    return HTMLResponse(content=_task_table_html())


def _run_deploy_agent(name: str, target_dir: str):
    from sw_lib.core.utils import now
    from sw_lib.core.config import TASKS
    deploy_log_path = TASKS / name / ".deploy_log"
    def log(msg):
        with open(deploy_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now()}] {msg}\n")
    try:
        log(f"开始部署: {target_dir}")
        agent_type = resolve_agent_type("03-coding")
        model_name = resolve_agent_model("03-coding")
        context = f"进入 {target_dir}，检测项目类型并启动服务。"
        callbacks = {"add_log": lambda s, m: log(f"[{s}] {m}"), "is_running": lambda: True, "on_complete": lambda: None, "on_ask_user": lambda q, r: r.put([""] * len(q))}
        agent = AgentFactory.create(agent_type, callbacks, name, "deploy", -1, model_name)
        agent.start()
        if hasattr(agent, 'send'):
            agent.send(context, is_system=True)
        from sw_lib.agents.pty import PtyAgent
        import threading as _th
        if isinstance(agent, PtyAgent):
            _th.Thread(target=agent.reader_loop, daemon=True).start()
        if hasattr(agent, 'wait'):
            agent.wait()
        log("部署完成")
        _service.complete_deploy(name, success=True)
    except Exception as e:
        log(f"部署失败: {e}")
        try:
            _service.complete_deploy(name, success=False)
        except Exception:
            pass


def _task_table_html() -> str:
    """渲染任务列表 HTML 片段"""
    data = _get_task_list_data()
    lines = []
    lines.append('<div id="task-list"'
                 ' hx-get="/tasks/table"'
                 ' hx-trigger="every 10s"'
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
            if t.get("status") == "Finished":
                ds = t.get("deploy_status", "idle")
                if ds == "idle":
                    actions_html += f'<button class="btn-success" hx-post="/tasks/{t["id"]}/deploy" hx-target="#task-list" hx-swap="outerHTML">部署</button>'
                elif ds == "deploying":
                    actions_html += '<span class="status-deploying">部署中...</span>'
                elif ds == "deployed":
                    actions_html += '<span class="status-deployed">已部署</span> '
                    actions_html += f'<a href="/tasks/{t["id"]}">日志</a>'
                elif ds == "deploy_failed":
                    actions_html += '<span class="status-failed">部署失败</span> '
                    actions_html += f'<button class="btn-success" hx-post="/tasks/{t["id"]}/deploy" hx-target="#task-list" hx-swap="outerHTML">重试</button>'
            else:
                actions_html += f'<button hx-post="/tasks/{t["id"]}/advance" hx-target="#task-list" hx-swap="outerHTML">推进</button>'
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
