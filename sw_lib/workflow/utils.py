"""sw_lib.workflow.utils — Shared workflow utilities for the workflow architecture.

Extracted from legacy engine.py to enable modularity.
"""

import re
from pathlib import Path
from typing import Optional

from ..core.config import TASKS, STAGES

def auto_check_gate(task_name: str, stage: str):
    """Automatically check all checkboxes in the ## Gate section of a stage file.
    
    Used when advancing manually to ensure the gate logic doesn't block progression.
    Also handles 04-review specific logic like backfilling Route fields.
    """
    tpl = TASKS / task_name / f"{stage}.md"
    if not tpl.exists():
        return
        
    content = tpl.read_text(encoding="utf-8", errors="replace")
    in_gate = False
    lines = content.splitlines()
    
    for i, line in enumerate(lines):
        if line.strip().startswith("## Gate"):
            in_gate = True
        elif in_gate and line.strip().startswith("##"):
            break
        elif in_gate and re.match(r"^\s*- \[ \]", line):
            lines[i] = line.replace("[ ]", "[x]", 1)
            
    content = "\n".join(lines) + "\n"

    # 04-review: Auto-backfill Route field if present and empty
    if stage == "04-review" and "`___`" in content:
        route = parse_route_from_ai_output(content)
        if route:
            content = content.replace("- **Route**: `___`", f"- **Route**: `{route}`", 1)
            # 如果是返工路由，同时自动回填 Reroute Evidence 表
            if route != "05-archive":
                content = _auto_fill_evidence_from_ai_output(content)

    tpl.write_text(content, encoding="utf-8")


def _auto_fill_evidence_from_ai_output(content: str) -> str:
    """从 AI Output 的审查结论表格中提取未通过的项，自动填入 Reroute Evidence 表。"""
    # 找到 Reroute Evidence 段
    ev_start = content.find("### Reroute Evidence")
    if ev_start < 0:
        return content
    # 找到下一个 ## 标题作为结束
    next_section = content.find("\n##", ev_start + 10)
    ev_end = next_section if next_section > ev_start else len(content)
    ev_section = content[ev_start:ev_end]

    # 检查数据行是否全为占位符
    data_lines = [l for l in ev_section.splitlines()
                  if l.strip().startswith("|") and l.strip().count("|") >= 5]
    data_lines = data_lines[2:]  # 跳过表头和分隔行
    has_real = any("___" not in l for l in data_lines)
    if has_real:
        return content  # 已有真实数据，不覆盖

    # 从 AI Output 提取未通过的审查项
    marker = "## 🤖 AI Output"
    ai_idx = content.find(marker)
    if ai_idx < 0:
        return content
    ai_section = content[ai_idx + len(marker):]
    # 截断到下一个 H2 标题（但排除 ### 子标题）
    ai_end = ai_section.find("\n## ")
    if ai_end > 0:
        ai_section = ai_section[:ai_end]
    elif ai_section.startswith("\n## "):
        ai_section = ""

    issues: list = []
    saw_table_header = False
    # 识别形如 | 门禁 | 状态 | 或 | 项目 | 结论 | 的审查表格
    for line in ai_section.splitlines():
        stripped = line.strip()
        if "|" not in stripped:
            continue
        # 检测表头行
        if not saw_table_header and ("门禁" in stripped or "状态" in stripped
                                      or "项目" in stripped or "Gate" in stripped):
            saw_table_header = True
            continue
        if stripped.startswith("|---"):
            continue
        if not saw_table_header:
            continue
        # 数据行：找 ⚠️ ❌ ⛔ 或非 ✅ 的状态
        if any(sym in stripped for sym in ('⚠️', '❌', '⛔', '⚠', '⛔')):
            parts = [p.strip() for p in stripped.split("|")]
            if len(parts) >= 3:
                gate_name = parts[1]
                status = parts[2] if len(parts) > 2 else ""
                issues.append((gate_name, status))

    if not issues:
        # 无明确问题行 → 不做任何改动
        return content

    # 用第一个问题构造 evidence 行
    gate_name, note = issues[0]
    row = f"| 1 | {gate_name} | medium | coding | 审查发现: {note} |"

    # 替换第一个占位符数据行 (| 1 | ___ | ...)
    pattern = r'(\|\s*)1(\s*\|\s*)___(\s*\|.*)'
    if re.search(pattern, content):
        content = re.sub(pattern, row, content, count=1)
    else:
        # 后备：在表头之后插入新行
        ev_idx = content.find("|---|------|---------|---------|-------------|")
        if ev_idx > 0:
            insert_pos = content.index("\n", ev_idx) + 1
            content = content[:insert_pos] + row + "\n" + content[insert_pos:]

    return content



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


def parse_route_field(task_name: str) -> Optional[str]:
    """Extract **Route**: `XXX` field from 04-review.md. 
    Only returns if it matches a valid stage.
    """
    path = TASKS / task_name / "04-review.md"
    if not path.exists():
        return None
        
    content = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'\*\*Route\*\*:\s*`([^`]+)`', content)
    if m:
        route = m.group(1).strip()
        # Only return if it's a known stage
        route_lower = route.lower()
        if any(s.lower() == route_lower for s in STAGES):
            return _normalize_stage_case(route)
            
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


