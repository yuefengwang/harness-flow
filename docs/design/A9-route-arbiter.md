# A9：Route 仲裁器 —— 三轨结论到路由决策

> 依赖：**A6**（客观轨）、**A7**（反例）、**A8**（设计意见）
> 被依赖：**A10**（归档报告读仲裁结论）、**A11**（探针以路由正确性为观测量）、
> **A12**（`route.intent` 的 `supplement` 语义由 A12 实现 01 侧行为）
> 范围：新增 `sw_lib/workflow/arbiter.py`；改 `stage_state.py` 的 Route 读写落点；
> 改 `hooks/check_04-review.sh` 的检查顺序；删 `parse_route_from_ai_output`；
> 改 `templates/04-review.md`、`hooks/04-review.md` 的 `hook-04-07`；
> `runtime.py` 的 `reroute_count` 持久化
> 不含：客观检查项实现（A6）、反例产出与执行（A7）、意见校验（A8）、
> `supplement` 返工回 01 之后的模板与 Gate 行为（**A12**）
>
> 上游依据：`docs/design-reviewer-independence.md` **7.4**（仲裁表）、**7.5**（返工收敛）
>
> 🔒 实施须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。末尾第 9 节为本任务特有的假绿风险。

> ⏰ **动手前先确认 B0 已执行。**
> 本任务属**审查侧改造**，一旦落地，「改造前 reviewer 对注入缺陷无反应」
> 这个观测量就再也无法重现。该基线由
> [`B0-baseline-capture.md`](B0-baseline-capture.md)（波 0，依赖为零）采集，
> **错过即永久丢失** —— A4 的 U4-3 与 A9 的 U9-1 将永远无法证明。
> 判断方法：`ls workspace/probe/baseline-*.json`。若为空，**先跑 B0**。

---

## 1. 目标与定位

### 1.1 要解决的问题

前面八份文档各自解决了「审查的输入」与「审查的形态」：
A3 换掉审查对象（事实而非自述），A4/A5 换掉模型（异构、多轨并行），
A6 把客观判定从 LLM 手里收回程序，A7 把主观意见的一部分变成可执行反例，
A8 给设计意见加了机械交叉校验。

但**这些结论最终要汇成一个动作：下一步去哪个阶段。**
现在这个动作由 agent 在正文里写一行 `**Route**: 05-Archive` 完成 ——
于是前面所有的独立性建设，在最后一步又收敛回「产出方自己宣布判决」。

A9 的职责是把这一步从 agent 手里拿走：
**agent 只提交结论与证据，路由由固定优先级的程序算出。**

> 判据：仲裁器不含 LLM 调用，不解析任何自然语言。
> 同一组三轨输入必须永远得到同一个 Route。

### 1.2 本任务的范围

| 在范围内 | 不在范围内 |
|---|---|
| 六级优先级仲裁表与 N1/N2/N3 特例 | 各轨结论的产出（A6 / A7 / A8） |
| Route 权威落点的迁移与签名 | `supplement` 回到 01 后的行为（**A12**） |
| `reroute_count` 持久化与收敛中止 | 阈值调参（第一批不设阈值，见 3.8） |
| 返工证据表的机械生成 | 证据表的人类可读排版优化 |
| 删除自然语言 Route 解析 | 反例的产出形态（A7） |
| 成立反例追加进 `red_witness`（A2 定案归属本任务） | `red_witness` 的冻结机制（A2） |

### 1.3 边界声明（必须诚实）

**仲裁器不产生新的判断力，它只是把已有结论按固定规则排序。**

三个具体的无能之处：

1. **垃圾进垃圾出**。三轨全部沉默时，仲裁器只能命中优先级 6（归档）。
   它无法分辨「确实没问题」与「审查者集体失职」——
   N1 只能挡住「提了反例但全不可复现」这一种失职，挡不住「什么都不提」。
2. **优先级顺序是人定的，不是推导出来的**。上游 7.4 那张表的次序
   （客观硬失败 > 反例 > 设计意见 > 需求层）反映的是证据强度直觉，
   没有实验依据。A11 的探针能观测它是否合理，但本批不调。
3. **它无法判断「这个反例值不值得修」**。反例成立即返工，
   不区分「核心逻辑错」与「边角 case」。severity 由 agent 自报，
   而自报的严重性不可信 —— 这是 A8 的 `verification` 想解决但只解决了一半的问题。

---

## 2. 现状：已实测的事实

本节每条断言均在当前代码上实测。**七条推翻或修正了上游写法**，
其中两条使上游 7.4 / 7.5 的设计前提不成立。

### 2.1 ⚠️ 头号问题：`MAX_REROUTE` 从未生效，`reroute_count` 从未落盘

上游 7.5 开篇写「`MAX_REROUTE = 3`（`core/config.py:30`）**已存在**」，
据此只补充了「同一反例不计新信息」等约束。**实测：这个上限从来没有生效过。**

`MAX_REROUTE` 的完整生命周期：

| 位置 | 行为 |
|---|---|
| `config.py:30` | 定义 `MAX_REROUTE = 3` |
| `runtime.py:26` | 传给 `build_harness_graph(max_reroute=...)` |
| `graph.py:66` | 收进参数，**函数体内从未使用** |
| `graph.py:108` | 赋值 `self.max_reroute = max_reroute` |
| 全仓 | **没有任何一处读取 `self.max_reroute`** |

`reroute_count` 同样是死的：它只存在于 `WorkflowState` 这个 TypedDict
（`workflow/state.py:28`），而 `WorkflowState` 每次
`LangGraphAdapter.invoke()` 都从 `metadata` 重建、默认 0
（`graph.py:132`），**从不写回 `.state`**。

实测（连续四轮 04→03 返工，探针走临时任务，已清理）：

