"""A1：任务级独立 git 仓库与基线锚定。

设计依据：docs/design/A1-task-git-repo.md（验收标准 1-17）。

⚠️ 环境自伤纪律（A1 的 9.1）：全部 git 写操作只在 tmp_path 内进行。
`target_dir == "."` 分支用**假的 harness 根**（临时目录 + 自己的 .git），
绝不在真实 harness-flow 仓库上 init/commit。会话结束时
test_harness_repo_untouched 断言真实仓库状态未变。
"""
import os
import subprocess
from pathlib import Path

import pytest

from sw_lib.core import git_repo
from sw_lib.core.git_repo import GitRepoError


# ── 辅助 ──

HARNESS_ROOT = Path(__file__).resolve().parents[3]


def _git(cwd, *args):
    """测试自己用的 git，刻意与被测实现无关（不复用 run_git）。"""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@l",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@l"},
    )


def _fake_parent_repo(tmp_path):
    """构造「父仓库 + repo/ 子目录」这个真实形态（A1 的 2.1）。"""
    parent = tmp_path / "parent"
    (parent / "repo").mkdir(parents=True)
    _git(parent.parent, "init", "-q", str(parent))
    (parent / ".gitignore").write_text("repo/\n", encoding="utf-8")
    (parent / "README.md").write_text("parent readme\n", encoding="utf-8")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-qm", "parent init")
    return parent


# ── 验收 1 / 2 / 12：新建任务的基线 ──

def test_fresh_repo_has_own_git_and_owned_toplevel(tmp_path):
    """验收 1：target_dir/.git 存在，且 show-toplevel 归一后等于 target_dir。"""
    target = tmp_path / "repo" / "T1"
    target.mkdir(parents=True)

    git_repo.ensure_repo(str(target), "T1")

    assert (target / ".git").exists(), "target_dir 下应有自己的 .git"
    top = _git(target, "rev-parse", "--show-toplevel").stdout.strip()
    assert Path(top).resolve() == target.resolve(), (
        f"show-toplevel 归一后应等于 target_dir，实际 {top}")


def test_empty_target_dir_yields_resolvable_baseline_sha(tmp_path):
    """验收 2：**空目录**也必须拿到可 rev-parse 的 baseline_sha。

    9.3 的假绿警告：不得断言「commit 命令没抛异常」——
    空目录下 git commit 会打印 "Initial commit" 并返回非零，
    异常不抛而 sha 悄悄变成空串。断言必须落在「sha 可被解析」上。
    """
    target = tmp_path / "repo" / "T-empty"
    target.mkdir(parents=True)
    assert not any(target.iterdir()), "前提：目录必须是空的"

    info = git_repo.ensure_repo(str(target), "T-empty")

    assert info.sha, "baseline_sha 不得为空"
    proc = _git(target, "rev-parse", "--verify", f"{info.sha}^{{commit}}")
    assert proc.returncode == 0, (
        f"baseline_sha 无法被 rev-parse 解析: {info.sha!r} / {proc.stderr}")
    assert len(info.sha) == 40, f"应为完整 sha，实际 {info.sha!r}"


def test_baseline_kind_is_fresh_for_new_repo(tmp_path):
    """验收 3：新建任务的 kind 为 fresh。"""
    target = tmp_path / "repo" / "T2"
    target.mkdir(parents=True)
    info = git_repo.ensure_repo(str(target), "T2")
    assert info.kind == "fresh", f"新建任务应为 fresh，实际 {info.kind!r}"


def test_marker_and_gitignore_are_inside_baseline_commit(tmp_path):
    """验收 12：`.sw-context` 与 harness 生成的 `.gitignore` 均在基线提交内，
    基线建立后 `git status --porcelain` 为空。

    9.2 的假绿警告：不得跳过 marker 写入 —— 那样文件不存在，断言天然通过。
    这里显式调用 service 的 marker 写入，走真实时序。
    """
    from sw_lib.core.service import _write_context_marker

    target = tmp_path / "repo" / "T3"
    target.mkdir(parents=True)

    _write_context_marker(str(target), "T3", "feature")
    assert (target / ".sw-context").exists(), "前提：marker 必须真的写出来"

    git_repo.ensure_repo(str(target), "T3")

    tracked = _git(target, "ls-tree", "--name-only", "-r", "HEAD").stdout.split()
    assert ".sw-context" in tracked, f"marker 应在基线提交内，实际 {tracked}"
    assert ".gitignore" in tracked, f".gitignore 应在基线提交内，实际 {tracked}"

    status = _git(target, "status", "--porcelain").stdout.strip()
    assert status == "", f"基线建立后工作区应干净，实际残留:\n{status}"


