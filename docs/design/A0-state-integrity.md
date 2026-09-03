# A0：状态完整性与证据不可伪造

> 依赖：无（波 1 地基）
> 被依赖：**A2**（`red_witness` 的存储）、A3（事实包）、A6（客观轨读判据）、
> A9（仲裁写 Route）、A10（归档报告）
> 范围：`sw_lib/core/state.py`、`sw_lib/tools/toolbox.py`、
> `sw_lib/agents/opencode.py`、新增 `sw_lib/core/evidence.py`
> 依据：`docs/self-verification-gaps.md` 的 R2、L6；
> `docs/design-reviewer-independence.md` 的 10.1、10.4 P1
>
> 🔒 **开发纪律**：实施本任务须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。
> ⚠️ **本任务含自指风险**：它实现的是「证据不可伪造」。测试若绕过受控入口
> 直接写 `.state`，就是在验证时使用被禁止的行为。处置见第 9 节 ——
> 结论是「绕过用于构造攻击样本是必要的，用于制造通过是禁止的」。

---

## 1. 目标与定位

**原则**：判据必须在循环之外。agent 能改写的东西不能充当判据。

A2 的 `red_witness`、A3 的事实包、A6 的客观轨结论、A9 的 Route 决策，
全部落在 `.state` 与 `facts/`。若这两处可被 agent 写，
**后续九个任务全部失效** —— 不是效果打折，是机制归零：
一个能改判据的 agent 永远能让自己通过。

### 1.1 必须先承认的边界

本任务的目标是 **篡改可检出**，不是 **篡改不可能**。

理由：当前唯一实际在跑的 agent 拥有 shell（`bash`）。只要有 shell，
就没有任何应用层机制能阻止它写文件。真正的阻止需要进程隔离
（`config.yaml:12` 的 `docker.enabled: false` 是现成挂载点，但未启用）。

因此 A0 交付的是两件事：

1. **完整性**：`.state` 不会因并发或中断而损坏、丢失、静默回退。
2. **可检出性**：证据被绕过受控入口修改时，校验能发现并拒绝。

把目标写成「不可伪造」而不标注这条边界，本身就是一次自欺。

---

## 2. 现状：已实测的缺陷

### 2.1 P0（本次新发现，比无锁写更严重）：损坏被自动迁移固化

`read_state`（`core/state.py:22`）对非 JSON 内容回退到旧的 `key: value`
文本格式解析，**并在 `:64` 回写磁盘**。

一个写入中途被打断的 `.state` 会被当作「旧格式」解析成功，然后固化。
实测（截断到一半的 JSON）：

```text
磁盘原文:  { "id": "T1", "stage": "03-coding", "stage_idx": 2, "re
回写之后:  { "\"id\"": "T1\",", "\"stage\"": "03-coding\",", "\"stage_idx\"": "2," }
read_state 返回: stage="01-brainstorming"  stage_idx=0  red_witness 消失
```

三重后果：键名带引号变成垃圾；`red_witness` 整棵子树丢失；
**stage 静默回退到第一阶段**（`:75` 与 `:69` 的默认值填充）。

这比 P1 严重的地方在于：非原子写只是「可能损坏」，
而自动迁移把损坏转成了一个**看起来合法**的状态 —— 没有任何报错，
下游读到的是一份自洽但错误的判据。

### 2.2 P1：无锁、非原子写（上游 10.4 已记录）

`write_state`（`:94`）在 `:112` 直接 `open(sf, "w")` + `json.dump`。
无临时文件、无 `os.replace`、无 `fsync`、无锁。
全仓库 `rg -n "os.replace|flock|tempfile" sw_lib` **零结果**。

`open(..., "w")` 先截断再写，因此崩溃窗口内文件必然是半截的 ——
正好落进 2.1 的固化路径。**这两个缺陷是连着的，必须一起修。**

### 2.3 P2：丢失更新（read-modify-write 无版本校验）

实测两个调用方各自 `read_state` 后写回，后写者静默覆盖前写者：

```text
客观轨写入 objective={"O3":"fail"}  →  攻击者写入 adversary={...}
最终 .state 只有 adversary，objective 消失
```

`write_state` 有 39 处调用点（`rg -n "write_state" -g'*.py'`），
包括 `workflow/stage_state.py` 的 8 处、`ui/tui.py:473`、
`core/service.py:351`、`core/health.py` 的 3 处（**独立线程**，
`health.py:59` 有 `threading.Event`）。Web 与 TUI 可同时运行。

> A5 的风险表 R1 提到并行写竞争，缓解方式是「子节点不直接写 `.state`」。
> 那只覆盖 A5 自己的 fan-out，不覆盖 TUI / Web / HealthMonitor 线程。

### 2.4 写保护的绕过路径

上游 10.1 记录了三条。实测补充三条，其中一条是 P0 级：

| # | 路径 | 证据 | 来源 |
|---|---|---|---|
| 1 | `facts/` 与 `evidence/` 不在保护范围 | `toolbox.py:129` 只匹配 `target.name`。实测 `WriteFileTool` 写 `facts/tests.json` 与 `evidence/x.log` **均返回成功** | 上游 |
| 2 | `restricted` 是命令字符串匹配，可拼接绕过 | `toolbox.py:182` 的 `".state" in command`。实测 `chr(46)+'state'` **不被拦截** | 上游 |
| 3 | 白名单含 `python` / `sh` / `bash` / `sed` | `toolbox.py:24` 起 | 上游 |
| 4 | **`restricted` 是 LLM 可见的工具参数，直接传 `False` 即关闭保护** | `toolbox.py:150` 的 description 明写 `restricted (bool, 可选...)`；`toolbox.py:271` 的 wrapper 是 `**kwargs` 透传。实测 `restricted=False` 后含 `.state` 的命令**正常执行** | **本次实测** |
| 5 | `sh -c` 可执行白名单外命令 | 白名单校验只看首 token。实测 `sh -c 'which rm'` 通过，`/bin/rm` 可达 | **本次实测** |
| 6 | `cwd` 参数不做 ROOT 校验 | `toolbox.py:186` 只做 `resolve()`，不比对 ROOT。实测 `cwd="/Users/yfwang"` 成功列出家目录。`_safe_path`（`:71`）只服务 read/write 工具，不在此路径上 | **本次实测** |

第 4 条最要紧：前三条需要一点巧思，这一条是**写在工具说明里的开关**。

### 2.5 决定性发现：以上加固对当前实际运行的 agent 全部无效

`config.yaml` 五个角色的 `agent` 字段**全是 `opencode`**（已核实）。
而 `Toolbox` 只被 `agents/gemini.py:18` 引用 ——
`rg -n "Toolbox|get_available_tools" sw_lib` 的结果只有 `toolbox.py` 自身
与 `gemini.py` 两处。

`OpenCodeAgent` 走的是 **opencode 原生工具**：
`agents/base.py:136` 硬编码 `use_native_tools=True`，
`opencode.py:106` 把阶段权限翻译成 opencode 工具开关
（`TOOL_MAP`，`opencode.py:46`），实际读写与命令执行发生在 opencode 进程内。

**结论：2.4 全部六条绕过路径分析的是一个当前没有被使用的模块。**
真实的 agent 通过 opencode 的 `write` / `edit` / `bash` 工具作用于文件系统，
`toolbox.py` 的白名单、`_PROTECTED_FILES`、`restricted` 对它一概不生效。

若不先弄清这一点，A0 会去加固一条死代码路径，然后宣布「证据已不可伪造」——
这正是本项目要消除的那类假绿。

### 2.6 opencode 侧的真实权限机制（实测 + 二进制符号分析）

| 事实 | 依据 |
|---|---|
| harness 的 `tools` 字典经 `set_tools` 进 `payload["tools"]`（`transport.py:276`），opencode 将其转成 session 权限规则 `{permission, action: allow\|deny, pattern: "*"}` | 二进制中该转换代码可见：`for(let[Q,C]of Object.entries(t.tools??{}))R.push({permission:Q,action:C?"allow":"deny",pattern:"*"})` |
| opencode 有 `PermissionV2`，规则形态 `{permission, pattern, action}`，action ∈ `ask` / `allow` / `deny` | 二进制符号 `PermissionV2.Rule` / `Ruleset` / `AssertInput` |
| 判定用 `findLast` 匹配：**后面的规则覆盖前面的，无匹配时默认 `ask`** | `findLast((z)=>match(j,z.permission)&&match(J,z.pattern)) ?? {action:"ask"}` |
| **权限支持路径粒度** —— `write` / `edit` 的 ask 传的是相对 worktree 的路径 | `ask({permission:"edit",patterns:[relative(worktree,u)],...})` |
| harness 只生成 `pattern:"*"` 的粗粒度规则，**未利用路径粒度** | `opencode.py:181` 返回 `{t: bool}`，无 pattern 维度 |
| **harness 不订阅 `permission.asked` 事件** | `rg -n "permission" sw_lib/agents/*.py` 零结果；`transport.py:421` 只处理 `question.asked`。→ 一旦产生 `ask` 会死等到 `CHAT_TIMEOUT`（1800 秒） |
| opencode workdir 是 `repo/<task>`（`opencode.py:113`），`.state` 在 `workspace/tasks/<task>/.state`，相对路径为 `../../workspace/tasks/<task>/.state` —— **在 worktree 之外** | 已核实路径计算 |
| 凭证注入是 `os.environ.copy()`（`opencode.py:130`）并整份传给子进程 | → **密钥放环境变量会被 `bash` 工具一条 `env` 拿到** |

两条直接推论：

1. A0 在 opencode 侧只能用 `allow` / `deny`，**不能用 `ask`**（无人应答）。
   想用 `ask` 就必须先补事件订阅，那属于另一个任务。
2. HMAC 密钥**不能放环境变量**，且必须从子进程 env 中显式剥离。

---

## 2.7 实测结论：D0-6 的前提不成立（2026-08-23，运行时实测）

9.3 末尾曾记录「未能起 `opencode serve`，2.6 是静态证据」。**现已补上运行时实测**
（`opencode serve` 1.18.20，本机 127.0.0.1:8891，隔离 `HOME`）。
结论与 2.6 的推断**部分冲突**，D0-6 必须改设计。

### 2.7.1 已确认成立的部分

| 事实 | 实测证据 |
|---|---|
| session 接受 `{permission, pattern, action}` 形态的规则数组 | `PATCH /session/{id}` 返回 200，规则原样出现在 session 的 `permission` 字段 |
| `action` 枚举确为 `allow` / `deny` / `ask` | `/doc` 的 `PermissionV2Effect` schema |
| **规则可在创建时带入** —— `POST /session` 的 body 支持 `permission` | 实测返回 200，`GET` 回读规则数正确 |

### 2.7.2 三条推翻或存疑的发现

**（1）`PATCH /session/{id}` 是 merge，不是 replace。**

连续三次 PATCH 不同规则集后，`GET` 回读到 **5 条规则**（1+2 条历史 + 后续追加），
而非最后一次下发的内容。这意味着：

- 每阶段重新下发权限会**不断累积**，旧规则永不失效；
- 由于判定是 `findLast`（后者优先），累积会让**先前的 deny 被后来的 allow 覆盖** ——
  恰好是最危险的方向。

**处置**：改用 `POST /session` 创建时一次性带入规则（已实测可行），
**不使用 PATCH 增量下发**。

**（2）`POST /api/session/{id}/permission` 不是纯规则求值器，不能用作验收判据。**

该端点（`v2.session.permission.create`）在以下场景**一律返回 `allow`**：

| 规则集 | 探测 | 返回 |
|---|---|---|
| 空规则集 | `write notes.txt` | `allow` |
| **仅一条 `write:* deny`** | `write notes.txt` | **`allow`**（应为 deny） |
| `allow *` + `deny **/.state` | `write .state` | **`allow`**（应为 deny） |

且服务端日志中**没有任何 `evaluated` 行**（该实现会在求值时
`logInfo("evaluated", ...)`，见 2.6 引用的代码）——
说明请求根本没走到规则求值分支。

