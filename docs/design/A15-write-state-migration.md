# A15：`.state` 受控入口的完成 —— 让丢失更新不可表达

> 依赖：**A0**（`update_state` / `state_lock` / HMAC 签名机制已建成）
> 被依赖：无（本任务是 A0 的收尾，不引入新机制）
> 范围：`sw_lib/workflow/runtime.py`（4 处）、`sw_lib/core/service.py`（5 处）、
>   `sw_lib/core/health.py`（3 处，经 `_write_state_safe`）、
>   `sw_lib/cli/commands.py`（1 处，**实施期 sweep 发现，本文档原先漏了**）、
>   `sw_lib/core/state.py`（给 `write_state` 加调用来源约束）
> 不含：`write_state` 本体的原子写实现（A0 已交付）、`.state` 格式迁移
> 状态：**已实施**（验收结果见第 6 节，含三处与设计不符的偏离）
> 复盘：`A0-state-integrity.md` 的 2.9.21

---

## 1. A0 建好了门，但一半的人还在走窗户

A0 的 2.3 实测复现过丢失更新：两个调用方各自 `read_state` 后写回，
后写者静默覆盖前写者。它的解法是 `update_state(name, mutator)` ——
在锁保护下执行 读 → mutate → 原子写。`update_state` 的 docstring 写着：

> 这是**写入 `.state` 的受控入口**。A2 的 red_witness、A3 的事实包、
> A6 的客观轨结论、A9 的 Route 决策都必须走这里。

实测当前的分布：

| 位置 | 裸 `write_state` | 受控 `update_state` |
|---|---|---|
| `workflow/stage_state.py` | 0 | 9 |
| `workflow/fact_pack.py` | 0 | 2 |
| `workflow/red_witness.py` | 0（走 `_mutate_witness`） | 间接 |
| **`workflow/runtime.py`** | **5** | 1（`set_stage_status`） |
| **`core/service.py`** | **5** | 0 |
| **`core/health.py`** | **3**（经 `_write_state_safe`） | 0 |

**判据本身已经建成，一半的调用点没接上。** 这是形状 S10（修了一半）的
第 6 个实例，而 S10 的判例 1 恰好就是这件事的前身：

> D0-1：十余处 `read_state→改→write_state` 调用点，只修了瓶颈处的单次写入。

### 1.1 危险在哪里：不是"可能出错"，是"注释在替代机制"

`runtime.advance()` 的 158-183 行现在长这样（节选真实注释）：

```python
# reset_gate / reset_route 刚刚写过盘，`st` 是函数入口读的旧快照 ——
# 直接拿它写回去会把重置结果整体覆盖掉。重读一次再改字段。
st = read_state(name) or st
```

这段注释是对的，也确实解决了当时那个 bug。但它把正确性**寄存在了
下一个读代码的人身上**：谁在 `advance` 里加一行状态修改，都必须自己
想到"我手上这份 `st` 可能过期了"。

`set_stage_status` 的 docstring 已经把这个教训写得很清楚：

> 实测（15 次交错）这会**抹掉用户刚签的 Gate** —— 它们读到签署前的旧快照，
> 改完整体写回，签署凭空消失。**判据丢失比状态显示错误严重得多。**

`set_stage_status` 被抽出来受控化了，`advance` 自己没有。而 `advance`
写的东西（`stage`、`stage_idx`、`stage_status`）恰恰与 `reset_gate` /
`reset_route` / `inject_reroute_context` 交错发生。

### 1.2 HealthMonitor：跨线程的真实并发

`core/health.py` 有 3 处经 `_write_state_safe` 写盘，而
`_write_state_safe` 的实现是：

```python
def _write_state_safe(self, name: str, data: dict):
    """安全写入状态（供 HealthMonitor 等外部调用），直接写 .state 文件。"""
    from .state import write_state as _ws
    _ws(name, data)
```

**函数名叫 `_write_state_safe`，实现是裸 `write_state`。** 名字承诺了
安全，实现没有兑现——这比直接调 `write_state` 更坏，因为它让调用方
以为问题已经处理过了。

