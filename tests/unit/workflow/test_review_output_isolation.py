"""A5 的 R1：并行审查者的产出不得互相覆盖。

探针实测（A5 的 11.1）：两个 StageRunnable 并发调 `_save_stage_output` 写同一个
`04-review.md`，落盘后**只剩后写的那一份** —— 前一个审查者的发现完全消失，
且没有任何报错。审查结果静默丢失比审查没跑更危险：报告会显示「已审查」。

所以多审查者场景下每个角色必须有自己的产出文件。
"""

import copy
import threading

import pytest

from sw_lib.core import config as C
from sw_lib.workflow.base import StageRunnable


@pytest.fixture(autouse=True)
def _restore_global_config():
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _configure_multi_reviewers(*names):
    """建立「真的配了多个主观审查者」的前提。

    分文件只在多审查者时发生（单角色回落必须留在 04-review.md），
    所以隔离类测试必须自己把这个前提摆出来，否则测的是回落路径。
    """
    cfg = C._manager.config
    cfg.roles = {n: C.RoleConfig(agent="opencode", model="m", description="",
                                 tools=["read_file"]) for n in names}
    cfg.review = C.ReviewConfig(
        objective_enabled=False,
        subjective=[C.SubjectiveReviewer(role=n, model=f"m{i}", kind="k")
                    for i, n in enumerate(names)])


def _make_runner(role_id=None):
    from unittest.mock import MagicMock
    return StageRunnable(
        stage="04-review", stage_idx=3,
        context_builder=MagicMock(), output_parser=MagicMock(),
        gate_validator=MagicMock(), agent_factory=MagicMock(),
        role_id=role_id,
    )


def test_role_scoped_output_filename_differs_per_role():
    """不同角色必须落到不同文件。相同则并发写必然覆盖。"""
    _configure_multi_reviewers("adversary", "design_critic")
    a = _make_runner("adversary")
    b = _make_runner("design_critic")

    assert a.output_filename != b.output_filename, \
        f"两个角色共用同一产出文件 {a.output_filename}，并发写会互相覆盖"
    assert "adversary" in a.output_filename
    assert "design_critic" in b.output_filename


def test_no_role_keeps_legacy_filename():
    """向后兼容（验收 7）：无 role_id 时文件名必须与改造前一致。

    否则既有的 gate 校验、归档、事实包都会读不到 04-review.md。
    """
    assert _make_runner().output_filename == "04-review.md"
    assert _make_runner(None).output_filename == "04-review.md"


def test_parallel_role_writes_all_survive(tmp_path, monkeypatch):
    """两个角色并发落盘后，两份产出都必须能读到。

    这条在改造前是红的：探针实测只剩 1 份。
    """
    import sw_lib.core.state as S
    import sw_lib.workflow.base as B
    import sw_lib.workflow.stage_state as SS

    _configure_multi_reviewers("adversary", "design_critic")
    task = "rw-a5-isolation"
    for mod in (C, S, SS, B):
        monkeypatch.setattr(mod, "TASKS", tmp_path, raising=False)
    (tmp_path / task).mkdir(parents=True)
    S.write_state(task, {"task": task, "stage": "04-review", "stage_idx": 3})

    runners = [_make_runner("adversary"), _make_runner("design_critic")]
    threads = [
        threading.Thread(target=r._save_stage_output,
                         args=(task, f"{r.role_id} 的独家发现-MARK{i}"))
        for i, r in enumerate(runners)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    blob = "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / task).glob("04-review*.md"))
    missing = [m for m in ("MARK0", "MARK1") if m not in blob]
    assert not missing, f"这些审查者的产出被覆盖丢失了：{missing}"


def test_role_output_nonce_is_per_role(tmp_path, monkeypatch):
    """nonce 也必须按角色隔离，否则两角色抢同一个 nonce 记录。"""
    import sw_lib.core.state as S
    import sw_lib.workflow.stage_state as SS
    import sw_lib.workflow.base as B

    _configure_multi_reviewers("adversary", "design_critic")
    task = "rw-a5-nonce"
    for mod in (C, S, SS, B):
        monkeypatch.setattr(mod, "TASKS", tmp_path, raising=False)
    (tmp_path / task).mkdir(parents=True)
    S.write_state(task, {"task": task, "stage": "04-review", "stage_idx": 3})

    a = _make_runner("adversary")
    b = _make_runner("design_critic")
    a._save_stage_output(task, "A 的发现")
    b._save_stage_output(task, "B 的发现")

    st = S.read_state(task)
    n_a = SS.issue_output_nonce(task, a.output_scope)
    n_b = SS.issue_output_nonce(task, b.output_scope)
    assert n_a != n_b, f"两角色共用 nonce {n_a}，产出区边界会互相踩"


def test_sole_reviewer_keeps_legacy_filename(monkeypatch):
    """e2e 抓到的回归：单角色回落时 role_id 非空（`reviewer`）。

    若据此把文件改名成 `04-review.reviewer.md`，门禁校验与事实包都读不到产出 ——
    e2e 现场表现为「stage file has no AI Output」，而单元测试全绿。

    判据：只有 `review.subjective` 真的配了多个审查者时才分文件；
    单审查者回落必须留在 `04-review.md`。
    """
    import copy
    from sw_lib.core import config as C

    snapshot = copy.deepcopy(C._manager.config)
    try:
        C._manager.config.review = C.ReviewConfig(subjective=[])
        C._manager.config.stage_roles = {"04-review": "reviewer"}
        assert _make_runner("reviewer").output_filename == "04-review.md"
    finally:
        C._manager._config = snapshot