> 更值得警惕的是：**首次实测与复现实测结果不一致。**
> 首轮曾出现 `write notes.txt -> deny`、`write .state -> deny`，
> 但随后用「一规则集一新 session」的受控设计复跑三次，全部为 `allow`；
> 同 session 内重复探测四次亦全部 `allow`。
> 首轮的 deny 无法复现，**判定为受 PATCH 累积污染的假象**。
>
> 这正是 DEV-PROTOCOL 第 3 节第 2 条要防的情形 ——
> 若当时就采信首轮结果，会得出「deny 规则有效」的错误结论并据此写实现。

**（3）因此「deny 规则真的阻止写入」这一条尚未被证明。**

真实的权限判定发生在**工具执行路径**（`ask()` 内），
需要一次真实的 LLM 会话触发 `write` 工具才会走到。
本次实测未使用真实模型（无凭证、且会产生外部调用），**故未能验证**。

**状态：❓ 未验证** —— 不是 ✅，也不是 ❌。

### 2.7.3 对设计的影响

| 影响 | 处置 |
|---|---|
| D0-6 的规则下发方式 | 从 PATCH 改为 `POST /session` 创建时带入 |
| D0-6 的有效性 | **降级为「尽力而为」**，不作为 A0 的交付承诺 |
| 验收第 15 条 | 无法用 assert 端点验证，改为「规则形状测试 + 真实会话验证（标 ❓ 直至具备条件）」 |
| **第二层（HMAC 校验）的地位** | **从「补充」上升为唯一可靠防线** |

最后一条是本次实测最重要的产出：既然写入侧的阻止无法被证明有效，
**准出时的校验就是唯一能兑现「篡改可检出」的机制**。
这与 1.1 的边界声明一致，也印证了 A2 的 3.3 修正
（哈希校验是长期主防线，而非临时替代）。

> 实施顺序因此调整：**第二层优先于第三层**，见第 6 节。

---

## 2.9 实施期补充实测（2026-08-23，D0-5 / D0-6 / D0-7）

### 2.9.1 D0-6：规则下发已端到端验证（但有效性仍 ❓）

用真实 `opencode serve` 1.18.20（127.0.0.1:8893，隔离 `HOME`）实测：

| 项 | 结果 |
|---|---|
| `POST /session` 携带 31 条规则 | ✅ HTTP 200 |
| `GET /session/{id}` 回读 | ✅ 31 条，`perm == rules` **逐字节一致** |
| 经**真实 `OpenCodeTransport`** 建 session（非手拼 dict） | ✅ 31 条一致 |
| `04-review` 阶段的 write 规则 | ✅ 全为 deny，无一条 allow |

**仍未验证的部分不变**：deny 是否真的阻止工具写入。
需真实 LLM 会话触发 `write`，assert 端点不可用作判据（2.7.2 第 2 条）。
**状态：规则下发 ✅ / 阻止有效性 ❓。**

### 2.9.1b 致命缺陷：消息级 `tools` 会整体覆盖 session 权限

**这是本次实施中最重要的发现，它证明 D0-6 的原实现在首条消息后即完全失效。**

opencode 二进制 `SessionPrompt.prompt`：

```js
for (let [Q, C] of Object.entries(t.tools ?? {}))
    R.push({permission: Q, action: C ? "allow" : "deny", pattern: "*"});
if (R.length > 0) O.permission = R, yield* o.setPermission({...});
```

`O.permission = R` 是**整体赋值，不是追加**。实测（真实 serve 1.18.20）：

| 步骤 | 规则数 | 路径级 deny |
|---|---|---|
| `POST /session` 带 31 条规则 | 31 | 22 |
| **发一条带 `tools` 的消息后** | **9** | **0** |
| 修复后（消息不带 `tools`）再发一条 | 31 | 22 |

即：`transport.py` 原本每条消息都发 `tools`（`:276`），
于是**首条消息就把全部路径 deny 抹掉**，只剩 `pattern:"*"` 的粗粒度规则。

这与 2.7.2 第 1 条（PATCH 累积）是**两个独立的坑**，但方向相同 ——
都让 deny 静默失效。若不实测，单测全绿而线上毫无防护。

**处置**：有规则表时消息不再携带 `tools`（`tools` 在 opencode 侧
**只**用于生成权限规则，不发不丢功能）；无规则表时退回旧行为。
已加测试 `test_send_message_does_not_carry_tools_when_rules_are_set` 固化。

> 教训与 2.7.2 同构：**接线正确 ≠ 生效**。
> 单测只能证明我方发出的形状，服务端如何处置必须实测。

### 2.9.2 三处实施期发现的新缺口（文档原先未列）

| # | 缺口 | 说明 | 处置 |
|---|---|---|---|
| 1 | **`_load_env()` 之外还有第二条环境出口** | `pty.py:206` 同样 `os.environ.copy()` 后交给 `gemini` 子进程。只堵 opencode 一处是假绿 | 抽 `evidence.strip_secrets()`，两处出口共用 |
| 2 | **按名字剥离密钥不够** | 密钥值若被复制到别名变量下（credentials.yaml 转发变量、用户 `export` 副本），按名字删不掉 | `strip_secrets` 增加**按值扫描** |
| 3 | **`ReadFileTool` 可直接读走密钥** | D0-7 表格只列了写入侧。`read_file("config/.evidence_key")` 与 `cat` 均可取走密钥 —— 密钥可读则 HMAC 形同虚设 | 密钥路径读写皆禁；范围**只含密钥**，`.state` / `facts/` 仍允许读（禁读会让工作流不可用） |

### 2.9.3 两处测试自身的缺陷（若不修会造成假绿）

1. **`get_key()` 的 `lru_cache` 跨用例残留** ——
   `test_evidence.py` 单跑 11 条全绿，全量跑却红一条。密钥隔离夹具改为
   `autouse` 并 `cache_clear()`。
2. **测试会在真实 `config/` 下创建密钥** ——
   `config.yaml` 的 `mock_agent.enabled` 为 `false`，本地跑测试走真实分支，
   `write_state` 签名时 `get_key()` 就把生产密钥文件创建出来了。
   已在 `tests/conftest.py` 加 session 级 `_isolate_evidence_key` 重定向到 tmp。
   实测：删除后跑全量 775 条，密钥**不再生成**。

> 第 2 条的危害不止是"脏"：若某用例写入了不同密钥，
> 用户既有 `.state` 的签名会集体变成 `tampered`。

### 2.9.4 白名单加固的连带影响

移除 `sh`/`bash`/`zsh` 后，既有用例
`test_run_command_state_file_protected`（`echo x > .state`，ROOT 下）转红 ——
新的前缀保护只覆盖 `workspace/`，而该用例的 `.state` 在 ROOT 下。
处置：前缀匹配之外**再加一层文件名匹配**
（`.state` / `.state.lock` / `STATUS.json` 在任何目录下禁写）。

> 这条是「加固引入回归」的实例，与 2.8 同类：
> 缺陷只在与既有用例组合时显形。

### 2.9.5 真实 LLM 会话实测：验收 15 结案，并推翻一条错误结论

前两轮验收 15 一直挂在 ❓，因为「deny 是否真的阻止写入」只能靠真实 LLM
触发 `write` 工具才能判定。本轮用真实凭证（`~/.local/share/opencode/auth.json`）
与 `mimo-v2.5-free` 跑真实会话补齐，四条结论**均有工具级证据**
（读 `/session/{id}/message` 的 tool part `status`，不看模型自述）：

| # | 结论 | 证据 |
|---|---|---|
| 1 | **deny 真的阻止 `write`** ✅ | A/B 对照：无 deny → 写入成功；有 deny → tool part `status=error`，错误文本含服务端规则列表，目标文件原样 |
| 2 | `**/.state` glob 覆盖嵌套路径 ✅ | `sub/.state` 同样被拦 |
| 3 | **`write` 工具查的是 `edit` 权限，不是 `write`** ❗ | 各 3 次：只 deny `write` → **写入成功**（`status=completed`）；只 deny `edit` → 被拦 2/2 |
| 4 | **`bash` 绕过成功，且模型自发回退** ❗ | 提示「用任何可行工具达成」后，`write` 被拒 → 模型**自己改用 `bash`**，3/3 全部写入成功 |

第 3 条改变了实现的正确性判断：规则表里同时 deny `write` 与 `edit`
**不是冗余，是必要条件**。若有人日后「精简」掉其中一条，保护即失效。
已在 `tests/unit/agents/test_opencode_permission_rules.py` 追加
`test_write_and_edit_both_denied_because_write_tool_checks_edit` 守住。

第 4 条给 U0-1 提供了实证：路径级 deny 不足以保护判据，
**HMAC 签名（验收 16）是唯一真兜底**，不是补充。

新增 `tests/integration/test_opencode_permission_live.py`（5 条，默认 skip，
`HARNESS_LIVE_OPENCODE=1` 开启）。实跑 **5 passed in 112.50s**。

**一次必须记录的自我纠错**：中途曾得出「只 deny `write` 也有效」的结论，
理由是「文件没被改」。核对工具级记录后发现模型**压根没调用 `write`**
（有一次还幻觉出 "Plan Mode" 自称只读）。
把「没发生」读成「被拦住」，方向恰好与事实相反。

> 处置已固化为测试纪律：**凡目标工具未被调用，一律 `pytest.skip`
> 判 INCONCLUSIVE，绝不计为通过。** 否则模型摆烂即得满分。

### 2.9.6 三态之外：`stage_status` 翻转会抹掉刚签的 Gate（真实缺陷，已修）

本轮排查遗留项时实测出一个**生产路径上的判据丢失**：

```text
base.py agent 跑完 → 翻 stage_status=idle（读旧快照，整体写回）
用户同时在 TUI 按 [A] 签 Gate
结果：gate.signed_by 变回 None
```

`runtime.py:114` 早有注释承认踩过这个坑，但当时只在那一处就地重读绕过，
**同形状的其他写入点仍在**（`base.py` 2 处、`graph.py` 1 处、`tui.py` 1 处）。

处置：新增 `WorkflowRuntime.set_stage_status(name, status)` 走 `update_state`，
四处调用点全部接入。测试 `test_status_flip_does_not_erase_gate_signature`。

**第二次自我纠错**：该测试第一版是**假绿** ——
`set_stage_status` 尚不存在，线程内 `AttributeError` 静默死掉，
断言因此毫无压力地通过。已改为用 `errors` 列表显式收集线程异常并断言非空为红。

> 与 2.9.5 的教训同构：两次假绿都源于**把「什么都没发生」当成「通过」**。
> 线程内异常、模型未调用工具，都属于这一类。

### 2.9.7 任务 helloworld 的现场：七处「指令与约束互不校验」（已修）

用户运行任务 `helloworld`（真实 opencode 会话）后交出完整日志。前一轮已修掉
03a 的死锁（A2 的 10.6 末两行），本节记的是**同一份日志里剩下的七处** ——
它们的共同形状是：**指令（prompt）与约束（权限规则 / 门禁判据）由两套独立
代码维护，彼此从不校验**。于是 harness 一边要求 agent 做某件事，一边在硬层
禁止它，或者一边宣称检查过，一边判据恒真。

