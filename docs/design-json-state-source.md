# 设计文档：JSON 状态源（Single Source of Truth）

> 状态：**待评审**。基于源码核查（2026-08-22）产出。
> 目标：彻底消除「Markdown 既是产出又是状态」导致的整类解析歧义 bug。

---

## 1. 问题陈述

### 1.1 现象

修一个、冒一个，同一类 bug 反复出现：

| # | 现象 | 根因位置 |
|---|------|---------|
| 1 | 02-05 阶段 Gate 无人可签，`/advance` 永久拒绝 | 签署入口只在 01 实现 |
| 2 | 校验把下游待办算作门禁项 | `check_stage_compliance` 语义混淆 |
| 3 | Route 已填仍被当路由，重复写入、Gate 签不上 | `_dispatch` 与渲染层判定不一致 |
| 4 | `/advance` 两次，待填项 2 → 4 递增 | `_save_stage_output` 用 `find` 取首个 Gate |

第 4 个是当前未修项。现场证据 `workspace/tasks/helloworld/02-planning.md`
含 **4 份** `## Gate`，前 3 份全为 `[ ]`，只有最后一份被签署。

### 1.2 根因：分隔符与内容共用同一词汇表

阶段文件同时承担四种职责：

1. agent 的自由文本产出
2. 用户的签署记录（Gate）
3. 路由决策（Route）
4. 机器可读的阶段状态

定位区域的唯一手段是在字符串里找 `## Gate` / `**Route**:`。而 agent 的产出是
**不受约束的自由文本** —— 它完全可以写出这些标记。`helloworld` 的 agent 就照
模板复述了一遍 Gate 区，于是：

```
_save_stage_output(base.py:453):  after.find("\n## Gate")   ← 命中 agent 正文里的
                                                              那个 Gate
→ 模板真正的 Gate 被当作 agent 产出的一部分，保留进 preserved
→ 每次 flush 追加一份
```

**这个 bug 无法通过把 `find` 改成 `rfind` 修好** —— agent 下次把 `## Gate`
写在结论末尾，`rfind` 就又错了。歧义是消不掉的，只要边界可被内容伪造。

### 1.3 歧义的规模

> 本节行号指向**重构前**的代码，作为问题规模的记录保留；这些位置现已删除或改写。

Gate 区定位散落 **11 处，4 种互不一致的语义**：

| 写法 | 位置 | 语义 |
|------|------|------|
| `after.find(gate_marker)` | `base.py:453` | 第一个 |
| `content.rfind("\n## Gate")` | `tui.py:818,1070` | 最后一个 |
| `startswith("## Gate")` | `utils.py:26,220,388,396` | 逐行扫首个 |
| `"## Gate" in content` | `utils.py:329,447` | 仅判存在 |

写入用「第一个」、签署用「最后一个」、校验用「逐行首个」。三者对同一份文件的
理解不同 —— 这就是 bug 1/3/4 的共同来源。每次修复只是把某一对齐上，剩余组合
继续错位。

`**Route**` 字段同样散落 **11 处**解析/写入点，分布在 5 个文件：
`utils.py`（5 处：39/161/232/479/481）、`tui.py:1030`、
`mock_fixups.py`（3 处：19/38/39）、`test_cmd.py:42`、
`hooks/check_04-review.sh:16`。同一个字段有 4 种正则写法（`re.search`
两种、`re.sub`、`re.subn`）加 3 处字面 `replace`。

---

## 2. 设计目标

| 目标 | 判据 |
|------|------|
| G1 | 状态判定不依赖对 agent 自由文本的解析 |
| G2 | Gate/Route 只有**一个**读写入口，不存在第二种定位语义 |
| G3 | agent 无论写什么内容，都不能改变门禁判定结果 |
| G4 | 现存 6 个任务可无损迁移，用户不需要手工编辑文件 |
| G5 | Markdown 仍然人类可读（这是 harness-flow 的产品特性，不能丢） |

**非目标**：不改变阶段划分、不改变 agent 提示词、不引入数据库。

---

## 3. 方案：状态进 JSON，Markdown 降级为渲染产物

### 3.1 核心原则

> **机器读的东西，agent 不能写。agent 写的东西，机器不解析。**

Gate 签署、Route 决策、阶段完成度这些**判定依据**存入 `.state`（已是 JSON）。
Markdown 继续承载 agent 产出和人类阅读，但**不再是任何判定的输入**。

### 3.2 状态结构

在 `.state` 中新增 `stages` 字段。现有字段（`id`/`stage`/`stage_idx`/
`stage_status`/`target_dir`/`health_config`）全部保持不变，向后兼容。

