# 剩余 Axxx 任务的并行执行方案

> 版本：2026-08-23
> 定位：`README.md` 第 4 节的波次表是**依赖顺序**的权威，本文档不覆盖它。
> 本文档回答它没回答的另一半：**同一波内多个 agent 同时动手时，谁会撞谁。**
>
> 依赖不冲突 ≠ 文件不冲突。波次表给的是前者，派发需要后者。
>
> 🔒 各任务实施仍须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。

---

## 1. 三句话结论

1. **剩余 10 项**（A3-A12 加 B0，其中 A1/A2 收尾中）。实测代码而非文档标记得出，见第 2 节。
2. **实际可并行峰值是 3，不是 10。** 瓶颈是三个集成文件的写冲突，不是 agent 数量。
3. **不要引入调度器。** 用 git worktree 加一张「单一写者」表即可；
   等 A5 落地，并行能力由被开发的系统自己提供。

---

## 2. 实测状态审计（不采信文档的「已写」标记）

`README.md` 的 ✅ 只表示**设计文档写完**，不表示代码已实现。按产出文件实测：

| 任务 | 核心产出 | 实测 | 结论 |
|---|---|---|---|
| A0 | `core/evidence.py` | 180 行，**已跟踪** | ✅ 已落盘 |
| A1 | `core/git_repo.py` | 501 行，**未跟踪** | ⚠️ 收尾中 |
| A2 | `workflow/red_witness.py` | 703 行，**未跟踪**，已接线 `check_03-coding.sh` | ⚠️ **开发中** |
| A3 | `workflow/fact_pack.py` | 不存在 | ⬜ 未开始 |
| A4 | `config.py` 的 `ConfigError` / `provider` | 零命中 | ⬜ 未开始 |
| A5 | `graph.py` 动态 fan-out | 只有静态 `add_conditional_edges` | ⬜ 未开始 |
| A6 | `workflow/objective_check.py` | 不存在 | ⬜ 未开始 |
| A7 | `workflow/counterexample.py` | 不存在 | ⬜ 未开始 |
| A8 | `workflow/design_review.py` | 不存在 | ⬜ 未开始 |
| A9 | `workflow/arbiter.py` | 不存在；`parse_route_from_ai_output` 仍在 | ⬜ 未开始 |
| A10 | `workflow/archive_report.py` | 不存在 | ⬜ 未开始 |
| A11 | `sw_lib/probe/` | 目录不存在 | ⬜ 未开始 |
| A12 | `hooks/01-brainstorming.md` 的 `hook-01-06` | 零命中 | ⬜ 未开始 |
| B0 | `sw_lib/probe/baseline.py` | 不存在 | ⬜ **未开始，且时间窗在流失** |

> A2 已有 **9 个** `test_red_witness_*.py` 测试文件并接入 `check_03-coding.sh`，
> 完成度比「开发中」听起来更高，但**全部未提交**。

### 2.1 当前测试基线（派发前必须知道的）

**不要记数字，记判读方法。** A2 正在实时转绿 —— 本节撰写期间连续三次全量
实测得到 `20 failed` → `6 failed` → `6 failed`，数字十几分钟就过期。

唯一稳定的基线是**排除 A2 范围后的净基线**：

```bash
python3 -m pytest tests/unit -q -p no:randomly --ignore=tests/unit/workflow
# 721 passed, 1 skipped   ← 三次实测均为此值，稳定
```

**这个 721 才是并行的判据。** 全量跑的失败请按下表归因：

| 失败位置 | 判读 |
|---|---|
| `test_red_witness_*.py` | A2 的红绿中间态，**与本批无关**，不要去修 |
| 其他位置 | 先按 2.2 排除测试污染，再判回归 |

> 上一版记录的「35 个 web errors」（starlette `TestClient(app=...)` 签名问题）
> **已消失**，现为 0。说明有人修掉了 —— 又一个数字会过期的例证。

### 2.2 ⚠️ 实测到一类会误判的现象：跨目录测试污染

撰写期间实测到 `tests/unit/web/test_console_api.py` 有一个用例
**只在全量跑时失败**：

