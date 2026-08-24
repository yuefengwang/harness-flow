# A2：测试冻结与 Red 见证

> 依赖：**A0**（`.state` 完整性 + 证据可检出）
> 被依赖：A3（事实包）、A6（客观轨 O4）、A7 / A8（审查者的判据来源）、
> A10（达成度报告）、A11（变异探针）
> 范围：`hooks/`（新增 pre/post 脚本）、`sw_lib/workflow/stage_state.py`、
> 新增 `sw_lib/workflow/red_witness.py`、`templates/03-coding.md`
> 依据：`docs/self-verification-gaps.md` 的 R1、F3、F4
>
> 🔒 **开发纪律**：实施本任务须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。
> ⚠️ **本任务自指最重**：它实现的正是「强制红绿流程」的机制，
> 而实现它时**没有任何前置机制可依赖**，只能靠执行者手动遵守协议第 1 节。
> 详见第 10 节。

---

## 1. 目标与定位

**原则**：红不是 agent 声称的状态，是 **harness 亲自观测并留痕的事件**。

### 1.1 它解决的是最根本的成因

`design-reviewer-independence.md` 的 1.3 把自校验失效拆为三个成因。
本任务针对 **C3（判据缺席）** —— 三者中唯一无法靠换模型缓解的那个。

| 成因 | 本任务是否解决 |
|---|---|
| C1 上下文污染 | 否（A3 负责） |
| C2 先验相关 | 否（A4/A5 负责） |
| **C3 判据缺席** | **是，主要部分** |

机制上，测试在实现之前被冻结，于是它**独立于 developer 与 reviewer 双方**。
04 阶段的审查者面对的不再是「请判断这段代码对不对」，而是
「这份冻结的测试 + 它的执行结果」—— **任务从「评价」变成「核对」，
而核对不需要拥有业务真理。**

> 这就是为什么 A2 必须早于 A7 / A8。缺了它，审查者拿到的仍只有
> 「代码 + 散文 spec」，只能再猜一次。

### 1.2 顺带解决的两个具体缺陷

- **F3**：`hook-03-02` 写了 "Red → Green required" 但无任何实现，
  `check_03-coding.sh` 中没有对应代码，红灯是否亮过全凭 agent 自述。
- **F4**：测试文件可被随意改写。「改测试让它过」是最常见的花架子手法，
  当前无任何拦截。

---

## 2. 关键实测结论（本设计的可行性基础）

pytest 的退出码天然区分五种情形，**这使「红必须是断言失败」可以被精确判定**：

| 情形 | 退出码 | 判定 |
|---|---|---|
| 断言失败 | **1** | ✅ **有效的红** |
| ImportError / SyntaxError（collection error） | **2** | ❌ 无效的红（造红） |
| 收集到 0 个测试 | **5** | ❌ 无效（无测试） |
| 全部 skip | **0** | ❌ 无效（假绿） |
| 全部通过 | 0 | 绿 |

失败节点 id 与断言原文可用 `-rf` 精确获取：

```
FAILED test_y.py::test_fail_a - assert 1 == 2
FAILED test_y.py::test_fail_b - assert [] == [1]
```

> 这两条实测决定了 R1 第 4 点（防造红）**不需要脆弱的文本解析** ——
> 退出码 1 与 2 的区分已经足够。这是本设计最重要的可行性依据。

---

## 3. 03 阶段拆分

### 3.1 两个动作

| 子阶段 | 允许写 | 禁止写 | 准出条件 |
|---|---|---|---|
| **03a-test** | 测试文件 | 实现文件 | 见证到有效的红（退出码 1） |
| **03b-impl** | 实现文件 | **测试文件** | 全部转绿 + 测试文件哈希未变 |

### 3.2 不引入新的 STAGES 条目

`STAGES`（`core/config.py:28`）是五阶段常量，被 TUI、Web、状态机多处依赖。
**不修改它** —— 03a / 03b 作为 03-coding 内部的**子状态**，
记录在 `.state` 的 `red_witness.phase` 字段。

理由：改 `STAGES` 会波及 `stage_idx` 语义、`entry_router`（`graph.py`）、
进度显示等，风险远超收益。子状态方案的改动面限于 03 阶段自身。

### 3.3 写入约束的实施位置

子阶段的写权限差异**不能只靠 prompt 声明**，需要实际拦截。
两层：

