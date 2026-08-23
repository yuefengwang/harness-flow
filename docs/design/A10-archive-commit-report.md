# A10：05 归档 —— 自动 commit 与达成度报告

> 依赖：**A1**（任务级 git 仓库）、**A2**（red witness）、**A3**（事实包）
> 被依赖：无
> 范围：`hooks/check_05-archive.sh`、`templates/05-archive.md`、
> 新增 `sw_lib/workflow/archive_report.py`
> 不含：git 仓库初始化（属 A1）
>
> 🔒 **开发纪律**：实施本任务须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。
> 本任务与协议第 2 节（三态报告）**互为镜像** —— 见第 9 节。

---

## 1. 目标

两件事：

1. **自动 commit**：归档时把任务变更提交到任务仓库，形成可追溯的交付快照。
2. **达成度报告**：产出一份三分类报告 —— 哪些做到了、哪些没做到、哪些不确定。

### 1.1 为什么「不确定」这一类必须存在

这是本文档的核心判断。二分类（做到 / 没做到）会**迫使系统对无法验证的项目撒谎**：

- 「支持并发访问」—— 没有并发测试，写「做到了」是假的，写「没做到」也不对。
- 「界面友好」—— 无可执行判据。

把无判据的项目诚实地归入「不确定」，是让报告可信的前提。
**「不确定」的数量本身就是最有价值的指标** —— 它度量了这个任务里有多少需求
根本没有验收手段，直接指向下一轮该补什么判据（对应 R10 oracle 成本递减）。

---

## 2. 现状（已核实）

| 项 | 现状 |
|---|---|
| 归档时的 git 操作 | **无任何 commit**。`hooks/check_05-archive.sh:14` 只用 `git diff HEAD --name-only` 检查 README 是否更新 |
| `templates/05-archive.md` | `Summary` / `Memory` / `Retro` 三段全部由 agent 自填散文 |
| 结算回调 | `graph.py:54` 在 05 阶段 gate 通过时触发 `on_settlement()`，可作为挂载点 |
| 任务归档动作 | `service.py:179` 用 `shutil.move` 移入 `.trash`；无版本快照 |

**问题**：交付物没有不可变快照，事后无法回答「当时交付的到底是什么」。

---

## 3. 自动 commit 设计

### 3.1 时机

05-archive 阶段的 **post hook**，在 gate 签署之后、任务归档之前。

### 3.2 动作

```
git -C <target_dir> add -A
git -C <target_dir> commit -m "<message>"
```

全部使用 `git -C`，**禁止依赖进程 cwd**（A1 的纪律，避免误提交到 harness 仓库）。

### 3.3 commit message 格式

```
harness(<task_name>): <一句话交付摘要>

Stage: 05-archive
Baseline: <baseline_sha>
Tasks: <done>/<total> done, <undone> undone, <unknown> unknown
Tests: <passed>/<collected> passed
Red-witness: <verified|absent>
Reroutes: <n>
```

摘要取自达成度报告，**不取 agent 自由文本** —— 后者可能与实际状态不符。

### 3.4 前置校验（缺一即拒绝 commit）

| 校验 | 失败处置 |
|---|---|
| `git -C <target_dir> rev-parse --show-toplevel` **`resolve()` 归一后**等于 `target_dir` | 硬失败：仓库归属错误 |
| `baseline_sha` 存在且可解析 | 硬失败 |
| 工作区非空（有实际产出） | 硬失败：复用 `lib_run_tests.sh` 的 `has_code_output` |

> ⚠️ **归一不可省**（A1 的 2.3 实测）：git 返回真实路径，
> `.state` 存的是用户给的原始字符串。macOS 下 `/tmp` 与 `/private/tmp`
> 使朴素字符串比较**必然误判硬失败**。
> 本表两处路径比较均须双侧 `Path(...).resolve()`。
> 实现应直接复用 A1 的 `git_repo.verify_ownership()`，不要各自再写一份。

### 3.5 边界情形

| 情形 | 处置 |
|---|---|
| 无任何改动（diff 为空） | 不 commit，报告标注「无变更」，不报错 |
| `target_dir == "."`（harness 自身） | **不自动 commit**。改动 harness 自身必须由人提交 |
| 存量任务无 git 仓库 | 按 A1 补建基线后再 commit，报告标注「基线为补建」 |
| commit 失败（如 hooks 拒绝） | 归档**不通过**，报告保留，供人处理 |

---

## 4. 达成度报告设计

### 4.1 输出

两份产物：

- `workspace/tasks/<task>/facts/archive-report.json` —— 机器可读
- `workspace/tasks/<task>/05-archive.md` 的报告段 —— 人类可读（由 sw 渲染，非 agent 填写）

### 4.2 三分类判定规则

