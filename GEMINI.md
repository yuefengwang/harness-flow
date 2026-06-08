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
- Do not refactor unrelated code. Surgical updates only (遵循 Karpathy 准则).
- Chinese replies throughout.

## Karpathy AI Coding Principles (核心准则)

本项目遵循 Andrej Karpathy 提倡的 AI 编程四项核心准则，以减少过拟合与过度设计：

### 1. Think Before Coding (三思而后行)
- **核心理念**：不假设，不隐藏疑惑。
- **行动计划**：在编码前明确所有假设，如有模糊点必须询问澄清。
- **权衡分析**：提供多种实现方案并分析优劣，而非盲目选择。

### 2. Simplicity First (简约至上)
- **核心理念**：用最少的代码解决问题，拒绝过度设计。
- **范围控制**：严格限制在需求范围内，不添加任何推测性的功能或抽象。
- **重构导向**：如果 50 行代码能解决 200 行的事，务必重写。

### 3. Surgical Changes (外科手术式改动)
- **核心理念**：只动必须动的地方，只清理自己产生的垃圾。
- **风格对齐**：严格匹配现有代码风格，即使你不喜欢。
- **零干扰**：不改动无关代码、注释或格式。清理因你改动而产生的孤立变量/导入。

### 4. Goal-Driven Execution (目标驱动执行)
- **核心理念**：定义成功标准，循环验证直至达成。
- **可验证目标**：将模糊任务转化为可测试的结果（如：编写失败测试 -> 修复 -> 测试通过）。
- **迭代闭环**：通过强有力的成功标准实现自主迭代，避免陷入“让它工作”的模糊循环。

## Key Commands
```bash
sw init                    # create task + start workflow
sw monitor --name=<name>   # open TUI to watch/interact
python3 -m pytest -v       # run tests
```

## Project Layout
```
GEMINI.md                          ← this file (entry point + cross-refs)
bin/sw                             ← CLI entry script
sw_lib/                            ← core library (11 modules)
  runnable/                        ← LangGraph engine & nodes
  prompts/                         ← ChatPromptTemplates & YAMLs
  output/                          ← Pydantic Output Parsers
templates/                         ← stage deliverable templates
hooks/                             ← stage guardrails (mandatory checks)
workspace/STATUS.json              ← task board
bin/dispatch.sh                   ← worktree dispatch
config/config.yaml                 ← agent role config
```

## Workflow Architecture (LangGraph)

系统已升级为基于 **LangGraph** 的响应式状态机架构。

*   **WorkflowRuntime**: 全局单例，管理 `StateGraph` 的生命周期。
*   **WorkflowState**: 强类型全局状态，包含 `history_outputs` 和 `last_output`。
*   **FileCheckpointSaver**: 实现磁盘持久化，支持任务断点续传（通过 thread_id）。
*   **StageRunnable**: 标准执行单元，封装了 Prompt -> Agent -> Parser -> Gate -> Save 流程。

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

## Generated Project Pattern: Spring Boot + Hexagonal (repo/cccc)

A reference example of a greenfield Spring Boot 3 project generated under `repo/`. Useful as a template for future Java code generation tasks.

**Project**: `repo/cccc/` — Hello World Web Service
**Stack**: Java 21 + Spring Boot 3.3.6 + Maven + JUnit 5
**Architecture**: Hexagonal (Domain → Application Port → Application Service → Infrastructure)

```
repo/cccc/
├── pom.xml                              # Spring Boot 3.3.6 parent, Java 21
├── Dockerfile                           # Multi-stage: maven:3.9-eclipse-temurin-21 build, eclipse-temurin:21-jre runtime
├── src/main/java/com/harnessflow/cccc/
│   ├── CcccApplication.java             # @SpringBootApplication entry
│   ├── domain/model/Greeting.java       # Domain record (zero Spring imports)
│   ├── application/port/in/GreetingUseCase.java  # Input port interface
│   ├── application/service/GreetingService.java  # Pure logic use case impl
│   └── infrastructure/
│       ├── config/GreetingBeanConfig.java        # @Configuration to wire domain beans
│       └── web/GreetingController.java           # @RestController (constructor injection)
├── src/main/resources/application.yml
└── src/test/java/com/harnessflow/cccc/
    ├── CcccApplicationTests.java        # @SpringBootTest integration (3 tests)
    └── application/service/GreetingServiceTest.java  # Pure unit test (4 tests)
```