```
round 1: stage=03-coding reroute_count=<ABSENT>
round 2: stage=03-coding reroute_count=<ABSENT>
round 3: stage=03-coding reroute_count=<ABSENT>
round 4: stage=03-coding reroute_count=<ABSENT>
.state 顶层键: ['health_config', 'id', 'stage', 'stage_idx',
               'stage_status', 'stages', 'updated_at']
route_history 长度: 4
```

第四轮仍在返工，`.state` 里连这个键都不存在。

**两个后果**，第二个是既有 bug：

1. 上游 7.5 的收敛设计**没有地基**。A9 不能「新增约束」，
   必须先把计数器建起来。
2. `commands.py:243` 用 `new_st.get("reroute_count", 0) > 0`
   判断「是否返工、要不要自动启动目标阶段」。
   该值恒为 0，所以**返工后从不自动拉起 agent**，
   用户必须手动 `./sw monitor`。注释写着「返工时自动启动目标阶段
   （无需用户手动 ./sw monitor）」—— 这个承诺从未兑现。

> `route_history` 是**可用**的（实测四轮四条），但它混着前进决策
> （`reset_route` 只在 `is_reroute` 时调用，前进到 05 的决策留在 `route` 里不进历史），
> 语义不纯，不适合直接当计数器。**定案：新建持久化计数器**（3.6）。

### 2.2 ⚠️ Route 决策完全没有签名保护（落点错位）

`core/evidence.py:38` 的 `EVIDENCE_FIELDS` 里
**已有一项 `"route"`，注释正是「A9：仲裁决策」** —— A0 实施时替本任务预留了位置。

但 `write_route` 写的是 `stages.04-review.route`
（`stage_state.py:274`），而签名算的是**顶层** `route` 键。两者从不相交。

实测：

| 操作 | `has_evidence` | `verify_evidence` |
|---|---|---|
| `write_route(by="arbiter")` 后 | `False` | `absent`（尚无任何证据字段） |
| 把 `stages.04-review.route.target` 由 `03-coding` 篡改为 `05-archive` | `False` | **`absent`** |
| 改为写顶层 `route` 键 | `True` | `valid` |
| 篡改顶层 `route.target` | `True` | **`tampered`** |

**路由决策当前可被任意篡改而不留痕迹。**
这是本套设计里最后一环的判据 —— 它不设防，
前面九份文档的独立性建设在最后一步全部作废。

**定案**（已与用户对齐）：顶层 `route` 为**权威判据**并纳入签名；
`stages.04-review.route` 降为 **UI 缓存**，由仲裁结论单向同步；
`read_route` 只读顶层。

⚠️ **连带改动，漏了则本条白做**：门禁脚本读的是
`./sw state get <task> 04-review route`（`check_04-review.sh:21`），
走的是 `stages` 那条路；`cli/commands.py:624` 的
`_STATE_FIELDS = ("gate", "route")` 与 `cmd_state_get` 也按 stage 取值。
**这条 CLI 契约必须同步改为读顶层** ——
否则篡改 UI 缓存即可骗过门禁，等于签名不存在。

### 2.3 ⚠️ 自然语言 Route 解析会把「反对归档」读成「归档」

`workflow/utils.py:24` 的第一优先级正则在 AI Output 段里
找反引号包住的阶段名，**不看上下文、不看否定词**。

实测三个输入：

| AI Output 内容 | 解析结果 |
|---|---|
| 未填写的模板（`Route: ___`） | `None` ✅ |
| `审查结论：建议路由: 05-Archive` | `05-archive` ✅ |
| **`我认为不应该走 05-Archive，因为测试全红。`**（阶段名带反引号） | **`05-archive`** ❌ |

第三行是本文档最该被记住的一条：**审查者明确反对归档，系统把它当成批准归档。**

该函数由 `ui/tui.py:1434` 的 `_auto_sign_off` 调用 ——
即**自动模式下**这是 Route 的唯一来源。

**定案**：A9 上线后**删除** `parse_route_from_ai_output` 及其调用点，
不保留兜底。留着等于留一条绕过仲裁器的暗路，而它已被证明会反向误判。

### 2.4 ⚠️ 门禁顺序倒置：先要决策，才允许检查

`check_04-review.sh:21-25` 在**任何客观检查之前**要求 Route 已存在，缺则 exit 1。

实测（无 Route 的 04-review 任务）：

```
[Hard Check] 04-review 门禁...
❌ 缺少 Route 决策（请在 TUI 中选择返工目标或批准归档）
EXIT=1
```

后面的回归测试（第 44-53 行）、README 校验（第 55-150 行）**一步都没跑**。

而仲裁器的**输入**恰恰是客观轨结论。现在的顺序是
「先有决策才允许检查」，A9 需要的是「先检查才能产出决策」。

**定案**：脚本拆成两段 —— **采集段**（跑全部客观检查、输出结构化结果，
不要求 Route）与**准出段**（校验 Route 存在且合法）。
仲裁器在两段之间运行。

### 2.5 ⚠️ 仲裁器写完 Route 会立刻被证据表卡死

`write_route(task, "03-Coding", by="arbiter")` 本身**能成功**
（实测返回 `True`，落盘 `{'target': '03-coding', 'decided_by': 'arbiter', ...}`
—— `by` 参数无白名单校验，任意字符串都收）。

但紧接着门禁在 `check_04-review.sh:30-41` 检查 Reroute Evidence 表：

```
[Hard Check] 04-review 门禁...
❌ Reroute Evidence 表中不能全是占位符（___），请填写具体问题
EXIT=1
```

即**仲裁器必须连带产出证据表**，否则自己写的 Route 过不了自己的门禁。

而现在填表逻辑在 TUI 里（`ui/tui.py:1139` 的 `_fill_reroute_evidence`），
数据源是用户的 `decisions` 拍板记录。仲裁器路径下用户还没拍板。

**定案**：证据表生成移到仲裁器，数据源改为三轨结构化结论（3.5）。