```json
{
  "id": "helloworld",
  "stage": "02-planning",
  "stage_idx": 1,
  "stage_status": "running",
  "stages": {
    "02-planning": {
      "gate": {
        "items": [
          {"key": "tests_pass",        "label": "Tests pass",        "checked": true},
          {"key": "no_regression",     "label": "No regression risk", "checked": true}
        ],
        "signed_by": "user",
        "signed_at": "2026-08-22T15:40:12"
      },
      "output_nonce": "7f3a9c2e"
    },
    "04-review": {
      "gate": { "items": [...], "signed_by": null, "signed_at": null },
      "route": {"target": "05-archive", "decided_by": "user", "decided_at": "..."}
    },
    "01-brainstorming": {
      "decisions": {
        "交易日历": {"answer": "A. 简单交易日历表",
                     "decided_by": "user", "decided_at": "..."}
      }
    }
  }
}
```

要点：

- `gate.items` 的 `key`/`label` 来自**模板**（不是 agent 产出），在阶段启动时
  由 `templates/{stage}.md` 一次性播种。
- `checked` 只能由签署入口写。agent 没有任何路径能改它。
- `route.target` 同理，只由用户选路或 auto 判定写入。
- `signed_by` 区分 `user` / `auto`，便于审计自动推进的行为。
- `decisions` 记录选项组拍板：`{问题: {answer, decided_by, decided_at}}`，
  由 ask_user 的答复落盘，key 为问题文本（改主意覆盖，不刷计数）。
  **这一条是设计初版的错误被修正后加上的**，详见 6.3。

### 3.3 唯一读写入口

新增 `sw_lib/workflow/stage_state.py`，是 Gate/Route 的**唯一**访问点：

```python
def parse_template_gate(stage) -> list[GateItem]  # 唯一还解析 Markdown Gate 的地方（读 templates/）
def seed_gate(task, stage) -> bool                # 从模板播种 gate.items（幂等）
def read_gate(task, stage) -> GateState           # 读取，不碰任务 Markdown
def sign_gate(task, stage, by="user") -> bool     # 签署全部项
def reset_gate(task, stage) -> None               # 返工时撤销签署
def read_route(task, stage="04-review") -> str | None
def write_route(task, target, by="user") -> bool  # 非法阶段名拒写
def reset_route(task, stage="04-review") -> None
def stage_todo(task, stage) -> list[str]          # 校验：纯 JSON 判定
def render_gate_section(task, stage) -> bool      # 单向渲染：状态 -> 文件
```

写入端校验（`write_route` 拒绝非法阶段名）意味着下游不必再重复验证 —— 这是
`check_04-review.sh` 能从「取值 + 查白名单」简化为「确认决策存在」的前提。

`check_stage_compliance` 的门禁部分改为调用 `stage_todo`。原先那 11 处
`## Gate` 定位与 11 处 Route 解析点全部退场：判定路径改读 JSON，承载它们的
5 个函数（`auto_check_gate`、`parse_route_field`、`ensure_gate_section`、
`_reset_gate_checkboxes`、`_auto_fill_evidence_from_ai_output`）物理删除，
不留第二条路径。

### 3.4 Markdown 的新角色

Markdown 保留全部人类可读性，Gate 区变为**状态的渲染结果**：

```markdown
## Gate
<!-- 由 sw 渲染，编辑无效；状态存于 .state -->
- [x] Tests pass
- [x] No regression risk
```

`render_gate_section(task, stage)` 在签署后重写这一段。注释明确告知用户
（和 agent）编辑无效，避免误导。

关键：**渲染是单向的**。即使 agent 或用户手改了这些复选框，判定结果不变，
因为判定只读 JSON。bug 4 那种「agent 正文写了 `## Gate`」的情况下，重复的
Gate 区最多是显示噪音，不再影响门禁 —— 这是 G3 的直接结果。

### 3.5 AI Output 的落盘

`_save_stage_output` 的歧义同源。改为用不可伪造边界包裹：

```markdown
## 🤖 AI Output
<!-- sw:ai-output:start 7f3a9c2e -->
（agent 产出原文，不做任何解析）
<!-- sw:ai-output:end 7f3a9c2e -->
```

nonce 在阶段首次落盘时随机生成并记入 `.state`（`stages.{stage}.output_nonce`），
同一阶段**复用**同一个值 —— 多轮 flush 才能替换上一次的产出而不是层层追加。

定位规则不依赖 `.state`：取文件里**第一个** `start` 标记，配上带同一 nonce 的
**最后一个** `end` 标记。两端都由 sw 写入，所以规则自洽：