```bash
pytest tests/unit/web/test_console_api.py      # 11 passed  ← 单独绿
pytest tests/unit/web tests/unit/workflow      # console_api 绿
pytest tests/unit                              # console_api 红
```

它落在 A2 范围之外，看起来像是「有人引入了回归」，实际是全量收集下的
状态污染（模块级单例未还原之类）。**并行汇合时这类现象会被反复误判成
新任务的 bug。**

**判读纪律**：怀疑回归时，先单独跑那个文件。
单独绿 = 污染，不是你的改动引入的；单独也红 = 真回归。
按 DEV-PROTOCOL 第 2 节，这种情况应报 ❓ 而非 ❌ —— 并说明是污染而非失败。

> A4 的 9.1 早已预言了这类问题：`_manager.config` 是模块级全局单例，
> 一个用例不还原会污染其余全部用例。**A4 实施时必须用 `deepcopy` 快照还原**，
> 否则并行期间每个 agent 都会看到别人造成的假失败。

---

## 3. 真正的阻塞：地基未落盘

```
?? sw_lib/core/git_repo.py                  ← A1 核心模块
?? sw_lib/workflow/red_witness.py           ← A2 核心模块
?? tests/unit/workflow/test_red_witness_*.py  ← A2 的 9 个测试文件
?? docs/design/                             ← 14 份任务书全部未跟踪
```

worktree 是**基于 commit 的快照**，gitignore 与未跟踪的文件一律不会带过去。
实测新建 worktree 后上述文件**全部 MISSING**。后果分两层：

**第一层**：A3 的设计明写「直接调用 A1 的 `baseline_diff()`，**不要重写**」，
A6 的 O4 要读 A2 的 `red_witness`。派出去的 agent 看不见前置产出，
只会各自重写一份 —— 波 3 汇合时必然冲突。

**第二层（更严重）**：
```
$ git ls-files docs/design/ | wc -l
0
```
**14 份任务书连同 `DEV-PROTOCOL.md` 全部未提交。**
派进 worktree 的 agent 看不到自己的任务书，也看不到必须遵守的开发协议，
只能凭 prompt 里的一句话猜范围。这正是 C1/C3（判据缺席）在**派发环节**的重现 ——
我们要建的机制是「让审查者有独立判据」，而派发时却让开发者没有判据。

> **第一步不是派活，是 commit。** 等 A2 的 `red_witness` 用例全部转绿后，
> 把 A1/A2 产出与 `docs/design/` 一并提交到 `dev`，后续 worktree 从该 commit 分叉。
> 这一步省掉，后面所有并行都是负收益。

`scripts/wave.sh` 已对此设前置闸门：检出未跟踪依赖即拒绝派发
（确认要继续需显式设 `HARNESS_ALLOW_DIRTY_BASE=1`）。

---

## 4. 文件足迹矩阵

从 14 份文档的「范围」声明与正文引用抽取。**这张表是分组的依据**：

| 任务 | 独占文件（新增为主） | 共享文件（争用点） |
|---|---|---|
| B0 | `probe/baseline.py`、`workspace/probe/` | 只读 `config.yaml` |
| A3 | `workflow/fact_pack.py` | **`hooks/lib_run_tests.sh`（写）** |
| A4 | `core/config.py` | **`config/config.yaml`（改结构）**、`core/bootstrap.py` |
| A5 | `workflow/graph.py`、`workflow/state.py` | `core/bootstrap.py` |
| A6 | `workflow/objective_check.py` | `lib_run_tests.sh`（读）、`hooks/check_04-review.sh` |
| A7 | `workflow/counterexample.py` | `config.yaml`（加角色）、`lib_run_tests.sh`（读） |
| A8 | `workflow/design_review.py` | `config.yaml`（加角色）、**`hooks/04-review.md`** |
| A9 | `workflow/arbiter.py` | `stage_state.py`、**`hooks/04-review.md`**、`templates/04-review.md` |
| A10 | `workflow/archive_report.py` | `hooks/check_05-archive.sh`、`templates/05-archive.md` |
| A11 | `probe/injector.py`、`probe/experiment.py` | `sw_lib/probe/`（与 B0 同目录） |
| A12 | `hooks/01-brainstorming.md` | `prompts/builder.py`、`config.yaml` |