> 附带收益：`inject_reroute_context`（`workflow/utils.py:78`）注入给 03 的返工上下文
> 将**第一次含有真实信息**。T3 那次「用户要求补 README，agent 连续三轮
> 只是把原代码重新确认一遍」的病根就在这里 ——
> 注入的是「需返工修复的问题 / 详见审查结论」这种既像内容又没内容的话。

### 2.6 ⚠️ 界面上没有返工到 01 的入口

`ui/tui.py:1025` 的 `review_routes` 只有三项：

```python
review_routes = {"A": "05-Archive", "B": "03-Coding", "C": "02-Planning"}
```

上游 7.4 优先级 5 的 `01-brainstorming` **在界面上无处可选**。
严格说这不阻塞仲裁器（优先级 5 的条件是「仅能由用户提出」），
但用户表达不了需求层变更。

**定案**：面板加 D 选项，且**区分两种意图**（与用户对齐的结论）：

| 选项 | 目标 | `route.intent` | 语义 |
|---|---|---|---|
| D1 | `01-brainstorming` | `revise` | 需求判断错了，推翻已有决策重来 |
| D2 | `01-brainstorming` | `supplement` | 需求没错，只是有未定义的点需要补 |

A9 负责产出 `route.intent` 字段；**`supplement` 回到 01 之后的模板与
Gate 行为属 A12**。

> 为什么不新增一个阶段：实测 `STAGES` 是五元列表（`config.py:28`），
> 全仓有 **18 处 `STAGES[idx]` 索引**、**5 处 `len(STAGES) - 1` 终点判断**，
> 模板与 hooks 全按 `NN-name` 命名，
> `tests/e2e-flow/{driver,verify}.py` 还各自硬编码了一份五元副本。
> 插入阶段会让 `stage_idx` 语义整体错位、存量 `.state` 全部失效。
> 因此 `supplement` 做成 01 的**模式**，不是新阶段。

### 2.7 上游行号偏移

上游 7.4 末尾写「`stage_state.write_route()`（`stage_state.py:245`）」。
实际位置：

| 函数 | 实际行号 |
|---|---|
| `read_route` | 252 |
| `write_route` | **261** |
| `reset_route` | 283 |
| `read_route_history` | 308 |

纯笔误，本文档按实际行号写。

### 2.8 F8 矛盾确认存在（上游判断正确）

| 位置 | 要求 |
|---|---|
| `templates/04-review.md:18` | 「根据分析判断**直接填写 Route，无需等待用户确认**」 |
| `hooks/04-review.md:39` | 「Route 决策必须通过 ask_user 与用户交互确认 —— **agent 不得自行填写**」 |

同一份产出被两条规则反向要求。上游 7.4 称仲裁器上线后该矛盾「自然消失」——
**不完全对**：`templates` 那句话必须**显式删除**，否则 agent 会继续填 Route。
仲裁器不读它，但它仍在污染 agent 的行为与产出（3.7）。

---

## 3. 设计

### 3.1 仲裁器的位置与纯度

```
04-review 阶段
    │
    ├─ 采集段（check_04-review.sh 第一段）
    │     客观轨 A6 → objective.json
    │
    ├─ 主观轨并行（A5 动态图）
    │     A7 攻击者 → counterexamples/round-N/manifest.json（含执行判定）
    │     A8 设计审视者 → design_opinions/round-N/manifest.json（含 verification）
    │
    ├─ 【A9 仲裁器】纯程序，零 LLM
    │     读三轨结构化结论 → 六级优先级 → route 决策 + 证据表
    │
    └─ 准出段（check_04-review.sh 第二段）
          校验 Route 存在且合法 → 允许推进
```

三条纯度纪律：

1. **零 LLM 调用**。可用调用计数器断言（与 A6 的验收 7 同法）。
2. **不解析自然语言**。输入全部是 JSON；`parse_route_from_ai_output` 被删除。
3. **确定性**。同一组输入两次运行必须得到同一个 Route
   （验收 6 有专门的重放断言）。

### 3.2 六级优先级表（实现上游 7.4）

按序匹配，**首个命中即决定**，不累计不加权。

| 优先级 | 条件 | Route | 证据强度 | 需用户确认 |
|---|---|---|---|---|
| 1 | 客观轨 `hard_fail == true` | `03-coding` | 硬 | 否 |
| 2 | 存在 `CONFIRMED` 且 `spec_backed != inferred` 的反例 | `03-coding` | 硬 | 否 |
| 3 | 设计意见 `severity=high` 且 `verification.status == verified` 且 `scope=coding` | `03-coding` | 中 | **是** |
| 4 | 同上但 `scope=planning`（须过 N2） | `02-planning` | 中 | **是** |
| 5 | 需求层变更（**仅用户可提出**） | `01-brainstorming` | — | **是** |
| 6 | 全部通过（且未命中 N1） | `05-archive` | — | 否 |

**优先级 2 的收窄是本文档对上游的一处补充**：上游只写「存在可复现反例」。
但 A7 的 3.2 已定案 `spec_backed: inferred` 的反例
（无 spec 依据、攻击者自行推断期望值）**不单独触发返工** ——
否则「攻击者凭自己的想象宣布代码错了」会取得硬证据地位，
那正是自问自答的另一种形态。

`inferred` 反例的去处：不触发返工，**累积为 A12 的 `supplement` 候选清单**
并进入 A10 报告的「不确定」分类（3.8）。

**优先级 3/4 只认 `verified`**：A8 的三态里
`refuted` 不进仲裁（意见已被机械驳回），
`unverifiable` **仅作提示、不触发返工** ——
它意味着「我们校验不了」，不是「意见成立」。

### 3.3 N1 / N2 / N3 三条特例

**N1 空转防护**（上游原文）：若攻击轨提交了反例但**全部不可复现**
（无 `CONFIRMED`），且设计轨无 `verified` 意见，**不得走优先级 6**。
标记 `arbitration: no_valid_conclusion` 并交用户裁决。

> 语义：「审查者胡说八道」不等于「审查通过」。

⚠️ **N1 的边界必须写清**，否则会误伤：

