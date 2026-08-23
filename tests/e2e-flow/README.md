# E2E Flow Testing Module

端到端流程测试模块。驱动真实的 `sw init --mock` TUI，经过全部 5 个阶段，验证每个阶段的产出合规性和推进逻辑。

**只跑 MockAgent。** 驱动真实 agent 的模式已删除：那条路径依赖模型输出，同一份
代码两次运行结果不同，失败无法区分是回归还是模型这次答得不一样。MockAgent 的每
个阶段输出都是写死的脚本（`sw_lib/agents/mock.py`），因此本模块的每一步都能断言
确切文本 —— 可执行、可复现、可验证。

测试的输入由测试自己固定，不从 `config.yaml` 继承：driver 会设好
`SW_MOCK_REVIEW_ROUTE=05-Archive`（仓库里那份 `review_route` 是 `02-Planning`，
继承它会走返工分支）和 `SW_MOCK_RESPONSE_DELAY=0.2`（config 里的值是给人演示用的）。

真实 agent 仍可通过 `sw init --no-mock` 使用 —— 那是生产功能，不是测试。

## 什么时候用它

**先跑单元测试。** 流程逻辑（路由决策、返工闭环、门禁重置、返工上下文注入）
已经由进程内测试覆盖，跑完只要几秒：

```bash
python3 -m pytest tests/unit/ui/test_reroute_flow_inprocess.py -q
python3 -m pytest tests/unit/workflow/test_reroute_cycle.py -q
```

那一层直接调 `MonitorTUI._dispatch` / `_run_advance`，不起子进程、不用 PTY、
不 sleep，失败信息直接指向断言。**定位流程 bug 应该从那里开始。**

本模块跑一轮约 14 秒。等待时机不再靠猜：驱动同时等 MockAgent 的场景收尾标记
和 TUI 的 `PROMPT_READY_MARKER`（面板切成 options 时自己打的），两个都到位才
按键 —— 那正是 `_validate_input` 会放行的时刻。此前靠"日志静默 3 秒"推断，
窗口可能落在脚本自带的 sleep 之间，过早按键被拒，三轮挂一轮。

它的不可替代之处只有一件：验证真实 PTY 下的按键处理、Rich 全屏渲染和输入
校验。改了 TUI 的输入循环或渲染，才需要跑它。

## 文件清单

| 文件 | 用途 |
|------|------|
| `plan.md` | **LLM 执行计划** — 读此文件开始工作。包含分步指令：执行 → 验收 → 修复 → 完成 |
| `acceptance.md` | **验收标准** — 逐阶段详细检查清单，所有项勾选才算测试通过 |
| `driver.py` | **PTY 驱动脚本** — 用 `pty.fork()` 启动真实 `sw init --mock`，通过 PTY 发送按键，完成 5 阶段交互 |
| `verify.py` | **自动化验收脚本** — 读取任务目录的 stage 文件，对照 acceptance.md 逐项检查 |
| `history.md` | **修复历史** — 记录每次测试发现的 bug 和修复方案 |
| `README.md` | 本文件 — 模块总览 |

## 前置条件

- Python 3.9+
- MockAgent 已启用（`config.yaml` 中 `mock_agent.enabled: true`）
- 无同名测试任务残留

## 快速开始

```bash
# 一条命令：驱动 5 阶段 → 自动验收 → 通过后清理（约 14s）
python3 tests/e2e-flow/driver.py
```

退出码 0 表示驱动与验收全部通过。失败时任务目录会保留，并在终端直接打印现场
诊断（state、阶段文件大小、Gate 区状态、最后 15 行日志），不必再去翻目录。

常用开关：

```bash
python3 tests/e2e-flow/driver.py --keep        # 通过后也保留任务目录
python3 tests/e2e-flow/driver.py --no-verify   # 只跑驱动，不做验收

# 单独对已有任务验收
python3 tests/e2e-flow/verify.py --task-dir workspace/tasks/e2e-xxxxx
cat workspace/tasks/e2e-xxxxx/.verify-report.json
```
