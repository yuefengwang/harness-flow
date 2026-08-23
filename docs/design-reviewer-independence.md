# 设计文档：Reviewer 独立性改造

> 版本：**v2**（2026-08-23 重整，消除 v1 四轮增补造成的自相矛盾）
> 状态：**待评审**。所有源码引用均已核实并标注行号。
> 上游：`docs/self-verification-gaps.md` 的 F1 / F2 / F8 与 R6 / R7。
> 范围：04-review 阶段的信息输入、审查角色划分、路由决策。
>
> 📌 **实施已拆分**：本文档保留为**分析与决策依据**，不再作为开发任务清单。
> 可执行的任务拆分见 [`docs/design/README.md`](design/README.md)（A0-A11）。
> 第 9 节的任务表已由拆分文档取代。

**一句话**：让 04-review 从「转述 developer 的结论」变成「基于机器事实的三轨审查」。

---

## 第一部分：问题

## 1. 现状缺陷

### 1.1 双向错配

| 方向 | 现象 | 证据 |
|---|---|---|
| 多了不该有的 | 整份 `03-coding.md` 注入 reviewer，含 developer 自填的 Implementation Notes / Decisions / 自签 Gate | `sw_lib/prompts/builder.py:63` 调用 `_read_previous_stage()`（定义在 `:116`） |
| 少了该有的 | prompt 中**完全没有 diff** | `grep -rn "diff" sw_lib --include="*.py"` 返回零结果 |

`templates/04-review.md:5` 第一行问 "Is the diff self-explanatory?"，
而 reviewer 手上从来没有 diff。它有 `read_file` 工具，但无人告知该读哪些文件。

**后果**：reviewer 在信息上除了转述别无选择。

### 1.2 缺失的审查角色

现有 reviewer 只做一种事：给出总体评价。而下列缺陷**任何反例都攻不破**：

- 三层嵌套 if-else，功能完全正确。
- 为单一调用点造的三层抽象，测试全绿。
- 业务逻辑塞进 IO 层，接口耦合上下游。

这些正是 `hooks/03-coding.md:15`（`hook-03-03`，"Max 3 nested if-else"、
"50 行能写完绝不写 200 行"）与 `hooks/04-review.md:10`（`hook-04-02`，
"检查是否有单次使用的多余抽象"）已写明的要求。**规则早已存在，从无执行者。**

### 1.3 自校验失效的三个成因

这是全文的分析框架。三者独立，必须分别对付：

| 成因 | 机制 | 换模型能解决吗 |
|---|---|---|
| **C1 上下文污染** | developer 的结论以权威文档形式传给 reviewer | 否 —— 需信息隔离 |
| **C2 先验相关** | 同权重模型的盲区重合，独立采样不等于独立犯错 | 部分 |
| **C3 判据缺席** | reviewer 手上没有「正确行为」的独立定义，只能猜 | **否 —— 这是根本问题** |

> **C3 是核心**。即使换成完全不同的模型、完全隔离上下文，reviewer 面对
> 「这个函数该返回什么」仍然只能推测，依据是散文 spec。散文可以被两个模型
> 以两种方式误解，也可以被同一个模型一致地误解。
>
> **没有独立判据时，「审查」在结构上等价于「再猜一次」。**

### 1.4 本设计的能力边界（重要）

| 成因 | 对策 | 归属 |
|---|---|---|
| C1 | 事实包 + 结构化事实改由 `.state` 承载 | **本设计**（第 4、5 节） |
| C2 | 三个异构模型 + 角色分工 | **本设计**（第 6 节） |
| C3 | 测试冻结前置（R1）+ 可执行验收场景（R8） | **不在本设计内，必须并批** |

> **本设计单独实施无法解决「自问自答」。** 它解决 C1、缓解 C2。
> C3 的主要部分靠 R1：测试在 03a 阶段冻结后，成为独立于 developer 与
> reviewer 双方的判据，reviewer 的任务从「评价」变成「**核对**」——
> 核对不需要它拥有业务真理。
>
> 因此 **R1 必须先于本设计实施**，见 9.1 批次划分。

---

## 2. 设计原则

沿用 `docs/design-json-state-source.md` 的原则并扩展一条：

