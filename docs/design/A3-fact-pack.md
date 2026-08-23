# A3：事实包生成器

> 依赖：**A0**（`.state` 受控写入与证据签名）、**A1**（任务级 git 仓库与 `baseline_sha`）
> 被依赖：**A6**（客观轨）、**A7**（攻击者）、**A8**（设计审视者）、**A10**（达成度报告）
> 范围：新增 `sw_lib/workflow/fact_pack.py`；改 `hooks/lib_run_tests.sh`；
> 03 准出与 04 入口的接线
> 不含：客观判定逻辑（属 A6）、prompt 组装（属 A5）、反例生成（属 A7）
>
> 上游依据：`docs/design-reviewer-independence.md` 第 4 节全部、第 5 节
>
> 🔒 实施须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。末尾第 9 节为本任务特有的假绿风险。

---

## 1. 目标与定位

### 1.1 这是对「自问自答」的正面回答

全套设计的三个成因（README 第 5 节）里，**C1 上下文污染**由本任务解决：

> developer 的结论以权威文档形式传给 reviewer，
> reviewer 读到的是「我做完了，测试都过了」这类自述，
> 于是复核的对象是**叙述**而不是**事实**。

A3 的做法是：**04 阶段完全不读 `03-coding.md`**。
reviewer 拿到的全部输入由 harness 用确定性程序生成，agent 只读。

这一条是整套机制里最直接的杠杆 —— 判据和产出不再出自同一个隐状态，
不是因为换了模型（那是 A4/A5 的事），而是因为**审查对象换成了机器事实**。

### 1.2 本任务的范围

| 在范围内 | 不在范围内 |
|---|---|
| 生成 `facts/` 下的七类文件 | 依据事实作出判定（A6/A7/A8/A9） |
| 结构化声明写入 `.state`（替代解析 md） | prompt 里怎么排布这些事实（A5） |
| pytest 结果的结构化采集 | 覆盖率阈值策略（A6 的 O8） |
| diff 截断与风险排序 | 反例文件的生成（A7） |
| 事实包完整性校验（缺失即硬失败） | 修复 agent 的产出质量 |

### 1.3 边界声明（必须诚实）

A3 保证的是「事实由 harness 生成且 agent 未加工」，**不保证事实完整**。

- `diff` 只反映文件系统的变化，agent 在会话里说过什么、想过什么，一概不在。
- `spec.md` 的第三段（设计陈述）本质仍是 agent 自述，只是被明确标注了低可信度。
- **真正的规格判据来自 R8（可执行验收场景），本任务不假装解决**
  （沿用上游 4.4 的表述）。

即 A3 消除的是「用自述冒充事实」，不是「需求本身跑偏」。
后者是 C3 的需求层部分，仍未有归属任务。

---

## 2. 现状：已实测的事实

以下全部真实执行过，探针已清理。

### 2.1 ⚠️ 头号问题：agent 新建但未 `git add` 的文件不出现在 diff 里

上游 4.1 规定 `diff.patch` 由 `git -C <target_dir> diff <baseline_sha>` 生成。
实测该命令**看不见未跟踪文件**：

```text
$ git status --porcelain
?? src/untracked.py

$ git diff <baseline> --numstat
（输出里没有 src/untracked.py）
```

而 agent **通常不自己 commit**（A10 的存在前提正是「归档时才 commit」）。
于是按上游写法生成的事实包会**漏掉 agent 的主要产出** ——
reviewer 拿到一个几乎空的 diff，然后判「无改动，通过」。

这是比上下文污染更严重的失效：事实包不是有偏，而是**空的**。

### 2.2 解法实测：临时索引 **+ 临时对象库**

试过四种，只有第四种真正零副作用：

| 方案 | 能否纳入未跟踪文件 | 副作用 |
|---|---|---|
| `git diff <baseline>` | ❌ | 无 |
| `git add -A -N` 后 diff，再 `git reset` | ✅ | ❌ **破坏 agent 的暂存状态**（实测：原本 `A  file` 被 reset 成 `?? file`） |
| `GIT_INDEX_FILE=<tmp>` + `git add -A -N` + diff | ✅ | ⚠️ **仍污染对象库**（见 2.2.1，本条原记「零副作用」，已被实测推翻） |
| **上一条 + `GIT_OBJECT_DIRECTORY` 重定向** | ✅ | ✅ 无（实测：真实对象库文件数前后相等，暂存状态 `A  src_staged.py` 保持不变） |

**定案：方案四。** 实现要点：

```
1. cp <repo>/.git/index <tmpdir>/idx          # 以真实索引为起点
2. mkdir <tmpdir>/objects                     # 新对象的落点
3. GIT_INDEX_FILE=<tmpdir>/idx \
   GIT_OBJECT_DIRECTORY=<tmpdir>/objects \
   GIT_ALTERNATE_OBJECT_DIRECTORIES=<repo 的真实 objects 路径> \
     git add -A -N
4. 同一组环境变量下 git diff <baseline_sha> ...
5. 删除整个 <tmpdir>
```