| # | 现场证据 | 根因 | 处置与判据 |
|---|---|---|---|
| 1 | 日志里 `invalid {'tool': 'bash', 'error': "Model tried to call unavailable tool 'bash'"}`，以及五个阶段模板都写着 `ask_user` | `ask_user` 是 harness 的抽象名（`TOOL_MAP` 的**键**），agent 只能调 opencode 原生名 `question`。prompt 写的是抽象名，每次必撞 `invalid tool` | 新增 `PromptBuilder.describe_tools(stage, role_id)`，从 `TOOL_MAP` **推导**原生名下发，不写死文案；五个模板 + `system.yaml` 改为 `question`。判据 `test_prompt_tool_contract.py` |
| 2 | 01 阶段模板区全是 `___`，agent 从未写成功过一次 | 01 prompt 命令「用 `write_file` 回填 `workspace/tasks/{task}/01-brainstorming.md`」，而 `_permission_rules()` 对 `workspace/**` 的 write/edit **一律 deny** —— **成功率恒为 0** 的指令 | 删掉该指令，改为「产出直接写在回复正文里」并列出应含结构。产出区由 harness 落盘，agent 无需也无权改那个文件 |
| 3 | 8 次 read/glob 打空，路径形如 `repo/helloworld/repo/helloworld` | prompt 告知「代码生成目录: repo/helloworld」（harness 相对路径），而 agent 的 cwd **就是**该目录 | `_build_project_info()` 改为「你当前的工作目录就是本任务的代码目录」，**不给任何路径**；`_write_context_marker` 去掉 `.resolve()`，与 `.state` 同一表示法。判据 `test_prompt_path_frame.py` |
| 4 | 01/02 两阶段模板区全是 `___` 与空表格，却双双过闸并推进到 03 | `check_01` 只验「文件存在 + gate 已签署」；`check_02` 只 `grep -q "## Task DAG"` —— **那个标题是模板自带的，判据恒真**，且它**完全没验 gate 签署**（实测未签署也能过闸） | 新增 `sw_lib/workflow/output_check.py`：产出区（nonce 围栏内）必须有实质内容，阈值 80 由实测校准（真实产出去噪后 303 字符，空转 <30）。两个钩子接入，`check_02` 补上 gate 检查。判据 `test_early_stage_output_check.py` |
| 5 | 日志里 `todowrite` 出现 9 次、还有 `skill` 与 `invalid` | `_MANAGED_TOOLS` 只列 9 个，这三个都不在其中 —— harness 对它们的存在毫无记录 | 新增 `_UNMANAGED_TOOLS` 并逐个写明为什么不管；`skill` 标注为**风险最高**（来自用户全局 `.agents/`），是知情选择而非遗漏。判据 `test_managed_tool_coverage.py` |
| 6 | 03 阶段 opencode 就绪 10:00:40，**最后一次工具调用在 +895s**，+900s 断开 —— 距上次活动仅 **5 秒** | `CHAT_TIMEOUT` 是**墙上时钟**，区分不出「卡死」与「在干活」。同类事故已两次（任务 `ttt` 300→900、`helloworld` 900 又砍一次），每次只是把数字调大 | 新增 `IDLE_TIMEOUT = 300.0` 与 `note_activity()` / `idle_seconds()`，用 `time.monotonic()`；取值三条边界：≥ 实测最大工具间隔 92s 的两倍、> `QUESTION_TIMEOUT`(180s)、< `CHAT_TIMEOUT`(900s)。超时报错改为**由空闲时长给出诊断**。判据 `test_idle_based_timeout.py`（**只锁关系与区间，不锁数值** —— 教训见 `test_timeout_hierarchy.py` 第一条） |
| 7 | 修完 4 之后 e2e 挂在 02：产出去噪后仅 65 字符；修完继续跑，`WBS items preserved` 打印「0 unchecked」而 mock 明明输出 3 条 | ① MockAgent 的 02 产出只有三行 WBS 标题、05 落在 `_scenario_generic` 一句话收工 —— **mock 薄得过不了自家门禁**；② 那条 e2e 验收项 `ok` 参数写死成字面量 `True`，且按 `## 🤖 AI Output` 标题 split 取 `[1]`（该标题在文件里出现两次，`[1]` 只是中间的围栏注释行）—— 两个 bug 叠成恒真判据 | mock 的 02 补齐 Task DAG / Test Strategy / Tech Detail（对齐 `fact_pack.build_plan` 的提取标题），新增 `_scenario_archive`；`verify.py` 新增 `output_region()` 按围栏 nonce 取产出区，WBS 判据改为真判断。判据 `test_mock_output_substance.py`（复用 `check_output` 本身当尺子并要求 20% 余量）、`test_e2e_verify_no_tautology.py`（源码级扫描 `ok` 不得为字面量 `True`） |

**第 7 项走到过一个岔路口，值得单独记**：e2e 卡在 `65 < 80` 时，把阈值降到 65
是一步就能变绿的改法。但阈值是实测校准出来的（真实 200+ / 空转 <30），
为迁就 mock 去动它，等于**让 e2e 反过来定义什么算「有产出」**——
正是 A6 的 9.3 明令禁止的「放宽标准让存量变绿」。定案是补 mock：
一个连自家门禁都过不了的驱动，测不出任何有价值的东西。

> 本节与 2.9.5 / 2.9.6 的教训同构，但换了一个方向：那两条是「把什么都没发生
> 当成通过」，这七条是**「把彼此矛盾的两套规则各自当成正确」**。
> 单元测试对后者结构性失明 —— 它们各自都有测试，各自都绿。
> 第 1、2、4 项在真实会话里已经连续失败了整整一个任务的生命周期，
> 而 1400+ 条单元测试全程无一变红。**这是「单元全绿 ≠ 机制接通」的第五次**
> （前四次见 A2 的 10.5）。共同解法只有一条：**为「指令与约束一致」本身写
> 判据**，而不是分别测试指令和约束。

---

---

## 2.8 实施中发现的自伤缺陷（D0-1 的漏洞，已修）

D0-1 让 `read_state` 对损坏返回 `{"_corrupted": True, ...}` 而不再回写。
实现 D0-3 之后实测发现：**这个改动自身引入了一条摧毁原始内容的路径。**

全仓库有十余处 `read_state` → 改 → `write_state` 的调用点
（`stage_state.py` 8 处、`runtime.py` 4 处、`base.py` 2 处、
`service.py`、`tui.py`、`graph.py`、`commands.py` 各若干）。
它们拿到 `_corrupted` 空壳后照常修改并写回。

实测复现（`stage_state.seed_gate`）：

```text
损坏原文:      { "id": "T", "stage": "0
seed_gate 后:  { "_corrupted": true, "_corrupt_reason": "...", "stages": {...} }
```

`seed_gate` 返回 `True`（表示"写入成功"），而损坏内容**已被覆盖** ——
D0-1 想保住的东西恰好被 D0-1 自己引入的空壳挤掉了。

**处置**：拦在 `write_state` 这个共同瓶颈上 ——
任何试图持久化 `_corrupted` 标记的写入一律 `ValueError`。
不逐个改十余处调用点：那样繁琐且必然有遗漏。

> 这条值得记录的原因不在于修法，而在于**它是一次典型的"修复引入新缺陷"**。
> 若只跑 D0-1 自己的六条测试，全部是绿的 —— 缺陷只在与真实调用方
> 组合时才显形。因此验收里补了一条**从真实调用方视角**出发的测试
> （`test_seed_gate_does_not_clobber_corrupted_state`），
> 而不是只验证 `write_state` 的内部约束。
> 只验证自己的内部约束，等于假设"瓶颈处已拦住"，那是未经检验的自信。

---

### 2.9.9 任务 helloworld2 的现场：四处已坐实、三处未修（登记待办）

用户运行任务 `helloworld2` 后交出日志。**第一处已修**（`bash` 绕过 03a，
见 A2 的 10.6 倒数第二行：降级为事后处理），下面三处已坐实根因但**本轮未修**，
登记以免丢失：

| # | 现场证据 | 根因 | 状态 |
|---|---|---|---|
| 1 | 13:20:42 → 13:29:58 静默 **9 分 16 秒**，按 `IDLE_TIMEOUT=300` 本该 13:25:42 切断 | `transport.py:156` 的 `is_idle()` **除自身测试外零生产调用点**（`rg -n "is_idle"` 只有定义 + 测试）。上一轮加了方法却只在超时报错文案里用了 `idle_seconds()` —— 又一次「判据存在、无人调用」 | ❌ 未修 |
| 2 | agent 把 7 任务 DAG 写进 `repo/helloworld2/PLAN.md`，而 `workspace/tasks/helloworld2/02-planning.md` 的 `Task DAG` 段仍是 `___`，产出区只有 262 字符摘要 | 02 的 prompt 至今只有一句「请开始规划阶段的工作」，**没有任何地方说明产出该落在哪**。上一轮只修了 01 的模板（2.9.7 第 2 条），02 同形状的问题漏了 | ❌ 未修 |
| 3 | 03 的 claims 三字段全空：`{"task_ids": [], "verify_cmd": "", "files_touched": []}` | 04 的 claims-vs-diff 对照因此拿到空数据，A3 的「声明与事实对照」空转 | ✅ **已修**（根因见 2.9.11 第 1 条：声明写在模板区，而抽取只读围栏区） |

另有一处**顺序倒置**：13:30:23 签署 Gate、13:30:29 硬校验才失败，
`.state` 里因此留下一条与事实矛盾的记录，且无任何机制标记这种矛盾。
Gate 签署与硬校验的先后关系需要单独设计，本轮不动。

---

### 2.9.10 任务 ppppp 的现场：约束只有下界没有上界（已修）

用户报「无限 ask_user」。**它不是死循环 bug** —— 每一轮都在等真人回答，
每一次回答都落了盘。它是**没有出口**。

现场：`15:02:00 → 15:07:58` 共 **14 轮 `question`**，13 条 decisions 全部
写入 `.state`，而产出区与模板区**全空**。

#### 根因：修一条矛盾时顺手删掉了终止动作

提交 `c0b7338`（即 2.9.7 第 2 条）把 01 prompt 的落盘指令

> **必须回填模板**：用 `write_file` 写回 `workspace/tasks/{task}/01-brainstorming.md`

改成

> **产出直接写在回复正文里**，不要试图写任务记账文件

当初改它的理由完全成立：`_permission_rules()` 对 `workspace/**` 的 write/edit
一律 deny，那条指令的成功率恒为 0。**但删掉的东西比修掉的更重要** ——
那是 agent 唯一**可执行**的终止动作，而且模板只有 3 个问题槽位，天然带边界。

换上的替代物是一段没有工具调用、没有数量锚点的描述性文字。
「把产出写在正文里」无法被 agent 判定为「已完成」：它每说一句话都在正文里，
于是「我说完了吗」这个问题**没有可执行的答案**，只能继续问。

叠加第二个缺口：`hook-01-02` 只写「≥3 questions」，是**下界**；
而本该提供上界的 `hook-01-01`（「Score < 8 → block Planning entry」）
全仓库检索 `ambiguity` 只有一个字段定义、**零消费方** ——
「判据存在、无人调用」的**第七例**（前六见 2.9.8 与 A2 的 10.6）。

> **判例**：约束只给下界不给上界，等于没有收敛条件。
> 删掉一条「做不到的指令」之前，先问它在流程里承担的是什么角色 ——
> 那条指令做不到，但它**指的方向是对的**。

#### 三处定案

**1. 权限精确放行**（`agents/opencode._stage_file_write_rules`）

那段 `workspace/**` deny 保护的是一块**没人当判据用的区域**：`check_01` 读
`.state` 拿 Gate、读围栏区判产出，**模板区在任何判据里都不出现**。
代价却是砍掉了 agent 的终止动作。现放行 `workspace/tasks/{task}/{stage}.md`
（相对 + 绝对两种写法），排在判据区 deny **之后**（`findLast` 后者优先）。

三条边界：只放行本任务本阶段那一个文件；判据目标（`.state` / `.state.lock` /
`facts/` / `STATUS.json` / `.evidence_key` / `.log` / `.input` / `.context`）
一律不放开；只读阶段（04-review）不放行，判据与 `_tool_switches()` 同源。
任务名或 stage 缺失时返回空列表 —— 拼不出精确 pattern 就**回落全段 deny**，
失败方向选在「拦住」那一侧。

**2. 围栏与 Gate 的事后校验**（`workflow/output_check.check_tamper`）

放开写权限同时暴露了两样东西：产出区围栏与 `## Gate`。现在**只盯这两处，
模板正文一律不管** —— 把「文件被改过」当成违规等于把刚放开的权限又收回去。
真相源全在 `.state`（`output_nonce` 与 `gate`，受 HMAC 覆盖），agent 动不了它，
这是本校验能成立的根据。校验顺序刻意是**篡改先查**：围栏被伪造时，
从围栏里读出的「实质内容」毫无意义（同 A2 `_check_03b` 的「哈希先查、测试后跑」）。

**3. 歧义分数接线**（`workflow/output_check.read_ambiguity_score`）

