# A14：仲裁器缺口的配置锁 —— 让「填了就卡住」不可表达

> 依赖：**A4**（`validate_config` / `ConfigError` 机制）、**A5**（fan-out 子图与
>   `needs_arbiter`）
> 被依赖：**A9**（本文档的守护栏在 A9 落地时**必须一并拆除**，见第 7 节）
> 范围：`sw_lib/core/config.py`（新增一条 `validate_config` 校验项）、
>   `sw_lib/core/bootstrap.py`（接上 `assert_config_valid` 的生产调用点）、
>   `scripts/orphan_criteria.py`（登记新判据）
> 不含：仲裁逻辑本身（**A9**）、优先级表与 N1/N2/N3（**A9**）
> 状态：**本文档 = 待实施**

---

## 1. 这不是「补一个功能」，是「焊死一个坑」

`review_graph.py:207` 写着 `ARBITER_IMPLEMENTED = False`。A5 只建了
fan-out / fan-in 的骨架，归约逻辑归 A9，而 A9 尚未实施。

当前 `config.yaml` 的 `harness.review.subjective` 为空列表，于是
`needs_arbiter()` 返回 `False`，这条路**没有人踩到**。

问题恰恰在这里：**它现在不坏，纯属配置侥幸。** 而配置是给人改的。
一旦有人往 `subjective` 里填两个角色：

```
prepare → subjective × 2（并行跑完，各自产出 findings）
        → arbiter（`return {}` —— 没人归约）
        → END
  ⇒ StageOutput.route = None，gate_passed = False
  ⇒ TUI 靠 route 推进 04 阶段 → 推不动
```

A5 的 11.1 记着这是**实测现象，不是推断**。

### 1.1 为什么不是「那就赶紧实现 A9」

A9 的范围是六级优先级仲裁表 + N1/N2/N3 特例 + Route 权威落点迁移 +
`reroute_count` 持久化，它自己的文档写着依赖 A6/A7/A8 三轨结论。
那是一个独立的可交付单元，不是随手能补的东西。

**在 A9 落地之前，正确的工程动作是让缺口不可达，而不是让它可达但危险。**
这与形状库 S10 的处置原则同构（2.9.19 的示范）：

> 修 S10 时优先问：「能不能让这个形状不可表达？」

---

## 2. 现状：已实测的事实

### 2.1 守护栏已存在，但只活在测试里

`tests/unit/workflow/test_review_arbiter_contract.py:71` 有一条
`test_config_does_not_enable_multi_reviewers_before_arbiter`，它读实际配置，
若 `len(subjective) >= 2` 而仲裁器未实现就报红。

这条测试是对的，但它的**触发时机**不对：

| | 测试守护栏 | 配置层守护栏 |
|---|---|---|
| 何时报错 | 有人跑 `pytest` 时 | 有人改完配置启动 harness 时 |
| 改配置的人是否会看到 | 只有他记得跑全量套件 | 必然看到 |
| 全量套件耗时 | 实测 505 秒 | 启动即时 |

改 `config.yaml` 的人**没有义务**跑一遍 8 分钟的全量套件。
守护栏放在这里，等于把「会不会发现」赌在人的习惯上。

### 2.2 ⚠️ 本轮新发现：`assert_config_valid` 自己就是 S7

```bash
$ rg -n "assert_config_valid" sw_lib/ hooks/ bin/ tests/
sw_lib/core/config.py:598:def assert_config_valid() -> None:
tests/unit/core/test_config_validation.py:62:        C.assert_config_valid()
tests/unit/core/test_config_validation.py:72:    C.assert_config_valid()
tests/unit/core/test_config_validation.py:169:        C.assert_config_valid()
```

**定义 1 处，测试 3 处，生产调用点 0 处。**

这是形状 S7（判据存在、无人调用）的第 4 个实例。A4 的 3.4 设计了
「配置写错就响亮失败」，`validate_config` 里躺着 6 条 error 级校验
（`stage_role_unknown`、`subjective_role_unknown`、`review_has_no_reviewer`…），
**它们至今从未在生产路径上执行过**。

这条发现改变了本任务的设计：如果只往 `validate_config` 里加一条校验，
它会加入那 6 条一起沉默。**必须同时把 `assert_config_valid` 接上生产调用点，
否则本任务交付的是第 5 个 S7 实例。**

### 2.3 `needs_arbiter` 与 `_is_sole_reviewer` 的判据同源（无需改动）

```python
# review_graph.py:211      graph.py:158
len(subjective) >= 2       len(subjective) < 2
```

两处互补且都读 `resolve_review_config()`，是同一个真相源。这一点**不是**
S8（一个语义两个数字），无需合并。区分特征：它们没有各自硬编码阈值，
阈值 2 只出现在这两个互补判断里，且语义相同（"多到需要归约"）。

