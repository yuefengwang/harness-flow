"""A1：基线信息写入 `.state` 的落点与 create_task 接线。

设计依据：docs/design/A1-task-git-repo.md 的 3.4 / 3.5（验收 3、9、17）。

⚠️ 全程隔离到 tmp_path：TASKS / TRASH / STATUS 均改指临时目录，
不碰真实 workspace（A1 的 9.1）。
"""
import subprocess

import pytest

import sw_lib.core.service as service_mod
import sw_lib.core.state as state_mod
from sw_lib.core.service import TaskService, TaskError


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks"
    trash = tasks / ".trash"
    tasks.mkdir(parents=True)
    monkeypatch.setattr(service_mod, "TASKS", tasks)
    monkeypatch.setattr(service_mod, "TRASH", trash)
    monkeypatch.setattr(state_mod, "TASKS", tasks)
    monkeypatch.setattr(state_mod, "STATUS", tmp_path / "STATUS.json")
    return tasks


# ── 验收 3 / 17：落点必须在 review 键下 ──

def test_baseline_lands_under_review_key(isolated_workspace, tmp_path):
    """验收 3 + 3.4：基线字段落在 `review` 下，**不是**顶层 `git` 键。

    落点错了签名就不覆盖，A1 的价值归零 —— 故此处显式断言顶层无 git 键。
    """
    target = tmp_path / "repo" / "T-state"
    TaskService().create_task("T-state", target_dir=str(target))

    st = state_mod.read_state("T-state")
    assert "git" not in st, (
        "基线信息不得落在顶层 git 键 —— 那不在 EVIDENCE_FIELDS 内，签名不覆盖")
    review = st.get("review", {})
    assert review.get("baseline_sha"), f"review.baseline_sha 缺失: {st.keys()}"
    assert review.get("baseline_kind") == "fresh", review
    assert review.get("toplevel"), "review.toplevel 应记录归一后的仓库根"
    assert review.get("baseline_created_at"), "应记录基线建立时间"


def test_baseline_sha_is_resolvable_after_create(isolated_workspace, tmp_path):
    """验收 2 的端到端形态：create_task 之后 sha 必须能 rev-parse。"""
    target = tmp_path / "repo" / "T-e2e"
    TaskService().create_task("T-e2e", target_dir=str(target))
    sha = state_mod.read_state("T-e2e")["review"]["baseline_sha"]
    assert _git(target, "rev-parse", "--verify",
                f"{sha}^{{commit}}").returncode == 0, f"不可解析的 sha: {sha!r}"


def test_tampering_baseline_sha_is_detected(isolated_workspace, tmp_path):
    """验收 17：直接改 `.state` 的 review.baseline_sha → verify_evidence 返回 tampered。

    本条同时验证落点正确：若误写成顶层 `git` 键，签名不覆盖，此条必红。
    """
    import json
    from sw_lib.core.evidence import verify_evidence

    target = tmp_path / "repo" / "T-tamper"
    TaskService().create_task("T-tamper", target_dir=str(target))

    sf = state_mod.state_path("T-tamper")
    assert verify_evidence(state_mod.read_state("T-tamper")).status == "valid", \
        "前提：刚写入的状态签名应有效"

    raw = json.loads(sf.read_text(encoding="utf-8"))
    raw["review"]["baseline_sha"] = "deadbeef" * 5
    sf.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_evidence(state_mod.read_state("T-tamper"))
    assert result.status == "tampered", (
        f"篡改 baseline_sha 应被检出为 tampered，实际 {result.status}: {result.detail}")


def test_create_task_preserves_concurrently_signed_gate(isolated_workspace, tmp_path):
    """3.4：必须走 update_state，禁止裸 read→改→write。

    裸路径会抹掉并发写入（A0 的 R1 实测）。这里在 marker/基线写入前后
    验证既有 review 子字段不被整体替换。
    """
    target = tmp_path / "repo" / "T-merge"
    TaskService().create_task("T-merge", target_dir=str(target))

    def _add_note(st):
        st.setdefault("review", {})["existing_note"] = "keep me"
        return st

    state_mod.update_state("T-merge", _add_note)
    from sw_lib.core.git_repo import record_baseline
    record_baseline("T-merge", str(target))

    review = state_mod.read_state("T-merge")["review"]
    assert review.get("existing_note") == "keep me", \
        f"record_baseline 覆盖了 review 下的既有字段: {review}"
    assert review.get("baseline_sha"), review


