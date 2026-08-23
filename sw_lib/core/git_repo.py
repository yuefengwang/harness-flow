"""sw_lib.core.git_repo — harness 内 **唯一** 的 git 调用出口。

设计依据：docs/design/A1-task-git-repo.md。

**要解决的问题**：`git diff` 语义不明 —— 分不清看到的是 harness-flow 自身的
改动，还是任务目标仓库的改动。`hooks/check_05-archive.sh` 从写下的第一天起
就在看错的仓库（裸 `git diff`，cwd 是 harness 根）。

**边界（不假装解决）**：本模块提供的是**事实来源的确定性**，不是安全性。
拥有 shell 的 agent 可以 `git -C <target> commit` 伪造历史。`.state` 里的
`baseline_sha` 由 A0 的 HMAC 兜底，git 历史本身不在防护范围内。
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import ROOT

# harness_self 分支下 diff 要排除的目录：任务产出与运行状态都在这两处，
# 不属于「harness 自身代码改动」。
HARNESS_SELF_EXCLUDES = ("workspace", "repo")

# 基线提交要写进 .gitignore 的行。`.sw-context` 必须在这里 ——
# 否则它要么污染基线，要么被当成 agent 的产出计入 diff（A1 的 2.5）。
GITIGNORE_LINES = (".sw-context",)

_BASELINE_MESSAGE = "chore(harness): baseline for {task}"

# harness 自己的记账文件，判定「目录里是否已有存量内容」时不算内容。
# 漏掉这层会把每个新任务都判成 reconstructed —— `.sw-context` 由
# create_task 在基线之前写入（A1 的 2.5），届时目录必然「非空」。
# 与 hooks/lib_run_tests.sh 的 has_code_output 同一口径。
_HARNESS_BOOKKEEPING = frozenset({".sw-context", ".git", ".DS_Store"})


class GitRepoError(Exception):
    """git 操作失败。含命令、退出码、stderr。

    **为什么必须是异常而不是空返回值**：空字符串会被上层误读为「没有改动」。
    A6 的 O5 会判「改动全在范围内 ✅」，A10 会报「无越界改动」——
    一个失败被翻译成了合规。那是本模块最需要避免的假事实。
    """

    def __init__(self, message: str, *, argv: Optional[List[str]] = None,
                 returncode: Optional[int] = None, stderr: str = ""):
        self.argv = argv or []
        self.returncode = returncode
        self.stderr = stderr
        detail = message
        if argv:
            detail += f"\n  命令: {' '.join(argv)}"
        if returncode is not None:
            detail += f"\n  退出码: {returncode}"
        if stderr:
            detail += f"\n  stderr: {stderr.strip()}"
        super().__init__(detail)


@dataclass
class BaselineInfo:
    """基线提交的三态描述。

    `kind` 必须三态而非布尔：A10 的报告需要区分「基线可信」与「基线为补建」——
    后者的 diff 不完整，不得与前者一同计为「已达成」。
    """
    sha: str
    kind: str            # fresh | reconstructed | harness_self
    note: str = ""
    toplevel: str = ""


@dataclass
class DiffFacts:
    """相对基线的 diff 事实。供 A3 消费。"""
    files: List[str] = field(default_factory=list)
    numstat: str = ""
    stat: str = ""
    patch: str = ""


# ── 底层调用 ──

def _git_env() -> Dict[str, str]:
    """交给 git 的环境。

    剥掉 `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE`：外部若设了这些，
    `-C` 会被它们悄悄覆盖 —— 那就绕回了「不知道在操作哪个仓库」的原点。
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    # 基线提交不能依赖用户的 git 身份配置：CI / 全新机器上可能没有 user.name，
    # 那会让 commit 失败，进而 baseline_sha 缺失。
    env.setdefault("GIT_AUTHOR_NAME", "harness")
    env.setdefault("GIT_AUTHOR_EMAIL", "harness@local")
    env.setdefault("GIT_COMMITTER_NAME", "harness")
    env.setdefault("GIT_COMMITTER_EMAIL", "harness@local")
    return env