**Key decisions**:
- Domain/Application layers MUST have **zero Spring imports**; beans are registered via Infrastructure `@Configuration`
- Use Java `record` for immutable domain models
- Constructor injection only (no `@Autowired`)
- Multi-stage Dockerfile produces slim JRE image
- Test strategy: unit test for pure logic + `@SpringBootTest(webEnvironment = RANDOM_PORT)` for integration

**Validation commands**:
```bash
cd repo/cccc && mvn clean verify     # compile + test + package
cd repo/cccc && docker build -t cccc-hello .  # (requires Docker Hub access)
```

## Generated Project Pattern: React + FastAPI Mall App (repo/test)

A reference example of a greenfield full-stack web application generated under `repo/`. Useful as a template for future JS+Python code generation tasks.

**Project**: `repo/test/` — Mall (E-commerce App)
**Stack**: React 18 + React Router 6 + Vite (frontend) | Python FastAPI (backend) | Vitest + React Testing Library + pytest (testing)
**Architecture**: Minimal single-file backend + component-based frontend with Context API

```
repo/test/
├── backend/
│   ├── main.py               # FastAPI 单文件: 10 mock products + GET /api/products + GET /api/products/{id}
│   ├── requirements.txt       # fastapi, uvicorn (+ pytest, httpx for test)
│   └── test_main.py           # pytest: 4 tests (list, detail, 404, product shape)
├── frontend/
│   ├── package.json           # Vite + React Router 6 + Vitest + @testing-library/react
│   ├── vite.config.js         # proxy /api → localhost:8000
│   ├── index.html
│   └── src/
│       ├── main.jsx           # App entry
│       ├── App.jsx            # React Router: / → HomePage, /product/:id → ProductDetail
│       ├── App.css            # Global + component styles (~500 lines, responsive grid)
│       ├── context/
│       │   └── CartContext.jsx # React Context + useReducer + localStorage persistence
│       ├── components/
│       │   ├── Navbar.jsx     # Logo + cart icon + badge count
│       │   ├── ProductCard.jsx # Image, name, price, "Add to cart" button
│       │   ├── ProductGrid.jsx # 3-column responsive CSS grid
│       │   └── CartDrawer.jsx # Side drawer: items, qty ±, total, remove
│       ├── pages/
│       │   ├── HomePage.jsx   # Fetch products → ProductGrid
│       │   └── ProductDetail.jsx # Fetch single product + qty selector
│       └── __tests__/
│           └── CartContext.test.jsx # Vitest: 9 tests (add/remove/qty/clear/persistence)
└── README.md
```

**Key decisions**:
- Backend: Single `main.py` for rapid prototyping (方案 A / minimal architecture)
- Frontend: `React Context + useReducer` for state (no Redux/Zustand)
- Cart persistence: `localStorage` read/write on every state change (simple, sufficient)
- Styling: Pure CSS (no Tailwind/MUI) to keep deps minimal
- CORS: Locked to `http://localhost:5173` via FastAPI CORSMiddleware
- `node_modules/` and `dist/` artifacts: `dist/` always removed before archiving; `node_modules/` kept in dev

**Validation commands**:
```bash
cd repo/test/backend && python -m pytest -v     # 4 tests
cd repo/test/frontend && npm test                # 9 tests
cd repo/test/frontend && npm run build           # production build (verify success)
```

## Pytest Patterns & Test Infrastructure

**Conftest fixtures** (`tests/conftest.py`):
- `dummy_task`: Creates a temporary task `pytest-dummy-task` under `workspace/tasks/`, writes initial state, yields name for test, then `shutil.rmtree` cleanup. Used by multiple test modules.
- `agent_callbacks`: Provides no-op callbacks (`add_log`, `is_running`) for agent testing.

**Test structure** (`tests/`):
```
tests/
├── conftest.py              # shared fixtures (dummy_task, agent_callbacks)
├── unit/                    # unit tests (agents, core, tools, ui, web)
│   ├── agents/              # test_lifecycle, test_opencode, test_pty
│   ├── core/                # test_config, test_engine, test_routing
│   ├── tools/               # test_toolbox
│   ├── ui/                  # test_tui_utils
│   └── web/                 # test_console_api, test_engine_manager, test_tasks_api
├── integration/             # integration tests (test_sw_cli)
└── e2e/                     # end-to-end tests (test_e2e.sh)
```