> 原有：**机器读的东西 agent 不能写，agent 写的东西机器不解析。**
>
> 新增：**审查者的输入必须是机器生成的事实，其输出必须可被机器部分核实。**

四条已确认的决策（2026-08-23）：

1. reviewer 必须看到 diff。
2. 业界成熟做法权重最高（见第 8 节）。
3. 客观核查与主观判断**都保留**，分轨承载。
4. 用多个异构模型规避先验盲区。

---

## 第二部分：架构

## 3. 三轨审查

### 3.1 总流程

事实包由 harness 生成后，分三轨并行：

```
03-coding 产出
      ↓
harness 生成事实包（diff / 测试结果 / 覆盖率 / spec / plan）
      ↓
  ┌───┴────────────────┬────────────────────┐
  │                    │                    │
客观轨               攻击轨              设计轨
hook 脚本         adversary agent    design_critic agent
不可说服           产出反例            产出设计意见
  │                    │                    │
  │            harness 执行验证      规则交叉校验
  │                    │                    │
  └───────────┬────────┴────────────────────┘
              ↓
        Route 仲裁器（第 7 节）
              ↓
  05-archive / 03-coding / 02-planning / 01-brainstorming
```

### 3.2 三轨职责

| 轨 | 问题 | 判定者 | 产出 | 证据强度 |
|---|---|---|---|---|
| 客观 | 构建过了吗？测试绿吗？ | shell hook | 通过 / 失败 | **硬**（不可说服） |
| 攻击 | 它会不会错？ | `adversary` agent | 可执行反例 | **硬**（harness 可验证） |
| 设计 | 它是否应该这样写？ | `design_critic` agent | 设计意见 + evidence | **中**（evidence 可部分核对） |

**三轨都不单独决定 Route**，由仲裁器按固定优先级合并。

### 3.3 为什么攻击与设计必须分开、且用不同模型

| | 攻击者 | 设计审视者 |
|---|---|---|
| 输入 | diff + 冻结测试 + spec | diff + 冻结测试 + spec + **02-planning 架构决策** |
| 可验证性 | 可 —— harness 执行 | 不可 —— 需规则辅助 |
| 失败模式 | 构造不出反例（漏检） | 空泛评论、风格偏好当缺陷 |
| 最优心态 | 悲观、找漏洞 | 结构感、克制 |

三条理由：

1. **任务隔离**：两者最优心态冲突，单次推理承担双目标会同时降低两者质量。
   这与用哪个模型无关，是任务混合本身的问题。
2. **先验去相关（C2）**：设计品味是模型的强先验。与 developer 同权重的模型
   倾向于**认可自己会写出的结构** —— 这正是过度抽象逃过审查的机制。
3. **失败模式不重叠**：漏检与空泛是两类错误，由不同模型承担则不会被同一盲区放过。

> 设计审查恰好是 C3 影响最小的场景 —— 「嵌套深度」「单次使用的抽象」这类判断
> **不需要业务真理**，只需结构感。因此它是主观轨里最适合交给 LLM 的部分。

**两个 agent 互不可见对方产出**，否则先跑完的一方会锚定另一方 ——
那是 C1 在主观轨内部的复现。

---

## 4. 事实包（Fact Pack）

### 4.1 内容

落盘至 `workspace/tasks/<task>/facts/`，harness 生成，agent 只读。

| 文件 | 内容 | 生成方式 | 供给 |
|---|---|---|---|
| `diff.stat` | 文件级增删统计 | `git -C <target_dir> diff --stat <baseline_sha>` | 三轨 |
| `diff.patch` | 完整变更 | `git -C <target_dir> diff <baseline_sha>` | 攻击轨、设计轨 |
| `diff.truncated` | 因体积被截断的文件清单 | 截断时生成 | 攻击轨、设计轨 |
| `tests.json` | 收集数、通过、失败、skip、退出码 | harness 执行 pytest / npm test | 三轨 |
| `coverage.json` | 覆盖率（若可得） | `coverage json` / diff-cover | 客观轨 |
| `spec.md` | 需求与已拍板决策 | 见 4.4 | 攻击轨、设计轨 |
| `plan.md` | Task DAG 与架构决策 | 从 `02-planning.md` 提取 | **仅设计轨** |

