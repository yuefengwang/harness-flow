"""产出有两个落点，判据只量一个 —— 任务 `rrr` 的 02 门禁误判。

## 现场

`workspace/tasks/rrr/.log`：

    18:34:37 agent 🔧 write {'filePath': '.../rrr/02-planning.md', ...}
    18:35:13 agent 🔧 question {'questions': [{'question': '规划已完成，请确认...
    18:35:30 agent 请在 TUI 中输入 `/advance` 推进到 03-coding 阶段。
    18:35:39 sw    ❌ 02-planning 的产出区没有实质内容（去掉占位符与格式符后
                     仅 30 字符，至少需要 80）

而 `rrr/02-planning.md` 的模板区里躺着一份**完整**规划：7 个任务的 DAG，
每个都有 `Do` / `Verify` / `Deps`，`## Test Strategy` 齐全，`## Tech Detail`
列了 4 个数据模型与 7 个 files-to-touch。agent 干完了活，判据说它空转。

## 根因

上一轮放行了阶段文件写权限，02 的 prompt 明确要求 agent 用 `write` 回填
模板区 —— 它照做了。但 `check_output` 只量围栏区，而 agent 在 `write`
之后又调了一次 `question`，最终回复只剩一句 39 字符的收尾话，
**围栏区收到的就只有这一句**。

01 阶段侥幸没挂：那次 agent 在 `write` 之后没再调 `question`，
最终回复带着完整结论。所以这不是运气问题 ——
**判据只看一个来源，而产出可能落在两个地方。**

这与 A0 的 2.9.11 第 1 条（claims 只读围栏区、声明写在模板区）是**同一个
bug 的第二次出现**。那次修了 `extract_claims_from_stage_file`
（围栏优先、模板回落、逐字段），却没有回头检查 `check_output`
有没有同样的毛病。

## 修法的边界（为什么不能「回落整篇」）

02 模板自带 `## Task DAG` / `## Test Strategy` / `## Tech Detail` 等标题与
`- **Method**: unit / integration / manual` 这类样板文字，去噪后早已超过
阈值 80。若直接把整篇拿去量，判据**恒真** —— 那正是 A0 的 2.9.7 判过的错
（`check_02` 拿模板自带的标题当判据）。

因此回落必须**减去发货模板里已有的行**，只算 agent 真正添进去的内容。
`test_untouched_template_is_not_mistaken_for_output` 就是钉这一条的。

## 复现路径

用 MockAgent 驱动（`SW_MOCK_PLANNING_TEMPLATE_ONLY=1`）而不是手拼文件：
要复现的是「agent 把产出写去了另一处」这个行为，而不是我们对那个行为的
想象。落盘走 `StageRunnable._save_stage_output`，与生产同一条路径。
"""

import queue
import shutil

import pytest
from unittest.mock import MagicMock

from sw_lib.agents.mock import MockAgent
from sw_lib.core.config import TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.base import StageRunnable
from sw_lib.workflow.output_check import _MIN_SUBSTANCE, check_output

_STAGE = "02-planning"
_TASK = "pytest-tplfallback"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """零延时 + 屏蔽 sw_log。测试自己不该变成慢的那一环。"""
    monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
    monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                        lambda name, msg, src="sw": None)


@pytest.fixture
def task():
    """建任务：拷真实模板 + 已签署的 gate（与 helloworld 现场一致）。"""
    created = []

    def _make(name=_TASK):
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)
        write_state(name, {
            "id": name, "stage": _STAGE, "stage_idx": 1,
            "stage_status": "running", "target_dir": f"repo/{name}",
            "stages": {_STAGE: {"gate": {
                "items": [
                    {"key": "tests_pass", "label": "Tests pass",
                     "checked": True},
                    {"key": "no_regression_risk",
                     "label": "No regression risk", "checked": True},
                ],
                "signed_by": "user", "signed_at": "2026-08-24T00:00:00",
            }}},
        })
        (d / f"{_STAGE}.md").write_text(
            (TPLS / f"{_STAGE}.md").read_text(encoding="utf-8"),
            encoding="utf-8")
        return name

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def _run_mock(name, monkeypatch, template_only: bool) -> str:
    """跑一轮 mock 的 02 场景，返回它作为 agent 说出来的文本。"""
    monkeypatch.setenv("SW_MOCK_PLANNING_TEMPLATE_ONLY",
                       "1" if template_only else "0")
    logs = []

    def on_ask_user(questions, res_queue: queue.Queue):
        res_queue.put(["A. 确认推进"] * len(questions))

    agent = MockAgent(
        {
            "add_log": lambda src, msg: logs.append((src, msg)),
            "is_running": lambda: True,
            "on_ask_user": on_ask_user,
        },
        name, _STAGE, 1,
    )
    agent.running = True
    agent._run_scenario()
    return "\n".join(msg for src, msg in logs if src == "agent")


