# A7：攻击者 agent —— 产出可执行反例

> 依赖：**A3**（事实包）、**A5**（动态图并行分支）
> 被依赖：**A9**（仲裁器消费反例判定）、**A11**（变异探针以反例检出率为判据）
> 范围：新增 `sw_lib/workflow/counterexample.py`；`prompts/` 的 adversary 模板；
> `config.yaml` 的 `adversary` 角色
> 不含：Route 决策（属 A9）、设计意见（属 A8）、反例的追加进判据集（属 A9）
>
> 上游依据：`docs/design-reviewer-independence.md` **7.1**、10.2 的 D2
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

### 1.1 为什么需要「攻击者」而不是「评审者」

最初的问题是：reviewer 和 developer 同模型、判据与产出同源，所以复核必然通过。
A3 换掉了审查对象（事实而非自述），A4/A5 换掉了模型（异构 provider）。
但还剩一件事没解决 —— **审查产出的形态本身不可验证**。

「这段代码有问题」是一句断言，无法自动判真假。
**「这段代码在输入 X 时返回 Y，而 spec 要求 Z」是一个可执行的反例。**

A7 把主观审查的一部分转成机器可判的形态：
agent 产出 pytest 测试函数，harness 执行它，退出码决定它是否成立。
模型说了什么不重要，反例跑不跑得出来才重要。

> 这是本套设计里唯一能让「主观判断」产生**硬证据**的环节。
> A8 的设计意见即使有交叉校验，仍需人判断；A7 的反例要么复现，要么不复现。

### 1.2 本任务的范围

| 在范围内 | 不在范围内 |
|---|---|
| 反例文件的契约与存放位置 | Route 决策（**A9**） |
| 反例执行器（harness 执行，非 agent 自验） | 反例修复后追加进判据集（**A9**，A2 已定案归属） |
| 三态判定：成立 / 无效 / 自身损坏 | 设计合理性意见（**A8**） |
| adversary 的 prompt 与工具权限 | 变异探针的对照实验（**A11**） |
| 反例集合的去重与轮次比对 | 非 pytest 形态的反例（第二批） |

### 1.3 边界声明（必须诚实）

**反例不可复现 ≠ 代码正确。**

上游 7.1 已定：「全部不可复现」不等于审查通过（见 A9 的 N1）。
A7 只能证明「找到了一个具体的错」，无法证明「没有错」。

三个具体的无能之处：

1. **反例的想象力受限于 spec**。`spec.md` 的高可信度段落常为空
   （A3 的 2.7 实测：T1 的 `decisions` 全空、ADR 段全是占位符），
   攻击者缺乏「正确行为」的定义，只能攻击代码的内部一致性。
2. **攻击者与 developer 若同 provider，盲区仍重合**。
   A4 的 3.5 提供异构校验，但 A4 的 U4-3 已声明 provider 不等于先验独立。
3. **反例形态限定为 pytest**，意味着只能攻击可被单元测试触及的行为。
   并发、性能、部署配置类问题一概不在。

---

## 2. 现状：已实测的事实

### 2.1 ⚠️ 头号问题：反例文件会污染主测试套件

上游 7.1 规定反例写入 `facts/counterexamples/ce-<n>_test.py`，
即在 `target_dir` **内部**。实测后果：

```text
$ python3 -m pytest -q          # 在 target_dir 跑主套件
FAILED facts/counterexamples/ce-1_test.py::test_ce - AssertionError: 反例
1 failed, 1 passed
```

pytest 默认递归收集，`ce-1_test.py` 符合 `*_test.py` 命名，**会被收集**。
于是「项目测试是否通过」这个判定永久失败。

连带破坏三处：

| 受害者 | 后果 |
|---|---|
| A6 的 O2（测试执行退出码 0） | 永远 fail |
| A2 的 03b 转绿检查（`exit == 0`） | 永远无法转绿 |
| A3 的 `tests.json` | `failed` 计数含反例，事实失真 |

**A2 的 6 节曾定案「反例与 `red_witness` 覆盖的测试目录物理隔离，
因此新增反例不破坏哈希」—— 哈希确实不破坏，但退出码破坏了。**
隔离的是哈希，不是 pytest 的收集范围。

