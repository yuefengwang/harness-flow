# A1：任务级独立 git 仓库与基线锚定

> 依赖：无（波 1，可与 A0 / A4 并行）
> 被依赖：**A3**（事实包的 diff 事实）、**A6**（O5 diff 范围）、
> **A10**（归档 commit 与达成度报告）
> 上游依据：`docs/design-reviewer-independence.md` 10.2 的 **D1**（已定案，含实测）
>
> 🔒 实施须遵守 [`DEV-PROTOCOL.md`](DEV-PROTOCOL.md)。末尾第 9 节为本任务特有的假绿风险。

---

## 1. 目标与定位

### 1.1 要解决的问题

reviewer 的客观判断以 `git diff` 为核心事实来源。当前 `git diff` **语义不明** ——
分不清看到的是 harness-flow 自身的改动，还是任务目标仓库的改动。

现状实证（`hooks/check_05-archive.sh:14`）：

```bash
CHANGES=$(git diff HEAD --name-only | grep README.md || true)
```

裸 `git diff`，无 `-C`，执行时 cwd 是 harness 根目录。
于是它检查的是 **harness-flow 的 README**，而不是任务产出仓库的 README。
这条 hook 从写下的第一天起就在看错的仓库。

### 1.2 本任务的范围

| 在范围内 | 不在范围内 |
|---|---|
| 任务创建时初始化目标仓库并建立基线提交 | 归档时的 `git add` / `commit`（属 **A10**） |
| `baseline_sha` 写入 `.state` 并可被下游读取 | diff 内容的解析与事实包生成（属 **A3**） |
| 提供统一的 git 调用入口（强制 `-C`，禁用 cwd 依赖） | diff 范围合规判定 O5（属 **A6**，本任务只提供能力） |
| 仓库归属校验（`show-toplevel` 归一比对） | 分支策略、PR 流程 |
| 存量任务补建基线 | 远程仓库、push |

### 1.3 边界声明（必须诚实）

A1 提供的是**事实来源的确定性**，不是安全性。
拥有 shell 的 agent 可以 `git -C <target> commit` 伪造历史，
也可以直接改 `.state` 里的 `baseline_sha`。
后者由 **A0 的 HMAC 签名**兜底；前者**不在本任务防护范围内**，
本文档不假装解决。

---

## 2. 现状：已实测的事实

以下全部在本机（macOS，git 2.x）真实执行过，探针已清理。

### 2.1 D1 的三条原始定案复现成立

| 验证项 | 结果 |
|---|---|
| 子仓库内 `git diff --stat` 正常反映变更 | ✅ |
| 父仓库 `git status --porcelain` 看不到子仓库内部改动 | ✅（**前提：`.gitignore` 含 `repo/`**，见 2.2） |
| 移除 ignore 后 `git add repo/` 视为 embedded repo | ✅ 报 `warning: adding embedded git repository: repo/T1` |

### 2.2 修正 D1 的一处表述：父仓库并非"输出为空"

D1 写「父仓库 `git status` 看不到子仓库内部改动 ✅ 输出为空」。
实测：**未 ignore 时输出为 `?? repo/`**，并不为空。
只有 `.gitignore` 含 `repo/` 时才真正为空。

即嵌套 `.git` 提供的是「内部改动不泄漏」，
而目录本身仍以未跟踪项的形式出现。
**结论不变（两层保护都要留），但验收断言必须写成
「`git status --porcelain` 中不出现 `repo/` 下的具体文件路径」，
而不是「输出为空」** —— 后者在缺少 ignore 时会以误导的方式红。

### 2.3 ⚠️ 新发现：`show-toplevel` 朴素字符串比较必然误判

D1 纪律第 2 条与 A10 的校验表都写「`rev-parse --show-toplevel` 等于 `target_dir`」。
实测（macOS，`/tmp` 是指向 `/private/tmp` 的符号链接）：

```text
target_dir : /tmp/a1probe/parent/repo/T1
toplevel   : /private/tmp/a1probe/parent/repo/T1
字符串相等 : NO   <-- 朴素比较在此硬失败
Path.resolve() 后相等 : True
```

git 返回的是**真实路径**，而 `.state` 里存的是用户给的原始字符串。
若按字面比较，`/tmp` 下的任务会**全部误判为「仓库归属错误」并硬失败**。

