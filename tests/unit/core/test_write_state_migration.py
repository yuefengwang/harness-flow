"""A15：`.state` 受控入口的完成 —— 让丢失更新不可表达。

对应 docs/design/A15-write-state-migration.md 的第 5 节红绿判据表。

形状（一句话，不含文件名）：
    状态写入走「读取快照 → 修改 → 整体写回」，两个并发写者中
    后写者静默覆盖前写者。

纪律（A0 的 9.2）：**顺序调用永远不会丢失**，那样测不出竞争。
因此这里用注入式脚手架，在被测函数「读」与「写」之间插入一个真实的
第二写者。适用于 read→write 窗口由调用方持有的情形，确定性复现、
无 sleep、不看运气。

断言落在**判据字段**（Gate 签名）而不只是显示字段：A0 的结论是
「判据丢失比状态显示错误严重得多」。
"""

import inspect
import json
import shutil
import warnings

import pytest

from sw_lib.core import state as state_mod
from sw_lib.core.state import read_state, write_state   # 基线对照用，见文件末
from sw_lib.core.config import STAGES
from sw_lib.workflow import stage_state as ss

_ARCHIVE_IDX = STAGES.index("05-archive")


@pytest.fixture
def racing_reader(monkeypatch):
    """在受害者**首次接触状态**之后，让第二个写者真实改一次 `.state`。

    模拟生产上真实存在的交错：
    受害者取得快照 / 准备写入 → **另一方写盘** → 受害者落盘。

    为什么注入而不是 sleep + 线程：交错窗口由调用方持有时注入是确定性的；
    sleep 只是把概率调高，仍会在慢机器上偶发假绿（A0 的 9.2 点名这条）。

    ⚠️ 为什么**同时**钩 `read_state` 与 `update_state`：
    第一版只钩了 `read_state`。迁移之后 `add_answer` / `remove_task` 不再调用
    它，注入点静默失效 —— 第二个写者从未运行，测试却在断言「签名还在」，
    于是变成一条**空转的判据**（这正是本仓库形状 S7 的近亲：判据在，但没接上）。
    钩住两者之后，无论受害者走哪条路都会被插入一次真实的并发写：

    * 旧实现走 `read_state` → 注入发生在**读之后**，受害者随即用旧快照整体
      写回 → 判据丢失，测试红；
    * 新实现走 `update_state` → 注入发生在**取锁之前**（因此不会死锁：
      A15 的 3.2.1 实测 `state_lock` 不可重入），受害者的 mutator 在锁内
      重新读盘，看得见这次写入 → 判据保住，测试绿。
    """
    def _install(module, task, mutation):
        fired = {"done": False}

        def _second_writer():
            if fired["done"]:
                return
            fired["done"] = True
            # 第二个写者走受控入口：代表 TUI 签 Gate / HealthMonitor 写盘。
            # 直接用 state_mod 的属性，避免打到自己的 patch 上造成递归。
            state_mod.update_state(task, mutation)

        real_read = state_mod.read_state
        real_update = state_mod.update_state

        def fake_read_state(name, *a, **kw):
            st = real_read(name, *a, **kw)
            if name == task:
                _second_writer()
            return st

        def fake_update_state(name, mutator, *a, **kw):
            if name == task:
                _second_writer()          # 取锁之前，否则会撞上不可重入的锁
            return real_update(name, mutator, *a, **kw)

        if hasattr(module, "read_state"):
            monkeypatch.setattr(module, "read_state", fake_read_state)
        if hasattr(module, "update_state"):
            monkeypatch.setattr(module, "update_state", fake_update_state)
        return fired

    return _install


def _sign_gate_mutation(stage):
    """第二个写者干的事：签署某阶段的 Gate（判据字段）。"""
    def _m(state):
        bucket = state.setdefault("stages", {}).setdefault(stage, {})
        bucket["gate"] = {
            "items": [{"key": "k1", "label": "l1", "checked": True}],
            "signed_by": "user",
            "signed_at": "2026-01-01 00:00:00",
        }
        return state
    return _m


# ── U15-1：advance 的 4 处写入 ──