def resolve_target_dir(target_dir: str) -> Path:
    """归一化 target_dir —— **全 harness 唯一的一处口径**。

    相对路径按 harness 根解释，而不是按进程 cwd（D1 纪律第 1 条禁 cwd 依赖）。
    `config.yaml` 的 `repo_path: repo` 指的就是 harness 根下的 `repo/`，
    而 `sw` 可以从任意目录调用、Web 进程的工作目录也未必相同。

    `service._prepare_target_dir` 必须复用本函数：两处各自解析时，
    实测会出现「目录建在 cwd 下、仓库初始化找 ROOT 下」的分裂。
    """
    p = Path(target_dir)
    if not p.is_absolute():
        p = Path(ROOT) / p
    # strict=False：目录可能还不存在（ensure_repo 会建），此时仍要归一符号链接。
    return p.resolve()


# 内部简写，保持既有调用点可读
_resolve = resolve_target_dir


def run_git(target_dir: str, *args: str, env_extra: Optional[Dict[str, str]] = None,
            check_dir: bool = True) -> str:
    """在 target_dir 内执行 git，返回 stdout。

    强制拼入 `-C <resolved_target_dir>`，**禁止依赖进程 cwd**（D1 纪律第 1 条）。
    只接受 argv 序列，不接受完整命令字符串（避免 shell 注入与拼接歧义）。
    失败抛 `GitRepoError`，**绝不返回空字符串**（3.2 第 4 条）。
    """
    if not args:
        raise GitRepoError("run_git 至少需要一个 git 子命令参数")
    for a in args:
        if not isinstance(a, str):
            raise GitRepoError(f"git 参数必须是字符串，实际 {type(a).__name__}")
        # 单个参数里带空格且形如 "status --porcelain" —— 调用方把整条命令
        # 当字符串传进来了。放过去会得到一个诡异的 git 报错，不如当场拒绝。
        if " -" in a or a.strip().count(" ") and a.strip().split()[0] in _GIT_SUBCOMMANDS:
            raise GitRepoError(
                f"run_git 只接受 argv 序列，不接受完整命令字符串: {a!r}")

    d = _resolve(target_dir)
    if check_dir and not d.is_dir():
        raise GitRepoError(f"git 目标目录不存在: {d}")

    argv = ["git", "-C", str(d), *args]
    env = _git_env()
    if env_extra:
        env.update(env_extra)

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    except FileNotFoundError as e:
        # 风险 R4：git 不存在时给出可执行的提示，不静默降级。
        raise GitRepoError(
            "未找到 git 可执行文件。harness 依赖 git 建立任务基线，"
            "请安装 git（macOS: xcode-select --install）后重试。",
            argv=argv) from e

    if proc.returncode != 0:
        raise GitRepoError(f"git 命令失败: {' '.join(args)}", argv=argv,
                           returncode=proc.returncode, stderr=proc.stderr)
    return proc.stdout.strip()


_GIT_SUBCOMMANDS = frozenset({
    "status", "diff", "add", "commit", "init", "rev-parse", "log",
    "ls-tree", "ls-files", "show", "checkout", "branch",
})


