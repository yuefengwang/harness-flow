# 05-Archive Hooks

## hook-05-01: README 同步

- **触发**: during
- **规则**: 每次归档必须检查并同步以下文档
  - `workflow/README.md`: 工作流自身变更（新阶段/新命令/新规则）
  - `hooks/README.md`: hooks 体系变更（新 hook/修改触发条件）
  - `repo/<project>/README.md`: 项目变更（新 API/新依赖/新配置）
- **验证**:
  - 已逐项检查以上文档
  - 有变更则已更新，无变更则已标记 "跳过"
  - 项目若尚无 README，已创建

## hook-05-02: 演化追踪

- **触发**: post
- **规则**: 同一流程痛点或改进建议连续出现 3 次时，必须记录到 `EVOLUTION.md`
- **验证**: 已检查最近 3 个任务的复盘笔记；如有重复痛点，已写入 EVOLUTION.md

## hook-05-03: 环境清理

- **触发**: post
- **规则**: 清理构建产物，归档任务日志，重置模板
- **验证**:
  - 构建产物已清理 (`mvn clean`)
  - 任务日志已移至 `archive/history/<task-name>.md`
  - 下一个任务模板已就绪