`hook-01-01` 从装饰数字变成真判据：读产出区的 `歧义分数：<0-10>`，
低于 8 或**读不到**都拦下本阶段。这才是**语义**收敛条件 ——
用户明确否决了「加轮次上限」那条路（上限会把「消除歧义」变成「凑够数」，
且恰好在最需要澄清的复杂任务上最先失效）。

分数只从**产出区**读，不读模板区：模板区现在是 agent 可写的，
拿它当判据等于让 agent 自己给自己打分 —— 2.9.7 已判过同一个错
（`check_02` 拿模板自带标题当判据，判据恒真）。
读不到返回 `None` 而非 `0`：0 是「歧义极高」这个具体结论，不可得是另一回事。

#### 连带修掉的三处

| # | 问题 | 处置 |
|---|---|---|
| 1 | 落点给**相对**路径，agent 的 cwd 是 `repo/<task>`，它会再拼一层（同 2.9.7 的参照系错） | `builder.build` 新增 `{stage_file}` 占位符，注入**绝对**路径 |
| 2 | 2.9.9 第 2 条：02 prompt 没说产出落在哪（`helloworld2` 把 DAG 写进 `PLAN.md`） | 02/03 prompt 补全落点与必写小节；02 的标题名不可改 —— `fact_pack.build_plan` 按标题提取事实 |
| 3 | `hooks/*.md` 里还有 **5 处** `ask_user`（2.9.7 第 1 条只改了模板与 `system.yaml`） | 全部改为 `question`。hooks 全文会被 `_read_hook_rules` 内联进 prompt，所以这些字面量与模板里的错误等价 |

#### 三条判据的重做（DEV-PROTOCOL 1.2）

本轮有三条**既有**判据因为前提变了而必须重做，都已在测试 docstring 里显式声明：

1. `test_prompt_does_not_order_writes_into_guarded_paths` —— 原是「写入动词 +
   `workspace/` 的文本黑名单」，前提是整段 deny。现改为**按真实权限语义
   （`findLast`）解析 prompt 里每条路径**：让写判据区会红，让写阶段文件不会红。
2. `test_full_prompt_contains_no_harness_relative_task_paths` —— 原断言子串
   `workspace/tasks/<task>` 不出现，把**绝对**路径也一并禁掉了，而绝对路径
   恰是正解。现只禁相对写法。
3. `test_stage_termination_anchor` 自身初版读的是 **YAML 模板原文**，
   而 agent 读的是 `build()` 渲染后的完整 prompt。改扫渲染结果后，
   立刻抓出上面那 5 处 hooks 里的 `ask_user` —— 原契约测试从不覆盖 hooks。

另新增 `test_write_instruction_probe_is_not_vacuous`：三个可写阶段若全部
skip，说明上一条判据空转。**「全 skip 的绿」在本项目已出现多次**，
这次把空转本身变成红。

---

### 2.9.11 任务 qqqq 的现场：判据的兑现阶段与能力阶段错位（已修）

上一轮的三处修复在这个任务上生效了 —— 01 落盘成功、歧义分数 9 过闸、
02/03 都写进了正确的阶段文件。但流程在 04 卡死。

现场：`16:20:10` 与 `16:21:39` 两次 `/advance`，输出**逐字相同**：

```text
❌ repo/qqqq/ 下无 README.md
❌ 客观轨硬失败: O6
   这些是程序判定的事实，不是意见 —— 修掉再推进。
error | 硬校验未通过，必须满足所有条件才能推进
```

Gate 四项全签、Route 已定 `05-archive`、pytest 2 passed。唯一拦路的是 O6。
用户重试一次得到一模一样的结果 —— 因为**重试不改变任何输入**。

#### 根因：要求在 04 兑现，能力只在 03 存在

| 环节 | 事实 |
|---|---|
| 03-coding | 唯一同时有 `write_file` 与 `run_command` 的阶段，但 prompt 与 hooks **通篇不提 README** |
| 04-review | `config.roles.reviewer.tools` 无 `write_file`；校验通过后 agent 已退出 |
| O6 | `severity=high` 硬阻断（`objective_check._readme_check`） |

于是没有任何角色能创建那个文件。这不是 agent 偷懒，是**流程设计里没有人
负责这件事**。

> **判例**：一条判据的**兑现阶段**必须与**能力所在阶段**一致，否则它不是
> 质量门禁，是死锁。同型已在 2.9.7 出现过（prompt 让写、硬层禁写），
> 那次错位在权限维度，这次在阶段维度。
>
> 推论：**新增任何硬阻断判据时，必须同时回答「谁在哪一步能满足它」。**
> 答不出来就说明判据放错了阶段。

#### 三处定案（第 3 条由用户拍板）

**1. claims 从模板区也能抽取**（`fact_pack.extract_claims_from_stage_file`）

`qqqq` 的 03 产出里三个文件名**写得明明白白**（`## Files Touched` 下
`main.py` / `test_main.py` / `requirements.txt`），而 `_record_coding_claims`
只解析围栏内的产出区，于是 `.state` 的 claims 为 `null`、O5 记
`unavailable` —— 这正是 2.9.9 第 3 条登记的未修项，根因至此查清。

只读围栏区在当时是对的：那会儿模板区 agent 写不进去（`workspace/**` 整段
deny），能出现在那里的只有占位符。上一轮放行阶段文件、并在 prompt 里**要求**
回填之后，前提就变了 —— 继续只读围栏等于让 agent 按要求做事，然后判它没做。

顺序刻意是**围栏区优先、模板区回落**，且逐字段回落：围栏区由 sw 落盘、
nonce 不可预测，可信度高于 agent 可任意改写的模板区。反过来会让 agent
在模板里写一份好看的清单、在产出里写另一份，而我们取到前者。
占位符由 `_is_placeholder` 挡住 —— 原实现担心的正是这个，但用错了刀：
该挡的是占位符，不是整个模板区。

**2. O6 的失败结论给出下一步**

两次 `/advance` 输出逐字相同的直接原因是文案只说「修掉再推进」，
没说谁去修、修什么（A2 的 10.6 已判过同型）。现在 `reason` 里写明：
让 04 的 agent 创建 README，或返工到 03 补文档。
**注意先后**：这句建议在放开写权限**之前**写就只是空头指示。

⚠️ 写了 `reason` 还不够 —— `check_04-review.sh` 的打印是
`c.get("detail") or c.get("reason")`，而 O6 **两者都有**，于是 `or` 短路，
那句下一步永远不会显示。「产出了正确的信息但没接到出口」是本项目的常见形状
（与「判据存在、无人调用」同族）。现在硬失败项把 `detail` 与 `reason` 都打，
并由 `test_hook_actually_prints_the_next_step` 钉住：
判据要求脚本里 `c.get("reason")` 出现 **≥2 次**，只有 `detail or reason`
那一处不算 —— 那个表达式在 detail 非空时恒短路。

**3. 给 04 的 reviewer `write_file`**（用户拍板）

与「审查者不修改被审对象」有张力，所以那条纪律换了兑现方式 ——
从「整个阶段不能写」收窄成「**写不到判据**」：

- 可写：`repo/<task>/**`（含 README）、`workspace/tasks/<task>/04-review.md`
- 仍 deny：`.state`（Gate / red_witness / claims）、`facts/`、
  `STATUS.json`、`.evidence_key`

真正会让审查失去意义的是 reviewer 能改 `.state` —— 那样它可以自己签 Gate、
把 red_witness 的 `unavailable` 改成通过。那部分一个字节都没放开。

**`design_critic` 不受影响**，仍是纯只读（A8 的 3.6）。它与 04 的单角色回落
`reviewer` 是两个角色；A8/A9 尚未实现，此时顺手放开无人会发现。

**4. 03 的 prompt 与 hooks 明确 README 是 03 的交付物**

新增 `hook-03-07`，prompt 里写清「项目是什么 / 怎么启动 / 怎么验证」
且不留占位符。只做前三条的话，结果是每个任务都要在 04 补一次文档，
而 04 的定位是审查 —— 判据落在 03 才让「兑现阶段 = 能力阶段」成立。

#### 按 1.2 重做的两条判据

`test_readonly_stage_does_not_allow_write` 与
`test_readonly_stage_still_cannot_write_stage_file`（后者是上一轮我自己写的）
都把「04 只读」当成不变量。不变量现改为「04 写不到判据」，两处均已在
docstring 里显式声明重做理由。

#### 顺带发现（未修，登记）

日志 `16:11:48` 有一条 `edit` 的目标是 `01-brstorming.md`（少了个 `a`）——
agent 自己拼错了文件名。edit 静默失败，agent **没有感知到**，继续宣布 01
完成。本次未影响推进（它随后用正确路径写了一次），但形状与 2.9.7 第 2 条
同源：**写失败没有反馈给 agent**。若哪次拼错的是唯一那次写入，
就会重演「以为自己写了」。

---

### 2.9.12 任务 rrr 的现场：产出有两个落点，判据只量一个（已修）

上一轮的修复在 `rrr` 上生效了 —— 01 过闸、02 的 agent 交出了一份**优质**
规划：7 个任务的 DAG（每个都有 `Do` / `Verify` / `Deps`）、完整的
`## Test Strategy`、`## Tech Detail` 里 4 个数据模型与 7 个 files-to-touch。

然后它被判为空转：

```text
18:34:37 agent 🔧 write {'filePath': '.../rrr/02-planning.md', ...}
18:35:13 agent 🔧 question {'questions': [{'question': '规划已完成，请确认...
18:35:30 agent 请在 TUI 中输入 `/advance` 推进到 03-coding 阶段。   ← 39 chars
18:35:39 sw    ❌ 02-planning 的产出区没有实质内容（仅 30 字符，至少需要 80）
18:35:39 error 硬校验未通过，必须满足所有条件才能推进
```

#### 根因：判据只量围栏区，而产出可能落在模板区

链条：

1. 上一轮放行了阶段文件写权限，02 的 prompt **要求** agent 用 `write` 回填
   模板区 —— 它照做了，1189 字符的实质内容全在模板区；
2. prompt 同时说「同一份内容也请留在你的回复正文里」，但 agent 在 `write`
   之后又调了一次 `question` 确认推进；
3. `question` 之后它的最终回复只剩一句收尾话，**围栏区只收到这一句**；
4. `check_output` 只量围栏区 → 30 字符 < 80 → 拦。

01 阶段侥幸没挂：那次 agent 在 `write` 之后没再调 `question`，最终回复
带着完整结论（围栏内 122 字符）。所以这不是运气问题 ——
**判据的取样点比产出的落点少一个。**

> **判例**：同一份产出有两个可能落点时，判据只量其中一个，就会把
> 「写在另一处」误判成「没写」。
>
> 这与 2.9.11 第 1 条（claims 只读围栏区、声明写在模板区）是**同一个 bug
> 的第二次出现**。那次修了 `extract_claims_from_stage_file`，
> **没有回头检查 `check_output` 有没有同样的毛病**。
>
> 推论：修掉一个「取样点少于落点」的 bug 之后，必须把**所有**读同一份
> 文件的判据都过一遍。放行写权限这类变更会一次性给所有判据换掉前提。

#### 修法：围栏优先、模板回落，但回落必须先减去模板自带的内容

`_substance_report` 与 `extract_claims_from_stage_file` 现在同一套语义：
围栏区够就用它（既有行为不变），不够才回落看模板区。

**回落不是「量整篇」**，这是本次最容易改坏的地方：02 模板自带三个二级标题
与 `- **Method**: unit / integration / manual` 这类样板，去噪后合计已超过
阈值 80 —— 量整篇会让判据**恒真**，正是 2.9.7 判过的那个错
（`check_02` 拿模板自带的标题当判据）。

所以 `_added_lines` 拿 `templates/{stage}.md` 逐行相消，只算 agent 真正
添进去的行；Gate 区（由 `render_gate_section` 从 `.state` 渲染）也排除掉 ——
把 harness 自己的输出算成 agent 的产出，等于判据给自己打分。

另外两条边界：**不相加**（两段各自不达标的碎片不许凑够阈值，「产出够不够」
问的是有没有一份完整交付）、**阈值仍是 80**（本轮修的是「看哪里」，
不是「看多严」，A6 的 9.3）。