* 取第一个 start —— 真正的围栏由 sw 写在 agent 文本之前，正文里出现的一定更靠后。
* 取最后一个同 nonce 的 end —— agent 有读文件权限，可能把围栏原样抄进正文；
  真正的收尾标记永远是最后那个。nonce 不同的 end（agent 瞎猜的）直接忽略。

不读 `.state` 是刻意的：状态丢失时仍要认出文件里已有的产出区，否则每轮 flush
都会追加一份。这两条都有测试锁住（`test_output_nonce_boundary.py`）。

---

## 4. Hook 脚本的处理

现有 5 个 hook 对 Markdown 的依赖分两类，处理方式不同：

**类型 A：结构存在性检查** —— 保留不动。

```bash
grep -q "## Task DAG" "$FILE"      # check_02
grep -q "## Security" "$FILE"      # check_04
grep -q "## Memory"   "$FILE"      # check_05
```

这些验证的是 agent **是否产出了要求的章节**，属于对产出内容的检查，本就该读
Markdown。不涉及状态判定，无歧义风险。

**类型 B：状态判定** —— 改为读 JSON。

```bash
# check_01 旧：grep -i -q "\[x\] Design approved" "$FILE"
# check_04 旧：ROUTE_LINE=$(grep -m 1 '^-\s*\*\*Route\*\*:\s*`' "$FILE")
```

新增 `sw state get <task> <stage> [gate|route]` CLI 子命令供 hook 调用。输出是
**裸值**（`signed` / `unsigned` / 归一化的阶段名），退出码表示「是否已就绪」，
所以 hook 里可以直接用 `||`；`--json` 给需要结构的调用方。

```bash
./sw state get "$TASK_NAME" 01-brainstorming gate >/dev/null || exit 1
ROUTE_VAL=$(./sw state get "$TASK_NAME" 04-review route 2>/dev/null || true)
```

这样 hook 与 Python 侧共用同一个 `stage_state.py`，不存在第二套解析。注意
Route 值经过归一化，比较时用小写（`05-archive`）。

前提是 `sw` 入口把退出码交回 shell —— 实施时发现它原本丢弃了 `main()` 的
返回值，等于 hook 里的 `||` 永远不触发（见 6.1）。

`check_04-review.sh` 的 Reroute Evidence 表检查保持读 Markdown —— 那是 agent
填写的证据内容，属类型 A。合法性校验（阶段名白名单）从 hook 移除，因为
`write_route` 在写入端就拒绝非法值。

---

## 5. 迁移

**无需迁移**。设计评审时 `workspace/tasks/` 下只有测试任务（helloworld / iiiii /
oooo / wwww / test-parse / web-deploy-test），用户授权直接删除，因此不实现
反向导入（从 Markdown 读回旧签署状态）—— 那条路径本身要解析 `## Gate`，
留着就等于留着根因。

新任务在阶段启动时由 `StageRunnable._seed_stage_gate` 播种门禁定义。旧任务
（`.state` 里没有 `stages` 字段）也能正常工作：`read_gate` 在无记录时回退模板
定义并报「待签署」，用户签一次即可 —— 代价是历史签署不被继承，这在测试任务上
没有成本。

### 5.1 回滚

`stages` 字段是纯新增。回滚只需还原代码，`.state` 里多出的字段被旧代码忽略
（`read_state` 不校验未知字段）。Markdown 仍是完整可读的，无数据丢失。

---

## 6. 实施记录

全部 9 步已完成（2026-08-22）。

| 步 | 内容 | 验收结果 |
|----|------|---------|
| 1 | `stage_state.py` 作为唯一读写入口 | 38 项新测试绿 |
| 2 | `check_stage_compliance` 门禁部分改读 JSON | 现有校验测试全绿 |
| 3 | 签署/选路入口改写 JSON（`tui.py`） | UI 测试全绿 |
| 4 | 测试适配 + 共享 fixture（`make_task` / `sign_gate`） | 全量绿 |
| 5 | `_save_stage_output` 改用 nonce 围栏 | **bug 4 回归 9/9 绿** |
| 6 | 阶段启动播种 + Gate 区单向渲染 | 12 项新测试绿 |
| 7 | `sw state get` + `check_01`/`check_04` 改读 JSON | CLI 集成 31 绿 |
| 8 | 删除旧解析点、`mock_fixups` 收敛 | pyflakes 干净 |
| 9 | 全量回归 | 单元 592 + CLI 31 + e2e 32/32 |

### 6.1 落地时发现的额外问题

