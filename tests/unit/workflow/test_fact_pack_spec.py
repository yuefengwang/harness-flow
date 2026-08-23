"""A3 的验收 8 / 9 / 10 / 17 / 18：spec.md 的三段拼装与 C1 的直接判据。

验收 17 是 C1（上下文污染）的直接判据：**04 阶段完全不读 `03-coding.md`**。
reviewer 复核的对象必须是事实，不是「我做完了，测试都过了」这类自述。

2.7 实测：三段里两段常空 —— `decisions` 五个阶段全部为空，
ADR 段全是 `___` 占位符。A3 的价值在于让这个贫瘠状态**可见**，而非掩盖。
"""

import json

import pytest

from sw_lib.core import git_repo as G
from sw_lib.core.state import read_state, write_state
from sw_lib.workflow import fact_pack as FP


SENTINEL = "SENTINEL-c9f13a-developer-self-narrative"


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    monkeypatch.setattr("sw_lib.core.config.TASKS", tasks_root, raising=False)
    monkeypatch.setattr("sw_lib.core.state.TASKS", tasks_root, raising=False)
    monkeypatch.setattr(FP, "TASKS", tasks_root, raising=False)

    task = "a3spec"
    task_dir = tasks_root / task
    task_dir.mkdir()
    target = tmp_path / "repo" / task
    target.mkdir(parents=True)
    (target / "kept.py").write_text("x = 1\n", encoding="utf-8")
    info = G.ensure_repo(str(target), task)
    write_state(task, {
        "id": task, "stage": "03-coding", "stage_idx": 2,
        "stage_status": "running", "target_dir": str(target),
        "review": {"baseline_sha": info.sha, "baseline_kind": info.kind},
    })
    return task, task_dir, target


def _spec(task_dir):
    return (task_dir / "facts" / "spec.md").read_text(encoding="utf-8")


def _manifest(task_dir):
    return json.loads((task_dir / "facts" / "manifest.json").read_text(encoding="utf-8"))


# ── 验收 8：三段带可信度标注，空态显式 ──

def test_spec_has_three_sections_with_confidence_labels(task_env):
    task, task_dir, _ = task_env
    FP.generate(task)
    spec = _spec(task_dir)

    assert "原始需求" in spec and "已拍板决策" in spec and "设计陈述" in spec
    assert spec.count("可信度") >= 3
    # 低可信度段必须被标出来 —— 否则 agent 自述会与用户原文等权。
    assert "可信度：低" in spec


def test_empty_decisions_renders_explicit_empty_state(task_env):
    task, task_dir, _ = task_env
    FP.generate(task)
    spec = _spec(task_dir)

    # 2.7 查明 decisions 为空的原因：record_decision 只在用户实际回答时写入，
    # agent 调了 question 但无人应答时一条都不落盘。
    assert "无用户拍板记录" in spec


def test_missing_context_renders_explicit_empty_state(task_env):
    task, task_dir, _ = task_env
    FP.generate(task)

    assert "用户未提供上下文" in _spec(task_dir)


# ── 验收 9：占位符不外泄 ──

def test_adr_placeholders_do_not_leak_into_spec(task_env):
    task, task_dir, _ = task_env
    (task_dir / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n"
        "## Design Decision (ADR)\n"
        "- **Proposal**: ___\n"
        "- **Why this**: ___\n"
        "- **Impact**: ___\n",
        encoding="utf-8")

    FP.generate(task)
    spec = _spec(task_dir)

    # 把 `- **Proposal**: ___` 喂给 reviewer，它会当成真实的设计陈述去审，
    # 产出的是对空气的评价（A3 的 2.7）。
    assert "___" not in spec, "占位符原样外泄"
    assert "未填写" in spec


def test_real_adr_content_is_preserved(task_env):
    task, task_dir, _ = task_env
    (task_dir / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n"
        "## Design Decision (ADR)\n"
        "- **Proposal**: 采用临时索引方案\n"
        "- **Why this**: 零副作用\n"
        "- **Impact**: 仅影响 diff 采集\n",
        encoding="utf-8")

    FP.generate(task)

    assert "采用临时索引方案" in _spec(task_dir)


# ── 验收 10：spec_availability 三态 ──

def test_spec_availability_reports_three_states(task_env):
    task, task_dir, _ = task_env
    (task_dir / "01-brainstorming.md").write_text(
        "## Design Decision (ADR)\n- **Proposal**: ___\n", encoding="utf-8")
    write_state(task, {**read_state(task), "context": "把 diff 采全"})

    FP.generate(task)
    avail = _manifest(task_dir)["spec_availability"]

    assert avail == {"context": "present", "decisions": "empty",
                     "adr": "placeholder_only"}


# ── 验收 17：04 的输入不含 03-coding.md 的任何内容（C1 的直接判据）──

def test_no_developer_narrative_reaches_facts(task_env):
    task, task_dir, _ = task_env
    # 哨兵写在**最可能被提取的位置**（A3 的 9.3）：Implementation Notes 段内，
    # 不是注释里、也不在围栏外 —— 那些区域本来就不会被读，测了等于没测。
    (task_dir / "03-coding.md").write_text(
        "# 03-Coding\n\n"
        "## Implementation Notes\n"
        f"- 我已完成全部实现，测试都过了。{SENTINEL}\n\n"
        "## Gate\n- [x] Tests pass\n",
        encoding="utf-8")

    FP.generate(task)

    facts_dir = task_dir / "facts"
    for path in sorted(facts_dir.iterdir()):
        body = path.read_text(encoding="utf-8", errors="replace")
        assert SENTINEL not in body, f"developer 自述泄漏进 {path.name}"

    claims = ((read_state(task).get("stages") or {})
              .get("03-coding") or {}).get("claims") or {}
    assert SENTINEL not in json.dumps(claims, ensure_ascii=False)


# ── 验收 18：claims 与真实 diff 不一致须被记录 ──

def test_claims_diff_mismatch_is_recorded_in_warnings(task_env):
    task, task_dir, target = task_env
    (target / "real.py").write_text("a = 1\n", encoding="utf-8")
    FP.record_claims(task, task_ids=["T1-1"], verify_cmd="python3 -m pytest",
                     files_touched=["ghost_never_written.py"])

    FP.generate(task)
    manifest = _manifest(task_dir)

    joined = " ".join(manifest["warnings"])
    assert "ghost_never_written.py" in joined, \
        "声明了 diff 里没有的文件，事实层未对照出来"


def test_claims_written_through_update_state(task_env):
    """验收 11：claims 经 update_state 写入，不抹掉既有子树。"""
    task, task_dir, _ = task_env
    from sw_lib.workflow import stage_state as ss

    ss.sign_gate(task, "03-coding", by="user")
    FP.record_claims(task, task_ids=["T1-1"], verify_cmd="pytest",
                     files_touched=["a.py"])

    stages = read_state(task).get("stages") or {}
    coding = stages.get("03-coding") or {}
    assert coding["claims"]["task_ids"] == ["T1-1"]
    assert coding.get("gate"), "写 claims 抹掉了 Gate 签名（A0 的 R1 回归锚点）"