1. **软层**：prompt 中明确当前子阶段与禁止事项。
2. **硬层**：按子阶段下发 opencode 的 `write` / `edit` deny 规则 ——
   03a 拒绝写非测试文件，03b 拒绝写测试文件。
   判定依据是路径（`test_*.py` / `*_test.py` / `tests/` 下）。

> ⚠️ **本节原写「`WriteFileTool` 增加子阶段感知」，已更正。**
> 依据 A0 的 2.5（实测）：`config.yaml` 五个角色全是 `opencode`，
> 而 `Toolbox` 只被 `agents/gemini.py:18` 引用 —— 改 `WriteFileTool`
> 对当前实际运行的 agent 完全无效。硬层的落点是
> `opencode.py:169` 的 `_tool_switches()`，见 A0 的 D0-6。
>
> 硬层依赖 A0 的第三层。且即便 A0 就绪，`bash` 工具**无法按路径约束**
> （A0 的 U0-1），agent 仍可绕过写文件 ——
> 因此哈希校验（3.4）不是「A0 未就绪时的临时防线」，
> 而是**长期的主防线**：它发生在准出时，不依赖写入被拦住。
>
> **实施状态**：硬层已落在 `opencode.py` 的 `_substage_write_rules()`
> （由 `_permission_rules()` 追加下发），按 `red_witness.phase` 分流：
> 03a 先 deny `*.py` 再 allow 测试路径（顺序刻意 —— `findLast` 后者优先，
> 写反会把测试文件一起挡掉导致 03a 死锁），03b 反过来 deny 测试路径。
> `phase == none` 时**返回空列表**，规则表与引入子阶段之前逐条一致 ——
> 否则「见证机制只是不观测」会变成「见证机制悄悄改了 agent 的写权限」。
> 有效性沿用 D0-6 的定性：**❓ 未验证**（A0 的 2.7.2，assert 端点一律
> 返回 allow，真实判定在工具执行路径，需真实 LLM 会话触发 write）。
> 契约见 `tests/unit/agents/test_opencode_substage_write_rules.py` ——
> 其中按 opencode 的真实正则语义（`*` 编译为带 `s` 标志的 `.*`，跨斜杠）
> 求值，而不是照字面猜 glob。

### 3.4 哈希冻结

03a 准出时记录每个测试文件的 `sha256`。03b 准出时重新计算并比对：

- 哈希一致 → 通过。
- 哈希不一致 → **拒绝**，并明确提示哪个文件被改动。

**允许的例外**：若 developer 在 03b 发现测试写错了，必须显式回退到 03a
重做（重新见证红），而不是静默修改。这对应 `DEV-PROTOCOL.md` 第 1.2 节。

---

## 4. 数据结构

`.state` 新增 `red_witness` 子树。**只能由 harness 写**（依赖 A0）：

| 字段 | 含义 |
|---|---|
| `phase` | `03a` / `03b` |
| `witnessed_at` | 见证红灯的时间戳 |
| `exit_code` | 03a 观测到的退出码（必须为 1） |
| `failed_nodes` | 失败节点 id 列表，如 `["test_y.py::test_fail_a"]` |
| `test_files` | 映射：文件相对路径 → `sha256` |
| `green_at` | 03b 转绿时间戳 |
| `green_nodes` | 转绿时通过的节点集合 |
| `rewitness_count` | 回退到 03a 重做的次数 |

### 4.1 判据集的单调性（对应 R12 第 3 条、R10）

`failed_nodes` 一旦被见证并转绿，**永久进入判据集**。后续任何阶段
（含返工、04 的反例、05 归档）都要求这些节点保持绿。

> 这是「oracle 成本单调递减」的具体落点：同一个 bug 不能反复出现。

---

## 5. 门禁实施

### 5.1 挂载点（已核实）

`sw_lib/workflow/base.py:417` 的 `_run_pre_hooks()` 会执行
`hooks/pre_check_<stage>.sh`，而**该文件当前不存在**（已确认 `hooks/pre_check_*` 无匹配）。
这是现成的挂载点，无需改动调用方。

**但有两个坑必须处理**：

| 坑 | 证据 | 处置 |
|---|---|---|
| 失败被吞 | `base.py:414` 注释写明 "Non-fatal on failure"，`check=False` 且异常被 `except: pass` | 03a 的见证**不能**只依赖 pre hook。见证逻辑放在 post 侧（`check_03-coding.sh`），pre hook 仅用于设置 phase |
| 超时 30 秒 | `base.py:427` `timeout=30` | 跑测试可能超时。见证走 post 侧，post 侧超时为 `HOOK_TIMEOUT_SECONDS`（120 秒，`config.py:36`） |