#### 实测证据

| 判据 | 结果 |
|---|---|
| `bash hooks/check_02-planning.sh rrr` | ❌ →  ✅「模板区（agent 回填）有实质内容（1189 字符）」 |
| 12 个存量任务阶段（6 任务 × 01/02）新旧逐一对照 | 11 个判定**分毫未动**，只有 `rrr` 的 02 翻绿 |
| MockAgent 复现（`SW_MOCK_PLANNING_TEMPLATE_ONLY=1`） | 写测试时红在「仅 30 字符」，与现场日志逐字相同 |

复现走 MockAgent + `StageRunnable._save_stage_output`，与生产同一条落盘路径 ——
要复现的是「agent 把产出写去了另一处」这个行为，而不是我们对它的想象。

---

### 2.9.13 等人回答的上限：两个语义挤在一个常数里（已修）

用户反馈：「sw init 启动的任务，如果出现 ask user 的场景，把超时时间设置成
30 分钟吧，这个应当是一个参数，我常常会看不到。」

`QUESTION_TIMEOUT` 原为 180s。超时的后果不是「慢」而是**决策被替换** ——
harness 会 `reject_question()` 让 agent 自行决定，而 01 阶段的全部意义就是
消除歧义，让它自己猜等于取消这个阶段。

#### 为什么不能只改一个数字

等人处在超时链最内层，每层都靠「比外层早醒」换取一次主动处置：

    StageRunnable.FIRST_RESPONSE_TIMEOUT   等 agent 首轮回复
      └── OpenCodeTransport.CHAT_TIMEOUT   单轮 POST /message（真实 HTTP 超时）
            ├── IDLE_TIMEOUT               空闲判据
            └── QUESTION_TIMEOUT           等真人回答

把最内层单独抬到 1800s 是**假的**：HTTP 在 900s 先断，`reject_question`
退化成死代码，用户在第 15 分钟作答时回答已无处可投。

#### 真正的根因：`IDLE_TIMEOUT` 混了两个语义

`IDLE_TIMEOUT` 问的是「agent 卡住了吗」，判据是有没有新的工具调用。而 agent
等人时**本来就零活动** —— 那是在等人，不是卡住。此前靠
`IDLE_TIMEOUT(300) > QUESTION_TIMEOUT(180)` 这个数值关系掩盖了混淆；
`test_idle_based_timeout.py` 里那条 `test_idle_timeout_leaves_question_wait_intact`
就是这个障眼法的化石。等人上限一抬到 30 分钟，它必然破。

> **判例**：当「放宽 A 就必须放宽 B」时，先问 A 与 B 是不是同一件事。
> 本例中把 `IDLE_TIMEOUT` 一路抬到 30 分钟以上"也能让测试变绿"，
> 代价是真死锁多沉默 25 分钟 —— 拿故障暴露能力换用户便利。
> 正解是把等人期从空闲计时里**摘出去**（`transport.waiting_for_human()`），
> 于是两个数字不再需要互相迁就：墙上时钟放宽到 33 分钟，
> 而「多久没动静算卡住」仍然是 5 分钟。

#### 转绿过程中撞出的真实缺陷（参数化的隐藏代价）

把 `QUESTION_TIMEOUT` 从类属性改成读配置的属性之后，
`agent.QUESTION_TIMEOUT = 0.05` 这种**测试压缩等待的标准手法静默失效**。
`test_opencode.py::test_question_timeout_rejects_instead_of_hanging` 于是真的
开始等 30 分钟：`tests/unit` 从 5 分钟涨到 15 分钟以上仍未结束。

> **判例**：把一个常量改成「从配置读」，会一次性取消**所有**既有的覆写点。
> 症状是套件变慢而不是变红 —— 没有任何断言失败，极难归因。
> 因此参数化时必须保留实例级逃逸口，并用判据钉住它
> （`test_instance_override_still_wins`，按 DEV-PROTOCOL 1.2 显式声明为
> 冻结后新增）。

#### 按 1.2 重做的一条判据

`test_chat_timeout_is_bounded_on_both_sides` 的上界从 `< 1800.0` 放宽到
`<= 3600.0`。原上界的依据是「F11 里让死锁静默 26 分钟的那个值」，但那次静默
的真正原因是当时**只有**墙上时钟这一层 —— `IDLE_TIMEOUT` 与
`permission.asked` 订阅都还不存在。这个职责已经移交，继续用 1800 卡住这一层
等于让一个已卸任的判据阻止新需求。

**放宽是有对价的**：同时新增 `test_idle_timeout_still_bounds_deadlock_silence`
钉住 `IDLE_TIMEOUT <= 300`，确保两者不会一起变大 —— 那才是真的放宽标准。

#### 落点

| 项 | 值 |
|---|---|
| `harness.ask_user_timeout`（新参数，写进 config.yaml） | 1800s，默认 30 分钟 |
| `transport.effective_chat_timeout()` | `max(CHAT_TIMEOUT, 等人 + 180)`，随配置自动跟上 |
| `IDLE_TIMEOUT` | **不变**，仍 300s |
| 两条 ask_user 路径（opencode / Toolbox） | 统一读同一个配置（原先 180 与 300 各写一处） |

---

### 2.9.14 任务 welll 的现场：两侧结论相反，因为跑在不同的地方（已修）

用户反馈：「依旧跑出了 bug，还是 coding 阶段红绿见证的问题？」

```text
agent 🔧 bash {'command': 'cd backend && source venv/bin/activate && python -m pytest tests/test_main.py -v'}
agent 商城应用开发完成。已完成所有7个任务：**后端 (17个测试全部通过)**
sw    ❌ 未能见证有效的红（退出码 2）
sw       造红：收集期就报错（ImportError / SyntaxError），断言从未被执行。
ERR   硬校验未通过，必须满足所有条件才能推进
```

本机实测同一份代码：

| 怎么跑 | 结果 |
|---|---|
| `cd repo/welll/backend && ./venv/bin/python -m pytest tests -q`（agent 的跑法） | **17 passed** |
| `cd repo/welll && python3 -m pytest -q`（见证的跑法） | **3 errors**，退出码 2 |

两边都没说谎。`agent` 的项目是 `backend/` + `frontend/` 布局，测试写
`from main import app`，只有 cwd 在 `backend/` 时才成立。

#### 这是同一个坑的第四次出现

前三次都记在 `lib_run_tests.sh` 与 `red_witness.py` 的注释里：T2（src-layout
缺 `PYTHONPATH=src`，agent 报 25 passed、门禁报失败）、T3（依赖装在 `.venv`
里，门禁报缺 pandas）、helloworld（两侧测试面判据错位，20 个测试一个没跑）。

三次的处置都是「在顶层再补一种探测」。这次的形状说明补的方向不够：

> **判例**：当 harness 与 agent 对同一份代码给出相反结论时，先问
> 「我们是不是在不同的地方执行」，而不是先怀疑代码。
> 前三次修的是「少认了一种标记」，这次错的是**判据假定项目根 == `target_dir`**。

#### 必须两侧同时修，否则误判会翻成假绿

见证侧修好后，钩子侧实测 `repo/welll` 仍是
`ⓘ 未发现 pytest 测试面（未执行 pytest，非『通过』）`、`rc=0`。而 A2 的所有
让路路径都把测试判定「交回 `run_project_tests`」——
只修见证侧的结果是**从「误拦好代码」翻成「放过坏代码」，比原 bug 更糟**。
`test_hook_subdir_layout.py::test_03_gate_blocks_failing_tests_in_subdir_layout`
钉住这条底线（该判据在修钩子前实测拿到 `[Hard Check] ✅ 通过`）。

#### 转绿过程中撞出的两个真实缺陷

两者都是**新旧行为逐任务对照**跑出来的，不是测试报的 —— 这个手法值得保留。

1. **`Path.resolve()` 会解掉 venv 的符号链接。** 为让解释器路径绝对化而用了
   `resolve()`，`backend/venv/bin/python` 是指向 `python3.12` 的软链，被解析成
   系统解释器后 `site-packages` 整个失效，`welll` 从 17 passed 退回退出码 2。
   虚拟环境靠的正是「从哪个路径启动」，这条链接不能跟：改用 `os.path.abspath`。
2. **相对 `target_dir` + 切换 cwd = unavailable。** `.state` 里存的是
   `repo/welll`，解释器路径也就是相对的，而 cwd 现在是 `repo/welll/backend`
   —— 到那里相对路径不存在，退出码 -1。`lib_run_tests.sh` 早就显式处理过
   同一件事，Python 侧漏了。

#### 顺手修实的一处文案

`repo/newworld` 从退出码 2 变成 4（它的 `conftest.py` 写
`from main import app` 而 `backend/main.py` 根本没写）。拦对了，但理由说的是
「无测试：未采集到任何测试节点结果」—— 测试文件有 3 个，是 conftest 塌了。
误导性的拒绝理由会把人推去补测试，而该补的是 `main.py`。新增
`EXIT_USAGE_ERROR = 4` 单独成句。

#### 落点

| 项 | 内容 |
|---|---|
| `red_witness.resolve_pytest_root()`（新） | 仓库根自己没有可收集测试时，往下找**一层**；命中**恰好一个**才切换，否则留在根 |
| `red_witness._run_in_target()` / `_pytest_env()` | 在探测出的项目根上跑；`PYTHONPATH=src` 也跟着看项目根 |
| `red_witness._project_python()` | 也找项目根下的 venv；返回 `abspath` 而非 `resolve()` |
| `hash_test_files()` | **不变**，仍以 `target_dir` 为基准 —— 冻结路径是 `.state` 里的既有记录 |
| `lib_run_tests.sh::_pytest_root()`（新） | 直接调 `resolve_pytest_root`，两侧判据同源而非各写一份 |
| `_has_pytest_surface` / `run_project_tests` / `_project_python`（shell） | 一律查项目根；pytest 在项目根上跑 |

探测刻意收得很窄：只有根上没测试才往下找、只找一层、跳过 `_IGNORED_DIRS`、
命中多个就退回根（monorepo 通常在根上配了 `pytest.ini` 指路）。宁可退回既有
行为也不猜。

---

### 2.9.15 任务 testNew 的现场：同一个坑的第三侧 —— 事实包没跟上（已修）

用户执行 `sw init` 起的新任务，04 阶段推进时被硬拦。现场（`workspace/tasks/testNew/.log`）：

```text
15:16:45 sw    运行 pytest (repo/testNew/backend)...
15:16:45 sw      使用项目虚拟环境: .../repo/testNew/backend/venv/bin/python
15:16:45 sw        ================ 9 passed, 17 warnings in 3.10s ================
15:16:45 sw    [Objective Track] 客观轨判定...
15:16:45 sw      ❓ O2 tests: unavailable target_dir 下没有 pytest 语义的测试面
15:16:45 sw      ❌ O3 test_validity: fail collected=0（无测试面）
15:16:45 sw    ❌ 客观轨硬失败: O3
```

**同一个 hook、同一份代码、同一次运行**：shell 那侧报 9 passed 并打印了
`backend/venv` 的解释器，客观轨报「无测试面」并以 O3（severity=high）硬拦。
两边都没说谎 —— 只是跑在不同的地方。

#### 根因：`resolve_pytest_root` 有三个消费方，2.9.14 只接了两个

这是 `welll` 那个坑的**第三侧**。形状逐字不变：**判据假定 `target_dir`
就是项目根**。2.9.14 修好了见证侧与钩子侧，`fact_pack` 这一侧留在原地：

| 位置 | 2.9.14 后的状态 | 后果 |
|---|---|---|
| `fact_pack._has_pytest_surface()` | 量 `target_dir` | 报「无测试面」 |
| `fact_pack.collect_tests()` 的 cwd | `target_dir` | 收集期 ImportError |
| `fact_pack._pytest_pythonpath()` | 看 `<repo>/src` | 子目录布局下失准 |
| `fact_pack._project_python()` | 只找 `target_dir` 下的 venv | 解释器对不上 |

`objective_check._tests_checks` 的 docstring 明写「刻意不自己跑 pytest，
复用采集层」—— 采集层错位，O2/O3 就一起错位，且 O3 是硬阻断。

