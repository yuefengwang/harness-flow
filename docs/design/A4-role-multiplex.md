# A4：配置层多角色支持 —— 一阶段多 agent

> 依赖：无（波 1，可与 A0 / A1 并行）
> 被依赖：**A5**（动态图，直接消费本任务的解析能力）、A7、A8
> 范围：`sw_lib/core/config.py`、`config/config.yaml`、`sw_lib/core/bootstrap.py`
> 不含：图编排与并行执行（属 **A5**）、审查者的 prompt 与判定（属 A7 / A8）
>
> 上游依据：`docs/design-reviewer-independence.md` 6.1 / 6.2 / **6.3**
>
> 🔒 实施须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。末尾第 9 节为本任务特有的假绿风险。

---

## 1. 目标与定位

### 1.1 要解决的问题

当前体系是**一阶段一角色的单射**。`config.yaml` 的 `stage_roles`
把每个 stage 映射到唯一一个 role，五处解析函数都按这个假设写。

于是「配两个 reviewer，各用不同模型」这件事**做不到** ——
而它正是 README 第 5 节里 **C2 先验相关**的解法：

> 同权重模型盲区重合。developer 与 reviewer 用同一个模型时，
> 测试编码了和实现完全相同的误解，所以它必然绿。

A4 提供的是**配置与解析层的能力**：让「N 个角色、各自的模型、各自的工具权限」
可以被声明并解析出来。真正的并行执行由 A5 完成。

### 1.2 本任务的范围

| 在范围内 | 不在范围内 |
|---|---|
| `stage_roles` 支持多角色声明 | LangGraph 的 fan-out / fan-in（**A5**） |
| 解析函数增加 `role_id` 维度 | 并行结果的 reducer（**A5**） |
| `factory` 签名增加 `role_id` | 各审查者的 prompt 组装（A7 / A8） |
| 角色/模型配置错误的显式校验 | 模型是否真实存在的在线探测 |
| 异构性校验（多角色不得同 provider） | 工具权限的强制实施（见 1.3） |
| 向后兼容：单角色配置行为不变 | — |

### 1.3 边界声明（必须诚实）

**A4 只解析工具权限，不强制实施。**

上游 6.2 要求「`design_critic` 纯只读」。A4 能让 `tools` 列表按角色解析出来，
但**能否真的阻止写入**取决于 agent 侧：

- A0 的 2.5 已定性：`Toolbox` 的白名单只被 `agents/gemini.py` 引用，
  对当前实际运行的 `opencode` **一概不生效**。
- A0 的 2.9.5 实测：opencode 的 permission 规则能拦住 `write`，
  但 **`bash` 可绕过，且模型会自发回退到 bash**。

所以「纯只读」在本批是**约定**而非保证。
A4 的职责是把它准确地传达到 `opencode.py:181` 的规则生成处，
真正的强度由 A0 的机制决定。**本文档不假装解决。**

---

## 2. 现状：已实测的事实

以下全部真实执行过。

### 2.1 ⚠️ 直接改成列表会让三个解析函数全部崩溃

上游 6.3 提议 `stage_roles: 04-review: [adversary, design_critic]`。
实测把配置值换成列表后：

```text
resolve_agent_type     -> TypeError: unhashable type: 'list'
resolve_agent_model    -> TypeError: unhashable type: 'list'
get_tools_for_stage    -> TypeError: unhashable type: 'list'
```

原因：`stage_roles: Dict[str, str]`（`config.py:65`），
五处 `cfg.stage_roles.get(stage)` 的返回值被直接当作 `cfg.roles` 的键去查
（`config.py:156`、`:176`、`:197`、`:222`）。列表不可哈希。

这证实了 6.3 的判断「新增第二个审查者**必须改代码**」，
也说明**不能靠改 YAML 试探** —— 会直接抛异常。

### 2.2 ⚠️ 修正上游 6.3 的一处位置引用

6.3 的表格写 `tools/toolbox.py:254` 是 `get_tools_for_stage(stage)`。
实测该函数**不在 toolbox.py**，而在 **`sw_lib/core/config.py:219`**。
`toolbox.py` 只是导入方（`:18`）与使用方（`:366`）。

改造落点因此不同：**改 `config.py` 一处即可覆盖两个消费者**：