### 5.2 `check_03-coding.sh` 改造

按 `red_witness.phase` 分流：

```
phase == 03a:
    1. 有测试文件产出（否则拒绝）
    2. harness 跑测试，要求 exit == 1
       exit == 2 → 拒绝：「造红」（collection error 不算红）
       exit == 5 → 拒绝：无测试
       exit == 0 → 拒绝：测试未失败，无法见证红
    3. 记录 failed_nodes + test_files 哈希，phase → 03b

phase == 03b:
    1. 重算测试文件哈希，与 red_witness.test_files 比对；不一致 → 拒绝
    2. 跑测试，要求 exit == 0 且 failed_nodes 全部转绿
    3. 记录 green_at / green_nodes
```

### 5.3 复用现有能力

`hooks/lib_run_tests.sh` 已解决三个真实踩过的坑，**必须复用而非重写**：

- `resolve_target_dir()` —— 从 `.state` 读 target_dir。
- `_pytest_pythonpath()` —— src-layout 下的 `PYTHONPATH=src`（任务 T2 的教训）。
- `_project_python()` —— 优先用项目 `.venv` 解释器（任务 T3 的教训：
  pandas 装在 `repo/T3/.venv`，用 harness 的 python3 会误报缺依赖）。

> 不复用这三处，会重现「门禁与 agent 对同一份代码给出相反结论」的问题。

需要新增的是**退出码分类**与**节点 id 提取**（`-rf` 输出解析）。

---

## 6. 与其他任务的接口

| 任务 | 接口 |
|---|---|
| **A0** | `red_witness` 经 `update_state` 写入并纳入 HMAC 签名；准出前先 `verify_evidence`，`tampered` / `unsigned` 均拒绝。**哈希校验是长期主防线**，不因 A0 就绪而降级（A0 的 U0-1：`bash` 无法按路径约束） |
| **A3** | 事实包注入 `red_witness` 与冻结的测试文件内容 |
| **A6** | O4 检查项读 `red_witness`：全部转绿且哈希未变 |
| **A7** | 反例文件存于 **`workspace/tasks/<task>/counterexamples/round-<n>/`**，与 `red_witness` 覆盖的测试目录**物理隔离** |
| | ⚠️ **A7 的 2.1 实测推翻了本行原写法**：原定 `facts/counterexamples/` 在 `target_dir` **之内**，会被主套件 `pytest` 收集（实测 `1 failed, 1 passed`）—— 哈希虽未被破坏，但**退出码被破坏**，让 A6 的 O2 与本任务的转绿检查永久失败。A7 的 3.1 已定案移出 `target_dir` |
| **A9** | 反例成立并修复后，该反例应追加进 `red_witness.failed_nodes`（单调性）。**A9 的 3.9 已定案触发时机：不是反例成立时，而是返工后该反例转绿时** —— 成立时追加会让 A6 的 O4（全部节点转绿）立刻失败，把返工路径堵死 |
| **A10** | 达成度报告的「测试」维度直接读 `red_witness` |
| **A11** | 变异探针依赖 `red_witness` 作为「哪些测试应该红」的基准 |

> A7 的隔离方案与 A9 的追加方案，是 `design-reviewer-independence.md` 10.2 的
> **D3 待定项**，此处定案。

---

## 7. mock 模式

`MockAgent` 是 CI 主力（`tests/unit`、`tests/e2e-flow`）。
见证机制若硬失败会挂掉全部 mock 测试。

**处置**：mock 模式（`is_mock_agent()`，`config.py:250`）下写入
标注 `mock: true` 的合成 `red_witness`，跳过真实测试执行与哈希校验。
**但保留结构** —— 下游任务读到的字段形状与真实模式一致。

---

## 8. 验收标准

**机制接通类**：

1. 03a 阶段写了失败测试（退出码 1）后可准出，`red_witness.failed_nodes` 非空。
2. 03a 阶段测试为 ImportError（退出码 2）时**被拒绝**，提示「造红」。
3. 03a 阶段无测试（退出码 5）时被拒绝。
4. 03a 阶段测试全部通过（退出码 0）时被拒绝 —— 无法见证红。
5. 03a 阶段测试全部 skip（退出码 0）时被拒绝。
6. 03b 阶段修改测试文件后**被拒绝**，提示具体文件。
7. 03b 阶段实现正确、测试未改动时通过，`green_nodes` 覆盖全部 `failed_nodes`。
8. src-layout 项目与带 `.venv` 的项目均能正确执行（复用 `lib_run_tests.sh`）。
9. mock 模式下全部既有测试通过（**单调性**）。