| 场景 | 处置 |
|---|---|
| 提交了反例，全部 `INVALID` | **命中 N1**（提了但都不成立） |
| 提交了反例，全部 `BROKEN` / `TIMEOUT` | **命中 N1**，且 `reason` 区分（反例自身坏了） |
| **一条都没提交** | **不命中 N1** —— 走优先级 6 |

最后一行是 A9 的公开无能之处（1.3 第 1 条）：
「什么都没提」与「确实没问题」在数据上不可分。
N1 只挡住「提了但全不成立」。
**这条必须写进 A10 报告的「不确定」维度**，让它可见。

**N2 scope 交叉校验**（上游原文）：优先级 4 的 `scope=planning`
必须能在 `facts/plan.md` 中定位到对应条目，否则**降级为 `scope=coding`**（走优先级 3）。

> 语义：防审查者把抓不到的代码问题推给上游以逃避责任。

⚠️ A3 的 2.7 与 A8 的 3.4 已实测：`02-planning.md` 同样可能是**未填写的模板**，
此时 `plan.md` 为空态。**空态下不得降级** ——
没有对照物时降级等于「因为计划是空的，所以这不是计划问题」，荒谬。
空态时保留 `scope=planning` 并标 `n2: unverifiable`。

**N3 客观轨严重性不依赖 Route**：A6 的 3.2 已定案，本任务只需**不重新引入**。
具体地：`check_04-review.sh:153` 现有的
`if [ "$ROUTE_VAL" = "05-archive" ] && [ "$README_ERRORS" -gt 0 ]` 必须删除 ——
它正是那个循环依赖的实体。README 缺失一律按最严判定，进入 `hard_fail`。

### 3.4 决策记录的数据结构（顶层 `route` 键，纳入签名）

```jsonc
// .state 顶层
"route": {
  "target": "03-coding",
  "by": "arbiter",                    // arbiter | user
  "intent": "revise",                 // revise | supplement（仅 target=01 时有意义）
  "priority": 2,                      // 命中的优先级，1-6
  "reroute_count": 2,                 // 持久化计数器（3.6）
  "needs_user_confirm": false,        // 优先级 3/4/5 为 true
  "confirmed_by": null,               // 用户确认后填 "user"
  "arbitration": "ok",                // ok | no_valid_conclusion
                                      // | max_reroute_exceeded | no_new_information
  "inputs": {                         // 三轨输入的指纹，供重放与审计
    "objective_hash": "…",
    "counterexample_round": 2,
    "counterexample_hash": "…",
    "opinion_hash": "…"
  },
  "evidence": [                       // 机械生成，供证据表与返工上下文（3.5）
    {"source": "counterexample", "id": "ce-2",
     "detail": "src/auth.py::verify_token 对过期 token 返回 True",
     "spec_backed": "spec"}
  ],
  "decided_at": "2026-08-23T15:54:48"
}
```

`inputs` 的哈希是**重放判据**：验收 6 要求同一组输入两次仲裁得同一结果，
哈希让「是否同一组输入」可判定，而不是靠人眼比对。

> 为什么 `reroute_count` 放在 `route` 内而不是新的顶层键：
> 它与仲裁决策是同一份判据、共享一次签名，
> 不必往 `EVIDENCE_FIELDS` 里加新键（那会牵动 A0 的既有签名测试）。

### 3.5 证据表的机械生成（解决 2.5）

仲裁器把 `route.evidence` 渲染进 `04-review.md` 的 Reroute Evidence 表。

| 表列 | 数据来源 |
|---|---|
| 问题 | 客观轨 `detail` / 反例 `actual` / 意见 `evidence.text` |
| 严重程度 | 优先级 1-2 → `high`；3-4 → 意见自报的 `severity` |
| 归属阶段 | 命中优先级对应的 target |
| 具体位置/描述 | 反例的 `target` 文件函数 / 意见的 `location` |

三条纪律：

1. **不得输出占位符**。无证据可填时说明仲裁命中的是优先级 6 或 N1，
   那两种情况不写证据表（前者不返工，后者交用户）。
2. **不得编造第二行**。TUI 现有实现在只有一条理由时会把第二行删掉
   而不是编一条（`tui.py:1177` 的注释写明了原因）——
   这个做法正确，仲裁器沿用。
3. `_fill_reroute_evidence` 从 TUI 移除；用户手选路由时也走仲裁器的渲染函数
   （用户的选择填 `by: "user"`，证据取 `decisions`）。

### 3.6 `reroute_count` 持久化与收敛中止（解决 2.1）

**自增点**：`runtime.py:118` 的 `if is_reroute:` 分支 ——
那是全仓唯一的返工真相点。

```python
if is_reroute:
    inject_reroute_context(name, next_stage)
    bump_reroute_count(name)          # 新增，走 update_state 受控入口
```

**三条中止条件**，任一命中即停自动流程并交用户：

| 条件 | `arbitration` 值 | 依据 |
|---|---|---|
| `reroute_count >= MAX_REROUTE` | `max_reroute_exceeded` | 上游 7.5 |
| 本轮反例集合与上轮完全相同（A7 的 `round_diff()` 新增集为空） | `no_new_information` | 上游 7.5「每轮必须产生新信息」 |
| N1 命中 | `no_valid_conclusion` | 上游 7.4 |

**同时修掉 2.1 的第二个后果**：`commands.py:243` 的
`reroute_count > 0` 判断在计数器落盘后**第一次真正生效**，
返工会自动拉起目标阶段的 agent。
这是行为变更，须在验收里显式覆盖（验收 12）。

> ⚠️ 单调性风险：`tests/unit/workflow/test_reroute_cycle.py` 与
> `tests/unit/core/test_advance_routing.py` 中有多个连续 advance 的测试。
> 计数器上线后它们会开始累计。实测这些测试最多做两轮返工
> （`test_second_round_route_choice_takes_effect` 等），
> 低于 `MAX_REROUTE = 3`，**预期不受影响** ——
> 但必须实跑确认，不得假定（验收 15）。