| 消费者 | 位置 | 用途 |
|---|---|---|
| `Toolbox.__init__` | `toolbox.py:366` | gemini 的工具白名单（A0 的 2.5：对 opencode 无效） |
| **`OpencodeAgent`** | **`opencode.py:181`** | **生成 permission 规则 —— 当前唯一真实生效的路径** |

第二行是重点：上游 6.3 的表格里没有 `opencode.py:181`，
但那才是工具权限真正落地的地方（A0 的 2.9.5 实测证明 deny 规则有效）。
**A4 若只改 `Toolbox` 相关路径，对实际运行的 agent 毫无作用。**

### 2.3 ⚠️ 最严重的现存缺陷：配置写错时静默兜底

实测把 `stage_roles` 指向一个不存在的角色：

```text
stage_roles: {04-review: nonexistent_role}

resolve_agent_type   -> "gemini"                                  (兜底)
resolve_agent_model  -> "gemini-2.0-flash"                        (兜底)
get_tools_for_stage  -> ['list_files','read_file','ask_user']      (兜底)
```

**三个函数全部静默返回兜底值，没有任何警告。**

这对多角色 reviewer 是致命的。A4 的全部目的是「用不同 provider 消除
先验盲区」，而一个拼写错误就会让某个 reviewer 静默退化成默认 gemini ——
**配置声称异构，实际同构，且无人知晓**。

同类问题在模型名上更隐蔽：

```text
roles.reviewer.model = "opencode/typo-model-xxx"
resolve_agent_model -> "opencode/typo-model-xxx"   (原样返回)
```

模型名不存在也照样返回，直到真实会话才失败 ——
而那时错误信息是 provider 的报错，与「配置写错了」相去甚远。

**A4 必须把静默兜底改成显式失败或显式警告。**
这一条比多角色支持本身更重要：没有它，多角色能力可以在无人察觉的情况下失效。

### 2.4 配置层完全没有 provider 概念

实测 `config.py` 源码中不含 `provider` 一词。
`RoleConfig` 只有 `agent` / `model` / `tools` / `description`。

于是「两个 reviewer 必须不同 provider」（上游 6.1 的模型要求）
**无法被表达，更无法被校验**。

当前 `config.yaml` 五个角色的 `agent` 全是 `opencode`，
`model` 全是 `opencode/mimo-v2.5-free` —— 这正是最初讨论里
「判据和产出出自同一个隐状态」的配置证据。

### 2.5 部分解析能力已存在，可复用

`resolve_agent_type` / `resolve_agent_model` 的第 1 条分支
（`config.py:142`、`:193`）已支持 `agent_override` 传**角色名**：

```python
if agent_override and agent_override in cfg.roles:
    return cfg.roles[agent_override].agent
```

即「给定 role_id 解析出 agent/model」的能力已经有了，
缺的是「给定 stage 解析出**多个** role_id」。

**这降低了改造量**：新增 `resolve_stage_roles(stage) -> List[str]`，
其余函数增加可选 `role_id` 参数走既有分支即可。

### 2.6 `factory` 签名确认

`bootstrap.py:92` 的 `factory(stage=stage, task_name=None)` 内部调用
`resolve_agent_type(stage, "")` / `resolve_agent_model(stage, "")` ——
第二参数传空串，所以走的是「按 stage 解析」路径。

A5 要求签名变为 `factory(stage, task_name, role_id=None)`（A5 的 4.3）。
改造后 `role_id` 非空时传给 `agent_override` 即可复用 2.5 的分支。

---

## 3. 设计

### 3.1 ⚠️ 先消除一个跨文档歧义：配置形态以 A5 为准

上游 6.3 与已定稿的 A5 提出了**两套不同的配置结构**：

| 来源 | 形态 |
|---|---|
| 上游 6.3 | `stage_roles: {04-review: [adversary, design_critic]}` |
| **A5 的第 1 节** | `harness.review.subjective: [{role, model, kind}, ...]` |

两者不能都实现 —— 那会产生两个真相来源。

**定案：以 A5 的 `harness.review` 为主，`stage_roles` 保持向后兼容。**

理由三条：

1. A5 已按 `harness.review.subjective` 写完图编排与 `Send` 派发细节
   （A5 的 4.2），改它的成本更高且会让已定稿文档失效。
2. `harness.review` 能表达 `kind`（`counterexample` / `design_review`）
   与 `objective.enabled`，`stage_roles` 的列表形态表达不了。
