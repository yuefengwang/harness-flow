# 🚀 Harness-Flow: AI 驱动的标准化开发引擎

`Harness-Flow` 是一套高度结构化、状态驱动且可扩展的 AI Agent 编排框架。它将复杂的软件开发任务拆解为 5 个标准阶段，通过严密的 **合规性门禁 (Hooks)** 和 **现代化终端交互 (TUI)**，确保 AI 在可控、透明且高效的环境下完成开发工作。

---

## 🌟 核心特性

-   **🎯 阶段驱动工作流**：将开发生命周期标准化为 `头脑风暴 → 规划 → 编码 → 评审 → 归档`。
-   **🖥️ 沉浸式终端面板 (TUI)**：基于 `Rich` 打造的实时监控界面，支持日志自动聚焦、手动滚动及结构化问答。
-   **🛡️ 强制性合规门禁**：每个阶段均设有前置 (Pre) 与后置 (Post) Hooks，严禁未经校验的非法推进。
-   **🔌 插件化架构**：
    -   **Agent 插件**：无缝切换 Gemini、OpenCode 或本地 PTY 模式。
    -   **工具插件**：基于类定义的原子工具箱，支持精细权限控制。
-   **⚡ 原子化交互**：全新设计的 `sw init` 向导，实现“三步进场，初始化即入场”。
-   **🧪 工业级稳定性**：内置完善的 `MockAgent` 模拟器与覆盖率极高的自动化测试套件。

---

## 📸 监控面板预览 (Monitor TUI)

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

---

## 🛠️ 快速开始

### 1. 安装环境
确保您的环境已安装 Python 3.9+ 及依赖：
```bash
pip install pyyaml rich google-genai
```

### 2. 配置凭证 (Credentials)
为了安全起见，敏感的 API Key 不会提交到仓库。请根据模板创建您的本地配置文件：
```bash
cp config/credentials-template.yaml config/credentials.yaml
# 然后编辑 credentials.yaml 填入您的 GOOGLE_API_KEY 等信息
```

> 🛡️ **安全提示**：`credentials.yaml` 已被列入 `.gitignore`。如果您在 IDE 中仍能看到该文件出现在待提交列表，请在终端执行 `git rm --cached config/credentials.yaml` 并提交，然后刷新 IDE 的 Git 插件缓存。

### 3. 初始化一个新任务
运行交互式向导，只需三步：起名、选型、贴需求。确认后会自动进入监控。
```bash
./sw init
```

### 4. 在监控面板中协作
在 `monitor` 中，您可以直接输入文本与 Agent 对话，或使用斜杠命令：
-   `/advance`：执行当前阶段校验并尝试推进入下一阶段。
-   `/status`：查看当前任务的详细状态。
-   `/context`：预览注入给 Agent 的完整上下文。
-   `/q`：保存并退出监控面板。

---

## 📂 项目架构

```text
harness-flow/
├── bin/                    # 🚀 入口脚本 (sw, dev-init.sh)
├── sw_lib/                 # 🧠 逻辑核心 (分层架构)
│   ├── agents/             # Agent 驱动 (Gemini, OpenCode, PTY, Mock)
│   ├── cli/                # 命令行解析
│   ├── core/               # 引擎、服务、状态、配置模型
│   ├── tools/              # 插件化工具箱
│   └── ui/                 # 交互界面 (Init UI & Monitor TUI)
├── config/                 # ⚙️ 全局配置与凭证
├── hooks/                  # 🛡️ 阶段合规性门禁脚本
├── templates/              # 📝 交付物标准 Markdown 模板
├── workspace/              # 💾 动态数据 (任务、看板、回收站)
│   ├── tasks/              # 活动任务
│   └── STATUS.json         # 任务状态总看板
└── tests/                  # 🧪 自动化测试套件
```

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

## 🧪 开发者测试

我们拥有一套稳健的测试套件，确保重构与功能的零退化：
-   **单元测试**：`python3 -m pytest tests/unit`
-   **端到端流测试**：`bash tests/e2e/test_e2e.sh`

---
*Powered by Harness-Engineering. 让 AI 开发如外科手术般精准。*