> `plan.md` 只给设计轨是刻意的：设计审查需要对照物，
> 「这样写合理吗」必须相对某个声明过的设计意图，否则退化为个人偏好。

### 4.2 生成时机

03 准出 hook 通过之后、04 prompt 构建之前。
**每次进入 04 都重新生成**（含返工后再次进入），否则审的是旧 diff。

### 4.3 diff 截断

截断本身会成为新盲区 —— 被截掉的部分等于没审。因此：

1. `diff.stat` 始终全量注入（体积小）。
2. `diff.patch` 按风险排序注入：生产代码 > 配置 > 测试 > 文档；同级内变更行数大者优先。
3. 被截断的文件清单**必须显式写入** `facts/diff.truncated` 并注入 prompt，
   让审查者知道自己没看全；review 产出中标注「未覆盖审查范围」。
4. 触发截断时 hook 输出警告，不静默。

### 4.4 `spec.md` 的来源（v1 遗留歧义，此处定案）

v1 写「从 `.state` 的 `decisions` 渲染」是不充分的 ——
`stage_state.read_decisions()` 存的是 `ask_user` 的问答对，**不是需求规格**。
01 阶段的实际产出在 `01-brainstorming.md` 里且是散文。

**定案**：`spec.md` 由三部分拼装，各自标注可信度：

| 段 | 来源 | 可信度 |
|---|---|---|
| 原始需求 | `.context`（用户原文） | 高 —— 用户输入，agent 未加工 |
| 已拍板决策 | `.state` 的 `decisions`（`stage_state.py` 的 `read_decisions`） | 高 —— 用户经 `ask_user` 确认 |
| 设计陈述 | `01-brainstorming.md` 的 ADR 段 | **低 —— agent 自述，仅作参考** |

审查者被明确告知第三段可信度低。
**真正的规格判据来自 R8（可执行验收场景），本设计不假装解决这一点。**

---

## 5. 03 阶段交付给 04 的内容

### 5.1 目标态

| 类别 | 内容 | 载体 | 04 是否可见 |
|---|---|---|---|
| 机器事实 | diff、测试结果、覆盖率 | `facts/` | ✅ 全部 |
| 冻结判据 | 03a 的测试文件 + `red_witness` 哈希 | `.state` + 测试文件 | ✅（依赖 R1） |
| 结构化声明 | Task ID、Verify cmd、Files touched | **`.state`** | ✅ |
| 自由叙述 | Implementation Notes、Decisions、AI Output | `03-coding.md` | ❌ **完全排除** |

**04 阶段完全不读 `03-coding.md`。**

### 5.2 为什么用 `.state` 而不是解析 md（修正 v1 的错误）

v1 曾提议复用 `stage_state.py` 的 nonce 围栏来切分「事实 / 评价」。**那是错的**：
围栏区分的是「谁写的」（agent 产出 vs 模板），不是「是事实还是评价」——
Task ID 与 Verify cmd 同样可能被 agent 写在围栏内。

**正确做法**：03 准出时把结构化字段写入 `.state`（与 Gate / Route 同机制），
04 从 `.state` 读。这样边界是机器可判的，不依赖任何文本解析。

### 5.3 已知代价

developer 说「这里用了 X 模式因为 Y」对设计审查有真实价值，排除叙述会损失它。
**折中（第二批）**：允许 developer 向 `facts/notes.md` 写受限的事实性说明
（无结论、无评价），03 准出校验其不含判定性措辞。第一批不做。

---

## 6. 审查角色配置

### 6.1 三个角色

| 角色 | 职责 | 工具权限 | 模型要求 |
|---|---|---|---|
| `adversary` | 构造可执行反例 | 只读 + 提交反例专用工具 | 与 developer 不同 provider |
| `design_critic` | 设计合理性审视 | **纯只读** | 与前两者都不同 provider |
| （客观轨） | — | 无 agent | — |

### 6.2 工具权限必须收紧（已核实的现存问题）

`config/config.yaml:79` 起，现有 reviewer 的 tools 为
`list_files` / `read_file` / `run_command`。虽无 `write_file`，但
`run_command` 白名单（`toolbox.py:24` 起）含 `python` / `sh` / `bash` /
`sed` / `cp` / `mv` / `touch` —— **等价于完全写权限**。
reviewer 可以直接改代码「顺手修掉」问题，审查与实现的边界形同虚设。

