"""
sw_lib.service — 任务管理核心业务逻辑 (TaskService).

该模块将原本散落在 commands.py 中的业务逻辑收拢，提供统一的、
不依赖于 CLI 表现层的任务操作接口。
"""

import shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple

from .config import TASKS, TPLS, STAGES, TRASH
from .state import (
    read_state, write_state, update_state, NO_CHANGE,
    upsert_task_summary, remove_task_summary,
)
from .utils import now, sanitize_name, sw_log
from ..workflow.runtime import WorkflowRuntime


class TaskError(Exception):
    """任务操作相关的业务异常"""
    pass


def _prepare_target_dir(target_dir: str) -> Path:
    """显式创建目标目录，失败即抛 TaskError。

    A1 的 2.6：此前目录是 `_write_context_marker` 顺手建的，而那个函数整段包在
    `except Exception: pass` 里 —— 创建失败被静默吞掉，`.state` 里仍留着一个
    指向不存在目录的 `target_dir`，直到 deploy 或归档才炸，那时上下文已丢失。

    相对路径的锚点**必须与 git_repo 一致**（都锚 harness 根），不能用
    `Path.cwd()`：`repo_path: repo` 指的是 harness 根下的 `repo/`，而 `sw`
    可以从任意目录调用。两处锚点不一致时目录建在一处、仓库初始化在另一处。
    """
    from .git_repo import resolve_target_dir
    d = resolve_target_dir(target_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise TaskError(
            f"无法创建任务目标目录: {d}\n  原因: {e}\n"
            f"  请检查路径是否可写、父路径是否为文件。") from e
    if not d.is_dir():
        raise TaskError(f"任务目标目录不可用: {d}")
    return d


def _write_context_marker(target_dir: str, project_name: str, task_type: str):
    """在目标目录创建 .sw-context 标记文件，用于项目自动发现。

    ⚠️ 时序：必须在**基线提交之前**写入（A1 的 2.5 实测）。
    基线之后写会让 `git status` 显示 `?? .sw-context`，被误当成 agent 的产出
    计入 diff；由 A3/A6/A10 各自过滤则是散落的隐性知识，漏一处就产生假事实。
    """
    import json
    marker_dir = _prepare_target_dir(target_dir)
    marker = {
        "project": project_name,
        # 与 `.state` 保持**同一种表示法**：原样存传入的 target_dir。
        #
        # 此前这里用 `.resolve()` 存绝对路径，而 `.state` 存相对路径 ——
        # 同一个概念两种写法。agent 读到 `.sw-context` 的绝对路径后，
        # 在自己的 cwd（就是该目录）里又拼了一层，于是探路全部落空
        # （任务 helloworld 实测）。表示法不一致本身就是故障源，
        # 哪一种都可以，但必须只有一种。
        "target_dir": target_dir,
        "type": task_type,
        "created": now(),
    }
    (marker_dir / ".sw-context").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
        # 刻意不走 update_state（A15 的 2.2 已裁定）：任务此刻**尚不存在**，
        # 不存在第二个写者；受控入口的 mutator 会拿到空状态，语义上是「创建」
        # 而非「修改」。硬套受控化会让「任务已存在」的冲突检测失效。
        write_state(clean_name, state_data, _internal=True)

        upsert_task_summary(clean_name,
            type=task_type,
            stage=STAGES[0], stage_idx=0, stage_status="pending",
            target_dir=target_dir,
            created_at=state_data["created_at"])

        # 目标仓库与基线锚定（A1 的 3.5）。
        # 失败**必须让 create_task 抛错** —— 带着无效 target_dir 的任务在归档时
        # 才炸，那时上下文已丢失（A1 的 2.6）。
        self._init_task_repo(clean_name, target_dir, task_type)

        sw_log(clean_name, f"task created: {clean_name} (type={task_type})", "sw")
        return clean_name

    def _init_task_repo(self, name: str, target_dir: str, task_type: str):
        """建立任务级独立仓库并把基线写入 `.state` 的 `review` 键。

        顺序不可调换：marker 与 `.gitignore` 都要**一并计入基线**，
        这样 `.sw-context` 既不出现在后续 diff 里，也不会被当作 agent 产出。
        """
        from .git_repo import GitRepoError, record_baseline

        if not target_dir:
            return
        if target_dir != ".":
            _write_context_marker(target_dir, name, task_type)
        try:
            record_baseline(name, target_dir)
        except GitRepoError as e:
            raise TaskError(f"任务 {name} 的基线仓库初始化失败:\n{e}") from e

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
        
        # 记录移除时间。走受控入口：任务可能仍在运行，agent / HealthMonitor
        # 都可能正在写盘，拿入口快照整体写回会把它们的写入抹掉。
        def _mark_removed(state):
            if not state:
                return NO_CHANGE
            state["removed_at"] = now()
            return state

        update_state(name, _mark_removed)

        shutil.move(str(task_dir), str(TRASH / name))
        
        remove_task_summary(name)
        sw_log(name, "moved to trash", "sw")

    def remove_all_tasks(self, purge: bool = False) -> List[Tuple[str, str]]:
        """批量移除所有活跃任务。

        默认与 remove_task 语义一致（移入回收站，可 restore）。purge=True 时
        连回收站一起物理删除，不可恢复。

        返回 [(任务名, "" 或错误原因)]，逐个隔离失败：一个任务删不掉不该
        让剩下的全都留在原地。
        """
        results: List[Tuple[str, str]] = []
        for entry in self.list_tasks():
            name = entry["id"]
            try:
                self.remove_task(name)
                results.append((name, ""))
            except (TaskError, OSError) as e:
                results.append((name, str(e)))
        if purge:
            self.purge_trash()
        return results

    def purge_trash(self) -> List[str]:
        """清空回收站，返回被物理删除的任务名。"""
        purged: List[str] = []
        if not TRASH.is_dir():
            return purged
        for d in sorted(TRASH.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            shutil.rmtree(d, ignore_errors=True)
            remove_task_summary(d.name)
            purged.append(d.name)
        return purged

    def restore_task(self, name: str):
        """从回收站恢复任务"""
        src = TRASH / name
        if not src.is_dir():
            raise TaskError(f"回收站中无此任务: {name}")

        dst = TASKS / name
        if dst.exists():
            raise TaskError(f"恢复失败，当前已有同名活跃任务: {name}")

        shutil.move(str(src), str(dst))

        # 清除移除标记。没有标记可清时返回 NO_CHANGE：不刷 updated_at、
        # 不重算签名，免得在审计里留下一次无内容的状态变更。
        def _clear_removed(state):
            if not state or "removed_at" not in state:
                return NO_CHANGE
            del state["removed_at"]
            return state

        st = update_state(name, _clear_removed)
        
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
        
        # 如果当前由于等待提问处于 pending 状态，则恢复为 running。
        # 走受控入口：这是与 agent 写状态**高频并发**的一处 —— 用户回答问题时
        # agent 往往正在写盘，拿旧快照整体写回会把它的写入抹掉。
        def _resume_running(state):
            if not state or state.get("stage_status") != "pending":
                return NO_CHANGE          # 幂等路径，不刷 updated_at
            state["stage_status"] = "running"
            state["updated_at"] = now()
            return state

        update_state(name, _resume_running)

        sw_log(name, f"user answer added: {text[:50]}", "user")

    # ── 异步包装接口 (供 Web Dashboard 等异步调用方使用) ──

    @classmethod
    def get_instance(cls) -> "TaskService":
        """获取全局单例"""
        global _service_singleton
        if _service_singleton is None:
            _service_singleton = cls()
        return _service_singleton

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
        
        from ..workflow.utils import check_stage_compliance
        return check_stage_compliance(name, cur_stage, idx)

    def advance_stage(self, name: str) -> Dict[str, Any]:
        """执行阶段推进 — 路由逻辑统一委托给 WorkflowRuntime.advance。

        Phase 1 重构：不再直接访问 executor 的私有属性 (_stage_map /
        _stage_order)，改由 WorkflowRuntime.advance() 作为唯一路由真相源。
        """
        return WorkflowRuntime.advance(name)

    def deploy_task(self, name: str, force: bool = False) -> Dict[str, Any]:
        """
        部署任务：将已完成的任务发布到目标目录。

        Args:
            name: 任务名称
            force: 若为 True，跳过 stage_status == "Finished" 检查

        Returns:
            更新后的任务状态字典
        """
        # 先读一次只为把「任务不存在」报成 TaskError，而不是让 update_state
        # 抛出更底层的错。真正的准入检查在 mutator 里、锁内重做一遍：
        # 「检查完再写」跨两次读写时，两个并发 deploy 会双双通过 deploying 检查。
        self.get_task_state(name)

        def _begin_deploy(state):
            if not state:
                raise TaskError(f"无法读取任务状态: {name}")
            if not force and state.get("stage_status") != "Finished":
                raise TaskError(f"任务未完成，无法部署: {name}")
            if state.get("deploy_status") == "deploying":
                raise TaskError(f"任务正在部署中: {name}")
            target_dir = state.get("target_dir", "")
            if not target_dir or not Path(target_dir).is_dir():
                raise TaskError(f"目标目录不存在或不可用: {target_dir}")
            state["deploy_status"] = "deploying"
            state["deploy_at"] = now()
            state["updated_at"] = now()
            return state

        st = update_state(name, _begin_deploy)
        upsert_task_summary(name, deploy_status="deploying")

        return st

    def complete_deploy(self, name: str, success: bool, deploy_url: str = ""):
        """完成部署：标记部署结果为成功或失败，可选记录服务地址"""
        self.get_task_state(name)       # 任务不存在时报 TaskError

        # 走受控入口：HealthMonitor 在独立线程里写同一份 `.state`
        # （health_status / deploy_status），拿旧快照整体写回会抹掉它的写入。
        def _finish_deploy(state):
            if not state:
                raise TaskError(f"无法读取任务状态: {name}")
            state["deploy_status"] = "deployed" if success else "deploy_failed"
            state["updated_at"] = now()
            if success:
                state["health_status"] = "ok"
                if deploy_url:
                    state["deploy_url"] = deploy_url
                if "health_config" not in state:
                    state["health_config"] = {
                        "enabled": True,
                        "check_interval": 10,
                        "failure_threshold": 3,
                        "auto_redeploy": False,
                        "max_redeploys": 5,
                        "redeploy_window_sec": 300,
                    }
            else:
                if deploy_url:
                    state["deploy_url"] = deploy_url
                elif "deploy_url" in state:
                    # 部署失败且无新 URL → 清除旧 URL，避免 HealthMonitor 检测失效域名
                    del state["deploy_url"]
            return state

        st = update_state(name, _finish_deploy)
        upsert_task_summary(name, deploy_status=st["deploy_status"])

    def _write_state_safe(self, name: str, data: dict):
        """把 `data` 里**发生变化的标量字段**合并进当前状态。

        供 HealthMonitor 等持有过期快照的外部调用方使用。

        原先这里是裸 `write_state`：**函数名承诺 safe，实现没兑现**。
        那比直接调 `write_state` 更坏 —— 它让调用方以为并发问题已经处理过了。
        HealthMonitor 跑在独立线程，它手里的快照与写盘之间隔着一次 HTTP 探测，
        过期是常态；整体写回会把这期间用户签的 Gate 抹掉（A15 已实测）。

        ⚠️ 语义不是「浅合并」，而是「只写差异标量」。真实路径实测（A15 的
        2.9.20）：浅合并**仍然会抹掉判据** —— 调用方的快照里早就带着一棵
        **旧的** `stages` 子树，`dict.update` 会用旧子树整棵替换新的，
        用户刚签的 Gate 随之消失（实测 True → False）。
        单元测试当时是绿的，因为那份快照读取于 `stages` 出现之前。

        因此这里只回写「调用方确实改动过的标量字段」：
        取 `data` 与磁盘现状逐键比对，跳过 dict / list 这类嵌套结构 ——
        判据（`stages`、`red_witness`、`facts`）全都住在嵌套结构里，
        而 HealthMonitor 要写的 `health_status` / `deploy_status` /
        `updated_at` / `deploy_url` 全是标量。

        边界（写明而不是假装没有）：
        - **删除键无法通过本函数表达**，嵌套结构也不能通过它修改。
          当前 3 处调用点都只赋标量；需要改嵌套结构的调用方必须自己写 mutator。
        """
        from .state import update_state as _us

        def _merge_scalars(state):
            state = state or {}
            for key, value in (data or {}).items():
                if isinstance(value, (dict, list)):
                    continue          # 判据住在嵌套结构里，不许被过期快照带回来
                if state.get(key) != value:
                    state[key] = value
            return state

        _us(name, _merge_scalars)


_service = TaskService()