### 2.2 三种候选隔离方案的实测对照

| 方案 | 主套件是否干净 | 代价 |
|---|---|---|
| 反例不用 `test_` 命名 | ✅ | ❌ 反例自己也跑不了（实测 `ce-1.py` 显式指定路径时能跑，但依赖 pytest 的 `python_files` 默认值，脆） |
| 用户仓库加 `pytest.ini` 的 `norecursedirs = facts` | ✅ | ❌ **侵入用户仓库配置**；用户已有 `pytest.ini` 时须合并，且 agent 可改回 |
| 每次跑主套件都加 `--ignore=facts` | ✅ | ❌ 要求所有跑测试的地方都记得加；漏一处即污染（hook、A6、A2、用户手动跑） |
| **反例存于 `target_dir` 之外的 harness 侧目录** | ✅ | 需设 `PYTHONPATH` 才能 import 被测代码（见 2.3） |

**定案：最后一种。** 反例落 `workspace/tasks/<task>/counterexamples/`，
**不在 `target_dir` 内**。

理由：前三种都要求「别的地方记得配合」，而漏配的后果是静默污染。
把反例放在 pytest 永远不会自动走到的地方，是唯一不依赖他人纪律的做法。

> 这与 A3 的 2.5 处置 `.sw-context` 同一思路：
> 让污染源从被观察对象里消失一次，而不是让每个观察者各自记得排除。

### 2.3 外部目录的反例需要 PYTHONPATH，且收集错误会伪装成失败

实测反例放在 `target_dir` 之外时：

```text
# 不设 PYTHONPATH
ERROR .../ce-2_test.py
!!!! Interrupted: 1 error during collection !!!!
exit=2

# PYTHONPATH=src
FAILED .../ce-2_test.py::test_ce - AssertionError: 反例
exit=1
```

若不区分，`exit != 0` 会把「反例自身 import 不到代码」当成「反例成立」——
**一个写坏的反例会伪造出硬证据，直接触发返工**。

### 2.4 退出码可区分三态（判定的基础）

实测四种情形：

| 情形 | 退出码 | 判定 |
|---|---|---|
| 断言失败（真反例） | **1** | `confirmed` —— 反例成立 |
| 反例自身 import 失败 | **2** | `broken` —— 反例损坏，不是证据 |
| 反例自身语法错误 | **2** | `broken` |
| 反例通过（代码其实是对的） | **0** | `invalid` —— 无效反例 |

`0 / 1 / 2` 三值恰好对应上游 7.1 的判定语义，无需解析文本。

⚠️ 但**退出码 2 与 1 的区分不可省**。上游 7.1 只写了「可复现 / 不可复现」
两态，实测表明必须是三态：`broken` 既不是可复现也不是不可复现，
它是**这条反例没有意义**。

### 2.5 `MAX_REROUTE` 已存在

`core/config.py:30` 的 `MAX_REROUTE = 3` 已被 `graph.py:66` / `:106` 与
`runtime.py:13` 消费。A7 只需提供「反例集合是否与上轮相同」的判据，
收敛逻辑属 A9。

---

## 3. 设计

### 3.1 反例的存放与命名

```
workspace/tasks/<task>/counterexamples/
    round-1/
        ce-1_test.py
        ce-2_test.py
        manifest.json        # 本轮反例的元数据与判定结果
    round-2/
        ...
```

三条约束：

1. **不在 `target_dir` 内**（2.2 的定案）。
2. **按轮次分目录**。返工后重新审查产生新一轮，旧轮保留 ——
   A9 的收敛判定需要比对相邻两轮（上游 7.5）。
3. 文件名 `ce-<n>_test.py`，`n` 在轮内递增。

> A2 的 6 节所说的「物理隔离」在此得到更强的实现：
> 不仅与 `red_witness` 的测试目录隔离，而且完全在 `target_dir` 之外，
> 因此对主套件的收集范围也是隔离的（2.1 的缺陷被消除）。

### 3.2 反例契约（对齐上游 7.1，补一个字段）

