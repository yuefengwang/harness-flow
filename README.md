# 🚀 Harness-Flow: AI 驱动的标准化开发引擎

`Harness-Flow` 是一套高度结构化、状态驱动且可扩展的 AI Agent 编排框架。它将复杂的软件开发任务拆解为 5 个标准阶段，通过严密的 **合规性门禁 (Hooks)** 和现代化交互界面（**终端 TUI + Web Dashboard**），确保 AI 在可控、透明且高效的环境下完成开发工作。

---

## 🌟 核心特性

-   **🎯 阶段驱动工作流**：将开发生命周期标准化为 `头脑风暴 → 规划 → 编码 → 评审 → 归档`。
-   **🖥️ 双模交互界面**：基于 `Rich` 的终端监控面板 (TUI) + 基于 `FastAPI + HTMX` 的 Web Dashboard。
-   **🛡️ 强制性合规门禁**：每个阶段均设有前置 (Pre) 与后置 (Post) Hooks，严禁未经校验的非法推进。
-   **🔌 插件化架构**：
    -   **Agent 插件**：无缝切换 Gemini、OpenCode、PTY 或 Mock 模式。
    -   **工具插件**：基于类定义的原子工具箱，支持精细权限控制。
-   **🗑️ 回收站系统**：任务支持软删除到 `.trash`，随时恢复，避免误操作。
-   **⚡ 原子化交互**：全新设计的 `sw init` 向导，零参数自动进入交互模式，三步完成初始化。
- **🧪 工业级稳定性**：内置完善的 `MockAgent` 模拟器与覆盖率极高的自动化测试套件。
- **⚙️ 角色化配置**：通过 `config.yaml` 为每个阶段配置独立的 AI 角色、模型与工具权限。
- **🤖 Karpathy AI 编程准则**：深度集成 Karpathy 的四项核心准则，确保 AI 开发过程简洁、精准且目标明确。

---

## 🤖 Karpathy AI 编程准则 (已深度集成)

`Harness-Flow` 核心逻辑与 Hooks 已全面集成 Andrej Karpathy 的 AI 编程准则：

1. **Think Before Coding (三思而后行)**：在 Brainstorming 阶段强制通过结构化提问暴露假设与歧义，不带猜测进入开发。
2. **Simplicity First (简约至上)**：在 Coding 阶段通过 Hooks 限制过度设计，优先使用简洁方案，拒绝冗余抽象。
3. **Surgical Changes (外科手术式改动)**：在 Review 阶段严格审计 Diff，严禁改动无关代码，确保每次变更精准无误。
4. **Goal-Driven Execution (目标驱动执行)**：全程以测试结果为导向，通过“失败测试 -> 修复 -> 验证通过”的闭环确保交付质量。


---

## 📸 交互界面预览

### 终端监控面板 (Monitor TUI)

`sw monitor` 提供了一个专业的交互环境，让您实时掌控 Agent 的思考与执行：