3. `stage_roles` 承载的是「五个阶段各自的默认角色」，
   04 的多审查者是一个**局部的、结构更丰富的**需求，
   不该把列表语义强塞进通用映射里。

于是 `stage_roles: {04-review: reviewer}` **保持不变**，
它继续为「单角色回落」与其余四个阶段服务。

### 3.2 配置结构

```yaml
harness:
  # 其余四阶段照旧
  stage_roles:
    01-brainstorming: analyst
    02-planning: architect
    03-coding: developer
    04-review: reviewer          # 保留：subjective 为空时的回落
    05-archive: archivist

  review:
    objective:
      enabled: true              # 客观轨：纯程序，无 LLM（A6）
    subjective:
      - role: adversary
        model: opencode/mimo-v2.5-free
        kind: counterexample
      - role: design_critic
        model: gemini-2.0-flash
        kind: design_review
    require_heterogeneous: true  # 见 3.5
```

新增数据模型：

```python
@dataclass
class SubjectiveReviewer:
    role: str                    # 必须存在于 roles 中
    model: str                   # 覆盖 roles[role].model
    kind: str                    # counterexample | design_review
    tools: Optional[List[str]] = None   # 缺省用 roles[role].tools

@dataclass
class ReviewConfig:
    objective_enabled: bool = True
    subjective: List[SubjectiveReviewer] = field(default_factory=list)
    require_heterogeneous: bool = True
```

### 3.3 新增解析接口

```python
def resolve_stage_roles(stage: str) -> List[str]:
    """一个 stage 的全部角色 id。

    04-review 且 review.subjective 非空时返回其中的 role 列表；
    否则返回 stage_roles 的单元素列表（向后兼容）。
    """

def resolve_review_config() -> ReviewConfig:
    """解析 harness.review。供 A5 的图构建消费。"""

def get_tools_for_role(role_id: str, stage: str) -> List[str]:
    """按角色解析工具权限。role_id 为 None 时回落到 get_tools_for_stage。"""
```

既有三个函数**增加可选 `role_id` 参数**，走 2.5 已有的角色名分支：

```python
def resolve_agent_type(stage, agent_override=None, role_id=None) -> str
def resolve_agent_model(stage, agent_override=None, role_id=None) -> str
def get_tools_for_stage(stage, role_id=None) -> List[str]
```

`role_id=None` 时行为与现在**逐字节相同** —— 这是向后兼容的判据。

### 3.4 修正静默兜底（对应 2.3，本任务的核心）

新增校验函数，在配置加载时执行：

```python
def validate_config() -> List[ConfigIssue]:
    """返回配置问题清单。空列表表示无问题。"""
```

三态处置，**不得二态化**（与 DEV-PROTOCOL 第 2 节同构）：

| 问题 | 严重性 | 处置 |
|---|---|---|
| `stage_roles` 指向不存在的角色 | **error** | 抛 `ConfigError`，拒绝启动 |
| `review.subjective[].role` 不存在于 `roles` | **error** | 抛 `ConfigError` |
| `review.subjective[].kind` 不是已知值 | **error** | 抛 `ConfigError` |
| `require_heterogeneous` 为真但多角色同 provider | **error** | 抛 `ConfigError`（见 3.5） |
| 模型名格式可疑（无 `/` 且非已知别名） | warn | 记警告，继续运行 |
| `subjective` 为空且 `objective.enabled` 为假 | **error** | 04 阶段将无任何审查者 |

为什么「指向不存在的角色」定为 error 而非 warn：
2.3 实测它会静默退化成 gemini。而 A4 的目的正是保证异构 ——
一个拼写错误就让机制失效且无人知晓，**这种失败必须响亮**。

> 兜底本身不是错的设计，错的是**兜底而不告知**。
> 改造后仍保留兜底值（避免既有流程崩），但同时抛出/记录问题，
> 由调用方决定是否继续。

### 3.5 异构性校验（对应 2.4）

`RoleConfig` 新增 `provider` 派生属性：从 `model` 字符串取 `/` 前的部分，
无 `/` 时取 `agent` 字段。

```python
@property
def provider(self) -> str:
    if "/" in self.model:
        return self.model.split("/", 1)[0]
    return self.agent
```

`require_heterogeneous: true` 时校验：

1. 全部 `subjective` 角色的 `provider` 互不相同。
2. 每个 `subjective` 角色的 `provider` 都与 **developer 角色**不同
   （C2 的原始诉求是「审查者与作者盲区不重合」）。