每条反例由两部分组成：**可执行的 pytest 文件** + **manifest 条目**。

manifest 条目字段：

| 字段 | 约束 |
|---|---|
| `id` | `ce-<n>`，轮内唯一 |
| `target` | 文件与函数，**必须落在 A3 的 `diff.numstat` 文件清单内** |
| `input` | 触发条件的描述 |
| `expected` | 期望行为，**必须引用 `spec.md` 的出处**（段号或引文） |
| `actual` | 实际行为 |
| `repro` | 固定格式的执行命令，由 **harness 生成**，agent 不填（见 3.3） |
| `spec_backed` | **新增**：`expected` 是否有 spec 依据（见下） |

`spec_backed` 是新增字段，理由来自 A3 的 2.7 实测：
`spec.md` 的高可信度段落常为空。此时攻击者的 `expected` 只能来自
它自己对代码的理解 —— **那又回到了自问自答**。

三态取值：

| 值 | 含义 | 对 Route 的影响 |
|---|---|---|
| `spec` | `expected` 引用了 `.context` 或 `decisions`（高可信段） | 强证据 |
| `adr` | 只引用了 ADR 段（A3 标为低可信度） | 弱证据，须标注 |
| `inferred` | 无 spec 依据，攻击者自行推断 | **仅作提示，不单独触发返工**（A9 定级） |

> 这个区分是本任务对「业务真理不可能凭空产生」的诚实回应：
> 没有 spec 的地方，攻击者产出的不是反例，是猜测。
> 把它标出来，而不是让它冒充硬证据。

### 3.3 `repro` 由 harness 生成，agent 不填

上游 7.1 把 `repro` 列为 agent 填写的字段。**本任务改为 harness 生成。**

理由：`repro` 是「怎么复现」的权威描述。若由 agent 填写，
agent 可以写一条与实际执行不同的命令，而 harness 执行的是自己那条 ——
两者不一致时，报告里的复现步骤是假的。

harness 生成的形态（含 2.3 的 PYTHONPATH）：

```bash
cd <target_dir> && PYTHONPATH=<extra> <python> -m pytest \
    <abs_path_to_ce_file>::<test_fn> -q
```

`<extra>` 与 `<python>` 复用 `hooks/lib_run_tests.sh` 已有的
`_pytest_pythonpath()` / `_project_python()` 逻辑 ——
那两个函数的注释记录了 T2/T3 两次「门禁与 agent 结论相反」的事故，
反例执行必须走同一套解析，否则会重演。

### 3.4 执行器：`sw_lib/workflow/counterexample.py`

```python
class CounterexampleVerdict(str, Enum):
    CONFIRMED = "confirmed"   # 退出码 1：反例成立
    INVALID   = "invalid"     # 退出码 0：反例不成立
    BROKEN    = "broken"      # 退出码 2：反例自身损坏
    TIMEOUT   = "timeout"     # 超时

@dataclass
class CounterexampleResult:
    id: str
    verdict: CounterexampleVerdict
    exit_code: int
    stdout_tail: str          # 保留输出，供报告与人工判断
    duration_ms: int
    spec_backed: str          # spec | adr | inferred

def run_round(task: str, round_no: int) -> List[CounterexampleResult]:
    """执行某轮全部反例。由 harness 调用，agent 不参与。"""
```

四条执行纪律：

1. **harness 执行，不由 agent 自验**（上游 7.1）。
   agent 的工具里**没有**执行反例的能力（见 3.6）。
2. 逐条独立执行，不合批。一条 `broken` 不该影响其他条的判定 ——
   合批时 pytest 的收集错误会中断整个会话（2.3 实测 `Interrupted`）。
3. 超时保护。反例可能死循环，需 `timeout` 参数（默认 60s）。
4. 退出码按 2.4 的表映射，**不解析输出文本**做判定。
   输出只保留供人看。

### 3.5 反例集合的去重（供 A9 的收敛判定）

上游 7.5：「同一反例重复出现不计入新信息」。
A7 提供判据，不做决策。

去重键：`(target, normalized_assertion)`。

`normalized_assertion` 的取法：解析反例文件的 AST，
取全部 `assert` 语句的**结构化表示**（去掉字面量的具体值与消息文本）。