第 1 步不可省：从空索引起会把 agent 已暂存的内容判成新增。
第 3 步的 alternate 不可省：新对象写临时目录，但**读**既有对象仍要走真实
对象库，否则 `diff` 解不开基线那棵树。

> 真实 objects 路径**不要拼 `<repo>/.git/objects`**：worktree 与 submodule 下
> `.git` 是含 `gitdir:` 的文件，对象库在别处。问 git 本人：
> `git -C <repo> rev-parse --git-path objects`。

#### 2.2.1 ⚠️ 修正本节原结论：`GIT_INDEX_FILE` 并非零副作用

本节原先记「方案三零副作用」，依据是真实索引未变。**该依据不充分** ——
索引没变，但对象库变了。A1 实施期用真实 CLI 跑 `baseline_diff` 时暴露：

```text
$ git -C <harness 根> add -A -N     # 沙箱内，对象库不可写
error: unable to create temporary file: Operation not permitted
fatal: cannot create an empty blob in the object database
```

`git add -N` 需要往对象库写**空 blob**。实测计数：

| | 真实 `.git/objects` 文件数 |
|---|---|
| `add -A -N` 前 | 3 |
| 仅 `GIT_INDEX_FILE` 隔离后 | **4** |
| 加上 `GIT_OBJECT_DIRECTORY` 重定向后 | 3（新对象落临时目录） |

> 绝对值随仓库起点而变（含已 staged 文件的仓库起点是 4），
> **判据是「前后相等」，不是某个具体数字** —— 验收 16b 须按差值断言。
> 另注：泄漏的空 blob 可被 `git prune` 回收，
> 但那要求有人事后想起来做，不能作为处置。

`GIT_INDEX_FILE` 只隔离索引，**不隔离对象写入**。两个后果：

1. **观察改变了被观察对象** —— 每次进入 04 都往 agent 的仓库塞对象。
   这与本任务「事实包是只读观察」的前提直接冲突。
2. **对象库不可写时整条链路硬失败**，且报错文面（`cannot create an empty
   blob`）与 diff 毫无关系，极易被上层当成「拿不到事实」而降级处理 ——
   那正是 A1 的 3.2 第 4 条要禁止的「失败被翻译成合规」。

第 2 点不是假想：只读挂载、容器化（README 提到的隔离方向）、
以及 CI 上把仓库挂成只读的做法都会触发。

> **A1 已按方案四实现于 `sw_lib/core/git_repo.py` 的 `baseline_diff()`，
> 并有两条回归测试**（`test_baseline_diff_writes_no_objects_into_observed_repo`、
> `test_baseline_diff_works_with_readonly_object_store`）。
> **A3 直接调用它，不要照本节的步骤再写一遍** —— 见 3.3。

> 为什么不让 A10 先 commit 再生成事实包：
> 那会把「未完成的中间状态」写进 git 历史，
> 且 04 返工后重新进入时又要 commit 一次，历史被搅乱。
> 事实包是只读观察，不该改变被观察对象。

### 2.3 ⚠️ 修正 A6 的一处断言：零测试的退出码是 5，不是 0

A6 的 3.1 写「『收集到 0 个测试』与『全部 skip』两种情况 pytest 均返回 **0**」。
实测：

| 场景 | 退出码 |
|---|---|
| 正常套件（2 passed, 1 skipped） | 0 |
| **零测试** | **5**（`no tests ran`） |
| 全部 skip | 0 |

即 A6 说的两个盲区里，**只有「全 skip」那一半成立**。
零测试其实已经能被退出码检出 —— 但 `run_project_tests` 有一个
更前置的缺陷让它照样漏过（见 2.4）。

**这不削弱 O3 的必要性**，但验收断言必须按真实退出码写，
否则会写出一条「构造零测试目录，断言退出码为 0」的测试 —— 那是错的。

### 2.4 现状：`run_project_tests` 在无测试文件时整段跳过

`hooks/lib_run_tests.sh` 的 `run_project_tests()`：

```bash
if [ -f "$dir/pytest.ini" ] || [ -f "$dir/pyproject.toml" ] \
   || ( cd "$dir" && ls test_*.py >/dev/null 2>&1 ); then
    ...运行 pytest...
fi
return 0
```

三个条件全不满足时**直接 `return 0`（通过）**，pytest 压根没跑，
所以 2.3 的退出码 5 永远不会出现。这是 A6 的 3.3
「禁止找不到就跳过」的实例，也是 A3 必须提供 `tests.json` 的原因：
**有没有跑过**这件事本身要落盘，不能只留一个退出码。

### 2.5 现状：成功时的 pytest 输出被整个丢弃

同一函数里，`out` 只在失败分支被回显：

```bash
if ! out=$(cd "$dir" && ... "$py" -m pytest 2>&1); then
    echo "❌ $fail_label"
    echo "$out" | tail -25 | sed 's/^/    /'
    return 1
fi
```

成功时 `out` 被丢弃。于是 collected / passed / skipped 计数全部丢失，
A6 的 O3 无从判定。`tests.json` 必须由 A3 采集并落盘。

### 2.6 `pytest-json-report` 不可依赖