白名单中**不含** `rm`（已核实），风险略低于预期，但不改变结论。

**要求**：`design_critic` 纯只读；`adversary` 的反例提交走专用工具，
不通过通用 `run_command`。

### 6.3 实现约束（已核实的硬约束）

当前体系是**一阶段一角色的单射**，新增第二个审查者**必须改代码**：

| 位置 | 现状 | 影响 |
|---|---|---|
| `config/config.yaml:84` | `stage_roles` 是 `stage -> role` 一对一 | 无法声明两个角色 |
| `core/config.py:137` / `:185` | `resolve_agent_type()` / `resolve_agent_model()` 以 stage 为键 | 无法解析出两个模型 |
| `core/bootstrap.py:92` | factory 签名 `factory(stage, task_name)` | 一个 stage 只产出一个 agent |
| `tools/toolbox.py:254` | `get_tools_for_stage(stage)` 以 stage 为键 | **两个审查者无法有不同工具权限** |

> v1 曾写「无需改动代码，仅改配置即可」。该判断只对**替换单个 reviewer 的模型**
> 成立，对新增角色不成立。此处更正。

**最小改造**：解析函数增加 `role_id` 维度，`stage_roles` 支持列表：

```yaml
stage_roles:
  04-review: [adversary, design_critic]   # 列表 = 并行多角色
```

字符串值仍按单角色处理，现有配置行为不变。

---

## 第三部分：判定

## 7. 产出契约与仲裁

### 7.1 攻击轨：反例

**形态收窄（第一批只允许一种）**：pytest 测试函数，写入
`facts/counterexamples/ce-<n>_test.py`，由 harness 用固定命令执行。
其余形态（shell 命令、curl 等）推至第二批。

每条反例含：`id`、`target`（文件与函数）、`input`、`expected`（引用 spec 出处）、
`actual`、`repro`（固定格式的执行命令）。

判定：

- **可复现** → 硬证据，直接支持 route 到 03-coding。
- **不可复现** → 记为「无效反例」，不影响 Route，计入质量档案。
- **全部不可复现** → 不等于审查通过，见 7.4 的 N1。

反例由 **harness 执行**，不由 agent 自验，否则循环闭合在同一模型内。

### 7.2 设计轨：意见 + 可核对 evidence

每条设计意见必须含：

| 字段 | 约束 |
|---|---|
| `location` | 文件与行号，**必须落在 diff 范围内** |
| `rule` | 规则 id，**只能取既有集合**：`hook-03-03` / `hook-04-01` / `hook-04-02` / `plan-drift`。不允许自创 |
| `evidence` | 客观可核对的事实，如「嵌套深度 4」「该抽象仅 1 处调用」 |
| `severity` | high / med / low |
| `scope` | coding / planning |
| `suggestion` | 具体改法，不接受「建议优化」这类空话 |

### 7.3 规则交叉校验（抑制空泛评论）

| 规则 | 可核对部分 | 校验方式 |
|---|---|---|
| `hook-03-03` | 「嵌套深度 N」 | AST 计算真实深度 |
| `hook-04-02` | 「仅 N 处调用」 | 静态计数调用点 |
| `hook-04-01` | 「改动了无关文件」 | 与 diff 文件清单比对 |
| `plan-drift` | 「plan 中无此模块」 | 与 `facts/plan.md` 比对 |

**校验不通过的意见被驳回，不进入仲裁**，但计入 `design_critic` 的质量档案。
这把「意见是否成立」从模型自述变成部分可测，与 7.1 的「无效反例」同构。

> **固有边界**：只有带可量化 evidence 的规则能被交叉校验。
> 纯结构判断（如「业务逻辑塞进了 IO 层」）无法机械核对，
> 仍需 `location` 加 `suggestion` 供人判断。不掩盖这一点。

### 7.4 Route 仲裁器（唯一权威表，替代 v1 的两张表）

**agent 不再直接写 Route**，只提交结论与证据；仲裁器按固定优先级决定。
这顺带消除 F8：`templates/04-review.md:18`（「无需等待用户确认」）与
`hooks/04-review.md` 的 `hook-04-07`（「不得自行填写」）的矛盾自然消失。