def test_baseline_commit_message_is_identifiable(tmp_path):
    """A1 的 2.4 定案：提交信息为 `chore(harness): baseline for <task>`。"""
    target = tmp_path / "repo" / "T-msg"
    target.mkdir(parents=True)
    git_repo.ensure_repo(str(target), "T-msg")
    subject = _git(target, "log", "-1", "--pretty=%s").stdout.strip()
    assert subject == "chore(harness): baseline for T-msg", subject


def test_ensure_repo_is_idempotent(tmp_path):
    """A1 的 2.8：重复调用不改写已有基线。"""
    target = tmp_path / "repo" / "T-idem"
    target.mkdir(parents=True)
    first = git_repo.ensure_repo(str(target), "T-idem")
    second = git_repo.ensure_repo(str(target), "T-idem")
    assert second.sha == first.sha, "重复 ensure_repo 不应改写基线"


def test_existing_gitignore_is_appended_not_overwritten(tmp_path):
    """风险 R5：用户已有 .gitignore 只追加缺失行，不得覆盖。"""
    target = tmp_path / "repo" / "T-ignore"
    target.mkdir(parents=True)
    (target / ".gitignore").write_text("*.log\nnode_modules/\n", encoding="utf-8")

    git_repo.ensure_repo(str(target), "T-ignore")

    body = (target / ".gitignore").read_text(encoding="utf-8")
    assert "*.log" in body, "用户原有规则不得丢失"
    assert "node_modules/" in body, "用户原有规则不得丢失"
    assert ".sw-context" in body, "harness 需要的规则应被追加"


# ── 验收 4：父仓库不泄漏 ──

def test_parent_repo_does_not_see_child_file_paths(tmp_path):
    """验收 4：子仓库内改文件后，父仓库 status **不出现 repo/ 下具体文件路径**。

    9.2 的假绿警告：不得断言「输出为空」—— A1 的 2.2 实测，
    缺 ignore 时输出是 `?? repo/`，断言为空会以误导方式红。
    """
    parent = _fake_parent_repo(tmp_path)
    target = parent / "repo" / "T1"
    target.mkdir(parents=True)

    git_repo.ensure_repo(str(target), "T1")
    (target / "feature.py").write_text("print('child change')\n", encoding="utf-8")

    status = _git(parent, "status", "--porcelain").stdout
    leaked = [ln for ln in status.splitlines()
              if "repo/T1/" in ln or "feature.py" in ln]
    assert not leaked, f"父仓库泄漏了子仓库内部路径: {leaked}"


# ── 验收 5：符号链接路径不误判 ──

