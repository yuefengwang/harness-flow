# One-Click Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one-click deploy for Finished tasks — agent enters target_dir, auto-detects project type, runs startup command.

**Architecture:** New `POST /tasks/{name}/deploy` route spawns background agent thread. Agent reads target_dir from task state, detects project type, runs startup command. UI shows deploy status via task table button state + 10s polling refresh.

**Tech Stack:** FastAPI, HTMX, existing Agent infrastructure (BaseAgent, AgentFactory), threading

**Files:**
- Create: (none new — inline deploy logic within existing files)
- Modify: `sw_lib/web/routes/tasks.py` — deploy route + `_task_table_html()` button logic
- Modify: `sw_lib/web/templates/task_table.html` — Jinja2 deploy button/status
- Modify: `sw_lib/web/templates/detail.html` — deploy log section
- Modify: `sw_lib/web/static/css/dashboard.css` — deploy status styles
- Modify: `sw_lib/core/service.py` — `deploy_task()` method + `list_tasks` add deploy_status
- Test: `tests/unit/web/test_tasks_api.py` — deploy test cases

---

### Task 1: Add `deploy_task()` to TaskService

**Files:**
- Modify: `sw_lib/core/service.py:327` (before `_service = TaskService()`)
- Modify: `sw_lib/core/service.py:156` (in `list_tasks`, add deploy_status to result dict)

- [ ] **Step 1: Add deploy_status to list_tasks result**

In `list_tasks()`, add `deploy_status` to the result dict after line 162 (`"updated_at": ...`):

```python
"deploy_status": st.get("deploy_status", "idle"),
```

- [ ] **Step 2: Add `deploy_task()` method**

Append before `_service = TaskService()` (line 327):

```python
    def deploy_task(self, name: str) -> Dict[str, Any]:
        """将任务标记为部署中并返回 target_dir"""
        st = self.get_task_state(name)
        if st.get("stage_status") != "Finished":
            raise TaskError("任务未完成，无法部署")
        if st.get("deploy_status") == "deploying":
            raise TaskError("部署正在进行中")
        target_dir = st.get("target_dir", "")
        if not target_dir or target_dir == ".":
            raise TaskError("项目目录未配置")
        from pathlib import Path
        if not Path(target_dir).is_dir():
            raise TaskError(f"项目目录不存在: {target_dir}")
        st["deploy_status"] = "deploying"
        st["deploy_at"] = now()
        st["updated_at"] = now()
        write_state(name, st)
        upsert_task_summary(name, deploy_status="deploying")
        return st

    def complete_deploy(self, name: str, success: bool):
        """标记部署完成或失败"""
        st = self.get_task_state(name)
        st["deploy_status"] = "deployed" if success else "deploy_failed"
        st["updated_at"] = now()
        write_state(name, st)
        upsert_task_summary(name, deploy_status=st["deploy_status"])
```

- [ ] **Step 3: Run existing tests to verify no regression**