> 为什么不用文件内容哈希：agent 换个变量名、改个断言消息，
> 哈希就变了，但反例实质相同。那样「每轮都有新信息」永远成立，
> 收敛判定形同虚设。
>
> 为什么不用 `id`：`id` 是轮内递增的，跨轮必然不同。

`round_diff(task, round_a, round_b) -> SetDiff` 返回新增/消失/重复三个集合，
供 A9 判断循环是否推进。

### 3.6 adversary 的工具权限（对齐上游 6.2 与 A4）

| 工具 | 是否给 | 理由 |
|---|---|---|
| `list_files` / `read_file` | ✅ | 需读代码与事实包 |
| `write_file` | ❌ | 不得改被测代码 |
| `run_command` | ❌ | 上游 6.2：白名单含 `python`/`bash`，等价写权限 |
| **`submit_counterexample`** | ✅ | **新增专用工具**，见下 |
| `ask_user` | ❌ | 攻击者不应打断用户；有疑问就产出 `inferred` 反例 |

`submit_counterexample(id, target, input, expected, actual, spec_ref, code)`：

- 只能写入 `workspace/tasks/<task>/counterexamples/round-<n>/`。
- 路径由 harness 计算，**agent 不传路径** —— 传路径就等于任意写。
- 写入前校验 `target` 在 A3 的 `diff.numstat` 清单内，否则拒绝。
- 写入后**不执行**。执行由 3.4 的 harness 侧执行器负责。

⚠️ **强度声明**（承 A4 的 1.3）：以上是配置层与工具层的声明。
A0 的 2.9.5 实测 opencode 的 `bash` 可绕过路径 deny，
且模型会**自发回退到 bash**。所以「不给 write_file」不等于「无法写文件」。
兜底是 A0 的 HMAC 签名：反例目录的 manifest 哈希纳入证据字段
（与 A3 的 3.6 同机制，落在 `facts` 键下）。

### 3.7 prompt 要点（不含完整文本）

三条硬性要求写进 adversary 的 prompt：

1. **只准提交能执行的 pytest 函数**，不接受散文描述。
2. **`expected` 必须引用 `spec.md` 的具体段落**；无依据时须显式声明
   `spec_ref: null`，harness 会据此标记 `spec_backed: inferred`。
3. **不得修改被测代码**。发现问题只能提交反例。

prompt 中注入的事实：`diff.patch`、`diff.stat`、`spec.md`、`tests.json`
（A3 提供）。**不注入** `plan.md`（那是 A8 的对照物，上游 4.1）、
**不注入** `03-coding.md`（A3 的验收 17）、
**不注入**兄弟分支的产出（A5 的验收 4）。

---

## 4. mock 模式

mock agent 无法产出真实反例。定案：

- mock 下 adversary 分支产出**一条固定的示例反例**（内容来自夹具），
  让 A5 的图结构与 A9 的仲裁链路可测。
- 该反例的 `spec_backed` 固定为 `inferred`，
  且 manifest 标 `mock: true`。
- **执行器照常真实执行它**（它是一个真实的 pytest 文件），
  于是 3.4 的三态映射在 mock 下同样被覆盖。

即 mock 影响「反例从哪来」，不影响「反例怎么判」。

---

## 5. 与其他任务的接口

| 任务 | A7 依赖 / 提供 | 契约 |
|---|---|---|
| **A3** | 消费 `diff.patch` / `diff.numstat` / `spec.md` / `tests.json` | `target` 必须在 `diff.numstat` 清单内（3.6） |
| **A5** | 作为一个 subjective 分支运行 | 分支隔离：不见兄弟产出 |
| **A4** | `adversary` 角色的 tools 声明 | `submit_counterexample` 的注册由本任务实现 |
| **A2** | 反例目录与 `red_witness` 的测试目录隔离 | **本任务加强了该定案**：不仅哈希隔离，且在 `target_dir` 之外（2.1 / 3.1） |
| **A9** | 提供 `CounterexampleResult` 列表与 `round_diff()` | Route 决策与「追加进判据集」均属 A9 |
| **A0** | 反例 manifest 哈希纳入 `facts` 证据键 | 不改 `EVIDENCE_FIELDS`（A3 的 3.6 已定） |
| **A11** | 反例检出率是变异探针的主要观测量 | ⚠️ **A11 的 2.1 修正**：只有**存活变异**（现有测试未捕获的变异）才评估本任务的能力；被测试捕获的变异走客观轨、命中 A9 的优先级 1，不计入检出率 |
| **A12** | `spec_backed: inferred` 的反例进入 `route.unresolved` | A12 把它们转成向用户的提问（承接 U7-2） |

