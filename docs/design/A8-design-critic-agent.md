# A8：设计审视者 agent 与规则交叉校验

> 依赖：**A3**（事实包，含仅此一轨可见的 `plan.md`）、**A5**（动态图并行分支）
> 被依赖：**A9**（仲裁器消费设计意见）、**A11**（变异探针）
> 范围：新增 `sw_lib/workflow/design_review.py`；`prompts/` 的 design_critic 模板；
> `config.yaml` 的 `design_critic` 角色
> 不含：Route 决策（属 A9）、反例（属 A7）
>
> 上游依据：`docs/design-reviewer-independence.md` **3.3 / 7.2 / 7.3**
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

### 1.1 为什么设计审查值得单独一轨

上游 3.3 有一个反直觉但重要的判断：

> 设计审查恰好是 **C3（判据缺席）影响最小**的场景 ——
> 「嵌套深度」「单次使用的抽象」这类判断**不需要业务真理**，只需结构感。

这解释了 A8 在整套设计里的位置。你最初指出「业务真理不可能凭空产生，
AI 无法自联想业务真理」—— 这对功能正确性成立，
但**结构质量的判据部分内生于代码本身**：
一个只被调用一次的抽象、一个四层嵌套的分支，
不需要知道业务是什么就能判定它可疑。

所以 A8 是主观轨里最适合交给 LLM 的部分，
也是唯一能在 spec 贫瘠时仍然产出有效判断的一轨。

### 1.2 但它的失败模式必须被治理

上游 3.3 的失败模式表：

| | 攻击者（A7） | 设计审视者（A8） |
|---|---|---|
| 失败模式 | 构造不出反例（**漏检**） | **空泛评论、风格偏好当缺陷** |
| 可验证性 | 可 —— harness 执行 | **不可 —— 需规则辅助** |

「建议优化这段代码」「这里可以更清晰」是零信息量的产出，
但看起来像在工作。A8 的核心机制（上游 7.3 的交叉校验）
就是把意见里**可量化的部分**变成机器可核对的，
让空泛评论无法通过。

### 1.3 本任务的范围

| 在范围内 | 不在范围内 |
|---|---|
| 设计意见的结构化契约（7.2 的六字段） | Route 决策（**A9**） |
| 四条规则的交叉校验实现（7.3） | 反例（**A7**） |
| `location` 的 diff 范围校验 | 意见的自然语言质量评价 |
| 意见驳回与质量档案 | 非 Python 语言的 AST 校验（见 2.3） |
| design_critic 的纯只读工具权限 | 强制只读的实施强度（属 A0） |

### 1.4 边界声明（必须诚实）

**交叉校验只能覆盖带可量化 evidence 的规则。**

上游 7.3 的固有边界声明照样成立并加强：

> 纯结构判断（如「业务逻辑塞进了 IO 层」）无法机械核对，
> 仍需 `location` 加 `suggestion` 供人判断。

A8 的做法是把意见分成两类并**分别标记**，而不是假装全部可校验：

| 类 | 例 | 处置 |
|---|---|---|
| 可校验 | 「嵌套深度 4」「该抽象仅 1 处调用」 | 机器核对，不通过则驳回 |
| 不可校验 | 「业务逻辑塞进了 IO 层」 | 标 `verifiable: false`，进人工判断，**不驳回也不当硬证据** |

第二类的存在是诚实的代价：它们可能是最有价值的意见，也可能是空话，
而机器分不出来。**A9 对这两类的定级必须不同。**

---

## 2. 现状：已实测的事实

### 2.1 四条规则中三条的 id 真实存在，一条是新造的

上游 7.2 规定 `rule` **只能取既有集合**，不允许自创。核实结果：

