"""每个阶段的门禁都必须「有人能签、签完就过」。

现场 bug（任务 iiiii / wwww）：/advance 在 02/03/04/05 全部被
「N 个待填项未完成」拒绝，用户无从下手。根因有两层：

1. 校验器把 ``## Gate`` 之外的复选框也算进门禁。那些框是过程记录
   （03 的 Red-Green）、审查自检（04 的 Security）、清理待办
   （05 的 Cleanup）与下游任务（02 的 Task DAG），本阶段收尾时
   本就不该勾上 —— 用户无法代替 agent 判断它们是否做过。
2. 只有 01 有勾 Gate 的入口（用户按 [A] 批准）。02-05 的 Gate
   没有任何人签：``auto_check_gate`` 是在 ``advance()`` 里跑的，
   即校验通过之后，于是 /advance 要求的东西正是它随后才会补上的。

唯一必须继续拦住的例外是 01 的 A/B 备选方案：那是用户自己的决定。
"""
import shutil

import pytest

from sw_lib.core.config import STAGES, TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.utils import check_stage_compliance


@pytest.fixture
def task():
    name = "pytest-all-stages-gate"
    d = TASKS / name
    d.mkdir(parents=True, exist_ok=True)
    write_state(name, {"id": name, "stage": "01-brainstorming",
                       "stage_idx": 0, "stage_status": "running"})
    yield name, d
    shutil.rmtree(d, ignore_errors=True)


def _fill_placeholders(content: str) -> str:
    """把 ``___`` 占位符填掉，模拟 agent 回填正文。"""
    return content.replace("___", "已填写内容")


# 02 的 Task DAG、03 的 Red-Green、04 的 Security、05 的 Cleanup 都不是
# 用户能签的东西，勾选与否不该决定 /advance 能不能走。
@pytest.mark.parametrize("stage,idx", [(s, i) for i, s in enumerate(STAGES)])
def test_signed_gate_unblocks_every_stage(task, stage, idx):
    name, d = task
    tpl = (TPLS / f"{stage}.md").read_text(encoding="utf-8")
    content = _fill_placeholders(tpl)
    if stage == "01-brainstorming":
        # 01 的 A/B 是用户决定，必须真的选一个才算完成
        content = content.replace("- [ ] A:", "- [x] A:")
    (d / f"{stage}.md").write_text(content, encoding="utf-8")
    # 签署走 .state（不再改 Markdown）
    assert ss.sign_gate(name, stage) is True
    if stage == "04-review":
        assert ss.write_route(name, "05-Archive") is True

    _, todo = check_stage_compliance(name, stage, idx)

    assert todo == [], f"{stage}: Gate 已签署却仍被拦住，用户无从完成: {todo}"


@pytest.mark.parametrize("stage,idx", [(s, i) for i, s in enumerate(STAGES)])
def test_unsigned_gate_still_blocks_every_stage(task, stage, idx):
    """放宽 Gate 之外的复选框，不能顺带放过真正的 Gate 项。"""
    name, d = task
    tpl = (TPLS / f"{stage}.md").read_text(encoding="utf-8")
    content = _fill_placeholders(tpl)
    assert "## Gate" in content, f"{stage} 模板无 Gate 区"
    (d / f"{stage}.md").write_text(content, encoding="utf-8")
    # 故意不签 .state

    _, todo = check_stage_compliance(name, stage, idx)

    assert any("门禁项待签署" in t for t in todo), \
        f"{stage}: Gate 未签署却放行: {todo}"


def test_every_stage_template_has_a_gate():
    """没有 Gate 区的阶段会退回全文扫描，正文里任何复选框都变成门禁。

    05-archive 就是这样：Cleanup 的 3 个待办成了事实上的门禁，
    而归档分支的 /advance 只报「检测到 N 个未完成项」。
    """
    missing = [s for s in STAGES
               if "## Gate" not in (TPLS / f"{s}.md").read_text(encoding="utf-8")]
    assert missing == [], f"这些阶段模板缺少 ## Gate 区: {missing}"


def test_process_checkboxes_outside_gate_are_not_gate_items(task):
    """03 的 Red-Green 是 TDD 过程记录，agent 常只在正文叙述而不回勾。"""
    name, d = task
    (d / "03-coding.md").write_text(
        "# 03-Coding\n\n"
        "## Red-Green\n"
        "- [ ] **Red**: repro/test fails\n"
        "- [ ] **Green**: fix passes\n"
        "- **Verify cmd**: `pytest`\n\n"
        "## Gate\n"
        "- [x] Code builds & tests pass\n"
        "- [x] Commit msg follows Conventional Commits\n",
        encoding="utf-8")

    ss.sign_gate(name, "03-coding")
    _, todo = check_stage_compliance(name, "03-coding", 2)

    assert todo == [], f"Red-Green 过程记录被当成门禁项: {todo}"


