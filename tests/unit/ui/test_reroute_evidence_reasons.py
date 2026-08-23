"""返工上下文必须带真实理由（任务 T3）。

现场：用户在 04-review 里明确指出「repo/T3 下没有 README.md」，选择返工到
03-coding。注入到 03-coding.md 的返工上下文却是：

    | 1 | 需返工修复的问题 | high | coding | 详见审查结论 |

这是 _fill_reroute_evidence 写死的占位文本，一个字的有效信息都没有。agent
读到「详见审查结论」无从下手，连续三轮返工都只是把原有代码重新确认一遍，
README.md 始终没补上。

理由的唯一可靠来源是 `.state` 里的拍板记录（用户在 ask_user 里选的原文）。
"""
import shutil

import pytest

from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import write_state
from sw_lib.ui.tui import MonitorTUI, TUIState
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.utils import extract_evidence_table, inject_reroute_context

_TASK = "pytest-reroute-evidence"

_REVIEW_MD = """# 04-Review

## Review Decision
- **Route**: `___` (05-Archive / 03-Coding)
- **Reason**: ___

### Reroute Evidence (仅在返工时填写)
| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |
|---|------|---------|---------|-------------|
| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |
| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |

## Gate
- [ ] Full build: `___`
"""


@pytest.fixture
def task_dir():
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {"id": _TASK, "stage": "04-review",
                        "stage_idx": STAGES.index("04-review"),
                        "stage_status": "running"})
    (d / "04-review.md").write_text(_REVIEW_MD, encoding="utf-8")
    (d / "03-coding.md").write_text("# 03-Coding\n\n原有内容\n", encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _tui():
    tui = MonitorTUI.__new__(MonitorTUI)
    tui.state = TUIState(name=_TASK, stage="04-review",
                         stage_idx=STAGES.index("04-review"))
    tui._add_log = lambda src, msg: None
    return tui


def test_evidence_carries_user_answer(task_dir):
    """用户答复的原文必须出现在 Evidence 表里。"""
    ss.record_decision(_TASK, "04-review",
                       question="发现 repo/T3 下缺少 README.md，如何处理？",
                       answer="B. 返工到 03-Coding 补 README.md")

    _tui()._fill_reroute_evidence("03-Coding")

    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    assert "README.md" in content, "返工理由没落进 Evidence 表"
    assert "详见审查结论" not in content, "仍在写死的占位文本"
    assert "需返工修复的问题" not in content


def test_evidence_table_stays_extractable(task_dir):
    """填完的表必须能被 extract_evidence_table 提出来并注入下游阶段。

    只留一条理由时不能保留第二行占位符 —— 那行里的 `___` 会让整表被判为未填，
    extract_evidence_table 返回 None，返工上下文根本注入不进去。
    """
    ss.record_decision(_TASK, "04-review",
                       question="缺 README.md 怎么办？",
                       answer="返工补文档")

    _tui()._fill_reroute_evidence("03-Coding")

    table = extract_evidence_table(_TASK)
    assert table is not None, "Evidence 表未通过提取（残留 ___ 占位行）"
    assert "README.md" in table

    inject_reroute_context(_TASK, "03-coding")
    coding = (task_dir / "03-coding.md").read_text(encoding="utf-8")
    assert "返工上下文" in coding
    assert "README.md" in coding, "agent 读到的 03-coding.md 里没有真实理由"


def test_two_reasons_fill_both_rows(task_dir):
    ss.record_decision(_TASK, "04-review", question="问题一", answer="答复一")
    ss.record_decision(_TASK, "04-review", question="问题二", answer="答复二")

    _tui()._fill_reroute_evidence("03-Coding")

    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    assert "答复一" in content and "答复二" in content
    rows = [ln for ln in content.splitlines()
            if ln.startswith("| 1 |") or ln.startswith("| 2 |")]
    assert len(rows) == 2
    assert not any("___" in ln for ln in rows), f"占位符未被替换: {rows}"


def test_fallback_when_no_decisions(task_dir):
    """没有拍板记录时给一句明确的兜底，而不是「详见审查结论」。"""
    _tui()._fill_reroute_evidence("03-Coding")

    table = extract_evidence_table(_TASK)
    assert table is not None
    assert "04-review 对话记录" in table
    assert "详见审查结论" not in table


def test_pipe_and_newline_are_neutralized(task_dir):
    """答复里的竖线/换行会把单行表格结构撑散，必须转义压平。"""
    ss.record_decision(_TASK, "04-review", question="要改哪些文件？",
                       answer="README.md | docs/design.md\n还有 hooks/")

    _tui()._fill_reroute_evidence("03-Coding")

    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    row = [ln for ln in content.splitlines() if ln.startswith("| 1 |")][0]
    # 表头 5 列 -> 6 个分隔竖线；多出来的都必须是转义过的 \|
    assert row.count("|") - row.count("\\|") == 6, f"表格列数被撑散: {row}"
    assert "docs/design.md" in row


def test_long_answer_is_truncated(task_dir):
    ss.record_decision(_TASK, "04-review", question="Q", answer="x" * 400)

    _tui()._fill_reroute_evidence("03-Coding")

    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    row = [ln for ln in content.splitlines() if ln.startswith("| 1 |")][0]
    assert "..." in row
    assert len(row) < 260, "超长答复未截断，表格不可读"


def test_archive_route_leaves_evidence_untouched(task_dir):
    """归档不是返工，Evidence 表应保持原样（占位符不动）。"""
    ss.write_route(_TASK, "05-Archive", by="user")
    tui = _tui()
    tui._write_review_route("05-Archive")

    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    assert "| 1 | ___ |" in content