---

## 6. 实施顺序（内部）

1. 反例目录结构与 manifest 数据模型（3.1 / 3.2）。
2. 执行器 `run_round()` 与三态映射（3.4，含 2.4 的退出码表）。
3. `repro` 生成（3.3，复用 `lib_run_tests.sh` 的解析）。
4. `submit_counterexample` 工具 + 路径与 `target` 校验（3.6）。
5. AST 去重与 `round_diff()`（3.5）。
6. `spec_backed` 判定（3.2）。
7. adversary 角色配置与 prompt（3.7）。
8. manifest 哈希写入 `.state` 的 `facts` 键。

> 第 2 步先于第 4 步是刻意的：**执行器可以用手写的反例文件测试**，
> 不必等 agent 能产出。这让本任务的大部分逻辑不依赖 LLM 即可验证。

---

## 7. 验收标准

**机制接通类**：

1. `submit_counterexample` 写入的文件落在
   `workspace/tasks/<task>/counterexamples/round-<n>/`，
   **不在 `target_dir` 内**。
2. agent 传入路径参数时被拒绝（路径由 harness 计算）。
3. `target` 不在 `diff.numstat` 清单内时提交被拒绝。
4. `run_round()` 逐条独立执行，一条 `broken` 不影响其他条的判定。
5. `repro` 命令可被复制粘贴直接执行，且执行结果与执行器一致。
6. 超时的反例判 `TIMEOUT`，不判 `CONFIRMED`。
7. `round_diff()` 对「换了变量名的同一反例」判为重复（3.5）。
8. adversary 的 tools 中无 `write_file` / `run_command`（A4 提供解析）。
9. 全部既有测试通过（**单调性**）。

**有效性类**：

10. **反例不污染主测试套件**（2.1 的回归锚点）：
    提交一条必然失败的反例后，在 `target_dir` 跑 `python3 -m pytest`
    退出码仍为 **0**。

    这是本任务的头号判据。按上游 7.1 的原写法（反例落 `facts/` 内）
    此条必红 —— 实测会 `1 failed, 1 passed`。
11. **三态判定正确**（2.4 的回归锚点）：
    三个手写反例分别产生 `CONFIRMED`（断言失败）、
    `INVALID`（断言通过）、`BROKEN`（import 不到被测模块）。

    特别地：`BROKEN` **不得**被判为 `CONFIRMED`。
    只用 `exit != 0` 判定时此条必红（2.3 实测两者分别为 2 与 1）。
12. **`spec_backed` 区分有效**：`expected` 引用 `.context` 时为 `spec`；
    只引用 ADR 段时为 `adr`；无引用时为 `inferred`。
13. **harness 执行而非 agent 自验**：adversary 的工具集中
    **不存在**任何能执行 pytest 的工具；
    人为给它 `run_command` 时，该配置应被 A4 的校验拒绝
    （若 A4 未就绪，本条记 ❓ 并说明）。
14. **反例 manifest 篡改可检出**：改 manifest 后 `verify_evidence`
    返回 `tampered`（依赖 A0 的 `facts` 键，A3 的 3.6 已定案落点）。
15. **返工两轮的反例集合可比对**：第二轮提交与第一轮实质相同的反例，
    `round_diff()` 的「新增」集合为空。

    这是 A9 收敛判定的数据基础；上游 7.5 的「同一反例重复不计新信息」
    在此变为可测。

> 第 10 与第 11 条是核心。
> 10 保证 A7 不破坏其他任务的判定，11 保证反例不会伪造硬证据。

---

## 8. 风险与遗留

