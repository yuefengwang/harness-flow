# A5：LangGraph 动态图 —— 可配置数量的审查者并行

> 依赖：**A4**（多角色配置）
> 被依赖：A7、A8
> 范围：`sw_lib/workflow/graph.py`、`sw_lib/workflow/state.py`、`sw_lib/core/bootstrap.py`
> 不含：审查者自身的 prompt 与判定逻辑（属 A7 / A8）
>
> 🔒 **开发纪律**：实施本任务须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)
> （红绿六步、三态报告、自循环四条）。本任务的特有要点见第 8 节。

---

## 1. 目标

让 04-review 阶段的审查者**数量与模型由配置决定**，而非硬编码。

```yaml
harness:
  review:
    objective:                      # 客观轨：纯程序，无 LLM
      enabled: true
    subjective:                     # 主观轨：N 个 LLM 审查者
      - role: adversary
        model: opencode/mimo-v2.5-free
        kind: counterexample
      - role: design_critic
        model: gemini-2.0-flash
        kind: design_review
      - role: security_critic       # 增删只改配置，不改代码
        model: claude-sonnet-4
        kind: design_review
```

**验收的本质**：增加或删除一个审查者，**只改 YAML，不动 Python**。

> ✅ **配置形态已定案以本节为准**（A4 的 3.1）：
> 上游 `design-reviewer-independence.md` 6.3 提出的另一形态
> `stage_roles: {04-review: [adversary, design_critic]}` **不采用** ——
> 两套并存会产生两个真相来源。
>
> 理由：本节的 `harness.review` 能表达 `kind` 与 `objective.enabled`，
> 列表形态表达不了；且本文档已按此写完 `Send` 派发细节（4.2）。
> `stage_roles` **保持 stage -> str 不变**，继续服务其余四阶段
> 与「`subjective` 为空时的单角色回落」。
>
> ⚠️ A4 的 2.1 实测：直接把 `stage_roles` 的值改成列表会让
> `resolve_agent_type` / `resolve_agent_model` / `get_tools_for_stage`
> **全部抛 `TypeError: unhashable type: 'list'`**（五处 `.get(stage)`
> 的返回值被当作 `roles` 的键）。故该形态也不能靠改 YAML 试探。

---

## 2. 现状约束（已核实）

| 位置 | 现状 | 阻碍 |
|---|---|---|
| `sw_lib/workflow/graph.py:78` | `workflow.add_node(s.stage, ...)` —— 一阶段一节点 | 无法为同一阶段建多个节点 |
| `sw_lib/workflow/graph.py:84` | `add_edge(name, END)` —— 每阶段直连 END | 无 fan-out / fan-in 结构 |
| `sw_lib/workflow/state.py` | `WorkflowState` 无并行结果容器 | 多分支结果无处汇聚 |
| `sw_lib/core/bootstrap.py:92` | `factory(stage, task_name)` 一 stage 一 agent | 无法产出多个 agent |

> ⚠️ **A4 修正了上游 6.3 的一处位置引用**：`get_tools_for_stage` 不在
> `toolbox.py:254`，而在 **`sw_lib/core/config.py:219`**。
> 更重要的是它有**两个消费者**，而上游表格只列了 `Toolbox` 那个：
>
> | 消费者 | 位置 | 是否真实生效 |
> |---|---|---|
> | `Toolbox.__init__` | `toolbox.py:366` | ❌ 只服务 gemini（A0 的 2.5） |
> | `OpencodeAgent` | **`opencode.py:181`** | ✅ **当前唯一真实生效** |
>
> 本任务的 `role_id` 传导须到达第二行，否则对实际运行的 agent 无效。

环境：langgraph **0.6.11**（已确认），支持 fan-out / fan-in 与 `Send` API。

---

## 3. 设计

### 3.1 图结构

04-review 从单节点展开为子图：

```
     START
       │
  04-review-prepare        (事实包就绪校验，A3 产物)
       │
       ├──────────┬──────────┬─────────────┐   fan-out
       │          │          │             │
  objective   review-0   review-1   ...  review-N
  (无 LLM)    (agent)    (agent)         (agent)
       │          │          │             │
       └──────────┴──────────┴─────────────┘   fan-in
       │
  04-review-arbiter         (A9 仲裁器)
       │
      END
```