HealthMonitor 跑在独立线程里（A0 的 2.3 点名了这个场景），与 TUI 的
Gate 签署、`advance` 的阶段推进真实并发。

---

## 2. 现状：逐处分类（same-shape-sweep 的 Step 3）

**形状陈述（一句话，不含文件名）**：
> 状态写入走"读取快照 → 修改 → 整体写回"，两个并发写者中后写者静默覆盖前写者。

### 2.1 同形状，本轮修

| # | 位置 | 写什么 | 为什么危险 |
|---|---|---|---|
| 1 | `runtime.advance` 终态分支 | `stage_status=Finished` | 与 Gate 签署并发 |
| 2 | `runtime.advance` 03a 留级分支 | `stage_status=pending` | 紧跟 `reset_gate` 之后，已知快照陷阱 |
| 3 | `runtime.advance` 无路可走分支 | `stage_status=Finished` | 同 1 |
| 4 | `runtime.advance` 主推进分支 | `stage/stage_idx/stage_status` | 紧跟 `reset_gate`+`reset_route`，靠注释兜住 |
| 5 | `service.remove_task` | `removed_at` | 与运行中的阶段并发 |
| 6 | `service.restore_task` | 删 `removed_at` | 同上 |
| 7 | `service.add_answer` | `stage_status=running` | **与 agent 写状态高频并发** |
| 8 | `service.deploy_task` | `deploy_status=deploying` | 与 HealthMonitor 并发 |
| 9 | `service.complete_deploy` | `deploy_status`/`health_*` | 与 HealthMonitor 并发 |
| 10 | `service._write_state_safe`（health.py 3 处） | `health_status`/`deploy_status` | 跨线程，名字骗人 |

### 2.2 同形状，**刻意排除**（附区分特征）

| 位置 | 区分特征 |
|---|---|
| `service.create_task:141` | **任务尚不存在**，此时不存在第二个写者。`update_state` 的 mutator 拿到的是空状态，语义上是"创建"而非"修改"。强行受控化会让"任务已存在"的冲突检测失效。 |
| `state.write_state` 本体 | 它**是**原子写的实现，`update_state` 内部要调它。受控化自己会造成无限递归。 |
| `red_witness._mutate_witness` | 已经走 `update_state`（实测确认），非本形状实例。 |
| `stage_state` / `fact_pack` 全部 | 已受控，非实例。 |

### 2.3 机械搜索的覆盖范围（证明这是 sweep 不是运气）

```bash
rg -n "write_state" sw_lib/ hooks/ bin/          # 全部写入点
rg -n "read_state" sw_lib/ | rg -v "test_"       # 读-改-写的上半截
rg -n "_write_state_safe" sw_lib/                # 名字承诺安全的包装层
```

三条命令覆盖 `sw_lib/`、`hooks/`、`bin/`。`workspace/` 与 `repo/` 是
数据目录不含代码，`tests/` 按 A0 第 9 节的裁定**允许**绕过受控入口构造
攻击样本（"绕过用于构造攻击样本是必要的，用于制造通过是禁止的"）。

---

## 3. 设计

### 3.1 迁移模式：`read → 改 → write` 变成 `mutator`

统一形态（以 `add_answer` 为例）：

```python
# 迁移前
st = read_state(name)
if st.get("stage_status") == "pending":
    st["stage_status"] = "running"
    st["updated_at"] = now()
    write_state(name, st)

# 迁移后
def mutate(state):
    if not state or state.get("stage_status") != "pending":
        return NO_CHANGE          # 幂等路径，不刷 updated_at、不重算签名
    state["stage_status"] = "running"
    state["updated_at"] = now()
    return state
update_state(name, mutate)
```

`NO_CHANGE` 是 A0 已有的哨兵值，语义是"无需写盘"。用它而不是
`return state`，可以避免"每次调用都刷新 `updated_at`"造成的审计噪音。

### 3.2 `advance` 的特殊处置：整个函数进一个 mutator

`advance` 的 4 处写入**不能各自受控化** —— 那样只是把 4 个小的
race window 变成 4 个更小的，路由决策本身仍然横跨多次读写。