| 优先级 | 条件 | Route | 证据强度 | 需用户确认 |
|---|---|---|---|---|
| 1 | 客观轨硬失败（构建 / 测试 / README 错误） | 03-coding | 硬 | 否 |
| 2 | 存在可复现反例 | 03-coding | 硬 | 否 |
| 3 | 设计意见 severity=high、通过交叉校验、scope=coding | 03-coding | 中 | 是 |
| 4 | 设计意见 severity=high、通过交叉校验、scope=planning | 02-planning | 中 | 是 |
| 5 | 需求层变更（仅能由用户提出） | 01-brainstorming | — | 是 |
| 6 | 全部通过 | 05-archive | — | 否 |

补充规则：

- **N1 空转防护**：若攻击轨提交了反例但全部不可复现，且设计轨无通过校验的意见，
  **不得走优先级 6**。此时标记「审查未产生有效结论」并交用户裁决。
  否则「审查者胡说八道」会被当成「审查通过」。
- **N2 scope 交叉校验**：优先级 4 的 `scope=planning` 必须能在 `facts/plan.md`
  中定位到对应条目，否则降级为 `scope=coding`。防止审查者把抓不到的代码问题
  推给上游以逃避责任。
- **N3 客观轨严重性不依赖 Route**（消除 v1 的循环依赖）：
  `check_04-review.sh` 现有逻辑中 README 缺失在归档路由时是错误、返工路由时是警告
  —— 这形成「Route 决定严重性、严重性又决定 Route」的循环。
  **定案**：客观轨一律按最严标准判定，与 Route 无关。返工时的宽容由
  优先级顺序体现（已经在返工路径上，不会因 README 再次返工）。

写入走既有受控入口 `stage_state.write_route()`（`stage_state.py:245`），
新增 `by="arbiter"`。

### 7.5 返工收敛

`MAX_REROUTE = 3`（`core/config.py:30`）已存在。新增约束：

- **同一反例重复出现不计入新信息**。若某轮返工后 04 产出的反例集合
  与上一轮完全相同，说明循环未推进，应中止自动流程并交用户。
  （对应 R12 的「每轮必须产生新信息」。）
- 反例一旦成立并触发返工，修复后**该反例测试应永久进入判据集**，
  否则同一 bug 可反复出现，违反 R10「oracle 成本单调递减」。
  具体归属见 10.2 的 D3。

---

## 8. 业界成熟做法对照

| 做法 | 先例 | 本设计对应 |
|---|---|---|
| 审查者信息不对称反转：多看客观事实，少看主观陈述 | GitHub PR review —— reviewer 默认看 diff 与 checks，PR description 是可折叠的次要信息 | 第 4、5 节 |
| 判据不接受 agent 自述 | SWE-bench harness —— 只看隐藏测试执行结果 | 7.4 优先级 1、2 |
| critic 输入为可验证产出物、输出为可验证挑战 | debate / critique 系列研究方向 | 7.1、7.2 |
| 客观检查下沉为不可说服的脚本 | CI required checks（branch protection）—— 人的 approve 无法覆盖失败的 check | 3.2 客观轨 |
| 审查者不应能修改被审对象 | 职责分离原则 | 6.2 工具收紧 |
| 多模型降低相关性 | ensemble / cross-model review | 第 6 节三个 provider |

**不采用**：LLM-as-judge 作为门禁。它可被优化掉，只适合分诊过滤。
本设计中两条主观轨都不具备单独放行权（优先级 1、2 在其之上，且 N1 兜底）。

---

## 第四部分：实施

## 9. 批次与任务

### 9.1 批次划分（含前置依赖）

| 批次 | 内容 | 为什么在这个位置 |
|---|---|---|
| **批 0** | R2 证据不可伪造 + T0 仓库形态 | 一切的地基。见 10.1 |
| **批 1** | **R1 Red 见证 / 测试冻结** | 提供独立判据，解决 C3 主要部分 |
| **批 2** | **本设计**（T0-T12） | 解决 C1、缓解 C2 |
| 批 3 | R4 变异注入 | 测量批 1、2 是否真的有效 |
| 批 4 | R8 可执行验收场景 | 解决 C3 的需求层部分 |