**定案**：比较前双侧 `Path(...).resolve()` 归一。
这条不是优化，是正确性前提。

### 2.4 ⚠️ 新发现：全新任务目录为空，基线提交建不出来

`create_task` 创建的目标目录里**没有任何文件**（见 2.6），
而 git 拒绝空提交：

```text
$ git add -A && git commit -m baseline
On branch master

Initial commit
$ git rev-parse --short HEAD
fatal: Needed a single revision
```

即「`git init` + `commit`」这条朴素路径在全新任务上**必然拿不到 `baseline_sha`**。
而 A10 明确要求 `baseline_sha` 缺失即硬失败 —— 若不处理，
**每个新任务在归档时都会硬失败**。

实测两条可行解：

| 解法 | 结果 | 代价 |
|---|---|---|
| **A：`git commit --allow-empty`** | ✅ 拿到 sha，后续 diff 正常（`git diff --cached --stat` 正确显示新增文件） | 无。空提交是纯 harness 元数据 |
| B：先写 `.gitignore` 再 commit | ✅ 拿到 sha | 往用户仓库塞 harness 自己的文件 |

**定案：采用 A**。提交信息 `chore(harness): baseline for <task>`。

### 2.5 ⚠️ 新发现：`.sw-context` 的时序会污染基线或 diff，二者必居其一

`_write_context_marker`（`service.py:26`）会在目标目录写 `.sw-context`。
实测两种时序都有问题：

```text
基线提交【之前】写入 -> 基线含 .sw-context
  => agent 未写一行代码，基线里已有 harness 的文件

基线提交【之后】写入 -> git status 显示 ?? .sw-context
  => 会被误当成 agent 的产出计入 diff
```

上游文档未涉及此问题。

**定案**：`.sw-context` 在基线提交**之前**写入，并**同时**写入一个
由 harness 生成的 `.gitignore`（含 `.sw-context`），二者一并计入基线。
这样它既不出现在后续 diff 里，也不会被当作 agent 产出。

> 为什么不选「基线后写入 + 让 A3 在解析时过滤」：
> 过滤名单是散落的隐性知识，A3、A6、A10 三处都要各自记得排除，
> 漏一处就产生假事实。让它在基线里消失一次即可，是唯一收口点。

### 2.6 现状：目标目录的创建是副作用，且静默失败

`create_task`（`service.py:52`）**从不显式创建 `target_dir`**。
目录是 `_write_context_marker` 顺手建的（`service.py:33`），
而整个函数包在：

```python
except Exception:
    pass  # marker 文件创建失败不阻塞任务创建
```

即目标目录创建失败会被**静默吞掉**，`.state` 里仍留着一个
指向不存在目录的 `target_dir`。

`service.py:315` 之后才在 deploy 时检查 `Path(target_dir).is_dir()` 并抛错 ——
错误延迟到很晚才暴露，且提示与真实原因（创建时就失败了）无关。

**A1 必须把目录创建变成显式的、失败即报错的步骤。**

### 2.7 现状：仓库无 `git init`，全仓库零 git 调用

- `rg "git " sw_lib --type py` 除一处注释外**零结果** ——
  `sw_lib` 里没有任何 git 调用。
- git 调用只存在于 `hooks/check_05-archive.sh`，且是裸调用（1.1）。
- `config.yaml:9-10` 的 `base_branch: dev` / `branch_prefix: harness`
  **不被 Python 侧消费** —— `config.py` 未读取这两个键（`grep` 零结果）。

  ⚠️ **修正上游 D1 的一处断言**：D1 称二者「无人消费的死配置」，
  依据是 `grep "git checkout|git branch" sw_lib` 零结果。
  该判断只覆盖了 Python 侧。实测 shell 侧**确有消费**：

  | 位置 | 消费方式 |
  |---|---|
  | `config/config.sh:8,11` | `HARNESS_BASE_BRANCH="${HARNESS_BASE_BRANCH:-dev}"`（同名环境变量，带默认值） |
  | `bin/dispatch.sh:17,122` | `git worktree add -b "$BRANCH" ... "$BASE_BRANCH"` |
  | `config/cleanup.sh:70` | 检查分支是否已合并 |
  | `config/pr.sh` | PR 的 base 分支 |

  即存在**两套并行配置**：`config.yaml` 的键（无人读）与
  `config.sh` 的环境变量（真正生效，默认值恰好相同）。
  yaml 里那两行是**冗余且误导**的影子配置，
  但删除它不等于删除功能 —— worktree 流程仍靠环境变量正常工作。