def test_advance_does_not_clobber_concurrent_gate_signature(make_task, racing_reader):
    """推进过程中用户签的 Gate 不得被推进的旧快照抹掉。

    红的形态：`advance` 在入口 `read_state` 拿到快照，随后用户签署 01 阶段的
    Gate，`advance` 末尾把快照整体写回 —— 签名凭空消失。
    """
    from sw_lib.workflow import runtime as rt

    name = make_task("pytest-a15-advance-gate", stage="02-planning", stage_idx=1)
    fired = racing_reader(rt, name, _sign_gate_mutation("01-brainstorming"))

    rt.WorkflowRuntime.advance(name)

    assert fired["done"], "第二个写者从未运行 —— 判据空转（见 racing_reader 的告警）"

    assert ss.read_gate(name, "01-brainstorming").signed is True, (
        "advance 用入口读的旧快照整体写回，抹掉了并发签署的 Gate 签名")


def test_advance_terminal_branch_does_not_clobber(make_task, racing_reader):
    """终态分支同样不得覆盖并发写入。

    这条单独存在的理由：终态分支在 `advance` 里**提前 return**，不经过主推进
    路径那句「重读一次再改字段」。只修主路径会留下这个兄弟实例（形状 S10）。
    """
    from sw_lib.workflow import runtime as rt

    name = make_task("pytest-a15-advance-terminal", stage="05-archive",
                     stage_idx=_ARCHIVE_IDX)
    fired = racing_reader(rt, name, _sign_gate_mutation("04-review"))

    st = rt.WorkflowRuntime.advance(name)

    assert fired["done"], "第二个写者从未运行 —— 判据空转"

    assert st.get("stage_status") == "Finished", "终态分支本身的行为不得改变"
    assert ss.read_gate(name, "04-review").signed is True, (
        "终态分支用旧快照写回，抹掉了并发签署的 Gate 签名")


def test_advance_still_progresses_single_threaded(make_task):
    """阳性对照：并发保护不得把正常推进锁死。

    若实现改成「干脆不写状态」，上面几条也会绿 —— 这条负责证伪那种实现。
    """
    from sw_lib.workflow.runtime import WorkflowRuntime

    name = make_task("pytest-a15-advance-positive", stage="01-brainstorming",
                     stage_idx=0)
    st = WorkflowRuntime.advance(name)

    assert st["stage_idx"] == 1, f"推进没有发生：stage_idx={st.get('stage_idx')}"
    assert st["stage"] == STAGES[1]
    assert st["stage_status"] == "pending"
    assert state_mod.read_state(name)["stage_idx"] == 1, "推进没有落盘"


# ── U15-2：service 的 5 处写入 ──

def test_add_answer_does_not_clobber_concurrent_write(make_task, racing_reader):
    """`add_answer` 与 agent 写状态高频并发，不得整体覆盖。"""
    from sw_lib.core import service as svc_mod

    name = make_task("pytest-a15-add-answer", stage="03-coding", stage_idx=2,
                     stage_status="pending")
    fired = racing_reader(svc_mod, name, _sign_gate_mutation("02-planning"))

    svc_mod.TaskService().add_answer(name, "用户的回复")

    assert fired["done"], "第二个写者从未运行 —— 判据空转"

    final = state_mod.read_state(name)
    assert final.get("stage_status") == "running", "add_answer 自身的写入丢了"
    assert ss.read_gate(name, "02-planning").signed is True, (
        "add_answer 用旧快照写回，抹掉了并发写入的 Gate 签名")


def test_add_answer_is_idempotent_when_not_pending(make_task):
    """U15-6：非 pending 时不得刷 `updated_at`（`NO_CHANGE` 生效）。

    幂等路径每次都写盘会刷新时间戳并重算签名，在审计里凭空制造状态变更。
    """
    from sw_lib.core import service as svc_mod

    name = make_task("pytest-a15-idempotent", stage="03-coding", stage_idx=2,
                     stage_status="running", updated_at="2020-01-01 00:00:00")

    svc_mod.TaskService().add_answer(name, "回复")

    assert state_mod.read_state(name).get("updated_at") == "2020-01-01 00:00:00", (
        "非 pending 状态下 add_answer 仍然写盘刷新了 updated_at")