> **R1 必须先于本设计。** 否则审查者拿到的仍只有「代码 + 散文 spec」，
> C3 完全没被触及。v1 把 R1 列为「遗留问题」是定位错误。

### 9.2 任务清单（批 2）

| # | 任务 | 文件 | 验证方式 |
|---|---|---|---|
| T0 | 任务创建时 `git init` 目标目录并建立基线提交；所有 git 调用改用 `git -C` | `sw_lib/core/service.py:120` 附近 | 子仓库 `rev-parse --show-toplevel` 等于 target_dir，父仓库 status 干净 |
| T1 | `.state` 增加 `review.baseline_sha`，创建时锚定 | `stage_state.py`、`service.py` | 字段存在且为合法 sha |
| T2 | 03 准出把结构化字段写入 `.state` | `sw_lib/workflow/base.py` | Task ID / Verify cmd 可从 `.state` 读出 |
| T3 | 事实包生成器 | 新增 `sw_lib/workflow/fact_pack.py` | 七个文件生成；基线失效时抛错而非跳过 |
| T4 | `STAGE_CONTEXT_POLICY`：04 不读 `03-coding.md`，改读 `.state` + `facts/` | `sw_lib/prompts/builder.py` | 04 prompt 含 diff.stat、不含 03 叙述 |
| T5 | `stage_roles` 支持列表；解析函数增加 `role_id` 维度 | `core/config.py`、`core/bootstrap.py`、`tools/toolbox.py` | 单角色配置行为不变；列表配置产出两个 agent 且工具权限可分别配置 |
| T6 | 04 并行执行两个审查者，产出隔离 | `sw_lib/workflow/base.py` | 两者 prompt 互不含对方产出 |
| T7 | 攻击者 prompt 模板 | `templates/04-review.adversary.yaml` | 人工核对 + mock 回归 |
| T8 | 设计审视者 prompt 模板 | `templates/04-review.design.yaml` | 人工核对 + mock 回归 |
| T9 | 反例执行器（形态限定为 pytest 函数） | 新增 `sw_lib/workflow/counterexample.py` | 可复现反例标 `verified=true` |
| T10 | 设计意见规则交叉校验器 | 新增 `sw_lib/workflow/design_rules.py` | 嵌套深度、调用点计数、越界文件各一例 |
| T11 | Route 仲裁器（6 级 + N1/N2/N3） | 新增 `sw_lib/workflow/review_arbiter.py` | 6 条规则 + 3 条补充规则各一例 |
| T12 | 消除 F8；角色配置为三个不同 provider；收紧工具权限 | `templates/04-review.md`、`hooks/04-review.md`、`config.yaml` | 三者 `resolve_agent_model` 返回值互不相同 |

### 9.3 第二批（增强）

| # | 任务 |
|---|---|
| T13 | 同类角色配多模型并行，取反例并集（需定义分歧处置） |
| T14 | `facts/notes.md` 受限事实性说明（5.3 的折中） |
| T15 | 覆盖率与 diff-cover 接入事实包（与 R3 合并） |
| T16 | 审查者质量档案看板：无效反例率、被驳回意见率、漏检率 |
| T17 | 反例形态扩展（shell / HTTP） |

### 9.4 验收标准

**机制接通类**（必要但不充分）：

1. 04 prompt 含 `diff.stat` 且内容非空，不含 `03-coding.md` 叙述。
2. 基线 sha 被破坏时 04 阶段硬失败，不降级跳过。
3. 04 实际启动**两个**不同 provider 的 agent，互不可见对方产出。
4. 三个角色（developer / adversary / design_critic）的
   `resolve_agent_model()` 返回值互不相同。
5. 可复现反例自动触发 route 到 03-coding，`.state` 留痕。
6. 客观轨失败时，两条主观轨的「通过」结论无法放行（优先级 1 生效）。

**有效性类**（真正的判据）：

7. **变异注入探针**：将 diff 中某函数体替换为 `raise NotImplementedError`，
   攻击者应产出可复现反例。改造前作为对照组。
8. **设计缺陷探针**：注入「功能正确但嵌套深度 5」的变更，
   设计审视者应报 `hook-03-03`，且 evidence 深度值能通过 AST 校验。