### 2.8 重复 `git init` 是安全的

实测：已有提交的仓库再次 `git init` 后 HEAD 不变。
故初始化可写成幂等操作，无需先做存在性判断（但仍要判断，见 3.3 的存量分支）。

### 2.9 ⚠️ 实施期新发现：`GIT_INDEX_FILE` 不隔离对象写入

> 本节是**实施期**（而非撰写期）的发现，同时修正 **A3 的 2.2**。

`baseline_diff` 要让未跟踪文件可见，须先 `git add -A -N`（A3 的 2.1/2.2）。
A3 定案用 `GIT_INDEX_FILE` 隔离索引并记为「零副作用」。
用真实 CLI 在 harness 自身仓库上跑时暴露：

```text
$ git -C <harness 根> add -A -N        # 沙箱内，对象库不可写
error: unable to create temporary file: Operation not permitted
fatal: cannot create an empty blob in the object database
```

`add -N` 需要往对象库写空 blob。实测对象文件数：
隔离索引后仍从 **3 变 4**，加 `GIT_OBJECT_DIRECTORY` 重定向后维持 3。

即「真实索引未变」不足以证明零副作用 —— 索引没变，对象库变了。

**定案**：临时索引之外**同时**重定向 `GIT_OBJECT_DIRECTORY`，
并把真实对象库挂成 `GIT_ALTERNATE_OBJECT_DIRECTORIES`（否则读不到基线那棵树）。
真实 objects 路径用 `rev-parse --git-path objects` 取，
**不要拼 `<repo>/.git/objects`** —— worktree 与 submodule 下 `.git` 是文件。

这条同时消除一个「失败被翻译成合规」的入口：对象库不可写时
（只读挂载、容器化、CI 沙箱）原方案硬失败，而报错文面与 diff 毫无关系，
极易被上层当成「拿不到事实」而降级。

---

## 3. 设计

### 3.1 新增模块：`sw_lib/core/git_repo.py`

集中全部 git 调用。**该模块是 harness 内 git 调用的唯一出口**，
其他模块不得直接 `subprocess` 调 git。

```python
class GitRepoError(Exception):
    """git 操作失败。含命令、退出码、stderr。"""

def run_git(target_dir: str, *args: str) -> str:
    """在 target_dir 内执行 git，返回 stdout。

    强制拼入 `-C <resolved_target_dir>`。
    禁止依赖进程 cwd —— 这是 D1 纪律第 1 条。
    """

def ensure_repo(target_dir: str, task_name: str) -> BaselineInfo:
    """确保 target_dir 是独立 git 仓库且有基线提交。幂等。"""

def verify_ownership(target_dir: str) -> None:
    """校验 show-toplevel 归一后等于 target_dir，否则 GitRepoError。"""

def baseline_diff(target_dir: str, baseline_sha: str) -> DiffFacts:
    """相对基线的 diff 事实。供 A3 消费，本任务只保证正确性。"""
```

`BaselineInfo` 字段：

| 字段 | 含义 |
|---|---|
| `sha` | 基线提交完整 sha |
| `kind` | `"fresh"`（新建）/ `"reconstructed"`（存量补建）/ `"harness_self"`（`target_dir == "."`） |
| `note` | 人类可读说明；`reconstructed` 时含「基线为补建，diff 可能不完整」 |

> `kind` 必须是三态而非布尔。A10 的报告需要区分
> 「基线可信」与「基线为补建」—— 后者的 diff 不完整，
> 不得与前者一同计为「已达成」。这与 DEV-PROTOCOL 第 2 节的三态纪律同构。

### 3.2 `run_git` 的强制约束

```
1. 第一个参数必须是 target_dir，函数内部 resolve() 后拼 `-C`。
2. 不接受完整命令字符串，只接受 argv 序列（避免 shell 注入与拼接歧义）。
3. 调用前先 verify_ownership()，除 init 阶段自身。
4. 失败抛 GitRepoError，不返回空字符串 —— 空字符串会被上层
   误读为「没有改动」，那是最危险的假事实。
```