### 3.7 用户确认与 `hook-04-07` 的关系（消解 F8）

这是一个**真实矛盾点**，不能靠「自然消失」蒙过去。

`hook-04-07` 现有规则是「Route 必须经 ask_user 与用户确认，
agent 不得自行填写」。仲裁器要自动决策优先级 1/2/6，看似违反。

**定案**：区分「agent」与「仲裁器」两个主体，规则改写为三条。

| 主体 | 权限 |
|---|---|
| **agent**（reviewer / adversary / design_critic） | **一律不得**填写 Route。它们只提交结论与证据 |
| **仲裁器** | 按 3.2 产出决策。优先级 1/2/6 直接生效；3/4/5 置 `needs_user_confirm: true` |
| **用户** | 确认或推翻仲裁结论；优先级 5 只能由用户发起 |

`hooks/04-review.md` 的 `hook-04-07` 须重写规则文本与决策树
（决策树本身与 3.2 的表一致，但要标注哪几级需确认）。
`templates/04-review.md:18` 那句「无需等待用户确认」**删除**（2.8）。

> **「无需用户确认」不等于「无人参与」**：Gate 签署仍是用户的批准动作，
> 且 04 的 Gate 必须在 Route 落定之后才可签（`tui.py:858-860` 的既有逻辑）。
> 优先级 1/2/6 省掉的只是「选路由」这一次交互，
> 用户仍在 Gate 处有一次否决机会。

### 3.8 `inferred` / `unverifiable` 的去处：只留档，不返工（第一批）

A7 的 `spec_backed: inferred` 反例、A8 的 `unverifiable` 意见、
A3 的 `spec_context: empty` 与「ADR 段全为占位符」，
是同一类信号：**审查者察觉到了什么，但没有业务真理可依据。**

这些信号在当前设计里全是死路（不触发任何路由）。A9 把它们收集起来：

```jsonc
"route": {
  "unresolved": [                      // 不触发返工，仅留档
    {"source": "counterexample", "id": "ce-3", "spec_backed": "inferred",
     "module": "src/auth.py"},
    {"source": "opinion", "id": "op-5", "status": "unverifiable",
     "module": "src/auth.py"}
  ]
}
```

**第一批不设阈值、不触发 `supplement` 返工**（与用户对齐的结论）：
`unresolved` 全部进入 A10 报告的「不确定」分类，先收集真实数据。

理由：触发阈值（例如「同一模块累积 3 个无依据点」）目前是拍的，
没有数据支撑。A10 的 1.1 已写「**不确定的数量本身就是最有价值的指标**」——
先让这个指标跑一段时间，再由 A11 的探针观测真实分布来定阈值。

> 提前把机制建好但不开启，比先开一个错的阈值再回调更省事：
> `unresolved` 的采集是纯记录，无行为副作用；
> A12 实现 `supplement` 时直接消费这个字段即可。

### 3.9 成立反例追加进 `red_witness`（A2 定案归属本任务）

A2 的第 6 节定案：「反例成立并修复后，该反例应追加进
`red_witness.failed_nodes`（单调性）」，归属 A9。

触发时机：**不是反例成立时，而是返工后该反例转绿时**。

```
反例 CONFIRMED → 优先级 2 → 返工 03 → 修复 → 回到 04
    → 该反例重跑变 INVALID（不再复现）
    → 此时追加进 red_witness.failed_nodes
```

> 为什么不在成立时就追加：`red_witness` 的语义是
> 「这些节点曾经红过、现在必须绿」。反例刚成立时它是红的，
> 追加进去会让 A6 的 O4（全部节点转绿）立刻失败，
> 把返工路径堵死。

上游 R10「oracle 成本单调递减」在此落地：同一个 bug 不能反复出现。

---

## 4. mock 模式

仲裁器是**纯程序逻辑，不依赖 LLM**，因此 mock 与真实模式下
**仲裁行为完全一致** —— 与 A6 同构。

mock 影响的只是三轨输入从哪来：

| 轨 | mock 下的来源 |
|---|---|
| A6 客观轨 | 照常真实执行（本来就无 LLM） |
| A7 反例 | 夹具提供的固定反例集（A7 第 4 节），**照常真实执行判定** |
| A8 设计意见 | 夹具提供三条覆盖 `verified`/`refuted`/`unverifiable` 的意见（A8 第 4 节） |

A8 的 mock 夹具**刻意覆盖三态**，正是为了让本任务的分级逻辑可测。
因此 A9 的仲裁分级测试**不需要真实 LLM**，全部可在 mock 下跑完。

---

## 5. 与其他任务的接口

| 任务 | A9 依赖 / 提供 | 契约 |
|---|---|---|
| **A6** | 消费 `objective.json` 的 `hard_fail` / `hard_fail_ids` | `unavailable` 不算通过；N3 的循环依赖由本任务删除 `check_04-review.sh:153` 落实 |
| **A7** | 消费 `CounterexampleResult` 列表与 `round_diff()` | `spec_backed=inferred` 不进优先级 2（3.2）；转绿后追加进 `red_witness`（3.9） |
| **A8** | 消费含 `verification` 的意见 | 只有 `verified` 进优先级 3/4；`unverifiable` 仅提示 |
| **A2** | 写 `red_witness.failed_nodes` | 时机是**反例转绿时**，非成立时（3.9） |
| **A0** | 顶层 `route` 键纳入 HMAC 签名 | `EVIDENCE_FIELDS` 已含 `"route"`，**无需修改该常量**（2.2） |
| **A3** | 消费 `facts/plan.md` 做 N2 校验 | 空态判 `unverifiable`，**不降级**（3.3） |
| **A5** | 在并行分支汇聚后运行 | 仲裁器是 join 节点，须等全部主观分支结束 |
| **A10** | 提供 `route` 全字段与 `unresolved` | `unresolved` 进「不确定」分类；N1 的盲区须在报告中可见 |
| **A12** | 提供 `route.intent` 与 `unresolved` | A12 实现 `supplement` 回到 01 后的模板与 Gate 行为 |
| **A11** | 路由正确性是探针的主要观测量 | 注入缺陷后应命中优先级 1 或 2，而非 6 |