| # | 风险 | 处置 |
|---|---|---|
| R1 | 反例在 `target_dir` 之外，import 被测代码需 PYTHONPATH（2.3） | 复用 `lib_run_tests.sh` 的 `_pytest_pythonpath()` / `_project_python()`。解析失败时判 `BROKEN` 而非 `CONFIRMED` |
| R2 | 攻击者产出大量 `inferred` 反例刷存在感 | `spec_backed: inferred` 的反例按 A9 的定级**不单独触发返工**。另记入质量档案（上游 7.1） |
| R3 | 反例可能有副作用（写文件、起进程） | 执行时 cwd 设为临时副本？**本批不做** —— 成本高且会破坏 import 路径。记为已知风险：反例代码由异构模型产出，仍是不可信输入 |
| R4 | AST 去重对「实质相同但结构不同」的反例无效 | 承认局限。去重是**降低**误判为新信息的概率，不是消除。A9 的 `MAX_REROUTE` 是最终兜底 |
| R5 | `submit_counterexample` 的路径由 harness 算，但 agent 仍可用 `bash` 写任意位置 | **不解决**（3.6 强度声明）。检出依赖 A0 的签名 |
| R6 | 反例目录随轮次增长，无清理策略 | 归档时随任务一并保留（A10）。不自动删除 —— 反例是审查历史的一部分 |

**明确遗留、不假装解决的问题**：

- **U7-1**：反例不可复现不等于代码正确（1.3）。A7 只提供正向证据。
- **U7-2**：无 spec 时攻击者只能攻击内部一致性（1.3 第 1 条）。
  `spec_backed: inferred` 把这件事标出来，但**不能解决它** ——
  解决需要 R8 的可执行验收场景。

  > ⚠️ **回填：本条已获得部分出口**。A9 的 3.8 定案把 `inferred` 反例
  > 收集进 `route.unresolved`（而非丢弃），**A12 负责把它们转成向用户的提问**。
  > A12 给出的不是「解决」—— 业务真理仍不能凭空产生 ——
  > 而是**把问题转给唯一能回答的人**，并在无人应答时如实报告
  > 「这些点由 AI 自行推断，正确性不受任何机制保障」（A12 的 3.5）。
  > 因此本条从「完全无出口」降级为「有出口但依赖用户参与」。
- **U7-3**：pytest 形态覆盖不到并发、性能、部署类问题（1.3 第 3 条）。
  上游 7.1 已把其余形态推至第二批。
- **U7-4**：反例代码是不可信输入且被真实执行（R3）。
  当前依赖「异构模型不会恶意」这个假设，**没有沙箱**。
  真正的隔离需要容器（`docker.enabled`），与 A0 的 R5 同属独立任务。

---

## 9. 本任务的红绿要点

### 9.1 自指风险

A7 实现「用可执行测试验证审查结论」。自指风险有两层：

**第一层**：A7 自己的测试也是 pytest 测试。
测试反例执行器时，**被测的反例文件与测试文件都在 pytest 的世界里**。
若不隔离，会出现「测试执行器的测试」被执行器当成反例执行的荒谬情形。

纪律：执行器的测试必须把手写反例放进 `tmp_path`，
且断言主套件（`tests/unit`）的收集数不受影响。

**第二层，更重要**：本任务的判据「退出码 1 = 反例成立」
**假设了 pytest 的退出码语义**。若该假设错了，
整个三态判定连同它的测试会一起错，且自证正确。

纪律：三态映射的测试必须**用真实 pytest 进程**验证（不 mock `subprocess`），
且退出码期望值来自本文档 2.4 的实测表 —— 那张表是在真实 pytest 上量出来的。

### 9.2 环境自伤风险

1. 反例执行会在 `target_dir` 跑 pytest。测试必须用 `tmp_path` 造假仓库，
   **绝不指向真实 `repo/` 或 harness 自身**。
2. 一条死循环的反例若无超时会挂住测试进程。
   `TIMEOUT` 的测试须用一个真的死循环反例 + 短超时（如 2s）验证。
3. 测试后断言 `tests/unit` 全量收集数未变 ——
   反例文件若漏在仓库里会被永久收集。

