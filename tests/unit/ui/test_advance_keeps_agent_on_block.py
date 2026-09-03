"""校验未通过时不得关掉 agent。

现场 bug（任务 iiiii）：第一次 /advance 先把 agent 收尾退出，再跑校验；校验失败
后提示「请完善后重试」，可此时 agent 已经死了，没人能改那个文件 —— 用户被卡在
死循环里，只能手工编辑生成的设计书。
"""
import queue
import shutil
from unittest.mock import MagicMock, patch

import pytest

from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import write_state
from sw_lib.ui.tui import MonitorTUI, TUIState
from sw_lib.workflow import stage_state as ss


_TASK = "pytest-advance-block"


@pytest.fixture(autouse=True)
def _task_dir():
    """硬校验读的是 `.state`，所以这里要真的签署，而不是往文件里写 `[x]`。

    阶段文件仍然写出来（hook 会检查它存在），但里面的勾选不再有任何效力。

    ⚠️ 夹具补了 **AI 产出区**：`check_01-brainstorming.sh` 现在要求产出区
    存在且有实质内容（`sw_lib/workflow/output_check.py`）。此前只写模板正文
    就能过闸 —— 那正是任务 helloworld 空转过闸的形状。本文件关心的是
    「校验未通过时别关掉 agent」，所以要让**硬校验本身通过**，
    否则测的就不再是原来那件事了。

    ⚠️ 同理，产出区的**歧义分数必须达标**：`hook-01-01` 已接线为真判据
    （`output_check.read_ambiguity_score`，阈值 8）。夹具原先写的是 6 分，
    硬校验会正确地拦下它 —— 于是本文件测的就变成了「校验失败路径」，
    而 `test_passing_validation_still_finalizes_agent` 需要的是通过路径。
    """
    d = TASKS / _TASK
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {"id": _TASK, "stage": "01-brainstorming",
                        "stage_idx": 0, "stage_status": "running"})
    # A13 第 1 步的完成性判据要求 01 至少一轮问答。本文件测的是
    # 「校验通过后 agent 应被收尾」—— 完成性只是前置条件。
    ss.record_decision(_TASK, "01-brainstorming", "需求范围？", "已确认")
    nonce = ss.issue_output_nonce(_TASK, "01-brainstorming")
    (d / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n"
        "## Design Decision (ADR)\n"
        "- **Proposal**: done\n\n"
        + ss.render_output_block(nonce,
            "## 需求澄清结果\n\n"
            "- 功能范围：最小可用的登录与会话管理\n"
            "- 技术栈：Python + FastAPI，SQLite 存储\n"
            "- 歧义分数：9/10，关键点已全部澄清\n\n"
            "## 设计决策\n\n"
            "- 方案：先做单体实现，接口留出替换点\n"
            "- 理由：规模小，拆分收益不足以抵消复杂度\n")
        + "\n## Gate\n"
        "- [ ] Design approved\n"
        "- [ ] Ready for Planning\n",
        encoding="utf-8")
    ss.sign_gate(_TASK, "01-brainstorming")
    yield
    shutil.rmtree(d, ignore_errors=True)


class _FakeStageDone:
    def __init__(self):
        self.set_called = False

    def set(self):
        self.set_called = True

    def is_set(self):
        return self.set_called


class _FakeActiveStage:
    def __init__(self):
        self.active_agent = MagicMock()
        self._stage_done = _FakeStageDone()
        self._agent_finalized = MagicMock()
        self._invoke_done = MagicMock()


def _tui(stage="01-brainstorming"):
    tui = MonitorTUI.__new__(MonitorTUI)
    # 用真实的 TUIState 而不是 MagicMock：mock 上未设置的属性是真值
    # (bool(m.pending_questions) is True)，会让 _dispatch 的前置分支恒真、
    # 后面的路由/签署分支永远不可达 —— 测试会"通过"却什么都没验证。
    tui.state = TUIState(name=_TASK, stage=stage,
                         stage_idx=STAGES.index(stage))
    tui.cmd_queue = queue.Queue()
    tui._auto_answer = False
    tui.callbacks = {}
    tui._add_log = lambda *a: None
    tui._refresh_display = lambda: None
    return tui


def test_failed_validation_keeps_agent_alive():
    """软校验有待办 → 必须在关 agent 之前就返回 blocked。"""
    tui = _tui()
    active = _FakeActiveStage()
    executor = MagicMock(active_stage=active)

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor",
               return_value=executor), \
         patch("sw_lib.core.service._service") as svc:
        svc.get_task_state.return_value = {"stage_idx": 0, "stage_status": "running"}
        svc.validate_stage.return_value = ([], ["01-brainstorming.md — 3 个待填项未完成"])
        result = tui._run_advance(auto=False)

    assert result == "blocked"
    assert not active._stage_done.set_called, \
        "校验失败却已通知 agent 收尾，用户再也无法让 agent 修文件"
    active.active_agent.shutdown.assert_not_called()


def test_passing_validation_still_finalizes_agent():
    """校验通过时仍要正常收尾 agent，否则阶段推进会与旧 agent 抢状态。"""
    tui = _tui()
    active = _FakeActiveStage()
    executor = MagicMock(active_stage=active)

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor",
               return_value=executor), \
         patch("sw_lib.core.service._service") as svc, \
         patch("sw_lib.ui.tui.read_state",
               return_value={"stage": "02-planning", "stage_idx": 1,
                             "stage_status": "pending"}), \
         patch("sw_lib.ui.tui.threading.Thread") as thread:
        svc.get_task_state.return_value = {"stage_idx": 0, "stage_status": "running"}
        svc.validate_stage.return_value = (["全部 5 项已勾选"], [])
        tui._run_advance(auto=False)

    assert active._stage_done.set_called, "校验通过后应通知 agent 收尾"
    thread.assert_called_once()