---

## 3. 设计

### 3.1 判据：多审查者 + 仲裁器未实现 = 配置错误

在 `validate_config()` 里新增一条 error 级校验：

```
code:     review_needs_unimplemented_arbiter
severity: error
条件:     len(review.subjective) >= 2 且 review_graph.ARBITER_IMPLEMENTED 为 False
```

失败信息必须给出**下一步可执行的动作**（criterion-design 的失败信息要求）：

```
harness.review.subjective 配了 N 个主观审查者，但仲裁器（A9）尚未实现。
实测后果：04 阶段的 route 为空，TUI 推不动阶段，任务卡在审查阶段。
下一步二选一：
  (a) 减到 1 个审查者 —— 单审查者路径自带 route，行为与改造前一致；
  (b) 实施 A9（docs/design/A9-route-arbiter.md）后把
      review_graph.ARBITER_IMPLEMENTED 置 True，本校验自动失效。
```

### 3.2 接线：`assert_config_valid` 挂到 `bootstrap()`

`bootstrap()` 是全部入口的共同瓶颈：`cli/main.py:_ensure_bootstrapped`、
`workflow/engine.py:_get_executor` 都经过它，且它自己是幂等的。

挂载位置在 `WorkflowRuntime.initialize(stages)` **之前** —— 配置错误应当
在建图之前拦住，而不是建完一张错的图再报错。

#### 3.2.1 一个必须正面处理的风险

`cli/main.py:29` 的 `_ensure_bootstrapped` 是这样写的：

```python
try:
    bootstrap()
except Exception:
    pass  # bootstrap failure is non-fatal
```

**`ConfigError` 会被这个 `except: pass` 吞掉。** 若不处理，本任务交付的
就是一条永远不会被人看见的校验 —— S7 换了个形态复发。

定案：`_ensure_bootstrapped` 对 `ConfigError` **单独放行到顶层**，
其余异常保持原有的非致命语义。理由：`ConfigError` 的语义是"配置写错了，
人必须来改"，它与"某个可选模块没装好"不是一类事。

### 3.3 三态设计

| 情形 | 判定 | 理由 |
|---|---|---|
| `subjective` 为 0 或 1 个 | pass | 单审查者路径自带 route（A5 验收 7） |
| `>= 2` 且 `ARBITER_IMPLEMENTED` 为 True | pass | A9 已落地，缺口不存在 |
| `>= 2` 且 `ARBITER_IMPLEMENTED` 为 False | **error** | 实测会卡住 |
| `review_graph` 导入失败 | **不静默** | 见下 |

最后一行的处置：导入失败时记 error 而非跳过。判据读不到自己要读的东西时
判"通过"，正是 A6 的 3.3 明令禁止的形态（`unavailable` 不算通过）。
但这里比 `unavailable` 更强——`review_graph` 是本仓库自己的模块，
导入失败意味着代码坏了，不是环境缺件。

---

## 4. Entry A：形状检查（CLOSED-LOOP 要求）

```
匹配形状: S7（判据存在、无人调用）—— 本任务同时是它的第 4 个实例的修复
         S2（阶段错位）—— 已排除，见下
判例参考: 2.9.9（is_idle 零调用点）、e791960（ambiguity 零消费方）
```

### 五问逐条

**Q1. 产出落在哪里？**
本判据不量 agent 产出，量的是 `config.yaml`。落点唯一（`_manager.config.review`），
不存在"多个落点只量一个"的风险。`resolve_review_config()` 是唯一读取路径，
已确认 `needs_arbiter` / `_is_sole_reviewer` 两个消费方都走它。

**Q2. 哪个阶段/角色能满足它？**
**不是 agent，是人。** 这条判据的满足者是改 `config.yaml` 的开发者，
动作是"减到 1 个审查者"或"实施 A9"。两者都在 agent 的运行阶段之外，
因此**不存在 S2（阶段错位）风险** —— 那个形状的成因是"判据要求的动作在
任何 agent 阶段都无权限做"，而本判据压根不面向 agent。

**Q3. 有上界吗？**
本判据是布尔判定，不含计数阈值，无 S3 风险。它自身**就是** `subjective`
数量的上界（在 A9 落地前上界为 1）。

**Q4. 判据在哪里跑，agent 在哪里跑？**
判据跑在 `bootstrap()`，即 harness 进程启动时，cwd 为 harness 根目录，
解释器为启动 `sw` 的那个 python3。它不读 `target_dir`、不跑 pytest、
不碰 agent 的执行环境，**无 S4 风险**。