```text
 🚀 Harness-Flow | my-feature-task | 01-brainstorming (gemini-2.0-flash) ● 连接中... 
 ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 对话日志 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
 ┃ [12:04:15] sw     | 启动 Agent (gemini-2.0-flash) — 01-brainstorming      ┃
 ┃ [12:04:16] sys    | 核心指令已通过 API System Instruction 注入完毕        ┃
 ┃ [12:04:18] agent  | 你好！我是你的需求分析专家。我已阅读了你的任务需求。  ┃
 ┃ [12:04:19] agent  | 在开始设计之前，我需要确认几个关键细节：              ┃
 ┃ [12:04:20] sw     | ❓ 收到 1 个结构化问题                                ┃
 ┃ [12:04:20] user   | [1] 需要，预留 i18n 接口                              ┃
 ┃ [12:04:21] agent  | 收到。我将据此制定支持多语言的设计方案。              ┃
 ┃ [12:04:22] sw     | ✨ 阶段产出已就绪。你可以继续交流，或输入 /advance 推进。 ┃
 ┃                                                                           ┃
 ┃                                  (按 ↑/↓ 滚动历史, 按 PageUp/Dn 快速翻页)  ┃
 ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
 ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
 ┃ 💬 输入消息或 /命令 (如 /advance, /status, /q)                            ┃
 ┃ 👉: /advance                                                              ┃
 ┃ ⚠️ 检测到 2 个待填项未完成，请完善后重试。                                ┃
 ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

### Web Dashboard

`sw dashboard` 启动浏览器端管理面板，支持任务 CRUD、Agent 实时对话、日志流式推送：

-   **任务总览**：HTMX 自动刷新，查看全部活跃任务与已移除任务。
-   **Agent 控制台**：SSE 实时流式日志、结构化问题回复、引擎启停管理。
-   **任务详情**：完整日志查看器，阶段推进操作。
-   **一键操作**：创建、推进、移除、恢复任务均在浏览器完成。

---

## 🛠️ 快速开始

### 1. 安装环境

确保您的环境已安装 Python 3.9+ 及依赖：

```bash
pip install pyyaml rich google-genai fastapi uvicorn
```

### 2. 配置凭证 (Credentials)

为了安全起见，敏感的 API Key 不会提交到仓库。请根据模板创建您的本地配置文件：

```bash
cp config/credentials-template.yaml config/credentials.yaml
# 然后编辑 credentials.yaml 填入您的 GOOGLE_API_KEY 等信息
```

> 🛡️ **安全提示**：`credentials.yaml` 已被列入 `.gitignore`。

### 3. 角色化配置 (可选)

编辑 `config/config.yaml` 可为每个开发阶段配置独立的 AI 角色与权限：

```yaml
harness:
  stage_roles:
    "01-brainstorming": "analyst"     # 需求分析
    "02-planning":     "architect"    # 系统架构
    "03-coding":       "developer"    # 编码实现
    "04-review":       "reviewer"     # 审计复核
    "05-archive":      "archivist"    # 文档归档
  roles:
    analyst:
      agent: opencode
      model: opencode/deepseek-v4-flash-free
      tools: [list_files, read_file]
```

### 4. 初始化一个新任务

运行交互式向导，只需三步：起名 → 选型 → 贴需求。确认后自动进入监控面板。

```bash
./sw init
```

也可通过命令行参数一键创建：

```bash
./sw init --type=feature --name=my-feature --context="实现用户登录模块"
```

### 5. 在监控面板中协作

在 `monitor` 中，您可以直接输入文本与 Agent 对话，或使用斜杠命令：

-   `/advance`：执行当前阶段校验并尝试推进入下一阶段。
-   `/status`：查看当前任务的详细状态。
-   `/context`：预览注入给 Agent 的完整上下文。
-   `/q`：保存并退出监控面板。

### 6. 启动 Web Dashboard

浏览器端管理面板，无需终端即可管理所有任务：

```bash
./sw dashboard
# 默认访问 http://127.0.0.1:8080
```

---

## 💻 CLI 命令参考

| 命令 | 说明 |
|:---|:---|
| `./sw init` | 创建新任务（零参数自动进入交互模式） |
| `./sw monitor --name=<id>` | 启动 TUI 监控面板 |
| `./sw dashboard` | 启动 Web Dashboard（FastAPI + HTMX） |
| `./sw status [--name=<id>]` | 查看任务状态 |
| `./sw advance [--name=<id>]` | 校验并推进到下一阶段 |
| `./sw resume --name=<id>` | 恢复并查看任务上下文 |
| `./sw list` | 列出所有活跃任务 |
| `./sw list --trash` | 查看回收站中的任务 |
| `./sw remove --name=<id>` | 将任务移入回收站（软删除） |
| `./sw restore --name=<id>` | 从回收站恢复任务 |
| `./sw answer --name=<id> --text=<reply>` | 以编程方式回复 Agent 问题 |

全局标志：`--yes/-y`（自动确认）、`--non-interactive`（非交互模式）

---

## 📂 项目架构

```text
harness-flow/
├── bin/                    # 🚀 入口脚本 (sw, dev-init.sh, dispatch.sh)
├── sw                      # 🚀 主入口 (Python CLI)
├── sw_lib/                 # 🧠 逻辑核心 (分层架构)
│   ├── agents/             # Agent 驱动 (Gemini, OpenCode, PTY, Mock)
│   ├── cli/                # 命令行解析与指令实现
│   ├── core/               # 服务、状态、配置模型
│   ├── runnable/           # 核心编排引擎 (LangGraph, StateGraph)
│   ├── prompts/            # YAML 驱动的提示词构建
│   ├── output/             # Pydantic 结构化产出解析
│   ├── tools/              # 插件化工具箱 (5 个原子工具)
│   ├── ui/                 # 终端交互界面 (Init UI & Monitor TUI)
│   └── web/                # Web Dashboard (FastAPI + HTMX + SSE)
├── config/                 # ⚙️ 全局配置与凭证
│   ├── config.yaml         # 角色化 Agent 配置
│   └── credentials.yaml    # API Key (本地, 不提交)
├── hooks/                  # 🛡️ 阶段合规性门禁脚本
├── templates/              # 📝 交付物标准 Markdown 模板
├── docs/                   # 📚 演化日志与文档
├── repo/                   # 📦 生成的目标代码仓库
├── workspace/              # 💾 动态数据 (任务、看板、回收站)
│   ├── tasks/              # 活动任务
│   │   └── .trash/         # 回收站 (软删除)
│   └── STATUS.json         # 任务状态总看板
└── tests/                  # 🧪 自动化测试套件
    ├── unit/               # 单元测试 (agents, core, tools, ui, web)
    ├── integration/        # 集成测试
    └── e2e/                # 端到端流测试