**为什么必须修这一侧**：agent 无从下手。它的测试确实存在、确实全绿，
失败信息却说「无测试面」。这与 2.9.11 的 O6 死锁同构 —— 判据要求的东西，
没有任何角色能通过修改代码来满足。

#### 第二层：单元全绿 ≠ 机制接通（第六次）

三处路径改完后单元测试 9 passed 全绿，但在 `repo/testNew` 上实测仍是：

```text
python: repo/testNew/backend/venv/bin/python
raw:    无法执行 repo/testNew/backend/venv/bin/python -m pytest --version
parse_status: unavailable
```

解释器**找对了**，但 `_project_python` 返回相对路径，而子进程 cwd 已切到
`backend/`，相对路径到那里不存在。整包记 `unavailable` —— 比原来的误判更糟
（`unavailable` 不算通过，O3 照旧拦）。

单元测试照不到这层：fixture 用的是 `tmp_path` 绝对路径，而 `.state` 里存的
`target_dir` 是 `repo/testNew` 这种**相对**路径。见证侧 `_project_python` 的
docstring 早已写明「返回绝对路径」并记录了实测后果，事实包侧漏了同一句。

这条只能靠「在真实任务目录上跑一遍」发现。判例：**涉及路径的修复，单元测试
必须额外覆盖相对 target_dir 的形态**，否则真实链路与测试链路的前提不同。

#### 落点

| 项 | 内容 |
|---|---|
| `fact_pack._has_pytest_surface()` | 量 `resolve_pytest_root(target)` 而非 target 自身 |
| `fact_pack.collect_tests()` | cwd 与版本探测都在项目根上；`pytest_root` 落盘便于定位 |
| `fact_pack._pytest_pythonpath()` | `PYTHONPATH=src` 相对项目根算 |
| `fact_pack._project_python()` | 也找项目根下的 venv；返回 `os.path.abspath` 而非 `resolve()` |
| `raw` 文案 | 由「target_dir 下没有…」改为打印实际探测到的项目根 |

三条边界刻意不放宽：平铺布局结论完全不变；真的没有测试仍报
`no_test_surface`（`unavailable` 与 `pass` 继续分开）；monorepo 多候选时
留在仓库根，与见证侧同源。

#### 同型清扫：登记一处未修

`sw_lib/probe/baseline.py::is_surviving()` 是这个形状的**第四个实例** ——
它调 `run_tests(target_dir)`，而 `run_tests` 是底层执行器、按传入目录跑，
不自己解析项目根。子目录布局下它会得到退出码 5/-1 并记 `unavailable`。

**本轮不修**，理由：它只被 `scripts/probe/b0_capture.py` 与
`b0_survival_screen.py` 调用，不在 04 门禁链路上，错位的后果是 B0 采样把
样本记为「未测量」（分母不被灌大，方向是保守的），不会误拦任何任务。
修它需要一并决定「变异注入的路径基准是否跟着项目根走」，那是 A11 的口径问题，
不宜夹在本次修复里。

登记于此，以免成为第八个「判据存在、无人回头」的遗留。

---

### 2.9.16 任务 8090 的现场：失败信息把人指向一条走不通的路（已修）

用户 `sw init` 起的 BBS 论坛任务（Flask），卡在 03-coding。
现场（`workspace/tasks/8090/.log` 尾部）：

```text
❌ 未能见证有效的红（退出码 2）
   造红：收集期就报错（ImportError / SyntaxError），断言从未被执行。
   红必须是断言失败，不是 import 失败。
   若本轮确实无法见证红（例如实现已先落盘），走这条显式放弃：
     python3 -m sw_lib.workflow.red_witness 8090 --abandon-witness '<为什么见证不到红>'
```

实测 `repo/8090`：`.venv` 不存在，flask 未安装，
`python3 -m pytest tests/ -q` → `ModuleNotFoundError: No module named 'flask'`，
`3 errors during collection`，rc=2。**门禁的拦截本身是对的**，
这次不是判据量错了地方。

#### 成因链

1. `10:07:15` agent 执行 `python3 -m venv .venv && ... && pip install ...`，
   下一条日志是 `10:14:37`，中间空 7 分 22 秒。`HOOK_TIMEOUT_MINUTES = 2.0`
   把它砍掉了，venv 没建成。
2. agent 没收到失败信号，交出「23 个测试全部通过」的产出。
3. 门禁跑 pytest，收集期 ModuleNotFoundError，rc=2 → 判「造红」。

#### 死锁的形状：出路能执行，但解决不了这个问题

实测走了日志给的那条出路：

```text
$ python3 -m sw_lib.workflow.red_witness 8090 --abandon-witness '...'
[Red Witness] 已放弃本轮见证（第 1 次）
   phase 已回到 none —— 测试判定交回常规门禁。

$ bash hooks/check_03-coding.sh 8090
⚠️ Red 见证未发生（unavailable）
运行 pytest (repo/8090)...
❌ pytest 失败
    ModuleNotFoundError: No module named 'flask'
```

**两条路通向同一堵墙。** 放弃见证后判定交回常规门禁，
常规门禁跑同一份 pytest，撞同一个 ModuleNotFoundError。

与 2.9.11（O6 死锁）**不同型**：那次是没有任何角色能满足判据；
这次判据能被满足（装上依赖就见证得到红），但**失败信息指错了方向**。
按失败信息三要件，缺的是第三条：**一个真实角色在真实阶段能执行的下一步**。
此处应为「装依赖」，而门禁说的是「改 import」和「放弃见证」。

> **判例**：拒绝给出的下一步，必须能**解决导致这次拒绝的那个原因**。
> 「有一条能执行的命令」不等于「有出路」—— `--abandon-witness` 跑得通，
> 但它治不了缺依赖。这是 2.9.7「拦住一条路而不给替代路径」的**变体**：
> 那次是没给路，这次是给了一条通向同一堵墙的路。

#### 根因：用退出码去猜原因

退出码 2 只说明「收集期塌了」，说不出塌的原因。而
「缺第三方依赖」与「自写模块 import 错了 / 语法错」性质不同，
需要的下一步动作也不同 —— 前者装依赖，后者改代码。
判据把两者合并成一句「造红」，于是必然有一半的情形被指错方向。

同形先例就在同一个函数里：退出码 4 的分支注释明写「conftest 一塌
pytest 就以 4 退出……落到兜底分支会说『无测试』，把人推去补测试 ——
该补的是 main.py」。本次是**同一形状的第二个实例**。

#### 修法：加一个独立的事实源，不从退出码推原因

| 落点 | 改动 |
|---|---|
| `red_witness.declared_dependencies()` | 新增。读 `requirements*.txt` 与 `pyproject.toml`（PEP 621 + poetry） |
| `red_witness.missing_dependencies()` | 新增。在**跑测试的那个解释器**里按发行名查 `importlib.metadata` |
| `red_witness.install_hint()` | 新增。给出落在项目根、用项目解释器的 `pip install` |
| `classify_exit_code()` | 加 `missing_deps` 参数。退出码 2 **与 4** 都据它分流 |
| `_check_03a()` | 缺依赖时给 `install_hint`，**不给** `--abandon-witness` |
| `lib_run_tests.sh::_diagnose_missing_deps()` | 新增。pytest 失败时补同一诊断 |

两个设计决策：

1. **只读声明，不从 ModuleNotFoundError 的模块名反推包名。**
   导入名与发行名经常对不上（`python-dotenv` → `dotenv`、`Pillow` → `PIL`），
   反推会给出一条装不上的命令 —— 那比不给下一步更糟，因为它看起来可执行。
2. **依赖要在跑测试的那个解释器里查**（`_project_python`），
   否则会出现「pytest 在 .venv 里跑、依赖却拿 harness 的解释器查」这种
   新的错位 —— 那正是 2.9.14 那个坑换一副面孔。

#### 两侧一起修

`7ee1928` 记过这个陷阱：只修一侧会把「误拦」翻成「放过坏代码」。
本次的形状是另一种 —— 见证侧修好了，但 `--abandon-witness` 之后判定
交回 `run_project_tests`，那边仍只回显原始 pytest 输出。
出路走到头还是看不懂为什么失败，死锁只是从一堵墙挪到另一堵墙。
`check_04-review.sh` 复用同一个 `run_project_tests`，因此一并覆盖。

#### 同型清扫

| 候选 | 判定 |
|---|---|
| `classify_exit_code` 退出码 4 分支 | **同型，本次一并修**（conftest 里 `import flask` 实测 rc=4） |
| `lib_run_tests.sh` 常规门禁侧 | **同型，本次一并修** |
| `check_04-review.sh` | 复用 `run_project_tests`，已覆盖 |
| `_check_03b` / `verify_green` | **不同型**：03b 的前提是 03a 已见证到红，依赖那时必然已装好 |
| `fact_pack.collect_tests` | **不同型**：它不判「造红」，缺依赖时如实记 `unavailable`（❓），未把人指向错误的下一步 |
| `probe/baseline.py::is_surviving` | **不同型**：同样不做原因诊断，缺依赖记 `unavailable`。它在 2.9.15 已因另一个形状（`resolve_pytest_root`）登记延后，此处不重复登记 |

#### 未修的部分（登记）

**`HOOK_TIMEOUT_MINUTES = 2.0` 砍掉装依赖命令这件事本身没修。**
本次修的是「被砍之后 harness 说什么」，不是「不要砍」。理由：
超时上限的存在是对的（钩子挂住会让 CLI 永久等待），
而装依赖耗时不可预估，放宽到多少都可能不够。
门禁文案里因此明说了「装依赖可能耗时超过钩子上限；若被中断，
在终端里手动装完再 /advance」—— 把不可抗因素交代清楚，
而不是假装它不存在。

更彻底的修法是**让 agent 知道自己的命令被超时砍了**（现在它收不到信号，
所以才会交出「23 个测试全部通过」）。那是 toolbox 层的改动，
影响所有工具调用，不宜夹在本次修复里。**登记于此**，
以免成为下一个「判据存在、无人回头」的遗留。

---

### 2.9.8 U0-1 的架构真相：不同 agent 后端的可控粒度不同，同一纪律的强制力也不同

2.5 已记「`Toolbox` 的白名单对 opencode 无效」，但只说了「无效」，
没说清**我们究竟还剩哪些旋钮**。任务 `helloworld2` 又一次因 `bash` 绕过
而卡死（A2 的 10.6 倒数第二行）之后，本机实测把这件事查清了：

**opencode 只调它自己二进制里的工具。** `sw_lib/tools/toolbox.py` 的
`Toolbox` 仅被 `agents/gemini.py`（2 处）与 `core/deploy_orchestrator.py`
（1 处，Python 内直调）引用，**opencode 路径零引用**；而 `config.yaml` 的
五个角色 `agent` 全是 `opencode`。也就是说 `_PROTECTED_FILES`、
`WriteFileTool._is_protected` 这类函数体内的约束，在当前生产配置下一行未生效。
我们**无法**给 opencode 递一个自己实现的 `write_file` 让它调用。

对 opencode 我们只剩两个旋钮：

1. **工具开关**（`opencode.py:_tool_switches()`，`TOOL_MAP` 里
   `run_command → bash`）—— 粒度是「给 / 不给」，没有中间态；
2. **session 权限规则** —— 路径级 allow/deny，对 `write`/`edit` 有效，
   **对 `bash` 无效**：它的参数是整条 shell 命令，没有结构化路径字段可匹配。

**可控粒度分三层**（本机实测各自的 CLI）：

| 后端 | 工具执行方 | 可控粒度 | 能否约束「怎么用」 |
|---|---|---|---|
| 纯模型 API（如 `GeminiAgent` + `Toolbox`） | **我们** | 函数体我们写 | 能。`WriteFileTool._is_protected` 这类检查真实生效 |
| Claude Code | 它自己 | **参数级**模式匹配：`--allowedTools "Bash(git *) Edit"`、`--tools ""`、`--permission-mode plan`、`--strict-mcp-config` | 部分能。可以只放行 `Bash(git *)` |
| opencode | 它自己 | 工具级开关 + 路径级规则 | **不能**。`bash` 一旦给出，写文件无从拦截 |

