"""stage_state — Gate/Route 的 JSON 单一状态源。

核心保证（G3）：**agent 无论写什么内容，都不能改变门禁判定结果。**
这是整类解析歧义 bug 的根治点，因此这里的断言围绕「伪造尝试无效」展开。
"""
import json
import shutil

import pytest

from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import read_state, write_state
from sw_lib.workflow import stage_state as ss

_TASK = "pytest-stage-state"


@pytest.fixture
def task():
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {
        "id": _TASK, "stage": "02-planning", "stage_idx": 1,
        "stage_status": "running",
    })
    yield _TASK
    shutil.rmtree(d, ignore_errors=True)


# ── 模板播种 ──

@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_has_gate_items_in_template(stage):
    """每个阶段的模板都必须定义门禁项，否则该阶段无从签署。"""
    items = ss.parse_template_gate(stage)
    assert items, f"{stage} 模板缺少 ## Gate 项"
    assert all(i.key for i in items), f"{stage} 有空 key"
    assert len(set(i.key for i in items)) == len(items), f"{stage} key 重复"


def test_gate_keys_survive_placeholder_labels():
    """04-review 的 ``Full build: `___` `` 带反引号占位符，key 不能为空。"""
    items = ss.parse_template_gate("04-review")
    keys = [i.key for i in items]
    assert "full_build" in keys, keys


def test_seed_is_idempotent(task):
    assert ss.seed_gate(task, "02-planning") is True
    first = read_state(task)["stages"]["02-planning"]["gate"]
    assert ss.seed_gate(task, "02-planning") is False, "重复播种应为 no-op"
    assert read_state(task)["stages"]["02-planning"]["gate"] == first


def test_seed_preserves_other_state_fields(task):
    """播种不能踩掉既有状态字段。"""
    ss.seed_gate(task, "02-planning")
    st = read_state(task)
    assert st["stage"] == "02-planning"
    assert st["stage_idx"] == 1
    assert st["stage_status"] == "running"


# ── 签署 ──

def test_unsigned_by_default(task):
    gate = ss.read_gate(task, "02-planning")
    assert gate.exists is True
    assert gate.signed is False
    assert len(gate.pending) == len(gate.items)


def test_sign_marks_all_items(task):
    assert ss.sign_gate(task, "02-planning", by="user") is True
    gate = ss.read_gate(task, "02-planning")
    assert gate.signed is True
    assert gate.pending == []
    assert gate.signed_by == "user"
    assert gate.signed_at


def test_sign_works_without_prior_seed(task):
    """没 seed 过也能签 —— 签署入口不该依赖调用顺序。"""
    st = read_state(task)
    assert "stages" not in st or not st.get("stages")
    assert ss.sign_gate(task, "02-planning") is True
    assert ss.read_gate(task, "02-planning").signed is True


def test_sign_records_auto_vs_user(task):
    ss.sign_gate(task, "02-planning", by="auto")
    assert ss.read_gate(task, "02-planning").signed_by == "auto"


def test_partial_signature_is_not_signed(task):
    """部分勾选不算签署 —— 门禁是全体通过，不是任一通过。

    这条锁死 `all` 语义：若退化成 `any`，一项勾上就放行，
    /advance 会在门禁未满足时推进。
    """
    ss.seed_gate(task, "02-planning")
    st = read_state(task)
    items = st["stages"]["02-planning"]["gate"]["items"]
    assert len(items) >= 2, "本用例需要多于一项的 Gate"
    items[0]["checked"] = True   # 只勾第一项
    write_state(task, st)

    gate = ss.read_gate(task, "02-planning")
    assert gate.signed is False, "部分勾选被误判为已签署"
    assert len(gate.pending) == len(items) - 1
    assert ss.stage_todo(task, "02-planning"), "部分勾选时必须仍有待办"


def test_reset_gate_revokes_signature(task):
    ss.sign_gate(task, "02-planning")
    ss.reset_gate(task, "02-planning")
    gate = ss.read_gate(task, "02-planning")
    assert gate.signed is False
    assert gate.signed_by is None
    assert gate.exists is True, "撤销签署不能丢掉门禁项定义"


# ── G3：agent 伪造尝试必须无效 ──

def test_agent_cannot_forge_gate_via_markdown(task):
    """agent 在 Markdown 里写满 [x] 也不能让门禁通过。"""
    (TASKS / _TASK / "02-planning.md").write_text(
        "# 02-Planning\n\n"
        "## 🤖 AI Output\n"
        "全部完成！\n\n"
        "## Gate\n"
        "- [x] Tests pass\n"
        "- [x] No regression risk\n",
        encoding="utf-8")
    assert ss.read_gate(task, "02-planning").signed is False, \
        "Markdown 里的 [x] 绝不能影响判定"
    assert ss.stage_todo(task, "02-planning"), "门禁应仍未通过"


