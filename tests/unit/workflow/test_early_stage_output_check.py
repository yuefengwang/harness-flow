"""01/02 阶段的门禁必须检查「产出区里真有内容」。

任务 `helloworld` 的现场：01 与 02 都过闸并推进到 03，而两个阶段文件的
模板区**全部**仍是 `___` 与空表格：

    ## Ambiguity Score
    Score: [0-10] | Goal: ___
    ## Pre-mortem
    | Risk | Prevention |
    |------|-----------|
    | | |          ← 空

`check_01-brainstorming.sh` 只做两件事：文件存在、`.state` 里 gate 已签署。
`check_02-planning.sh` 只 `grep -q "## Task DAG"` —— 那个标题**是模板自带的**，
不管 agent 干了什么都在，判据恒真。

对比 04 阶段：`check_04-review.sh` 早就有占位符检查（Reroute Evidence 表
不许全是 `___`）。也就是说「声明与事实分开验」这条纪律在 03/04 建起来了
（A3 的 claims 对照、A6 的客观轨），01/02 却完全没有等价物。

⚠️ 判据落在**产出区**而不是模板区，这是刻意的：

本轮同时修掉了 01 prompt 里「用 write_file 回填 workspace/... 模板」那条
指令 —— 硬层对 `workspace/**` 的 write 一律 deny，那条指令成功率恒为 0
（见 `test_prompt_tool_contract.py`）。既然 agent 不该也不能改阶段文件，
模板区的 `___` 就会一直在，拿它当判据是错的。

真正能证明「这一阶段确实产出了东西」的是 sw 自己写下的围栏区
（`<!-- sw:ai-output:start <nonce> -->`）—— 内容由 agent 提供、边界由
harness 控制、nonce 不可预测。所以判据是：**围栏区必须存在且有实质内容**。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS
from sw_lib.workflow import stage_state

ROOT = Path(__file__).resolve().parents[3]

STAGE_FILES = {
    "01-brainstorming": "01-brainstorming.md",
    "02-planning": "02-planning.md",
}


def _run_hook(stage, name):
    return subprocess.run(
        ["bash", str(HOOKS_DIR / f"check_{stage}.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )


@pytest.fixture
def task():
    """建任务：拷真实模板 + 写已签署的 gate。"""
    created = []

    def _make(name, stage, output=None):
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        tpl = ROOT / "templates" / STAGE_FILES[stage]
        body = tpl.read_text(encoding="utf-8")

        gate_items = {
            "01-brainstorming": [
                {"key": "design_approved", "label": "Design approved",
                 "checked": True},
                {"key": "ready_for_planning", "label": "Ready for Planning",
                 "checked": True},
            ],
            "02-planning": [
                {"key": "tests_pass", "label": "Tests pass", "checked": True},
                {"key": "no_regression_risk", "label": "No regression risk",
                 "checked": True},
            ],
        }[stage]

        state = {
            "id": name, "stage": stage, "stage_idx": 0 if "01" in stage else 1,
            "stage_status": "running", "target_dir": f"repo/{name}",
            "stages": {stage: {"gate": {
                "items": gate_items,
                "signed_by": "user", "signed_at": "2026-08-24T00:00:00",
            }}},
        }

        if output is not None:
            nonce = "abcd1234"
            state["stages"][stage]["output_nonce"] = nonce
            body = body.rstrip() + "\n\n" + stage_state.render_output_block(
                nonce, output)

        (d / STAGE_FILES[stage]).write_text(body, encoding="utf-8")
        (d / ".state").write_text(json.dumps(state), encoding="utf-8")
        created.append(d)
        return name

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


# ── 前提自检 ──

@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_template_placeholders_are_present_in_fixture(task, stage):
    """前提：真实模板里确实带 `___` 占位符。

    若模板改成不带占位符，下面「有产出即通过」的判据仍成立，但
    「无产出被拦」那条的现场就变了 —— 显式守住前提，避免判据空转。
    """
    name = task(f"early-tpl-{stage[:2]}", stage)
    body = (TASKS / name / STAGE_FILES[stage]).read_text(encoding="utf-8")
    assert "___" in body, f"{stage} 模板不含 `___`，判据前提已变"


# ── 无产出必须被拦 ──

@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_gate_rejects_stage_without_any_output(task, stage):
    """签了 gate 但**从未产出**的阶段不得过闸。

    这正是 helloworld 的现场：gate 两项 `[x]`、硬校验 ✅ 通过、阶段推进，
    而阶段文件里一个字的实质内容都没有。
    """
    name = task(f"early-noout-{stage[:2]}", stage, output=None)
    r = _run_hook(stage, name)

    assert r.returncode != 0, (
        f"{stage}: 完全没有产出的阶段过闸了 —— "
        f"门禁只看签署不看内容:\n{r.stdout}")


@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_gate_rejects_empty_output_region(task, stage):
    """产出区存在但为空（agent 一句话没说）同样不得过闸。

    空转的代价必须在本阶段暴露，而不是推迟到下游（A2 的 has_code_output
    是同一条纪律：任务 T3 的空转被推迟到 04 才发现，然后来回返工）。
    """
    name = task(f"early-emptyout-{stage[:2]}", stage, output="   \n\n  ")
    r = _run_hook(stage, name)

    assert r.returncode != 0, (
        f"{stage}: 产出区为空却过闸了:\n{r.stdout}")


@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_gate_rejects_output_that_is_only_placeholders(task, stage):
    """产出区里只有占位符，等于没产出。

    模型有时会把模板原样抄一遍。抄回来的 `___` 不是内容。
    """
    name = task(f"early-phout-{stage[:2]}", stage,
                output="## Ambiguity Score\nScore: ___ | Goal: ___\n"
                       "- **Proposal**: ___\n- **Impact**: ___\n")
    r = _run_hook(stage, name)

    assert r.returncode != 0, (
        f"{stage}: 产出区全是占位符却过闸了:\n{r.stdout}")


# ── 有实质产出必须放行 ──

@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_gate_accepts_substantive_output(task, stage):
    """有实质内容时必须放行 —— 判据不能把正常流程一起堵死。

    用 helloworld 那次真实的 01 产出作为样本（它内容是齐的，
    只是没回填模板区）。

    ⚠️ 原样本里 agent 写的是「**歧义分数：** 7/10（目标<8，已达标）」——
    它把阈值方向算反了：7 低于 8 是**未**达标。歧义分数接线之后
    （`hook-01-01`，见 `test_ambiguity_gate.py`）这份样本被正确拦下。
    这里把分数改到达标值，**不是**降阈值 —— 本用例要测的是「有实质产出就放行」，
    而 7 分那份样本恰恰是「产出齐全但歧义未消除」，属于另一条判据的辖区。
    """
    real = (
        "## 01-Brainstorming 阶段完成\n\n"
        "已完成需求澄清和分析：\n\n"
        "**需求澄清结果：**\n"
        "- 功能范围：基础预订管理（机票、酒店、火车票预订 + 审批 + 报销）\n"
        "- 技术栈：Python FastAPI\n"
        "- 部署环境：本地开发环境\n\n"
        "**歧义分数：** 9/10（阈值 8，已达标）\n\n"
        "**事前分析（Pre-mortem）：**\n"
        "1. 需求理解偏差 → 预防：详细澄清和原型验证\n"
        "2. 数据库设计不合理 → 预防：提前设计数据模型\n\n"
        "**架构决策（ADR）：**\n"
        "- 使用 Python FastAPI 构建基础商旅预订管理平台\n"
        "- 理由：开发速度快，适合 API 服务\n"
    )
    name = task(f"early-good-{stage[:2]}", stage, output=real)
    r = _run_hook(stage, name)

    assert r.returncode == 0, (
        f"{stage}: 有实质产出却被拦下:\n{r.stdout}\n{r.stderr}")


# ── 既有契约不得回退 ──

@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_unsigned_gate_still_blocks(task, stage):
    """未签署仍必须拦住 —— 新判据是叠加，不是替换。"""
    name = task(f"early-unsigned-{stage[:2]}", stage, output="实质内容若干，足够长。")
    d = TASKS / name
    st = json.loads((d / ".state").read_text(encoding="utf-8"))
    st["stages"][stage]["gate"]["signed_by"] = None
    st["stages"][stage]["gate"]["signed_at"] = None
    for item in st["stages"][stage]["gate"]["items"]:
        item["checked"] = False
    (d / ".state").write_text(json.dumps(st), encoding="utf-8")

    r = _run_hook(stage, name)
    assert r.returncode != 0, f"{stage}: 未签署的 gate 过闸了:\n{r.stdout}"


@pytest.mark.parametrize("stage", sorted(STAGE_FILES))
def test_missing_stage_file_still_blocks(task, stage):
    """阶段文件不存在仍必须拦住。"""
    name = task(f"early-nofile-{stage[:2]}", stage, output="实质内容若干。")
    (TASKS / name / STAGE_FILES[stage]).unlink()

    r = _run_hook(stage, name)
    assert r.returncode != 0, f"{stage}: 阶段文件缺失却过闸:\n{r.stdout}"
