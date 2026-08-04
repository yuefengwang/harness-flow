# LLM 执行计划

> 当被指向 `tests/e2e-flow/` 时，按此计划执行。每次只推进一个阶段，确认无误后再进入下一步。

---

## Phase 0 — 环境检查

1. 读取 `README.md`、`acceptance.md`、`driver.py`、`verify.py`，理解全貌。
2. 检查 `config.yaml` 中 `mock_agent.enabled` 是否为 `true`，若不是则修改。
3. 检查 `workspace/tasks/` 下是否有之前的 e2e 测试残留目录，如有则删除。
4. 确认 `python3 ./sw --help` 可正常执行。

---

## Phase 1 — 执行驱动

运行驱动脚本：

```bash
python3 tests/e2e-flow/driver.py
```

驱动脚本会：
1. 用 `pty.fork()` 创建子进程
2. 子进程运行 `sw init --mock --name=e2e-{pid}` --context="..."
3. 父进程通过 PTY 发送按键、监控 `.log` 文件
4. 依次经过 01→02→03→04→05 全部阶段
5. 在每个阶段回答 MockAgent 提问、发送 `/advance`
6. 输出测试任务名和简要结果

**成功标志**：脚本输出 `ALL STAGES PASSED`，退出码为 0。

**失败处理**：
- 如果脚本退出码非 0 → 进入 Phase 3（修复）
- 如果脚本成功 → 进入 Phase 2（验收）

从输出中获取测试任务名（格式：`e2e-{pid}`），后续步骤需要用到。

---

## Phase 2 — 验收

### 2a. 运行自动化验收脚本

```bash
python3 tests/e2e-flow/verify.py --task-dir workspace/tasks/<任务名>
```

验收脚本会逐阶段检查 stage 文件，输出 JSON 格式的验收报告并存到 `.verify-report.json`。

### 2b. 根据报告判断

- 所有检查项通过 → 进入 Phase 4（完成）
- 有失败项 → 进入 Phase 3（修复）

### 2c. 手动核验（可选）

如需深入，读取 `workspace/tasks/<任务名>/.log` 查看完整运行日志，逐阶段检查。

---

## Phase 3 — 修复 Bug

> 修复原则：外科手术式改动，只改有问题的代码，不改无关逻辑。

### 流程

1. **读取失败信息**：从验收报告或 `.log` 中了解失败内容
2. **定位根因**：
   - 软校验失败 → `sw_lib/runnable/utils.py` 中 `check_stage_compliance()` 的逻辑
   - 硬校验失败 → `hooks/check_XX.sh` 中的 shell 脚本逻辑
   - 驱动交互失败 → `driver.py` 中的交互逻辑
   - Agent 输出问题 → `sw_lib/agents/mock.py` 中的场景脚本
   - 输出保存/替换问题 → `sw_lib/runnable/base.py` 中 `_save_stage_output()`
3. **修复代码**：修改对应的源文件
4. **重新运行**：
   ```bash
   # 清理测试任务
   rm -rf workspace/tasks/e2e-*
   
   # 重新执行 Phase 1
   python3 tests/e2e-flow/driver.py
   ```
5. **验证修复**：进入 Phase 2 验收
6. **记录修复**：在 `history.md` 中追加记录

### 常见故障点

| 症状 | 可能根因 | 修复位置 |
|------|---------|---------|
| Agent 提问题后 driver 未回答 | driver.py 中 wait_log 超时或 pattern 不匹配 | `driver.py` |
| `/advance` 后软校验失败（未勾选框） | mock 模式下 `[ ]`→`[x]` 替换未覆盖 Gate 区 | `sw_lib/runnable/base.py` `_save_stage_output()` |
| `/advance` 后硬校验失败 | hook 脚本检查了 mock 未填充的字段 | `hooks/check_XX.sh` 或 mock 输出逻辑 |
| WBS 中的 `[ ]` 被错误替换为 `[x]` | `[ ]`→`[x]` 替换不当，误伤 AI Output 区 | `sw_lib/runnable/base.py` `_save_stage_output()` |
| Review 返工时 Evidence 表无数据 | `_write_review_route()` 未填充证据表 | `sw_lib/ui/tui.py` |

---

## Phase 4 — 完成

1. `verify.py` 全部检查项通过
2. 所有 5 个阶段均成功推进
3. 在 `history.md` 中记录本次测试摘要
4. 输出完成报告：

```
✅ E2E Flow 测试全部通过
   任务: e2e-xxxxx
   阶段: 01-brainstorming → 02-planning → 03-coding → 04-review → 05-archive
   验收: N/N 全部通过
   修复: X 个 bug 已修复（详见 history.md）
```
