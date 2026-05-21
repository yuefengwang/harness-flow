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
import threading
from sw_lib.agents.base import AgentFactory
from sw_lib.core.config import resolve_agent_type, resolve_agent_model

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
    """后台线程：构建部署上下文 → 创建 Agent → 执行 → 记录结果"""
    from sw_lib.core.utils import now, sw_log
    from sw_lib.core.state import read_state, write_state

    deploy_log_path = TASKS / name / ".deploy_log"

    def log(msg: str):
        with open(deploy_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now()}] {msg}\n")
        sw_log(name, f"[deploy] {msg}", "deploy")

    try:
        log("开始部署...")
        agent_type = resolve_agent_type("03-coding")
        model_name = resolve_agent_model("03-coding")

        context = (
            "## 部署任务\n\n"
            "你是一个部署专家。请执行以下操作：\n\n"
            f"1. 进入项目目录: {target_dir}\n"
            "2. 列出目录内容，检测项目类型（Dockerfile / docker-compose.yml / package.json / pom.xml / requirements.txt / go.mod 等）\n"
            "3. 根据检测到的项目类型，选择合适的启动方式：\n"
            "   - Docker: `docker-compose up -d` 或 `docker build && docker run`\n"
            "   - Node.js: `npm install && npm start`\n"
            "   - Python: `pip install -r requirements.txt && python app.py` 或 `uvicorn`\n"
            "   - Java: `mvn spring-boot:run` 或 `java -jar target/*.jar`\n"
            "   - Go: `go run .` 或 `go build && ./binary`\n"
            "4. 执行启动命令\n"
            "5. 确认服务是否成功启动（检查端口、进程、HTTP 响应等）\n"
            "6. 报告最终结果：服务地址、端口、状态\n"
        )

        callbacks = {
            "add_log": lambda s, m: log(f"[{s}] {m}"),
            "is_running": lambda: True,
            "on_complete": lambda: None,
            "on_ask_user": lambda q, r: r.put([""] * len(q)),
        }

        agent = AgentFactory.create(agent_type, callbacks, name, "deploy", -1, model_name)
        log(f"Agent 已创建: {agent_type} / {model_name}")

        agent.start()
        if hasattr(agent, 'send'):
            agent.send(context, is_system=True)

        from sw_lib.agents.pty import PtyAgent
        if isinstance(agent, PtyAgent):
            import threading as _th
            _th.Thread(target=agent.reader_loop, daemon=True).start()

        log("Agent 已启动，等待完成...")
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
