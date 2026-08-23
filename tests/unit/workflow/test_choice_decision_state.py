"""选项组拍板记在 `.state`，不靠 agent 回写 Markdown。

现场 bug（任务 T1）：用户经 ask_user 选了「A. 简单交易日历表」，Gate 也签了，
`/advance` 仍报「3 个选项组尚未拍板」，且反复 /advance 无解 —— 因为判定读的是
agent 有没有把选择转写成 `[x]` / `(Chosen)` / `- **Chosen**:` 这些记法之一。
用户的决定明明已经做出，却由 agent 的书写习惯决定能不能过闸。

这与 docs/design-json-state-source.md 的结论是同一个病根：判定依据不能握在
agent 手里。Gate/Route 已经迁到 .state，选项组是漏下的最后一块。

历史补丁的思路是让校验器认识更多 Markdown 记法（见 test_chosen_marker_gate.py），
那只是扩大解析词汇表 —— agent 总能写出没被枚举到的第 N 种写法。
"""
import shutil
import inspect

import pytest

from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.utils import check_stage_compliance

STAGE = "01-brainstorming"

# 用户已通过 ask_user 拍板，但 agent 一个标记都没回写：
# 没有 [x]，没有 (Chosen) 后缀，也没有 **Chosen** 字段。
_NO_MARKER_WRITEBACK = """# 01-Brainstorming

## 方案选型

### 交易日历
- [ ] A. 简单交易日历表 - 预计算交易日历表，O(1) 查询
- [ ] B. 动态计算 - 每次查询时实时计算

### 存储引擎
- [ ] A. SQLite - 单文件，零运维
- [ ] B. Postgres - 支持并发写入

### 对外接口
- [ ] A. REST - 通用易调试
- [ ] B. gRPC - 性能更好
"""


@pytest.fixture
def task():
    name = "pytest-choice-decision"
    d = TASKS / name
    d.mkdir(parents=True, exist_ok=True)
    write_state(name, {"id": name, "stage": STAGE,
                       "stage_idx": 0, "stage_status": "running"})
    ss.sign_gate(name, STAGE)
    (d / f"{STAGE}.md").write_text(_NO_MARKER_WRITEBACK, encoding="utf-8")
    yield name
    shutil.rmtree(d, ignore_errors=True)


def test_recorded_decisions_unblock_choice_groups(task):
    """用户拍过板 → 不得再拦，即使 agent 没在 Markdown 里留任何标记。"""
    ss.record_decision(task, STAGE, question="交易日历", answer="A. 简单交易日历表")
    ss.record_decision(task, STAGE, question="存储引擎", answer="A. SQLite")
    ss.record_decision(task, STAGE, question="对外接口", answer="A. REST")

    _, todo = check_stage_compliance(task, STAGE, 0)

    assert todo == [], f"用户已拍板却仍被拦住: {todo}"


def test_no_decision_still_blocks(task):
    """没有任何拍板记录时必须拦住 —— 否则闸门等于形同虚设。"""
    _, todo = check_stage_compliance(task, STAGE, 0)

    assert any("选项组尚未拍板" in t for t in todo), todo


def test_partial_decisions_still_block(task):
    """只拍了一部分：剩下的仍要拦，且数量要对得上。"""
    ss.record_decision(task, STAGE, question="交易日历", answer="A. 简单交易日历表")

    _, todo = check_stage_compliance(task, STAGE, 0)

    assert any("2 个选项组尚未拍板" in t for t in todo), todo


def test_decisions_are_scoped_per_stage(task):
    """01 阶段的拍板不能替 02 阶段解锁。"""
    ss.record_decision(task, STAGE, question="交易日历", answer="A")
    ss.record_decision(task, STAGE, question="存储引擎", answer="A")
    ss.record_decision(task, STAGE, question="对外接口", answer="A")

    assert ss.count_decisions(task, STAGE) == 3
    assert ss.count_decisions(task, "02-planning") == 0


def test_record_decision_is_idempotent_per_question(task):
    """同一问题改主意（先 B 后 A）只算一次拍板，不能刷计数。

    现场就发生过：用户误输入 /advance 后重新选，如果每次追加一条，
    计数会超过实际选项组数，把「还差几个」的提示也一起搞乱。
    """
    ss.record_decision(task, STAGE, question="交易日历", answer="B. 动态计算")
    ss.record_decision(task, STAGE, question="交易日历", answer="A. 简单交易日历表")

    assert ss.count_decisions(task, STAGE) == 1
    assert ss.read_decisions(task, STAGE)["交易日历"]["answer"] == "A. 简单交易日历表"


def test_markdown_writeback_still_accepted(task):
    """agent 确实回写了 `[x]` 时也要认 —— 老任务/老模板不能因此卡住。"""
    (TASKS / task / f"{STAGE}.md").write_text(
        _NO_MARKER_WRITEBACK.replace("- [ ] A.", "- [x] A."), encoding="utf-8")

    _, todo = check_stage_compliance(task, STAGE, 0)

    assert todo == [], f"Markdown 勾选方式失效: {todo}"


# ── TUI 链路：用户在面板里选完，闸门就该放行 ──
#
# 上面的用例直接调 record_decision，验的是判定逻辑。但真正的 bug 出在
# 「用户答复根本没被记下来」这一环，所以必须有一个从 _dispatch 进入的用例，
# 否则 API 写好了却没人调用，测试全绿而现场照旧卡住。