实测本机未安装该插件（`import pytest_jsonreport` → ImportError）。
hook 跑在**用户仓库的解释器**上（`_project_python()` 会优先用
`target_dir/.venv/bin/python`），更不能假定插件存在。

**定案**：解析 pytest 的摘要行。实测其形态稳定：

```text
2 passed, 1 skipped in 0.00s
no tests ran in 0.00s
```

`--tb=no -q` 下形态一致。解析失败时 `tests.json` 记
`parse_status: "unparsed"` 并保留原始输出，**不猜数字**。

### 2.7 ⚠️ `spec.md` 的两个「高可信度」来源实测常为空

上游 4.4 定案 `spec.md` 三段拼装，前两段标「高可信度」。
用真实任务 T1 实测：

| 段 | 来源 | T1 实测 |
|---|---|---|
| 原始需求 | `.context` | ✅ 有内容（用户原文，48 字） |
| 已拍板决策 | `.state` 的 `decisions` | ❌ **五个阶段全部为空** |
| 设计陈述 | `01-brainstorming.md` 的 ADR 段 | ❌ **全是 `___` 占位符，agent 从未填写** |

`decisions` 为空的原因已查明：`record_decision` 只在**用户实际回答**时写入
（`tui.py:1118`、`web/engine_manager.py:131`）。
T1 那次 agent 确实调用了 `question` 工具（`01-brainstorming.md` 的
AI Output 段可见两次 `🔧 question`），但**无人应答**，所以一条都没落盘。

**处置**：三段各自可空，且**空与缺失必须区分**：

| 情况 | `spec.md` 的写法 |
|---|---|
| `.context` 为空 | `## 原始需求\n（用户未提供上下文）` + 事实包标记 `spec_context: "empty"` |
| `decisions` 为空 | `## 已拍板决策\n（本任务无用户拍板记录 —— 可能是 ask_user 未被触发或无人应答）` |
| ADR 段全为占位符 | 检出 `___` 占位模式，写「agent 未填写设计决策」，**不把占位符原样喂给 reviewer** |

最后一条是关键：把 `- **Proposal**: ___` 喂给 reviewer，
它会当成真实的设计陈述去审，产出的是对空气的评价。

> 这也解释了为什么 `spec.md` 不能作为规格判据（1.3）：
> 三段里两段常空，剩下的 `.context` 是一句话需求。
> **A3 的价值在于让这个贫瘠状态可见**，而不是掩盖它。

### 2.8 diff 风险排序的数据来源可用

`git diff --numstat` 直接给出逐文件增删行数，可支撑上游 4.3 的排序：

```text
1	0	config/c.yaml
51	0	docs/README.md
3000	0	src/big.py
2	0	tests/test_t.py
```

二进制文件显示为 `-	-	src/b.bin`，需单独处理（不计入行数排序，
但**必须出现在文件清单里**，否则二进制产出等于隐身）。

### 2.9 `facts/` 目录当前不存在，且不受保护

- `workspace/tasks/*/facts/` 目录当前无任何任务拥有。
- A0 的 2.9.2 第 3 条已把 `facts/` 纳入 Toolbox 写保护与 opencode deny，
  但 A0 的 2.5 同时指出 **Toolbox 对当前实际运行的 opencode 无效**。
  故 `facts/` 的实际保护只有 opencode 的 permission 规则，
  且 A0 的 2.9.5 实测证明 **`bash` 可绕过**。

**结论**：`facts/` 是「agent 只读」的**约定**，不是强制。
A3 不假装它不可写；篡改检出依赖 A0 的签名（见 3.6）。

---

## 3. 设计

### 3.1 新增模块：`sw_lib/workflow/fact_pack.py`

```python
class FactPackError(Exception):
    """事实包生成失败。缺前提时抛此异常，不静默降级。"""

@dataclass
class FactPack:
    task: str
    target_dir: str
    baseline_sha: str
    baseline_kind: str          # 来自 A1：fresh / reconstructed / harness_self
    files: Dict[str, Path]      # 生成的文件名 -> 路径
    truncated: List[str]        # 被截断的文件清单
    warnings: List[str]         # 非致命问题，须注入 prompt

def generate(task: str, *, force: bool = True) -> FactPack:
    """生成 facts/ 全部内容。每次进入 04 都重新生成（上游 4.2）。"""

def verify(task: str) -> List[str]:
    """校验事实包完整性，返回缺失项清单。空列表表示完整。"""
```

### 3.2 产出文件（对齐上游 4.1，补两处）

| 文件 | 内容 | 生成方式 | 供给 |
|---|---|---|---|
| `diff.stat` | 文件级增删统计 | 临时索引 + `diff --stat` | 三轨 |
| `diff.patch` | 完整变更（可截断） | 临时索引 + `diff` | 攻击轨、设计轨 |
| `diff.numstat` | **新增**：逐文件增删行数 | `diff --numstat` | 排序依据、A6 的 O5 |
| `diff.truncated` | 因体积被截断的文件清单 | 截断时生成 | 攻击轨、设计轨 |
| `tests.json` | 收集数/通过/失败/skip/退出码/解析状态 | harness 执行并采集 | 三轨 |
| `coverage.json` | 覆盖率（若可得） | `coverage json` | 客观轨 |
| `spec.md` | 需求与已拍板决策（三段，标可信度） | 见 3.4 | 攻击轨、设计轨 |
| `plan.md` | Task DAG 与架构决策 | 从 `02-planning.md` 提取 | **仅设计轨** |
| `manifest.json` | **新增**：事实包自身的元数据 | 见 3.7 | 全部 + A10 |