def _save(name, output):
    """走生产落盘路径把回复写进围栏区。"""
    r = StageRunnable(_STAGE, 1, MagicMock(), MagicMock(), MagicMock(),
                      MagicMock())
    r._save_stage_output(name, output)


# ── 前提自检：复现场景确实复现了 rrr 的形状 ──

def test_mock_template_only_writes_plan_into_template_region(task, monkeypatch):
    """前提：复现场景把规划写进了模板区，且回复正文只剩一句。

    若这条不成立，下面的判据测的就不是 `rrr` 的现场 —— 前提必须显式守住，
    否则一个空转的复现也能让判据看起来在工作。
    """
    name = task()
    reply = _run_mock(name, monkeypatch, template_only=True)

    body = (TASKS / name / f"{_STAGE}.md").read_text(encoding="utf-8")
    assert "___" not in body.split("## 🤖 AI Output")[0], \
        "模板区还留着占位符 —— 复现场景没有回填"
    assert "定义数据模型" in body, "模板区没有规划内容"

    # 回复正文只有收尾话：与 rrr 现场的 39 字符同量级
    assert len(reply.strip()) < 60, f"回复正文不该这么长: {reply!r}"
    assert "/advance" in reply


def test_mock_normal_mode_still_puts_plan_in_reply(task, monkeypatch):
    """默认形态不变：产出仍写在回复正文里。

    复现开关默认关闭，e2e 的主路径不受影响。
    """
    name = task()
    reply = _run_mock(name, monkeypatch, template_only=False)

    assert "Task DAG" in reply and "Tech Detail" in reply, \
        "默认形态的回复正文里没有规划 —— 开关默认值反了"


# ── 主判据：rrr 的形态必须放行 ──

def test_plan_written_only_to_template_region_passes_gate(task, monkeypatch):
    """产出全在模板区、围栏区只有收尾话 —— 必须放行。

    这就是 `rrr` 被拦下的那一刻。agent 完整交付了 7 个任务的 DAG，
    门禁报「没有实质内容（仅 30 字符）」。
    """
    name = task()
    reply = _run_mock(name, monkeypatch, template_only=True)
    _save(name, reply)

    verdict = check_output(name, _STAGE)

    assert verdict.ok, (
        "产出写在模板区就被判成空转 —— 判据只量了围栏区一个来源:\n"
        + "\n".join(verdict.lines))


def test_real_rrr_stage_file_passes_gate(task, monkeypatch):
    """拿 `rrr` 的真实文件当样本，判据必须放行。

    比 mock 生成的文本更有说服力：这是现场原物，包含 7 个任务、
    4 个数据模型、7 个 files-to-touch，而围栏区只有那句 39 字符的收尾话。

    只读 `rrr` 的 md、复制进临时任务；**不动**它的 `.state`。
    """
    src = TASKS / "rrr" / f"{_STAGE}.md"
    if not src.is_file():
        pytest.skip("rrr 现场文件已不在，跳过（不影响其余判据）")

    name = task("pytest-tplfallback-rrr")
    body = src.read_text(encoding="utf-8", errors="replace")
    # 围栏 nonce 换成本任务 .state 里的那个，否则会撞上 check_tamper
    nonce = ss.issue_output_nonce(name, _STAGE)
    body = body.replace("eab4f6fd", nonce)
    (TASKS / name / f"{_STAGE}.md").write_text(body, encoding="utf-8")

    verdict = check_output(name, _STAGE)

    assert verdict.ok, (
        "rrr 的真实产出仍被判为空转:\n" + "\n".join(verdict.lines))


