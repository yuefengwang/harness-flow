"""A5 的验收 1-7：04-review 的动态 fan-out / fan-in。

并发与图结构改动**很容易假绿** —— 测试可能根本没触发并行路径（A5 的第 8 节）。
所以本文件的断言全部数数量、验隔离、验异常路径，不接受「不报错」式判据。

撰写前用探针实测了 langgraph 0.6.11 的三条行为（见 A5 的 11.1）：
  1. `Send` 可复用同一个节点承载 N 个分支（无需按数量预建节点）。
  2. 各分支看不到兄弟分支写入的 reducer 字段 —— 隔离天然成立。
  3. ⚠️ **失败不隔离**：一个分支抛异常整图崩，其余分支结果全丢。
     所以 3.4 的失败隔离必须由节点内部自己兜住。
"""

import copy

import pytest

from sw_lib.core import config as C
from sw_lib.workflow import review_graph as RG


@pytest.fixture(autouse=True)
def _restore_global_config():
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _role(agent="opencode", model="opencode/m", tools=None):
    return C.RoleConfig(agent=agent, model=model, description="",
                        tools=list(tools or ["list_files", "read_file"]))


def _configure(n_subjective: int, objective: bool = True):
    """按数量配置主观审查者。名字与模型都不同，便于验证「各自不同」。"""
    names = ["adversary", "design_critic", "third_eye", "fourth_eye"][:n_subjective]
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role(), "developer": _role()}
    for i, name in enumerate(names):
        cfg.roles[name] = _role(model=f"opencode/model-{i}")
    cfg.stage_roles = {"03-coding": "developer", "04-review": "reviewer"}
    cfg.review = C.ReviewConfig(
        objective_enabled=objective,
        require_heterogeneous=False,
        subjective=[
            C.SubjectiveReviewer(role=n, model=f"opencode/model-{i}",
                                 kind="counterexample" if i % 2 == 0 else "design_review")
            for i, n in enumerate(names)
        ],
    )
    return names


# ── 验收 1 / 2：分支数由配置决定，增删不改 Python ──

def test_two_reviewers_produce_two_branches():
    _configure(2)
    sends = RG.dispatch_reviewers({"task_name": "t", "stage_idx": 3})

    subjective = [s for s in sends if s.node == RG.SUBJECTIVE_NODE]
    assert len(subjective) == 2
    assert [s.arg["role_id"] for s in subjective] == ["adversary", "design_critic"]


def test_three_reviewers_without_code_change():
    _configure(3)
    sends = RG.dispatch_reviewers({"task_name": "t", "stage_idx": 3})

    assert len([s for s in sends if s.node == RG.SUBJECTIVE_NODE]) == 3


def test_each_branch_carries_its_own_model():
    """验收 1 的后半：模型各自不同。只数分支数不足以证明异构。"""
    _configure(3)
    sends = RG.dispatch_reviewers({"task_name": "t", "stage_idx": 3})

    models = [s.arg["model"] for s in sends if s.node == RG.SUBJECTIVE_NODE]
    assert models == ["opencode/model-0", "opencode/model-1", "opencode/model-2"]
    assert len(set(models)) == 3


def test_objective_branch_dispatched_when_enabled():
    _configure(2, objective=True)
    sends = RG.dispatch_reviewers({"task_name": "t", "stage_idx": 3})

    assert sum(1 for s in sends if s.node == RG.OBJECTIVE_NODE) == 1


# ── 验收 3：零主观审查者时仅客观轨执行，不报错 ──

def test_zero_subjective_falls_back_to_single_reviewer():
    """subjective 为空时回落到 stage_roles 的单角色，而非「零个审查者」。

    断言列表内容而非「长度 <= 1」—— 空列表意味着 04 阶段无人审查。
    """
    C._manager.config.review = C.ReviewConfig(subjective=[])
    C._manager.config.stage_roles = {"04-review": "reviewer"}
    C._manager.config.roles = {"reviewer": _role()}

    sends = RG.dispatch_reviewers({"task_name": "t", "stage_idx": 3})

    roles = [s.arg["role_id"] for s in sends if s.node == RG.SUBJECTIVE_NODE]
    assert roles == ["reviewer"]


def test_objective_only_when_no_subjective_and_objective_on():
    cfg = C._manager.config
    cfg.roles = {}
    cfg.stage_roles = {}
    cfg.review = C.ReviewConfig(objective_enabled=True, subjective=[])

    sends = RG.dispatch_reviewers({"task_name": "t", "stage_idx": 3})

    assert [s.node for s in sends] == [RG.OBJECTIVE_NODE]