但它也不能整体塞进一个 mutator：中间要调 `reset_gate`、`reset_route`、
`inject_reroute_context`，而这些自己就要 `update_state`。

#### 3.2.1 ⚠️ 实测：`state_lock` **不可重入**，`update_state` **不可嵌套**

两条都在当前代码上实测（`flock` 每次 `os.open` 拿新 fd，`LOCK_EX` 对
同一进程的第二个 fd 同样阻塞）：

```text
外层锁已获得
内层锁超时 → 不可重入: 获取状态锁超时（2.0s）: .../.state.lock

update_state 嵌套调用 → 超时 → 确认不可嵌套
```

**这否决了「外层加一把锁、内部保持现状」的方案。** 若照那样实施，
`advance` 会在调用 `reset_gate` 时死锁 —— 而死锁在测试里表现为「卡住」
而不是「失败」，极易被当成环境问题（本任务第 8 节风险 2 即此）。

**定案（唯一可行路径）**：

1. `reset_gate` / `reset_route` / `inject_reroute_context` 保留在**锁外**
   调用，它们各自是原子的；
2. `advance` 的**最终状态落盘**收拢成一次 `update_state`；
3. mutator 内部**重新读取**当前状态，不使用函数入口的 `st` 快照 ——
   这正是现在那句「重读一次再改字段」注释想做的事，区别在于
   现在由机制保证，而不是靠人记得。

即：不追求「advance 整体原子」，只保证「advance 的每次落盘不覆盖他人的写入」。
前者需要重构路由逻辑，超出本任务范围；后者足以消除已实测的判据丢失。

> 这条边界必须写明：**本任务不让 `advance` 变成原子操作。**
> 它消除的是"用过期快照整体覆盖"，不是"推进过程中的所有交错"。
> 把目标写大而实现做不到，本身就是一次自欺（A0 的 1.1 同一条纪律）。

### 3.3 `_write_state_safe`：名字要么兑现，要么改掉

两个选项：

- **(a)** 把它改成走 `update_state`，名字兑现承诺；
- **(b)** 改名为 `_write_state_unsafe` 并在 docstring 写明风险。

定案 **(a)**。理由：HealthMonitor 的 3 处调用都是"读取当前状态 → 改 1-2 个
health 字段 → 写回"，完全适配 mutator 形态。而 (b) 只是把问题记录下来，
不解决它——A0 的目标是完整性，不是完整性的说明书。

### 3.4 防止 S10 第 7 次：让裸写在新代码里不可表达

迁移完 10 处之后，`write_state` 在 `sw_lib/` 里的合法调用点只剩
`update_state` 内部与 `create_task`。为了让下一个人写不出第 11 处：

给 `write_state` 加一个**关键字参数** `_internal: bool = False`，
非内部调用时发 `DeprecationWarning`，指向 `update_state`。

不用"改名 + 私有化"（`_write_state`）的理由：那会一次性打断所有既有
调用点包括测试，回滚粒度变粗；而警告可以让迁移分批、可观测。

> 这条是本任务里唯一"新增机制"的部分，其余都是接线。
> 它对应 CLOSED-LOOP 的原则：**能不能让这个形状不可表达？**

---

## 4. Entry A：形状检查（CLOSED-LOOP 要求）

```
匹配形状: S10（修了一半）—— 第 6 个实例
         S7（判据存在无人调用）—— update_state 是判据，一半调用点没接
判例参考: 2.8（D0-1 十余处只修瓶颈）、2.9.6（set_stage_status 受控化）
```

### 五问逐条

**Q1. 产出落在哪里？**
`.state` 是唯一落点，但**读取路径有两条**：`read_state`（返回快照）与
`update_state`（锁内读）。本任务要消除的正是"从第一条路读、往第二条路写"
的混用。已枚举全部 13 处写入点（2.1 十处 + 2.2 三处排除）。

**Q2. 哪个阶段/角色能满足它？**
满足者是 harness 自己的代码，不是 agent。本任务不新增对 agent 的要求，
**无 S2 风险**。

