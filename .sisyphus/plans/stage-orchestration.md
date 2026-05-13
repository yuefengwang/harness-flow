# SW 阶段编排设计文档

## 1. 设计目标

解决当前 `sw` 中 5 个 stage 之间无强先后顺序约束的问题，增加：
- **阶段顺序强制**: 不可跳过、不可回退（除非 remove + re-init）
- **完成校验**: 每个阶段自动校验 + 用户确认后才可推进
- **上下文连续性**: 跨 stage 的 AI Agent 可通过 task 目录文件获取完整上下文
- **tmux 复用**: 同一任务的多个 stage 复用同一个 tmux 会话

## 2. 命令职责分离

| 命令 | 职责 | 副作用 |
|------|------|--------|
| `sw init` | 创建任务，进入 stage 01 | 创建 task 目录、.state、STATUS.md |
| `sw next` | 为**当前 stage** 启动 AI Agent | 复用/创建 tmux，注入上下文 |
| `sw advance` | 校验当前 stage 完成 → 推进到下一 stage | 更新 .state、STATUS.md |
| `sw status` | 查看当前 stage 和完成状态 | 无 |
| `sw resume` | 查看任务恢复信息 | 无 |

### 2.1 典型工作流

```
  sw init --name=add-auth --agent=opencode
       │
       ▼  (自动进入 stage 01)
  sw next --name=add-auth
       │  ┌─ 复用 tmux 会话 sw-add-auth
       │  ├─ 右侧 pane: opencode（注入 01-brainstorming 模板 + hooks）
       │  └─ Agent 填写 templates/01-brainstorming.md
       ▼
  sw advance --name=add-auth
       │  ├─ 校验: 01-brainstorming.md 是否已填写？设计批准标记？
       │  ├─ 提问: "01-头脑风暴 已完成，推进到 02-规划？[Y/n]"
       │  └─ 更新 .state → stage_idx=1, stage="02-planning"
       ▼
  sw next --name=add-auth
       │  ┌─ 复用已存在的 tmux 会话 sw-add-auth
       │  ├─ 右侧 pane: Ctrl+C 旧 agent → 启动新 opencode
       │  ├─ 注入上下文: .state + 01-brainstorming.md(已填写) + 02-planning.md(模板)
       │  └─ Agent 读取前一阶段产出，继续工作
       ▼
  ... 重复 advance → next 直到 stage 05 (archive)
```

## 3. 阶段状态机

```
  [pending] ──sw next──▶ [in_progress] ──sw advance──▶ [completed] ──▶ 下一 stage
      ▲                      │                              │
      │                      └── sw next (重新启动) ────────┘
      │
      └── sw remove → [trashed] ──sw restore──▶ 回到 pending/completed
```

**.state 新增字段**:

```yaml
id: add-auth
type: feature
stage: "02-planning"
stage_idx: 1
stage_status: "in_progress"    # 新增: pending | in_progress | completed
created_at: 2026-05-13T10:00:00
updated_at: 2026-05-13T10:30:00
```

## 4. 阶段校验器

### 4.1 校验规则表

| Stage | 自动校验项 | 用户确认项 |
|-------|-----------|-----------|
| 01-brainstorming | 模板文件存在且内容非空 (非初始模板) | 设计批准标记为"是" |
| 02-planning | 模板文件存在，含至少 2 个微任务 | 落地方案已确认 |
| 03-coding | `git log` 有常规提交；编译/测试命令通过（如存在） | 代码变更已审查 |
| 04-review | 构建/测试通过（如存在） | 评审清单已逐项完成 |
| 05-archive | 任务目录准备就绪 | 归档后 README 已同步 |

### 4.2 校验器实现

```python
class StageValidator:
    def validate(self, task_dir: Path, stage: str) -> ValidationResult:
        """返回 (passed: bool, missing: list[str], warnings: list[str])"""
```

每个 stage 的校验逻辑：
- 检查对应模板文件是否有用户填写的内容（非初始空模板）
- 检查 hooks 中可自动化的项（如编译状态）
- 返回缺失清单供 `sw advance` 展示