`diff.numstat` 新增的理由：A6 的 O5（diff 范围）需要**文件路径列表**做
越界判定，从 `diff.stat` 的人类可读格式里反解析是脆的。

`plan.md` 只给设计轨的规定沿用上游（4.1 脚注）：
设计审查需要对照物，否则退化为个人偏好。

### 3.3 diff 生成流程：**直接调用 A1 的 `baseline_diff()`**

```python
from sw_lib.core.git_repo import baseline_diff, read_baseline, GitRepoError

info = read_baseline(task)                  # None → FactPackError（缺前提）
facts = baseline_diff(target_dir, info.sha, include_patch=True)
#   facts.files    -> List[str]   逐文件路径（供 A6 的 O5）
#   facts.numstat  -> str         写 diff.numstat
#   facts.stat     -> str         写 diff.stat
#   facts.patch    -> str         写 diff.patch（截断逻辑仍属 A3，见 3.8）
```

`baseline_diff()` 内部已完成：归属校验（`verify_ownership`）、
基线可解析校验、临时索引 + 临时对象库（2.2 的方案四）、
`harness_self` 时排除 `workspace/` 与 `repo/`。

**A3 不要自己实现 2.2 的步骤。** 那段逻辑有三个易错点（索引起点、
对象库重定向、worktree 下的 objects 路径），各写一遍必然分叉；
A1 侧已有回归测试锚住它们。A3 的职责从「生成 diff」缩小为
「把 `DiffFacts` 落盘 + 截断 + 排序」。

> 归属校验必须在生成前完成（`baseline_diff` 已内置）：
> **归属错误时生成的 diff 是 harness 自身的改动**，
> 那是 A1 的 1.1 要消除的歧义，不能在这里复现。

其余 git 调用一律走 **A1 的 `git_repo.run_git()`**，不自己拼 subprocess ——
A1 侧有源码断言（`test_git_calls_are_centralized_in_git_repo`）会红。

**失败传递**：`baseline_diff` 抛 `GitRepoError` 时，A3 转为
`FactPackError` 向上抛，**不得吞掉后继续生成半个事实包**（验收 12）。
捕获后回落到裸 `git diff` 是最危险的写法 —— 那会静默退回 2.1 的缺陷。

### 3.4 `spec.md` 的三段拼装（含 2.7 的空态处理）

```markdown
# 需求与决策事实

> 本文件由 harness 生成，developer 未参与编辑。
> 各段可信度已标注，**低可信度段落不得作为判据**。

## 1. 原始需求（可信度：高 —— 用户原文）
<.context 原文，或「（用户未提供上下文）」>

## 2. 已拍板决策（可信度：高 —— 用户经 ask_user 确认）
<decisions 渲染，或「（本任务无用户拍板记录）」>

## 3. 设计陈述（可信度：低 —— agent 自述，仅作参考）
<01-brainstorming.md 的 ADR 段，或「（agent 未填写）」>
```

占位符检出规则：ADR 段内若**每个字段值都匹配 `_{3,}`**，视为未填写。
不做部分保留 —— 半填的模板同样会误导 reviewer。

### 3.5 结构化声明写入 `.state`（对齐上游 5.2）

**04 阶段完全不读 `03-coding.md`。** 03 准出时把结构化字段写入 `.state`：

```json
{
  "stages": {
    "03-coding": {
      "claims": {
        "task_ids": ["T1-1", "T1-2"],
        "verify_cmd": "python3 -m pytest",
        "files_touched": ["src/a.py"]
      }
    }
  }
}
```

必须走 **A0 的 `update_state`**（`stage_state.py` 的既有写入函数已全部迁移，
见 A0 的 R1）。禁止裸 `read_state` → 改 → `write_state`。

> 上游 5.2 已否决「复用 nonce 围栏切分事实/评价」：
> 围栏区分的是「谁写的」，不是「是事实还是评价」。
> `.state` 的边界是机器可判的，不依赖文本解析。

`claims` 是 agent 的**声明**而非事实 —— 命名刻意如此。
A6 的职责之一是把 `claims.files_touched` 与 `diff.numstat` 的真实文件列表
对照，不一致即为一条客观发现。

### 3.6 事实包的可信性：签名而非保护

2.9 已说明 `facts/` 不是强制只读。处置：

`manifest.json` 记录每个事实文件的 SHA-256，
并把 `manifest.json` 自身的哈希写入 `.state` 的证据字段，
纳入 **A0 的 HMAC 签名**范围。

于是篡改 `facts/` 任一文件 → manifest 哈希不匹配 → 可检出；
连 manifest 一起改 → `.state` 里的哈希不匹配 → `verify_evidence` 报 `tampered`。

