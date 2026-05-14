"""sw_lib.state — state file read/write, STATUS.md management, StageValidator"""

import re
import subprocess
from pathlib import Path
from typing import Optional

from .config import ROOT, TASKS, TPLS, STATUS, STAGES, STAGE_NAMES


def state_path(name: str) -> Path:
    return TASKS / name / ".state"


def read_state(name: str) -> dict:
    """读取 .state 文件为字典"""
    sf = state_path(name)
    if not sf.exists():
        return {}
    state = {}
    for line in sf.read_text().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            state[key.strip()] = val.strip().strip('"')
    return state


def write_state(name: str, data: dict):
    """写入 .state 文件"""
    sf = state_path(name)
    sf.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}: {v}" for k, v in data.items()]
    sf.write_text("\n".join(lines) + "\n")


def update_status_md_active(name: str):
    """更新 STATUS.md 中的活动任务和当前阶段"""
    content = STATUS.read_text()
    content = re.sub(r'^(- \*\*活动任务:\*\*)\s*.*', rf'\1 {name}', content, flags=re.MULTILINE)
    content = re.sub(r'^(- \*\*当前阶段:\*\*)\s*.*', r'\1 01-头脑风暴', content, flags=re.MULTILINE)
    STATUS.write_text(content)


def clear_status_md_active(name: str):
    """清除 STATUS.md 中指定任务的活动引用"""
    content = STATUS.read_text()
    content = re.sub(r'^(- \*\*活动任务:\*\*)\s*.*', r'\1 无', content, flags=re.MULTILINE)
    content = re.sub(r'^(- \*\*当前阶段:\*\*)\s*.*', r'\1 N/A', content, flags=re.MULTILINE)
    STATUS.write_text(content)


def update_status_md_stage(idx: int):
    """推进 STATUS.md 中的当前阶段"""
    content = STATUS.read_text()
    new_stage = f"0{idx+1}-{STAGE_NAMES[idx]}"
    content = re.sub(r'^(- \*\*当前阶段:\*\*)\s*.*', rf'\1 {new_stage}', content, flags=re.MULTILINE)
    STATUS.write_text(content)


def get_active_from_status() -> Optional[str]:
    """从 STATUS.md 读取当前活动任务名"""
    content = STATUS.read_text()
    m = re.search(r'\*\*活动任务:\*\*\s*(.+?)(?:\*\*)?$', content, re.MULTILINE)
    if m:
        name = m.group(1).strip().rstrip("*")
        if name and name != "无":
            return name
    return None


class StageValidator:
    """阶段完成校验器"""

    @staticmethod
    def check(task_dir: Path, stage: str, stage_idx: int) -> tuple[list[str], list[str]]:
        """返回 (done_items, todo_items)"""
        done = []
        todo = []
        tpl = task_dir / f"{stage}.md"

        if not tpl.exists():
            todo.append(f"模板文件不存在: {stage}.md")
            return done, todo

        content = tpl.read_text()

        unchecked = content.count("[ ] ")
        checked = content.count("[x] ") + content.count("[X] ")

        if unchecked == 0 and checked > 0:
            done.append(f"{stage}.md — 全部 {checked} 项已勾选")
        elif unchecked > 0:
            todo.append(f"{stage}.md — {unchecked} 个待填项未完成")
        elif unchecked == 0 and checked == 0:
            tpl_orig = TPLS / f"{stage}.md"
            if tpl_orig.exists() and content != tpl_orig.read_text():
                done.append(f"{stage}.md — 内容已修改（非初始模板）")
            else:
                todo.append(f"{stage}.md — 尚未填写（与初始模板一致）")

        if stage == "01-brainstorming":
            if re.search(r'\[x\]\s*设计是否已批准', content, re.IGNORECASE):
                done.append("设计批准已勾选 [x]")
            elif '(是/否)' in content or '（是/否）' in content:
                todo.append("设计尚未获得批准（模板中仍为 '(是/否)'，需改为'是'）")
            else:
                done.append("设计批准已填写")

        elif stage == "03-coding":
            try:
                result = subprocess.run(
                    ["git", "-C", str(ROOT), "log", "--oneline", "-5"],
                    capture_output=True, text=True)
                if result.stdout.strip():
                    done.append(f"最近 git 提交:\n    {result.stdout.strip()[:200]}")
            except Exception:
                pass

        return done, todo