def git_version() -> str:
    """探测 git 可用性（风险 R4）。失败抛 GitRepoError。"""
    try:
        proc = subprocess.run(["git", "--version"], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise GitRepoError(
            "未找到 git 可执行文件。harness 依赖 git 建立任务基线，"
            "请安装 git 后重试。") from e
    if proc.returncode != 0:
        raise GitRepoError("git --version 执行失败", returncode=proc.returncode,
                           stderr=proc.stderr)
    return proc.stdout.strip()


# ── 归属校验 ──

def _toplevel(target_dir: str) -> Path:
    out = run_git(target_dir, "rev-parse", "--show-toplevel")
    if not out:
        raise GitRepoError(f"无法确定仓库根: {target_dir}")
    return Path(out).resolve()


def verify_ownership(target_dir: str) -> Path:
    """校验 `show-toplevel` 归一后等于 target_dir，否则抛 GitRepoError。

    **归一不可省**（A1 的 2.3 实测）：git 返回真实路径，`.state` 存的是用户
    给的原始字符串。macOS 下 `/tmp` 是 `/private/tmp` 的符号链接，朴素字符串
    比较会让 `/tmp` 下的任务**全部误判为「仓库归属错误」并硬失败**。
    这不是优化，是正确性前提。
    """
    d = _resolve(target_dir)
    top = _toplevel(target_dir)

    harness_root = Path(ROOT).resolve()
    # D1 纪律第 2 条：toplevel 落在 harness 根就硬失败，**不区分 target_dir
    # 是否恰好等于 harness 根**。二者失效后果相同 —— 对它 diff/commit 读写的是
    # harness-flow 自身。`target_dir == "."` 是显式声明的例外，由
    # is_harness_self 分支单独处理，不走本函数。
    if top == harness_root:
        raise GitRepoError(
            f"仓库归属错误：{d} 的仓库根是 harness 自身（{top}）。"
            f"对它执行 git 操作会读写 harness-flow 而非任务产出。"
            f"请为该任务建立独立仓库（ensure_repo），"
            f"或在确实要改 harness 自身时显式使用 target_dir='.'。")

    if top != d:
        raise GitRepoError(
            f"仓库归属错误：target_dir={d}，但 git 认为仓库根是 {top}。"
            f"该目录不是独立仓库，对它的 diff 会掺入上层仓库的改动。")
    return top


def is_harness_self(target_dir: str) -> bool:
    """target_dir 是否指向 harness 自身。"""
    if str(target_dir).strip() in (".", ""):
        return True
    try:
        return _resolve(target_dir) == Path(ROOT).resolve()
    except Exception:
        return False


# ── 基线建立 ──

def _has_head(target_dir: str) -> bool:
    try:
        run_git(target_dir, "rev-parse", "--verify", "HEAD")
        return True
    except GitRepoError:
        return False


def _write_gitignore(d: Path) -> None:
    """写/追加 harness 需要的 ignore 规则。

    风险 R5：用户已有的 `.gitignore` **只追加缺失行，绝不覆盖**。
    """
    gi = d / ".gitignore"
    if gi.exists():
        body = gi.read_text(encoding="utf-8")
        existing = {ln.strip() for ln in body.splitlines()}
        missing = [ln for ln in GITIGNORE_LINES if ln not in existing]
        if not missing:
            return
        prefix = "" if body.endswith("\n") or not body else "\n"
        gi.write_text(body + prefix + "\n".join(missing) + "\n", encoding="utf-8")
        return
    gi.write_text("\n".join(GITIGNORE_LINES) + "\n", encoding="utf-8")


def _commit_baseline(target_dir: str, task_name: str) -> str:
    """建立基线提交并返回**可解析的**完整 sha。

    `--allow-empty` 不可省（A1 的 2.4 实测）：全新任务目录里没有任何文件，
    而 git 拒绝空提交 —— 它会打印 "Initial commit" 并返回非零，
    于是 `rev-parse HEAD` 报 `fatal: Needed a single revision`，
    `baseline_sha` 悄悄变成空串，每个新任务在归档时都会硬失败。
    """
    d = _resolve(target_dir)
    _write_gitignore(d)
    run_git(target_dir, "add", "-A")
    # .sw-context 已被 .gitignore 排除，但它必须**在基线里**，
    # 否则后续 git status 会把它显示成 ?? 并被当作 agent 的产出。
    if (d / ".sw-context").exists():
        run_git(target_dir, "add", "-f", ".sw-context")
    run_git(target_dir, "commit", "--allow-empty", "--no-verify", "-q",
            "-m", _BASELINE_MESSAGE.format(task=task_name))
    sha = run_git(target_dir, "rev-parse", "HEAD")
    if not sha or len(sha) != 40:
        raise GitRepoError(
            f"基线提交后仍拿不到可用的 sha（得到 {sha!r}）。"
            f"没有 baseline_sha，下游的 diff 事实无从锚定。")
    return sha


def ensure_repo(target_dir: str, task_name: str) -> BaselineInfo:
    """确保 target_dir 是独立 git 仓库且有基线提交。幂等。

    四条分支（A1 的 3.3）：

    - `target_dir == "."` → **不 init**，基线取 harness 当前 HEAD，`harness_self`
    - 已是独立仓库且有 HEAD → 沿用，`fresh`
    - `.git` 不存在但落在别的仓库里 → 就地 init，`reconstructed`（基线不完整）
    - 其余（空目录 / 无 HEAD 的空仓库）→ init + 空基线提交，`fresh`
    """
    git_version()   # R4：先探测，失败给出可执行提示

    # 分支四：harness 自身。
    if is_harness_self(target_dir):
        root = Path(ROOT).resolve()
        if not _has_head(str(root)):
            raise GitRepoError(
                f"target_dir 指向 harness 自身（{root}）但它没有 HEAD，"
                f"无法取得基线。harness 自身仓库不由本模块初始化。")
        sha = run_git(str(root), "rev-parse", "HEAD")
        return BaselineInfo(
            sha=sha, kind="harness_self",
            note="基线为 harness 自身 HEAD；diff 已排除 workspace/ 与 repo/。"
                 "改动 harness 自身须由人提交。",
            toplevel=str(root))

    d = _resolve(target_dir)
    if not d.exists():
        raise GitRepoError(f"target_dir 不存在: {d}（应由调用方先显式创建）")
    if not d.is_dir():
        raise GitRepoError(f"target_dir 不是目录: {d}")

    has_own_git = (d / ".git").exists()

    # 分支二：已是独立仓库。
    if has_own_git:
        verify_ownership(str(d))
        if _has_head(str(d)):
            sha = run_git(str(d), "rev-parse", "HEAD")
            return BaselineInfo(sha=sha, kind="fresh", note="",
                                toplevel=str(_toplevel(str(d))))
        # 已 init 但无 HEAD 的空仓库：补基线提交。
        sha = _commit_baseline(str(d), task_name)
        return BaselineInfo(sha=sha, kind="fresh", note="",
                            toplevel=str(_toplevel(str(d))))

    # 分支三：无自己的 .git，但目录已有内容 —— 存量任务，就地补建。
    # **不静默通过，也不硬失败**：标注基线不完整并向上传递。
    inherited: Optional[Path] = None
    try:
        inherited = _toplevel(str(d))
    except GitRepoError:
        inherited = None

    has_content = any(p.name not in _HARNESS_BOOKKEEPING for p in d.iterdir())
    reconstructed = has_content or inherited is not None

    run_git(str(d), "init", "-q", ".")
    verify_ownership(str(d))
    sha = _commit_baseline(str(d), task_name)

    if reconstructed:
        note = ("基线为补建（目录在此之前已有内容或隶属上层仓库），"
                "补建之前的改动无法追溯，diff 可能不完整。")
        if inherited is not None:
            note += f" 原上层仓库: {inherited}"
        return BaselineInfo(sha=sha, kind="reconstructed", note=note,
                            toplevel=str(_toplevel(str(d))))
    return BaselineInfo(sha=sha, kind="fresh", note="",
                        toplevel=str(_toplevel(str(d))))


# ── diff 事实 ──

def _diff_pathspec(target_dir: str) -> List[str]:
    """harness_self 时排除 workspace/ 与 repo/（3.3 实测可用）。"""
    if is_harness_self(target_dir):
        return ["--", ".", *(f":(exclude){p}" for p in HARNESS_SELF_EXCLUDES)]
    return []


def _git_object_dir(repo_dir: Path) -> Path:
    """真实对象库路径。

    不假定 `<repo>/.git` 是目录 —— worktree 与 submodule 下它是一个含
    `gitdir:` 的文件，此时对象库在别处。问 git 本人最可靠。
    """
    try:
        out = run_git(str(repo_dir), "rev-parse", "--git-path", "objects")
        p = Path(out)
        if not p.is_absolute():
            p = repo_dir / p
        return p.resolve()
    except GitRepoError:
        return (repo_dir / ".git" / "objects").resolve()


def baseline_diff(target_dir: str, baseline_sha: str,
                  include_patch: bool = False) -> DiffFacts:
    """相对基线的 diff 事实。失败抛 GitRepoError，不返回空 DiffFacts。

    用**临时索引**而不是裸 `git diff`（A3 的 2.2 定案）：agent 通常不自己
    commit，裸 diff 看不见未跟踪文件 —— 事实包会漏掉 agent 的主要产出，
    reviewer 拿到一个几乎空的 diff 然后判「无改动，通过」。
    临时索引以真实索引为起点（否则 agent 已暂存的内容会被判成新增），
    且**不改变**真实暂存状态。

    ⚠️ 仅隔离索引**不够**（实测修正 A3 的 2.2「零副作用」）：
    `git add -A -N` 仍会把空 blob 写进**真实**对象库。后果有二 ——
    观察改变了被观察对象；对象库不可写时（沙箱、只读挂载）整条链路抛错，
    且报错看起来与 diff 无关（实测：`cannot create an empty blob in the
    object database`）。因此对象写入也要重定向到临时目录，
    并把真实对象库挂成 alternate 以便读取既有对象。
    """
    if not baseline_sha:
        raise GitRepoError("baseline_diff 需要 baseline_sha —— "
                           "缺失时必须硬失败，不得当作『无改动』")

    repo_dir = Path(ROOT).resolve() if is_harness_self(target_dir) \
        else _resolve(target_dir)
    if not is_harness_self(target_dir):
        verify_ownership(str(repo_dir))

    # 基线不可解析 → 抛错而非跳过（A3 契约）。
    run_git(str(repo_dir), "rev-parse", "--verify", f"{baseline_sha}^{{commit}}")

    pathspec = _diff_pathspec(target_dir)
    tmpdir = tempfile.mkdtemp(prefix="harness-idx-")
    idx = Path(tmpdir) / "idx"
    tmp_objects = Path(tmpdir) / "objects"
    try:
        real_idx = repo_dir / ".git" / "index"
        if real_idx.exists():
            shutil.copy2(real_idx, idx)
        tmp_objects.mkdir(parents=True, exist_ok=True)
        env_extra = {
            "GIT_INDEX_FILE": str(idx),
            # 新对象落临时目录；真实对象库作为 alternate 只读可达。
            "GIT_OBJECT_DIRECTORY": str(tmp_objects),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(_git_object_dir(repo_dir)),
        }
        # -N：只登记路径，不写对象；足以让 diff 看见未跟踪文件。
        run_git(str(repo_dir), "add", "-A", "-N", env_extra=env_extra)

        numstat = run_git(str(repo_dir), "diff", baseline_sha, "--numstat",
                          *pathspec, env_extra=env_extra)
        stat = run_git(str(repo_dir), "diff", baseline_sha, "--stat",
                       *pathspec, env_extra=env_extra)
        patch = ""
        if include_patch:
            patch = run_git(str(repo_dir), "diff", baseline_sha, *pathspec,
                            env_extra=env_extra)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    files = []
    for line in numstat.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        files.append(parts[-1])
    return DiffFacts(files=files, numstat=numstat, stat=stat, patch=patch)


# ── .state 落点 ──

def record_baseline(task_name: str, target_dir: str) -> BaselineInfo:
    """建立基线并写入 `.state` 的 **`review`** 键。

    ⚠️ 落点是 `review`，**不是新增顶层 `git` 键**（A1 的 3.4）：
    A0 的 `EVIDENCE_FIELDS` 已含 `review`，顶层 `git` 不在签名范围内 ——
    那样 `baseline_sha` 可被无痕篡改，A1 的价值归零。

    必须走 `update_state`（受控入口）。裸 `read_state` → 改 → `write_state`
    在并发下会抹掉别人刚签署的 Gate（A0 的 R1 实测）。
    """
    from .state import update_state
    from .utils import now

    info = ensure_repo(target_dir, task_name)

    def _mutator(st):
        review = dict(st.get("review") or {})
        review.update({
            "baseline_sha": info.sha,
            "baseline_kind": info.kind,
            "baseline_note": info.note,
            "toplevel": info.toplevel,
            "baseline_created_at": now(),
        })
        st["review"] = review
        st["updated_at"] = now()
        return st

    update_state(task_name, _mutator)
    return info


def read_baseline(task_name: str) -> Optional[BaselineInfo]:
    """从 `.state` 读回基线信息。缺失返回 None —— 由调用方决定三态处置。

    **不要把 None 当作「基线正常」**：A6 在此情形下记 `unavailable`，不记 pass。
    """
    from .state import read_state
    review = (read_state(task_name) or {}).get("review") or {}
    sha = review.get("baseline_sha")
    if not sha:
        return None
    return BaselineInfo(sha=sha, kind=review.get("baseline_kind", ""),
                        note=review.get("baseline_note", ""),
                        toplevel=review.get("toplevel", ""))