第 4 条是重点：**git 失败与「无改动」必须可区分**。
若 `git diff` 因仓库损坏失败而返回空，A6 的 O5 会判「改动全在范围内 ✅」，
A10 会报「无越界改动」—— 一个失败被翻译成了合规。

### 3.3 `ensure_repo` 的四条分支

| 场景 | 判定依据 | 行为 |
|---|---|---|
| **新建** | `target_dir/.git` 不存在 | `git init` → 写 `.gitignore` + `.sw-context` → `git add -A` → `commit --allow-empty` → `kind="fresh"` |
| **已是独立仓库** | `.git` 存在且 `show-toplevel` 归一等于自身 | 有 HEAD 则沿用；无 HEAD（空仓库）则补 `commit --allow-empty`。`kind="fresh"` |
| **存量：在父仓库内但无自己的 `.git`** | `.git` 不存在且 `show-toplevel` 指向别处 | 就地 `git init`，以当前状态为基线，`kind="reconstructed"`，note 标注「基线为补建，diff 可能不完整」 |
| **`target_dir == "."`** | 字面判断 | **不 `git init`**。基线取 harness 当前 HEAD，`kind="harness_self"`。diff 时排除 `workspace/` 与 `repo/` |

第三条对应 D1 的「存量任务」特例：**不静默通过，也不硬失败**。

`harness_self` 的 diff 排除已实测可用：

```bash
git diff --stat HEAD -- . ':(exclude)workspace' ':(exclude)repo'
```

### 3.4 `.state` 字段

`ensure_repo` 成功后，通过 **A0 的 `update_state` 受控入口**写入。

⚠️ **落点是 `review` 键，不是新增顶层 `git` 键。**
A0 已定案 `EVIDENCE_FIELDS` 为六元组（`evidence.py:33`），
其中 `review` 的注释明写「含 `baseline_sha`」（A0 的 3.2 / D0-4）。
**顶层新增 `git` 键不在签名范围内** —— 那样 `baseline_sha`
可被无痕篡改，A1 的价值归零。

```json
{
  "review": {
    "baseline_sha": "e496536...",
    "baseline_kind": "fresh",
    "baseline_note": "",
    "toplevel": "/abs/path/to/repo/T1",
    "baseline_created_at": "2026-08-23T..."
  }
}
```

> 这处歧义是撰写 A1 时与 A0 核对代码才发现的：
> A1 初稿设计的是顶层 `git` 键，而 A0 已把位置预留在 `review` 下。
> 两份文档若各自实现，签名保护会静默落空 ——
> `verify_evidence` 对不在 `EVIDENCE_FIELDS` 里的字段一律不管。

**必须走 `update_state`，禁止裸 `read_state` → 改 → `write_state`**
（A0 的 R1 实测：裸路径会抹掉并发签署的 Gate）。

`baseline_sha` 是判据字段，因落在 `review` 下而自动纳入 A0 的证据签名范围 ——
篡改它应使 `verify_evidence` 报 `tampered`。

> 与 A0 的接口约定：**无需改动 `EVIDENCE_FIELDS`** ——
> `review` 键已在其中（`evidence.py:33`）。
> A1 只需保证写入落在 `review` 下，签名自动覆盖。
>
> 同理 A3 的事实包哈希落在既有的 `facts` 键下（A3 的 3.6），
> 也无需改动该元组。**两个任务都不要去动 `EVIDENCE_FIELDS`**：
> 它是元组常量，并行改动时后提交者容易整体替换掉前者。

### 3.5 调用时机

| 时机 | 位置 | 说明 |
|---|---|---|
| 任务创建 | `service.create_task` | 显式创建目录（修 2.6）→ `ensure_repo` → 写 `.state` |
| 进入 04-review 前 | A3 事实包生成入口 | 存量任务在此补建（`reconstructed`） |

创建时失败**必须让 `create_task` 抛错**，不得沿用 2.6 的 `except: pass`。
理由：带着无效 `target_dir` 的任务在归档时才炸，那时上下文已丢失。

### 3.6 清理影子配置

删除 `config.yaml:9-10` 的 `base_branch` / `branch_prefix` 两行。

**只删 yaml，不动 shell 侧**（2.7 实测：`config.sh` 的同名环境变量
才是真正生效的配置，`dispatch.sh` / `cleanup.sh` / `pr.sh` 依赖它）。
`config.py` 无需改动 —— 它从未读取这两个键。