对 02-planning 的每个 Task DAG 条目逐项判定。**判定由程序做，不问 agent**：

| 分类 | 判定条件 | 依据来源 |
|---|---|---|
| ✅ **做到了** | 该任务的 `Verify` 命令可执行**且**退出码为 0 | A3 事实包 + 02 的 Verify 字段 |
| ❌ **没做到** | `Verify` 命令可执行**但**退出码非 0；或 `red_witness` 中对应测试仍为红 | 同上 + A2 |
| ❓ **不确定** | `Verify` 缺失、非可执行文本、或无对应测试节点 | 同上 |

补充维度：

| 维度 | ✅ | ❌ | ❓ |
|---|---|---|---|
| 测试 | `red_witness` 全部转绿 | 有红灯 | 无 `red_witness`（A2 未生效） |
| 需求条目 | 有对应的可执行验收场景且通过 | 场景失败 | **无验收场景**（R8 未落地，当前多数情形） |
| diff 范围 | 改动全在 `target_dir` 内 | 有越界改动 | 无法判定（A1 未生效） |
| **审查独立性** | 审查者之间及与 developer 均**异构 provider** | — | `require_heterogeneous: false`（**审查者同构**，见 A4 的 3.5）或 mock 模式 |
| **审查有效性** | A9 仲裁命中优先级 1-4（审查产出了有效结论） | — | A9 的 `arbitration != ok`，或**三轨全沉默**（见下方 A9 回填） |
| **需求依据** | `unresolved` 为空（无「AI 自行推断」的点） | — | `unresolved` 非空；含 A12 的 `unanswered` 与「按 AI 推断继续」条目 |

> 当前阶段绝大多数需求条目会落入 ❓。**这是真实状况的准确反映**，
> 不应通过放宽判据来美化。报告的价值正在于暴露这一点。

> ⚠️ **「审查独立性」一行来自 A4 的接口约定**（A4 的 3.5、R2）：
> A4 实测当前 `config.yaml` 五个角色的 provider **全是 `opencode`**，
> 因此 `require_heterogeneous` 默认值定为 `false` 以不阻塞既有流程。
> **默认关闭不等于同构可接受** —— 关闭时本报告必须标注为 ❓，
> 让降级可见。C2（先验相关）在此状态下未被解决。

> ⚠️ **「审查有效性」与「需求依据」两行来自 A9 / A12 的接口约定**（回填）：
>
> **A9 的 U9-1 把一个盲区推给了本报告**：A9 的 3.3 定案，「一条反例都没提交」**不命中 N1**，会正常走优先级 6 归档 ——
> 因为「三轨全沉默」与「确实没问题」在数据上不可分。
> A9 无法解决它，**只能靠本报告让它可见**：三轨均无产出时，
> 「审查有效性」必须标 ❓ 而非 ✅。**这是本报告承接的一条真实盲区**，
> 不是可选的补充维度。
>
> **A12 的 `unanswered` 是「不确定」的主要成分**：A12 的 3.5 定案，
> 用户选择「按 AI 推断继续」时该决定须落盘进 `decisions`，
> 并进入本报告的 ❓ 分类。若只是放行而不落盘，**用户的知情放弃会消失**，事后无法复盘（A12 的验收 9）。

### 4.3 报告结构

```jsonc
{
  "task": "my-feature",
  "generated_at": "...",
  "baseline_sha": "...",
  "commit_sha": "...",              // 本次归档 commit
  "summary": { "done": 5, "undone": 1, "unknown": 3 },
  "review_independence": "same_provider",   // heterogeneous | same_provider | mock_skipped（A4 的 3.5）
  "items": [
    {
      "id": "T1",
      "desc": "实现 Note 数据模型",
      "verdict": "done",
      "evidence": "pytest tests/test_model.py → exit 0",
      "source": "facts/tests.json"
    },
    {
      "id": "T3",
      "desc": "支持并发写入",
      "verdict": "unknown",
      "reason": "02-planning 的 Verify 字段为自由文本，不可执行",
      "suggestion": "补充可执行验收场景（R8）"
    }
  ],
  "unknown_causes": {               // 聚合「不确定」的成因，供 EVOLUTION 沉淀
    "verify_not_executable": 2,
    "no_acceptance_scenario": 1
  }
}
```

### 4.4 与 EVOLUTION.md 的联动（R10）

`unknown_causes` 的聚合结果应追加进 `docs/EVOLUTION.md`：
同一成因累计出现 3 次即提示补充对应的自动判据。

> 这是让 oracle 成本单调递减的具体机制 ——
> 把「这次没法验证」变成「下次能验证」的待办，而非每次重新付人工成本。

---

## 5. 归档是否阻断

