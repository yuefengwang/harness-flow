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

### 5. Spec-Driven Collaboration (规约驱动与长效进展对齐)
- **核心理念**：意图为先，进展交接。代码服务于规约。
- **可验证目标**：启动时硬性对齐 `progress.txt` 中前一任进展与本次首要待办；开发时在 `02-Planning` 阶段完成用例与接口冻结；退出时在 `progress.txt` 提交高密度交接小结（不少于 50 字）作为门禁硬卡点，确立长会话开发的信任链条。

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
templates/                         ← stage deliverable templates
hooks/                             ← stage guardrails (mandatory checks)
workspace/STATUS.json              ← task board
bin/dispatch.sh                   ← worktree dispatch
config/config.yaml                 ← agent role config
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