实施过程中暴露了三个设计阶段没预见的缺陷，均已修复并有测试覆盖：

1. **`sw` 入口丢弃退出码**（`sw:19`）。`main()` 的返回值没有传给 `sys.exit`，
   hook 里 `sw state get ... || exit 1` 会永远判为成功 —— 门禁形同虚设。
   现为 `sys.exit(main() or 0)`，由 `test_cli_propagates_exit_code` 锁住。

2. **`WorkflowRuntime.advance` 的读-改-写丢失**（`runtime.py`）。函数入口读一次
   `.state`，末尾把那份旧快照写回去，于是中途 `reset_gate` 的写入被静默覆盖：
   返工/推进到的阶段仍带着上一轮的签名，直接放行。修法是重置后重读再改字段。

3. **推进路由仍读 Markdown**。`advance` 用 `parse_route_field`（无锚点
   `re.search`）取 Route，而校验已改读 JSON —— 两个源可以给出不同答案，
   agent 在正文里写一句 ``**Route**: `03-Coding` `` 就能改变推进目标。
   现在统一读 `.state`。

### 6.2 `mock_fixups` 的收敛结果

它原本做全局 `[ ]`→`[x]` 与 Route 占位符归一化，那些动作现在**完全无效**
（判定不看 Markdown）。实测摘除后 e2e 卡在 01 阶段的「3 个选项组尚未拍板」——
它唯一还起作用的是填 `- **Chosen**: ___`。因此改名为
`apply_mock_template_fixups`，职责收窄为「填 MockAgent 不会写的模板内容」，
并明确不碰 Gate 与 Route。产出区（nonce 围栏内）一概不动。

### 6.3 选项组：初版把它排除在 JSON 之外是错的

3.2 原先论证「选项组是内容检查，解析失败只会让用户多填一行」。**这个论证是
错的**，任务 T1 证伪了它：用户经 ask_user 选了「A. 简单交易日历表」，Gate 也
签了，`/advance` 仍报「3 个选项组尚未拍板」，而且**用户无从下手** —— 决定早就
做过了，缺的是 agent 把它转写成 `[x]` / `(Chosen)` / `- **Chosen**:` 之一。
反复 `/advance` 只会反复失败。

所以它不是「多填一行」，而是死锁：判定依据握在 agent 手里，正是本文档
第 1 节要根治的那个模式。`mock_fixups` 之所以必须保留填 `- **Chosen**: ___`
的动作（见 6.2），本身就是这个耦合的症状 —— 当时被当成了偶然。

修法与 Gate/Route 一致：ask_user 的答复落进 `stages.{stage}.decisions`，
校验读它。三条写入路径都接上，缺一条就会在对应前端复现：

1. TUI 用户作答（`_dispatch` → `_record_decision`）
2. 无人值守代答（`_on_ask_user` 的 `_auto_answer` 分支，记 `decided_by=auto`）
3. Web 前端提交（`WebEngineSession.submit_answer`）

Markdown 记法保留为兼容回退（老任务、agent 确实回写了的情况仍然认），但不再是
唯一依据。`_count_unresolved_choice_groups` 的结果会被拍板数抵扣。

教训：判断一个字段「是不是判定依据」，标准不是它看起来像内容还是像状态，而是
**它能不能阻塞推进**。能阻塞的就必须存在用户/系统可写、agent 不可写的地方。

---

## 7. 收益与代价

**收益**：bug 1/2/3/4 属于同一根因，全部消除且不可复发（G3 保证 agent 写什么
都不影响判定）。判定逻辑从 4 种语义收敛为 1 个模块，新增阶段或字段不再需要
考虑「这里该用 find 还是 rfind」。

**代价**：改动覆盖 `sw_lib/workflow`、`sw_lib/ui`、`sw_lib/cli`、2 个 hook 与
e2e harness。删除死代码 5 个函数（`auto_check_gate`、`parse_route_field`、
`ensure_gate_section`、`_reset_gate_checkboxes`、
`_auto_fill_evidence_from_ai_output`）。

**残留的 Markdown 解析**（全部是内容检查，不是判定）：

* `parse_template_gate` —— 读 `templates/`，agent 碰不到的文件。
* `_count_unresolved_choice_groups` —— 选项组是否拍板。
* `parse_route_from_ai_output` —— 自动模式下推测 agent 结论，结果仍要写 `.state` 才算决定。
* `extract_evidence_table` / hook 的章节存在性检查 —— 验证 agent 是否产出了要求的内容。

这些的共同点是：解析失败只会让用户多做一步，不会让门禁被绕过。