```bash
python3 -m pytest tests/unit/core/ -q --tb=short
```

Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add sw_lib/core/service.py
git commit -m "feat: add deploy_task and complete_deploy to TaskService"
```

---

### Task 2: Add deploy route and agent execution

**Files:**
- Modify: `sw_lib/web/routes/tasks.py:132` (after restore route, before `_task_table_html`)

- [ ] **Step 1: Add imports for agent creation**

Add at top of `sw_lib/web/routes/tasks.py` after line 15:

```python
import threading
from sw_lib.agents.base import AgentFactory
from sw_lib.core.config import resolve_agent_type, resolve_agent_model, get_tools_for_stage
from sw_lib.core.engine import ContextBuilder
```

- [ ] **Step 2: Add deploy route**

Insert before `_task_table_html()` (after the restore route, line 132):

```python
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
        agent_type = resolve_agent_type("03-coding")  # use developer role
        model_name = resolve_agent_model("03-coding")

        context = (
            f"## 部署任务\n\n"
            f"你是一个部署专家。请执行以下操作：\n\n"
            f"1. 进入项目目录: {target_dir}\n"
            f"2. 列出目录内容，检测项目类型（Dockerfile / docker-compose.yml / package.json / pom.xml / requirements.txt / go.mod 等）\n"
            f"3. 根据检测到的项目类型，选择合适的启动方式：\n"
            f"   - Docker: `docker-compose up -d` 或 `docker build && docker run`\n"
            f"   - Node.js: `npm install && npm start`\n"
            f"   - Python: `pip install -r requirements.txt && python app.py` 或 `uvicorn`\n"
            f"   - Java: `mvn spring-boot:run` 或 `java -jar target/*.jar`\n"
            f"   - Go: `go run .` 或 `go build && ./binary`\n"
            f"4. 执行启动命令\n"
            f"5. 确认服务是否成功启动（检查端口、进程、HTTP 响应等）\n"
            f"6. 报告最终结果：服务地址、端口、状态\n"
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

        from ..agents.pty import PtyAgent
        import threading as _th
        if isinstance(agent, PtyAgent):
            _th.Thread(target=agent.reader_loop, daemon=True).start()

        log("Agent 已启动，等待完成...")
        agent.wait() if hasattr(agent, 'wait') else None
        log("部署完成")

        _service.complete_deploy(name, success=True)

    except Exception as e:
        log(f"部署失败: {e}")
        try:
            _service.complete_deploy(name, success=False)
        except Exception:
            pass
```

- [ ] **Step 3: Run tests to verify imports and route registration**

```bash
python3 -c "from sw_lib.web.app import create_app; app = create_app(); print([r.path for r in app.routes if 'deploy' in r.path])"
```

Expected: `['/tasks/{name}/deploy']`

- [ ] **Step 4: Commit**

```bash
git add sw_lib/web/routes/tasks.py
git commit -m "feat: add POST /tasks/{name}/deploy route with agent execution"
```

---

### Task 3: Update task table HTML with deploy button/status

**Files:**
- Modify: `sw_lib/web/routes/tasks.py:148-162` (`_task_table_html()` actions column)
- Modify: `sw_lib/web/templates/task_table.html:21-31` (actions column)

- [ ] **Step 1: Update `_task_table_html()` actions column**

Replace the actions cell (lines 157-160) with deploy-aware logic:

```python
                f'<td class="actions">'
if t.get("status") == "Finished":
    ds = t.get("deploy_status", "idle")
    if ds == "idle":
        lines[-1] += f'<button class="btn-success" hx-post="/tasks/{t["id"]}/deploy" hx-target="#task-list" hx-swap="outerHTML">部署</button>'
    elif ds == "deploying":
        lines[-1] += f'<span class="status-deploying">部署中...</span>'
    elif ds == "deployed":
        lines[-1] += f'<span class="status-deployed">已部署</span> <a href="/tasks/{t["id"]}">日志</a>'
    elif ds == "deploy_failed":
        lines[-1] += f'<span class="status-failed">部署失败</span>'
        lines[-1] += f'<button class="btn-success" hx-post="/tasks/{t["id"]}/deploy" hx-target="#task-list" hx-swap="outerHTML">重试</button>'
else:
    lines[-1] += f'<button hx-post="/tasks/{t["id"]}/advance" hx-target="#task-list" hx-swap="outerHTML">推进</button>'
lines[-1] += f'<button class="danger" hx-post="/tasks/{t["id"]}/remove" hx-target="#task-list" hx-swap="outerHTML" hx-confirm="确认移除任务 {t["id"]}?">删除</button>'
                f'</td>'
```

- [ ] **Step 2: Update `task_table.html` Jinja2 template**

Replace the actions cell in `templates/task_table.html` (lines 21-31):

```html
                <td class="actions">
                    <a href="/tasks/{{ t.id }}/console" class="btn">对话</a>
                    {% if t.status == "Finished" %}
                        {% if t.deploy_status == "idle" %}
                        <button class="btn-success" hx-post="/tasks/{{ t.id }}/deploy"
                                hx-target="#task-list" hx-swap="outerHTML">部署</button>
                        {% elif t.deploy_status == "deploying" %}
                        <span class="status-deploying">部署中...</span>
                        {% elif t.deploy_status == "deployed" %}
                        <span class="status-deployed">已部署</span>
                        <a href="/tasks/{{ t.id }}">日志</a>
                        {% elif t.deploy_status == "deploy_failed" %}
                        <span class="status-failed">部署失败</span>
                        <button class="btn-success" hx-post="/tasks/{{ t.id }}/deploy"
                                hx-target="#task-list" hx-swap="outerHTML">重试</button>
                        {% endif %}
                    {% else %}
                    <button hx-post="/tasks/{{ t.id }}/advance"
                            hx-target="#task-list" hx-swap="outerHTML">推进</button>
                    {% endif %}
                    <button class="danger"
                            hx-post="/tasks/{{ t.id }}/remove"
                            hx-target="#task-list"
                            hx-swap="outerHTML"
                            hx-confirm="确认移除任务 {{ t.id }}?">删除</button>
                </td>
```

- [ ] **Step 3: Run tasks API tests to verify table HTML**

```bash
python3 -m pytest tests/unit/web/test_tasks_api.py -q --tb=short
```

Expected: all 19 pass

- [ ] **Step 4: Commit**

```bash
git add sw_lib/web/routes/tasks.py sw_lib/web/templates/task_table.html
git commit -m "feat: add deploy button/status to task table for Finished tasks"
```

---

### Task 4: Add deploy log to task detail page

**Files:**
- Modify: `sw_lib/web/templates/detail.html`

- [ ] **Step 1: Read template, add deploy log section**

Read `detail.html`, then append a deploy log section before `{% endblock %}`:

```html
{% if task.get("deploy_status") and task.deploy_status != "idle" %}
<div class="deploy-section">
    <h3>部署日志</h3>
    <pre class="log-viewer">{{ deploy_log or "暂无日志" }}</pre>
</div>
{% endif %}
```

- [ ] **Step 2: Update `task_detail` route to pass deploy_log**

In `sw_lib/web/routes/tasks.py`, after line 73 (reading `.log`), add:

```python
    deploy_log = ""
    deploy_log_file = task_dir / ".deploy_log"
    if deploy_log_file.exists():
        deploy_log = deploy_log_file.read_text(encoding="utf-8")[-5000:]
```

And add `"deploy_log": deploy_log` to the template context dict on line 75.

- [ ] **Step 3: Commit**

```bash
git add sw_lib/web/routes/tasks.py sw_lib/web/templates/detail.html
git commit -m "feat: add deploy log section to task detail page"
```

---

### Task 5: Add CSS styles for deploy status

**Files:**
- Modify: `sw_lib/web/static/css/dashboard.css`

- [ ] **Step 1: Add deploy status styles**

Append at end of `dashboard.css`:

```css
/* Deploy status */
.status-deploying { color: var(--warning); font-style: italic; }
.status-deployed { color: var(--success); font-weight: bold; }
.status-failed { color: var(--danger); }
.btn-success {
    background: var(--success);
    color: #fff;
    border: none;
    padding: 0.3em 0.8em;
    border-radius: 4px;
    cursor: pointer;
}
.btn-success:hover { opacity: 0.85; }
.deploy-section { margin-top: 2em; }
.deploy-section h3 { margin-bottom: 0.5em; }
```

- [ ] **Step 2: Commit**

```bash
git add sw_lib/web/static/css/dashboard.css
git commit -m "style: add deploy status CSS styles"
```

---

### Task 6: Add deploy test cases

**Files:**
- Modify: `tests/unit/web/test_tasks_api.py` (append at end)

- [ ] **Step 1: Write deploy test class**

Append these test cases after `test_full_lifecycle_smoke`:

```python
# ── Deploy Tests ──

class TestDeploy:
    """部署功能测试"""

    @pytest.fixture(autouse=True)
    def setup_finished_task(self):
        name = "web-deploy-test"
        task_dir = TASKS / name
        trash_dir = TASKS / ".trash" / name
        for d in [task_dir, trash_dir]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        # 创建已完成的 task
        target = TASKS.parent / "repo" / name
        target.mkdir(parents=True, exist_ok=True)
        _service.create_task(name, task_type="feature", target_dir=str(target))
        # 直接设为 Finished
        st = read_state(name)
        st["stage_status"] = "Finished"
        st["deploy_status"] = "idle"
        write_state(name, st)
        from sw_lib.core.state import upsert_task_summary
        upsert_task_summary(name, stage_status="Finished", deploy_status="idle")
        yield name
        for d in [task_dir, trash_dir, target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def test_deploy_on_finished_task(self, setup_finished_task, client):
        name = setup_finished_task
        resp = client.post(f"/tasks/{name}/deploy")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/html" in ct
        st = read_state(name)
        assert st["deploy_status"] == "deploying"
        assert "部署中" in resp.text or "deploying" in resp.text.lower()

    def test_deploy_on_unfinished_task(self, client):
        # 创建非 Finished 任务
        name = "web-deploy-unfinished"
        task_dir = TASKS / name
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        try:
            _service.create_task(name, task_type="feature")
            resp = client.post(f"/tasks/{name}/deploy")
            assert resp.status_code == 400
            assert "未完成" in resp.text or "无法" in resp.text
        finally:
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    def test_deploy_already_deploying(self, setup_finished_task, client):
        name = setup_finished_task
        # First deploy
        client.post(f"/tasks/{name}/deploy")
        # Second deploy should be blocked
        resp = client.post(f"/tasks/{name}/deploy")
        assert resp.status_code == 400
        assert "正在" in resp.text or "进行中" in resp.text

    def test_deploy_missing_target_dir(self, setup_finished_task, client):
        name = setup_finished_task
        # Remove target_dir
        st = read_state(name)
        target = Path(st.get("target_dir", ""))
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        st["target_dir"] = "/nonexistent/path/xyz"
        write_state(name, st)
        resp = client.post(f"/tasks/{name}/deploy")
        assert resp.status_code == 400
        assert "不存在" in resp.text or "未配置" in resp.text

    def test_deploy_button_visible_in_table(self, setup_finished_task, client):
        name = setup_finished_task
        resp = client.get("/tasks/table")
        assert resp.status_code == 200
        assert "部署" in resp.text
        assert f'hx-post="/tasks/{name}/deploy"' in resp.text

    def test_deploy_button_hidden_for_non_finished(self, client):
        name = "web-deploy-pending"
        task_dir = TASKS / name
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        try:
            _service.create_task(name, task_type="feature")
            resp = client.get("/tasks/table")
            assert 'hx-post="/tasks/' + name + '/deploy"' not in resp.text
            assert "推进" in resp.text
        finally:
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    def test_deploy_after_archive_marks_finished(self, client):
        """完整流程: 创建 → 推进所有阶段 → 部署"""
        name = "web-deploy-full-flow"
        task_dir = TASKS / name
        trash_dir = TASKS / ".trash" / name
        for d in [task_dir, trash_dir]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        try:
            target = TASKS.parent / "repo" / name
            target.mkdir(parents=True, exist_ok=True)
            _service.create_task(name, task_type="feature", target_dir=str(target))
            # 推进所有阶段
            from sw_lib.core.config import STAGES
            for stage in STAGES:
                tpl_file = TASKS / name / f"{stage}.md"
                tpl_file.write_text(f"## Gate\n- [x] Done\n", encoding="utf-8")
                client.post(f"/tasks/{name}/advance")
            # 验证 Finished
            st = read_state(name)
            assert st["stage_status"] == "Finished"
            # 部署
            resp = client.post(f"/tasks/{name}/deploy")
            assert resp.status_code == 200
            assert "部署中" in resp.text or "deploying" in resp.text.lower()
        finally:
            for d in [task_dir, trash_dir, target]:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 2: Add missing import**

Add at top of test file after line 6:
```python
import json
```

- [ ] **Step 3: Run deploy tests**

```bash
python3 -m pytest tests/unit/web/test_tasks_api.py -k "deploy" -v --tb=short
```

Expected: 7 passed (test_deploy_button_visible, test_deploy_button_hidden, test_deploy_on_finished_task, test_deploy_on_unfinished_task, test_deploy_already_deploying, test_deploy_missing_target_dir, test_deploy_after_archive_marks_finished)

- [ ] **Step 4: Run full test suite**

```bash
python3 -m pytest tests/unit/ -q --tb=short
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tests/unit/web/test_tasks_api.py
git commit -m "test: add deploy test cases (7 new tests)"
```