```

---

## 🤖 Agent 类型

`Harness-Flow` 支持 4 种 Agent 后端，通过 `config.yaml` 灵活切换：

| Agent | 适用场景 | 通信方式 |
|:---|:---|:---|
| **Gemini** | 生产环境，需要 Google AI 能力 | google-genai SDK (HTTP 流式) |
| **OpenCode** | 生产环境，使用 OpenCode/DeepSeek | CLI NDJSON 流式输出 |
| **PTY** | 本地命令行程序 | 伪终端 (PTY) |
| **Mock** | 自动化测试，无需外部 AI | 场景脚本模拟 |

每个阶段可独立配置使用的 Agent 类型与模型，详见 `config/config.yaml` 中的 `stage_roles` 配置。

---

## 📐 五大开发阶段

1.  **01-Brainstorming**：需求对齐。AI 引导用户澄清歧义，产出 Specs 文档。
2.  **02-Planning**：任务拆解。生成 WBS、测试计划与详细设计。
3.  **03-Coding**：纯粹执行。AI 编写业务代码，并确保单元测试通过。
4.  **04-Review**：审计复核。对代码变更进行多维度检查与影响分析。
5.  **05-Archive**：总结沉淀。生成任务简报，自动清理工作区并归档。

---

## 🛡️ 流程审计 (Hooks)

`Harness-Flow` 严禁不合规的开发行为。

-   **Soft Check**：检查 Markdown 模板中的 `[ ]` 是否全部勾选，`___` 是否全部填写。
-   **Hard Check**：执行 `hooks/check_XX.sh` 脚本进行物理环境验证（如编译检查、Lint 检查）。
-   **强制性**：`/advance` 指令会无条件执行上述检查，失败则拒绝推进。

---

## 🗑️ 回收站系统

任务移除不会真正删除数据，而是移入回收站：

```bash
./sw remove --name=my-task   # 移入 workspace/tasks/.trash/
./sw list --trash             # 查看已移除任务
./sw restore --name=my-task   # 恢复到活跃任务列表
```

任务的所有产物（`.state`、`.log`、阶段文档）均完整保留，恢复后可继续推进。

---

## 🔌 工具插件

AI Agent 通过插件化工具箱与系统交互，每个角色可配置允许使用的工具：

| 工具 | 功能 |
|:---|:---|
| `list_files` | 列出文件目录 |
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件 |
| `run_command` | 执行 Shell 命令 |
| `ask_user` | 向用户发起结构化提问 |

工具权限通过 `config.yaml` 中每个 `role` 的 `tools` 字段进行精细化控制。

---

## 🧪 开发者测试

我们拥有一套稳健的测试套件，确保重构与功能的零退化：

-   **单元测试**：`python3 -m pytest tests/unit`
-   **集成测试**：`python3 -m pytest tests/integration`
-   **端到端流测试**：`bash tests/e2e/test_e2e.sh`

`MockAgent` 支持全流程模拟，无需外部 AI 即可在 CI 中运行完整测试。

---

*Powered by Harness-Engineering. 让 AI 开发如外科手术般精准。*