| 报告结果 | 归档 |
|---|---|
| 有 ❌ 项 | **阻断**。走返工路由，不允许带着已知失败归档 |
| 仅有 ❓ 项 | **放行但显著提示**。❓ 是判据不足，不是交付失败 |
| 全 ✅ | 放行 |

> ❓ 不阻断是刻意的：否则在 R8 落地前所有任务都无法归档。
> 但必须在报告顶部显示 ❓ 计数，且 commit message 中带 `unknown` 数量 ——
> 让「没有判据」这件事在 git 历史里留下痕迹。

---

## 6. 验收标准

1. 归档后任务仓库存在一个新 commit，message 含任务数与测试数统计。
2. commit **只落在任务仓库**，harness 自身 `git status` 保持干净。
3. `target_dir == "."` 时不自动 commit。
4. `Verify` 为可执行命令且通过的任务判为 ✅，退出码非 0 判为 ❌。
5. `Verify` 为自由文本的任务判为 ❓，且 `reason` 说明原因。
6. 存在 ❌ 项时归档被阻断。
7. 仅有 ❓ 项时归档放行，但报告与 commit message 均显示 ❓ 计数。
8. `unknown_causes` 聚合正确，并追加到 `EVOLUTION.md`。
9. 报告段由 sw 渲染，**agent 改写 `05-archive.md` 不影响 json 内容**
   （沿用 `docs/design-json-state-source.md` 的单向渲染原则）。

---

## 7. 风险

| # | 风险 | 处置 |
|---|---|---|
| R1 | `Verify` 命令执行有副作用（如部署、删数据） | 只执行 02 阶段声明的 Verify；受 `run_command` 白名单与超时约束；文档要求 Verify 必须幂等 |
| R2 | 自动 commit 吞掉未预期的文件 | `add -A` 前先检查 diff 文件清单，超出 `target_dir` 则拒绝 |
| R3 | 报告过度乐观 | 判定全部由程序做，不接受 agent 自述（本文档核心约束） |
| R4 | mock 模式 | mock 下跳过真实 commit，生成标注 `mock=true` 的报告 |

---

## 8. 回滚

自动 commit 与报告生成各由独立开关控制
（`harness.archive.auto_commit`、`harness.archive.report`），默认可关。
关闭后 05 阶段行为与现在完全一致。

---

## 9. 本任务的红绿要点

### 9.1 与开发协议互为镜像

本任务实现的三分类（done / undone / unknown）与
`DEV-PROTOCOL.md` 第 2 节要求执行者使用的三态（✅ / ❌ / ❓）**是同一套语义**。

因此：**实施本任务时的汇报，本身就是这个功能的第一个使用样例。**
如果在开发过程中把「未验证」报成「已完成」，那么正在写的这个功能
也不会被正确地实现 —— 因为写的人不相信这个区分有意义。

### 9.2 红的正确形态

| 验收项 | 红的正确形态（实现前须看到） | 假绿风险 |
|---|---|---|
| ✅ 判定 | 构造 Verify 可执行且退出 0 的任务，断言 verdict 为 `done` | 只测一种 verdict |
| ❌ 判定 | 构造 Verify 退出非 0 的任务，断言 `undone` | 同上 |
| ❓ 判定 | 构造 Verify 为自由文本的任务，断言 `unknown` **且 `reason` 非空** | 断言了 unknown 但不检查 reason |
| commit 落点 | 断言 harness 自身 `git status` 干净，故意在 harness 根目录留改动应能让它红 | 只断言子仓库有 commit，不验证父仓库未被污染 |
| ❌ 阻断 | 存在 undone 时断言归档被拒绝 | 只测放行路径 |
| ❓ 放行 | 仅 unknown 时断言放行**且** commit message 含 unknown 计数 | 只测放行，不检查计数是否真的写进去了 |
| 单向渲染 | agent 改写 `05-archive.md` 后断言 json 内容不变 | 从不测试篡改场景 |

### 9.3 最容易出的假绿

**「commit 只落在任务仓库」这条**。测试若只断言子仓库多了一个 commit，
完全无法发现改动同时被提交进了 harness 仓库。
必须**同时**断言父仓库 `git status --porcelain` 为空。

构造红的方法：先故意用不带 `-C` 的 `git commit`，确认测试能红；
改用 `git -C <target_dir>` 后转绿。这样才证明测试真的在测这件事。

### 9.4 报告不得由 agent 自述（本任务的核心约束）

第 4.2 节已定：判定由程序做。开发时对应的纪律是 ——
**不要为了让报告好看而放宽判定**。
当前阶段绝大多数需求条目会落入 ❓，这是真实状况。
若实施过程中产生「把 ❓ 归入 ✅ 会不会更好看」的想法，
记录该摩擦点（协议第 7 节），但不要改判据。
