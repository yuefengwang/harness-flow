"""
sw_lib.state — 任务状态持久化及汇总管理。

主要职责：
1. 维护任务的 .state 文件 (JSON 格式)，每个任务独立管理自身状态。
2. 通过扫描 STATUS.json 查找当前活跃任务。
3. 封装对任务状态的读取、写入及汇总逻辑。
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any

from .config import TASKS, STATUS, STAGES


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

    # health_config 默认值自动填充（向后兼容旧 .state 文件）
    if "health_config" not in state:
        state["health_config"] = {
            "enabled": True,
            "check_interval": 10,
            "failure_threshold": 3,
            "auto_redeploy": False,
            "max_redeploys": 5,
            "redeploy_window_sec": 300,
        }

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


# ── STATUS.json 全局任务汇总 ──

def _load_task_summary() -> Dict[str, Any]:
    """加载 STATUS.json 全局任务汇总"""
    if STATUS.exists():
        try:
            return json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"project": "", "updated_at": "", "tasks": {}}


def _save_task_summary(data: Dict[str, Any]):
    """保存 STATUS.json"""
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now_iso()
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def upsert_task_summary(name: str, **fields):
    """在 STATUS.json 中创建或更新一个 task 条目"""
    data = _load_task_summary()
    entry = data["tasks"].get(name, {})
    entry["id"] = name
    entry.update(fields)
    entry["updated_at"] = _now_iso()
    if "created_at" not in entry:
        entry["created_at"] = _now_iso()
    data["tasks"][name] = entry
    _save_task_summary(data)


def remove_task_summary(name: str):
    """从 STATUS.json 中移除一个 task 条目"""
    data = _load_task_summary()
    data["tasks"].pop(name, None)
    _save_task_summary(data)


def get_active_from_status() -> Optional[str]:
    """从 STATUS.json 的 tasks 中查找当前活跃任务。

    STATUS.json 是缓存，任务目录被外部删除（测试残留、手工 rm）后条目会留下，
    此时必须跳过，否则调用方拿到一个读不出 .state 的名字就直接报错退出。
    """
    data = _load_task_summary()
    tasks = data.get("tasks", {})
    candidates = []
    for name, entry in tasks.items():
        if entry.get("stage_status") not in ("running", "pending", "waiting"):
            continue
        if not state_path(name).exists():
            continue
        candidates.append((entry.get("updated_at", ""), name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_context_from_cwd(start_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """从当前目录向上查找 .sw-context 标记文件，返回项目上下文

    返回格式: {"project": "...", "target_dir": "...", "type": "...", "created": "..."}
    未找到时返回 None。
    """
    cwd = Path(start_dir).resolve() if start_dir else Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        marker = parent / ".sw-context"
        if marker.exists():
            try:
                return json.loads(marker.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None