**有效性类**：

10. **绕过尝试**：用 `python -c` 修改测试文件后，03b 仍能通过哈希校验检出。
11. **造红尝试**：写一个引用不存在模块的空测试，不能通过 03a。

> 第 10、11 条是本任务唯一验证「机制真的防住了」的判据。
> 前九条只验证正常流程接通。

---

## 9. 风险

| # | 风险 | 处置 |
|---|---|---|
| R1 | 存量任务无 `red_witness`，进入 03b 时无基准 | 无记录时视为 `phase=03a`，首次进入即开始见证；不对历史任务追溯 |
| R2 | 非 Python 项目（npm 等）退出码语义不同 | 第一批**只支持 pytest**。npm 项目跳过见证并在事实包标注 `red_witness: unavailable`（不算通过，对应 A6 三态） |
| R3 | 测试执行超时 | 复用 `HOOK_TIMEOUT_SECONDS`；超时记为 `unavailable` 而非通过 |
| R4 | agent 在 03a 就写实现，导致测试直接绿 | 退出码 0 被拒绝（验收第 4 条）即可覆盖；无需额外检测 |
| R5 | 拆分子阶段增加交互轮次，用户体验变差 | 自动模式下 03a→03b 自动衔接；手动模式需两次 `/advance` |
| R6 | 哈希对空白与行尾敏感，格式化工具会误伤 | 计算哈希前不做规范化（保持严格）；若实践中误伤频繁，记录为摩擦点而非放宽 |

---

## 10. 本任务的红绿要点（自指最重）

### 10.1 自指的性质

本任务实现的是「强制走红绿流程」的机制。实现它时**没有该机制可用** ——
这是全部 A0-A11 中唯一完全依靠人自觉的任务。

**因此必须逐字遵守 `DEV-PROTOCOL.md` 第 1 节的六步。**
如果在实现 red_witness 的过程中跳过了「先看红」这一步，
那么这个机制的正确性就没有任何保障 —— 而它是后续所有任务的判据来源。

### 10.2 红的正确形态

| 验收项 | 红的正确形态（实现前必须看到） | 假绿风险 |
|---|---|---|
| 退出码 1 通过 | 断言「见证成功」失败，因为函数还不存在 | 用 mock 造假的退出码，测的是分支逻辑而非真实 pytest 行为 |
| 退出码 2 拒绝 | 构造真实 ImportError 测试目录，断言被拒绝 | 只断言「返回非 0」，无法区分 1 与 2 |
| 退出码 5 拒绝 | 构造真实空目录 | 同上 |
| 全 skip 拒绝 | 构造真实 `@pytest.mark.skip` 测试 | **退出码是 0**，若只看退出码会误判为绿 |
| 哈希校验 | 构造「改了测试」的场景，断言被拒绝且提示文件名 | 只测哈希一致的路径 |
| 节点 id 提取 | 断言提取到 `test_y.py::test_fail_a`，格式错误时应红 | 断言列表非空而不校验内容 |

### 10.3 必须用真实 pytest，不得 mock

第 2 节的五种退出码是**实测得到的 pytest 真实行为**。
若测试中 mock 掉 pytest 调用、直接喂造好的退出码，
那验证的是自己的分支逻辑，而不是「pytest 真的这样表现」。

**要求**：退出码分类的测试必须在临时目录中真实执行 pytest。
这与 A6 的 9.1 是同一条纪律。

### 10.4 最容易出的假绿

**「全部 skip」这一条**。它的退出码是 **0** ——
如果实现里只判断 `exit != 0` 算红、`exit == 0` 算绿，
那么一个全 skip 的测试套件会在 03a 被当成「测试通过、无法见证红」而拒绝（正确），
但在 03b 会被当成「全部转绿」而**通过**（错误）。

**03b 的绿必须同时满足**：退出码 0 **且** `failed_nodes` 中每个节点
在本次运行中确实 **passed**（不是 skipped）。
构造红的方法：把已见证的失败测试标上 `@pytest.mark.skip`，
断言 03b 拒绝。若这条测试写不出来，说明实现只看了退出码。

### 10.5 摩擦点记录

手动执行六步时若感到某处别扭，记录下来：

- 若「先写测试再写实现」在本任务中难以执行（因为要测的是测试机制本身），
  这个困难会同等地出现在 agent 身上，应反馈到 3.1 的子阶段划分设计。