---

## 6. 实施顺序（内部）

1. **Route 落点迁移**（2.2）：顶层 `route` 键 + `read_route` 改读顶层 +
   `stages.04-review.route` 降为单向同步的 UI 缓存。
2. **CLI 契约同步**（2.2 的连带改动）：`cmd_state_get` 与 `_STATE_FIELDS` 读顶层。
3. **`reroute_count` 持久化**（3.6）：`bump_reroute_count` + `runtime.py` 自增点。
4. **门禁脚本拆段**（2.4）：采集段 / 准出段，删除 `:153` 的循环依赖。
5. **仲裁器核心**（3.2 / 3.3）：六级表 + N1/N2/N3，纯函数、输入输出皆 JSON。
6. **证据表生成**（3.5）：从 TUI 移出。
7. **收敛中止**（3.6）：三条中止条件。
8. **删除自然语言解析**（2.3）：`parse_route_from_ai_output` + 调用点 + 模板那句话。
9. **`unresolved` 采集**（3.8，纯记录）。
10. **`red_witness` 追加**（3.9）。
11. **TUI 面板 D 选项与 `intent`**（2.6）。

> 第 5 步是纯函数，**可以先于 1-4 步单独测试** ——
> 给定三轨 JSON 就能验证仲裁表，不需要跑真实任务。
> 这让本任务的核心逻辑最先可验证。
>
> 第 1、2 步必须**同一次提交**：只改落点不改 CLI 契约会让门禁读不到 Route，
> 全部任务立刻卡死在 04。

---

## 7. 验收标准

**机制接通类**：

1. 仲裁器是纯函数：给定三轨 JSON，返回决策 dict，**不读 `.state`、不写盘**
   （落盘由调用方负责，便于测试）。
2. 六级优先级各有一个用例，命中的 `priority` 与期望一致。
3. 优先级冲突时**取最高级**：同时存在客观硬失败与 `verified` 高危意见时，
   `priority == 1`。
4. `route` 落在 `.state` **顶层**，`has_evidence` 为 `True`。
5. `stages.04-review.route` 与顶层 `route.target` 始终一致（单向同步）。
6. **确定性重放**：同一组输入连续仲裁两次，`target` / `priority` /
   `inputs` 哈希全部相同。
7. 客观检查在无 Route 时可完整执行（2.4 的回归锚点）。
8. 仲裁器执行过程**零 LLM 调用**（调用计数器断言，与 A6 验收 7 同法）。
9. `parse_route_from_ai_output` 及其调用点已删除，全仓 grep 无残留。
10. 全部既有测试通过（**单调性**）。

**有效性类**（核心）：

11. **Route 篡改可检出**（2.2 的回归锚点）：
    写入仲裁决策后把顶层 `route.target` 由 `03-coding` 改为 `05-archive`，
    `verify_evidence` 返回 **`tampered`**。

    按迁移前的落点（`stages.04-review.route`）此条**必红** ——
    实测返回 `absent`，篡改毫无痕迹。

12. **UI 缓存篡改骗不过门禁**（2.2 连带改动的回归锚点）：
    只篡改 `stages.04-review.route` 而不动顶层，
    `./sw state get` 与门禁读到的仍是顶层的**正确值**。

    漏改 CLI 契约时此条必红。这一条比 11 更容易被漏 ——
    11 会在签名测试里自然暴露，12 只在「攻击者改的是缓存」这个具体场景下暴露。

13. **返工上限真实生效**（2.1 的回归锚点）：
    连续返工至 `reroute_count` 达 `MAX_REROUTE`，
    第 4 次仲裁返回 `arbitration: max_reroute_exceeded` 且不自动推进。

    在当前代码上此条**必红**：实测四轮后 `.state` 里连 `reroute_count` 键都没有。

14. **返工后自动拉起 agent**（2.1 第二个后果的回归锚点）：
    返工推进后 `new_st["reroute_count"] > 0` 成立。
    改造前恒为 0，此条必红。

15. **收敛中止：同一反例不算新信息**：
    第二轮提交与第一轮实质相同的反例（A7 的 `round_diff()` 新增集为空），
    仲裁返回 `no_new_information` 而非再次返工。

16. **N1 三种边界正确**（3.3 的表）：
    全 `INVALID` → 命中 N1；全 `BROKEN` → 命中 N1 且 `reason` 区分；
    **一条未提交 → 不命中 N1，走优先级 6**。

    第三种是刻意的放行，**必须显式断言**，否则实现者会顺手把它也拦下来，
    于是「三轨全沉默」的任务永远无法归档。

17. **`inferred` 反例不触发返工**（3.2）：
    只有 `spec_backed: inferred` 的 `CONFIRMED` 反例时，
    **不命中优先级 2**，且该反例出现在 `unresolved` 中。

18. **`unverifiable` 意见不触发返工**（3.2）：
    只有 `unverifiable` 高危意见时不命中优先级 3/4，且进入 `unresolved`。

19. **N2 空态不降级**（3.3）：
    `plan.md` 为空态时 `scope=planning` 的意见**保持** `planning`
    并标 `n2: unverifiable`，而非降级为 `coding`。

20. **N3 循环依赖已消除**：README 缺失在返工与归档两种情形下
    进入 `hard_fail` 的结果**一致**（与 A6 验收 3 呼应，但此处测的是
    `check_04-review.sh:153` 那段已被删除）。

21. **证据表机械生成且无占位符**（2.5 的回归锚点）：
    仲裁器写入 Route 后**直接跑门禁应通过**，
    不需要任何人工填表。改造前此条必红（实测卡在「不能全是占位符」）。

