# Simple Workflow (Superpower 风格)

本项目采用一套高度结构化、状态驱动且具有自我演化能力的 AI 辅助开发工作流（借鉴 Superpowers 理念）。旨在通过严谨的工程实践（TDD、Google 标准、模式驱动）确保代码质量，并通过自动化的复盘机制不断优化开发流程。

## 🚀 核心理念

- **设计先行 (Design First)**：在编写任何代码之前，必须经过头脑风暴和详细规划，确保需求和实施方案无歧义。
- **状态驱动 (State Driven)**：通过中心看板 `STATUS.md` 追踪任务进度，实现多任务并行与上下文持久化。
- **高工程标准**：强制执行 Google Java 风格指南、TDD 循环、编译保证以及复杂逻辑的设计模式应用。
- **流程演化 (Evolutionary Workflow)**：通过任务归档阶段的复盘，自动识别并记录流程痛点，驱动工作流模板的持续改进。

## 📁 目录结构

```text
.
├── workflow/
│   ├── STATUS.md           # 核心看板：当前任务焦点、各任务阶段及状态
│   ├── evolution.md        # 演化日志：记录重复出现的痛点及流程改进方案
│   ├── templates/          # 五大阶段标准模板 (01-05)
│   ├── tasks/              # 活动任务的工作空间（按任务 ID 隔离）
│   └── archive/            # 已完成任务的历史存档
├── GEMINI.md               # 专为 AI Agent 准备的操作指南
└── README.md               # 本文件
```

## 🛠️ 工作流阶段

1.  **头脑风暴 (01-Brainstorming)**：定义目标，强制提问 3 次以澄清需求。
2.  **规划 (02-Planning)**：技术选型，微小任务拆解，强制落地详细实施方案。
3.  **编码 (03-Coding)**：TDD 循环（红-绿-重构），强制编译通过，复杂逻辑必须配备设计模式与注释。
4.  **评审 (04-Review)**：零记忆评审，满足 Google 代码评审标准，确定性构建验证。
5.  **归档 (05-Archive)**：流程复盘，清理环境，将频繁出现的痛点记入演化日志。

## 🔌 集成到您的项目

本工作流设计为“可插拔”模式，您可以将其应用到任何现有的 Java/Git 仓库中。

### 方案 A：直接复制（最快捷）
1. 将本项目中的 `workflow/` 目录和 `GEMINI.md` 文件直接复制到目标项目的根目录。
2. 确保目标项目有 `pom.xml` (Maven) 或 `build.gradle` (Gradle)。
3. 直接开始对 AI 下令：“按照本项目的工作流开启一个新任务...”。

### 方案 B：Git Submodule（便于同步更新）
如果您希望在多个项目中使用并保持工作流模板同步，可以将其作为子模块添加：
```bash
git submodule add [本项目仓库地址] .workflow_engine
# 然后在根目录创建一个符号链接或在 GEMINI.md 中引用 .workflow_engine/templates
```

## 🤖 AI 协作指南


如果您正在使用 Gemini CLI 或 Claude Code 协作：
- 它们会自动读取 `GEMINI.md` 了解操作规范。
- 它们会首先检查 `workflow/STATUS.md` 以获取当前任务上下文。
- 它们会严格遵守模板中的“强制”规则（如编译保证、详细设计等）。

## 📈 演化规则

当同一个流程改进建议或痛点在任务归档阶段连续出现 **3 次** 时，该点将被记录到 `workflow/evolution.md` 中，并作为下次工作流优化的依据。

---
*Created with ❤️ by Gemini CLI.*
