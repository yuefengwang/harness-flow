# 阶段 02：规划 (Planning)

> 📎 本阶段 hooks: `hooks/02-planning.md`

## 项目概述
优化 Harness Dashboard — 增加日志流、凭证配置、搜索筛选三大功能。

## 技术架构
- **语言/运行时:** Python 3 (FastAPI), JS (vanilla)
- **关键模块:** fastapi StreamingResponse, pyyaml
- **前端:** 纯 JS + CSS，零框架

## 任务拆解

- [x] **任务 1: 后端 — 日志 SSE 端点 + 凭证端点**
  - 文件: `harness/dashboard/server.py`, `harness/credentials.yaml`
  - 验证: 模块导入通过 ✓
- [x] **任务 2: dispatch.sh — 读取 credentials.yaml 注入 Docker**
  - 文件: `harness/dispatch.sh`
  - 验证: bash -n 语法通过 ✓
- [x] **任务 3: .gitignore — 忽略 credentials.yaml**
  - 验证: ✓
- [ ] **任务 4: 前端 — 日志流查看器 + 搜索筛选 + 凭证状态**
  - 文件: `harness/dashboard/static/app.js`, `style.css`, `index.html`
  - 已委托: visual-engineering agent (bg_4c59e300)
- [ ] **任务 5: 端到端验证**
  - 验证: 启动 server → mock 数据 → 全功能测试

## 验收标准
- [x] 日志 SSE 端点正常
- [ ] 前端日志流实时显示
- [x] credentials.yaml 可解析
- [ ] 凭证状态指示器正常
- [ ] 搜索筛选即时生效
