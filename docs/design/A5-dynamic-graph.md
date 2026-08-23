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
