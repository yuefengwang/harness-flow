"""01 阶段完成性：0 轮问答不得过闸（A13 第 1 步，纯证据判据）。

## 这条判据要解决什么

任务 `7090` 的 `.state` 至今 `stage_status: running` —— **那个阶段从来
没走完，门禁却已经判过了**。链条是这样断的：

    agent 用正文提问（不是 question 工具）
      → harness 收不到 question 事件
      → 判定「agent 说完了」
      → TUI 弹出门禁签署
      → 用户批准（界面就是这么请求的，不是用户的错）
      → 门禁去量一个尚未产生的产出

判据的**输入**由被判者提供已经很危险（2.9.17 判过），判据的**触发时机**
也由被判者控制，则门禁只会在错误的时刻测量错误的东西（形状 S11）。

## 为什么只用 `decisions`，不用文本启发式

用户拍板：**只用纯证据，零误报优先**。

`decisions` 是干净的证据 —— `record_decision` 只在用户**实际回答**时写入，
三个生产调用点（`tui.py` / `web/engine_manager.py` / `cli/test_cmd.py`）
无一例外。agent 伪造不了，不写也跳不过：它根本不经手这个字段。

被否决的方案：判断产出区结尾是不是问句。实测 7090 结尾是「你同意方案 A
吗？」、helloworld 是「这两者差异很大」，而其余五个任务都是「请输入
`/advance`」—— 信号看着很强。但那仍然是**读 agent 写的文本**，与歧义
分数同一个错（用户在 2.9.17 那轮已经纠正过一次）。判据不该第三次栽在
同一个形状上。

## 这条判据的边界

只查「有没有问过」，**不查「问够几轮」**。`hook-01-02` 的「≥3 questions」
是 prompt 级的收敛建议，不是门禁硬规则 —— 判据不替 prompt 做裁判。
把轮次下界写进门禁会立刻撞上 2.9.10（ppppp 14 轮无上界）的反面：
下界一旦硬化，agent 就会为了凑数而提问。
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
from sw_lib.workflow.output_check import check_output

_STAGE = "01-brainstorming"
_TASK = "pytest-completion"

#: 一段够长的产出，确保不会撞上「实质内容不足」那条前置硬规则。
#: 本文件测的是完成性，不是产出量 —— 红必须来自本判据。
_SUBSTANCE = (
    "需求分析完成。核心结论：\n"
    "- 商品类型: 实物商品\n"
    "- 技术栈: React 18.2 + Python FastAPI 0.110 + SQLAlchemy 2.0\n"
    "- 功能范围: 商品展示、购物车、订单管理、支付、物流追踪\n"
    "- 部署方案: Docker Compose + Nginx 反向代理 + Let's Encrypt TLS\n"
    "- 数据库: PostgreSQL 16 + Redis 7 缓存 + Celery 异步任务队列\n"
    "产出已全部回填至阶段文件。\n"
    "歧义分数：9\n"
)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("SW_MOCK_RESPONSE_DELAY", "0")
    monkeypatch.setattr("sw_lib.agents.mock.sw_log",
                        lambda name, msg, src="sw": None)


@pytest.fixture
def task():
    """建任务：真实模板 + 已签署 gate（与 7090 现场一致 —— 用户批准过）。"""
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
                "items": [{"key": "design_approved",
                           "label": "Design approved", "checked": True}],
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


def _save(name, output):
    """走生产落盘路径写入围栏区。"""
    r = StageRunnable(_STAGE, 0, MagicMock(), MagicMock(), MagicMock(),
                      MagicMock())
    r._save_stage_output(name, output)


# ── 主判据：0 轮问答必须拦下 ──

def test_zero_decisions_is_blocked(task):
    """一轮都没问过 —— 01 的语义不成立，必须拦。

    产出再长也不行：01 阶段的产出**应当**是问答的结论。
    没有问答而有结论，那结论是 agent 自己编的需求。
    """
    name = task()
    _save(name, _SUBSTANCE)

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, \
        "0 轮问答却过闸了 —— 阶段完成性判据没接上:\n" + "\n".join(verdict.lines)
    text = "\n".join(verdict.lines)
    assert "问答" in text, f"失败信息没说清缺的是什么:\n{text}"
    assert "question" in text, f"失败信息没给出可执行的下一步:\n{text}"


def test_one_decision_is_enough_to_pass(task):
    """有过一轮真实问答就放行 —— 不查「问够几轮」。

    轮次下界属 prompt 的收敛条件，不是门禁硬规则。
    硬化下界会逼出「为凑数而提问」，那是 2.9.10 的反面。
    """
    name = task()
    ss.record_decision(name, _STAGE, "需求范围？", "基础电商功能")
    _save(name, _SUBSTANCE)

    verdict = check_output(name, _STAGE)

    assert verdict.ok, \
        "一轮问答仍被拦 —— 判据把 prompt 的建议当成了硬规则:\n" \
        + "\n".join(verdict.lines)


# ── 证据的性质：agent 碰不到它 ──

def test_decisions_come_from_user_answers_not_agent_text(task):
    """agent 在正文里写什么都不影响这条判据 —— 这正是它可靠的原因。

    7090 的 agent 输出了 357 字符的完整方案，还写了「你同意方案 A 吗」。
    若判据读文本，这些都会成为「它问过了」的假证据。
    `decisions` 只认用户经 TUI/web 实际回答的记录。
    """
    name = task()
    _save(name, _SUBSTANCE
          + "\n我已经问过你很多问题了。\n你同意方案 A 吗？\n"
            "已完成 5 轮 question 交互。\n")

    verdict = check_output(name, _STAGE)

    assert not verdict.ok, \
        "agent 声称问过就放行了 —— 判据在读自述，不是证据"


# ── MockAgent 复现：7090 的真实形态必须放行 ──

def test_7090_shape_with_real_decisions_passes(task, monkeypatch):
    """7090 走完 5 轮真实问答 —— 完成性这一关必须过。

    它的问题不在「没问过」（问了 5 轮，`.log` 可查），
    而在最后那次追问没走 question 工具。本判据不管后者 —— 那需要
    harness 侧的「最后一次发言是否在等回答」事件，属未实施部分。
    这条守着**不误伤**：7090 不该被这条新判据拦下。
    """
    monkeypatch.setenv("SW_MOCK_BRAINSTORM_NO_SCORE", "1")
    name = task()
    logs = []
    answers_given = ["A. 技术社区论坛", "A. React + FastAPI", "A. SQLite",
                     "A. 基础功能", "A. 纯文本"]
    idx = {"n": 0}

    def on_ask_user(questions, res_queue: queue.Queue):
        out = []
        for q in questions:
            a = answers_given[min(idx["n"], len(answers_given) - 1)]
            idx["n"] += 1
            ss.record_decision(name, _STAGE, q.get("question", ""), a)
            out.append(a)
        res_queue.put(out)

    agent = MockAgent({"add_log": lambda s, m: logs.append((s, m)),
                       "is_running": lambda: True,
                       "on_ask_user": on_ask_user},
                      name, _STAGE, 0)
    agent.running = True
    agent._run_scenario()
    _save(name, "\n".join(m for s, m in logs if s == "agent"))

    assert ss.count_decisions(name, _STAGE) == 5, "复现的问答轮次不对"

    verdict = check_output(name, _STAGE)
    assert verdict.ok, \
        "7090 被新判据误伤了 —— 它问了 5 轮:\n" + "\n".join(verdict.lines)


# ── 不得越界 ──

@pytest.mark.parametrize("stage", ["02-planning", "03-coding", "04-review"])
def test_other_stages_are_not_affected(task, stage):
    """完成性判据只作用于 01 —— 别的阶段没有「必须提问」这条语义。

    02 及以后的阶段本来就不以问答为核心动作。把这条推广过去
    会立刻造出 2.9.11（qqqq 的 O6）那种「没有角色能满足它」的死锁。
    """
    name = task()
    d = TASKS / name
    write_state(name, {"id": name, "stage": stage, "stage_idx": 1,
                       "stage_status": "running",
                       "target_dir": f"repo/{name}"})
    (d / f"{stage}.md").write_text(
        (TPLS / f"{stage}.md").read_text(encoding="utf-8"), encoding="utf-8")

    r = StageRunnable(stage, 1, MagicMock(), MagicMock(), MagicMock(),
                      MagicMock())
    r._save_stage_output(name, _SUBSTANCE * 2)

    verdict = check_output(name, stage)

    assert verdict.ok, \
        f"{stage} 被 01 的完成性判据误伤:\n" + "\n".join(verdict.lines)


# ── 边界：判据不得恒真 ──

def test_criterion_is_not_tautological(task):
    """空产出 + 有问答 —— 仍须被前置的实质内容判据拦下。

    确认新判据没有意外地让别的规则失效。
    """
    name = task()
    ss.record_decision(name, _STAGE, "需求？", "做个网站")
    _save(name, "___\nTODO\n")

    assert not check_output(name, _STAGE).ok, \
        "空产出过闸了 —— 加新判据时把硬规则弄坏了"