**Key patterns discovered**:
- `MockAgent` enables full flow simulation without external AI — essential for CI
- Web tests use `FastAPI TestClient` with `collections.deque`-based SSE polling (avoid `asyncio.Queue` cross-event-loop deadlock)
- Task cleanup uses `shutil.rmtree` on teardown — safe because task dirs contain no git history
- `repo/` target dirs must be cleaned in test teardown to avoid cross-test contamination

**Known pitfalls**:
- `asyncio.Queue` + TestClient = cross-event-loop deadlock → use `collections.deque` + polling
- `read_state()` lives in `sw_lib/core/state.py`, not `config.py` — wrong import is a common mistake
- `repo/` directories for dummy tasks accumulate over time — must clean in `pytest`/`module`/`session` scoped fixtures with `autouse=True`

## Generated Project Pattern: Java HelloWorld Maven (repo/helloworld)

A reference example of a minimal Java CLI application generated under `repo/`. Useful as a lightweight Java Maven template.

**Project**: `repo/helloworld/` — Hello World CLI
**Stack**: Java 25 + Maven 3.9 + JAR packaging
**Architecture**: Single-class CLI with optional name argument

```
repo/helloworld/
├── pom.xml                              # Maven 构建 (JAR + shade plugin)
└── src/main/java/com/helloworld/
    └── App.java                         # 主类 (12 lines)
```

**Key decisions**:
- Single `App.java` with `main(String[] args)` — reads first arg or defaults to `"World"`
- Maven `maven-jar-plugin` with `mainClass` configured for executable JAR
- Java 25 (latest LTS-aligned version available on macOS)
- No dependencies beyond JDK — zero external jars, minimal footprint
- Package: `mvn package` → JAR at `target/helloworld-1.0.0.jar`

**Validation commands**:
```bash
cd repo/helloworld && mvn clean verify     # compile + test + package
java -jar repo/helloworld/target/helloworld-1.0.0.jar         # → Hello, World!
java -jar repo/helloworld/target/helloworld-1.0.0.jar Harness # → Hello, Harness!
```

**Lessons learned**:
- Java 25 `java --version` output format differs from Java 21 (2021 vs 2025 era), but Maven handles both identically
- `mvn package` creates JAR with manifest; `mvn compile` alone is insufficient for `java -jar`
- Build artifacts (`target/`) must be cleaned before archiving to avoid stale state

## Generated Project Pattern: HealthMonitor (repo/optimize-deploy)

A reference example of a deployment health monitoring module with auto-recovery and circuit breaker. Produced under `repo/optimize-deploy/`.

**Module**: `sw_lib/core/health.py` — HealthMonitor for post-deploy service health
**Stack**: Python 3.9+ (stdlib only: socket, threading, os, time, dataclasses)

```
sw_lib/core/
├── health.py              ← HealthMonitor + HealthConfig (256 lines)
tests/unit/core/
└── test_health_monitor.py ← 53 tests (full coverage)
```

**Architecture**:
- `HealthConfig` dataclass: `enabled`, `check_interval`, `failure_threshold`, `auto_redeploy`, `max_redeploys`, `redeploy_window_sec`
- `HealthMonitor` class: `run()` blocking loop, `stop()` via `threading.Event`, `_check_tcp()`, `_check_pid()`, `_trigger_redeploy()`, `_redeploy()`, `_check_circuit_breaker()`
- Integration: `DeployOrchestrator` for actual redeploy, `stop_tunnel` for tunnel cleanup, task state for status persistence

**Key decisions**:
- Dual health check: TCP port listen + PID process alive (`os.kill(pid, 0)`) — two independent checks
- Circuit breaker: timestamp-based window (default 5 redeploys in 300s), simple list cleanup O(n) where n ≤ 5
- Port reuse: `socket.bind(preferred)` to test, fallback to `bind(0)` for random port
- `threading.Event.wait(timeout)` for interruptible sleep instead of `time.sleep()`
- All redeploy exceptions caught — tunnel failure, process kill failure, deploy failure — never blocks the chain
- Auto/Manual mode via `health_config.auto_redeploy` field in task `.state` file

