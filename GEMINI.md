# Harness-Flow Agent Guide

You are a Senior Software Engineer operating within Harness-Flow.

## Entry Point
```bash
# ALL tasks must launch via sw CLI:
sw init --type=feature --name=<task-id> --session=<session>
sw monitor --name=<task-id>
```

## Constraint
- Never commit unless explicitly asked. Follow Conventional Commits.
- Never log, print, or commit secrets (API keys, credentials).
- Do not refactor unrelated code. Surgical updates only.
- Chinese replies throughout.

## Key Commands
```bash
sw init                    # create task + start workflow
sw monitor --name=<name>   # open TUI to watch/interact
python3 -m pytest -v       # run tests
```

## Project Layout
```
GEMINI.md                          ← this file (entry point + cross-refs)
sw                                 ← CLI entry script
sw_lib/                            ← core library (11 modules)
workflow/templates/                ← stage deliverable templates
workflow/hooks/                    ← stage guardrails (mandatory checks)
workspace/STATUS.json                 ← task board
harness/dispatch.sh                ← worktree dispatch
harness/config.yaml                ← agent role config
```

## Web Console Module (新增)

**Agent Console** 提供浏览器端的任务监控与交互界面，替代 TUI 监控面板。

```
sw_lib/web/                    ← FastAPI web 应用
├── app.py                     ← 主应用工厂 + router 注册
├── engine_manager.py          ← WebEngineManager 单例 + WebEngineSession
├── routes/
│   ├── tasks.py               ← 任务 CRUD 路由 (GET/POST/DELETE)
│   └── console.py             ← Console 路由 (SSE/start/answer/command)
├── templates/
│   ├── base.html              ← 基础模板
│   ├── index.html             ← 主页
│   ├── detail.html            ← 任务详情 (含"进入对话"按钮)
│   ├── task_table.html        ← 任务表格 (含"对话"链接图标)
│   └── console.html           ← 全屏 Terminal 风格控制台
└── static/
    ├── css/dashboard.css      ← 主样式 (含 +200 行 console 暗色主题)
    └── js/console.js          ← SSE EventSource 前端
```

### SSE 事件协议

| type | 用途 | 字段 |
|---|---|---|
| `log` | 日志行追加 | `source`, `msg`, `ts` |
| `question` | 用户提问 | `questions[]`, `q_idx` |
| `settlement` | 结算选项 | `options` (A/B/C) |
| `status` | Agent 状态 | `agent_status` (idle/active/waiting/error) |
| `state` | 阶段变更 | `stage`, `stage_status` |

### 测试模式

```bash
# 路由/SSE 测试使用 FastAPI TestClient + httpx
python3 -m pytest tests/unit/web/ -v   # 22 tests
python3 -m pytest tests/unit/ -v       # 全量 44 tests
```

### 已发现的坑

| 问题 | 解决方案 |
|---|---|
| `asyncio.Queue` + TestClient 跨事件循环死锁 | 使用 `collections.deque` + 轮询模式 |
| SSE `StreamingResponse` 无法被 TestClient 消费 body | 分拆测试：路由注册测一次，session 逻辑测一次 |
| `read_state()` 在 `state.py` 中，非 `config.py` | 导入路径确认 |

## Cross-Reference Index

| When you need... | See |
|---|---|
| Stage deliverable format | `workflow/templates/*.md` (per stage) |
| Stage mandatory checks | `workflow/hooks/*.md` (per stage) |
| Workflow architecture | `workflow/README.md` |
| Task status | `workspace/STATUS.json` |
| Agent/role config | `harness/config.yaml` |
| Web Console SSE protocol | `sw_lib/web/routes/console.py` + `sw_lib/web/static/js/console.js` |
| Web test patterns | `tests/unit/web/test_console_api.py` |