### 9.3 红的正确形态

| 验收项 | 红的正确形态 | 假绿风险 |
|---|---|---|
| **10 不污染主套件** | 把反例放进 `target_dir/facts/counterexamples/`（上游原写法）时，断言「主套件退出码为 0」应红（实测 `1 failed, 1 passed`） | 测试里跑主套件时带了 `--ignore=facts` —— 那样两种存放位置都能过，缺陷被掩盖。**必须用不带任何排除参数的裸 `pytest`** |
| **11 BROKEN 不判 CONFIRMED** | 只用 `exit != 0` 判定时，import 失败的反例会被判 `CONFIRMED`，断言 `BROKEN` 为红 | 构造 `BROKEN` 样本时用了「语法错误」而非「import 失败」。两者退出码都是 2，但**import 失败更隐蔽**且是 2.3 的真实场景（外部目录 + 无 PYTHONPATH）。两种都要测 |
| 5 repro 可复现 | 若 `repro` 漏了 PYTHONPATH，人工执行会 `exit=2` 而执行器 `exit=1`，断言「两者一致」为红 | 只断言 `repro` 字符串非空，或只断言它含 `pytest` |
| 6 超时判 TIMEOUT | 用真死循环 + 短超时。实现若无超时保护，测试**挂住**而非红 | 用 `time.sleep(999)` 之外的假死循环；或超时设得比测试框架的超时还长，于是整个测试会话被 kill，看起来像基础设施故障 |
| 7 去重识别改名 | 提交两条「变量名不同、断言结构相同」的反例，断言判重复。用内容哈希实现时为红 | 两条反例写得完全一样 —— 那内容哈希也能判重复，测不出 AST 去重的价值 |
| 12 spec_backed 三态 | 三个样本分别引用 `.context` / ADR / 无引用 | 只测 `spec` 与 `inferred` 两态，漏掉 `adr`。而 `adr` 恰是最容易被误当强证据的一态（A3 的 2.7：ADR 段常是占位符） |
| 13 agent 不能自验 | 断言 adversary 的 tools 中无可执行工具 | 断言「tools 不含 `run_command`」但漏了别的执行途径（如某个新增工具内部调了 subprocess）。**应断言工具集是白名单式的显式集合** |
| 14 manifest 篡改检出 | 落点错误（未写在 `facts` 键下）时应红 | 只验 A7 自己算的哈希比对，跳过 `verify_evidence` —— 与 A3 的同名陷阱同源 |

### 9.4 特别提醒：第 10 项的测试极易被自己写的排除参数掩盖

写这条测试时最自然的手法是：

```python
run_git(...)  # 造仓库
submit_counterexample(...)
out = run(["python3","-m","pytest","-q","--ignore=facts"], cwd=target)
assert out.returncode == 0        # <-- 加了 --ignore，两种实现都绿
```

`--ignore=facts` 一加，上游原写法（反例落 `facts/` 内）也能通过，
2.1 那个头号缺陷被永久隐藏。

**测试必须用裸 `python3 -m pytest`，不带任何排除参数**，
并在注释里写明这是刻意的。

### 9.5 单调性

反例目录若曾被放进 `target_dir`（早期实现），迁移到新位置后
既有任务的 `facts/counterexamples/` 需清理。
按 DEV-PROTOCOL 第 5 节，**记录但不顺手删用户数据** ——
提供一个迁移脚本，由用户决定是否执行。

---

## 10. 回滚

| 层 | 回滚方式 |
|---|---|
| 反例目录 | 纯新增，删除即回到现状 |
| `submit_counterexample` 工具 | 从 adversary 的 tools 移除 |
| 执行器 | 独立模块，不被调用即失效 |
| `.state` 的 `facts` 子字段 | 纯新增子字段 |
| adversary 角色 | 从 `harness.review.subjective` 移除，A5 的分支数自动减一 |

> 回滚 A7 会让主观审查退回「只有意见、没有硬证据」的状态。
> A8 的意见即使有交叉校验仍需人判断，
> **A7 是唯一产出机器可判证据的一轨** —— 回滚它应作为决策记录。