违反即 error。

> 为什么允许关掉（`require_heterogeneous: false`）：
> 用户可能只有一个 provider 的额度。
> 但**关掉时必须在 A10 的达成度报告里标注「审查者同构」** ——
> 让降级可见，而不是静默接受。这一条需 A10 配合，记入接口约定。

### 3.6 工具权限的传导路径（对应 2.2）

按角色解析出的 `tools` 必须到达**两个**消费者：

```
get_tools_for_role(role_id, stage)
        │
        ├──> toolbox.py:366   Toolbox.allowed_tool_names   (gemini 用)
        │
        └──> opencode.py:181  permission 规则生成           (当前真实生效)
```

第二条是必须的（2.2）。实施时 `opencode.py:181` 的
`get_tools_for_stage(self.stage)` 改为按当前 agent 的 `role_id` 解析。

`OpencodeAgent` 需要知道自己的 `role_id` —— 由 `factory` 传入（3.7）。

### 3.7 `factory` 签名改造（对齐 A5 的 4.3）

```python
def factory(stage=stage, task_name=None, role_id=None):
    agent_type = resolve_agent_type(stage, "", role_id=role_id)
    model = resolve_agent_model(stage, "", role_id=role_id)
    ...
    return AgentFactory.create(agent_type, callbacks, task_name or "",
                               stage, stage_idx, model, role_id=role_id)
```

`AgentFactory.create` 与各 agent 构造函数增加 `role_id=None` 参数，
供 3.6 的权限解析使用。缺省 `None` 时行为不变。

### 3.8 收紧 reviewer 的工具（对应上游 6.2）

现有 `reviewer` 的 tools 含 `run_command`（`config.yaml:82`）。
上游 6.2 核实其白名单含 `python` / `sh` / `bash` / `sed` —— 等价写权限。

配置层处置：

| 角色 | tools |
|---|---|
| `design_critic` | `list_files` / `read_file`（**无 `run_command`**） |
| `adversary` | `list_files` / `read_file` + 反例提交专用工具 |

反例提交工具的实现属 **A7**，A4 只保证它能按角色声明并解析出来。

⚠️ **强度声明**：如 1.3 所述，这是配置层的声明。
A0 的 2.9.5 实测 `bash` 可绕过路径 deny，
所以「无 `run_command`」不等于「无法执行命令」——
opencode 侧是否给了 `bash` 工具由 A0 的规则表决定。

---

## 4. mock 模式

`mock_agent.enabled` 为真时，多角色应照常解析出 N 个角色，
但全部由 mock agent 承担。理由：A5 的图结构测试需要「N 个分支」这一形状，
与分支内跑的是真实 LLM 还是 mock 无关。

异构性校验在 mock 下**跳过**（mock 只有一个 provider），
但须在结果中标注 `heterogeneous: "mock_skipped"`，不记为通过。

---

## 5. 与其他任务的接口

| 任务 | A4 提供 | 契约 |
|---|---|---|
| **A5** | `resolve_review_config()`、`factory(..., role_id=)` | 配置形态以 A5 的 `harness.review` 为准（3.1） |
| **A7** | `adversary` 角色的 tools 声明 | 反例提交工具的注册由 A7 实现 |
| **A8** | `design_critic` 角色的纯只读声明 | 强度由 A0 的规则决定（1.3） |
| **A10** | `require_heterogeneous: false` 时的降级标记 | 报告须标注「审查者同构」（3.5） |
| **A0** | 需 `opencode.py:181` 按 role_id 解析工具 | A0 的规则生成逻辑已在 `_tool_switches()`，A4 只改入参来源 |

---

## 6. 实施顺序（内部）

1. `validate_config()` + `ConfigError`（**先做**：2.3 的静默兜底是现存缺陷，
   独立于多角色能力，且能立即暴露既有配置问题）。
2. `RoleConfig.provider` 派生属性 + 异构性校验。
3. `ReviewConfig` / `SubjectiveReviewer` 数据模型 + `resolve_review_config()`。
4. `resolve_stage_roles()`。
5. 三个既有函数增加 `role_id` 参数（保持 `None` 时逐字节等价）。
6. `factory` 与 `AgentFactory.create` 签名扩展。
7. `opencode.py:181` 改为按 role_id 解析（2.2 的真实生效路径）。
8. `config.yaml` 加 `harness.review` 段与两个新角色。