**Q3. 有上界吗？**
不含计数阈值。但有一个隐藏的下界/上界问题：`update_state` 的
`timeout=10.0`。10 处调用点全部走锁之后，锁竞争会变多。
**必须实测锁等待是否会超时** —— 若超时，`advance` 会抛异常而不是静默丢失，
那是"能被发现的失败"，但仍是失败。验收项 U15-7 钉这一点。

**Q4. 判据在哪里跑，agent 在哪里跑？**
本任务不涉及 `target_dir` / pytest / 子进程，全部在 harness 进程内，
**无 S4 风险**。但涉及**多线程**（HealthMonitor / TUI / Web），
这是 S4 的近亲："两侧跑在不同的**线程**里"。`state_lock` 是文件锁
（`.state.lock` 实测存在），跨线程与跨进程都有效。

**Q5. `unavailable` 与 `pass` 区分了吗？**
相关：`update_state` 对损坏的 `.state` **抛异常而非静默跳过**。
迁移后 `remove_task` 等操作在 `.state` 损坏时会抛错，而此前裸
`write_state` 会把损坏覆盖掉。**这是行为变化，且是变好** ——
但必须在验收里显式确认，因为它会让某些既有测试变红。

### wiring check 计划

```bash
rg -n "write_state" sw_lib/ | rg -v "test_|update_state|def write_state"
```
交付后此命令应只剩 `create_task` 一处 + `state.py` 内部。

---

## 5. 红绿判据

**红必须是断言失败，不是 ImportError。** 判据的形状是"并发交错下判据不丢"。

| # | 测试 | 红的形态 |
|---|---|---|
| 1 | `advance` 与 `sign_gate` 并发 N 轮，Gate 签名不得消失 | 断言 `signed_at` 存在失败 |
| 2 | `add_answer` 与 `set_stage_status` 并发，两者的字段都不得丢 | 断言字段值失败 |
| 3 | HealthMonitor 写 `health_status` 与 `advance` 并发，`stage_idx` 不得回退 | 断言 `stage_idx` 失败 |
| 4 | `write_state` 非内部调用发 `DeprecationWarning` | 断言 warning 失败 |
| 5 | 迁移后 `rg` 结果只剩 2 处合法裸写 | 断言计数失败 |
| 6 | 幂等路径：`add_answer` 在非 pending 状态下不刷 `updated_at` | 断言时间戳未变失败 |

第 1 条的构造方式：**必须走生产路径**（`WorkflowRuntime.advance` +
`stage_state.sign_gate`），不手工拼 `.state`。criterion-design 的
第 1 步明令这一点：复现的是"agent/用户实际做的事"，不是"我想象的形状"。

### 5.1 反恒真判据

第 1-3 条如果实现写错方向（比如干脆不写状态），它们也会"绿"。
所以必须配一条**阳性对照**：

| # | 测试 | 作用 |
|---|---|---|
| 7 | 单线程下 `advance` 仍能正常推进 stage_idx | 证明并发保护没有把功能锁死 |

这是 A2 教训的直接应用：只放松见证侧会把"拦住好代码"变成"放过坏代码"。

---

## 6. 验收标准

**交付结果（三态，✅ 有执行证据 / ❌ 已尝试失败 / ❓ 未验证）**