def test_remove_task_does_not_clobber_concurrent_write(make_task, racing_reader):
    """`remove_task` 打移除标记时不得覆盖并发写入。"""
    from sw_lib.core import service as svc_mod

    name = make_task("pytest-a15-remove", stage="03-coding", stage_idx=2)
    fired = racing_reader(svc_mod, name, _sign_gate_mutation("02-planning"))

    try:
        svc_mod.TaskService().remove_task(name)
        assert fired["done"], "第二个写者从未运行 —— 判据空转"
        # 任务已被移到回收站，从回收站里读回它的 .state
        final = json.loads(
            (svc_mod.TRASH / name / ".state").read_text(encoding="utf-8"))
        assert final.get("removed_at"), "remove_task 自身的写入丢了"
        gate = final.get("stages", {}).get("02-planning", {}).get("gate", {})
        assert gate.get("signed_by") == "user", (
            "remove_task 用旧快照写回，抹掉了并发写入的 Gate 签名")
    finally:
        shutil.rmtree(svc_mod.TRASH / name, ignore_errors=True)


def test_complete_deploy_does_not_clobber_concurrent_write(make_task, racing_reader):
    """`complete_deploy` 与 HealthMonitor 并发，不得整体覆盖。"""
    from sw_lib.core import service as svc_mod

    name = make_task("pytest-a15-complete-deploy", stage="05-archive",
                     stage_idx=_ARCHIVE_IDX, stage_status="Finished",
                     deploy_status="deploying")

    def health_writes(state):
        state["health_status"] = "circuit_broken"
        return state

    fired = racing_reader(svc_mod, name, health_writes)

    svc_mod.TaskService().complete_deploy(name, success=False)

    assert fired["done"], "第二个写者从未运行 —— 判据空转"

    final = state_mod.read_state(name)
    assert final.get("deploy_status") == "deploy_failed", "complete_deploy 自身的写入丢了"
    assert final.get("health_status") == "circuit_broken", (
        "complete_deploy 用旧快照写回，抹掉了 HealthMonitor 的并发写入")


# ── U15-3：_write_state_safe 的名字必须兑现 ──

def test_write_state_safe_goes_through_controlled_entry():
    """名字承诺 safe，实现不得是裸 `write_state`。

    这比直接调 `write_state` 更坏：它让调用方以为问题已经处理过了。
    HealthMonitor 的 3 处写入都经它落盘，且跑在独立线程里。
    """
    from sw_lib.core import service as svc_mod

    src = inspect.getsource(svc_mod.TaskService._write_state_safe)
    assert "update_state" in src, (
        "_write_state_safe 仍是裸 write_state —— 名字承诺了安全，实现没兑现:\n" + src)


def test_write_state_safe_does_not_clobber_concurrent_write(make_task):
    """HealthMonitor 拿着过期快照写盘，不得抹掉之后签署的 Gate。

    这里不需要注入：HealthMonitor 的快照来自 `get_task_state`，
    与写盘之间隔着一次 HTTP 探测，过期是常态而非例外。
    """
    from sw_lib.core import service as svc_mod

    name = make_task("pytest-a15-health-write", stage="05-archive",
                     stage_idx=_ARCHIVE_IDX, deploy_status="deployed")

    stale = state_mod.read_state(name)          # HealthMonitor 手里的快照
    ss.sign_gate(name, "04-review")             # 之后用户签了 Gate

    stale["health_status"] = "health_failed"
    svc_mod.TaskService()._write_state_safe(name, stale)

    assert state_mod.read_state(name).get("health_status") == "health_failed", \
        "HealthMonitor 自己的写入丢了"
    assert ss.read_gate(name, "04-review").signed is True, (
        "_write_state_safe 用过期快照整体写回，抹掉了用户签署的 Gate")


