"""A3 的验收 15 / 16 / 16b / 16c / 20：diff 事实的采集正确性与零副作用。

**全部用真实 git 仓库，不 mock `run_git`**（A3 的 9.1 第 2 条）：
2.1 那个头号缺陷（agent 未 `git add` 的文件不进 diff）只有在真实仓库里
才会暴露 —— 任何 mock 都会按实现者的预期返回，而实现者的预期恰好就是
错的那个。
"""

import json
import os
import shutil
import stat

import pytest

from sw_lib.core import git_repo as G
from sw_lib.core.state import read_state, write_state
from sw_lib.workflow import fact_pack as FP


@pytest.fixture
def task_repo(tmp_path, monkeypatch):
    """在 tmp_path 内建：任务目录（含 .state）+ 独立 git 仓库 + 基线提交。

    A3 的 9.2：绝不碰真实 `workspace/tasks/*/`。TASKS 被多个模块在导入期
    各自绑定，所以逐个 monkeypatch —— 漏一个就会写到真实 workspace。
    """
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    monkeypatch.setattr("sw_lib.core.config.TASKS", tasks_root, raising=False)
    monkeypatch.setattr("sw_lib.core.state.TASKS", tasks_root, raising=False)
    monkeypatch.setattr(FP, "TASKS", tasks_root, raising=False)

    task = "a3probe"
    (tasks_root / task).mkdir()
    target = tmp_path / "repo" / task
    target.mkdir(parents=True)

    (target / "kept.py").write_text("x = 1\n", encoding="utf-8")
    info = G.ensure_repo(str(target), task)

    write_state(task, {
        "id": task,
        "stage": "03-coding",
        "stage_idx": 2,
        "stage_status": "running",
        "target_dir": str(target),
        "review": {"baseline_sha": info.sha, "baseline_kind": info.kind},
    })
    return task, target, info


def _numstat(task, target):
    pack = FP.generate(task)
    return (target.parent.parent / "tasks" / task / "facts" / "diff.numstat"), pack


def _facts_dir(task, tmp_root):
    return tmp_root / "tasks" / task / "facts"


# ── 验收 15：未 git add 的产出必须出现在 diff 里（头号判据）──

def test_untracked_file_appears_in_numstat(task_repo, tmp_path):
    task, target, _ = task_repo
    (target / "untracked.py").write_text("y = 2\n", encoding="utf-8")

    # ⚠️ 此处**刻意不执行 git add** —— agent 通常不自己 add（A3 的 2.1）。
    # 测试若代替 agent add 了，裸 `git diff` 的错误实现同样能通过，
    # 头号缺陷被永久隐藏（A3 的 9.4）。
    porcelain = G.run_git(str(target), "status", "--porcelain")
    assert "?? untracked.py" in porcelain, \
        f"前提不成立：该文件应处于未跟踪状态，实际 status 为 {porcelain!r}"

    FP.generate(task)
    numstat = (_facts_dir(task, tmp_path) / "diff.numstat").read_text(encoding="utf-8")

    assert "untracked.py" in numstat


# ── 验收 16：不改变 agent 的暂存状态 ──

def test_generate_preserves_staged_state(task_repo, tmp_path):
    task, target, _ = task_repo
    (target / "staged.py").write_text("z = 3\n", encoding="utf-8")
    G.run_git(str(target), "add", "staged.py")
    before = G.run_git(str(target), "status", "--porcelain")
    assert "A  staged.py" in before

    FP.generate(task)

    after = G.run_git(str(target), "status", "--porcelain")
    assert "A  staged.py" in after, \
        f"暂存状态被破坏：{before!r} -> {after!r}（`git add -A -N` + reset 的典型症状）"


# ── 验收 16b：不往被观察仓库的对象库写入 ──

def _count_objects(target):
    obj_dir = G._git_object_dir(target.resolve())
    return sum(1 for p in obj_dir.rglob("*") if p.is_file())


