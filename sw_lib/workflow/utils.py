"""sw_lib.workflow.utils — Shared workflow utilities for the workflow architecture.

Extracted from legacy engine.py to enable modularity.
"""

import re
from typing import Optional

from ..core.config import TASKS, STAGES

# AI Output 区标题。产出区的复选框是 agent 叙述，不参与任何判定。
_AI_OUTPUT_HEADING = "## 🤖 AI Output"

def parse_route_from_ai_output(content: str) -> Optional[str]:
    """Parse Route decision from AI Output section in 04-review.md."""
    marker = "## 🤖 AI Output"
    idx = content.find(marker)
    if idx < 0:
        return None
        
    ai_section = content[idx + len(marker):]
    
    # Priority 1: backticks `05-Archive`
    m = re.search(r'`\s*(05-Archive|04-Review|03-Coding|02-Planning|01-Brainstorming)\s*`', ai_section, re.I)
    if m:
        return _normalize_stage_case(m.group(1))

    # Priority 2: natural language markers
    m = re.search(
        r'(?:建议路由|Route|路由|路由决策|应返工)\s*[：:→>为至]\s*\*{0,2}\s*'
        r'(05-Archive|04-Review|03-Coding|02-Planning|01-Brainstorming)',
        ai_section, re.I
    )
    if m:
        return _normalize_stage_case(m.group(1))
        
    return None


def extract_evidence_table(task_name: str) -> Optional[str]:
    """从 04-review.md 提取 Reroute Evidence 的 Markdown 表格。"""
    review_path = TASKS / task_name / "04-review.md"
    if not review_path.exists():
        return None
    content = review_path.read_text(encoding="utf-8", errors="replace")

    # 查找 "### Reroute Evidence" 章节后的表格
    lines = content.splitlines()
    in_evidence = False
    table_lines = []
    for line in lines:
        if line.strip().startswith("### Reroute Evidence"):
            in_evidence = True
            continue
        if in_evidence:
            # 表格结束条件：空行 或 下一个 ## 标题
            if not line.strip() or line.startswith("##"):
                break
            # 只收集表格行（| 开头）
            if line.strip().startswith("|"):
                table_lines.append(line)

    if not table_lines:
        return None

    # 至少需要表头 + 分隔线 + 1 行数据（且数据行不能全是占位符 `___`）
    if len(table_lines) < 3:
        return None

    data_rows = table_lines[2:]  # 跳过表头和分隔线
    has_real_data = any("___" not in row for row in data_rows)
    if not has_real_data:
        return None

    return "\n".join(table_lines)


def inject_reroute_context(task_name: str, target_stage: str):
    """将 review 的 Evidence 表注入到目标 stage 模板顶部。"""
    review_path = TASKS / task_name / "04-review.md"
    target_path = TASKS / task_name / f"{target_stage}.md"
    if not review_path.exists() or not target_path.exists():
        return

    evidence = extract_evidence_table(task_name)
    if not evidence:
        return

    inject_block = (
        "> 🔄 **返工上下文（来自 04-Review）**\n"
        f"{evidence}\n"
        "> 请优先修复上述问题。\n\n"
    )

    original = target_path.read_text(encoding="utf-8", errors="replace")

    # 清理旧的返工上下文 block（两个标记之间）
    cleaned = _remove_old_reroute_blocks(original)

    # 注入到文件头部
    target_path.write_text(inject_block + cleaned, encoding="utf-8")


def _remove_old_reroute_blocks(content: str) -> str:
    """移除之前注入的所有返工上下文 block。"""
    lines = content.splitlines()
    result = []
    skip_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("> 🔄 **返工上下文（来自 04-Review）**"):
            skip_block = True
            continue
        if skip_block:
            if stripped == "" or stripped.startswith(">") or stripped.startswith("|"):
                continue
            else:
                skip_block = False
        if not skip_block:
            result.append(line)
    return "\n".join(result)


# agent 表达「已选定某个选项」的两种写法（除了直接勾 [x]）：
#   - [ ] B: 轻量询价模式 — 不含在线支付 (Chosen)
#   - **Chosen**: 轻量询价模式
# 经 ask_user 与用户对齐后 agent 常用这两种记法而不改复选框，此时选项组
# 已有结论，不应再要求用户去勾 —— 那个校验用户无法完成。
_CHOSEN_SUFFIX_RE = re.compile(r'\(\s*chosen\s*\)\s*$', re.IGNORECASE)
_CHOSEN_FIELD_RE = re.compile(r'^\s*[-*]\s*\*{0,2}chosen\*{0,2}\s*[:：]\s*(.+)$',
                              re.IGNORECASE)


# 表示「尚未决定」的填充值。agent 有时会把 Chosen 填成这些占位而非真实选项，
# 当成已选择会让空模板通过门禁。
_UNDECIDED_VALUES = {"n/a", "na", "tbd", "todo", "待定", "未定", "无", "-", "?", "？"}


def _marks_chosen_option(line: str) -> bool:
    """该行是否以 ``(Chosen)`` 后缀标记「这个选项被选中」。"""
    return bool(_CHOSEN_SUFFIX_RE.search(line.rstrip()))