def test_write_state_safe_preserves_gate_when_snapshot_has_stale_stages(make_task):
    """快照里**已经带着一棵旧的 `stages` 子树**时，仍不得抹掉新的签名。

    这条是真实路径验证抓出来的（A15 的 2.9.20，本仓库第 7 次「单元绿 ≠ 机制通」）：
    上一条用例里 HealthMonitor 的快照在 `stages` 出现**之前**读取，因此浅合并
    看起来够用；而真实任务早在 01/02 阶段就有了 `stages` 子树，快照带着旧版本
    写回去，整棵判据树被替换 —— 签名从 True 变 False。

    实测（真实任务 a15probe，无 mock）：
        签署后 05 gate: True → _write_state_safe 写盘后: False

    因此这里的前提必须是「快照里已有旧 stages」，那才是生产上的常态。
    """
    from sw_lib.core import service as svc_mod

    name = make_task("pytest-a15-stale-stages", stage="05-archive",
                     stage_idx=_ARCHIVE_IDX, deploy_status="deployed")
    ss.seed_gate(name, "04-review")             # 早期阶段就已存在的 stages 子树

    stale = state_mod.read_state(name)          # 快照里带着「未签署」的旧子树
    assert "stages" in stale, "前提没建立起来：快照里必须已有 stages"

    ss.sign_gate(name, "04-review")             # 之后用户签了 Gate

    stale["health_status"] = "health_failed"
    svc_mod.TaskService()._write_state_safe(name, stale)

    assert state_mod.read_state(name).get("health_status") == "health_failed", \
        "HealthMonitor 自己的写入丢了"
    assert ss.read_gate(name, "04-review").signed is True, (
        "快照里的旧 stages 子树把新签名整棵替换掉了")


# ── U15-4/U15-5：让第 11 处裸写不可表达 ──

def test_bare_write_state_warns_non_internal_callers(make_task):
    """非内部调用 `write_state` 必须发 `DeprecationWarning`，指向 `update_state`。

    这是本任务唯一新增的机制：受控入口建成之后仍不断长出新的裸写调用点
    （形状 S10 第 6 个实例），说明光靠文档与评审拦不住。
    """
    name = make_task("pytest-a15-warn")
    st = state_mod.read_state(name)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        state_mod.write_state(name, st)

    msgs = [str(w.message) for w in caught
            if issubclass(w.category, DeprecationWarning)]
    assert msgs, "裸 write_state 没有发 DeprecationWarning"
    assert any("update_state" in m for m in msgs), (
        f"警告没有指向替代入口 update_state: {msgs}")


def test_internal_write_state_does_not_warn(make_task):
    """反向：受控入口内部的写入不得发警告。

    若连 `update_state` 自己的落盘都告警，警告立刻变噪音，
    下一个人会整体忽略它 —— 那等于机制没建。
    """
    name = make_task("pytest-a15-nowarn")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        state_mod.update_state(name, lambda st: {**st, "stage_status": "running"})

    msgs = [str(w.message) for w in caught
            if issubclass(w.category, DeprecationWarning)
            and "update_state" in str(w.message)]
    assert not msgs, f"update_state 内部的写入也发了警告，会把信号淹掉: {msgs}"


def test_no_bare_write_state_left_in_production_paths():
    """U15-5：迁移后 `sw_lib/` 里的裸写只剩已裁定的合法处。

    wiring check：判据建成而调用点没接上，正是形状 S7/S10。
    白名单里的每一处都必须在 A15 的 2.2 节有区分特征，不得随手加。
    """
    import subprocess
    from sw_lib.core.config import ROOT

    out = subprocess.run(
        ["rg", "-n", r"write_state\(", "sw_lib/", "hooks/", "bin/"],
        cwd=str(ROOT), capture_output=True, text=True).stdout

    offenders = []
    for line in out.splitlines():
        path, _, rest = line.partition(":")
        body = rest.partition(":")[2].strip()
        if body.startswith(("from", "import", "#", "*", '"')):
            continue
        if "update_state" in body or "def write_state" in body:
            continue
        if "_write_state_safe" in body:          # 包装层自身，下面单独有判据
            continue
        # 合法处 1：state.py 内部（update_state 的落盘）
        if path.endswith("core/state.py"):
            continue
        # 合法处 2：显式声明的内部调用（A15 的 2.2 已裁定并附区分特征）
        if "_internal=True" in body:
            continue
        offenders.append(line)

    assert not offenders, (
        "仍有未受控的裸 write_state 调用点（形状 S10「修了一半」）:\n  "
        + "\n  ".join(offenders))