def test_generate_writes_no_objects_into_observed_repo(task_repo, tmp_path):
    task, target, _ = task_repo
    (target / "untracked.py").write_text("y = 2\n", encoding="utf-8")
    before = _count_objects(target)

    FP.generate(task)

    after = _count_objects(target)
    # 判据是「前后相等」，不是某个具体数字（A3 的 2.2.1）——
    # 绝对值随仓库起点而变。
    assert after == before, \
        f"对象库被写入：{before} -> {after}。观察改变了被观察对象"


# ── 验收 16c：对象库不可写时仍能生成 diff ──

def test_generate_works_with_readonly_object_store(task_repo, tmp_path):
    task, target, _ = task_repo
    (target / "untracked.py").write_text("y = 2\n", encoding="utf-8")
    obj_dir = G._git_object_dir(target.resolve())

    original = stat.S_IMODE(obj_dir.stat().st_mode)
    os.chmod(obj_dir, 0o500)
    try:
        FP.generate(task)
        numstat = (_facts_dir(task, tmp_path) / "diff.numstat").read_text(encoding="utf-8")
    finally:
        os.chmod(obj_dir, original)

    # 实现若依赖往真实对象库写空 blob，这里会拿到
    # `cannot create an empty blob in the object database` —— 而那个失败
    # 极易被上层当成「无改动」降级处理（A3 的 2.2.1 第 2 个后果）。
    assert "untracked.py" in numstat


# ── 验收 20：归属错误时不生成 harness 自身的 diff ──

def test_ownership_violation_raises_and_leaves_no_patch(tmp_path, monkeypatch):
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    monkeypatch.setattr("sw_lib.core.config.TASKS", tasks_root, raising=False)
    monkeypatch.setattr("sw_lib.core.state.TASKS", tasks_root, raising=False)
    monkeypatch.setattr(FP, "TASKS", tasks_root, raising=False)

    task = "a3owned"
    (tasks_root / task).mkdir()
    # target_dir 指向 harness 根的子目录：其仓库根是 harness 自身。
    monkeypatch.setattr(FP, "_HARNESS_SELF_ALLOWED", False, raising=False)
    write_state(task, {
        "id": task, "stage": "03-coding", "stage_idx": 2,
        "stage_status": "running",
        "target_dir": str(G.ROOT / "sw_lib"),
        "review": {"baseline_sha": "HEAD", "baseline_kind": "fresh"},
    })

    with pytest.raises(FP.FactPackError):
        FP.generate(task)

    # 只断言抛错不够：先生成后校验的实现会在异常抛出时已把文件落盘，
    # 半成品会被下游当成完整的读（A3 的 9.3）。
    assert not (tasks_root / task / "facts" / "diff.patch").exists()


# ── 验收 12：缺 baseline_sha 时硬失败，不生成半个事实包 ──

def test_missing_baseline_raises_and_creates_nothing(tmp_path, monkeypatch):
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    monkeypatch.setattr("sw_lib.core.config.TASKS", tasks_root, raising=False)
    monkeypatch.setattr("sw_lib.core.state.TASKS", tasks_root, raising=False)
    monkeypatch.setattr(FP, "TASKS", tasks_root, raising=False)

    task = "a3nobase"
    (tasks_root / task).mkdir()
    target = tmp_path / "repo" / task
    target.mkdir(parents=True)
    G.ensure_repo(str(target), task)
    write_state(task, {
        "id": task, "stage": "03-coding", "stage_idx": 2,
        "stage_status": "running", "target_dir": str(target),
    })  # 刻意不写 review.baseline_sha

    with pytest.raises(FP.FactPackError):
        FP.generate(task)

    facts = tasks_root / task / "facts"
    leftovers = sorted(p.name for p in facts.iterdir()) if facts.exists() else []
    assert leftovers == [], f"抛错前已落盘半个事实包：{leftovers}"