- 若哈希校验在开发过程中频繁误伤（如编辑器自动格式化），
  记录频率 —— 这直接关系 R6 的处置是否需要调整。

**实施期实录（按 DEV-PROTOCOL 1.2 与本节要求补记）**：

1. **空壳先行是必要开销。** 状态读写类接口在函数缺席时只能红成
   `AttributeError` / `FileNotFoundError`，那是「造红」（1.1）。必须先落一个
   「永远放行的空壳」，红才落在断言上。agent 会遇到同样的困难 ——
   03a 的准出条件应容忍「测试红 + 空壳实现」这种中间态，而它恰好就是
   本机制的正常形态（退出码 1）。
2. **签名跨进程是一处会反复踩的坑。** 钩子是独立子进程、读真实
   `config/.evidence_key`，而测试进程的密钥被 `conftest` 重定向到 tmp。
   在测试进程里调 `begin_test_phase()` 写 `.state`，钩子必然判成 `tampered`。
   处置：测试一律走真实 pre hook 进入 03a，让写入与校验落在同一密钥域。
   曾因此重写两批用例（已显式声明重做）。
3. **`--phase` 曾沉默失效。** 钩子里写了 `red_witness "$T" --phase`，而
   `main()` 按位置参数解析、把 `--phase` 当成任务名 —— 钩子读到空串，
   分流从未生效，且**全部用例照旧通过**。补 `test_red_witness_cli.py` 锁住
   「未知参数必须报错，不得被当成任务名吞掉」。教训：bash 侧的接口
   （退出码 + stdout）本身就是契约，需要单独测。
4. **哈希误伤未观察到。** 03b 期间未发生一次因格式化导致的误判 ——
   开发中改的是实现文件，测试文件确实没有理由被动。R6 的处置暂不需调整。
5. **单元测试全绿 ≠ 机制接通。** 这是本任务最贵的一条教训。
   1053 条单元测试全绿、变异探针也过，而 `tests/e2e-flow/driver.py` 一跑
   就在 03 阶段卡死（`tampered`），且**连跑两个不同的 bug**（见 10.6 后两行）。
   共同点：两者都是**跨进程**故障 —— 签名域分裂、环境变量丢失。
   进程内的测试天然共享内存配置与密钥，因此对这类问题结构性失明。
   **A3-A11 的实施应把「跑一次 e2e」当作准出的必要条件**，
   而不是可选的补充验证；A6 的 O2 若只读单元测试结果，会漏掉同一类故障。

### 10.6 十二处对设计原文的修正（均由实施期实测推翻原文）

