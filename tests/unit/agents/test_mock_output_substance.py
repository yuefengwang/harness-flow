"""MockAgent 每个阶段的产出必须过得了该阶段自己的门禁。

背景：本轮给 01/02 补上了「产出区必须有实质内容」的硬校验
（`sw_lib/workflow/output_check.py`）。补完之后 e2e 立刻挂在 02-planning：

    ❌ 02-planning 的产出区没有实质内容（去掉占位符与格式符后仅 74 字符，
       至少需要 80）

MockAgent 是 e2e 的**唯一驱动**。它的 02 产出只有三行 WBS 标题，
去噪后 74 字符 —— 比真实 agent 的产出薄一个量级（helloworld 那次真实的
01 产出去噪后 204 字符以上）。

这里有两条路，只有一条是对的：

* ❌ 把阈值从 80 降到 70 —— 那是「放宽标准让存量变绿」，A6 的 9.3 明令禁止。
  阈值 80 是实测校准出来的（真实产出 200+ / 空转 <30），为了迁就 mock 去动它，
  等于让 e2e 反过来定义什么算「有产出」。
* ✅ 把 mock 的产出补到真实形态 —— 它薄得不真实，本来就是 mock 的缺陷。
  一个连自家门禁都过不了的 mock，测不出任何有价值的东西。

判据刻意**复用 `check_output` 本身**而不是自己数字符：
mock 与门禁必须共用同一把尺子，否则尺子改了 mock 不会跟着红。
同时要求 20% 余量 —— 恰好压线的 mock 会在阈值任何微调下变红，
那种红是假信号（e2e 挂了，但被测的东西并没坏）。
"""

import json
import queue
import shutil

import pytest

from sw_lib.agents.mock import MockAgent
from sw_lib.core.config import STAGES, TASKS
from sw_lib.workflow import stage_state
from sw_lib.workflow.output_check import _MIN_SUBSTANCE, check_output

#: 阈值之上必须留的余量。压线不算通过 —— 见模块 docstring。
_HEADROOM = 1.2


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """零延时 + 屏蔽 sw_log，测试自己不该变成慢的那一环。"""
    monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
    monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                        lambda name, msg, src="sw": None)


def _run_stage(stage: str, stage_idx: int, task: str) -> str:
    """跑完一个 mock 场景，返回它作为 agent 说出来的全部文本。

    拼接方式与 `StageRunnable._collect_agent_output` 的回退分支一致：
    取 source == "agent" 的行用换行连接。
    """
    logs = []

    def on_ask_user(questions, res_queue: queue.Queue):
        res_queue.put(["A. 第一个选项"] * len(questions))

    agent = MockAgent(
        {
            "add_log": lambda src, msg: logs.append((src, msg)),
            "is_running": lambda: True,
            "on_ask_user": on_ask_user,
        },
        task, stage, stage_idx,
    )
    agent.running = True
    agent._run_scenario()
    return "\n".join(msg for src, msg in logs if src == "agent")


@pytest.fixture
def staged():
    """把一段产出按真实链路写进阶段文件，返回任务名。

    走 `issue_output_nonce` + `render_output_block` 而不是手拼围栏：
    产出区的边界规则只有一处定义，判据不该复制它。
    """
    created = []

    def _make(stage: str, output: str) -> str:
        name = f"pytest-mocksub-{stage[:2]}"
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / ".state").write_text(json.dumps({
            "id": name, "stage": stage, "stage_idx": 0,
            "target_dir": f"repo/{name}",
        }), encoding="utf-8")
        created.append(d)

        nonce = stage_state.issue_output_nonce(name, stage)
        (d / f"{stage}.md").write_text(
            stage_state.render_output_block(nonce, output), encoding="utf-8")
        return name

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


# ── 前提自检 ──

def test_all_five_stages_have_a_scenario():
    """五个阶段都必须有 mock 场景可跑。

    若某阶段落到 `_scenario_generic`，下面的判据仍会跑，但测的是那个通用
    兜底而不是该阶段的产出形态 —— 显式守住前提，避免判据看起来在测五个
    阶段、实际只测了一个。
    """
    missing = [s for s in STAGES
               if not hasattr(MockAgent, f"_scenario_{s[3:].replace('-', '_')}")]
    assert not missing, f"这些阶段没有专属 mock 场景: {missing}"