## 5. 上下文注入机制

### 5.1 sw next 启动 Agent 时注入的上下文

Agent 启动时，`sw next` 会：

1. **设置环境变量** (通过 `export` 在 pane 中):
   ```bash
   SW_TASK=add-auth
   SW_STAGE=02-planning
   SW_TASK_DIR=workflow/tasks/add-auth
   ```

2. **右侧 pane 发送 Agent 命令**，附带系统提示:
   ```
   你正在执行 Simple Workflow 的 02-规划 阶段。
   
   任务: add-auth
   上一阶段产出: workflow/tasks/add-auth/01-brainstorming.md
   当前模板: workflow/tasks/add-auth/02-planning.md
   强制规则: hooks/02-planning.md
   
   请先读取上一阶段产出和当前模板，然后按 hooks 规范执行。
   ```

3. **左侧 pane** 保持在 task 目录，方便文件操作

### 5.2 上下文连续性保证

- Stage N 的 Agent 启动时，Stage N-1 的所有产出文件（.md 模板）已保留在 task 目录中
- Agent 指令明确要求先读取前序文件
- `.state` 文件包含完整任务元数据
- tmux 会话复用保留了 shell 历史和工作目录

## 6. Tmux 会话复用策略

### 6.1 首次 sw next
```
  创建 tmux: sw-{task-name}
  ├── 左侧 pane: bash @ 项目根目录 (可操作文件)
  └── 右侧 pane: $AGENT (注入上下文后启动)
```

### 6.2 后续 sw next (复用)
```
  复用 tmux: sw-{task-name}
  ├── 左侧 pane: 保持不动 (保留 bash 历史)
  └── 右侧 pane:
       1. 如果 Agent 仍在运行 → Ctrl+C 中断
       2. 清除 pane 内容 (可选)
       3. 发送新的 Agent 启动命令 + 新上下文提示
```

### 6.3 tmux 不存在时
- 如果会话已被 kill，重新创建（与首次行为一致）

## 7. 文件改动清单

| 文件 | 改动 |
|------|------|
| `sw` | 重写 `advance`、新增 `next` 命令、新增 `StageValidator` 类、新增 `inject_context()` 函数 |
| `workflow/templates/*.md` | 增加阶段完成标记区 (用户填写"已完成") |
| `workflow/commands/` | 新增 `next` 快捷命令 |

## 8. 用户交互流程

### 8.1 sw advance 交互

```
  $ ./sw advance --name=add-auth

  ━━━ 阶段校验: 01-头脑风暴 ━━━
  [✓] 模板已填写 (01-brainstorming.md)
  [✓] 设计批准: 是
  [!] 提示: 请确认 3 个交互问题已获得用户回答

  是否推进到 02-规划？[Y/n]: y

  ━━━ 阶段推进 → 规划 ━━━
  [✓] stage_idx: 0 → 1
  [✓] STATUS.md 已更新
  下一步: ./sw next --name=add-auth
```

### 8.2 sw next 交互

```
  $ ./sw next --name=add-auth

  ━━━ 启动阶段: 02-规划 ━━━
  任务: add-auth
  前序产出: 01-brainstorming.md (已填写)
  Agent: opencode

  确认启动？[Y/n]: y

  [✓] tmux 会话: sw-add-auth (复用)
  [✓] 上下文已注入: hooks/02-planning.md → templates/02-planning.md
  [✓] Agent 已启动

  完成后执行: ./sw advance --name=add-auth
```

## 9. 边缘情况

| 场景 | 处理 |
|------|------|
| 重复 advance | 提示 "已是 stage N，尚未完成" |
| advance 但 stage 未开始 | 提示 "当前 stage 尚未开始，请先 /sw next" |
| next 但 stage 已完成 | 提示 "已完成，请 /sw advance 推进" |
| next 但 agent 已在运行 | 询问 "Agent 正在运行，中断并重启？[y/N]" |
| tmux 不可用 | 降级：直接输出启动命令，用户手动执行 |
| 非交互环境 | advance 跳过确认直接推进，next 跳过 tmux 输出命令 |