| 原文 | 实测结果 | 定案 |
|---|---|---|
| R1「无记录时视为 03a」 | 从测试结果反推子阶段不可能：「测试红」既可能是 03a 刚写完测试，也可能是 03b 实现没写对，处置恰好相反。按此实现会推翻既有契约 `test_real_failure_still_blocks`，并让存量任务与**每一轮 04→03 返工**永久卡死（实测 9 条既有用例转红） | 新增 `phase == "none"` 态。phase 只由 `begin_test_phase()` 显式设定，生产入口是 `hooks/pre_check_03-coding.sh`。`none` 时如实记 `unavailable` 并**不接管测试判定** —— 结论交回 `run_project_tests` |
| 第 7 节「mock 模式跳过真实测试执行与哈希校验」 | 照字面实现后，合成见证接管了整个 03 门禁并直接放行，`run_project_tests` 一并被跳过 —— **mock 下失败的测试能过闸**（实测 `test_real_failure_still_blocks` 转红）。mock 是 CI 主力，这个洞会让 CI 对任何坏实现报绿 | mock 免的是「红绿流程要真的走过一遍」，不是「测试要通过」。`--phase` 在 mock 下一律报 `none`，测试判定交回常规门禁。契约见 `test_red_witness_mock_mode.py` |
| —（原文未涉及） | `mark_unavailable` 曾用 `setdefault("phase", PHASE_TEST)` 补字段，导致存量任务过闸一次即被静默推进到 03a，**下一轮**按 03a 要求「测试必须是红的」而永久卡死 | `mark_unavailable` 不写 `phase`。它记录的恰恰是「未进入见证流程」 |
| 3.4「必须显式回退到 03a 重做」 | `request_rewitness()` 实现了，但**没有任何生产调用点**，门禁的拒绝信息也没提它 —— 设计里唯一合法的出路在真实流程中不存在。agent 在 03b 发现测试写错时只能静默改测试（被拦），然后卡死且没有下一步。**拦住一条路而不给替代路径，等于把人推向绕过机制** | 补 CLI 入口 `--rewitness <理由>`（理由必填，留痕并计数），并在哈希拒绝信息里**打印这条命令**。回退清冻结哈希但**保留 `failed_nodes`**（4.1 单调性），且回退后仍须重新见证到真实的红 —— 不是跳过冻结的捷径。契约见 `test_red_witness_rewitness.py` |
| 第 7 节「mock 模式下写入合成 red_witness」 | `sw init --mock` 只改**主进程内存**的 `mock_agent.enabled`，不写 `config.yaml`。而钩子是独立子进程、重新加载配置 —— 主进程用 mock 的固定 `_MOCK_KEY` 签名，钩子用真实 `config/.evidence_key` 校验，必然 `tampered`，**03 阶段永久无法准出**。全部单元测试当时是绿的：签名域分裂只在跨进程时出现 | 新增 `SW_MOCK_AGENT` 环境变量（`core/config.MOCK_ENV_NAME`）与 `set_mock_agent()`，`--mock` 同时写内存与环境，子进程继承。不回写 `config.yaml` —— `--mock` 是一次性开关，固化它会让下一次不带标志的运行也悄悄走 MockAgent。契约见 `test_mock_mode_crosses_process.py` |
| —（原文未涉及） | 上一处修好后 e2e 五阶段走通，但 `verify.py` 复查钩子时**另起进程、不带环境变量**，同一份合法 `.state` 又被判 `tampered`。根因是设计问题：**签名用哪把密钥是那份数据的属性，不是读它的进程的属性** —— 靠环境变量维系意味着任何一次环境丢失都让 mock 产出永久不可校验 | `verify_evidence` 改为从 `.state` **自描述**判断签名域（`red_witness.mock is True`），环境变量只决定新写入用哪把密钥。安全方向是收紧：mock 密钥是源码里的公开常量，因此只有**明确标了 `mock: true`** 的记录才允许用它校验，抹掉标记想蒙过去签名立刻对不上。契约见 `test_mock_signature_is_self_describing.py` |
| R2「npm 项目跳过见证并标注 `unavailable`」 | 照原文只在文档里写了，实现里没有落点：`hash_test_files` 只认 `.py`，纯 npm 项目在 03a 撞上「无测试：必须先写测试文件」的硬拒绝。**这不是跳过，是永久卡死** —— agent 写多少 `*.test.js` 都不会被看见，不存在「补上测试就能过」的自救路径（实测：`node test.js` 项目走真实钩子，门禁退出码 1） | 03a 在冻结集为空时先问「这个栈有 pytest 语义吗」（`_has_pytest_surface`：pytest.ini / pyproject / setup.py / tests\_ 目录 / 任意 Python 源码），没有则 `leave_witness_flow` + `mark_unavailable` 放行。**phase 必须抹回 `none`**，否则钩子不会把判定交回 `run_project_tests`，npm 的失败测试会过闸 —— 与本表第二行同一个洞。判据不能用「有没有 .py 文件」的反面来写：只有 `impl.py` 而漏写测试的 03a 仍须被拦（实测该处曾推翻 2 条既有契约）。契约见 `test_red_witness_non_python.py` |
| —（原文未涉及） | `status` 字段只在 `mark_unavailable` 里被写，`record_red` / `record_green` 从不清它。真实序列「返工轮记 unavailable → 下一轮见证到红 → 转绿」跑完后，`status` 仍是 `unavailable`（实测）—— 一次货真价实的见证在 A6/A10 的报告里显示成 ❓。方向上它是「把做到了的说成没做到」，但若 ❓ 既可能是真没见证、也可能是陈旧残留，这个字段就失去了信息量 | `record_red` / `record_green` 经 `_mark_status_ok()` 把 `status` 转 `ok` 并清掉 `unavailable_reason`（留着会出现「ok 却附着一条未见证的理由」这种自相矛盾的记录）；`request_rewitness` 反向清掉 `status`，否则「已转绿」会在「测试已解冻、红未重新见证」的窗口里继续对下游生效。修正方向是「见证发生时才转 ok」而非「把 unavailable 一律去掉」—— 后者会把 ❓ 变成假 ✅。契约见 `test_red_witness_status_freshness.py` |
| R4「agent 在 03a 就写实现，导致测试直接绿」→「退出码 0 被拒绝即可覆盖；无需额外检测」 | 拒绝实现了，**出路没有**。任务 `helloworld` 的真实死法：agent 用 `bash` heredoc 绕过 03a 的写入约束（`bash` 无法按路径约束，A0 的 U0-1）把 `src/` 下三个实现文件全部写完，20 个测试因此全绿，`phase` 停在 03a。`request_rewitness` 只能从 03b→03a，`leave_witness_flow` 没有 CLI 入口 —— 困在 03a 且测试已绿时命令行上不存在合法路径，只能手改 `.state`。**与本表第四行同一条判例：拦住一条路而不给替代路径，等于把人推向绕过机制** | 补 `abandon_witness()` + CLI `--abandon-witness <理由>`（理由必填，留痕 `abandon_reason`/`abandon_count`/`abandoned_at`），并在 03a **两条**拒绝路径（见证不到红 / 无测试）的输出里打印这条命令。四条约束：记 `unavailable` 而非伪造 `green_at`；phase 必须抹回 `none` 把判定交回 `run_project_tests`；**不动 `failed_nodes`**（4.1 单调性）；`_check_03a` 仍然拒绝全绿的 03a（验收第 4 条不变）—— 放弃是人的显式动作，不是自动放宽。契约见 `test_red_witness_abandon.py` |
| —（原文未涉及，由上一行的判据连带查出） | `run_project_tests` 的测试面判据是 `pytest.ini` / `pyproject.toml` / 顶层 `test_*.py` 三者之一，**认不出只放在 `tests/` 下的测试**。`repo/helloworld` 正是这个形状（`src/*.py` + `tests/test_*.py`，无 pyproject），20 个测试一个没跑，钩子打印「未发现 pytest 测试面」并返回 0 —— 失败的测试套件就此过闸。而见证侧的 `_has_pytest_surface` 认得 `tests/`，其 docstring 里写着「`tests/` 布局在那边是靠 pyproject 命中的」，该假设对无 pyproject 的项目不成立。**两侧判据错位使 A2 的所有让路路径把判定交给一个空转的函数** ——让路从「不接管判定」变成「取消判定」，是本表第二/七行那个洞的第三种形态 | 在 `lib_run_tests.sh` 里补 shell 版 `_has_pytest_surface()`，与见证侧判据对齐（补 setup.py / setup.cfg / tox.ini / 顶层 `*_test.py` / `tests|test` 目录下**确有** `test_*.py`\|`*_test.py`）。只认「目录存在」不够：空的 `tests/` 会让 pytest 报退出码 5，那时应走「无测试面」而非「跑了但没测试」。真无测试的项目仍如实打印「未发现 pytest 测试面」，不得反过来说成已执行。契约见 `test_hook_test_surface_detection.py` |
| R4 的**再次**修正：上一行定案「`_check_03a` 仍然拒绝全绿的 03a（验收第 4 条不变）——放弃是人的显式动作」 | 补了 `--abandon-witness` 之后，任务 `helloworld2` 用**同一种死法**又卡死一次：13:17:17 / 13:20:07 / 13:20:12 三次 `cat > src/... << 'EOF'`，9 个实现模块全部落盘 → 19 个测试全绿 → 门禁报「未能见证有效的红（退出码 0）」。**「显式放弃」这条出路在真实流程里没人走** —— 它要求用户为一件 harness 已经看得很清楚的事实手敲一条命令，而拒绝本身并没有因此多拦住任何东西。根因是架构性的：`bash` 是我们自己发给 developer 角色的工具（`config.yaml` 的 `tools` 含 `run_command`），而 opencode 的 session 权限规则只对 `write`/`edit` 有路径字段，`bash` 的参数是整条 shell 命令、无结构化路径可匹配。**我们没有拦住它的手段**，除非彻底不给 `bash`（那会废掉装依赖与跑测试） | **降级为事后处理（用户拍板）**：`_bypass_files()` + `_check_impl_first_bypass()`。退出码 0、**确有节点 passed**、**且存在实现文件**三条同时成立时，`leave_witness_flow` + `record_bypass` 并**放行**，测试判定交回 `run_project_tests`。成因判定刻意收窄 —— 造红（退出码 2）、全 skip、无测试、以及「自证测试全绿但无实现文件」四种**仍然拒绝**：那些不是拦不住，是 agent 做错了且都有自救办法。`record_bypass` 写**结构化**字段 `bypassed`/`bypass_files`（完整清单，不截断）/`bypass_count`，理由**指名实现文件**。代价必须写明：R1 在 Python 项目上不再有强制力，任何 agent 先写实现即可把见证降成 ❓ 且无摩擦；换到的是**如实** —— 不再有「机制存在但被绕过且无人知晓」这种最坏形态。`--abandon-witness` 退化为其余四种拒绝的兜底。契约见 `test_red_witness_post_hoc.py`；四处冲突的既有契约按 DEV-PROTOCOL 1.2 显式重做（`test_red_witness_abandon.py` 的夹具与两条前提自检、`test_red_witness_gate.py` 的验收 4、`test_red_witness_rewitness.py` 的后门用例） |
| —（原文未涉及，由上一行连带查出） | 降级之后「见证被绕过」成为常态，而它**在任何界面上都不可见**：`rg -n "red_witness\|见证" sw_lib/ui/tui.py` 返回空；事实包的 `warnings` 虽有内容，但 `manifest.json` **不在** `PromptBuilder._FACT_ORDER` 里，warnings **从未进过 reviewer 的 prompt**。于是 reviewer 只会读到 `tests.json` 的 19 passed 并判「实现已验证」，而那 19 个绿在绕过的情形下证明不了任何事 —— 没有任何断言曾经先失败。这是本项目反复出现的同一种形态：**判据存在、无人调用**（同 A0 的 2.5 `Toolbox` 白名单对 opencode 零引用、A5 的 R2 `active_roles()` 未接进渲染、上一轮的 `is_idle()` 零生产调用点） | 新增**单一来源** `witness_summary()`（三态标签：`ok`→✅ / `unavailable`·绕过→❓ / `absent`→❓，`detail` 带实现文件名）与 `witness_report_line()`，三处显示都从它取值 —— 各处自拼文案必然分叉。接线三处：TUI 头部 `_witness_badge()`（03 阶段显示「❓ 见证被绕过 xN」，真见证过时返回空以保区分度）、`fact_pack.witness_warnings()` 进 `generate()` 的 warnings、`PromptBuilder._read_fact_caveats()` 把 manifest 的 warnings 注入 04 prompt 且**排在 diff/tests 之前**（读到 19 passed 之前先知道那份绿有没有见证支撑）。判据只测**接线**不测字段：`test_witness_visibility.py` 断言 rich 渲染出的**文本**而非源码里有没有函数名 —— 源码断言（`test_multi_reviewer_display.py` 的做法）漏得过「引用了但没渲染出来」 |