> 与 A0 的接口约定：**无需改动 `EVIDENCE_FIELDS`** ——
> `facts` 键已在该六元组中（`evidence.py:33`，注释即写「A3：事实包」）。
> A3 只需把 manifest 哈希写在 `.state` 的 `facts` 键下，签名自动覆盖。
>
> 同理 A1 的 `baseline_sha` 落在既有 `review` 键下（A1 的 3.4）。
> **两个任务都不要去动 `EVIDENCE_FIELDS`**：它是元组常量，
> 并行改动时后提交者容易整体替换掉前者。

### 3.7 `manifest.json` 内容

```json
{
  "task": "T1",
  "generated_at": "2026-08-23T...",
  "baseline_sha": "...",
  "baseline_kind": "fresh",
  "files": {"diff.stat": "<sha256>", "tests.json": "<sha256>"},
  "truncated": ["src/big.py"],
  "warnings": ["diff 超过阈值，2 个文件被截断"],
  "spec_availability": {
    "context": "present",
    "decisions": "empty",
    "adr": "placeholder_only"
  }
}
```

`spec_availability` 三态直接来自 2.7 的实测 ——
让「规格贫瘠」成为可被 A10 统计的事实，而不是消失在渲染后的散文里。

### 3.8 截断策略（对齐上游 4.3）

1. `diff.stat` 与 `diff.numstat` **始终全量**（体积小，实测 4 文件 3054 行仅数百字节）。
2. `diff.patch` 按风险排序注入：生产代码 > 配置 > 测试 > 文档；
   同级内按 `numstat` 的变更行数降序。
3. 被截断的文件清单**必须写入** `facts/diff.truncated` 并注入 prompt。
4. 触发截断时 `warnings` 非空，hook 输出警告，**不静默**。
5. 二进制文件（`numstat` 为 `-	-`）不参与行数排序，但**必须出现在文件清单**（2.8）。

### 3.9 前提缺失时的行为：硬失败，不降级

上游 T3 验收要求「基线失效时抛错而非跳过」。本任务照此，并扩展为一张表：

| 前提 | 缺失时 |
|---|---|
| `.state` 无 `git.baseline_sha`（A1 未生效） | `FactPackError` 硬失败 |
| `baseline_sha` 无法 `rev-parse` | `FactPackError` 硬失败 |
| `verify_ownership` 不通过 | `FactPackError` 硬失败 |
| `target_dir` 不存在 | `FactPackError` 硬失败 |
| pytest 未安装 / 无法执行 | `tests.json` 记 `unavailable`，**不硬失败**（进 A6 的三态） |
| 覆盖率工具缺失 | `coverage.json` 记 `unavailable` |
| `.context` / `decisions` / ADR 为空 | 按 3.4 渲染空态，**不失败** |

分界线是：**「事实来源本身不可信」硬失败，「某项事实不可得」记 `unavailable`。**
后者进入 A6 的三态与 A10 的「不确定」分类，绝不计为通过。

---

## 4. mock 模式

上游 P2 记录了风险：「mock 模式下事实包硬失败会挂掉 CI」。

**定案**：mock 下照常生成事实包，但

- `tests.json` 允许 `mock: true` 且跳过真实 pytest 执行；
- 基线校验**照常执行**（A1 在 mock 下也建真实仓库，见 A1 第 4 节）；
- `manifest.json` 标 `mock: true`，A10 的报告中该任务的达成度标注「mock 数据」。

即 mock 影响的是「测试是否真跑」，不是「事实包是否生成」。
若 mock 下允许跳过基线校验，A3 的链路就从未被测过。

---

## 5. 与其他任务的接口

| 任务 | A3 提供 | 契约 |
|---|---|---|
| **A0** | 复用既有 `facts` 证据键，**不改** `EVIDENCE_FIELDS` | 写入落在 `.state` 的 `facts` 下即自动签名（3.6） |
| **A1** | 消费 `baseline_diff()` / `read_baseline()` / `run_git()` | **diff 生成整体复用 `baseline_diff`**（3.3），不自己拼 git 命令、不自己实现临时索引与对象库重定向 |
| **A6** | `tests.json`、`diff.numstat`、`manifest.json` | O3 的判定数据来自 `tests.json`；O5 的文件列表来自 `diff.numstat` |
| **A7** | `diff.patch`、`spec.md` | 反例写 `facts/counterexamples/`，与 `red_witness` 物理隔离（A2 的第 204 行） |
| **A8** | `diff.patch`、`spec.md`、**`plan.md`** | `plan.md` 仅此一轨可见 |
| **A10** | `manifest.json` 的 `spec_availability` 与 `warnings` | 截断与规格缺失须出现在达成度报告 |

---

## 6. 实施顺序（内部）

1. `fact_pack.py` 骨架 + `manifest.json` + `verify()`。
2. diff 三件套：调用 A1 的 `baseline_diff()` 落盘（3.3）。
   **先读 `sw_lib/core/git_repo.py` 的现有实现**，确认 `DiffFacts` 的字段
   已够用；不够用则**扩 A1 的返回值**，不要在 A3 侧另起一套 git 调用。