9. **伪造 evidence 探针**：提交声称「嵌套深度 6」而实际为 2 的意见，
   交叉校验必须驳回，且不产生 Route 效果。
10. **失败模式不重叠**：变异注入场景由攻击者捕获，过度抽象场景由设计审视者捕获，
    两者不应互相替代。
11. **N1 空转防护**：构造「反例全部不可复现且无有效设计意见」的场景，
    不得走优先级 6 自动归档。

> 第 7 至 11 条是唯一验证「审查能力真的提升」的判据。
> 第 1 至 6 条只验证接线正确。**不要用前六条宣布完成。**

---

## 10. 依赖、决策与风险

### 10.1 硬依赖：R2（证据不可伪造）

`facts/` 与 `review.baseline_sha` 若可被 agent 写，整套设计失效。
已核实的绕过路径：

| # | 路径 | 证据 |
|---|---|---|
| 1 | 写保护只匹配文件名，`facts/` 不在保护范围 | `toolbox.py:129` 的 `target.name in _PROTECTED_FILES` |
| 2 | `restricted` 检查是命令字符串匹配，可拼接绕过 | `toolbox.py:182` 的 `".state" in command` |
| 3 | 白名单含 `python` / `sh` / `bash` / `sed`，可 `python -c` 写任意文件 | `toolbox.py:24` 起 |

**R2 必须先落地或同批落地，不可后置。**

### 10.2 已定案的架构决策

#### D1：目标目录的版本控制形态 —— 嵌套独立 git 仓库

**背景**：harness-flow 自身是一个 git 仓库；`repo/` 期望支持多个独立仓库但当前不是；
`.gitignore:36` 的 `repo/` 是为了不污染 harness 提交。
**核心矛盾**：不隔离则 `git diff` 分不清看的是 harness 还是 repo。

**实测验证**（探针已清理）：

| 验证项 | 结果 |
|---|---|
| 子仓库内 `git diff --stat` 正常反映变更 | ✅ |
| 父仓库 `git status` 看不到子仓库内部改动 | ✅ 输出为空 |
| 移除父仓库 `.gitignore` 后，含 `.git` 的子目录被视为 embedded repo | ✅ 报 `warning: adding embedded git repository` |

**定案**：每任务一个独立仓库；`.gitignore` 中的 `repo/` **保留**
（嵌套 `.git` 已提供隔离，gitignore 是第二层保险，可防误 `git add`）。

实施纪律（两条，缺一则歧义复现）：

1. 所有 git 调用统一 `git -C <target_dir>`，**禁止依赖进程 cwd**。
2. 生成 diff 前校验 `git -C <target_dir> rev-parse --show-toplevel`
   等于 `target_dir` 自身；返回 harness 根目录则**硬失败**。

两个特例：

- `target_dir == "."`（`commands.py:93` 允许）：不做 `git init`，
  基线取 harness 当前 HEAD，diff 时排除 `workspace/` 与 `repo/`。
- 存量任务无 `.git`：首次进入 04 时就地 `git init` 并以当前状态为基线，
  事实包标注「基线为补建，diff 可能不完整」。不静默通过，也不硬失败。

附带收益：`config.yaml` 中无人消费的 `base_branch` / `branch_prefix` 死配置
（已核实：`grep -rn "git checkout\|git branch" sw_lib --include="*.py"` 零结果）
可以落地或明确删除。**建议本批删除**，避免继续误导。

#### D2：反例形态 —— 收窄为 pytest 测试函数

见 7.1。理由：形态不定则执行器无法实现。

#### D3：反例测试与 `red_witness` 的归属（需 R1 落地时同步确认）

两个 P0 机制的接口，必须在 R1 实施时定义：

- reviewer 在 04 新增反例测试文件，**是否破坏** `red_witness` 哈希？
  倾向：反例文件存于 `facts/counterexamples/`，与 `red_witness` 覆盖的
  测试目录**物理隔离**，因此不破坏。
- 反例成立触发返工，developer 修复后，该反例**是否进入 `red_witness`**？
  倾向：**应该进入**，否则同一 bug 可反复出现，违反 R10 与 R12 单调性。