### 4.1 三个争用点

| 文件 | 争用者 | 性质 |
|---|---|---|
| `hooks/lib_run_tests.sh` | **A3 写**；A6 A7 读 | A3 改输出计数；A6/A7 复用 `_pytest_pythonpath()` / `_project_python()` |
| `hooks/04-review.md` | **A8 A9 都写** | A8 追加 `hook-04-09`；A9 重写 `hook-04-07` |
| `config/config.yaml` | **A4 改结构**；A7 A8 A12 追加 | A4 必须先落地，否则后三者加的角色会被结构改造推翻 |

除这三处，剩余任务的文件足迹**基本正交**。
所以：**不需要新框架，只需给这三个文件定单一写者。**

> `A4` 与 `A5` 都碰 `core/bootstrap.py`，但 A4 是 3.7 的 factory 签名改造、
> A5 是 4.3 消费该签名 —— 这是**接口两端**，A5 本就依赖 A4，属串行，不算并行冲突。
> 实测 `bootstrap.py:92` 的 `def factory(stage=stage, task_name=None)` 即改造对象。

---

## 5. 派发批次（在波次表之内再切一刀）

原则：**一个文件同一时刻只有一个写者。**

| 批 | 可同时派发 | 并行度 | 闸门条件 |
|---|---|---|---|
| **0** | **B0** 单独 | 1 | ⏰ 不可与任何 A 任务并行 —— 它采「改造前」快照，别人一改就污染 |
| **1** | **A4** 单独 | 1 | A4 独占 `config.yaml` 结构改造，**必须先落地** |
| **2** | **A3** + **A5** | 2 | A3 独占 `lib_run_tests.sh`；A5 只碰 graph/state，二者零交集 |
| **3** | **A6** + **A7** + **A8** | **3** | A3 落盘后开。三者互不写同一文件 |
| **4** | **A9** 单独 → 然后 **A10** + **A11** + **A12** | 3 | A9 必须晚于 A8（同写 `04-review.md`）；A9 落盘后三路全并行 |

**峰值并行 3。** 再多 agent 只会在那三个集成文件上排队。

### 5.1 两条容易踩的时序

**B0 必须最先，且不可补做。** 它依赖为零（刻意设计），现在就能跑。
A11 的对照实验需要「改造前」数据，改造一旦落地旧行为无法重现，**错过即永久丢失**。
B0 的 1.3 已实测基线部分污染（A0/A1 已实施），但审查侧 A6-A9 全未实施，
故对「审查能力」这一观测量仍然有效 —— **每多等一天污染多一层**。

**A9 必须晚于 A8。** A8 的 3.4 明写「编号已核实可用：现有 hook 到 `hook-04-08` 为止」。
若按完成时间而非批次合并，A9 会拿到一个自己没见过的编号表，该前提被打破。

### 5.2 已被实测消解的依赖

`README.md` 的依赖图里 A12 依赖 A9，但 A12 的 2.3 实测后**已部分消解**：
`supplement` 不需要改动 Gate 重置逻辑（原以为需保留 Gate 签名，实测保留反而错）。
故 A12 与 A9 只剩 `route.intent` 字段的**读依赖**，可在 A9 落盘后立即并行。

---

## 6. 隔离方式：一 worktree 一 agent

### 6.1 不要用 `bin/dispatch.sh`

**它已失效**：`ROOT_DIR` 算出 `/Users/yfwang`（仓库外），
并 source 一个重构后不存在的 `workflow/harness/config.sh`。
`config/status.sh` / `pr.sh` / `cleanup.sh` 有同样的路径问题。

### 6.2 用 `scripts/wave.sh`

```bash
scripts/wave.sh A3 --dry-run   # 先看将要做什么
scripts/wave.sh A3             # 建 .worktrees/A3，分支 codex/a3-fact-pack
scripts/wave.sh --list         # 看现有 worktree
scripts/wave.sh A3 --remove    # 收工清理（分支保留）
```

它做四件事：从 `docs/design/` 唯一匹配任务 ID（写错即报错）、
建 worktree 与分支、补齐 gitignore 拦下的文件、打印给 agent 的开场指令。

