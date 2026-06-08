"""
sw_lib.service — 任务管理核心业务逻辑 (TaskService).

该模块将原本散落在 commands.py 中的业务逻辑收拢，提供统一的、
不依赖于 CLI 表现层的任务操作接口。

同时提供异步包装方法，供 Web Dashboard (FastAPI) 等异步调用方使用。
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from .config import ROOT,  TASKS, TPLS, STAGES, STAGE_NAMES, TRASH
from .state import (
    read_state, write_state, state_path,
    get_active_from_status, upsert_task_summary, remove_task_summary,
)
from .utils import now, sanitize_name, sw_log


class TaskError(Exception):
    """任务操作相关的业务异常"""
    pass


def _write_context_marker(target_dir: str, project_name: str, task_type: str):
    """在目标目录创建 .sw-context 标记文件，用于项目自动发现"""
    import json, os
    marker_dir = Path(target_dir)
    if not marker_dir.is_absolute():
        marker_dir = Path.cwd() / target_dir
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = {
            "project": project_name,
            "target_dir": str(marker_dir.resolve()),
            "type": task_type,
            "created": now(),
        }
        (marker_dir / ".sw-context").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass  # marker 文件创建失败不阻塞任务创建


class TaskService:
    """
    封装 Harness-Flow 任务的全生命周期管理逻辑。
    """

    def create_task(self, name: str, task_type: str = "feature", 
                    session: str = "N/A", agent: str = "N/A", 
                    context: str = "", allow_trash_collision: bool = False,
                    target_dir: str = "") -> str:
        """
        创建一个新任务。
        
        Args:
            name: 任务原始名称
            task_type: 任务类型
            session: 会话 ID
            agent: 指定的 Agent 或角色
            context: 需求上下文文本
            allow_trash_collision: 是否允许同名任务在回收站中
            target_dir: 生成代码的目标目录 (空字符串 = 使用 config repo_path 默认值)
            
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
            safe_context = context.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
            (task_dir / ".context").write_text(safe_context, encoding="utf-8")

        # 解析 target_dir：显式传参 > config 默认值
        if not target_dir:
            from .config import get_repo_path
            target_dir = str(Path(get_repo_path()) / clean_name)

        # 2. 写入初始状态
        state_data = {
            "id": clean_name,
            "type": task_type,
            "session": session or "N/A",
            "agent": agent or "N/A",
            "stage": STAGES[0],
            "stage_idx": 0,
            "stage_status": "pending",
            "target_dir": target_dir,
            "created_at": now(),
            "updated_at": now(),
        }
        write_state(clean_name, state_data)

        upsert_task_summary(clean_name,
            type=task_type,
            stage=STAGES[0], stage_idx=0, stage_status="pending",
            target_dir=target_dir,
            created_at=state_data["created_at"])

        if target_dir and target_dir != ".":
            _write_context_marker(target_dir, clean_name, task_type)

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
                "removed_at": st.get("removed_at", "N/A") if from_trash else None,
                "deploy_status": st.get("deploy_status", "idle"),
                "deploy_url": st.get("deploy_url", ""),
                "health_status": st.get("health_status", ""),
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
        
        remove_task_summary(name)
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
        
        upsert_task_summary(name,
            type=st.get("type", "feature"),
            stage=st.get("stage", ""), stage_idx=st.get("stage_idx", 0),
            stage_status=st.get("stage_status", "pending"))
        
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

    # ── 异步包装接口 (供 Web Dashboard 等异步调用方使用) ──

    @classmethod
    def get_instance(cls) -> "TaskService":
        """获取全局单例"""
        global _service_singleton
        if _service_singleton is None:
            _service_singleton = cls()
        return _service_singleton

    async def async_list_tasks(self, from_trash: bool = False) -> List[Dict[str, Any]]:
        """异步版 list_tasks，通过线程池包装同步 I/O"""
        return await asyncio.to_thread(self.list_tasks, from_trash)

    async def async_get_task_state(self, name: str) -> Dict[str, Any]:
        """异步版 get_task_state"""
        return await asyncio.to_thread(self.get_task_state, name)

    async def async_create_task(self, name: str, task_type: str = "feature",
                                 session: str = "N/A", agent: str = "N/A",
                                 context: str = "", allow_trash_collision: bool = False) -> str:
        """异步版 create_task"""
        return await asyncio.to_thread(
            self.create_task, name, task_type, session, agent, context, allow_trash_collision
        )

    async def async_advance_stage(self, name: str) -> Dict[str, Any]:
        """异步版 advance_stage"""
        return await asyncio.to_thread(self.advance_stage, name)

    async def async_remove_task(self, name: str):
        """异步版 remove_task"""
        return await asyncio.to_thread(self.remove_task, name)

    async def async_restore_task(self, name: str):
        """异步版 restore_task"""
        return await asyncio.to_thread(self.restore_task, name)

    async def async_validate_stage(self, name: str) -> Tuple[List[str], List[str]]:
        """异步版 validate_stage"""
        return await asyncio.to_thread(self.validate_stage, name)

    async def async_add_answer(self, name: str, text: str):
        """异步版 add_answer"""
        return await asyncio.to_thread(self.add_answer, name, text)

    def get_task_state(self, name: str) -> Dict[str, Any]:
        """获取任务完整状态，若不存在则抛出异常"""
        st = read_state(name)
        if not st:
            raise TaskError(f"无法读取任务状态: {name}")
        return st

    def validate_stage(self, name: str) -> Tuple[List[str], List[str]]:
        """执行当前阶段的内容校验"""
        st = self.get_task_state(name)
        idx = int(st.get("stage_idx", 0))
        cur_stage = STAGES[idx]
        
        from ..runnable.utils import check_stage_compliance
        return check_stage_compliance(name, cur_stage, idx)

    def advance_stage(self, name: str) -> Dict[str, Any]:
        """执行阶段推进 — 使用 WorkflowRuntime 的路由逻辑确定下一阶段。"""
        st = self.get_task_state(name)
        idx = int(st.get("stage_idx", 0))
        cur_stage = STAGES[idx]

        if idx >= len(STAGES) - 1:
            st["stage_status"] = "Finished"
            st["updated_at"] = now()
            write_state(name, st)
            upsert_task_summary(name, stage_status="Finished")
            sw_log(name, "🏁 任务已完成 (Finished)", "sw")
            return st

        from ..runnable.utils import auto_check_gate, parse_route_field
        from ..runnable.runtime import WorkflowRuntime

        auto_check_gate(name, cur_stage)
        executor = WorkflowRuntime.get_executor()

        next_stage = cur_stage
        next_idx = idx

        # 1. 优先处理 Review 阶段的路由
        if cur_stage == "04-review":
            target = parse_route_field(name)
            if target and target in executor._stage_map:
                next_stage = target
                next_idx = executor._stage_map[target].stage_idx

        # 2. 如果没有路由决策或非 Review 阶段，则线性推进
        if next_stage == cur_stage:
            cur_idx_in_chain = executor._stage_order.index(cur_stage)
            if cur_idx_in_chain + 1 < len(executor._stage_order):
                next_stage = executor._stage_order[cur_idx_in_chain + 1]
                next_idx = executor._stage_map[next_stage].stage_idx

        if next_stage == cur_stage:
             # 无处可去，标记为结束
            st["stage_status"] = "Finished"
            st["updated_at"] = now()
            write_state(name, st)
            upsert_task_summary(name, stage_status="Finished")
            return st

        st["stage"] = next_stage
        st["stage_idx"] = next_idx
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        write_state(name, st)
        upsert_task_summary(name, stage=next_stage, stage_idx=st["stage_idx"],
                              stage_status="pending")
        sw_log(name, f"advanced to {next_stage} (via chain logic)", "sw")
        return st

    def deploy_task(self, name: str, force: bool = False) -> Dict[str, Any]:
        """
        部署任务：将已完成的任务发布到目标目录。

        Args:
            name: 任务名称
            force: 若为 True，跳过 stage_status == "Finished" 检查

        Returns:
            更新后的任务状态字典
        """
        st = self.get_task_state(name)

        if not force and st.get("stage_status") != "Finished":
            raise TaskError(f"任务未完成，无法部署: {name}")

        if st.get("deploy_status") == "deploying":
            raise TaskError(f"任务正在部署中: {name}")

        target_dir = st.get("target_dir", "")
        if not target_dir or not Path(target_dir).is_dir():
            raise TaskError(f"目标目录不存在或不可用: {target_dir}")

        st["deploy_status"] = "deploying"
        st["deploy_at"] = now()
        st["updated_at"] = now()
        write_state(name, st)
        upsert_task_summary(name, deploy_status="deploying")

        return st

    def complete_deploy(self, name: str, success: bool, deploy_url: str = ""):
        """完成部署：标记部署结果为成功或失败，可选记录服务地址"""
        st = self.get_task_state(name)
        st["deploy_status"] = "deployed" if success else "deploy_failed"
        st["updated_at"] = now()
        if success:
            st["health_status"] = "ok"
            if deploy_url:
                st["deploy_url"] = deploy_url
            if "health_config" not in st:
                st["health_config"] = {
                    "enabled": True,
                    "check_interval": 10,
                    "failure_threshold": 3,
                    "auto_redeploy": False,
                    "max_redeploys": 5,
                    "redeploy_window_sec": 300,
                }
        else:
            if deploy_url:
                st["deploy_url"] = deploy_url
            elif "deploy_url" in st:
                # 部署失败且无新 URL → 清除旧 URL，避免 HealthMonitor 检测失效域名
                del st["deploy_url"]
        write_state(name, st)
        upsert_task_summary(name, deploy_status=st["deploy_status"])

    def _write_state_safe(self, name: str, data: dict):
        """安全写入状态（供 HealthMonitor 等外部调用），直接写 .state 文件。"""
        from .state import state_path, write_state as _ws
        _ws(name, data)


_service = TaskService()
