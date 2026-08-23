"""A5：04-review 的动态 fan-out / fan-in 子图。

主观审查者的**数量与模型由配置决定** —— 增删一个审查者只改 YAML，不动 Python。

实测过的 langgraph 0.6.11 行为（A5 的 11.1），实现依赖这三条：
  1. `Send` 可复用同一个节点承载 N 个分支，无需按数量预建节点。
  2. 各分支看不到兄弟分支写入的 reducer 字段 —— 产出隔离天然成立（3.3）。
  3. ⚠️ **失败不隔离**：一个分支抛异常会让整图崩，其余分支结果全部丢失。
     所以 3.4 要求的失败隔离必须由节点内部自己 try/except 兜住，
     不能指望框架。
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Set

from langgraph.graph import StateGraph, END, START
from langgraph.types import Send

from .state import WorkflowState

PREPARE_NODE = "04-review-prepare"
SUBJECTIVE_NODE = "04-review-subjective"
OBJECTIVE_NODE = "04-review-objective"
ARBITER_NODE = "04-review-arbiter"

# 分支 payload 中允许携带的父状态字段。
# 白名单而非黑名单：新增状态字段时默认**不**渗进分支，
# 避免哪天有人往 WorkflowState 加个字段就悄悄破坏 3.3 的隔离。
_BRANCH_STATE_KEYS = ("task_name", "stage_idx")


def _branch_payload(state: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    """构造分支 payload。**不含** review_findings。

    若先完成的审查者结果进入后者的 prompt，后者会被锚定 ——
    那是 C1（上下文污染）在主观轨内部的复现（A5 的 3.3）。
    """
    payload = {k: state.get(k) for k in _BRANCH_STATE_KEYS}
    payload.update(extra)
    return payload


def dispatch_reviewers(state: Dict[str, Any]) -> List[Send]:
    """按配置动态派发审查分支（A5 的 4.2）。

    客观轨在 `objective.enabled` 时派 1 个；主观轨按 `review.subjective` 的
    长度派 N 个。`subjective` 为空时回落到 `stage_roles` 的单角色 ——
    回落成空列表意味着 04 阶段无人审查，等于无条件放行。
    """
    from ..core.config import resolve_review_config, resolve_stage_roles

    cfg = resolve_review_config()
    sends: List[Send] = []

    if cfg.objective_enabled:
        sends.append(Send(OBJECTIVE_NODE, _branch_payload(state, kind="objective")))

    if cfg.subjective:
        for reviewer in cfg.subjective:
            sends.append(Send(SUBJECTIVE_NODE, _branch_payload(
                state,
                role_id=reviewer.role,
                model=reviewer.model,
                kind=reviewer.kind,
            )))
        return sends

    # 回落：单角色路径，与引入 review 配置段之前行为一致（A5 的第 7 节）。
    for role_id in resolve_stage_roles("04-review"):
        sends.append(Send(SUBJECTIVE_NODE, _branch_payload(
            state, role_id=role_id, model="", kind="")))
    return sends


def plan_batches(sends: List[Send], max_parallel: Optional[int] = None) -> List[List[Send]]:
    """把分支切成不超过 max_parallel 的批次（A5 的 3.5）。

    多个 LLM 并发会撞 API 限流，而限流导致的失败会被误记为「审查者无发现」。
    """
    if max_parallel is None:
        from ..core.config import resolve_review_config
        max_parallel = resolve_review_config().max_parallel
    size = max(1, int(max_parallel))
    return [sends[i:i + size] for i in range(0, len(sends), size)]


def build_review_graph(runner: Callable[..., Dict[str, Any]],
                       objective_runner: Callable[[str], Dict[str, Any]]):
    """构造 04-review 子图。

    `runner(task_name, role_id, model, kind) -> dict` 执行一个主观审查者；
    `objective_runner(task_name) -> dict` 执行客观轨。
    两者都由调用方注入，便于在不起 agent 的情况下测图结构。
    """
    workflow = StateGraph(WorkflowState)

    def prepare(state: WorkflowState) -> Dict[str, Any]:
        # 事实包就绪校验由 A3 负责，此处只作为 fan-out 的锚点。
        return {}

    def subjective(payload: Dict[str, Any]) -> Dict[str, Any]:
        role_id = payload.get("role_id")
        try:
            result = runner(
                task_name=payload.get("task_name"),
                role_id=role_id,
                model=payload.get("model"),
                kind=payload.get("kind"),
            ) or {}
        except BaseException as exc:  # noqa: BLE001
            # 必须在节点内部吞掉：实测抛出去会让整图崩，兄弟分支结果全丢。
            # 记 error 而**不是** verdict=no_finding —— 限流导致的失败
            # 被当成「没找到问题」，等于让故障变成放行理由（A5 的 R4）。
            return {"review_findings": [{
                "role_id": role_id,
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }]}

        finding = dict(result)
        finding["role_id"] = role_id
        finding.setdefault("kind", payload.get("kind"))
        finding.setdefault("model", payload.get("model"))
        finding["status"] = "ok"
        return {"review_findings": [finding]}

    def objective(payload: Dict[str, Any]) -> Dict[str, Any]:
        # 客观轨是纯程序流程，失败意味 bug 或环境问题 —— 硬失败，
        # 不 try/except（A5 的 3.4）。
        return {"objective_result": objective_runner(payload.get("task_name"))}

    def arbiter(state: WorkflowState) -> Dict[str, Any]:
        # 仲裁逻辑属 A9。此处只作为 fan-in 汇聚点。
        return {}

    workflow.add_node(PREPARE_NODE, prepare)
    workflow.add_node(SUBJECTIVE_NODE, subjective, input_schema=dict)
    workflow.add_node(OBJECTIVE_NODE, objective, input_schema=dict)
    workflow.add_node(ARBITER_NODE, arbiter)

    workflow.add_edge(START, PREPARE_NODE)
    workflow.add_conditional_edges(
        PREPARE_NODE, dispatch_reviewers, [SUBJECTIVE_NODE, OBJECTIVE_NODE, ARBITER_NODE])
    workflow.add_edge(SUBJECTIVE_NODE, ARBITER_NODE)
    workflow.add_edge(OBJECTIVE_NODE, ARBITER_NODE)
    workflow.add_edge(ARBITER_NODE, END)

    return workflow.compile()


def run_review(graph, state: Dict[str, Any],
               max_parallel: Optional[int] = None,
               config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """执行子图，并把 max_parallel 落到**真实执行路径**上（A5 的 3.5）。

    用 langgraph 原生的 `max_concurrency` 而不是自己分批：实测它把 4 分支的
    运行时峰值压到设定值（A5 的 11.1），而自己分批要重入图、还得手工合并
    reducer 结果，多一层容易出错的状态拼接。

    `plan_batches` 保留给需要预先知道批次形状的调用方（如进度显示）。
    """
    if max_parallel is None:
        from ..core.config import resolve_review_config
        max_parallel = resolve_review_config().max_parallel
    merged: Dict[str, Any] = dict(config or {})
    merged["max_concurrency"] = max(1, int(max_parallel))
    return graph.invoke(state, config=merged)


def clone_stage_for_role(stage_runnable, role_id: Optional[str]):
    """按角色克隆一个 StageRunnable。

    为什么必须克隆而不是复用同一实例：`StageRunnable` 持有可变的实例状态
    （`_agent_output_lines`、`_agent_text_buffer`、`active_agent` 与几个
    `threading.Event`）。N 个分支共用一个实例会互相踩输出缓冲，
    产出彼此串味且无报错。

    `role_id` 为 None 时**返回原实例**，保证单角色路径与改造前完全一致
    （验收 7）。
    """
    if role_id is None:
        return stage_runnable

    cls = type(stage_runnable)
    clone = cls.__new__(cls)
    clone.__dict__.update(stage_runnable.__dict__)

    import threading as _threading

    clone.role_id = role_id
    # 每个角色一套独立的缓冲与信号量，否则并发写同一个 list。
    clone._agent_output_lines = []
    clone._agent_text_buffer = []
    clone._agent_tool_writes = []
    clone._agent_complete = _threading.Event()
    clone._stage_done = _threading.Event()
    clone._agent_finalized = _threading.Event()
    clone._invoke_done = _threading.Event()
    clone.active_agent = None
    clone._saved_callbacks = {}
    return clone


# 仲裁器归约逻辑属 A9。A5 只负责把 fan-out / fan-in 建起来，
# 并让「仲裁尚未实现」这件事可以被程序查到。
ARBITER_IMPLEMENTED = False
ARBITER_OWNER = "A9"


def needs_arbiter() -> bool:
    """是否需要仲裁器把多份 findings 归约成一个阶段结论。

    单审查者路径自己就带 route 与 gate_passed，不需要仲裁（验收 7）。
    两个以上时必须有人归约，否则 `StageOutput.route` 为空、TUI 推不动 04 阶段
    —— 这是实测过的现象，不是推断（A5 的 11.1）。
    """
    from ..core.config import resolve_review_config
    return len(resolve_review_config().subjective) >= 2


def arbiter_status() -> Dict[str, Any]:
    """仲裁器的实现状态，供 A10 的报告与配置守护栏消费。

    `verdict` 取 `pending_arbitration` 而不是 `ok` / `no_finding`：
    把「没人下结论」表述成通过，正是本项目要消除的失效模式。
    """
    return {
        "implemented": ARBITER_IMPLEMENTED,
        "owner": ARBITER_OWNER,
        "verdict": "ok" if ARBITER_IMPLEMENTED else "pending_arbitration",
        "needs_arbiter": needs_arbiter(),
    }


# ── R2：多分支可见性 ──
#
# `create_stage_node` 里的 `adapter.active_stage = stage_runnable` 是后写覆盖
# 前写，实测并行两个审查者时 TUI 只能看到其中一个（A5 的 11.1）。
# 这不影响正确性，但会让用户以为只跑了一个。下面这份注册表提供「当前在跑的
# 全部角色」，供 TUI / Web 显示聚合状态。
_active_roles: Set[str] = set()
_active_roles_lock = threading.Lock()


def reset_active_roles() -> None:
    with _active_roles_lock:
        _active_roles.clear()


def register_active_role(role_id: Optional[str]) -> None:
    """登记一个开始执行的角色。`None`（单角色路径）不登记。"""
    if not role_id:
        return
    with _active_roles_lock:
        _active_roles.add(role_id)


def unregister_active_role(role_id: Optional[str]) -> None:
    if not role_id:
        return
    with _active_roles_lock:
        _active_roles.discard(role_id)


def active_roles() -> Set[str]:
    """当前在跑的全部审查者角色。返回副本，避免调用方迭代时被并发修改。"""
    with _active_roles_lock:
        return set(_active_roles)


def active_roles_label() -> str:
    """供显示层用的聚合标签，无并行审查者时为空串。

    单值的 `adapter.active_stage` 在并行时只反映其中一个分支，
    直接显示它会让用户以为只跑了一个审查者。排序固定，避免界面逐帧抖动。
    """
    roles = sorted(active_roles())
    if not roles:
        return ""
    return f"{len(roles)} 审查者: {', '.join(roles)}"


def ui_multi_agent_status() -> Dict[str, Any]:
    """R2 的状态声明，供 A10 的报告消费。

    `single_agent_assumption` 为 True 表示 `LangGraphAdapter.active_stage`
    仍是单值 —— 显示层看到的审查者数量可能少于实际在跑的数量。
    """
    return {
        "single_agent_assumption": True,
        "known_risk": "R2",
        "active_roles": sorted(active_roles()),
        "note": "adapter.active_stage 为单值，并行时只反映其中一个分支；"
                "需要完整列表请用 active_roles()",
    }