3. `tests.json` 采集（含 2.6 的摘要解析与 `unparsed` 三态）。
4. `spec.md` 三段拼装（含 2.7 的空态与占位符检出）。
5. `plan.md` 提取。
6. `.state` 的 `claims` 写入（03 准出）。
7. 接线：03 准出后、04 prompt 前调用；改 `lib_run_tests.sh` 输出计数。
8. 校验 manifest 哈希确实落在 `.state` 的 `facts` 键下（签名覆盖验证）。

---

## 7. 验收标准

**机制接通类**（只验证接线，不足以宣布完成）：

1. `generate()` 后 `facts/` 下九个文件齐备，`verify()` 返回空列表。
2. `manifest.json` 中每个文件的 SHA-256 与磁盘内容一致。
3. 每次进入 04 都重新生成（改一个文件后再次进入，`diff.stat` 应变化）。
4. `tests.json` 含 collected / passed / failed / skipped / exit_code /
   parse_status 六个字段。
5. 零测试目录的 `tests.json` 记 `collected: 0` 且 `exit_code: 5`
   （按 2.3 的真实退出码，**不是 0**）。
6. 全 skip 目录的 `tests.json` 记 `skipped == collected` 且 `exit_code: 0`。
7. pytest 摘要无法解析时 `parse_status: "unparsed"` 且保留原始输出，
   **数字字段不猜测**。
8. `spec.md` 三段各带可信度标注；`decisions` 为空时渲染显式空态说明。
9. ADR 段全为 `___` 占位符时，`spec.md` 写「agent 未填写」，
   **原始占位符不出现在文件里**。
10. `manifest.json` 的 `spec_availability` 三个键取值正确
    （用 T1 实测：`context=present` / `decisions=empty` / `adr=placeholder_only`）。
11. `.state` 的 `03-coding.claims` 经 `update_state` 写入，
    并发下不抹掉 Gate 签名（A0 的回归锚点）。
12. 缺 `baseline_sha` 时抛 `FactPackError`，**不生成半个事实包**。
13. pytest 不可用时 `tests.json` 记 `unavailable`，`generate()` **不抛异常**。
14. 全部既有测试通过（**单调性**）。

**有效性类**（唯一能证明「事实包真的是事实」的判据）：

15. **未 `git add` 的产出必须出现在 diff 里**（2.1 的回归锚点）：
    在 `target_dir` 新建一个文件且**不执行 `git add`**，
    `diff.numstat` 必须含该文件。

    这是本任务的头号判据 —— 按上游原写法实现会漏掉它，
    而那意味着事实包对 agent 的主要产出是盲的。
16. **生成事实包不改变 agent 的暂存状态**（2.2 的回归锚点）：
    先 `git add` 一个文件（状态 `A  file`），生成事实包后
    `git status --porcelain` 必须仍为 `A  file`，不得变成 `?? file`。
16b. **生成事实包不往被观察仓库的对象库写入**（2.2.1 的回归锚点）：
    生成前后清点 `.git/objects` 下的文件数，**必须相等**。

    只验第 16 条不足以证明「零副作用」—— 索引没变而对象库变了，
    正是 2.2.1 推翻原结论的那处。
16c. **对象库不可写时仍能生成 diff**（2.2.1 的第 2 个后果）：
    把 `.git/objects` 置为只读（`chmod 500`）后生成事实包，
    `diff.numstat` 仍须正确产出，**不得抛错**。

    这条锚住只读挂载 / 容器化场景。实现若依赖往真实对象库写空 blob，
    此处会拿到 `cannot create an empty blob`，而那个失败极易被上层
    当成「无改动」降级处理。

    > 16b / 16c 在 **A1 侧已有等价测试**（`baseline_diff` 的两条）。
    > A3 若按 3.3 直接调用 A1，这两条应天然成立 ——
    > **仍要在 A3 侧断言一次**，因为它们保护的是「A3 没有自己另写一份 diff
    > 逻辑」这个事实，而那恰恰是最可能被绕开的地方。
17. **04 的输入中不含 `03-coding.md` 的任何内容**：
    在 `03-coding.md` 里写入一个唯一哨兵字符串，
    断言它不出现在 `facts/` 任一文件、也不出现在 `.state` 的 `claims` 中。

    对应上游 5.1 的「自由叙述完全排除」。这是 C1 的直接判据。
18. **`claims` 与真实 diff 不一致时可被发现**：
    构造 `claims.files_touched` 声明了一个 diff 里没有的文件，
    A3 须在 `manifest.warnings` 中记录该不一致
    （判定归 A6，但**事实层的对照必须由 A3 提供**）。
19. **事实包篡改可检出**：改 `facts/diff.stat` 后 `verify()` 报哈希不匹配；
    连 `manifest.json` 一起改则 `verify_evidence` 返回 `tampered`。
    **本条同时验证落点正确** —— manifest 哈希若未写在 `.state` 的
    `facts` 键下，签名不覆盖，第二半必红。
