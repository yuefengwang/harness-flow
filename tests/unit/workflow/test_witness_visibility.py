"""降级之后：绕过必须**可见**，否则机制自我消解。

A2 的红绿见证在 Python 项目上已降级为事后处理（见
test_red_witness_post_hoc.py）：agent 用 `bash` 先写实现，harness 不再拒绝，
只如实记 `unavailable` + `bypassed` + `bypass_files`。

这个降级只有在「记录被人看到」时才成立。若 `.state` 里记了而 TUI 不显示、
reviewer 读不到、报告不提，那么结果与「机制不存在」完全相同 ——
**而且更坏**：`.state` 里有一份「我们检查过」的痕迹，让人以为有人在管。

这正是本项目反复踩的同一个坑（A0 的 2.5、A5 的 R2、上一轮的 `is_idle()`）：
**判据存在、无人调用**。`Toolbox` 的白名单一行未生效、`active_roles()`
交付了却没接进渲染、`is_idle()` 除自身测试外零调用点。每一次都是
「做完了但没接上」，因此本文件只测**接线**，不测字段本身。

三个落点，各自的失效后果不同：

1. **TUI 头部** —— 用户在跑任务时唯一持续看到的地方。不显示等于操作者
   全程不知道这一轮的红绿是真见证还是被绕过。
2. **04 事实包** —— reviewer 的判据来源。读不到就会把「测试全绿」当成
   「实现被验证过」，而实际上一次断言都没有先失败过。
3. **05 报告** —— 任务的最终留痕。缺了它，归档产物会把 ❓ 静默升级成 ✅。
"""

import json
import shutil

import pytest

from sw_lib.core.config import TASKS, is_mock_agent
from sw_lib.workflow import red_witness as rw


@pytest.fixture
def bypassed_task(tmp_path):
    """一个已被记录为「绕过」的任务。

    直接写 `.state` 再走受控入口打标：本文件测的是**显示层**，
    不需要真的跑一遍 pytest（那部分由 post_hoc 覆盖）。
    """
    created = []

    def _make(name="rw-vis-bypassed", files=2, mark=True):
        target = tmp_path / name
        target.mkdir(parents=True, exist_ok=True)
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / ".state").write_text(json.dumps({
            "id": name, "stage": "03-coding", "stage_idx": 2,
            "stage_status": "running", "target_dir": str(target),
        }), encoding="utf-8")
        created.append(d)
        if mark:
            rw.record_bypass(name, [f"src/mod{i}.py" for i in range(files)])
        return name

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


# ── 摘要函数本身（下游三处共用一个来源）──

def test_summary_reports_bypass(bypassed_task):
    """有一个**共用**的摘要函数，三处显示都从它取值。

    各处自己拼文案会立刻分叉：改了 TUI 忘了改报告，两边说法不一致，
    而用户无从判断哪个是真的（A5 的 R2 就是这么裂开的）。
    """
    name = bypassed_task()

    summary = rw.witness_summary(name)

    assert summary["status"] == "unavailable", summary
    assert summary["bypassed"] is True, summary
    assert summary["bypass_count"] == 1, summary


def test_summary_label_is_questionable_not_pass(bypassed_task):
    """摘要的标签必须是 ❓ —— 三态里的「不确定」，不得显示成 ✅。"""
    name = bypassed_task()

    label = rw.witness_summary(name)["label"]

    assert "❓" in label, f"绕过被显示成非 ❓ 的形态: {label!r}"
    assert "✅" not in label, f"绕过被显示成通过: {label!r}"


def test_summary_names_files_in_detail(bypassed_task):
    """摘要要能给出「绕过了什么」，而不只是「绕过了」。"""
    name = bypassed_task()

    detail = rw.witness_summary(name)["detail"]

    assert "src/mod0.py" in detail, detail


def test_summary_on_real_witness_is_pass(bypassed_task):
    """对照面：真见证过并转绿的任务，摘要是 ✅。

    否则「一律显示 ❓」也能让上面几条通过 —— 那等于摘要不含信息。
    """
    name = bypassed_task(name="rw-vis-green", mark=False)
    rw.begin_test_phase(name)
    rw.record_red(name, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t::a"]),
                  {"t.py": "abc"})
    rw.record_green(name, rw.WitnessVerdict("ok", "", 0, passed_nodes=["t::a"]))

    summary = rw.witness_summary(name)

    assert summary["status"] == "ok", summary
    assert summary["bypassed"] is False, summary
    assert "✅" in summary["label"], summary


def test_summary_absent_when_no_record(bypassed_task):
    """没有任何见证记录时不编造 —— 三态的第三态要能表达「无记录」。"""
    name = bypassed_task(name="rw-vis-empty", mark=False)

    summary = rw.witness_summary(name)

    assert summary["status"] == "absent", summary
    assert "✅" not in summary["label"], summary


# ── 接线 1：TUI 头部 ──