**主观分支数量由配置长度决定**，用 `Send` API 动态派发。

### 3.2 状态扩展

`WorkflowState` 新增并行结果容器。**必须用 reducer**，否则并发写覆盖：

```python
# sw_lib/workflow/state.py
class WorkflowState(TypedDict):
    ...  # 现有字段不变

    # 主观轨结果：每个 reviewer 追加一条，用 add reducer 合并
    review_findings: Annotated[List[Dict[str, Any]], add]

    # 客观轨结果：单份，直接覆盖
    objective_result: Optional[Dict[str, Any]]
```

> 现有 `history_outputs` 已用 `Annotated[List[...], add]`（`state.py`），
> 沿用同一模式，与既有风格一致。

### 3.3 产出隔离（关键约束）

并行分支**互不可见对方产出**。这不是性能考虑，而是正确性要求：
若先完成的审查者结果进入后者的 prompt，后者会被锚定 ——
那是 C1（上下文污染）在主观轨内部的复现。

LangGraph 的 fan-out 天然满足：各分支从**同一份**父状态派生，
互相看不到兄弟分支的写入。**实现时不得为了「让第二个 reviewer 参考第一个」
而破坏这一点。**

### 3.4 失败隔离

单个审查者失败（超时、模型不可用、输出不可解析）**不得中断其他分支**：

| 情形 | 处置 |
|---|---|
| 某分支异常 | 记录 `status: "error"` 进 `review_findings`，继续其他分支 |
| 全部主观分支失败 | 客观轨结果仍有效，仲裁器按 A9 的 N1 规则交用户裁决 |
| 客观轨失败 | **硬失败** —— 它是程序流程，失败意味 bug 或环境问题 |

### 3.5 并发上限

配置 `review.max_parallel`（默认 3）。审查者数量超过上限时分批执行。
理由：多个 LLM 并发会撞 API 限流，而限流导致的失败会被误记为「审查者无发现」。

---

## 4. 实施要点

### 4.1 `build_harness_graph` 改造

保持一阶段一 invoke 的整体模型（`graph.py` 的注释已说明 TUI 负责阶段跳转），
**只把 04-review 内部展开为子图**。其余阶段结构不变。

```python
def build_harness_graph(stages, max_reroute=MAX_REROUTE):
    workflow = StateGraph(WorkflowState)
    for s in stages:
        if s.stage == "04-review":
            _add_review_subgraph(workflow, s)   # 展开
        else:
            workflow.add_node(s.stage, create_stage_node(s))
            workflow.add_edge(s.stage, END)
    ...
```

### 4.2 动态 fan-out

用条件边加 `Send` 按配置派发：

```python
def _dispatch_reviewers(state: WorkflowState):
    from ..core.config import get_review_config
    cfg = get_review_config()
    sends = []
    if cfg.objective_enabled:
        sends.append(Send("04-review-objective", state))
    for idx, r in enumerate(cfg.subjective):
        sends.append(Send(f"04-review-subjective-{idx}", {**state, "_role": r.role}))
    return sends
```

### 4.3 agent factory 改造

`bootstrap.py:92` 的 `factory(stage, task_name)` 增加 `role_id` 参数
（由 A4 提供解析能力）。签名变为 `factory(stage, task_name, role_id=None)`，
缺省 `None` 时行为与现在完全一致。

---

## 5. 验收标准

1. 配置两个主观审查者时，04 阶段实际启动 **2 个** agent，模型各自不同。
2. 配置改为三个，**不修改任何 Python 代码**即可启动 3 个。
3. 配置改为零个主观审查者时，仅客观轨执行，流程不报错。
4. 各分支 prompt 中**不含**兄弟分支的产出（隔离验证）。
5. 人为让一个分支抛异常，其余分支仍完成，`review_findings` 含一条 `status: error`。
6. `review_findings` 在并行写入后条目数等于分支数（reducer 正确性）。
7. 现有单角色配置与全部既有测试**行为不变**（向后兼容）。

---

## 6. 风险