| rule id | 出处 | 规则原文中的可量化部分 |
|---|---|---|
| `hook-03-03` | `hooks/03-coding.md:13` | **「Max 3 nested if-else」** —— 明确阈值 |
| `hook-04-01` | `hooks/04-review.md:3` | 「严格检查是否动了无关代码」 |
| `hook-04-02` | `hooks/04-review.md:8` | **「检查是否有单次使用的多余抽象」** —— 明确计数 |
| `plan-drift` | ❌ **不存在于 hooks/** | 上游新造的 id |

前三条的措辞对交叉校验很有利：`hook-03-03` 直接给出阈值 3，
`hook-04-02` 直接给出「单次使用」这个可计数的判据。
**校验实现应引用规则原文的阈值，而不是自定阈值** ——
否则规则与校验会各说一套。

`plan-drift` 需在实施时明确：要么在 `hooks/04-review.md` 中补一条正式规则，
要么改用既有的 `hook-04-05`（Deliverable Consistency，
措辞含「code, test, doc, config in sync」）。
**本文档定案：新增 `hook-04-09: Plan Drift` 到 `hooks/04-review.md`**，
理由是「与 plan 不一致」和「交付物内部不一致」是两件事，
复用会让规则语义含混。

### 2.2 ⚠️ 交叉校验自身会产生假阳性：跨文件调用计数

`hook-04-02` 的校验方式是「静态计数调用点」。实测一个具体的错误：

```text
sample.py 定义 helper()，自身调用 1 次
other.py  另外调用 2 次

只解析 sample.py -> helper 被调 1 次
                 -> 校验「单次使用的多余抽象」为**通过**
实际全仓 3 次     -> 该意见根本不成立
```

若校验范围只覆盖 diff 内的文件，一个有三处调用的函数会被
「校验通过」地判定为多余抽象。**交叉校验本该驳回错误意见，
此时反而为错误意见背书** —— 比不校验更糟，因为它带上了机器权威。

**定案**：`hook-04-02` 的调用计数范围为**整个 `target_dir`**，
而非仅 diff 涉及的文件。

代价是慢（要解析全仓 Python 文件）。缓解：只在有该规则的意见时才执行，
并缓存单轮结果。

> 这条与 A3 的 2.1（diff 漏未跟踪文件）同类：
> **范围取窄了会让判定悄悄变错**，而错的方向恰好是「看起来成立」。

### 2.3 ⚠️ AST 校验只覆盖 Python

实测 `ast.parse()` 对 TypeScript 文件抛 `SyntaxError`。

于是 `hook-03-03`（嵌套深度）与 `hook-04-02`（调用计数）
**对非 Python 文件无法校验**。

**定案**：三态，不得二态化（与 DEV-PROTOCOL 第 2 节同构）：

| 校验结果 | 含义 | 处置 |
|---|---|---|
| `verified` | 校验执行且 evidence 与实测一致 | 意见进入仲裁 |
| `refuted` | 校验执行且 evidence 与实测**矛盾** | **驳回**，计入质量档案 |
| `unverifiable` | 无法校验（非 Python、解析失败、规则不可量化） | 进入仲裁但**标记未校验**，A9 定级低于 `verified` |

⚠️ `unverifiable` **不等于 `verified`**。上游 7.3 只写了「校验不通过的意见被驳回」，
是二态表述。实测表明必须三态 —— 否则非 Python 项目的全部设计意见
会因「校验没报错」而被当成已核实。

### 2.4 嵌套深度与调用计数的 AST 实现可行

实测（Python 3.12）：

```text
def deep(a,b,c,d): 四层嵌套 if   -> 计算得 4     ✅ 超过 hook-03-03 的阈值 3
def helper()                     -> 被调用 1 次   ✅ 可计数
```

两条可量化规则的机械核对成立。实现要点：
嵌套深度按 `ast.If` 的链式层数计，不含 `elif`（`elif` 在 AST 里是嵌套的
`If`，需按 `orelse` 特判，否则 `if/elif/elif` 会被算成三层嵌套 ——
而 `hook-03-03` 的原文是「Max 3 nested if-else」，
`elif` 链在语义上是平铺的分支，不是嵌套）。

> 这个特判必须写进实现，否则一个正常的 `if/elif/elif/else` 会被误报。
> 那会让 design_critic 产出大量假阳性，机制很快被当噪音关掉。

### 2.5 `location` 的范围校验可行

实测用 A3 的 `diff.numstat`（文件清单）加 `diff.patch`（行号范围）
可以三态判定：

```text
sample.py:3   -> 通过（文件在 diff 内，行号在变更范围内）
sample.py:99  -> 驳回（行号不在变更范围内）
nofile.py:1   -> 驳回（文件不在 diff 内）
```

上游 7.2 要求 `location` **必须落在 diff 范围内**。
本任务把它实现为**提交时的硬校验**，不是事后统计。

### 2.6 两个 agent 互不可见（上游 3.3 末）

> 两个 agent 互不可见对方产出，否则先跑完的一方会锚定另一方 ——
> 那是 C1 在主观轨内部的复现。

这条由 A5 的验收 4 保证（各分支 prompt 不含兄弟分支产出）。
A8 的职责是**不主动去读** A7 的反例目录 ——
`design_critic` 的工具权限中不含对 `counterexamples/` 的读取路径。

---

## 3. 设计

### 3.1 设计意见的契约（对齐上游 7.2，补两个字段）

```jsonc
{
  "id": "op-1",
  "location": {"file": "src/a.py", "line": 42},   // 必须在 diff 范围内
  "rule": "hook-04-02",                            // 只能取既有集合
  "evidence": {
    "kind": "call_count",                          // 新增：evidence 的类型
    "claim": 1,                                    // 新增：可量化的断言值
    "text": "该抽象仅 1 处调用"
  },
  "severity": "med",
  "scope": "coding",
  "suggestion": "内联到唯一调用点",
  // 以下由 harness 填写，agent 不填
  "verification": {
    "status": "refuted",                           // verified | refuted | unverifiable
    "measured": 3,                                 // 实测值
    "detail": "全 target_dir 计数 3 次（sample.py 1 + other.py 2）"
  }
}
```

两个新增字段的理由：

**`evidence.kind` + `evidence.claim`**：上游 7.2 的 `evidence` 是自由文本
（如「嵌套深度 4」）。要机械核对必须从文本里抽出数字 ——
那是脆的（「深度约 4 层」「4 级嵌套」）。
改为**结构化声明**：agent 必须给出 `kind` 与数值 `claim`，
`text` 只作人类可读补充。

**`verification` 由 harness 填写**：与 A7 的 `repro` 同理（A7 的 3.3）——
校验结论若由 agent 自填，就是自己给自己打分。

### 3.2 `evidence.kind` 的封闭集合

| kind | 对应 rule | 校验方式 | 覆盖语言 |
|---|---|---|---|
| `nesting_depth` | `hook-03-03` | AST 计算 `if` 链深度（含 2.4 的 `elif` 特判） | Python |
| `call_count` | `hook-04-02` | **全 `target_dir`** 静态计数（2.2 的定案） | Python |
| `unrelated_file` | `hook-04-01` | 与 A3 的 `diff.numstat` 清单比对 | 全部 |
| `plan_absent` | `hook-04-09` | 与 A3 的 `plan.md` 比对 | 全部 |
| `structural` | 任意 | **不可校验**，标 `unverifiable`（1.4 第二类） | 全部 |

`structural` 是给「业务逻辑塞进 IO 层」这类意见的合法出口。
**agent 必须显式选它**，不能把结构性意见伪装成 `call_count` 之类
来骗取 `verified` —— 那样实测值与 claim 不符会被 `refuted`，
反而暴露。

### 3.3 校验器：`sw_lib/workflow/design_review.py`

```python
class VerificationStatus(str, Enum):
    VERIFIED     = "verified"
    REFUTED      = "refuted"
    UNVERIFIABLE = "unverifiable"

@dataclass
class OpinionVerification:
    status: VerificationStatus
    measured: Optional[Union[int, str]]
    detail: str

def verify_opinion(task: str, opinion: dict) -> OpinionVerification:
    """单条意见的交叉校验。harness 调用，agent 不参与。"""

def verify_round(task: str, round_no: int) -> List[dict]:
    """校验一轮全部意见，回填 verification 字段。"""
```

四条纪律：

1. **范围校验先行**：`location` 不在 diff 范围内即 `refuted`，
   不再做 evidence 校验（2.5）。
2. `rule` 不在封闭集合内即 `refuted`（上游 7.2「不允许自创」）。
3. `evidence.kind` 与 `rule` 不匹配即 `refuted`
   （防止用 `structural` 蒙混可校验的规则，或反之）。
4. AST 解析失败 → `unverifiable`，**不是 `refuted`** ——
   解析失败是我方能力不足，不是意见错误。

第 4 条的区分很重要：把「我们校验不了」记成「意见被驳回」
会让非 Python 项目的全部设计意见凭空消失。

### 3.4 `hook-04-09: Plan Drift` 的新增（对应 2.1）

在 `hooks/04-review.md` 末尾新增（编号已核实可用：现有 hook 到 `hook-04-08` 为止）：

```markdown
## hook-04-09: Plan Drift (计划漂移)
- **When**: during
- **Rule**: diff 中的模块/职责必须能在 02-planning 的 Task DAG 中找到对应项。
  新增未在 plan 中声明的模块视为漂移。
- **Check**: 与 facts/plan.md 的模块清单比对
```

`plan_absent` 的校验：从 `facts/plan.md` 提取模块/文件清单，
比对意见中指出的模块是否确实缺席。

⚠️ A3 的 2.7 已实测：`02-planning.md` 同样可能是**未填写的模板**。
此时 `plan.md` 为空态，`plan_absent` 的校验须判 `unverifiable`
（无对照物），**不得因为「plan 里没有」就判意见成立** ——
那会让所有新增模块都被判漂移。

### 3.5 质量档案

上游 7.3：「校验不通过的意见被驳回，但计入 `design_critic` 的质量档案」。

```jsonc
// workspace/tasks/<task>/design_opinions/round-<n>/manifest.json
{
  "round": 1,
  "role": "design_critic",
  "model": "gemini-2.0-flash",
  "counts": {"verified": 2, "refuted": 3, "unverifiable": 4},
  "opinions": [ /* 含 verification 的完整条目 */ ]
}
```

`refuted` 占比高说明该模型在此任务上产出低质意见。
**本批只记录，不做自动降权** —— 降权策略需要跨任务的统计基础，
且过早自动化会掩盖「校验器自己有 bug」这种可能（2.2 就是一例）。

存放位置与 A7 的反例同理，在 `target_dir` 之外
（`workspace/tasks/<task>/design_opinions/`）——
虽然 `.json` 不会被 pytest 收集，但保持一致的隔离原则更省心。

### 3.6 design_critic 的工具权限（纯只读）

| 工具 | 是否给 |
|---|---|
| `list_files` / `read_file` | ✅ |
| `write_file` / `run_command` / `ask_user` | ❌ |
| `submit_opinion` | ✅ 专用工具 |

`submit_opinion(id, file, line, rule, evidence_kind, claim, evidence_text, severity, scope, suggestion)`：

- 路径由 harness 计算，agent 不传（与 A7 的 3.6 同）。
- 提交时立即做 3.3 的第 1-3 条校验，**不通过则当场拒绝并返回原因**，
  让 agent 有机会修正而不是产出一堆 `refuted`。
- `verification` 字段由 harness 在 `verify_round()` 时填写。

⚠️ **强度声明**（承 A4 的 1.3、A7 的 3.6）：
「纯只读」是配置层声明。A0 的 2.9.5 实测 opencode 的 `bash` 可绕过，
且模型会自发回退到 bash。真正的兜底是 A0 的签名。

### 3.7 prompt 要点

注入的事实（A3 提供）：`diff.patch`、`diff.stat`、`spec.md`、
**`plan.md`**（仅此一轨可见，上游 4.1）。

**不注入**：`03-coding.md`（A3 的验收 17）、A7 的反例目录（2.6）、
`tests.json` 的失败详情（那是 A7/A6 的领域，避免设计轨去追测试问题）。

三条硬性要求：

1. 每条意见必须给出 `evidence_kind` 与 `claim`（结构化，3.1）。
2. 无法量化的意见必须选 `structural`，**不得伪装成可校验类型**。
3. `location` 必须在 diff 范围内 —— 提交时会被拒（3.6）。

---

## 4. mock 模式

mock 下 design_critic 产出**三条固定意见**（夹具提供），
分别覆盖 `verified` / `refuted` / `unverifiable` 三态，
让 A9 的仲裁分级逻辑可测。

校验器**照常真实执行** —— 它是纯程序逻辑，不依赖 LLM。
即 mock 影响「意见从哪来」，不影响「意见怎么校验」（与 A7 第 4 节同构）。

---

## 5. 与其他任务的接口

| 任务 | A8 依赖 / 提供 | 契约 |
|---|---|---|
| **A3** | `diff.patch` / `diff.numstat` / `spec.md` / **`plan.md`** | `plan.md` 仅此一轨可见；`plan.md` 为空态时 `plan_absent` 判 `unverifiable`（3.4） |
| **A5** | 作为一个 subjective 分支 | 不见 A7 的产出（2.6） |
| **A4** | `design_critic` 纯只读声明 | `submit_opinion` 的注册由本任务实现 |
| **A9** | 提供带 `verification` 的意见列表 | **三态定级不同**：`verified` 可作证据，`unverifiable` 仅作提示，`refuted` 不进仲裁。**A9 的 3.2 已按此实现**（优先级 3/4 只认 `verified`） |
| **A0** | 意见 manifest 哈希纳入 `facts` 证据键 | 不改 `EVIDENCE_FIELDS` |
| **A11** | 设计缺陷检出率是变异探针的观测量之一 | A11 的 M3（五层真嵌套）与 M6（多余抽象）针对本任务；**M8 是反向探针** —— 提交数值造假的意见，本任务的交叉校验必须判 `refuted`（A11 的验收 13）。⚠️ A11 的 2.4 提醒：构造 M3 须用**真嵌套**而非 `elif` 链，否则测到的是本文档 2.4 那个 bug |
| **A12** | `unverifiable` 的意见进入 `route.unresolved` | A12 把它们转成向用户的提问 |
| **hooks** | 新增 `hook-04-09`（3.4） | 规则 id 集合随之扩为四条 |

---

## 6. 实施顺序（内部）

1. 意见数据模型与 `evidence.kind` 封闭集合（3.1 / 3.2）。
2. `location` 范围校验（2.5，最简单且能立即拦掉大量无效意见）。
3. AST 校验器：`nesting_depth`（含 2.4 的 `elif` 特判）。
4. AST 校验器：`call_count`（**全 `target_dir` 范围**，2.2 的定案）。
5. `unrelated_file` / `plan_absent` 的清单比对。
6. `unverifiable` 三态与非 Python 处置（2.3）。
7. `hook-04-09` 新增（3.4）。
8. `submit_opinion` 工具 + 提交时校验（3.6）。
9. 质量档案 manifest 与哈希写入（3.5）。

> 第 2-6 步全部是**纯程序逻辑，不依赖 LLM**，可用手写意见样本完整测试。
> 这与 A7 的实施顺序同一思路：把不依赖模型的部分先做实。

---

## 7. 验收标准

**机制接通类**：

1. `submit_opinion` 写入 `workspace/tasks/<task>/design_opinions/round-<n>/`。
2. `rule` 不在四条封闭集合内时提交被拒。
3. `location` 的文件不在 `diff.numstat` 清单内时提交被拒。
4. `location` 的行号不在 `diff.patch` 变更范围内时提交被拒。
5. `evidence.kind` 与 `rule` 不匹配时判 `refuted`。
6. `verify_round()` 回填的 `verification` 字段含 `status` / `measured` / `detail`。
7. design_critic 的 tools 无 `write_file` / `run_command` / `ask_user`。
8. `hook-04-09` 已存在于 `hooks/04-review.md`。
9. 全部既有测试通过（**单调性**）。

**有效性类**：

10. **`call_count` 的范围必须是全 `target_dir`**（2.2 的回归锚点）：
    构造 `sample.py` 定义并调用 `helper()` 一次、`other.py` 再调两次，
    且**只有 `sample.py` 在 diff 内**。
    agent 声称「仅 1 处调用」时必须判 **`refuted`**（实测 3 次）。

    只解析 diff 内文件的实现会判 `verified` —— 那是为错误意见背书。
    **这是本任务的头号判据。**
11. **`elif` 链不被误算为嵌套**（2.4 的回归锚点）：
    `if/elif/elif/else` 的嵌套深度应为 **1**，不是 3。
    不做特判的实现会误报，让正常代码被判违反 `hook-03-03`。
12. **`unverifiable` 不等于 `verified`**（2.3 的回归锚点）：
    对 `.ts` 文件提交 `nesting_depth` 意见时判 `unverifiable`，
    **不得判 `verified`**，也不得判 `refuted`。
13. **解析失败判 `unverifiable` 而非 `refuted`**：
    构造一个语法错误的 Python 文件，意见判 `unverifiable`。
    对应 3.3 第 4 条 —— 我方能力不足不该算意见错误。
14. **`plan.md` 为空态时 `plan_absent` 判 `unverifiable`**（3.4）：
    `02-planning.md` 全为占位符时（A3 的 2.7 实测形态），
    不得因「plan 里没有」就判所有新增模块漂移。
15. **`structural` 意见不被驳回也不当硬证据**：
    提交一条 `structural` 意见，`status` 为 `unverifiable`，
    且它出现在交给 A9 的列表里（不被过滤掉）。
16. **agent 不能自填 `verification`**：提交时传入 `verification` 字段应被忽略或拒绝。
17. **不可见 A7 的产出**：design_critic 的 prompt 中不含
    `counterexamples/` 下的任何内容（2.6）。
18. **意见 manifest 篡改可检出**：改 manifest 后 `verify_evidence` 报 `tampered`。

> 第 10 与第 12 条是核心。
> 10 防止交叉校验为错误意见背书（比不校验更糟），
> 12 防止「校验没报错」被当成「已核实」。

---

## 8. 风险与遗留

| # | 风险 | 处置 |
|---|---|---|
| R1 | 全 `target_dir` 的 AST 解析慢 | 只在存在 `call_count` 意见时执行，单轮缓存（2.2） |
| R2 | 大量 `unverifiable` 让机制显得无用 | 这是**真实状况的反映**（非 Python 项目、结构性意见）。A9 对其定级低但不丢弃。**不得为了提高 `verified` 占比而放宽校验** |
| R3 | `evidence.claim` 要求结构化，模型可能填不对格式 | 提交时立即校验并返回原因（3.6），给 agent 修正机会。格式错误不计入质量档案的 `refuted` |
| R4 | `hook-04-09` 是新增规则，与既有 hook 的编号体系耦合 | 追加在末尾，不改动既有编号。若将来 hooks 重构需同步 |
| R5 | 质量档案可能被用来自动降权某模型，而校验器自己有 bug | **本批不做自动降权**（3.5）。2.2 就是校验器出错的实例 |
| R6 | `nesting_depth` 的语义（是否含 `for`/`while`/`with`） | 按 `hook-03-03` 原文「nested if-else」**只计 `if`**。若将来扩展需同步改规则文本，不得只改实现 |

**明确遗留、不假装解决的问题**：

- **U8-1**：不可校验的结构性意见（1.4 第二类）无法机械判真假。
  这是上游 7.3 已声明的固有边界，A8 只做标记。
- **U8-2**：AST 校验只覆盖 Python（2.3）。其他语言需各自的解析器，
  不在本批。TypeScript 可考虑 `tree-sitter`，但引入新依赖属独立决策。
- **U8-3**：`verified` 只说明「evidence 的数值属实」，
  **不说明「这是个真问题」**。嵌套深度确实是 4，
  但那段代码可能正是最清晰的写法。
  **交叉校验治理的是空泛，不是品味。**
- **U8-4**：design_critic 与 developer 若同 provider，
  「倾向认可自己会写出的结构」（上游 3.3 理由 2）依然成立。
  依赖 A4 的异构校验，而 A4 的 U4-3 已声明 provider 不等于先验独立。

---

## 9. 本任务的红绿要点

### 9.1 自指风险

A8 实现「检查意见是否成立」的校验器。自指风险：

> **校验器自身的正确性由谁校验？**
> 2.2 那个缺陷（范围取窄导致为错误意见背书）
> 正是校验器出错的实例 —— 而它出错的方向是**让意见看起来已核实**。

纪律：

1. 每条 `evidence.kind` 的校验必须有**至少一个 `refuted` 样本**，
   不能只测 `verified` 通路。只测通过路径的校验器等于没校验。
2. `refuted` 样本的期望值必须**手工算出并写死**
   （如 2.2 的「实际 3 次」），不得调用被测函数生成期望。
3. 校验器的测试用**真实 AST 解析**，不 mock `ast.parse`。

### 9.2 环境自伤风险

1. 全 `target_dir` 的 AST 扫描若误指向 harness 自身，
   会解析上千个文件并可能触发意外行为。测试必须用 `tmp_path` 造小仓库。
2. 新增 `hook-04-09` 会修改 `hooks/04-review.md` —— 那是**真实的用户文件**。
   须确认修改只是追加，且既有七条规则的文本一字不动。
3. 测试后断言 `hooks/` 下文件的既有内容未被改动。

### 9.3 红的正确形态

| 验收项 | 红的正确形态 | 假绿风险 |
|---|---|---|
| **10 call_count 全仓范围** | 只解析 diff 内文件的实现会判 `verified`，断言 `refuted` 为红 | **测试里把 `other.py` 也放进 diff 清单** —— 那样两种实现都能算出 3 次，缺陷被掩盖。必须让 `other.py` **不在** diff 内 |
| **11 elif 不算嵌套** | 不做特判的实现算出 3，断言深度为 1 为红 | 样本只用 `if/else` 而无 `elif` —— 那样两种实现结果相同，特判逻辑未被覆盖 |
| **12 unverifiable ≠ verified** | 二态实现（只有通过/驳回）会把 `.ts` 判成其中之一，断言第三态为红 | 断言「不是 `verified`」—— 若实现判成 `refuted` 也满足，而那会让非 Python 意见凭空消失 |
| 13 解析失败判 unverifiable | 实现若 `except: return REFUTED` 则红 | 断言「抛异常被捕获」而不检查 status 取值 |
| 14 plan 空态 | `plan.md` 为占位符时，实现若判 `verified`（「plan 里确实没有」）则红 | 测试用**真的有内容**的 `plan.md` —— 那样空态分支从未被走到。必须用 A3 的 2.7 实测形态（全 `___`） |
| 3/4 location 范围 | 未实现范围校验时提交成功，断言「被拒」为红 | 只测「文件不在清单」，漏测「行号超范围」。后者更隐蔽 —— 文件对了但指向了未改动的行 |
| 15 structural 不被过滤 | 实现若把 `unverifiable` 过滤掉，断言「出现在列表里」为红 | 只断言 status 正确，不断言它进入了交给 A9 的列表 |
| 16 agent 不填 verification | 实现直接信任 agent 传入的字段时为红 | 测试不传 `verification` —— 于是覆盖不到该路径 |

### 9.4 特别提醒：第 10 项的测试构造必须让调用者在 diff 之外

这条是本任务唯一「实现错了反而更像对的」的判据。

正确构造：

```
diff.numstat 清单 = ["sample.py"]          # 只有 sample.py 在 diff 内
sample.py: def helper(); helper()           # 1 次
other.py:  helper(); helper()               # 2 次，且 other.py 不在 diff 内
agent 意见: call_count claim=1
期望: refuted, measured=3
```

若测试把 `other.py` 也写进 diff 清单，
「只解析 diff 内文件」的错误实现同样算出 3 次，测试变绿。
**`other.py` 不在 diff 内是这条测试的全部意义。**

### 9.5 单调性

新增 `hook-04-09` 后，既有测试若断言 `04-review.md` 的规则条数或全文哈希会红。
按 DEV-PROTOCOL 第 3 节第 3 条：那是**真实变更**，更新断言。

**不得为了让测试通过而不新增规则** —— 那会让 `plan-drift` 继续引用
一个不存在的 id（2.1）。

---

## 10. 回滚

| 层 | 回滚方式 |
|---|---|
| 意见目录与 manifest | 纯新增，删除即回到现状 |
| `submit_opinion` 工具 | 从 design_critic 的 tools 移除 |
| 校验器 | 独立模块，不被调用即失效 |
| `hook-04-09` | 从 `hooks/04-review.md` 删除该节（只删新增部分） |
| design_critic 角色 | 从 `harness.review.subjective` 移除 |

> 回滚 A8 会让设计审查退回「空泛评论无法被拦截」的状态。
> 上游 1.2 记录的原始问题之一正是「reviewer 产出风格偏好当缺陷」，
> **回滚意味着该问题复原**，应作为决策记录。
