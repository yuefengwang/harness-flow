# One-Click Deploy for Finished Tasks — Design Spec

**Date:** 2026-05-21 | **Status:** Approved

## Overview

Add a "deploy" action for tasks that have completed all 5 stages (archive stage ⇒ `stage_status = "Finished"`). The deploy is agent-driven: the agent enters the project's `target_dir`, auto-detects the project type, and runs the appropriate startup command locally.

## Requirements

1. "部署" button appears in the task table ONLY for `stage_status == "Finished"` tasks
2. One-click POST triggers background agent execution in `target_dir`
3. Agent auto-detects project type (Dockerfile / package.json / pom.xml / requirements.txt) and runs the right startup command
4. Status tracked in `.state` as `deploy_status` field
5. Deploy progress visible in task table (button state changes) and task detail page (deploy log)

## UI Design

### Task Table (`_task_table_html` + `task_table.html`)

| deploy_status | Display |
|---|---|
| `"idle"` (default) | Green "部署" button: `<button class="btn-success" hx-post="/tasks/{id}/deploy" hx-target="#task-list" hx-swap="outerHTML">部署</button>` |
| `"deploying"` | Disabled status: `<span class="status-deploying">⏳ 部署中...</span>` |
| `"deployed"` | Success badge: `<span class="status-deployed">✅ 已部署</span>` `<a href="/tasks/{id}">日志</a>` |
| `"deploy_failed"` | Error + retry: `<span class="status-failed">❌ 部署失败</span>` `<button ...>重试</button>` |

For Finished tasks, the "推进" button is replaced by the deploy button/status.

### Task Detail Page (`detail.html`)

Append a deploy log section that reads `.deploy_log` content (max 5000 chars).

## Backend Design

### New Route

```
POST /tasks/{name}/deploy
```

**Handler flow:**
1. Read task `.state` → get `target_dir`, verify `stage_status == "Finished"`
2. If `deploy_status == "deploying"` → return 409 (already deploying)
3. Write `deploy_status = "deploying"` to `.state` and `STATUS.json`
4. Spawn background thread with `_run_deploy_agent(name, target_dir)`
5. Return `_task_table_html()` (shows "部署中..." state)

### Agent Execution

`_run_deploy_agent(name, target_dir)`:
1. Build deploy context with system prompt instructing the agent to:
   - Enter `target_dir`
   - List files to detect project type
   - Run the appropriate startup command
   - Report service URL/status on success
2. Determine agent type and model from `config.yaml` `stage_roles` (use `developer` role as fallback, which has `run_command` permission)
3. Create agent instance, inject context, start execution
4. Pipe agent output to `.deploy_log`
5. On completion: set `deploy_status = "deployed"` or `"deploy_failed"` in `.state` and `STATUS.json`

### State Fields (new in `.state`)

| Field | Type | Values |
|---|---|---|
| `deploy_status` | string | `"idle"` (default), `"deploying"`, `"deployed"`, `"deploy_failed"` |
| `deploy_at` | string | ISO timestamp of last deploy attempt |

### Files

| File | Purpose |
|---|---|
| `sw_lib/web/routes/tasks.py` | Add `deploy` route, update `_task_table_html()` with deploy button logic |
| `sw_lib/web/templates/task_table.html` | Add deploy button/status in Jinja2 template |
| `sw_lib/web/templates/detail.html` | Add deploy log section |
| `sw_lib/web/static/css/dashboard.css` | Styles for deploy status badges |
| `tests/unit/web/test_tasks_api.py` | Add deploy test cases |

## Error Handling

- Task not finished → 400 "任务未完成，无法部署"
- Already deploying → 409 "部署正在进行中"
- `target_dir` missing → 400 "项目目录不存在"
- Agent execution failure → `deploy_status = "deploy_failed"`, error logged to `.deploy_log`
- Trash collision on restore → existing behavior (blocked)

## Agent Context Template

```
## 部署任务

你是一个部署专家。请执行以下操作：

1. 进入项目目录: {target_dir}
2. 列出目录内容，检测项目类型（Dockerfile / docker-compose.yml / package.json / pom.xml / requirements.txt / go.mod 等）
3. 根据检测到的项目类型，选择合适的启动方式：
   - Docker: `docker-compose up -d` 或 `docker build && docker run`
   - Node.js: `npm install && npm start`
   - Python: `pip install -r requirements.txt && python app.py` 或 `uvicorn`
   - Java: `mvn spring-boot:run` 或 `java -jar target/*.jar`
   - Go: `go run .` 或 `go build && ./binary`
4. 执行启动命令
5. 确认服务是否成功启动（检查端口、进程、HTTP 响应等）
6. 报告最终结果：服务地址、端口、状态
```

## Testing

| Test | What it verifies |
|---|---|
| `test_deploy_on_finished_task` | Finished task → deploy starts, status changes to "deploying" |
| `test_deploy_on_unfinished_task` | Non-finished task → 400 error |
| `test_deploy_already_deploying` | Deploying task → 409 conflict |
| `test_deploy_missing_target_dir` | Missing target_dir → 400 error |
| `test_deploy_button_visible` | Finished task shows deploy button in table HTML |
| `test_deploy_button_hidden` | Non-finished task does NOT show deploy button |