| # | 风险 | 处置 |
|---|---|---|
| R1 | 并行写 `.state` 竞争 | 依赖 A0 的 `update_state`（原子写 + 文件锁）；子节点**不直接写** `.state`，结果经 `WorkflowState` 汇聚后由仲裁器统一落盘。A0 的 2.3 已实测「后写覆盖前写」会静默丢失 |
| R2 | TUI / Web 的单 agent 假设 | `LangGraphAdapter.active_stage` 与 `stage.active_agent`（`engine.py`）假设单 agent。需改为可返回多 agent 列表，或在多 agent 时降级显示聚合状态 |
| R3 | mock 模式 | `MockAgent` 需支持按 role 返回不同产出，否则多分支产出相同，测试无意义 |
| R4 | API 限流 | `max_parallel` 限流 + 失败重试；重试耗尽记为 error 而非「无发现」 |

---

## 7. 回滚

配置中 `review.subjective` 为空列表时退化为「仅客观轨」；
删除 `review` 配置段则完全回落到现有单 reviewer 路径。
`_add_review_subgraph` 可用一个开关旁路，直接走原 `add_node` 分支。

---

## 8. 本任务的红绿要点

并发与图结构类改动**很容易假绿** —— 测试可能根本没触发并行路径。
按 `DEV-PROTOCOL.md` 第 1 节执行时，特别注意：

| 验收项 | 红的正确形态（实现前必须先看到） | 假绿风险 |
|---|---|---|
| 启动 N 个 agent | 断言 `len(agents) == 2` 失败，实际为 1 | 测试只断言「不报错」，不数数量 |
| 分支隔离 | 断言 B 的 prompt **不含** A 的产出，失败时能看到污染内容 | 只断言 prompt 非空 |
| reducer 正确性 | 断言 `len(review_findings) == 分支数`，失败时看到条目被覆盖只剩 1 条 | 单分支测试无法暴露覆盖问题 |
| 失败隔离 | 让一个分支抛异常，断言其余分支仍产出结果 | 从不测试异常路径 |
| 向后兼容 | 单角色配置下既有测试全绿（**单调性**，协议第 3 节第 3 条） | 只跑新增测试，不跑既有套件 |

**必须做的一步**：并发相关断言在实现前须能**真实地红**。
若某条测试在改动前就通过，说明它没有测到新行为，等于没测。

> 摩擦点记录：若发现 LangGraph 的并行路径难以在单测里稳定复现，
> 记录下来 —— 这直接影响 A11 变异探针的可行性。

---

## 11. 实施记录

实施于 2026-08-24。落盘：`sw_lib/workflow/review_graph.py`（新增）、
`sw_lib/workflow/graph.py`（04 展开为子图）、`sw_lib/workflow/state.py`
（两个并行结果容器）、`sw_lib/workflow/base.py`（`role_id` 与按角色落盘）、
`sw_lib/core/config.py`（`max_parallel`）、`config/config.yaml`。
测试 25 条分五个文件。

### 11.1 实测的 langgraph 0.6.11 行为

实现前后用探针实测，以下均为观测结果而非推断：

| # | 行为 | 观测 |
|---|---|---|
| 1 | `Send` 复用同一节点承载 N 个分支 | 成立，无需按数量预建节点 |
| 2 | 分支间看不到兄弟的 reducer 字段 | 成立，隔离天然满足（3.3） |
| 3 | **失败不隔离** | ⚠️ 一个分支抛异常**整图崩**，其余结果全丢。故 3.4 的失败隔离必须由节点内部 try/except 兜住 |
| 4 | 真并发 | 4 分支各 sleep 0.3s 总耗时 0.32s，4 个 ThreadPoolExecutor 线程，区间重叠 |
| 5 | 原生 `max_concurrency` | 有效：4 分支设 2 时运行时峰值确为 2。故 `max_parallel` 走它而非自己分批 |
| 6 | `dispatch` 返回空列表 | 不报错，正常走到 arbiter |

### 11.2 实现中发现的三个真问题

这三个都不是设计文档预见到的，且都会静默失效：

1. **并行审查者共写 `04-review.md` 会丢产出**。探针实测两个 runnable 并发调
   `_save_stage_output`，落盘后**只剩后写的那一份**，前者完全消失且无报错。
   根因是复用同一 nonce 并替换同一产出区。已改为多审查者时按
   `04-review.<role>.md` 分文件、nonce 按 `stage:role` 分作用域。