def test_symlinked_target_dir_does_not_hard_fail(tmp_path):
    """验收 5：target_dir 位于符号链接路径下时不得误判硬失败（A1 的 2.3）。

    9.2 的假绿警告：**必须故意传入未归一的路径**。
    用已 resolve 过的路径构造，两边天生相等，此测试永远绿。
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)

    unnormalized = link / "T1"          # 经由符号链接的路径
    unnormalized.mkdir()
    assert str(unnormalized) != str(unnormalized.resolve()), (
        "前提失效：构造出的路径已经是归一形式，本测试将失去意义")

    info = git_repo.ensure_repo(str(unnormalized), "T1")
    assert info.sha, "符号链接路径下应正常建立基线"

    # 关键：传入未归一路径，verify_ownership 不得抛错
    git_repo.verify_ownership(str(unnormalized))


# ── 验收 6 / 16：失败必须抛错，不返回空串 ──

def test_run_git_raises_on_nonrepo(tmp_path):
    """验收 6：对不存在仓库抛 GitRepoError，**不返回空字符串**。"""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(GitRepoError):
        git_repo.run_git(str(plain), "rev-parse", "HEAD")


def test_run_git_raises_on_missing_dir(tmp_path):
    """目录本身不存在时同样抛错，不静默。"""
    with pytest.raises(GitRepoError):
        git_repo.run_git(str(tmp_path / "nope"), "status", "--porcelain")


def test_git_failure_is_not_translated_into_compliance(tmp_path):
    """验收 16：让 git 失败（删掉 .git），上层必须收到 GitRepoError。

    9.2 的假绿警告：不得断言「返回值为空」——
    那恰好是要禁止的行为，方向反了。
    """
    import shutil
    target = tmp_path / "repo" / "T-broken"
    target.mkdir(parents=True)
    info = git_repo.ensure_repo(str(target), "T-broken")
    (target / "a.py").write_text("x = 1\n", encoding="utf-8")

    shutil.rmtree(target / ".git")      # 仓库损坏

    with pytest.raises(GitRepoError):
        git_repo.baseline_diff(str(target), info.sha)


def test_run_git_rejects_command_string(tmp_path):
    """3.2 第 2 条：只接受 argv 序列，不接受完整命令字符串。"""
    target = tmp_path / "repo" / "T-argv"
    target.mkdir(parents=True)
    git_repo.ensure_repo(str(target), "T-argv")
    with pytest.raises(GitRepoError):
        git_repo.run_git(str(target), "status --porcelain")


# ── 验收 7：存量任务补建 ──

def test_existing_dir_without_git_is_reconstructed(tmp_path):
    """验收 7：目录有内容、无 .git → reconstructed，note 含「补建」。"""
    parent = _fake_parent_repo(tmp_path)
    target = parent / "repo" / "legacy"
    target.mkdir(parents=True)
    (target / "existing.py").write_text("# agent 早先的产出\n", encoding="utf-8")

    info = git_repo.ensure_repo(str(target), "legacy")

    assert info.kind == "reconstructed", (
        f"存量任务应为 reconstructed，实际 {info.kind!r}")
    assert "补建" in info.note, f"note 应标注补建，实际 {info.note!r}"
    assert info.sha, "补建后仍须有可用 baseline_sha"
    # 补建以当前状态为基线：既有文件不该出现在后续 diff 里
    facts = git_repo.baseline_diff(str(target), info.sha)
    assert "existing.py" not in facts.files, (
        f"补建基线应含既有内容，实际 diff: {facts.files}")


def test_repo_with_git_but_no_head_gets_baseline(tmp_path):
    """3.3 第二分支：已 init 但无 HEAD 的空仓库要补上基线提交。"""
    target = tmp_path / "repo" / "T-nohead"
    target.mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(target))
    assert _git(target, "rev-parse", "HEAD").returncode != 0, "前提：无 HEAD"

    info = git_repo.ensure_repo(str(target), "T-nohead")

    assert info.kind == "fresh", f"实际 {info.kind!r}"
    assert _git(target, "rev-parse", "--verify",
                f"{info.sha}^{{commit}}").returncode == 0


# ── 验收 8：target_dir == "." ──

def test_harness_self_does_not_init(tmp_path, monkeypatch):
    """验收 8：`target_dir == "."` 不执行 git init，kind 为 harness_self。

    9.1 第 2 条：必须构造**假的 harness 根**，不得用真实仓库。
    """
    fake_root = tmp_path / "fake-harness"
    (fake_root / "workspace").mkdir(parents=True)
    (fake_root / "repo").mkdir()
    _git(tmp_path, "init", "-q", str(fake_root))
    (fake_root / "README.md").write_text("fake harness\n", encoding="utf-8")
    _git(fake_root, "add", "-A")
    _git(fake_root, "commit", "-qm", "fake harness init")
    head_before = _git(fake_root, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(git_repo, "ROOT", fake_root)

    info = git_repo.ensure_repo(".", "T-self")

    assert info.kind == "harness_self", f"实际 {info.kind!r}"
    assert info.sha == head_before, "基线应取 harness 当前 HEAD，不新建提交"
    assert _git(fake_root, "rev-parse", "HEAD").stdout.strip() == head_before, \
        "harness 自身的 .git 不得被改写"


def test_harness_self_diff_excludes_workspace_and_repo(tmp_path, monkeypatch):
    """3.3 第四分支：harness_self 的 diff 排除 workspace/ 与 repo/。"""
    fake_root = tmp_path / "fake-harness2"
    (fake_root / "workspace" / "tasks").mkdir(parents=True)
    (fake_root / "repo").mkdir()
    _git(tmp_path, "init", "-q", str(fake_root))
    (fake_root / "README.md").write_text("fake\n", encoding="utf-8")
    (fake_root / ".gitignore").write_text("nothing-ignored\n", encoding="utf-8")
    _git(fake_root, "add", "-A")
    _git(fake_root, "commit", "-qm", "init")
    monkeypatch.setattr(git_repo, "ROOT", fake_root)

    info = git_repo.ensure_repo(".", "T-self")
    (fake_root / "sw_lib_change.py").write_text("x = 1\n", encoding="utf-8")
    (fake_root / "workspace" / "tasks" / "noise.txt").write_text("n\n", encoding="utf-8")
    (fake_root / "repo" / "noise.txt").write_text("n\n", encoding="utf-8")

    facts = git_repo.baseline_diff(".", info.sha)

    assert "sw_lib_change.py" in facts.files, f"应含 harness 自身改动: {facts.files}"
    assert not [f for f in facts.files if f.startswith(("workspace", "repo"))], \
        f"workspace/ 与 repo/ 应被排除，实际 {facts.files}"


# ── 验收 14：归属混淆可被检出 ──

def test_verify_ownership_rejects_harness_root(tmp_path, monkeypatch):
    """验收 14：target_dir 指向 harness 根目录时**必须硬失败**。

    9.2 的假绿警告：断言必须是「抛 GitRepoError」，
    而不是「返回值不等于某个东西」—— 后者返回 False 也能满足。
    """
    fake_root = tmp_path / "fake-harness3"
    fake_root.mkdir()
    _git(tmp_path, "init", "-q", str(fake_root))
    (fake_root / "README.md").write_text("fake\n", encoding="utf-8")
    _git(fake_root, "add", "-A")
    _git(fake_root, "commit", "-qm", "init")
    monkeypatch.setattr(git_repo, "ROOT", fake_root)

    with pytest.raises(GitRepoError):
        git_repo.verify_ownership(str(fake_root))


def test_verify_ownership_rejects_parent_repo_toplevel(tmp_path):
    """归属校验的一般形态：toplevel 指向别处（父仓库）时抛错。"""
    parent = _fake_parent_repo(tmp_path)
    inner = parent / "src" / "sub"
    inner.mkdir(parents=True)
    with pytest.raises(GitRepoError):
        git_repo.verify_ownership(str(inner))


# ── 验收 15：diff 归属正确（本任务核心判据）──

def test_baseline_diff_contains_only_task_repo_changes(tmp_path):
    """验收 15：harness 根与任务仓库**各留一处改动**，
    baseline_diff 只含任务仓库那处。

    9.2 的假绿警告：只断言 diff 非空不足以证明看的是对的仓库 ——
    必须断言 harness 那处改动的文件名**不在**结果里。
    这里用假 harness 根（父仓库）扮演「另一个仓库」，
    并把进程 cwd 切到它，复现 1.1 裸 git diff 的失效条件。
    """
    parent = _fake_parent_repo(tmp_path)
    target = parent / "repo" / "T1"
    target.mkdir(parents=True)
    info = git_repo.ensure_repo(str(target), "T1")

    # 父仓库（扮演 harness 自身）的改动
    (parent / "harness_side_change.md").write_text("harness edit\n", encoding="utf-8")
    # 任务仓库的改动
    (target / "task_side_change.py").write_text("task edit\n", encoding="utf-8")

    cwd_before = Path.cwd()
    try:
        os.chdir(parent)                # 裸 git diff 会在此看错仓库
        facts = git_repo.baseline_diff(str(target), info.sha)
    finally:
        os.chdir(cwd_before)

    assert "task_side_change.py" in facts.files, (
        f"任务仓库的改动必须出现，实际 {facts.files}")
    assert "harness_side_change.md" not in facts.files, (
        f"看错了仓库：结果里出现了 harness 侧的改动 {facts.files}")


def test_baseline_diff_sees_untracked_files(tmp_path):
    """A3 的 2.1：agent 通常不 commit，未跟踪文件必须出现在 diff 事实里，
    且真实索引不受影响（A3 的 2.2 定案：临时索引）。"""
    target = tmp_path / "repo" / "T-untracked"
    target.mkdir(parents=True)
    info = git_repo.ensure_repo(str(target), "T-untracked")

    (target / "staged.py").write_text("a = 1\n", encoding="utf-8")
    _git(target, "add", "staged.py")
    (target / "untracked.py").write_text("b = 2\n", encoding="utf-8")
    status_before = _git(target, "status", "--porcelain").stdout

    facts = git_repo.baseline_diff(str(target), info.sha)

    assert "untracked.py" in facts.files, f"未跟踪文件应被纳入: {facts.files}"
    assert "staged.py" in facts.files, f"已暂存文件应被纳入: {facts.files}"
    assert _git(target, "status", "--porcelain").stdout == status_before, \
        "生成 diff 不得改变 agent 的暂存状态"


def test_baseline_diff_empty_when_no_change(tmp_path):
    """无改动时返回空文件列表 —— 与「git 失败」必须可区分（3.2 第 4 条）。"""
    target = tmp_path / "repo" / "T-clean"
    target.mkdir(parents=True)
    info = git_repo.ensure_repo(str(target), "T-clean")
    facts = git_repo.baseline_diff(str(target), info.sha)
    assert facts.files == [], f"干净仓库应无改动，实际 {facts.files}"


def test_baseline_diff_raises_on_unresolvable_sha(tmp_path):
    """基线失效时抛错而非跳过（A3 契约：上游 T3 验收）。"""
    target = tmp_path / "repo" / "T-badsha"
    target.mkdir(parents=True)
    git_repo.ensure_repo(str(target), "T-badsha")
    with pytest.raises(GitRepoError):
        git_repo.baseline_diff(str(target), "0" * 40)


def test_baseline_diff_writes_no_objects_into_observed_repo(tmp_path):
    """事实包是**只读观察**，不得改变被观察对象（A3 的 2.2「零副作用」）。

    实测发现：`git add -A -N` 会把空 blob 写进**真实**对象库 ——
    `GIT_INDEX_FILE` 只隔离索引，不隔离对象库。两个后果：
    1. 每次生成事实包都往 agent 的仓库塞对象（观察改变了被观察对象）；
    2. 对象库不可写时（沙箱、只读挂载）整条链路直接 GitRepoError，
       而失败点看起来与 diff 毫无关系。

    真实复现（harness 自身仓库，沙箱内）：
        error: unable to create temporary file: Operation not permitted
        fatal: cannot create an empty blob in the object database
    """
    target = tmp_path / "repo" / "T-objects"
    target.mkdir(parents=True)
    info = git_repo.ensure_repo(str(target), "T-objects")
    (target / "untracked.py").write_text("x = 1\n", encoding="utf-8")

    objects_dir = target / ".git" / "objects"
    before = {p for p in objects_dir.rglob("*") if p.is_file()}

    facts = git_repo.baseline_diff(str(target), info.sha)
    assert "untracked.py" in facts.files, "前提：未跟踪文件仍须可见"

    after = {p for p in objects_dir.rglob("*") if p.is_file()}
    added = sorted(str(p.relative_to(objects_dir)) for p in after - before)
    assert not added, (
        f"baseline_diff 往被观察仓库的对象库写入了 {len(added)} 个对象: {added}")


def test_baseline_diff_works_with_readonly_object_store(tmp_path):
    """对象库不可写时仍能得出 diff —— 这是上一条的直接后果。

    只读 `.git/objects` 模拟沙箱/只读挂载。若实现依赖往真实对象库写空 blob，
    此处会抛 GitRepoError，而那个失败会被上层当成「拿不到事实」。
    """
    import stat

    target = tmp_path / "repo" / "T-ro"
    target.mkdir(parents=True)
    info = git_repo.ensure_repo(str(target), "T-ro")
    (target / "untracked.py").write_text("y = 2\n", encoding="utf-8")

    objects_dir = target / ".git" / "objects"
    original = stat.S_IMODE(objects_dir.stat().st_mode)
    objects_dir.chmod(0o500)        # r-x：可读可遍历，不可写
    try:
        facts = git_repo.baseline_diff(str(target), info.sha)
    finally:
        objects_dir.chmod(original)

    assert "untracked.py" in facts.files, f"只读对象库下应仍得出 diff: {facts.files}"


# ── 9.1 第 3 条：事后验证真实环境未变 ──

def test_harness_repo_untouched():
    """本测试文件跑完后，真实 harness 仓库的 HEAD 与 status 不得被改动。

    A0 实施期曾用真实路径做探针并截断了真实 .state。这条是那次事故的锚点。
    """
    head = _git(HARNESS_ROOT, "rev-parse", "HEAD")
    assert head.returncode == 0, "前提：harness 自身是 git 仓库"
    status = _git(HARNESS_ROOT, "status", "--porcelain").stdout
    # 测试不得在 harness 仓库里留下任何 T-* / repo 下的探针
    strays = [ln for ln in status.splitlines()
              if "a1probe" in ln or "fake-harness" in ln]
    assert not strays, f"测试在真实仓库留下残渣: {strays}"