def test_agent_repeating_gate_section_does_not_affect_todo(task):
    """现场 bug 4 的形状：agent 正文复述 Gate，文件里多份 ## Gate。"""
    dup = ("# 02-Planning\n\n## Gate\n- [ ] Tests pass\n"
           "- [ ] No regression risk\n") * 4
    (TASKS / _TASK / "02-planning.md").write_text(dup, encoding="utf-8")
    ss.sign_gate(task, "02-planning")
    assert ss.stage_todo(task, "02-planning") == [], \
        "重复 Gate 区最多是显示噪音，不能影响判定"


def test_markdown_absent_does_not_break_judgment(task):
    """连阶段文件都没有时判定依然可用（状态不寄生于 Markdown）。"""
    md = TASKS / _TASK / "02-planning.md"
    if md.exists():
        md.unlink()
    ss.sign_gate(task, "02-planning")
    assert ss.stage_todo(task, "02-planning") == []


def test_agent_cannot_forge_route_via_markdown(task):
    (TASKS / _TASK / "04-review.md").write_text(
        "# 04-Review\n- **Route**: `05-Archive`\n", encoding="utf-8")
    assert ss.read_route(task) is None, "Markdown 里的 Route 不参与判定"


# ── Route ──

def test_route_roundtrip(task):
    assert ss.write_route(task, "05-Archive", by="user") is True
    assert ss.read_route(task) == "05-archive"


@pytest.mark.parametrize("raw,expected", [
    ("05-Archive", "05-archive"),
    ("05-archive", "05-archive"),
    ("`03-Coding`", "03-coding"),
    ("  02-Planning  ", "02-planning"),
])
def test_route_normalizes_case_and_backticks(task, raw, expected):
    assert ss.write_route(task, raw) is True
    assert ss.read_route(task) == expected


@pytest.mark.parametrize("bad", ["", "待定", "___", "06-nonexistent", "archive"])
def test_route_rejects_invalid_target(task, bad):
    """非法目标必须拒写并如实返回 False，不能静默成功。"""
    assert ss.write_route(task, bad) is False
    assert ss.read_route(task) is None


def test_reset_route(task):
    ss.write_route(task, "05-Archive")
    ss.reset_route(task)
    assert ss.read_route(task) is None


# ── 校验 ──

def test_todo_empty_only_when_signed(task):
    assert ss.stage_todo(task, "02-planning"), "未签署时必须有待办"
    ss.sign_gate(task, "02-planning")
    assert ss.stage_todo(task, "02-planning") == []


def test_review_todo_requires_both_gate_and_route(task):
    """04 要 Gate 与 Route 都落定才算完。"""
    assert len(ss.stage_todo(task, "04-review")) == 2
    ss.sign_gate(task, "04-review")
    todo = ss.stage_todo(task, "04-review")
    assert len(todo) == 1 and "Route" in todo[0], todo
    ss.write_route(task, "05-Archive")
    assert ss.stage_todo(task, "04-review") == []


# ── nonce ──

def test_nonce_is_stable_across_calls(task):
    n1 = ss.issue_output_nonce(task, "02-planning")
    n2 = ss.issue_output_nonce(task, "02-planning")
    assert n1 == n2, "同阶段必须复用同一 nonce，否则多轮 flush 会层层追加"
    assert ss.read_output_nonce(task, "02-planning") == n1


def test_nonce_differs_per_stage(task):
    a = ss.issue_output_nonce(task, "02-planning")
    b = ss.issue_output_nonce(task, "03-coding")
    assert a != b


def test_nonce_unpredictable(task):
    """nonce 必须足够随机，否则 agent 能猜到并伪造边界。"""
    n = ss.issue_output_nonce(task, "02-planning")
    assert len(n) >= 8 and n.isalnum(), n


# ── 健壮性 ──

def test_corrupt_stages_field_does_not_crash(task):
    """.state 被写坏时读取要降级而不是抛异常。"""
    st = read_state(task)
    st["stages"] = "not-a-dict"
    write_state(task, st)
    gate = ss.read_gate(task, "02-planning")
    assert gate.exists is True and gate.signed is False
    assert ss.sign_gate(task, "02-planning") is True


def test_gate_with_corrupt_items_recovers(task):
    st = read_state(task)
    st["stages"] = {"02-planning": {"gate": {"items": "bad"}}}
    write_state(task, st)
    assert ss.read_gate(task, "02-planning").exists is True


def test_state_stays_valid_json(task):
    ss.sign_gate(task, "02-planning")
    ss.write_route(task, "05-Archive", stage="04-review")
    raw = (TASKS / _TASK / ".state").read_text(encoding="utf-8")
    json.loads(raw)  # 不抛即通过


def test_missing_task_returns_false(task):
    """任务不存在时写操作如实失败。"""
    assert ss.sign_gate("no-such-task-xyz", "02-planning") is False
    assert ss.write_route("no-such-task-xyz", "05-Archive") is False