# ── 验收 9：目录创建失败必须抛错 ──

def test_create_task_raises_when_target_dir_uncreatable(isolated_workspace, tmp_path):
    """验收 9：目标目录无法创建时 create_task **抛错**（修 2.6 的静默失败）。

    构造方式：把一个**普通文件**的子路径作为 target_dir —— mkdir 必然失败。
    """
    blocker = tmp_path / "a-file"
    blocker.write_text("not a dir\n", encoding="utf-8")
    bad_target = blocker / "T-bad"

    with pytest.raises(TaskError):
        TaskService().create_task("T-bad", target_dir=str(bad_target))


def test_create_task_creates_target_dir_explicitly(isolated_workspace, tmp_path):
    """2.6：目录创建必须是显式步骤，不再是 marker 写入的副作用。"""
    target = tmp_path / "deep" / "nested" / "T-mk"
    TaskService().create_task("T-mk", target_dir=str(target))
    assert target.is_dir(), "create_task 应显式创建 target_dir"
    assert (target / ".sw-context").exists(), "marker 仍应写出"


def test_create_task_with_dot_target_does_not_init(isolated_workspace, monkeypatch, tmp_path):
    """验收 8 的接线形态：target_dir == "." 时不得对 harness 仓库 git init。

    用假 harness 根，绝不碰真实仓库（9.1 第 2 条）。
    """
    from sw_lib.core import git_repo

    fake_root = tmp_path / "fake-harness"
    fake_root.mkdir()
    subprocess.run(["git", "init", "-q", str(fake_root)], capture_output=True)
    (fake_root / "README.md").write_text("x\n", encoding="utf-8")
    _git(fake_root, "add", "-A")
    _git(fake_root, "-c", "user.name=t", "-c", "user.email=t@l",
         "commit", "-qm", "init")
    head_before = _git(fake_root, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(git_repo, "ROOT", fake_root)

    TaskService().create_task("T-dot", target_dir=".")

    st = state_mod.read_state("T-dot")
    assert st["review"]["baseline_kind"] == "harness_self", st.get("review")
    assert _git(fake_root, "rev-parse", "HEAD").stdout.strip() == head_before, \
        "harness 自身 HEAD 不得变化"


# ── 相对 target_dir 的锚点必须与 git_repo 一致（D1 纪律第 1 条：禁 cwd 依赖）──

def test_relative_target_dir_is_anchored_to_harness_root(isolated_workspace,
                                                         monkeypatch, tmp_path):
    """`target_dir` 为相对路径时（`repo/<task>` 是 config 的默认形态），
    目录创建与仓库初始化必须锚在**同一处**。

    实测发现的分歧：`_prepare_target_dir` 用 `Path.cwd()`，而
    `git_repo._resolve` 用 harness ROOT。cwd 不等于 ROOT 时（`sw` 从别处调用、
    Web 进程工作目录不同），目录建在一处、仓库初始化在另一处，
    create_task 直接抛「target_dir 不存在」。

    锚点取 ROOT：`config.yaml` 的 `repo_path: repo` 指的是 harness 根下的
    `repo/`，hook 也以 harness 根为 cwd 解析 target_dir。
    """
    from sw_lib.core import git_repo

    fake_root = tmp_path / "fake-root"
    fake_root.mkdir()
    monkeypatch.setattr(git_repo, "ROOT", fake_root)
    monkeypatch.setattr(service_mod, "ROOT", fake_root, raising=False)
    # 故意把进程 cwd 挪到别处 —— 这是本测试的关键前提
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    TaskService().create_task("T-rel", target_dir="repo/T-rel")

    assert (fake_root / "repo" / "T-rel" / ".git").exists(), (
        "相对 target_dir 应锚在 harness 根，而不是进程 cwd")
    assert not (elsewhere / "repo").exists(), (
        f"目录被建到了 cwd 下: {list(elsewhere.iterdir())}")
    review = state_mod.read_state("T-rel")["review"]
    assert review.get("baseline_sha"), review