class _DispatchTUI:
    """装配 _dispatch 处理结构化提问回复所需的最小状态。

    刻意复用真实的 MonitorTUI._dispatch / _record_decision，不手写替身逻辑：
    手写替身会随生产代码演进而静默失效。
    """

    def __init__(self, name, questions):
        from sw_lib.ui.tui import MonitorTUI
        self._cls = MonitorTUI
        self.state = type("S", (), {
            "name": name, "stage": STAGE, "stage_idx": 0,
            "pending_questions": list(questions), "current_q_idx": 0,
            "options": [], "input_mode": "options", "is_settled": False,
            "agent_status": "waiting",
        })()
        self._q_answers = []
        self._q_res_queue = None
        self.logs = []

    def _add_log(self, src, msg):
        self.logs.append((src, msg))

    def _refresh_display(self):
        pass

    def answer(self, label):
        """模拟用户在面板里敲一个选项标签。"""
        self._cls._dispatch(self, label)

    def __getattr__(self, item):
        """未显式装配的方法一律转发给真实 MonitorTUI。

        手写替身逐个列方法会随生产代码演进而静默失效 —— 本文件就踩过一次：
        _record_decision 拆出 _record_decision_direct 后替身缺了新方法。
        """
        from sw_lib.ui.tui import MonitorTUI
        # 用 inspect 拿到原始描述符，区分 staticmethod（不吃 self）与普通方法。
        raw = inspect.getattr_static(MonitorTUI, item, None)
        if raw is None:
            raise AttributeError(item)
        if isinstance(raw, staticmethod):
            return raw.__func__
        attr = getattr(MonitorTUI, item)
        if callable(attr):
            def _bound(*a, **k):
                return attr(self, *a, **k)
            return _bound
        raise AttributeError(item)


def test_tui_answer_records_decision_and_unblocks(task):
    """用户在 TUI 里逐个作答后，/advance 的选项组检查必须放行。"""
    questions = [
        {"question": "交易日历", "options": ["A. 简单交易日历表", "B. 动态计算"]},
        {"question": "存储引擎", "options": ["A. SQLite", "B. Postgres"]},
        {"question": "对外接口", "options": ["A. REST", "B. gRPC"]},
    ]
    tui = _DispatchTUI(task, questions)

    for q in questions:
        tui.state.options = [(o.split(".")[0], o) for o in q["options"]]
        tui.answer("A")

    assert ss.count_decisions(task, STAGE) == 3, ss.read_decisions(task, STAGE)

    _, todo = check_stage_compliance(task, STAGE, 0)
    assert todo == [], f"用户已在 TUI 里选完却仍被拦: {todo}"


def test_tui_answer_survives_state_write_failure(task):
    """记录失败不能打断用户作答 —— 这条路径在主流程上。"""
    import sw_lib.workflow.stage_state as ss_mod

    questions = [{"question": "交易日历", "options": ["A. 简单", "B. 动态"]}]
    tui = _DispatchTUI(task, questions)
    tui.state.options = [("A", "A. 简单"), ("B", "B. 动态")]

    orig = ss_mod.record_decision
    ss_mod.record_decision = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    try:
        tui.answer("A")          # 不应抛出
    finally:
        ss_mod.record_decision = orig

    # 答复本身仍要正常收下并回传给 agent。
    assert tui._q_answers == ["A. 简单"]
    assert any("拍板记录写入失败" in m for _, m in tui.logs)


def test_auto_answer_also_records_decision(task):
    """无人值守代答同样要留痕。

    否则 --unattended 会卡死在「选项组尚未拍板」上 —— 而那个场景里
    根本没有人能去拍板。
    """
    import queue
    from sw_lib.ui.tui import MonitorTUI

    questions = [
        {"question": "交易日历", "options": ["A. 简单交易日历表", "B. 动态计算"]},
        {"question": "存储引擎", "options": ["A. SQLite", "B. Postgres"]},
        {"question": "对外接口", "options": ["A. REST", "B. gRPC"]},
    ]
    tui = _DispatchTUI(task, questions)
    tui._auto_answer = True

    res = queue.Queue()
    MonitorTUI._on_ask_user(tui, questions, res)

    assert not res.empty(), "代答应立即回填答案"
    assert ss.count_decisions(task, STAGE) == 3, ss.read_decisions(task, STAGE)
    assert all(d["decided_by"] == "auto"
               for d in ss.read_decisions(task, STAGE).values())

    _, todo = check_stage_compliance(task, STAGE, 0)
    assert todo == [], f"代答后仍被拦: {todo}"


def test_web_submit_answer_records_decision(task):
    """Web 前端提交的答复也要落进同一套状态源。

    Web 与 TUI 是同一工作流的两个前端；只在 TUI 记录会让同一个 bug
    在 Web 上原样复现。
    """
    import queue
    from sw_lib.web.engine_manager import WebEngineSession

    sess = WebEngineSession.__new__(WebEngineSession)
    sess.task_name = task
    sess._pending_questions = [{"question": "交易日历"}]
    sess._pending_res_queue = queue.Queue()
    sess._events = []
    sess._lock = __import__("threading").Lock()
    sess.engine = type("E", (), {"submit_answer": lambda *a, **k: None})()

    WebEngineSession.submit_answer(sess, "A. 简单交易日历表")

    decisions = ss.read_decisions(task, STAGE)
    assert "交易日历" in decisions, decisions
    assert decisions["交易日历"]["answer"] == "A. 简单交易日历表"