def _resolves_choice_group(line: str) -> bool:
    """该行是否是填写过的 ``- **Chosen**: xxx`` 字段。

    未填写的占位符（``___`` / 空）与「待定」类值都不算，
    否则空模板或未决状态会被判为已完成。
    """
    m = _CHOSEN_FIELD_RE.match(line)
    if not m:
        return False
    val = m.group(1).strip().strip('*').strip()
    if not val or val.strip('_') == "":
        return False
    return val.lower() not in _UNDECIDED_VALUES


def check_stage_compliance(task_name: str, stage: str, stage_idx: int) -> tuple[list[str], list[str]]:
    """Check if a stage is complete. Returns (done_items, todo_items).

    职责一分为二：

    * **门禁判定**（Gate 签署、Route 决策）委托 ``stage_state``，读 JSON。
      agent 在 Markdown 里写什么都不影响结果 —— 这是根治整类解析歧义 bug
      的关键（见 docs/design-json-state-source.md）。
    * **内容检查**（选项组是否已拍板）优先读 ``.state`` 里的拍板记录，
      Markdown 只作为兼容回退。用户经 ask_user 做的选择是状态，不该由
      agent 有没有回写记法来决定能否过闸（任务 T1）。

    选项组（``- [ ] A: ...``）是要用户拍板的备选方案，没结论必须拦住；
    它不属于 Gate，但同样是推进的前置条件。
    """
    from . import stage_state as ss

    done: list[str] = []
    todo: list[str] = []
    task_dir = TASKS / task_name
    tpl = task_dir / f"{stage}.md"

    if not tpl.exists():
        todo.append(f"模板文件不存在: {stage}.md")
        return done, todo

    content = tpl.read_text(encoding="utf-8", errors="replace")

    # ── 1. 门禁：纯 JSON 判定 ──
    gate = ss.read_gate(task_name, stage)
    if not gate.exists:
        todo.append(f"{stage}.md — 模板未定义 ## Gate 项（请检查 templates/{stage}.md）")
    elif gate.signed:
        done.append(f"{stage}.md — 全部 {len(gate.items)} 项门禁已签署")
    else:
        todo.append(f"{stage}.md — {len(gate.pending)} 个门禁项待签署")

    # ── 2. 选项组：先看 .state 的拍板记录，Markdown 仅作兼容回退 ──
    # 用户在 TUI 里选过的每个问题都会落进 decisions。Markdown 里那些还是
    # `[ ]` 的选项组，只要已有对应数量的拍板记录，就说明决定已经做过了，
    # 只是 agent 没回写 —— 那是产出美观问题，不是推进的阻塞条件。
    unresolved = _count_unresolved_choice_groups(content)
    if unresolved:
        unresolved = max(0, unresolved - ss.count_decisions(task_name, stage))
    if unresolved:
        todo.append(f"{stage}.md — {unresolved} 个选项组尚未拍板")

    # ── 3. Route：04-review 专属，同样读 JSON ──
    if stage == "04-review":
        route_val = ss.read_route(task_name)
        if route_val:
            done.append(f"**Route**: {route_val}")
        else:
            todo.append("**Route** 决策尚未填写")

    return done, todo


def _count_unresolved_choice_groups(content: str) -> int:
    """统计未拍板的选项组数量。

    选项组形如 ``- [ ] A: 方案`` 连续若干行，由 ``- **Chosen**: xxx`` 或
    某一项被勾选/标注 ``(Chosen)`` 收尾。AI Output 区跳过：那里的复选框是
    agent 的叙述，不是待用户决策的选项。
    """
    unresolved = 0
    in_choice_group = False
    group_has_checked = False
    skip_ai_output = False

    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith(_AI_OUTPUT_HEADING):
            skip_ai_output = True
            continue
        if skip_ai_output:
            # AI Output 区一直延伸到下一个二级标题
            if stripped.startswith("## "):
                skip_ai_output = False
            else:
                continue

        cbs = re.findall(r'\[([ xX])\]', line)
        if not cbs:
            if in_choice_group and _resolves_choice_group(line):
                group_has_checked = True
                in_choice_group = False
                continue
            if in_choice_group and stripped != "":
                if not group_has_checked:
                    unresolved += 1
                in_choice_group = False
            continue

        if re.match(r'^\s*[-*]\s+\[[ xX]\]\s*[A-Z\d]+[.\u3001:)]\s', line):
            if not in_choice_group:
                in_choice_group = True
                group_has_checked = False
            if cbs[0].lower() == 'x' or _marks_chosen_option(line):
                group_has_checked = True
        else:
            if in_choice_group and not group_has_checked:
                unresolved += 1
            in_choice_group = False

    if in_choice_group and not group_has_checked:
        unresolved += 1
    return unresolved


def _normalize_stage_case(route: str) -> str:
    """Normalize stage string to match STAGES list (lowercase)."""
    route_lower = route.lower()
    for s in STAGES:
        if s.lower() == route_lower:
            return s
    return route_lower