def _reset_gate_checkboxes(task_name: str, stage: str):
    """Reset all checkboxes and stage-specific placeholders (like Route) for a stage."""
    tpl = TASKS / task_name / f"{stage}.md"
    if not tpl.exists():
        return
    content = tpl.read_text(encoding="utf-8", errors="replace")

    in_gate = False
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("## Gate"):
            in_gate = True
        elif in_gate and line.strip().startswith("##"):
            break

        if in_gate:
            lines[i] = line.replace("[x]", "[ ]")

    content = "\n".join(lines) + "\n"
    
    # 04-review: reset Route field
    if stage == "04-review":
        content = re.sub(r'(\*\*Route\*\*:\s*)`[^`]+`', r'\1`___`', content)

    tpl.write_text(content, encoding="utf-8")


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


def check_stage_compliance(task_name: str, stage: str, stage_idx: int) -> tuple[list[str], list[str]]:
    """Check if a stage is complete. Returns (done_items, todo_items).
    
    Migrated from legacy StageValidator.
    """
    from ..core.config import TPLS, STAGES, STAGE_NAMES
    done = []
    todo = []
    task_dir = TASKS / task_name
    tpl = task_dir / f"{stage}.md"

    if not tpl.exists():
        todo.append(f"模板文件不存在: {stage}.md")
        return done, todo

    content = tpl.read_text(encoding="utf-8", errors="replace")

    lines = content.splitlines()
    unchecked = 0
    checked = 0
    
    in_choice_group = False
    group_has_checked = False
    has_checkboxes = False
    skip_ai_output = False  # Skip AI Output region until ## Gate
    
    for line in lines:
        stripped = line.strip()
        
        # Skip AI Output region (## 🤖 AI Output → ## Gate)
        if stripped.startswith("## 🤖 AI Output"):
            skip_ai_output = True
            continue
        if skip_ai_output:
            if stripped.startswith("## Gate"):
                skip_ai_output = False
            else:
                continue  # Skip AI output content
        
        cbs = re.findall(r'\[([ xX])\]', line)
        if not cbs:
            if in_choice_group and line.strip() != "":
                # Non-empty line with no checkbox ends a choice group
                if not group_has_checked:
                    unchecked += 1
                in_choice_group = False
            continue
            
        has_checkboxes = True
        is_choice_opt = bool(re.match(r'^\s*[-*]\s+\[[ xX]\]\s*[A-Z\d]+[.、:)]\s', line))
        
        if is_choice_opt:
            if not in_choice_group:
                in_choice_group = True
                group_has_checked = False
            
            if cbs[0].lower() == 'x':
                checked += 1
                group_has_checked = True
        else:
            if in_choice_group and not group_has_checked:
                unchecked += 1
            in_choice_group = False
            for cb in cbs:
                if cb.lower() == 'x':
                    checked += 1
                else:
                    unchecked += 1

    # Handle unclosed choice group at end of file
    if in_choice_group and not group_has_checked:
        unchecked += 1

    if not has_checkboxes:
        todo.append(f"{stage}.md — 缺少门禁选项 (无复选框)")

    if unchecked == 0 and checked > 0:
        done.append(f"{stage}.md — 全部 {checked} 项已勾选")
    elif unchecked > 0:
        todo.append(f"{stage}.md — {unchecked} 个待填项未完成")
    elif not has_checkboxes:
        pass # Already handled
    elif unchecked == 0 and checked == 0:
        tpl_orig = TPLS / f"{stage}.md"
        if tpl_orig.exists() and content != tpl_orig.read_text(encoding="utf-8", errors="replace"):
            done.append(f"{stage}.md — 内容已修改（非初始模板）")
        else:
            todo.append(f"{stage}.md — 尚未填写（与初始模板一致）")

    if stage == "01-brainstorming":
        if re.search(r'\[x\]\s*Design approved', content, re.IGNORECASE):
            done.append("设计批准已勾选 [x]")
        elif '[ ] Design approved' in content:
            todo.append("设计尚未获得批准（模板中 'Design approved' 尚未勾选）")
        else:
            done.append("设计批准已填写")

    elif stage == "04-review":
        route_val = parse_route_field(task_name)
        if route_val:
            done.append(f"**Route**: {route_val}")
        else:
            # Check if it's explicitly ___
            if re.search(r'\*\*Route\*\*:\s*`___`', content):
                todo.append("**Route** 字段尚未填写")
            elif re.search(r'\*\*Route\*\*:\s*`([^`]+)`', content):
                todo.append("缺少有效的 **Route** 决策")
            else:
                todo.append("缺少 **Route** 字段")

    return done, todo


def _normalize_stage_case(route: str) -> str:
    """Normalize stage string to match STAGES list (lowercase)."""
    route_lower = route.lower()
    for s in STAGES:
        if s.lower() == route_lower:
            return s
    return route_lower