---

## 7. 验收标准

**机制接通类**（只验证接线，不足以宣布完成）：

1. `resolve_stage_roles("04-review")` 在 `subjective` 配两项时返回两个 role_id。
2. `subjective` 为空时 `resolve_stage_roles("04-review")` 返回
   `["reviewer"]`（回落到 `stage_roles`）。
3. `resolve_review_config()` 正确解析 `objective.enabled` 与 `kind`。
4. `resolve_agent_model(stage, "", role_id="design_critic")` 返回该角色的模型，
   与 `reviewer` 的模型不同。
5. `get_tools_for_role("design_critic", "04-review")` **不含** `run_command`。
6. `factory(stage, task, role_id="adversary")` 创建的 agent 的 model
   等于 adversary 的配置模型。
7. `RoleConfig.provider` 对 `opencode/mimo-v2.5-free` 返回 `"opencode"`，
   对无 `/` 的模型名回落到 `agent` 字段。
8. 全部既有测试通过（**单调性**）。

**有效性类**（唯一能证明「真的解决了」的判据）：

9. **`role_id=None` 时逐字节等价**（向后兼容的硬判据）：
   对全部五个 stage，改造前后 `resolve_agent_type` / `resolve_agent_model` /
   `get_tools_for_stage` 的返回值完全相同。

   实现方式：改造前先把五个 stage 的解析结果快照写入测试，
   改造后断言一致。**不是「跑一遍没报错」，而是逐值比对。**
10. **配置写错不再静默兜底**（2.3 的回归锚点）：
    `stage_roles` 指向不存在的角色时抛 `ConfigError`，
    **而非返回 `"gemini"`**。

    这条直接对应 2.3 实测。改造前它返回兜底值，改造后必须失败。
11. **同构配置被拒绝**（3.5）：两个 `subjective` 角色配同一 provider 且
    `require_heterogeneous: true` 时抛 `ConfigError`。
12. **审查者与 developer 同 provider 也被拒绝**：这是 C2 的原始诉求 ——
    只校验审查者之间互不相同是不够的，
    审查者与**作者**盲区重合同样致命。
13. **工具权限到达 opencode 侧**（2.2 的核心判据）：
    配 `design_critic` 无 `run_command` 后，
    `OpencodeAgent` 生成的 permission 规则中该工具为 deny。

    只验证 `get_tools_for_role()` 的返回值**不够** ——
    A0 的 2.5 已证明 `Toolbox` 那条路径对 opencode 无效。
    必须断言规则表本身。
14. **增删审查者不改 Python**：这是 A5 的验收 2，但配置层的部分由 A4 承担 ——
    `subjective` 从两项改三项后，`resolve_stage_roles` 返回三个，
    `resolve_review_config().subjective` 长度为 3，全程无代码改动。

> 第 9 与第 10 条是本任务的核心。
> 9 保证不破坏既有行为，10 保证新能力不会静默失效。
> 第 13 条防止「改了个对 opencode 无效的地方」这类白做。

---

## 8. 风险与遗留

| # | 风险 | 处置 |
|---|---|---|
| R1 | `validate_config()` 上线会让**当前配置**报错（若有历史笔误） | 这是**真实检出**，不是回归。先跑一次校验，把发现的问题列出来逐个修，**不得放宽校验让它过** |
| R2 | 异构性校验会让当前配置直接失败 —— 五个角色全是 opencode | 预期行为。`require_heterogeneous` 默认值定为 **`false`**，用户显式开启；但 A10 报告须标注同构（3.5）。**默认 false 是为了不阻塞既有流程，不是因为同构可接受** |
| R3 | `provider` 从 model 字符串推断不可靠（如自建网关代理多家模型） | 记为已知限制。允许 `RoleConfig` 显式声明 `provider` 覆盖推断值 |
| R4 | 五个函数增加参数，调用点多 | `role_id` 全部为**可选关键字参数**，既有调用点零改动。验收 9 逐值比对守住 |
| R5 | `opencode.py:181` 的改造需要 agent 实例知道自己的 role_id | 由 `factory` 传入（3.7）。若某条创建路径未传，回落到 stage 解析 —— 但**须记录警告**，否则又是静默降级 |
| R6 | 多角色与 `worktree_dir` / docker 隔离的关系未厘清 | 不在本批。A4 只解析配置，不涉及执行隔离 |