### 6.3 worktree 里必须补的东西

| 文件 | 不补的后果 |
|---|---|
| `config/credentials.yaml` | agent 起不来 |
| `config/.evidence_key` | **见下** |
| `docs/design/` | agent 看不到任务书（第 3 节） |
| `workspace/` | 任务状态目录，`config.py:21` 按 `ROOT/workspace` 解析 |

`.evidence_key` 那条最阴险：`evidence.py:get_key()` 在密钥缺失时**静默新建一把**。
于是 A9 在 worktree 里签的 route 证据，回主仓库校验会判 `tampered` ——
**而这个失败看起来完全像是 A9 的实现有 bug**，可能浪费掉一整轮排查。
`wave.sh` 用 symlink 保证全 worktree 同一把密钥（已实测 `cmp` 一致）。

### 6.4 状态天然隔离，不需要加锁

`config.py` 全部路径由 `Path(__file__).resolve()` 推导（`ROOT` 在 `config.py:17`，
`WORKSPACE` 在 `:21`），实测仓库内零 `/Users/` 绝对路径泄漏。
因此每个 worktree 的 `workspace/STATUS.json` 互不干扰，多个 `./sw` 可同时跑。

**已实测**：在 `.worktrees/A6/` 内跑 `pytest tests/unit/workflow -q` 得 `216 passed`。
worktree 是可直接干活的，不是空壳。

---

## 7. 汇合纪律

**按第 5 节的批次顺序合回 `dev`，不按完成时间合。** 理由见 5.1 的 A9/A8。

每次合并后跑一遍：
```bash
python3 -m pytest tests/unit -q -p no:randomly
```

判读顺序（严格按此三步，跳步会误判 —— 依据 2.1、2.2 的实测）：

1. **先看净基线**：`--ignore=tests/unit/workflow` 是否仍为 `721 passed`。
   不是，才说明净基线被破坏。
2. **失败在 `test_red_witness_*`**：A2 的中间态，与本批无关，**不要顺手修**
   （DEV-PROTOCOL 第 5 节：范围外的问题记录但不修）。
3. **失败在别处**：单独跑那个文件。单独绿 = 测试污染（2.2），报 ❓；
   单独也红 = 真回归，按 DEV-PROTOCOL 第 3 节第 3 条优先恢复单调性。

> 合并前后都要跑。只在合并后跑，分不清失败是自己引入的还是合进来的。

---

## 8. 为什么不建议写调度器

你要「不手动引入很多复杂度」，那就不要为这件事写编排器：

1. **峰值并行只有 3。** 三个任务的协调用第 5 节那张表就够，调度器的成本高于收益。
2. **真正的约束是「单一写者」，这是设计期的人工判断**，不是运行期能自动发现的。
   调度器只能在冲突发生后报错，不能替你决定谁先写 `hooks/04-review.md`。
3. **A5 本身就是「可配置数量的 reviewer 并行」。**
   先把 A5 做出来，harness 自己就有了并行能力，再回头用它跑剩下的任务。

> 一句话：用 worktree 加一张表撑过波 3；到波 4 时 A5 已落地，
> 并行能力由被开发的系统自己提供 —— 这比现在手搓一个更省，
> 且顺带验证了 A5 是真的可用。

---

## 9. 立即可执行的下一步

```bash
# 1. 等 A2 的 red_witness 用例全绿，然后固化地基（这是唯一的硬阻塞）
git add docs/design sw_lib/core/git_repo.py sw_lib/workflow/red_witness.py \
        hooks/rw_pytest_plugin.py tests/unit/core/test_git_repo.py \
        tests/unit/workflow/test_red_witness_*.py
git commit -m "feat: A1 任务级 git 仓库 + A2 红见证落盘；纳入 docs/design 任务书"

# 2. 波 0：抢救那个会流失的观测（依赖为零，可与第 1 步并行准备）
scripts/wave.sh B0

# 3. 波 1：A4 独占 config.yaml 结构改造
scripts/wave.sh A4

# 4. 波 2：两路并行
scripts/wave.sh A3 && scripts/wave.sh A5
```

**若只能做一件事，做 B0** —— 其余任务晚做只是慢，B0 晚做是永久失去。