删除理由是消除「两套配置」的歧义：改 yaml 不生效会让人困惑很久。

> D1 原话是「建议本批删除」，但其依据（`grep sw_lib` 零结果）
> 只覆盖 Python 侧。若照字面理解为「功能无人使用」而顺手删掉
> `config.sh` 的变量，**会打断 worktree 创建流程**。
> 本任务只删 yaml 中无人读取的两行。

### 3.7 修正 `hooks/check_05-archive.sh`

裸 `git diff HEAD` 改为 `git -C "$TARGET_DIR" diff HEAD`，
`TARGET_DIR` 从 `.state` 读取。
这是 1.1 那条从第一天就看错仓库的检查。

---

## 4. mock 模式

`config.yaml` 的 `mock_agent.enabled` 影响测试路径（A0 的 2.9.3 实测：
本地为 `false`，测试走真实分支）。

A1 的 git 操作**不区分 mock**：mock agent 也需要真实仓库才能让
A3/A6/A10 的链路可测。
上游 P2 提到「mock 下事实包硬失败会挂 CI」，
但那是事实包的行为，不是仓库初始化的行为 —— A1 在 mock 下照常建仓库。

---

## 5. 与其他任务的接口

| 任务 | A1 提供 | 契约 |
|---|---|---|
| **A3** | `baseline_diff()` + `read_baseline()` + `baseline_sha` | 基线失效时**抛错而非跳过**（上游 T3 验收）。diff 采集**整体复用** `baseline_diff` —— 含 2.9 的对象库重定向，A3 不再自行实现临时索引（见 A3 的 3.3） |
| **A6** | `baseline_diff()` 的文件列表 | A1 未生效时 O5 记 `unavailable`，不记 pass |
| **A10** | `git -C` 入口 + `baseline_kind` | commit 只落任务仓库；`reconstructed` 须在报告中标注 |
| **A0** | 复用既有 `review` 证据键，**不改** `EVIDENCE_FIELDS` | 见 3.4 |

---

## 6. 实施顺序（内部）

1. `git_repo.py` 的 `run_git` + `verify_ownership`（含 2.3 的 resolve 归一）。
2. `ensure_repo` 四分支（含 2.4 的 `--allow-empty`、2.5 的 `.sw-context` 时序）。
3. 接入 `create_task`：显式建目录 + 失败抛错（修 2.6）。
4. `.state` 写入走 `update_state`；向 A0 追加签名字段。
5. `baseline_diff`。
6. 修 `hooks/check_05-archive.sh`；删死配置。

---

## 7. 验收标准

**机制接通类**（只验证接线，不足以宣布完成）：

1. 新建任务后 `target_dir/.git` 存在，且 `show-toplevel` 归一等于 `target_dir`。
2. 新建任务（**空目录**）能拿到有效 `baseline_sha`，`rev-parse` 可解析。
3. `.state` 的 `review.baseline_kind` 为 `"fresh"`。
4. 在子仓库内改文件后，harness 自身 `git status --porcelain`
   **不出现 `repo/` 下的具体文件路径**（按 2.2 的措辞，非「输出为空」）。
5. `target_dir` 位于 `/tmp` 等符号链接路径下时**不误判硬失败**（2.3 的回归锚点）。
6. `run_git` 对不存在的仓库抛 `GitRepoError`，**不返回空字符串**。
7. 存量任务（目录有内容、无 `.git`）走 `reconstructed` 分支，
   `baseline_note` 含「补建」字样。
8. `target_dir == "."` 时不执行 `git init`，harness 的 `.git` 未被改写，
   `baseline_kind == "harness_self"`。
9. `create_task` 在目标目录无法创建时**抛错**（修 2.6 的静默失败）。
10. `config.yaml` 中 `base_branch` / `branch_prefix` 两行已删除；
    **`config/config.sh` 的 `HARNESS_BASE_BRANCH` / `HARNESS_BRANCH_PREFIX`
    仍在**（2.7：删掉会打断 worktree 流程）。
    断言方式：yaml 中无这两个键，且 `bin/dispatch.sh` 仍能取到 base 分支。
11. `hooks/check_05-archive.sh` 中不存在裸 `git diff`
    （源码断言：出现 `git diff` 时必须同时出现 `-C`）。