22. **反例转绿后进入 `red_witness`**（3.9）：
    反例成立 → 返工 → 修复 → 该反例转 `INVALID` 后，
    `red_witness.failed_nodes` 含该节点，且**成立当时不含**。

> 第 11、12、13 条是本任务的核心判据。
> 11/12 保证路由决策不可伪造，13 保证返工不会无限循环 ——
> 后者是上游误以为已经存在的东西。

---

## 8. 风险与遗留

| # | 风险 | 处置 |
|---|---|---|
| R1 | Route 落点迁移会让存量任务的 `.state` 读不到 Route，卡在 04 | 读路径**双读兜底**：顶层缺失时回落读 `stages`，并**立刻写回顶层**（一次性迁移）。写路径只写顶层。迁移逻辑保留一个版本周期后删除 |
| R2 | `reroute_count` 上线让既有测试开始累计并触达上限 | 实测既有测试最多两轮返工，低于上限。**但必须实跑全量确认**（验收 10）。夹具需提供重置计数器的入口 |
| R3 | 优先级 1/2 不需用户确认，误判会直接返工 | 客观硬失败与可复现反例都是硬证据，误判概率低。用户仍可在 Gate 处否决（3.7）。真实误判率由 A11 观测 |
| R4 | 仲裁器成为单点：它错了全流程错 | 它是纯函数、无 LLM、可重放（验收 6），且逻辑量小。**这是刻意的设计权衡**：把不确定性集中到一个可测的点，好过散落在多个 agent 的自述里 |
| R5 | 三轨输入缺失时（某轨崩了）仲裁行为未定义 | A5 的验收 5 已定：崩溃分支产出 `status: error` 条目。**定案**：任一主观轨 `error` 时不得走优先级 6，按 N1 同等处置（交用户） |
| R6 | 删除 `parse_route_from_ai_output` 会让自动模式在仲裁器故障时无路可走 | **接受**。宁可停下交用户，也不要用会把「反对归档」读成「归档」的解析兜底（2.3） |
| R7 | 证据表渲染改动 `04-review.md`，与 agent 的产出可能冲突 | 沿用 `render_gate_section` 的**单向映像**原则（`stage_state.py:473` 注释）：文件是状态的映像，重复区块最多是显示噪音 |

**明确遗留、不假装解决的问题**：

- **U9-1**：「三轨全沉默」与「确实没问题」不可分（1.3 第 1 条、验收 16 第三种）。
  N1 挡不住集体失职。当前只能靠 A10 报告让它可见，
  **真正的解法是 A11 的变异探针** —— 注入已知缺陷后若仍走优先级 6，
  说明整套审查是空转。这是 A11 存在的根本理由。

  > ⚠️ **回填：该证明依赖一份不可重采的对照数据**。
  > A11 需要「改造前」的基线才能证明「提升」，
  > 而本任务实施后旧行为即无法重现。该采集已拆为独立任务
  > **[`B0-baseline-capture.md`](B0-baseline-capture.md)，时序为波 0** ——
  > **必须在本任务实施之前执行**。
  > 对照组的期望结果正是「注入缺陷后 `route.target` 仍为 `05-archive`」，
  > 即本条描述的空转的直接证据（B0 的 5 节、验收 7）。
  > **若 B0 被跳过，U9-1 将永久无法证明。**
- **U9-2**：优先级顺序无实验依据（1.3 第 2 条）。本批不调。
- **U9-3**：反例的 severity 由 agent 自报，仲裁器不区分「核心错」与「边角 case」
  （1.3 第 3 条）。一个边角反例会和核心 bug 一样触发返工。
  可能造成返工疲劳，需真实数据后再定策略。
- **U9-4**：`supplement` 的触发阈值未定（3.8）。第一批只留档。
  这是**刻意推迟**，不是遗漏 —— 拍一个错的阈值比没有阈值更难纠正。
- **U9-5**：用户推翻仲裁结论时，`route.by` 变为 `user` 但
  **仲裁器的原始结论会被覆盖**。审计上应保留「仲裁器说 A、用户改成 B」这个分歧，
  当前只有 `route_history` 能间接看出。完整的分歧记录留给 A10。

---

## 9. 本任务的红绿要点

### 9.1 自指风险

A9 是**判据的判据** —— 它决定「审查结论如何转成动作」。
自指风险有两层：

**第一层**：仲裁器的测试需要构造三轨输入，
而三轨输入的**格式定义在 A6/A7/A8 的文档里**。
若我照自己的理解造夹具，测的是「我的理解自洽」，不是「接口正确」。

纪律：夹具字段必须**逐个对照 A6 的 4 节、A7 的 3.2/3.4、A8 的 3.1** 抄写，
且在夹具文件里注明每个字段的出处文档与节号。
A6/A7/A8 若尚未实施，夹具即为**契约声明**，
它们实施时必须以此为准 —— 不一致时改的是实现，不是夹具。

**第二层，更重要**：本任务的核心验收（11/12/13）都是
「改造前必红」的形态。**而「必红」这件事本身要能被验证。**

纪律：这三条必须**在改造前先跑一遍并记录实际失败输出**
（本文档 2.1/2.2 的实测表就是这个记录），
改造后再跑一遍看转绿。只在改造后跑绿的测试**不能证明它测到了东西**。

### 9.2 环境自伤风险

1. **Route 落点迁移会改 `.state` 结构**。测试必须用临时任务名
   （`pytest-*` 前缀）并在 teardown 清理。
   **绝不可**在 `workspace/tasks/` 下的真实任务上验证 —— A0 实施期
   曾用真实路径探针截断 `workspace/tasks/T1/.state`，那次教训适用于此。
2. `reroute_count` 的测试会连续 advance 多轮。
   每轮都写 `.state`，须确认用的是隔离任务。
3. 门禁脚本拆段后，采集段会**真实跑 pytest**（A6 的 O2）。
   测试须指向 `tmp_path` 造的假仓库，不指向 harness 自身 ——
   `check_04-review.sh:44-47` 的注释记录过一次事故：
   在 harness 根目录直接跑 pytest 会递归执行整套测试并让 advance 无限挂住。

