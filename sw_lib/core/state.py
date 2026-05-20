"""
sw_lib.state — 任务状态持久化及阶段校验。

主要职责：
1. 维护任务的 .state 文件 (JSON 格式)，每个任务独立管理自身状态。
2. 通过扫描 .state 文件查找当前活跃任务。
3. 封装对任务状态的读取、写入及自动迁移逻辑。
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from .config import ROOT, TASKS, STAGES, STAGE_NAMES


def state_path(name: str) -> Path:
    """获取任务 .state 文件的绝对路径"""
    return TASKS / name / ".state"


def read_state(name: str) -> Dict[str, Any]:
    """
    读取并解析任务的 .state 文件。
    
    支持自动迁移：如果发现文件是旧的 'key: value' 文本格式，会解析并自动保存为新的 JSON 格式。
    
    Args:
        name: 任务名称
        
    Returns:
        状态字典。如果文件不存在，返回空字典。
    """
    sf = state_path(name)
    if not sf.exists():
        return {}
    
    try:
        content = sf.read_text(encoding="utf-8").strip()
    except Exception:
        return {}

    if not content:
        return {}

    state: Dict[str, Any] = {}
    # 判断是否为 JSON 格式
    is_json = content.startswith("{") and content.endswith("}")
    
    if is_json:
        try:
            state = json.loads(content)
        except json.JSONDecodeError:
            is_json = False

    if not is_json:
        # 解析旧格式 (key: value) 并自动迁移
        for line in content.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                state[key.strip()] = val.strip().strip('"')
        
        if state:
            write_state(name, state)

    # 核心字段类型强制转换与默认值填充
    if "stage_idx" in state:
        try:
            state["stage_idx"] = int(state["stage_idx"])
        except (ValueError, TypeError):
            state["stage_idx"] = 0
    else:
        state["stage_idx"] = 0
        
    if "stage" not in state:
        state["stage"] = STAGES[0]
    else:
        state["stage"] = state["stage"].strip('"')
        
    return state


def write_state(name: str, data: Dict[str, Any]):
    """
    将状态字典以 JSON 格式持久化到任务的 .state 文件。
    
    Args:
        name: 任务名称
        data: 状态字典
    """
    sf = state_path(name)
    sf.parent.mkdir(parents=True, exist_ok=True)
    
    # 关键字段预处理，防止写入非法类型
    if "stage_idx" in data:
        try:
            data["stage_idx"] = int(data["stage_idx"])
        except (ValueError, TypeError):
            pass

    with open(sf, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _find_active_task_from_states() -> Optional[str]:
    """扫描 workspace/tasks/*/.state 文件，返回最近更新的活跃任务名"""
    if not TASKS.is_dir():
        return None
    candidates = []
    for d in TASKS.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        sf = d / ".state"
        if not sf.exists():
            continue
        try:
            st = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if st.get("stage_status") in ("running", "pending", "waiting"):
            candidates.append((st.get("updated_at", ""), d.name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def get_active_from_status() -> Optional[str]:
    """从 .state 文件中查找当前活跃任务（替代旧的 STATUS.json 机制）"""
    return _find_active_task_from_states()


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

        lines = content.splitlines()
        unchecked = 0
        checked = 0
        
        in_choice_group = False
        choice_group_has_checked = False
        
        for line in lines:
            cbs = re.findall(r'\[([ xX])\]', line)
            if not cbs:
                if in_choice_group and line.strip() != "":
                    # Choice 组在中间结束，空组不计入 unchecked
                    in_choice_group = False
                continue
                
            is_choice_opt = bool(re.match(r'^\s*[-*]\s+\[[ xX]\]\s*[A-Z\d]+[.、:)]\s', line))
            
            if is_choice_opt:
                if not in_choice_group:
                    in_choice_group = True
                    choice_group_has_checked = False
                
                if cbs[0].lower() == 'x':
                    choice_group_has_checked = True
                    checked += 1
            else:
                # Choice 组结束：若组内无选中项，不计入 unchecked（空选择组是有效状态）
                in_choice_group = False
                
                for cb in cbs:
                    if cb.lower() == 'x':
                        checked += 1
                    else:
                        unchecked += 1

        # Choice 组在文件末尾结束：空组中性，不计数

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
            if re.search(r'\[x\]\s*Design approved', content, re.IGNORECASE):
                done.append("设计批准已勾选 [x]")
            elif '[ ] Design approved' in content:
                todo.append("设计尚未获得批准（模板中 'Design approved' 尚未勾选）")
            else:
                done.append("设计批准已填写")

        # elif stage == "03-coding":
        #     try:
        #         result = subprocess.run(
        #             ["git", "-C", str(ROOT), "log", "--oneline", "-5"],
        #             capture_output=True, text=True)
        #         if result.stdout.strip():
        #             done.append(f"最近 git 提交:\n    {result.stdout.strip()[:200]}")
        #     except Exception:
        #         pass

        return done, todo