20. **归属错误时不生成 harness 自身的 diff**：
    构造 `target_dir` 指向 harness 根目录的任务，
    `generate()` 必须抛 `FactPackError`，
    且 `facts/diff.patch` **未被创建**。

> 第 15 与第 17 条是本任务的核心。
> 15 保证事实包不是空的，17 保证它不是自述。
> 其余各条都是在这两条成立之后才有意义。

---

## 8. 风险与遗留

| # | 风险 | 处置 |
|---|---|---|
| R1 | 临时索引方案依赖 `.git/index` 可读可复制 | 复制失败时抛 `FactPackError`，不回落到会产生副作用的裸 `add -N`。宁可失败也不污染 agent 状态 |
| R1b | **对象库不可写**（只读挂载、容器化、沙箱）时 `add -N` 失败 | 已由 2.2.1 的 `GIT_OBJECT_DIRECTORY` 重定向解决（A1 的 `baseline_diff` 已实现）。**不得捕获该错误后回落到裸 `git diff`** —— 那会静默退回 2.1 的「事实包是空的」 |
| R2 | pytest 摘要行格式随版本变化 | 解析失败记 `unparsed` 并保留原文（2.6）。**不猜数字**。同时把 pytest 版本写入 `tests.json` 便于日后定位 |
| R3 | 大 diff 截断后审查范围不完整 | 截断清单显式注入 prompt（3.8 第 3 条），review 产出须标注「未覆盖审查范围」 |
| R4 | `facts/` 可被 agent 的 `bash` 改写 | **不解决**（2.9）。检出依赖 3.6 的签名链 |
| R5 | `spec.md` 三段常为空（2.7），reviewer 缺乏规格判据 | **不解决，但让它可见**：`spec_availability` 三态进 A10 报告。真正的解法是 R8 |
| R6 | 二进制产出无行数，可能在排序中沉底 | 单独列入文件清单（3.8 第 5 条），不参与行数排序 |
| R7 | `plan.md` 从 `02-planning.md` 提取需解析 md | 与 `spec.md` 的 ADR 段同法：结构化字段缺失时渲染显式空态，不静默交空文件 |

**明确遗留、不假装解决的问题**：

- **U3-1**：事实包只覆盖文件系统事实。agent 的推理过程、
  试错历史一概不在，reviewer 无从判断「为什么这样写」。
  上游 5.3 的折中（`facts/notes.md` 受限事实性说明）**第一批不做**。
- **U3-2**：`spec.md` 不是规格判据（1.3、R5）。
  需求跑偏这一类失效，A3 完全无力，需 R8 的可执行验收场景。
- **U3-3**：`claims` 由 agent 声明，A3 只做对照不做纠正。
  agent 可以少报 `files_touched`，对照会发现「diff 里有未声明的文件」，
  但**无法区分「忘了报」与「刻意隐瞒」**。

---

## 9. 本任务的红绿要点

### 9.1 自指风险

A3 生成的是「供审查的事实」。自指风险在于：

> **A3 自己的测试要断言「事实包内容正确」，
> 而判断正确的依据只能是另一份手工构造的期望值。
> 若期望值由同一套代码生成，就是自问自答的翻版。**

强制纪律：

1. 期望值**必须手工写死**（如「新建一个文件，断言 `diff.numstat` 含该文件名」），
   不得调用 `fact_pack` 的任何函数来生成期望。
2. diff 相关断言**必须用真实 git 仓库**（`tmp_path` 内），
   不得 mock `run_git` 的返回值 —— 那测的是解析逻辑，不是事实采集。
3. pytest 采集的断言必须**真实运行 pytest**于临时目录
   （沿用 A6 的 9.1 纪律：不接受 mock 掉 pytest 输出）。

> 第 2 条尤其重要。2.1 那个头号缺陷（未 `add` 的文件不进 diff）
> **只有在真实仓库里才会暴露** —— 任何 mock 都会按实现者的预期返回，
> 而实现者的预期恰好就是错的那个。

### 9.2 环境自伤风险

与 A1 同源：本任务测试要反复建 git 仓库、跑 pytest、写 `facts/`。

1. 全部在 `tmp_path` 内，**绝不碰真实 `workspace/tasks/*/`**。
2. 测试后断言 harness 自身 `git status --porcelain` 与测试前一致。
3. 不得用真实任务（如 T1）做写入探针 —— 只读取可以。

> A0 实施期曾用真实路径做探针，截断了 `workspace/tasks/T1/.state`。
> 本任务同时操作 git 与 `.state`，破坏面更大。

### 9.3 红的正确形态