12. `.sw-context` 与 harness 生成的 `.gitignore` 均在基线提交内，
    基线建立后 `git status --porcelain` 为空。
13. 全部既有测试通过（**单调性**）。

**有效性类**（唯一能证明「真的解决了歧义」的判据）：

14. **仓库归属混淆可被检出**：构造一个 `target_dir` 指向 harness 根目录
    的任务，`verify_ownership` 必须硬失败。
    这直接对应 D1 纪律第 2 条「返回 harness 根目录则硬失败」。
15. **diff 归属正确**：在 harness 根目录与任务仓库**各留一处改动**，
    `baseline_diff(target_dir, sha)` 的结果**只含任务仓库的那处**。

    这是本任务的核心判据 —— 1.1 的缺陷正是「看错了仓库」。
    只断言「diff 非空」不足以证明它看的是对的仓库。
16. **git 失败不被翻译成合规**：让 `git diff` 失败（如删除 `.git`），
    断言上层收到 `GitRepoError`，而非「无改动」。
    对应 3.2 第 4 条。
17. **基线篡改可检出**（依赖 A0）：直接改 `.state` 里的
    `review.baseline_sha` 后，`verify_evidence` 返回 `tampered`。
    **本条同时验证落点正确** —— 若误写成顶层 `git` 键，
    签名不覆盖，此条必红。
    A0 未就绪时本条记 ❓ 并说明原因，**不得记为通过**。

> 第 15 条是刻意设计的。A1 的全部价值就是让「diff 指向哪个仓库」
> 不再有歧义。如果这条测不出来，前 14 条都只是在证明「git 命令能跑」。

---

## 8. 风险与遗留

| # | 风险 | 处置 |
|---|---|---|
| R1 | 用户的目标目录本就是某个大仓库的子目录，`git init` 造成嵌套 | 这正是 D1 的定案形态。`.gitignore` + 嵌套 `.git` 双层隔离；embedded repo 警告已实测（2.1） |
| R2 | 空基线提交（`--allow-empty`）让 git 历史多一条无内容提交 | 接受。提交信息带 `chore(harness):` 前缀可识别。代价远小于「拿不到 baseline_sha」 |
| R3 | agent 可 `git -C <target> commit --amend` 改写基线 | **不解决**，见 1.3。A0 的签名只保护 `.state` 里的 sha，不保护 git 历史本身 |
| R4 | `git` 可执行文件不存在或版本过旧 | `ensure_repo` 首次调用时探测，失败抛 `GitRepoError` 并给出可执行的提示；不静默降级 |
| R5 | 用户已有的 `.gitignore` 被 harness 覆盖 | **必须先检查存在性**：已存在则追加而非覆盖，且只追加缺失的行 |
| R6 | Windows 路径与符号链接语义不同 | 本批只保证 macOS / Linux。`resolve()` 是跨平台 API，但未实测 Windows，记为 ❓ |

**明确遗留、不假装解决的问题**：

- **U1-1**：agent 可伪造 git 历史（R3）。A1 只消除歧义，不防伪造。
- **U1-2**：`reconstructed` 基线的 diff 本质上不完整 ——
  补建之前的改动无法追溯。这是存量任务的固有代价，
  处置是**标注并向上传递**，而非假装完整。
- **U1-3**：远程仓库、分支策略均不在本批。删除 yaml 中的
  `base_branch` / `branch_prefix` 只是消除影子配置；
  真实的分支能力仍在 `config.sh` + `dispatch.sh` 的 worktree 流程里，
  A1 不触碰它，也不将其纳入任务级仓库模型 ——
  **两者的关系（worktree 流程 vs 任务级独立仓库是否重叠）尚未厘清，
  记为待定，不在本批假设答案。**

---

## 9. 本任务的红绿要点

### 9.1 自指风险

A1 本身自指风险低（不实现"检查自己"的机制），但有一个**环境自伤风险**：

> 本任务的测试要反复 `git init` / `commit`。
> **若测试误在 harness-flow 仓库自身执行这些操作，会污染用户的 git 状态。**

强制纪律：

1. 全部 git 测试在 `tmp_path` 内进行，**绝不碰 `/Users/.../harness-flow/.git`**。
2. `target_dir == "."` 分支的测试**必须构造一个假的 harness 根**
   （临时目录 + 自己的 `.git`），不得用真实仓库。