# ── 验收 4：分支隔离 ──

def test_branch_payload_excludes_sibling_findings():
    """派发给分支的 payload 不得含 review_findings。

    若先完成的审查者结果进入后者的 prompt，后者会被锚定 ——
    那是 C1 在主观轨内部的复现（A5 的 3.3）。
    """
    _configure(2)
    state = {"task_name": "t", "stage_idx": 3,
             "review_findings": [{"role": "adversary", "verdict": "有反例"}]}

    sends = RG.dispatch_reviewers(state)

    # 守卫：sends 为空时下面的循环体一次都不执行，测试会空转通过。
    # 隔离类断言必须先证明「确实有分支被检查过」（DEV-PROTOCOL 的 1.1）。
    assert len(sends) == 3, f"应派发 2 主观 + 1 客观，实际 {len(sends)}"
    for s in sends:
        assert "review_findings" not in s.arg, \
            f"分支 payload 携带了兄弟分支产出：{s.arg}"
        assert "有反例" not in str(s.arg)


# ── 验收 5：失败隔离（实测默认行为是整图崩）──

def test_one_failing_branch_does_not_kill_others():
    _configure(3)

    def runner(task_name, role_id, model, kind):
        if role_id == "design_critic":
            raise RuntimeError("模型不可用")
        return {"role_id": role_id, "verdict": "no_finding"}

    graph = RG.build_review_graph(runner=runner, objective_runner=lambda t: {"ok": True})
    out = graph.invoke({"task_name": "t", "stage_idx": 3,
                        "review_findings": [], "objective_result": None})

    by_role = {f["role_id"]: f for f in out["review_findings"]}
    assert set(by_role) == {"adversary", "design_critic", "third_eye"}
    assert by_role["design_critic"]["status"] == "error"
    assert "模型不可用" in by_role["design_critic"]["error"]
    assert by_role["adversary"]["status"] == "ok"
    assert by_role["third_eye"]["status"] == "ok"


def test_failure_is_not_recorded_as_no_finding():
    """R4：失败必须记 error，不得记为「审查者无发现」。

    限流导致的失败被当成「没找到问题」，等于让故障变成放行理由。
    """
    _configure(1)

    def runner(task_name, role_id, model, kind):
        raise TimeoutError("rate limited")

    graph = RG.build_review_graph(runner=runner, objective_runner=lambda t: {"ok": True})
    out = graph.invoke({"task_name": "t", "stage_idx": 3,
                        "review_findings": [], "objective_result": None})

    finding = out["review_findings"][0]
    assert finding["status"] == "error"
    assert finding.get("verdict") != "no_finding"


def test_objective_failure_is_hard(monkeypatch):
    """客观轨是纯程序流程，失败意味 bug 或环境问题 —— 硬失败（A5 的 3.4）。"""
    _configure(1)

    def boom(task_name):
        raise RuntimeError("客观轨自身出错")

    graph = RG.build_review_graph(runner=lambda **kw: {"verdict": "ok"},
                                  objective_runner=boom)
    with pytest.raises(Exception):
        graph.invoke({"task_name": "t", "stage_idx": 3,
                      "review_findings": [], "objective_result": None})


# ── 验收 6：reducer 正确性 ──

def test_findings_count_equals_branch_count():
    """条目数等于分支数。单分支测试无法暴露「被覆盖只剩 1 条」。"""
    _configure(4)

    graph = RG.build_review_graph(
        runner=lambda task_name, role_id, model, kind: {"role_id": role_id},
        objective_runner=lambda t: {"ok": True})
    out = graph.invoke({"task_name": "t", "stage_idx": 3,
                        "review_findings": [], "objective_result": None})

    assert len(out["review_findings"]) == 4
    assert out["objective_result"] == {"ok": True}


# ── 3.5：并发上限 ──

def test_max_parallel_batches_branches():
    """审查者超过上限时分批 —— 多个 LLM 并发会撞 API 限流，
    而限流失败会被误记为「无发现」（A5 的 3.5）。"""
    _configure(4)
    C._manager.config.review.max_parallel = 2

    batches = RG.plan_batches(RG.dispatch_reviewers({"task_name": "t", "stage_idx": 3}),
                              max_parallel=2)

    assert [len(b) for b in batches] == [2, 2, 1]  # 4 主观 + 1 客观