---

## 11. 回滚

`red_witness` 机制由开关控制（`harness.coding.red_witness`），默认可关。
关闭后 03 阶段不拆分子状态，`check_03-coding.sh` 走原有逻辑，
行为与现在完全一致。`.state` 中的 `red_witness` 字段被忽略但不删除。

---

## 12. 多语言支持待办（未展开，仅登记）

见证机制第一批只支持 pytest（第 2 节）。常规测试执行本身是双栈的
（`lib_run_tests.sh` 认 pytest 与 `npm test`），但**见证**只覆盖 Python。
下面是已确认的缺口，按影响排序，尚未做设计：

- [ ] **npm 项目无法见证。** `npm test` 只有 0 / 非 0 两态，区分不出
      「造红」与真红、也认不出「全 skip 的假绿」。可行方向：挂 JSON reporter
      拿到节点级 outcome，把第 2 节的退出码表换成 outcome 表。
- [ ] **Go / Rust 等栈完全没有测试判据。** `run_project_tests` 不认
      `go test` / `cargo test`，这类项目在 03 阶段等于无测试门禁 ——
      `has_code_output` 只查「目录非空」。这比见证缺席更严重，
      且不属于 A2 范围（落点在 `hooks/lib_run_tests.sh`）。
- [ ] **03a 硬层只 deny `*.py`。** 非 Python 项目在 03a 可自由写实现文件。
      当前无实际影响（这类项目走让路路径、phase 为 `none`、不下发规则），
      但扩展见证到其他栈时必须同步扩 `_TEST_PATTERNS` 与 deny 模式，
      否则会出现「硬层允许写、准出时却算进冻结哈希」的错位。
- [ ] **`_has_pytest_surface` 的判据需随之调整。** 它现在把「有任意
      `.py` 源码」当作 Python 栈（为的是不放走漏写测试的 03a）。
      支持多栈后，「是不是 Python 栈」与「该用哪个 runner」要分开判定。

> 登记依据：本轮实测确认了让路路径可用（R2），但让路只是不卡死，
> 不等于这些栈受到保护 —— 报告里它们应始终显示 ❓ 而非 ✅。