def test_security_selfcheck_outside_gate_is_not_a_gate_item(task):
    name, d = task
    (d / "04-review.md").write_text(
        "# 04-Review\n\n"
        "## Security\n"
        "- [ ] No hardcoded secrets\n"
        "- [ ] Input validated (SQL/command injection)\n"
        "- [ ] Access control OK\n\n"
        "## Review Decision\n"
        "- **Route**: `05-Archive`\n\n"
        "## Gate\n"
        "- [x] Full build: `make`\n"
        "- [x] Lint/static analysis pass\n",
        encoding="utf-8")

    ss.sign_gate(name, "04-review")
    ss.write_route(name, "05-Archive")
    _, todo = check_stage_compliance(name, "04-review", 3)

    assert todo == [], f"Security 自检项被当成门禁项: {todo}"


def test_choice_group_outside_gate_still_blocks(task):
    """01 的 A/B 是用户决定，放宽 Gate 之外的框时绝不能连它一起放过。"""
    name, d = task
    (d / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n"
        "## Clarifying Questions (3)\n"
        "1. **Topic**: 目标用户\n"
        "   - [ ] A: C端消费者 — Pros/Cons\n"
        "   - [ ] B: B2B批发 — Pros/Cons\n"
        "   - **Chosen**: ___\n\n"
        "## Gate\n"
        "- [x] Design approved\n"
        "- [x] Ready for Planning\n",
        encoding="utf-8")
    ss.sign_gate(name, "01-brainstorming")

    _, todo = check_stage_compliance(name, "01-brainstorming", 0)

    assert any("选项组尚未拍板" in t for t in todo), \
        f"未做的用户决策被放过: {todo}"


# ── 旧任务：模板更新前创建的任务文件没有 Gate 区 ──

def test_legacy_stage_file_without_gate_gets_one(task):
    """模板更新前创建的任务文件缺 Gate 区，也必须能签署。

    现场情况：iiiii/05-archive.md 是加 Gate 之前拷的模板，只有 Cleanup 三个
    待办。既没有可签署的门禁项，也没人能勾 —— /advance 到归档阶段必然死锁。

    现在门禁项定义来自模板 + `.state`，与任务文件的形状无关，所以不再需要
    往 Markdown 里补区块（旧的 `ensure_gate_section`）。渲染只是顺带让人
    在文件里也能看到签署状态。
    """
    name, d = task
    legacy = ("# 05-Archive\n\n## Cleanup\n"
              "- [ ] Temp files removed\n"
              "- [ ] Workflow logs archived\n")
    (d / "05-archive.md").write_text(legacy, encoding="utf-8")

    # 签署入口直接可用，不依赖文件里有没有 Gate 区
    assert ss.sign_gate(name, "05-archive") is True
    _, todo = check_stage_compliance(name, "05-archive", 4)
    assert todo == [], f"签署后仍被拦: {todo}"

    # 渲染后文件里能看到已签署的门禁，且原有正文不被破坏
    assert ss.render_gate_section(name, "05-archive") is True
    content = (d / "05-archive.md").read_text(encoding="utf-8")
    assert "## Gate" in content, "Gate 区未渲染出来"
    assert "- [ ] Temp files removed" in content, "原有正文被破坏"
    gate_body = content[content.rfind("## Gate"):]
    assert "- [x]" in gate_body and "- [ ]" not in gate_body, gate_body


def test_render_does_not_duplicate_existing_gate(task):
    """已有 Gate 区的文件重复渲染不得产生第二个 Gate。"""
    name, d = task
    tpl = (TPLS / "03-coding.md").read_text(encoding="utf-8")
    (d / "03-coding.md").write_text(tpl, encoding="utf-8")

    ss.seed_gate(name, "03-coding")
    ss.render_gate_section(name, "03-coding")
    ss.render_gate_section(name, "03-coding")

    content = (d / "03-coding.md").read_text(encoding="utf-8")
    assert content.count("## Gate") == 1, "Gate 区被重复追加"


def test_markdown_without_gate_section_still_judged_by_state(task):
    """阶段文件里没有 Gate 区也不影响判定 —— 门禁项定义来自模板 + .state。

    旧实现会因为文件里找不到 ``## Gate`` 而退回全文扫描，把正文里任何复选框
    都变成门禁项。现在文件形状与判定彻底解耦：缺区只是显示问题。
    """
    name, d = task
    (d / "05-archive.md").write_text(
        "# 05-Archive\n\n## Summary\n- **Delivered**: x\n", encoding="utf-8")

    _, todo = check_stage_compliance(name, "05-archive", 4)
    assert any("门禁项待签署" in t for t in todo), \
        f"未签署却放行: {todo}"

    assert ss.sign_gate(name, "05-archive") is True
    _, todo = check_stage_compliance(name, "05-archive", 4)
    assert todo == [], f"签署后仍被拦: {todo}"