**这条的意义超出 A2**：任何依赖「agent 只能经受控入口改文件」的机制
（A2 的红绿见证、A11 的变异探针、A0 的写入保护），其强制力**取决于后端**。
同一份 prompt 纪律在 Gemini 路径上是硬约束，在 opencode 路径上只是建议。
设计文档里写「禁止 X」时必须同时问一句：**这条在当前后端上由谁强制？**
答案是「没人」时，就应当按 A2 那样改成事后检测 + 如实记录，
而不是留一条拦不住的规则假装它是约束。

**长期方向（用户明确，未展开）**：MCP。从 opencode 1.18.20 二进制挖到的
配置形态是 `{"mcp": {"<name>": {"type":"local","command":[...],
"environment":{}}}}`（stdio + JSON-RPC，**不需要 web server**），
另有 `{"type":"remote","url":...}` 走 HTTP + 完整 OAuth；落点是 worktree 下的
`opencode.json` / `.opencode/opencode.json`。**但 MCP 只能加工具、不能替换
自带的 `write`/`bash`** —— 要让我们的受控入口成为唯一路径，仍须先关掉自带的，
而关掉之后问题本已解决。因此 MCP 的价值在于「给 agent 更强的受控能力」，
不在于「补上这个洞」。

---

## 3. 设计

分三层。第一层解决完整性，第二层解决可检出性，第三层收窄攻击面。

### 3.1 第一层：`.state` 完整性（纯本地改动，无外部依赖）

**D0-1　移除自动迁移回写**（修 2.1）

`read_state` 遇到解析失败时**不再回写**。三种归宿：

- 内容为空 → 返回 `{}`（与现状一致）。
- 合法 JSON → 正常返回。
- 其余 → 返回 `{"_corrupted": True, "_raw_path": ...}` 并记录日志，
  **不填充默认 stage**。调用方必须显式处理，不得静默当作新任务。

旧格式迁移移出读路径，改为一次性脚本 `scripts/migrate_state_format.py`。
读路径里的自动迁移是「顺手兼容」，代价是把损坏合法化 —— 不值得。

> **不填充默认 stage** 是关键。现状 `:69`/`:75` 的默认值填充，
> 把「读不出来」伪装成「处于第一阶段」，这是最恶劣的一种静默降级。

**D0-2　原子写**（修 2.2）

`write_state` 改为：同目录建临时文件 → 写入 → `flush` + `os.fsync`
→ `os.replace(tmp, target)` → `fsync` 父目录。
同分区 `os.replace` 是原子的，读者永远看到完整的旧版或完整的新版。

**D0-3　串行化 read-modify-write**（修 2.3）

新增 `update_state(name, mutator)`：持排他文件锁（`fcntl.flock`）
→ 读 → 调用 `mutator(state)` → 原子写 → 释放锁。

把 39 处 `read_state` + 改 + `write_state` 的调用点迁到这个入口。
迁移可分批，但 A2/A3/A6/A9/A10 新增的写入点**必须**用它。

> 锁文件用 `.state.lock`，与 `.state` 分离 —— 否则原子替换会把锁的
> inode 换掉，持锁方失去互斥。这一点容易漏，写测试时专门验。

### 3.2 第二层：证据可检出（HMAC）

**D0-4　划定证据字段**

`.state` 中以下字段为「证据」，只能由 harness 写：
`red_witness`、`facts`、`review`（含 `baseline_sha`）、
`objective`、`route`、`archive_report`。

其余字段（`stage`、`stage_idx`、`stage_status` 等）是运行状态，不签名。

**D0-5　HMAC 签名**

新增 `sw_lib/core/evidence.py`：

- `sign_evidence(payload) -> str`：对证据子树的规范化 JSON
  （`sort_keys=True`，分隔符固定）做 HMAC-SHA256。
- `verify_evidence(state) -> VerifyResult`：三态返回
  `valid` / `tampered` / `unsigned`。

签名存 `.state` 的 `_evidence_sig`。校验点：A2 准出、A6 客观轨入口、
A9 仲裁前、A10 归档。**`tampered` 与 `unsigned` 都不算通过**
（与 A6 的 3.3「禁止找不到就跳过」同构）。

密钥管理，三条硬约束：

1. 落 `config/.evidence_key`，权限 `0600`，加入 `.gitignore`。
   不存在则首次运行生成。
2. **不放环境变量。** 依据 2.6 —— `opencode.py:130` 的
   `os.environ.copy()` 会把它交给子进程，`bash` 一条 `env` 就读到。
3. `_load_env()` 中显式 `env.pop("HARNESS_EVIDENCE_KEY", None)`,
   防止将来有人图方便加了环境变量后静默泄露。
   现有 `env.pop("NODE_EXTRA_CA_CERTS", None)`（`opencode.py:148`）
   已是同一模式的先例。

> **HMAC 挡不住什么，必须说清楚**：agent 有 shell，能读到
> `config/.evidence_key`（它在 ROOT 内，opencode 的 `bash` 可达）。
> 因此 HMAC 防的是**误改与顺手改**，不防**定向攻击**。
> 定向攻击只能靠 3.3 的隔离与容器化。
> 把 HMAC 说成「不可伪造」是过度承诺。

### 3.3 第三层：收窄攻击面

**D0-6　opencode 侧（当前唯一真实路径，但有效性未经证明）**

> ⚠️ 依据 2.7 的实测，本项**降级为尽力而为**：规则下发可以做对，
> 但「deny 真的阻止写入」尚未被验证。**不得**以本项为由
> 声称证据已受保护 —— 那个承诺由第二层兑现。

`_tool_switches()` 的返回类型从 `Dict[str, bool]` 扩展为携带 pattern 的规则表，
在 allow 规则之后追加 deny 规则（依据 `findLast` 语义，后者优先）：

```text
[ {permission:"write", pattern:"*",  action:"allow"},
  {permission:"write", pattern:"**/.state",       action:"deny"},
  {permission:"write", pattern:"**/workspace/**", action:"deny"},
  {permission:"edit",  pattern:"**/.state",       action:"deny"},
  {permission:"bash",  pattern:"*",  action:"allow"} ]
```

四条纪律：

- **只用 `allow` / `deny`，不用 `ask`**（依据 2.6：无人应答，会死等）。
- `bash` 无法按路径约束，因此 deny 规则**只覆盖 `write` / `edit`**。
  `bash` 的漏洞由 3.2 的校验兜底，不假装堵住了。
- **规则必须在 `POST /session` 创建时一次性带入，禁止用 PATCH 增量下发**
  （依据 2.7.2 第 1 条实测：PATCH 是 merge，累积会让 deny 被后续 allow 覆盖）。
- pattern 的 glob 语义**仍未验证**（assert 端点不可用于验证，见 2.7.2 第 2 条）。
  因此规则同时覆盖多种写法（`.state`、`**/.state`、`**/workspace/**`），
  宁可冗余。**不得声称已确认匹配语义。**

**D0-7　Toolbox 加固（服务 gemini 与未来自研 agent）**

虽然当前不在主路径上，仍须修 —— 否则切回 gemini 时缺口全在。

| 项 | 改动 |
|---|---|
| 2.4 第 4 条 | **移除 `restricted` 参数**。保护策略由 harness 决定，不作为 agent 可传的入参，description 里同步删除 |
| 2.4 第 1 条 | `_PROTECTED_FILES` 从「匹配文件名」改为「匹配解析后的绝对路径前缀」，纳入 `workspace/`（含 `facts/`、`evidence/`、`.state`、`STATUS.json`） |
| 2.4 第 2 条 | 删除命令字符串匹配。改为：`run_command` 的 `cwd` 与 argv 中出现的路径参数逐个做前缀校验 |
| 2.4 第 6 条 | `cwd` 复用 `_safe_path` 的 ROOT 前缀校验；越界直接拒绝 |
| 2.4 第 3、5 条 | 白名单移除 `sh` / `bash` / `zsh`。`python` 保留（跑测试需要）但**不视为安全边界** —— 这一点写进注释，避免后人误以为白名单是防线 |

> 白名单从来不是安全边界，它是**误操作护栏**。文档里必须这样定性，
> 否则下一个人会基于「有白名单」这个错觉做设计。

---

## 4. mock 模式

`MockAgent` 是 CI 主力。签名机制若硬失败会挂掉全部 mock 测试。

处置：mock 模式（`is_mock_agent()`，`config.py:250`）下照常签名 ——
HMAC 是纯本地计算，无外部依赖，没有跳过的理由。
**但密钥固定为测试常量**，避免 CI 每次生成新密钥导致夹具失效。

> 这里刻意不给 mock 开后门。A6 的 3.3 要求「找不到不等于跳过」，
> 同一条纪律适用于我们自己。

---

## 5. 与其他任务的接口

| 任务 | 接口 |
|---|---|
| **A2** | `red_witness` 经 `update_state` 写入并纳入签名。A2 准出前先 `verify_evidence`，`tampered` → 拒绝 |
| **A3** | 事实包生成器只用 `update_state`；`facts/` 目录纳入 Toolbox 保护与 opencode deny |
| **A5** | 并行 reviewer 子节点不直接写 `.state`（A5 已定），汇聚后由仲裁器单次 `update_state` 落盘。锁提供第二层保险 |
| **A6** | 客观轨入口先校验签名；`unsigned` / `tampered` 计入 `unavailable`，按最严标准处理 |
| **A9** | Route 决策写入纳入签名 |
| **A10** | 归档报告读签名状态；签名无效时报告中该项标 ❓ 而非 ✅ |
| **A11** | 变异探针可复用 `verify_evidence` 作为「篡改能否被检出」的元测试入口 |

---

## 6. 实施顺序（内部）

1. ~~实测 opencode pattern 语义~~ —— **已完成，结论见 2.7**。
   产出：PATCH 是 merge 语义（改用 `POST /session`）；
   assert 端点不可用作判据；deny 有效性未验证 → D0-6 降级。
2. D0-1 + D0-2：两者耦合，同批改，一起测。
3. D0-3：`update_state` 入口 + 锁文件独立性测试。
4. **D0-5：`evidence.py` + 校验点接入** ←（因 2.7.3 升为唯一可靠防线，
   优先级高于 D0-6）。
5. D0-7：Toolbox 加固 + 回归。
6. D0-6：opencode 规则下发（尽力而为，不阻塞交付）。

> **顺序相对初稿有调整**：原计划 D0-6 在 D0-7 之前，
> 现因 D0-6 的有效性无法证明而后置。
> 先把能兑现的（第一、二层）做实，再做尽力而为的部分。

---

## 7. 验收标准

**机制接通类**（只验证接线，不足以宣布完成）：

1. 截断的 `.state` 被读取后，磁盘内容**未被改写**，返回 `_corrupted` 标记。
2. 截断的 `.state` 不再被伪装成 `stage=01-brainstorming`。
3. `write_state` 过程中崩溃（模拟：写临时文件后抛异常），
   原 `.state` 保持完整可读。
4. 两个并发 `update_state` 各自的修改**都保留**（2.3 的场景不再丢失）。
5. `.state` 原子替换后，持锁方仍持有有效锁（锁文件未被换 inode）。
6. 走 `update_state` 写入证据字段后，`verify_evidence` 返回 `valid`。
7. 未签名的 `.state`（存量任务）返回 `unsigned`，**不返回 `valid`**。
8. `restricted` 参数已从 `RunCommandTool` 的签名与 description 中消失。
9. `WriteFileTool` 拒绝写 `facts/` 与 `evidence/` 下的路径。
10. `run_command` 的 `cwd` 越出 ROOT 时被拒绝。
11. mock 模式下全部既有测试通过（**单调性**）。
12. `_load_env()` 的返回值中不含 `HARNESS_EVIDENCE_KEY`。
    **补充（2.9.2）**：`pty.py` 的 `_build_env()` 是第二条环境出口，
    同样必须剥离；且需**按值扫描**，因为密钥可能被复制到别名变量下。
17. `ReadFileTool` 与 `run_command` 均拒绝读取 `config/.evidence_key`
    （2.9.2 第 3 条：密钥可读则 HMAC 形同虚设）。