2. **单角色回落时 `role_id` 非空**（值为 `reviewer`），若据此改名会让门禁校验
   与事实包读不到产出。e2e 现场表现为 `stage file has no AI Output`，
   **而单元测试全绿** —— 典型的「单元绿 != 机制接通」。已改为只在
   `len(subjective) >= 2` 时分文件。
3. **新增属性会被 `flush_output` 的 `except` 吞掉**。既有测试用 `__new__` 手工
   构造实例，`self.role_id` 直接取会抛 `AttributeError`，被吞成「没有产出」。
   已改用 `getattr`。静默丢产出正是本任务要防的失效模式，却差点由本任务引入。

### 11.3 验收结果

| # | 判据 | 状态 | 证据 |
|---|---|---|---|
| 1 | 两个审查者实际启动 2 个、模型各自不同 | ✅ | `test_two_reviewers_actually_invoke_two_agents`（数真实 invoke 次数）+ `test_each_branch_carries_its_own_model` |
| 2 | 改三个不动 Python | ✅ | `test_three_reviewers_without_python_change` |
| 3 | 零主观审查者仅客观轨、不报错 | ✅ | `test_objective_only_when_no_subjective_and_objective_on` |
| 4 | 分支 payload 不含兄弟产出 | ✅ | `test_branch_payload_excludes_sibling_findings`（含非空守卫，防空转通过） |
| 5 | 一分支异常其余仍完成、记 `status: error` | ✅ | `test_one_failing_branch_does_not_kill_others`；变异探针证实去掉 try/except 整图崩，故断言有判别力 |
| 6 | `review_findings` 条目数等于分支数 | ✅ | `test_findings_count_equals_branch_count`（4 分支） |
| 7 | 既有测试行为不变 | ✅ | 单元 1246 passed / 1 skipped（基线 1213 + 新增 25），e2e 32/32 |
| — | `max_parallel` 在真实执行路径限流 | ✅ | `test_runtime_peak_concurrency_respects_max_parallel` 数运行时峰值而非批次长度 |
| — | 多审查者产出不互相覆盖 | ✅ | `test_parallel_role_writes_all_survive`；真实链路验证两份产出均落盘 |

### 11.4 未兑现与需拍板

| # | 事项 | 状态 |
|---|---|---|
| 1 | **`config.yaml` 的 `subjective` 仍为空** | ❓ 能力已通但未启用 |
| 2 | 客观轨节点是占位（`status: not_implemented`） | ❌ 属 A6 |
| 3 | 仲裁器是空节点 | ❌ 属 A9 |
| 4 | R2：TUI 单 agent 假设 | 部分缓解 |
| 5 | R3：`MockAgent` 未按 role 区分产出 | ❓ |

**第 1 项是本任务最重要的拍板点。** A5 已让「填两个角色就真跑两个」成立
（真实链路实测：两个审查者各自落盘、产出零丢失），但**仲裁器属 A9 尚未实现**：
实测填上两个审查者后 `StageOutput.route` 为 `None`、`gate_passed` 为 `False`，
TUI 会卡在 04 阶段推不动。

所以现在填上去的后果不是「声称两个只跑一个」，而是**「两个都跑了但没人下结论」**。
故仍留空，并用 `test_review_arbiter_contract.py` 的
`test_config_does_not_enable_multi_reviewers_before_arbiter` 守住这个前置条件 ——
A9 落地后取消 YAML 注释、删掉该守护测试即可，无需改 Python。

R2 的缓解程度需说清：`review_graph.active_roles()` 提供「当前在跑的全部角色」，
实测并行 3 分支时峰值确为 3，而同一时刻 `adapter.active_stage` 只反映其中一个。
但**注册表尚未接入 TUI / Web 的渲染代码** —— 显示层要用它才有意义，
故 R2 只是「可查」，不是「已显示」。

> 摩擦点（供 A11 参考）：并行路径在单测里**可以**稳定复现 ——
> 用 `threading.Lock` 数运行时峰值并发比断言耗时可靠得多，后者在 CI 上会抖。
> 变异探针（去掉 try/except 看是否整图崩）也工作良好，A11 可沿用这个手法。