| 项 | 态 | 证据 |
|---|---|---|
| U15-1 advance 的 4 处写入全部受控 | ✅ | 4 处全走 `update_state`；两处终态收拢为 `WorkflowRuntime.finish` |
| U15-2 service 的 5 处写入全部受控 | ✅ | `remove_task`/`restore_task`/`add_answer`/`deploy_task`/`complete_deploy` |
| U15-3 `_write_state_safe` 名字兑现 | ✅ | 走 `update_state`；且**不是浅合并**，见下方修正 |
| U15-4 非内部调用发 `DeprecationWarning` | ✅ | 一正一反两条判据（内部调用不得告警） |
| U15-5 裸写只剩已裁定的合法处 | ✅ | 机械搜索固化为测试；实际发现 **11** 处而非 10 处 |
| U15-6 幂等路径不刷 `updated_at` | ✅ | `NO_CHANGE` 用于 `add_answer` / `restore_task` |
| U15-7 锁竞争 `timeout=10.0` 是否足够 | ❓ | 957 条单元测试 + 真实任务未触发超时；**高并发多轨场景未压测** |
| U15-8 损坏 `.state` 抛错而非覆盖 | ✅ | 截断样本上 `add_answer` / `complete_deploy` 均抛 `ValueError`，原文逐字节保住 |
| U15-9 既有测试单调性 | ✅ | `tests/unit/core/` 333 passed、`tests/unit/workflow/` 624 passed，无 failed |
| U15-10 真实任务走一遍 advance | ✅ | 任务 `a15probe` 走完 01→05；`advance` 与 `sign_gate` 并发不丢签名 |

### 6.1 实施期的三处偏离（设计与现实不符的地方）

**(1) 是 11 处，不是 10 处。** 设计文档 2.1 亲手枚举了 10 处，
sweep 的机械搜索抓到第 11 处：`cli/commands.py` 的归档分支也在写终态。
**漏的那处正是写这份文档的人漏的** —— 这条记进 CLOSED-LOOP 的 S10。
处置：三个写终态的地方全部收拢到 `WorkflowRuntime.finish`。

**(2) `_write_state_safe` 不能用浅合并。** 原定案「锁内重读 + `dict.update`」
在 13 条单元测试上全绿，**在真实任务上仍然丢判据**：

```text
真实任务 a15probe（无 mock）
  签署后 05 gate: True
  _write_state_safe 写盘后: False
```

原因：调用方的快照里**本就带着一棵旧的 `stages` 子树**，`dict.update`
用旧子树整棵替换新的。单元测试没抓住，是因为那份快照读取于 `stages`
出现之前 —— **测试的前提比生产干净**。
改为「只回写调用方改动过的**标量**字段」，跳过 dict / list：
判据都住在嵌套结构里，而 health 字段都是标量。

**(3) 红测试自己空转过一次。** 首版只钩 `read_state` 注入并发写者；
迁移后被测函数不再调用它，注入静默失效，测试变成恒真。
修法：同时钩 `update_state`（注入在取锁**之前**，避开不可重入的锁），
并给每条用例加 `assert fired["done"]`。按 DEV-PROTOCOL 的 1.2，
这次改动发生在见红之后，因此已在**未改动的 HEAD 代码**上重跑确认仍红
（8 failed / 4 passed，与首版红的集合一致）。

---

## 7. 回滚

分两级：

- **撤 3.4 的警告**：删 `_internal` 参数，不影响已迁移的调用点；
- **撤某处迁移**：单点恢复 `read_state`+`write_state`，因为
  `update_state` 与裸写**在数据格式上完全兼容**（都经 `write_state` 落盘、
  都过 HMAC 签名）。这一点是本任务能分批迁移的前提。

---

## 8. 本任务特有的假绿风险

1. **并发测试天然不稳定**。20 轮交错可能恰好不触发 race。
   处置：迁移**之前**先让测试在旧代码上跑红（A0 的 2.3 用 15 次交错复现过），
   记录红的输出。**没见过红的并发测试不算判据。**
2. **锁的可重入性未经实测**。3.2 的两条路径取决于它。
   若误判会导致死锁 —— 而死锁在测试里表现为"卡住"，不是"失败"，
   容易被当成环境问题。必须先单独实测 `state_lock` 的可重入性。
3. **`update_state` 对损坏状态抛异常**会让某些既有测试变红（U15-8）。
   这些红是**对的**，不得靠改回裸写来消除。
4. **单元绿 ≠ 机制接通**（已发生六次）：必须在
   `workspace/tasks/<task>/` 的真实任务上走一遍 `advance`。
5. **`NO_CHANGE` 用错方向**：mutator 里 `return NO_CHANGE` 与
   `return state` 搞反，会导致"该写的没写"。这比丢失更新更隐蔽，
   因为它不需要并发就能发生。U15-6 钉这一点。
