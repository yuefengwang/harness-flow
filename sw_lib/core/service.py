"""
sw_lib.service — 任务管理核心业务逻辑 (TaskService)。

该模块将原本散落在 commands.py 中的业务逻辑收拢，提供统一的、
不依赖于 CLI 表现层的任务操作接口。
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from .config import ROOT,  TASKS, TPLS, STAGES, STAGE_NAMES, TRASH
from .state import (
    read_state, write_state, state_path,
    update_status_md_active, clear_status_md_active,
    update_status_md_stage, get_active_from_status,
    StageValidator
)
from .utils import now, sanitize_name, sw_log


class TaskError(Exception):
    """任务操作相关的业务异常"""
    pass


class TaskService:
    """
    封装 Harness-Flow 任务的全生命周期管理逻辑。
    """

    def create_task(self, name: str, task_type: str = "feature", 
                    session: str = "N/A", agent: str = "N/A", 
                    context: str = "", allow_trash_collision: bool = False) -> str:
        """
        创建一个新任务。
        
        Args:
            name: 任务原始名称
            task_type: 任务类型
            session: 会话 ID
            agent: 指定的 Agent 或角色
            context: 需求上下文文本
            allow_trash_collision: 是否允许同名任务在回收站中
            
        Returns:
            清理后的正式任务名称
        """
        clean_name = sanitize_name(name)
        if len(clean_name) < 2:
            raise TaskError("任务名太短，请至少使用 2 个字符")
        
        task_dir = TASKS / clean_name
        if task_dir.exists():
            raise TaskError(f"任务已存在: {clean_name}")
        
        if not allow_trash_collision and (TRASH / clean_name).exists():
            raise TaskError(f"同名任务已在回收站中: {clean_name}")

        # 1. 创建目录并初始化结构
        task_dir.mkdir(parents=True, exist_ok=True)
        for tpl in TPLS.glob("*.md"):
            shutil.copy2(tpl, task_dir / tpl.name)
        
        (task_dir / ".input").touch(exist_ok=True)
        
        if context:
            # 清理 surrogate 字符，防止 macOS Python 3.9 编码崩溃
            safe_context = context.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
            (task_dir / ".context").write_text(safe_context, encoding="utf-8")

        # 2. 写入初始状态
        state_data = {
            "id": clean_name,
            "type": task_type,
            "session": session or "N/A",
            "agent": agent or "N/A",
            "stage": STAGES[0],
            "stage_idx": 0,
            "stage_status": "pending",
            "created_at": now(),
            "updated_at": now(),
        }
        write_state(clean_name, state_data)

        # 3. 同步外部面板
        try:
            update_status_md_active(clean_name)
        except Exception as e:
            sw_log(clean_name, f"同步 STATUS.md 失败: {e}", "error")

        sw_log(clean_name, f"task created: {clean_name} (type={task_type})", "sw")
        return clean_name

    def list_tasks(self, from_trash: bool = False) -> List[Dict[str, Any]]:
        """
        获取任务列表及其核心状态。
        """
        base_dir = TRASH if from_trash else TASKS
        results = []
        
        if not base_dir.is_dir():
            return results

        for d in sorted(base_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            
            # 使用 read_state 自动处理旧格式迁移
            # 注意：read_state 默认从 TASKS 读，如果查回收站需特殊处理
            if from_trash:
                # 临时模拟环境让 read_state 读 TRASH 比较麻烦，
                # 这里简单直接读 JSON（回收站任务通常已经是新格式）
                sf = d / ".state"
                st = {}
                if sf.exists():
                    try:
                        import json
                        st = json.loads(sf.read_text(encoding="utf-8"))
                    except: pass
            else:
                st = read_state(d.name)
                
            results.append({
                "id": d.name,
                "stage": st.get("stage", "N/A"),
                "status": st.get("stage_status", "N/A"),
                "updated_at": st.get("updated_at", "N/A"),
                "removed_at": st.get("removed_at", "N/A") if from_trash else None
            })
        return results

    def remove_task(self, name: str):
        """将任务移动到回收站"""
        task_dir = TASKS / name
        if not task_dir.is_dir():
            raise TaskError(f"任务不存在: {name}")

        TRASH.mkdir(parents=True, exist_ok=True)
        
        # 记录移除时间
        st = read_state(name)
        st["removed_at"] = now()
        write_state(name, st)

        shutil.move(str(task_dir), str(TRASH / name))
        
        # 清除活跃标记
        try:
            clear_status_md_active(name)
        except: pass
        
        sw_log(name, "moved to trash", "sw")

    def restore_task(self, name: str):
        """从回收站恢复任务"""
        src = TRASH / name
        if not src.is_dir():
            raise TaskError(f"回收站中无此任务: {name}")

        dst = TASKS / name
        if dst.exists():
            raise TaskError(f"恢复失败，当前已有同名活跃任务: {name}")

        shutil.move(str(src), str(dst))

        # 清除移除标记
        st = read_state(name)
        if "removed_at" in st:
            del st["removed_at"]
        write_state(name, st)
        
        sw_log(name, "restored from trash", "sw")

    def add_answer(self, name: str, text: str):
        """向任务输入管道追加用户回复"""
        task_dir = TASKS / name
        if not task_dir.is_dir():
            raise TaskError(f"任务不存在: {name}")

        input_file = task_dir / ".input"
        with open(input_file, "a", encoding="utf-8") as f:
            f.write(f"[{now()}] user | {text}\n")
        
        # 如果当前由于等待提问处于 pending 状态，则恢复为 running
        st = read_state(name)
        if st.get("stage_status") == "pending":
            st["stage_status"] = "running"
            st["updated_at"] = now()
            write_state(name, st)

        sw_log(name, f"user answer added: {text[:50]}", "user")

    def get_task_state(self, name: str) -> Dict[str, Any]:
        """获取任务完整状态，若不存在则抛出异常"""
        st = read_state(name)
        if not st:
            raise TaskError(f"无法读取任务状态: {name}")
        return st

    def validate_stage(self, name: str) -> Tuple[List[str], List[str]]:
        """执行当前阶段的内容校验"""
        st = self.get_task_state(name)
        idx = st.get("stage_idx", 0)
        cur_stage = STAGES[idx]
        
        task_dir = TASKS / name
        return StageValidator.check(task_dir, cur_stage, idx)

    def advance_stage(self, name: str) -> Dict[str, Any]:
        """
        执行阶段推进逻辑。
        推进后状态默认为 'pending'，等待用户进入 monitor 或启动 Agent。
        
        Returns:
            更新后的状态字典
        """
        st = self.get_task_state(name)
        idx = int(st.get("stage_idx", 0))
        
        if idx >= len(STAGES) - 1:
            raise TaskError("任务已是最后阶段，无法继续推进")

        next_idx = idx + 1
        next_stage = STAGES[next_idx]

        st["stage"] = next_stage
        st["stage_idx"] = next_idx
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        
        write_state(name, st)
        
        try:
            update_status_md_stage(next_idx)
        except Exception as e:
            sw_log(name, f"更新看板失败: {e}", "error")
            
        sw_log(name, f"advanced to stage {next_idx}: {next_stage} (pending)", "sw")
        return st
