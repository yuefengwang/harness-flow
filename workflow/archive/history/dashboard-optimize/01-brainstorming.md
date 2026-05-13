# 阶段 01：头脑风暴 (Brainstorming)

> 📎 本阶段 hooks: `hooks/01-brainstorming.md`

## 核心目标
优化 Harness Dashboard 前端：增加日志流、凭证管理、搜索筛选功能。

## 🔍 需求澄清

### 问题 1: 日志流展示方式
- [x] 选项 A: SSE 实时流 + 历史尾部 — 兼顾实时和回溯 (Recommended)
- [ ] 选项 B: 仅 SSE 流 — 简单但无历史
- [ ] 选项 C: 仅静态加载 — 最简单但无实时
- **用户选择:** A

### 问题 2: 凭证注入方式
- [x] 选项 A: 配置文件 (YAML) — 结构清晰，便于管理 (Recommended)
- [ ] 选项 B: 环境变量 — 12-factor，CI 友好
- [ ] 选项 C: Docker secrets — 生产级但复杂
- **用户选择:** A

### 问题 3: 额外优化范围
- [x] 选项 A: 日志流 + 凭证 + 搜索/筛选 — 核心三件 (Recommended)
- [ ] 选项 B: 仅日志流 + 凭证 — 最小集
- [ ] 选项 C: 全部建议 — 磁盘用量/批量清理/任务模板等
- **用户选择:** A

## 设计文档
### 需求列表
- [x] 功能 1: Agent 详情面板增加实时日志流查看器 (SSE)
- [x] 功能 2: 凭证配置文件 (harness/credentials.yaml) 注入 Docker
- [x] 功能 3: Agent 列表搜索/筛选
- [x] 功能 4: 凭证状态指示器

### 技术约束
- 后端: FastAPI SSE StreamingResponse
- 前端: 纯 JS/CSS，零框架
- 凭证: YAML，不提交 Git

## 决策 / 下一步
- [x] 设计是否已批准？ (是)
- [x] 进入 **规划阶段 (Planning)**