# ── 反向判据：不许修成放行一切 ──

def test_untouched_template_is_not_mistaken_for_output(task):
    """模板一个字没改、围栏区也空 —— 必须仍然拦下。

    这是本次修改最容易踩坏的地方：02 模板自带的标题与
    `- **Method**: unit / integration / manual` 这类样板文字去噪后已超过
    阈值 80。若回落时直接量整篇，判据恒真 —— A0 的 2.9.7 判过这个错。

    正确做法是减去发货模板里已有的行，只算 agent 添进去的部分。
    """
    name = task("pytest-tplfallback-bare")
    _save(name, "好的。")          # 围栏区只有两个字

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, (
        "未回填的模板被当成了产出 —— 判据恒真:\n" + "\n".join(verdict.lines))


def test_template_filled_with_placeholders_still_blocked(task):
    """模板区被「回填」成一堆占位符，等于没填。

    模型有时会把模板原样抄一遍再交回来。抄回来的 `___` 不是内容 ——
    这条与 `test_early_stage_output_check.py` 里那条同源，只是落点换到模板区。
    """
    name = task("pytest-tplfallback-ph")
    path = TASKS / name / f"{_STAGE}.md"
    body = path.read_text(encoding="utf-8")
    # 在模板区多加几行，但全是占位符
    body = body.replace("## Tech Detail",
                        "## Tech Detail\n- **TODO**: ___\n- **FIXME**: ___\n"
                        "- **TBD**: ___\n")
    path.write_text(body, encoding="utf-8")
    _save(name, "好的。")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, (
        "模板区只有占位符却过闸了:\n" + "\n".join(verdict.lines))


def test_no_output_region_at_all_still_blocked(task):
    """连围栏区都没有（阶段从未跑过）—— 必须拦。

    「本阶段没产出」与「产出写在别处」是两回事。回落不该把前者也一起放过：
    模板区在 `sw init` 时就存在，若它能单独让门禁通过，
    一个从未运行过的阶段也能过闸。
    """
    name = task("pytest-tplfallback-noregion")
    path = TASKS / name / f"{_STAGE}.md"
    # 模板区回填成真内容，但**不**落任何围栏
    body = path.read_text(encoding="utf-8")
    body = body.replace("## Task DAG", MockAgent._planning_body())
    path.write_text(body, encoding="utf-8")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, (
        "没有围栏区（阶段从未跑过）却过闸了:\n" + "\n".join(verdict.lines))


# ── 顺序判据：围栏区优先，与 claims 抽取同序 ──

def test_fenced_region_is_preferred_when_both_have_content(task):
    """两处都有内容时以围栏区为准（与 `extract_claims_from_stage_file` 同序）。

    围栏区由 sw 落盘、nonce 不可预测，可信度高于 agent 可任意改写的模板区。
    判据报出的字符数应当来自围栏区 —— 若取的是模板区，
    agent 在模板里写一份好看的、在产出里写另一份，我们会读到前者。
    """
    name = task("pytest-tplfallback-both")
    path = TASKS / name / f"{_STAGE}.md"
    body = path.read_text(encoding="utf-8")
    body = body.replace("## Task DAG", MockAgent._planning_body())
    path.write_text(body, encoding="utf-8")

    fenced = "围栏区自己就有足够长的实质结论。" * 8
    _save(name, fenced)

    verdict = check_output(name, _STAGE)
    assert verdict.ok, "\n".join(verdict.lines)

    from sw_lib.workflow.output_check import _strip_noise
    expected = len(_strip_noise(fenced))
    joined = "\n".join(verdict.lines)
    assert f"{expected} 字符" in joined, (
        f"报出的字符数不是围栏区的 {expected} —— 优先级取反了:\n{joined}")


# ── 阈值不得放宽 ──

def test_threshold_is_not_lowered():
    """阈值必须仍是 80。

    修「产出在别处」不该顺手降阈值 —— 那是「放宽标准让存量变绿」
    （A6 的 9.3）。本轮修的是**看哪里**，不是**看多严**。
    """
    assert _MIN_SUBSTANCE == 80, \
        f"阈值被改成 {_MIN_SUBSTANCE} —— 判据被放宽了，而不是修对了落点"