**Q5. `unavailable` 与 `pass` 区分了吗？**
区分了，见 3.3 表格第 4 行：`review_graph` 导入失败记 error，不记 pass。

### wiring check 计划

```bash
rg -n "assert_config_valid|review_needs_unimplemented_arbiter" sw_lib/ | rg -v "test_|docs/"
```
交付前必须看到 `bootstrap.py` 出现在结果里。并把
`assert_config_valid` 登记进 `scripts/orphan_criteria.py`，让 S7 复发可被脚本抓住。

---

## 5. 红绿判据

严格按 DEV-PROTOCOL 第 1 节六步。**红必须是断言失败，不是 ImportError。**

| # | 测试 | 红的形态 |
|---|---|---|
| 1 | 配 2 个审查者 + 仲裁器未实现 → `validate_config` 含 `review_needs_unimplemented_arbiter` | 断言 code 在列表里失败（当前无此 code） |
| 2 | 配 2 个审查者 → `assert_config_valid` 抛 `ConfigError` | 断言抛异常失败（当前不抛） |
| 3 | 配 1 个审查者 → 不抛（既有行为不变） | 应当直接绿，用于证明判据非恒真 |
| 4 | `ARBITER_IMPLEMENTED` 为 True 时配 2 个 → 不抛 | 证明判据不是"永远拦住多审查者" |
| 5 | `bootstrap()` 在配置错误时抛 `ConfigError` | 断言抛异常失败（当前 bootstrap 不校验） |
| 6 | `_ensure_bootstrapped` 不吞 `ConfigError` | 断言失败（当前 `except: pass` 会吞） |

第 3、4 条是**反恒真判据**（criterion-design 的第 3 步）：只有它们绿着，
才能证明第 1、2 条不是"无条件报错"。

---

## 6. 验收标准

```
[ ] U14-1 配 2 个审查者时 validate_config 报 error 级 review_needs_unimplemented_arbiter
[ ] U14-2 配 1 个审查者时不报（既有行为不变）
[ ] U14-3 ARBITER_IMPLEMENTED=True 时配 2 个不报（判据非恒真）
[ ] U14-4 bootstrap() 遇配置错误抛 ConfigError，且在建图之前抛
[ ] U14-5 sw 的 _ensure_bootstrapped 不吞 ConfigError
[ ] U14-6 失败信息含两条可执行的下一步
[ ] U14-7 assert_config_valid 有生产调用点（rg 验证 + orphan_criteria 登记）
[ ] U14-8 既有测试单调性：1628 passed 不减少
[ ] U14-9 真实任务目录上跑一遍 sw state get，确认正常启动未被误拦
```

---

## 7. A9 落地时必须拆除的东西

本文档交付的是**临时守护栏**，A9 落地时须一并处理，否则它会变成
"拦住正确配置"的新缺陷：

1. `review_graph.ARBITER_IMPLEMENTED` 置 `True`；
2. 本校验自动失效（条件不再成立），**无需删代码**——这是刻意的设计，
   避免"A9 落地后忘了拆守护栏"；
3. 删掉 `test_config_does_not_enable_multi_reviewers_before_arbiter`
   （该测试自己的 docstring 已写明这一点）；
4. 填 `config.yaml` 的 `subjective`。

> 第 2 条是本设计的关键：守护栏的失效条件写在判据里，而不是写在
> 某人的记忆里。

---

## 8. 回滚

单条校验 + 一处接线，回滚粒度小：

- 撤 `validate_config` 里的新增分支 → 校验消失，行为回到今天；
- 撤 `bootstrap()` 里的 `assert_config_valid()` 调用 → 全部 6 条既有校验
  一并回到沉默状态（**注意这是回到 S7，不是回到安全**）。

---

## 9. 本任务特有的假绿风险

1. **判据恒真（S5）**：如果实现写成"`subjective >= 2` 就报错"而漏读
   `ARBITER_IMPLEMENTED`，A9 落地后会拦住正确配置。U14-3 专门钉这一点。
2. **S7 换形态复发**：校验加了、`assert_config_valid` 也接上了，但
   `_ensure_bootstrapped` 的 `except: pass` 把它吞掉。U14-5 专门钉这一点。
   **这是本任务最可能的失败方式**——两处都改对了，第三处静默吞掉。
3. **单元绿 ≠ 机制接通**（本仓库已发生六次）：必须真的启动一次 `sw`
   并观察配置错误时的行为，而不只是在测试里调 `bootstrap()`。
4. **配置快照污染**：`test_config_validation.py` 用 `_restore_global_config`
   fixture 恢复全局配置。新测试若忘了这个 fixture，会让**后续**测试读到
   被改过的配置——那是典型的"红在别处"。