**明确遗留、不假装解决的问题**：

- **U4-1**：工具权限只是声明，强度由 A0 决定（1.3、3.8）。
  「纯只读」在 `bash` 可用时不成立。
- **U4-2**：模型是否真实存在无法在配置层校验。
  格式可疑只记 warn（3.4），真实性要到会话才知道。
  **不引入启动时的在线探测** —— 那会让离线开发不可用。
- **U4-3**：`provider` 不等于「先验独立」。
  同一家的两个模型（如 gemini-flash 与 gemini-pro）provider 相同会被拒；
  而不同家的模型若共享训练数据，盲区仍可能重合。
  **provider 校验是廉价代理指标，不是真正的独立性证明。**
  真正的证明只能来自 A11 的变异探针。

  > ⚠️ **回填：A11 承接本条的证明，但证明所需的对照数据由 B0 抢救**。
  > 对照实验需要「改造前」的基线，而**改造一旦落地，旧行为无法重现**。
  > 因该时序与 A11 本体相反，已拆出独立任务
  > **[`B0-baseline-capture.md`](B0-baseline-capture.md)，时序为波 0** ——
  > 必须在 A6-A9 实施**之前**执行。
  > B0 的归档会记录 `config_snapshot`，即本文档 2.4 实测的
  > 「五角色全 `opencode/mimo-v2.5-free`」这个同构状态。
  >
  > ⚠️ **B0 的 1.3 实测：基线已部分污染** —— A0 与 A1 已实施，
  > 严格的改造前时点已错过。但审查侧（A6-A9）完全未动，
  > 故对「审查能力」这一观测量仍有效。
  > **U4-3 的证明结论须表述为「审查侧改造带来的提升」**，
  > 不能表述为「全套设计带来的提升」。
  > **若 B0 被跳过，U4-3 将永久无法证明。**

---

## 9. 本任务的红绿要点

### 9.1 自指风险

A4 自身自指风险低，但有一个**测试污染风险**：

> 本任务的测试要反复修改 `_manager.config` 的内存状态。
> `ConfigManager` 是**模块级全局单例**（`config.py:131`），
> 一个用例改了配置不还原，后续用例全部受影响。

实测证据：撰写本文档时用探针改了 `stage_roles["04-review"]` 与
`roles["reviewer"].model`，同一进程内后续读取全部拿到被污染的值。

强制纪律：

1. 每个改配置的用例用 `monkeypatch` 或 `autouse` 夹具**还原全局配置**。
2. 夹具须还原 `roles` 的**嵌套对象**，不只是顶层 dict ——
   `roles["reviewer"].model = x` 改的是 `RoleConfig` 实例的字段，
   浅拷贝还原不了。用 `copy.deepcopy` 快照。
3. 测试后断言磁盘 `config/config.yaml` **未被改动**。

> 第 3 条不是多余：A0 的 2.9.3 记录过测试污染生产密钥的事故。
> 配置文件同理 —— 一旦被写坏，用户的五个角色都会错。

### 9.2 红的正确形态

| 验收项 | 红的正确形态 | 假绿风险 |
|---|---|---|
| **9 逐字节等价** | 改造中若不小心改了默认分支的顺序，快照比对应红 | **快照在改造后才生成** —— 那样比的是自己和自己，永远绿。快照必须在改造**之前**采集并写死进测试文件 |
| **10 不再静默兜底** | 改造前 `resolve_agent_type` 返回 `"gemini"`，断言「抛 `ConfigError`」为红 | 断言「返回值不是 gemini」—— 若实现改成返回 `None` 也能过，而 `None` 会在下游炸得更难查 |
| **13 权限到达 opencode** | 只改 `Toolbox` 路径时，断言「permission 规则中 `run_command` 为 deny」应红 | 只断言 `get_tools_for_role()` 的返回值。A0 的 2.5 已证明那条路径对 opencode 无效，测了等于没测 |
| 11 同构被拒 | 断言抛 `ConfigError`。实现漏了校验则不抛，为红 | 用两个**不同 provider** 的角色构造测试 —— 那样天然不违反，断言永远不触发 |
| 12 与 developer 同 provider | 构造「两审查者互不相同，但其中一个与 developer 相同」的配置 | 只测审查者之间的互异性，漏掉与 developer 的对比。**这是最容易漏的一条**，因为它需要三个角色参与 |
| 2 空 subjective 回落 | 断言返回 `["reviewer"]`。实现若返回 `[]` 则 04 阶段无审查者 | 断言「长度 <= 1」—— 空列表也满足 |
| 5 design_critic 无 run_command | 断言 `"run_command" not in tools` | 断言 `tools == ["list_files","read_file"]` 过于死板，加一个合法工具就红。但**反向的宽松断言更危险**：只断言「含 read_file」 |
| 14 增删不改代码 | 改配置后断言长度为 3 | 测试里**用 Python 构造** `ReviewConfig` 而不是从 YAML 加载 —— 那样绕过了「只改 YAML」这个核心判据。必须写临时 YAML 文件并真实加载 |