**Test patterns**:
- `timeout > my_monitor.config.check_interval` in `run()` / `stop()` race tests
- Mock `socket.create_connection` for TCP failure scenarios
- Mock `os.kill` for PID check failure scenarios
- Mock `time.time` for circuit breaker window tests (avoid real sleep)
- `stop_event.wait()` as synchronization point

**Validation commands**:
```bash
python3 -m pytest tests/unit/core/test_health_monitor.py -v   # 53 tests
python3 -m pytest tests/unit/core/ -v                         # no regression (82 tests)
```

## Generated Project Pattern: CLI Note Manager (repo/test-app)

A reference example of a zero-dependency Python CLI application with layered architecture and comprehensive testing — produced by the `e2e-1780569694` task. **Replaces earlier `e2e-test-1780559048` iteration** with cleaner `src/` layout and standard `pyproject.toml`-based packaging.

**Project**: `repo/test-app/` — CLI Note Manager
**Stack**: Python 3.10+ (stdlib only: argparse, json, dataclasses, uuid, pathlib, datetime) + pytest
**Architecture**: 3-layer: `models.py` (data) → `storage.py` (JSON CRUD) → `cli.py` (argparse dispatch)

```
repo/test-app/
├── pyproject.toml            # setuptools build + [project.scripts] notes = "notes.cli:main"
├── src/notes/
│   ├── __init__.py           # exports Note, NotesStore
│   ├── models.py             # Note dataclass (id, title, content, tags, timestamps)
│   ├── storage.py            # NotesStore: JSON CRUD + search with atomic writes
│   └── cli.py                # argparse CLI (add/list/delete/search)
└── tests/
    ├── __init__.py           # empty
    ├── test_models.py        # 3 tests — creation, tags, id uniqueness
    ├── test_storage.py       # 9 tests — CRUD, search, persistence, empty store
    └── test_cli.py           # 8 tests — integration via tmp_path + NOTEFILE env
```

**CLI commands**:
```
notes add <title> <content> [-t tag [tag ...]]
notes list
notes delete <id>
notes search [query] [-t tag]
```

**Key decisions**:
- Zero external runtime dependencies — pure stdlib means no `pip install` for production use
- `src/` layout with `[tool.setuptools.packages.find] where = ["src"]` — standard modern Python packaging
- `Note` dataclass with `field(default_factory=...)` for mutable defaults (tags, id, timestamps)
- `NOTES_FILE` env var for test isolation (no mocking of `Path.home()` needed) — cleaner than `unittest.mock.patch`
- `NotesStore.search()` uses case-insensitive substring match on title+content + optional tag filter
- Atomic file writes: write to `.tmp`, then `os.replace(tmp, target)` — prevents partial writes on crash
- UUID hex[:8] for short 8-char IDs (good enough uniqueness for a personal CLI tool)
- `NotesStore._note_to_dict()` / `_dict_to_note()` for explicit datetime serialization via `.isoformat()` / `fromisoformat()`
- Default storage path: `~/.notes/notes.json` via `DEFAULT_NOTES_DIR`
- `_get_notes_path()` reads env `NOTES_FILE` first, falls back to default
- `try/finally` cleanup pattern in CLI tests to avoid `NOTES_FILE` env var leaking between tests
- Output format: `<id>  <title> [tags]` with timestamp + content preview on subsequent lines

**Test patterns**:
- `tmp_path` fixture for isolated storage per test — no mocks needed for `NotesStore`
- `NOTES_FILE` env var + `tmp_path` for CLI integration tests (avoids `unittest.mock.patch`)
- `capsys` fixture for CLI output assertions (not used in current tests — just return codes, but available)
- 20 tests total (3 model + 9 storage + 8 CLI), all passing in ~0.06s
- Edge cases: empty store, missing note ID (exit code 1), file persistence verified via `json.loads()`

**Validation commands**:
```bash
cd repo/test-app && python3 -m pytest tests/ -v   # 20 tests
cd repo/test-app && python3 -m notes add "Hello" --body "world"
cd repo/test-app && python3 -m notes list
```

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