# ── 主判据 ──

@pytest.mark.parametrize("stage_idx,stage", list(enumerate(STAGES)))
def test_mock_output_passes_its_own_output_check(staged, stage, stage_idx):
    """每个阶段的 mock 产出都要过 `check_output`。

    01/02 现在由钩子强制执行这条；03/04/05 暂时没接，但一个连自家判据都
    过不了的 mock 产出本身就是缺陷 —— 今天没接的钩子明天会接。
    """
    out = _run_stage(stage, stage_idx, f"pytest-mockrun-{stage[:2]}")
    assert out.strip(), f"{stage}: mock 一个字都没说"

    name = staged(stage, out)
    verdict = check_output(name, stage)

    assert verdict.ok, (
        f"{stage} 的 mock 产出过不了自家门禁 —— 该补 mock，不是降阈值:\n"
        + "\n".join(verdict.lines))


@pytest.mark.parametrize("stage_idx,stage", list(enumerate(STAGES)))
def test_mock_output_has_headroom_above_threshold(stage, stage_idx):
    """产出量要比阈值高出 20% 以上，不许压线。

    压线的后果：阈值往上动一个字符，e2e 就红 —— 而红的原因是 mock 太薄，
    不是被测的机制坏了。假信号会训练人忽略红灯。
    """
    from sw_lib.workflow.output_check import _strip_noise

    out = _run_stage(stage, stage_idx, f"pytest-mockroom-{stage[:2]}")
    n = len(_strip_noise(out))
    need = int(_MIN_SUBSTANCE * _HEADROOM)

    assert n >= need, (
        f"{stage}: mock 产出去噪后 {n} 字符，低于阈值 {_MIN_SUBSTANCE} "
        f"的 {_HEADROOM} 倍余量（{need}）")


# ── 形态判据：不是凑字数 ──

def test_planning_output_carries_the_sections_its_stage_is_about(staged):
    """02 的产出必须谈到它这一阶段真正要交的东西。

    只把 WBS 那三行复述得更啰嗦也能过字数，但 02 的模板要的是
    Task DAG（含依赖与验证方式）、测试策略、技术细节。产出形态对不上
    模板，下游的 `fact_pack.build_plan` 提取出来就是空段
    （它按标题关键词找 `task dag` / `test strategy` / `tech detail`）。
    """
    out = _run_stage("02-planning", 1, "pytest-mockplan")

    for keyword in ("Task DAG", "Verify", "Deps", "Test Strategy",
                    "Tech Detail"):
        assert keyword in out, f"02 的 mock 产出缺少 `{keyword}` 段"


def test_archive_output_carries_summary_memory_retro(staged):
    """05 的产出必须有 Summary / Memory / Retro。

    `check_05-archive.sh` 就在 grep 这些章节（目前只 warn 不 fail）。
    mock 走的是 `_scenario_generic`，一句「工作已顺利完成」就收工 ——
    归档阶段等于没被 e2e 覆盖。
    """
    out = _run_stage("05-archive", 4, "pytest-mockarch")

    for keyword in ("Summary", "Memory", "Retro"):
        assert keyword in out, f"05 的 mock 产出缺少 `{keyword}` 段"


def test_planning_wbs_items_stay_unchecked():
    """02 的 WBS 条目必须仍是 `[ ]`（e2e 的既有判据依赖这一点）。

    补内容时很容易顺手写成 `[x]`，而 `verify.py` 与 `acceptance.md` 都在
    确认「AI Output 区里的 WBS `[ ]` 没被误替换成 `[x]`」—— 那条判据是为了
    守住「产出区不被全局替换污染」，若 mock 自己就输出 `[x]`，判据当场失效。
    """
    import re

    out = _run_stage("02-planning", 1, "pytest-mockwbs")
    assert re.search(r"^\d+\.\s*\[ \]", out, re.M), \
        "02 的 mock 产出里没有未勾选的 WBS 条目"
    assert not re.search(r"^\d+\.\s*\[x\]", out, re.M), \
        "02 的 mock 产出出现已勾选的 WBS 条目，e2e 的保真判据会失效"