| 验收项 | 红的正确形态 | 假绿风险 |
|---|---|---|
| **15 未 add 的文件进 diff** | 用上游原写法（裸 `git diff <baseline>`）实现时，断言「`diff.numstat` 含 `untracked.py`」为红 | **测试里顺手 `git add` 了**。一旦 add，两种实现都能通过，头号缺陷被永久隐藏。必须显式断言「未执行 add」 |
| **16 不改暂存状态** | 用 `git add -A -N` + `git reset` 实现时，断言「状态仍为 `A  file`」为红（实测会变成 `?? file`） | 只断言「生成成功」，不检查 `git status`。或测试里根本没有预先 staged 的文件，于是无从破坏 |
| **16b 不写对象库** | 仅隔离 `GIT_INDEX_FILE` 时，对象库文件数从 3 变 4，断言「前后相等」为红（2.2.1 实测） | **只验 `git status` 不变就宣布「零副作用」** —— 那正是本节原结论的错法：索引没变而对象库变了。必须清点 `.git/objects` |
| **16c 只读对象库可用** | 依赖真实对象库写空 blob 时，`chmod 500` 后抛 `cannot create an empty blob`，断言「仍产出 numstat」为红 | 测试在可写的 tmp 仓库上跑就永远绿 —— **必须真的把 objects 置为只读**。A1 侧漏掉此条正是因为 tmp 下对象库总可写 |
| **17 不含 03-coding.md** | 若实现从 md 提取任何字段，哨兵字符串会出现，断言为红 | 哨兵写在 md 的注释里或 AI Output 围栏外 —— 那些区域本来就不会被读。**哨兵必须写在最可能被提取的位置**（如 Implementation Notes 段内） |
| 5 零测试退出码 | 断言 `exit_code == 5`。若按 A6 原文写成 `== 0`，**测试本身是错的** | 沿用 A6 的错误断言（2.3）。实现若碰巧记录了真实退出码，测试会红，然后有人去"修"实现 |
| 7 摘要不可解析 | 喂一段畸形输出，断言 `parse_status == "unparsed"` 且数字字段为 `None` | 实现用 `except: return 0`，断言「不抛异常」也能过 —— 于是 0 collected 被当成真实事实 |
| 9 占位符不外泄 | 断言 `spec.md` 中**不含** `___`。实现若原样拼接则红 | 只断言「含『未填写』字样」，而占位符**同时**也在里面 |
| 12 缺基线硬失败 | 断言抛 `FactPackError`，且 `facts/` 下**无任何文件被创建** | 只断言抛异常，不检查是否已写了半个事实包 —— 半成品会被下游当成完整的读 |
| 20 归属错误不生成 | 断言抛错**且** `diff.patch` 不存在 | 同上：先生成后校验，异常抛出时文件已落盘 |
| 19 篡改检出 | 把 manifest 哈希误写在 `facts` 键之外时，第二半断言（`tampered`）应红 | 只验 `verify()` 的哈希比对（那是 A3 自己算的），跳过 `verify_evidence` —— 于是落点错误无人发现 |

### 9.4 特别提醒：第 15 项极易被自己写的测试掩盖

写这条测试时最自然的手法是：

```python
(target / "new.py").write_text("x = 1")
run_git(target, "add", "-A")        # <-- 这一行毁掉整条判据
pack = generate(task)
assert "new.py" in read_numstat(pack)
```

加了 `git add` 之后，**裸 `git diff` 的错误实现同样能通过**。
2.1 那个缺陷正是「agent 不会自己 add」，测试若代替 agent add 了，
就把被测场景改成了一个不存在的场景。

**测试里必须显式注明「此处刻意不执行 git add」**，
并在断言前 `assert git status --porcelain` 显示该文件为 `??`。

### 9.4.1 提醒：不要为了「独立验证」而在 A3 侧重写 diff 采集

9.1 第 1 条要求期望值手工写死，这条对**期望值**成立；
但**采集实现**要复用 A1 的 `baseline_diff`（3.3）。
两者不冲突：手工写死的是「断言 `untracked.py` 在结果里」，
不是「自己再拼一遍 git 命令来取结果」。

若在 A3 侧另写一份采集逻辑，2.2.1 的三个易错点（索引起点、对象库重定向、
worktree 下的 objects 路径）会各错一次，而 A1 侧的回归测试**保护不到它**。

### 9.5 单调性

改 `lib_run_tests.sh` 输出计数会影响 03 与 04 两个 hook。
按 DEV-PROTOCOL 第 3 节第 3 条，须区分：

- 既有测试断言 hook 的输出文本 → 更新断言（属真实变更）。
- 既有任务因 O3 上线而失败 → 那是 A6 的事，A3 只提供数据，
  **不得为了让存量任务变绿而在 `tests.json` 里做手脚**。

---

## 10. 回滚

| 层 | 回滚方式 |
|---|---|
| `facts/` 目录 | 纯新增，删除即回到现状。下游读不到时按 `unavailable` 处理 |
| `.state` 的 `claims` | 纯新增字段 |
| `lib_run_tests.sh` 的计数输出 | 单文件回滚；注意 03/04 两个 hook 都在用 |
| 04 不读 `03-coding.md` | 这是行为变更。回滚意味着 C1 未被解决，**须显式记录**而非静默恢复 |
| `.state` 的 `facts` 子字段 | 纯新增子字段；`EVIDENCE_FIELDS` 未被改动，无需回滚 |

> 最后一行的意思是：回滚 A3 会让「reviewer 读 developer 自述」这件事复原。
> 那不是一个中性的技术回退，而是**放弃本任务的全部目的**，
> 应当作为决策记录下来。