18. 跑完全量测试后，真实 `config/.evidence_key` **未被创建**
    （2.9.3 第 2 条：测试不得污染生产密钥）。

**有效性类**（唯一能证明「真的防住了」的判据）：

13. **篡改检出**：绕过 `update_state` 直接修改 `.state` 的 `red_witness`
    后，`verify_evidence` 返回 `tampered`，且下游校验点**拒绝通过**。
14. ~~**拼接绕过失效**：`python -c` 用字符串拼接构造 `.state` 路径写入，
    被路径前缀校验拦截。~~ **❌ 未达成，本条判据设计有误。**

    实测两种写法均未被拦下：

    ```text
    python3 -c "open(chr(46)+'state','w').write('{}')"      → 未拦
    python3 -c "open('workspace/tasks/T1/.state','w')"      → 未拦
    ```

    原因是路径在解释器的字符串字面量里，不在 argv 里 —— argv 前缀校验
    在**原理上**看不到它，而 `python` 必须留在白名单（跑测试要用）。
    这不是实现偷懒，是本条验收与 1.1 的边界声明自相矛盾：
    既然白名单只是误操作护栏，就不该要求它拦住解释器。

    **处置**：缺口固化为测试
    `test_python_dash_c_write_is_not_blocked_by_guard` ——
    它转红意味着有人误以为堵住了。兜底由第 16 条承担。
15. **opencode deny 生效**：**✅ 已验证**（2.9.5，真实 LLM 会话）。
    规则下发端到端一致（2.9.1），且 A/B 对照证明 deny 真的让 `write`
    返回 `status=error`、目标文件原样；`**/.state` 覆盖嵌套路径。

    **附带修正一条实现前提**：`write` 工具查的是 `edit` 权限而非 `write` ——
    只 deny `write` 时写入 3/3 成功。**两者同时 deny 是必要条件，不是冗余。**
    测试 `test_write_and_edit_both_denied_because_write_tool_checks_edit` 守住。
16. **越界写仍可检出**：✅ 已验证
    （`test_bypass_write_is_still_detected_by_signature`）。
    绕过护栏改写 `red_witness` 后 `verify_evidence` 报 `tampered`。
    这是第 14 条失守之后**唯一**还站得住的防线。

    **2.9.5 第 4 条把这条从「补充」抬成「主防线」**：真实会话中 `write`
    被拒后，模型**自发改用 `bash`** 并 3/3 写入成功。
    即路径级 deny 对有 shell 的 agent 不构成边界，HMAC 才是兜底。
19. **`stage_status` 翻转不抹判据**：✅ 已验证（2.9.6）。
    并发「翻状态」与「签 Gate」后 `gate.signed_by` 仍在
    （`test_status_flip_does_not_erase_gate_signature`）。
20. **判据类写入点全部走受控入口**：✅ 已验证。
    `stage_state.py` 7 处写入函数体内不得出现 `write_state(`
    （源码断言测试 `test_stage_state_writers_go_through_controlled_entry`）。

> 第 16 条是刻意设计的。它验证的是 1.1 那条边界诚实成立 ——
> 阻止失败时检出仍然工作。如果这条测不出 `tampered`，
> 说明整个第二层是装饰。

---

## 8. 风险与遗留

| # | 风险 | 处置 |
|---|---|---|
| R1 | 39 处 `write_state` 调用点迁移面大 | **判据类已迁完**：`stage_state.py` 7 处（`seed_gate`/`sign_gate`/`reset_gate`/`write_route`/`reset_route`/`record_decision`/`issue_output_nonce`）全走 `update_state`，源码断言测试守住。剩 `service.py` 6 处、`runtime.py` 3 处未迁 —— 均为整体覆盖或已就地重读，不含判据字段，风险低于判据类。见「明确遗留」U0-4 |
| R2 | `fcntl.flock` 在部分网络文件系统上不可靠 | 记录为已知限制。本地开发与 CI 均为本地盘，不引入分布式锁 |
| R3 | 存量任务无签名，一律 `unsigned` | 提供 `scripts/sign_existing_state.py` 一次性补签，并在报告中标注「签名为补建」。不静默视为 `valid` |
| R4 | opencode 版本升级导致 permission 规则形态变化 | 规则构造集中在 `_tool_switches()` 一处；补一个针对该函数输出形状的测试，升级时先红 |
| R5 | 密钥文件在 ROOT 内，agent 的 `bash` 可读 | **不解决**，见 3.2 末尾。真正的隔离需要容器（`docker.enabled`），属独立任务 |
| R6 | `_corrupted` 三态需要 12 处调用方逐个处理 | **已收口**：`state.py` 提供 `StateCorruptedError`（继承 `ValueError` 以兼容既有 `except ValueError`）、`is_corrupted()`、`raise_if_corrupted()`，错误信息含任务名/文件路径/原因/`migrate_state_format.py` 修复指引；接在 `runtime.advance()` 入口。测试 `test_advance_rejects_corrupted_state_at_entry` 从真实调用方视角验证 |

**明确遗留、不假装解决的问题**：

- **U0-1**：`bash` 工具无法按路径约束。这是 opencode 权限模型的固有边界，
  不是本任务能修的。
- **U0-2**：拥有 shell 的 agent 可读密钥并伪造签名。A0 只防误改。
- **U0-3**：`STATUS.json` 也是无锁写（`state.py:129`），
  但它是缓存而非判据，本批不纳入签名。若将来有判定依赖它，须重新评估。
- **U0-4**：`service.py` 6 处、`runtime.py` 3 处 `write_state` 未迁到
  `update_state`。**为什么不做**：这些点或是「整体覆盖」语义（如任务初始化，
  本就不该做字段级归并），或已在 2.8 之后就地重读过。
  它们不写 `gate` / `output_nonce` / `red_witness` 等判据字段，
  丢写的后果是缓存类字段回退而不是判据丢失。
  一并迁移会把 A0 的 diff 面再扩大一倍，且需要逐点确认语义
  ——留给 A2 起的后续任务按需推进，不在此假装完成。
- **U0-5**：验收 14（`python -c` 字符串拼接写 `.state`）**原理上**无法用
  argv 前缀校验堵住，且 `python` 必须留在白名单。已固化为
  `test_python_dash_c_write_is_not_blocked_by_guard`，
  该测试转红即意味着有人误以为堵上了。兜底由验收 16 承担。
- **U0-6**：`Toolbox` 白名单**对当前实际运行的 agent 无效** ——
  `config.yaml` 五角色全是 `opencode`，而 `Toolbox` 只被
  `agents/gemini.py:18` 引用。D0-7 的加固服务于 gemini 与未来自研 agent，
  当前生效的保护是 opencode permission 规则（验收 15）+ HMAC（验收 16）。

---

## 9. 本任务的红绿要点

### 9.1 自指风险与处置

A0 实现「证据不可伪造」，而它的测试必须**绕过受控入口写 `.state`**
才能构造出被篡改的样本 —— 那正是本任务要禁止的行为。

**处置：按用途区分，而不是按手法区分。**

| 测试用途 | 允许绕过？ | 理由 |
|---|---|---|
| 构造攻击样本，断言校验器**检出** | ✅ 必须绕过 | 不绕过就无法制造篡改，验收 13/14/16 写不出来 |
| 构造正常状态，断言流程**通过** | ❌ 禁止绕过 | 必须走 `update_state`，否则测的不是真实写入路径 |

一句话：**绕过用于制造失败是必要的，用于制造通过是自欺。**

> 具体识别方式：如果一个测试里出现了 `open(state_path, "w")`
> 而断言是 `assert result.ok`，那它就越界了。

### 9.2 红的正确形态

| 验收项 | 红的正确形态（实现前必须看到） | 假绿风险 |
|---|---|---|
| 1 截断不回写 | 构造真实截断文件，断言读后磁盘内容不变；实现前会因为**发生了回写**而红 | 只断言返回值有 `_corrupted`，不检查磁盘 —— 回写照样发生 |
| 2 不伪装 stage | 断言返回的 `stage` **不是** `01-brainstorming`；实现前因默认填充而红 | 断言 `stage is None`，但实现改成填别的默认值也能过 |
| 3 崩溃后完整 | 注入异常于 `fsync` 之后 `replace` 之前，断言旧内容可读；实现前因 `open("w")` 已截断而红 | 用 mock 替掉整个写流程，测不到真实截断行为 |
| 4 并发不丢失 | 两个线程各自 `update_state`，断言两份修改都在；实现前因后写覆盖而红 | 串行调用两次 —— 那永远不会丢失，测不出竞争 |
| 5 锁不被换 inode | 持锁期间执行一次原子替换，断言锁仍互斥；实现前若锁在 `.state` 上则红 | 只测「能加锁」，不测替换之后 |
| 6/7 签名三态 | `unsigned` 场景断言**不等于** `valid`；实现前因无该函数而红（应是 AttributeError → **不算有效红**，须先建空壳再断言语义） | 只测 `valid` 分支；`unsigned` 被 `if not sig: return valid` 放过 |
| 13 篡改检出 | 直接改文件后断言 `tampered`；实现前因无校验而红 | 篡改的是不签名的字段（如 `stage`），当然检不出，却以为机制失效 |
| 15 deny 生效 | 真实 opencode 会话请求越界 `write`，断言被拒；实现前因只有 `pattern:"*"` 而红 | 用假的 payload 断言自己构造的规则表 —— 测的是自己的字典，不是 opencode 的行为 |
| 16 越界仍检出 | 用 `bash` 绕过 write deny 后断言 `tampered`；实现前因无签名而红 | **把这条写成「断言 bash 被阻止」** —— 那是错的预期，会导致为了让它绿而做出虚假的阻止 |

### 9.3 最容易出的两个假绿

**第一个：用 mock 替掉文件系统。**

验收 1-5 全部关于真实文件系统行为（截断语义、`os.replace` 原子性、
`flock` 互斥、inode 身份）。这些**不能 mock** ——
mock 掉之后验证的是自己对 POSIX 的理解，而不是 POSIX 的实际行为。

**要求**：1-5 必须在 `tmp_path` 下操作真实文件。
这与 A2 的 10.3、A6 的 9.1 是同一条纪律。

**第二个：验收 15 自说自话。**

最省事的写法是构造规则表然后断言它长得对。那不验证任何东西 ——
opencode 是否真的按 `findLast` 语义应用这些规则，是**外部行为**。

**要求**：至少一条测试起真实 `opencode serve`，
发一次越界 `write` 请求，断言被拒。
若环境不具备（CI 无 opencode），该项标 ❓ 并说明原因，
**不得标 ✅**（DEV-PROTOCOL 第 2 节）。

> 撰写本文档时曾尝试在本机起 `opencode serve` 验证 pattern 语义，
> 因日志目录权限（`FileSystem.open .../opencode/log`）与
> `ServeError` 未成功。**因此 2.6 中标注为「二进制符号分析」的几条
> 是静态证据，不是运行时实测。** 实施时第 6 节第 1 步必须补上。

### 9.4 摩擦点记录

- 若 `update_state` 的 mutator 形态在改造 39 处调用点时反复别扭，
  记录下来 —— 说明接口设计需要调整，而不是硬迁。
- 若 opencode 的 pattern 语义与 3.3 的假设不符，
  **先更新本文档再改代码**，不要让代码与文档悄悄分叉。

---

## 10. 回滚

三层可独立回滚：

| 层 | 回滚方式 | 回滚后行为 |
|---|---|---|
| 第一层（D0-1/2/3） | 无开关，属纯修复 | 不回滚。若 `_corrupted` 三态引发问题，可临时让调用方把它当 `{}` 处理 |
| 第二层（D0-4/5） | 配置 `harness.evidence.sign: false` | 不签名、不校验，行为与现在一致 |
| 第三层（D0-6/7） | 配置 `harness.evidence.strict_tools: false` | opencode 只下发原有 `pattern:"*"` 规则；Toolbox 恢复旧保护逻辑 |

> 第一层刻意不做开关。「读到损坏文件时静默回退到第一阶段」
> 没有任何值得保留的价值，给它留开关等于给假绿留后路。