### 9.3 红的正确形态

| 验收项 | 红的正确形态 | 假绿风险 |
|---|---|---|
| **11 Route 篡改可检出** | 落点仍在 `stages` 下时，断言 `tampered` 会得到 `absent`，为红 | 断言写成「不是 `valid`」—— `absent` 也不是 `valid`，于是迁移前后都绿，缺陷被完整保留。**必须断言等于 `tampered`** |
| **12 UI 缓存篡改骗不过门禁** | 漏改 `cmd_state_get` 时，门禁读到被篡改的缓存值，断言为红 | 测试里直接调 `read_route()` 而不走 `./sw state get` —— 那测的是 Python 层，而门禁走的是 CLI。**必须真实执行 CLI** |
| **13 返工上限生效** | 当前代码上第 4 轮仍正常返工，断言 `max_reroute_exceeded` 为红 | 用 mock 塞一个假的 `reroute_count` 进 `.state` 再断言 —— 那测的是「读到 3 会停」，没测「计数器会涨到 3」。**必须真实连续 advance** |
| **14 返工自动拉起 agent** | 改造前 `reroute_count` 恒为 0，断言 `> 0` 为红 | 断言 `.get("reroute_count") is not None` —— 键存在但为 0 也能过 |
| **16 N1 三边界** | 「一条未提交」若被实现者顺手拦下，断言「走优先级 6」为红 | 只测「全 INVALID 命中 N1」这一种，漏掉放行分支。于是三轨全沉默的任务永远无法归档，而测试全绿 |
| **17 inferred 不返工** | 实现若只判 `verdict == CONFIRMED`，断言「不命中优先级 2」为红 | 夹具里的 `inferred` 反例同时也是 `INVALID` —— 那它本来就不该命中，测不出 `spec_backed` 的过滤作用。**夹具必须是 `CONFIRMED` + `inferred` 的组合** |
| **19 N2 空态不降级** | 实现若「找不到就降级」，断言保持 `planning` 为红 | 用一个**非空但不含该条目**的 `plan.md` 做夹具 —— 那是应该降级的情形。**必须用空态 `plan.md`**，两者都要测 |
| **21 证据表无占位符** | 仲裁器不生成证据表时，跑门禁会卡在占位符检查，为红 | 测试只断言「表里没有 `___`」而不实跑门禁 —— 门禁的判定条件是「至少 3 行且非全占位」，行数不足同样会失败 |
| **22 反例转绿才入 red_witness** | 实现若在成立时就追加，断言「成立当时不含」为红 | 只断言最终含有，不断言成立当时不含。于是「立刻追加」的错误实现也绿，而它会让 A6 的 O4 永久失败、返工路径被堵死 |
| **6 确定性重放** | 实现里若掺入时间戳或字典遍历顺序，两次结果不同，为红 | 两次调用之间没改任何输入，而实现恰好是稳定的 —— 应**额外**断言 `inputs` 哈希相同，把「输入相同」也钉住 |

### 9.4 特别提醒：第 11 条极易被「不是 valid」写法掩盖

写这条测试时最自然的手法是：

```python
write_route(task, "03-coding", by="arbiter")
tamper_route_target(task, "05-archive")
assert verify_evidence(read_state(task)).status != "valid"   # <-- 迁移前后都绿
```

迁移前实测返回的是 `absent`（根本没有证据字段），它同样 `!= "valid"`。
于是这条测试在**落点错误的情况下也是绿的**，2.2 那个「路由决策毫无保护」
的缺陷被完整保留下来。

**必须断言 `status == "tampered"`**，并在注释里写明
`absent` 是迁移前的实测值、不可接受。

同理，第 13 条不可用「塞假计数器」的写法 ——
那会把「计数器不会涨」这个真实缺陷测成绿。

### 9.5 单调性

三处会改变既有行为，均须显式分辨「真实检出」与「误判」：

1. **返工后自动拉起 agent**（3.6）：这是修复一个从未兑现的承诺，
   但对习惯了手动 `./sw monitor` 的用户是行为变更。属预期行为。
2. **README 缺失一律按最严判定**（N3）：会让部分存量任务在返工路径上
   也判 `hard_fail`。与 A6 的 R1 同源 ——
   **不得用放宽判定让存量任务变绿**。
3. **Route 落点迁移**：存量任务靠 R1 的双读兜底一次性迁移。
   按 DEV-PROTOCOL 第 5 节，**记录但不顺手改用户数据** ——
   迁移在读取时惰性发生，不做批量重写。

---

## 10. 回滚

| 层 | 回滚方式 |
|---|---|
| 仲裁器核心 | 独立模块，不被调用即失效 |
| Route 落点 | 双读兜底使回滚安全：改回只读 `stages` 即可，顶层键留着无害 |
| `reroute_count` | 纯新增子字段，停止自增即回到现状 |
| 门禁脚本拆段 | 两段可合回一个脚本，顺序恢复 |
| `parse_route_from_ai_output` | **已删除，回滚需从 git 历史恢复** |
| 证据表生成 | `_fill_reroute_evidence` 需从 git 历史恢复到 TUI |
| TUI 的 D 选项 | 纯新增，移除即可 |

> **回滚 A9 意味着放弃本套设计的目的**：路由决策会重新回到
> 「agent 在正文里写一行、正则去解析」的状态，
> 而那个正则已被实测证明会把「反对归档」读成「归档」（2.3）。
> A6/A7/A8 三轨的结论会全部失去出口 —— 它们产出的结构化证据
> 没有任何机制转成动作。
>
> 因此回滚 A9 必须作为**显式决策记录**，
> 并同时说明三轨结论改由什么机制消费。
> 部分回滚（保留落点迁移与计数器，仅关闭自动仲裁）是更可取的降级路径：
> 那样 Route 仍受签名保护、返工仍有上限，只是决策回到用户手选。