### 9.3 特别提醒：第 9 项的快照必须先采集

这是本任务唯一**无法事后补救**的一条。

正确顺序：

```
1. 改造前运行探针，把五个 stage × 三个函数 = 15 个返回值打印出来
2. 把这 15 个值手工写死进测试文件
3. 确认该测试在改造前是绿的（此时它证明快照采集正确）
4. 改造
5. 再跑，仍绿 => 向后兼容成立
```

第 3 步容易被跳过。若快照采集本身有误（比如写错了某个模型名），
改造后测试红，人会以为是改造破坏了兼容性，然后去改实现 ——
方向完全错了。**先确认快照测试在改造前为绿。**

**本文档已代为采集**（撰写时于未污染进程内实测，可直接写进测试文件）：

```json
{
  "01-brainstorming": {"type": "opencode", "model": "opencode/mimo-v2.5-free",
    "tools": ["list_files", "read_file", "write_file", "ask_user"]},
  "02-planning":      {"type": "opencode", "model": "opencode/mimo-v2.5-free",
    "tools": ["list_files", "read_file", "write_file", "ask_user"]},
  "03-coding":        {"type": "opencode", "model": "opencode/mimo-v2.5-free",
    "tools": ["list_files", "read_file", "write_file", "run_command", "ask_user"]},
  "04-review":        {"type": "opencode", "model": "opencode/mimo-v2.5-free",
    "tools": ["list_files", "read_file", "run_command", "ask_user"]},
  "05-archive":       {"type": "opencode", "model": "opencode/mimo-v2.5-free",
    "tools": ["list_files", "read_file", "write_file", "ask_user"]}
}
```

⚠️ 该快照对应**撰写时的 `config.yaml`**。若实施前配置已变更，
须重新采集 —— 但仍必须在改造**之前**采集。

> 注意这条与 DEV-PROTOCOL 第 1 节的红绿顺序**看似矛盾**：
> 这里要求测试在改造前就是绿的。
> 不矛盾 —— 它是一条**回归防护测试**而非功能测试，
> 其"红"的形态是「改造破坏了兼容性」，只在出错时才该红。
> 功能测试（验收 1-7、10-14）仍严格走红绿六步。

### 9.4 单调性

`validate_config()` 上线会让**当前 `config.yaml` 直接报错**（R1）。
按 DEV-PROTOCOL 第 3 节第 3 条，须逐个分辨：

- 真有笔误 → 修配置。
- 异构性校验导致（五角色全 opencode）→ 这是 R2，
  默认 `require_heterogeneous: false` 让它不阻塞，
  **但不得因此把校验逻辑删掉**。

**不得为了让既有配置通过而放宽校验规则。**
若发现校验过严，须显式记录理由后调整，而非静默降低标准。

---

## 10. 回滚

| 层 | 回滚方式 |
|---|---|
| `harness.review` 配置段 | 删除即回落到 `stage_roles` 单角色（3.3 的回落逻辑） |
| 三个函数的 `role_id` 参数 | 可选参数，不传即旧行为。回滚只需删参数 |
| `validate_config()` | 单独函数，不调用即回到静默兜底 —— **但那意味着 2.3 的缺陷复原，须显式记录** |
| `opencode.py:181` 的改造 | 单行回滚到 `get_tools_for_stage(self.stage)` |
| `factory` 签名 | 可选参数，A5 未上线时无人传 |

> `validate_config()` 那一行的意思是：回滚它会让「配置写错静默退化成
> 同构 reviewer」这件事复原。那不是中性的技术回退，
> 而是**放弃 C2 的解法**，应作为决策记录。