def test_tui_header_shows_bypass(bypassed_task):
    """TUI 头部必须显示绕过标记。

    断言**渲染结果的文本**而不是源码里有没有那个函数名：
    「引用了但没渲染出来」是本项目反复出现的形态（test_multi_reviewer_display
    的源码断言就漏得过去）。
    """
    from rich.console import Console

    from sw_lib.ui.tui import TUIState, MonitorTUI

    name = bypassed_task(name="rw-vis-tui")
    ui = MonitorTUI.__new__(MonitorTUI)
    ui.state = TUIState(name=name, stage="03-coding", stage_idx=2)
    ui._auto_advance = False
    ui._auto_stopped = False

    console = Console(width=200, no_color=True)
    with console.capture() as cap:
        console.print(ui._render_header())
    text = cap.get()

    assert "❓" in text, f"TUI 头部没有显示见证被绕过:\n{text}"


def test_tui_header_clean_when_witnessed(bypassed_task):
    """真见证过的任务不该在头部出现 ❓ —— 否则标记失去区分度。"""
    from rich.console import Console

    from sw_lib.ui.tui import TUIState, MonitorTUI

    name = bypassed_task(name="rw-vis-tui-green", mark=False)
    rw.begin_test_phase(name)
    rw.record_red(name, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t::a"]),
                  {"t.py": "abc"})
    rw.record_green(name, rw.WitnessVerdict("ok", "", 0, passed_nodes=["t::a"]))

    ui = MonitorTUI.__new__(MonitorTUI)
    ui.state = TUIState(name=name, stage="03-coding", stage_idx=2)
    ui._auto_advance = False
    ui._auto_stopped = False

    console = Console(width=200, no_color=True)
    with console.capture() as cap:
        console.print(ui._render_header())

    assert "❓" not in cap.get(), cap.get()


# ── 接线 2：04 事实包（reviewer 的判据）──

def test_fact_pack_warns_about_bypass(bypassed_task):
    """事实包的 warnings 必须包含绕过 —— reviewer 要读到它。

    没有它，reviewer 看到 `tests.json` 全绿就会判「实现已验证」，
    而实际上没有任何断言先失败过：那份绿证明不了任何事。
    """
    name = bypassed_task(name="rw-vis-facts")

    from sw_lib.workflow.fact_pack import witness_warnings

    warnings = witness_warnings(name)

    assert warnings, "事实包对绕过一言不发"
    joined = "\n".join(warnings)
    assert "src/mod0.py" in joined, joined
    assert "unavailable" in joined or "❓" in joined, joined


def test_fact_pack_silent_when_witnessed(bypassed_task):
    """真见证过时不该报警 —— 否则 warnings 变成噪音，没人再看。"""
    name = bypassed_task(name="rw-vis-facts-green", mark=False)
    rw.begin_test_phase(name)
    rw.record_red(name, rw.WitnessVerdict("ok", "", 1, failed_nodes=["t::a"]),
                  {"t.py": "abc"})
    rw.record_green(name, rw.WitnessVerdict("ok", "", 0, passed_nodes=["t::a"]))

    from sw_lib.workflow.fact_pack import witness_warnings

    assert witness_warnings(name) == []


def test_reviewer_prompt_carries_the_bypass(bypassed_task):
    """接线的终点：绕过必须真的出现在 **reviewer 的 prompt** 里。

    事实包生成了 warnings 却没进 prompt，是这套设计已经存在的缺口
    （`manifest.json` 不在 `_FACT_ORDER` 里，warnings 从未被注入过）。
    reviewer 读不到的事实，等于没采集。
    """
    name = bypassed_task(name="rw-vis-prompt")
    facts = TASKS / name / "facts"
    facts.mkdir(parents=True, exist_ok=True)
    (facts / "tests.json").write_text('{"passed": 19}', encoding="utf-8")
    # 注入源刻意是 manifest 而非实时重算：reviewer 看到的限定必须与它手上的
    # 事实同源（生成之后有人动 `.state` 也不该改变这一份的说法）。
    from sw_lib.workflow.fact_pack import witness_warnings

    (facts / "manifest.json").write_text(json.dumps({
        "task": name, "warnings": witness_warnings(name), "files": {},
    }, ensure_ascii=False), encoding="utf-8")

    from sw_lib.prompts.builder import PromptBuilder

    text = PromptBuilder(None)._read_fact_pack(name)

    assert text and "src/mod0.py" in text, \
        f"reviewer 的 prompt 里没有绕过信息:\n{text}"


# ── 接线 3：05 归档报告 ──

def test_final_report_records_the_bypass(bypassed_task):
    """归档报告必须留痕 —— 任务的最终产物不能把 ❓ 说成 ✅。"""
    name = bypassed_task(name="rw-vis-report")

    from sw_lib.workflow.red_witness import witness_report_line

    line = witness_report_line(name)

    assert "❓" in line, line
    assert "src/mod0.py" in line, line
