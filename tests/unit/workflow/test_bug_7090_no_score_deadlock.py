"""任务 `7090` 的 01 门禁死锁 —— 用 MockAgent 走生产路径复现。

## 现场（`workspace/tasks/7090/.log`，逐条可查）

    14:42:01  agent 🔧 question {'BBS 用途' ...}        ← 5 轮 question
    14:48:32  agent 🔧 question {'技术栈选择' ...}         每轮用户都答了
    14:49:31  agent 🔧 question {'数据库选择' ...}
    14:50:37  agent 🔧 question {'MVP 功能范围' ...}
    14:51:43  agent 🔧 question {'帖子内容格式' ...}
    14:52:32  agent 好的，纯文本格式。…… **推荐方案 A** …… 你同意方案 A 吗？
    14:52:32  agent complete reply (357 chars)
    14:52:32  sw    ⏸ 等待用户拍板: 门禁签署（01-brainstorming）
    14:52:40  user  [A] 批准头脑风暴
    14:52:42  sw    ❌ 产出区未给出歧义分数（hook-01-01 要求 0-10 的自评）
    14:52:42  error 硬校验未通过，必须满足所有条件才能推进

**这是死锁**：agent 已 `complete reply` 并被 harness 收尾，不会再发言；
门禁要一个只有 agent 能写的数字；用户手里只有 `/advance`，敲一次得到
同一句报错。没有任何角色能打破它 —— 与 2.9.11（qqqq 的 O6：在 04 要求
README，而只有 03 有 write_file）同型：**拦住一条路而不给替代路径**。

## 为什么必须用 MockAgent 复现

`harness-criterion-design` 的红绿第 1 步：「用 MockAgent 加真实的
`StageRunnable._save_stage_output` 复现，不要手搓文件 —— 你要复现的是
agent 做了什么，而不是你对它做了什么的想象。」

手搓夹具会漏掉真实链路上的东西。这里至少有三处只有走生产路径才对：
围栏 nonce 由 `issue_output_nonce` 签发、产出由 `_save_stage_output`
包进围栏、`.state` 的 decisions 由 `record_decision` 落盘。

## 判据的边界

7090 的产出**实质内容是够的**（357 字符，阈值 80），缺的只有自评。
所以本文件同时守住反面：产出为空时**仍须拦下**。若「不给分数就放行」
顺手把空转也放过，就是把死锁换成了更坏的东西 —— 坏产出过闸。
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

_STAGE = "01-brainstorming"
_TASK = "pytest-bug7090"

#: 7090 现场用户的 5 个回答，顺序与 .log 一致。
_ANSWERS = [
    "A. 技术社区论坛 - 程序员技术交流、问答、分享",
    "A. React + FastAPI - React 18 + Python FastAPI，前后端分离，响应快",
    "A. SQLite - 轻量级，无需安装，适合 MVP/个人项目",
    "A. 基础功能 - 用户注册登录、发帖/回帖、分类、搜索、用户主页",
    "A. 纯文本 - 最简单，无格式支持",
]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """零延时 + 屏蔽 sw_log。测试自己不该变成慢的那一环。"""
    monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
    monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                        lambda name, msg, src="sw": None)


@pytest.fixture
def task():
    """建任务：真实模板 + 已签署的 gate（与 7090 现场一致 —— 用户批准过）。"""
    created = []

    def _make(name=_TASK):
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)
        write_state(name, {
            "id": name, "stage": _STAGE, "stage_idx": 0,
            "stage_status": "running", "target_dir": f"repo/{name}",
            "stages": {_STAGE: {"gate": {
                "items": [
                    {"key": "design_approved", "label": "Design approved",
                     "checked": True},
                    {"key": "ambiguity_resolved",
                     "label": "Ambiguity resolved", "checked": True},
                ],
                "signed_by": "user", "signed_at": "2026-09-03T14:52:40",
            }}},
        })
        (d / f"{_STAGE}.md").write_text(
            (TPLS / f"{_STAGE}.md").read_text(encoding="utf-8"),
            encoding="utf-8")
        return name

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def _run_mock(name, monkeypatch, no_score: bool) -> str:
    """跑一轮 mock 的 01 场景，返回它作为 agent 说出来的文本。

    每轮 question 的回答同时走 `record_decision` 落进 `.state` ——
    与 TUI 的真实行为一致（`decisions` 是 harness 写的证据，
    7090 现场记着 5 条）。
    """
    monkeypatch.setenv("SW_MOCK_BRAINSTORM_NO_SCORE", "1" if no_score else "0")
    logs = []
    asked = {"n": 0}

    def on_ask_user(questions, res_queue: queue.Queue):
        answers = []
        for q in questions:
            idx = min(asked["n"], len(_ANSWERS) - 1)
            answer = _ANSWERS[idx]
            asked["n"] += 1
            ss.record_decision(name, _STAGE, q.get("question", ""), answer)
            answers.append(answer)
        res_queue.put(answers)

    agent = MockAgent(
        {
            "add_log": lambda src, msg: logs.append((src, msg)),
            "is_running": lambda: True,
            "on_ask_user": on_ask_user,
        },
        name, _STAGE, 0,
    )
    agent.running = True
    agent._run_scenario()
    return "\n".join(msg for src, msg in logs if src == "agent")


def _save(name, output):
    """走生产落盘路径把回复写进围栏区（与 7090 现场同一条路）。"""
    r = StageRunnable(_STAGE, 0, MagicMock(), MagicMock(), MagicMock(),
                      MagicMock())
    r._save_stage_output(name, output)


# ── 前提自检：复现场景确实复现了 7090 的形状 ──

def test_repro_matches_the_7090_shape(task, monkeypatch):
    """前提：5 轮问答、产出够长、正文里追问方案、**没有分数**。

    前提必须显式守住 —— 否则一个空转的复现也能让下面的判据看起来在工作。
    这四条同时成立，才是 7090；少一条测的就是别的东西。
    """
    name = task()
    reply = _run_mock(name, monkeypatch, no_score=True)
    _save(name, reply)

    assert ss.count_decisions(name, _STAGE) == 5, \
        f"问答轮次与 7090 现场不符: {ss.count_decisions(name, _STAGE)}"

    from sw_lib.workflow.output_check import _strip_noise, read_output_region
    region = read_output_region(name, _STAGE)
    assert region and len(_strip_noise(region)) >= _MIN_SUBSTANCE, \
        "复现的产出不够长 —— 那会撞上「实质内容不足」，测的就不是本形状了"

    assert "你同意方案 A 吗" in reply, "正文里的追问没了 —— 那是 7090 的关键特征"
    from sw_lib.workflow.output_check import read_ambiguity_score
    assert read_ambiguity_score(name, _STAGE) is None, \
        "复现场景给了分数 —— 那就不是 7090 了"


def test_default_scenario_still_gives_a_score(task, monkeypatch):
    """默认形态不变：正常场景仍然自评。复现开关默认关闭。"""
    name = task()
    reply = _run_mock(name, monkeypatch, no_score=False)
    _save(name, reply)

    from sw_lib.workflow.output_check import read_ambiguity_score
    assert read_ambiguity_score(name, _STAGE) == 9, \
        "默认形态的产出里没有分数 —— 开关默认值反了"


# ── 主判据：7090 的那一刻不得再死锁 ──

def test_7090_shape_is_not_blocked(task, monkeypatch):
    """**这就是 14:52:42 那一刻。** 现在必须放行。

    agent 已被收尾、不会再发言，用户手里只有 `/advance`。
    拦下去等于让任务永远停在这里。
    """
    name = task()
    _save(name, _run_mock(name, monkeypatch, no_score=True))

    verdict = check_output(name, _STAGE)

    assert verdict.ok, (
        "7090 的形态仍被硬拦 —— 死锁原样复现:\n" + "\n".join(verdict.lines))


def test_7090_shape_is_recorded_as_unavailable(task, monkeypatch):
    """放行必须留痕：`.state` 记 `unavailable`，不伪造成 ✅。"""
    name = task()
    _save(name, _run_mock(name, monkeypatch, no_score=True))
    check_output(name, _STAGE)

    record = ss.read_ambiguity_record(name, _STAGE)
    assert record.get("status") == "unavailable", \
        f"放行了却没记账，下游无从判断这项没测到: {record}"
    assert record.get("reason"), "留痕必须说明为什么不可得"


def test_7090_verdict_says_it_is_unknown_not_pass(task, monkeypatch):
    """门禁输出要明说这项是 ❓ —— 三态不得二态化。"""
    name = task()
    _save(name, _run_mock(name, monkeypatch, no_score=True))

    text = "\n".join(check_output(name, _STAGE).lines)

    assert "❓" in text, f"放行了却没说这项未测到:\n{text}"


def test_hook_script_exits_zero_on_7090_shape(task, monkeypatch):
    """走**钩子脚本**再验一次 —— 单元绿不等于机制接通（本仓库已六次）。

    `check_01-brainstorming.sh` 是 TUI 真正调用的那一个。
    它的退出码才是「用户敲 /advance 能不能推进」的直接决定者。
    """
    import subprocess
    from sw_lib.core.config import ROOT

    name = task()
    _save(name, _run_mock(name, monkeypatch, no_score=True))

    res = subprocess.run(
        [str(ROOT / "hooks" / "check_01-brainstorming.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, check=False)

    assert res.returncode == 0, (
        "钩子仍然拒绝 7090 的形态 —— 用户敲 /advance 依旧推不动:\n"
        f"{res.stdout}\n{res.stderr}")
    assert "❓" in res.stdout, f"钩子输出没有 ❓:\n{res.stdout}"


# ── 反面：放宽的只是自评，硬规则原样 ──

def test_empty_output_from_same_path_is_still_blocked(task, monkeypatch):
    """同一条落盘路径写入空转产出 —— **仍须拦下**。

    这条是本次改动的边界。7090 的产出是够的（357 字符），缺的只有自评；
    若「不给分数就放行」顺手把空转也放过，就是把死锁换成坏产出过闸，
    那比原来的 bug 更糟（`7ee1928` 记过这个近失）。
    """
    name = task()
    _save(name, "___\nTODO\n")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, \
        "空产出过闸了 —— 硬规则被一起放宽了:\n" + "\n".join(verdict.lines)
