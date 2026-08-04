# E2E Flow Testing Module

端到端流程测试模块。驱动真实的 `sw init --mock` TUI，经过全部 5 个阶段，验证每个阶段的产出合规性和推进逻辑。

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
# 1. 运行驱动（完整 5 阶段，约 60s）
python3 tests/e2e-flow/driver.py

# 2. 验收（需提供驱动输出的任务名）
python3 tests/e2e-flow/verify.py --task-dir workspace/tasks/e2e-xxxxx

# 3. 查看验收报告
cat workspace/tasks/e2e-xxxxx/.verify-report.json
```