3. 测试后断言 harness 自身 `git status --porcelain` 与测试前一致。

> 第 3 条是从 A0 的实际事故里学来的：
> A0 实施期曾用真实路径做探针，截断了 `workspace/tasks/T1/.state`。
> 探针一律走 tmp，且**事后验证真实环境未变**。

### 9.2 红的正确形态

| 验收项 | 红的正确形态 | 假绿风险 |
|---|---|---|
| 2 空目录拿到 sha | 未实现 `--allow-empty` 时，`rev-parse HEAD` 抛 `fatal: Needed a single revision`。**须断言 sha 可解析，而非断言 commit 命令退出码为 0** | 只断言 `git commit` 没抛异常 —— 实测它在空目录下**也不抛**（返回非零但输出 "Initial commit"），于是 sha 缺失被漏过 |
| 5 符号链接不误判 | 在 `tmp_path`（macOS 下即 `/private/var/...` 的符号链接形式）构造任务，朴素字符串比较应红 | 用已 resolve 过的路径构造测试 —— 那样两边天生相等，永远绿。**必须故意传入未归一的路径** |
| 14 归属混淆检出 | `verify_ownership` 未实现时不抛错，断言「必须抛 `GitRepoError`」为红 | 断言「不等于某个值」而非「抛错」，实现返回 `False` 也能满足 |
| 15 diff 归属正确 | 用裸 `git diff`（不带 `-C`）实现时，结果会含 harness 的改动，断言应红 | **只断言 diff 非空**。必须断言 harness 那处改动的文件名**不在**结果里 |
| 16 失败不翻译成合规 | 实现若 `except: return ""`，断言「抛 GitRepoError」为红 | 断言返回值为空 —— 那恰好是要禁止的行为，方向反了 |
| 4 父仓库不泄漏 | 按 2.2，断言「不含 `repo/` 下具体文件路径」 | 断言 `git status` 输出为空 —— 无 ignore 时会以误导方式红，让人以为隔离失效 |
| 12 `.sw-context` 不在 diff | 若在基线**后**写入，`git status` 会显示 `?? .sw-context`，断言为红 | 测试里根本不调用 `_write_context_marker`，于是文件不存在，断言天然通过 |
| 17 基线篡改检出 | 依赖 A0。A0 未就绪时**记 ❓ 并说明**，不得记 ✅ | 用「A0 会处理」的口径跳过，最终没人验 |

### 9.3 特别提醒：第 2 项的红很容易假绿

实测记录（本文 2.4）：

```text
$ git add -A && git commit -qm baseline   # 空目录
On branch master

Initial commit
$ git rev-parse --short HEAD
fatal: Needed a single revision
```

`git commit` 在这里的行为是「打印说明并返回非零」，
若实现用 `subprocess.run(...)` 而**不检查 returncode**，
或用 `except: pass` 包住，流程会继续往下走，
`baseline_sha` 悄悄变成空字符串。

**断言必须落在「`baseline_sha` 可被 `rev-parse` 解析」上**，
而不是「初始化函数没抛异常」。

### 9.4 单调性

删除 `base_branch` / `branch_prefix` 与修改 hook 可能让既有测试红。
按 DEV-PROTOCOL 第 3 节第 3 条，须逐个分辨：

- 测试断言了 yaml 中那两个键的存在 → 该测试本身应删（Python 侧从不读它）。
- 测试断言了 hook 的裸 `git diff` 行为 → 属**真实检出**，更新断言。
- 测试或脚本依赖 `HARNESS_BASE_BRANCH` → **不是死配置，不许删**（2.7）。

**不得为了让存量测试变绿而保留 yaml 影子配置；
也不得因为「D1 说是死配置」而误删 shell 侧真配置。**

---

## 10. 回滚

| 层 | 回滚方式 |
|---|---|
| `.state` 的 `review.baseline_*` 字段 | 纯新增子字段，下游读不到时按 `unavailable` 处理（A6 已有该三态） |
| `ensure_repo` 调用 | `create_task` 中去掉调用即回到现状；已建的 `.git` 需手动删除 |
| hook 修正 | 单文件回滚 |
| 死配置删除 | 恢复两行 yaml + 两个字段 |

> 已建立的任务仓库**不随回滚删除** —— 那会丢用户代码。
> 回滚只停止新建，不清理既有。