> 标注为「倾向」而非「定案」—— 需在 R1 设计时一并拍板。

### 10.3 尚未消除的歧义（明确列出，不假装解决）

| # | 歧义 | 现状 |
|---|---|---|
| U1 | `severity=high` 由审查者自评 | 已用「必须通过交叉校验」约束（7.4 优先级 3、4），但 severity 分级本身仍是自评 |
| U2 | 纯结构类设计意见无法机械核对 | 7.3 已声明为固有边界 |
| U3 | 截断盲区 | 4.3 缓解（显式列出未审文件），未根除 |
| U4 | 同类角色多模型的意见分歧处置 | 推至 T13。攻击者与设计审视者职责不重叠，第一批无此问题 |
| U5 | 越界 diff：developer 改动 `target_dir` 之外的文件 | `toolbox.py:71` 的 `_safe_path` 只拦项目根之外。`hook-04-01` 正需此信息，第一批不覆盖 |

### 10.4 已核实的次生风险

| # | 风险 | 证据 | 处置 |
|---|---|---|---|
| P1 | `.state` 无锁且非原子写 | `core/state.py:112` 直接 `open(sf, "w")` + `json.dump`，无 flock、无临时文件 rename | 事实包生成、反例回填、仲裁写 Route 三处并发写；Web 与 TUI 可能同时运行。**建议本批改为临时文件 + `os.replace` 原子写** |
| P2 | mock 模式下事实包硬失败会挂掉 CI | `tests/e2e-flow` 与 `tests/unit` 依赖 `MockAgent`（`is_mock_agent()`，`core/config.py:250`） | 需定义 mock 下的事实包行为：生成空壳并标注 `mock=true`，跳过基线校验 |
| P3 | 反例执行的沙箱 | 白名单无 `rm`，但有 `python` / `sh` | 反例限定 pytest 函数（D2）+ 固定执行命令 + 超时；不走通用 `run_command` |

### 10.5 回滚

改动集中在 prompt 组装层、hook 脚本与新增模块，不动 LangGraph 拓扑
（`graph.py` 的一阶段一 invoke 模型保持不变）。
`STAGE_CONTEXT_POLICY` 缺省 `full`，`stage_roles` 缺省单角色，
删掉 04 相关条目即恢复旧行为。

---

## 11. 确认记录

| 日期 | 条目 | 结论 | 落点 |
|---|---|---|---|
| 2026-08-23 | reviewer 需看到 diff | 采纳 | 第 4 节 |
| 2026-08-23 | 业界成熟做法权重最高 | 采纳 | 第 8 节 |
| 2026-08-23 | 客观核查与主观判断都保留 | 采纳 | 第 3 节三轨 |
| 2026-08-23 | 多 LLM 规避先验盲区 | 采纳 | 第 6 节三 provider |
| 2026-08-23 | `repo/` 需支持多仓库且 diff 不能混淆 | 采纳，方案已实测 | 10.2 D1 |
| 2026-08-23 | 需补基于 diff 的设计合理性审查，由另一模型承担 | 采纳 | 3.3、7.2、7.3 |

### v1 → v2 的实质性更正

| # | v1 说法 | v2 更正 |
|---|---|---|
| 1 | 复用 nonce 围栏切分「事实 / 评价」 | **错误**。围栏区分「谁写的」，非「事实 / 评价」。改为 `.state` 承载（5.2） |
| 2 | 异构模型是「自问自答」的解决方案之一 | **高估**。它只缓解 C2，不解决 C3。C3 靠 R1（1.3、1.4） |
| 3 | R1 / R8 属「遗留问题」 | **定位错误**。它们是自校验失效的主要成因，须并批（9.1） |
| 4 | 「无需改动代码，仅改配置」 | 只对替换单个模型成立；新增角色须改代码（6.3） |
| 5 | `spec.md` 从 `.state` 的 `decisions` 渲染 | 不充分。`decisions` 是问答对而非规格；改为三段拼装并标注可信度（4.4） |
| 6 | 仲裁器 5 级 | 扩为 6 级 + N1/N2/N3 补充规则（7.4） |
| 7 | 客观轨严重性随 Route 变化 | **循环依赖**。定案：一律按最严标准（7.4 N3） |
