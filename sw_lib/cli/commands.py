"""sw — Simple Workflow CLI (统一入口)

用法: ./sw <command> [options]
  ./sw init    --type=feature --name=<id> [--context=<text>] [--agent=<agent>] [--target=<dir>] [--self]
  ./sw init                              # 交互模式
  ./sw monitor --name=<id>               # Rich TUI 监控面板 + Agent 对话
  ./sw status  [--name=<id>]
  ./sw next    [--name=<id>] [--agent=<agent>]   # 注入上下文 (monitor 替代交互)
  ./sw advance [--name=<id>] [--force]           # 校验+推进阶段
  ./sw resume  --name=<id>
  ./sw list    [--trash]
  ./sw remove  --name=<id>
  ./sw restore --name=<id>
  ./sw answer  --name=<id> --text=<reply>   # 回复 Agent 提问
  ./sw dashboard                             # 启动 Web Dashboard
"""

import sys
import os
import subprocess

from ..core.config import ROOT, TASKS, STAGES, STAGE_NAMES, HOOKS_DIR, load_harness_config, resolve_agent_type
from ..web.app import create_app
from ..core.state import get_active_from_status
from ..core.utils import (
    green, yellow, blue,
    ok, warn, hdr, die,
    prompt, prompt_yn,
)
from ..ui.tui import MonitorTUI
from ..ui.init_ui import InitializationUI
from ..core.service import _service, TaskError


# ── commands ──

def cmd_init(args):
    """创建新任务"""
    name = args.name or ""
    task_type = args.type or "feature"
    context = getattr(args, "context", "") or ""
    agent = getattr(args, "agent", "") or ""
    interactive = getattr(args, "interactive", False)
    target = getattr(args, "target", "") or ""
    self_dev = getattr(args, "self", False)
    allow_trash_collision = False

    # 解析 target_dir
    if self_dev:
        target_dir = "."
    elif target:
        target_dir = target
    else:
        target_dir = ""  # 让 create_task 使用 config 默认值: repo/<name>

    # 交互模式：进入精简版专业向导界面
    if interactive or not name:
        ui = InitializationUI()
        result = ui.run(default_name=name, default_type=task_type)
        if not result:
            return # 用户取消
        
        name = result["name"]
        task_type = result["type"]
        context = result["context"]
        allow_trash_collision = result["allow_trash_collision"]
        target_dir = result.get("target_dir", target_dir)

    try:
        # 如果未指定 agent，则传 N/A 让 Service/Engine 自动解析
        clean_name = _service.create_task(
            name, task_type, 
            session="N/A", 
            agent=agent or "N/A", 
            context=context,
            allow_trash_collision=allow_trash_collision,
            target_dir=target_dir
        )
        
        # 自动进入监控面板 (原子化操作)
        if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("SW_NON_INTERACTIVE") != "1":
            args.name = clean_name
            cmd_monitor(args)
        else:
            hdr(f"任务已创建: {clean_name}")
            ok("Stage: 01-头脑风暴 (Pending)")
            print(f"  请稍后运行 ./sw monitor 启动 Agent")

    except TaskError as e:
        die(str(e))


def cmd_resume(args):
    """恢复交互上下文显示"""
    name = getattr(args, "name", "") or ""
    if not name:
        die("请指定任务名: ./sw resume --name=<id>")
    
    try:
        st = _service.get_task_state(name)
        hdr(f"恢复任务: {name}")
        print(f"  当前阶段: {st.get('stage')}")
        print(f"  当前状态: {st.get('stage_status')}")
        print(f"  操作: ./sw monitor (运行) 或 ./sw advance (推进)")
    except TaskError as e:
        die(str(e))


def cmd_status(args):
    """显示任务状态"""
    name = getattr(args, "name", "") or get_active_from_status()
    if not name or name == "无":
        die("无活跃任务")
    
    try:
        st = _service.get_task_state(name)
        stage = st.get("stage", "unknown")
        status = st.get("stage_status", "N/A")
        updated = st.get("updated_at", "N/A")
        print(f"任务: {name} | Stage: {stage} | 状态: {status} | {updated}")
    except TaskError as e:
        die(str(e))


def cmd_advance(args):
    """阶段推进指令 (强制执行校验)"""
    name = getattr(args, "name", "") or get_active_from_status()
    if not name:
        die("无活跃任务，请指定: ./sw advance --name=<task>")
    
    try:
        st = _service.get_task_state(name)
        idx = int(st.get("stage_idx", 0))
        cur_status = st.get("stage_status", "pending")
        
        if idx >= len(STAGES) - 1:
            ok("任务已完成 (05-归档)")
            return

        if cur_status == "pending":
            die("当前阶段尚未开始运行。请先运行: ./sw monitor")

        # 阶段内容校验 (Soft Check)
        hdr(f"阶段校验: {STAGES[idx]} ({STAGE_NAMES[idx]})")
        done_items, todo_items = _service.validate_stage(name)
        for item in done_items: print(f"  {green('[✓]')} {item}")
        for item in todo_items: print(f"  {yellow('[!]')} {item}")

        # 强阻断钩子 (Hard Check)
        hook_script = HOOKS_DIR / f"check_{STAGES[idx]}.sh"
        # 兼容新的命名规范
        if not hook_script.exists():
            hook_script = HOOKS_DIR / f"post_check_{STAGES[idx]}.sh"

        if hook_script.exists():
            hdr(f"系统硬校验: {hook_script.name}")
            res = subprocess.run([str(hook_script), name], cwd=str(ROOT), check=False)
            if res.returncode != 0:
                die(f"硬校验未通过，必须满足所有条件才能推进。")

        # 如果存在待办事项，也禁止推进（除非后续允许交互式确认，但这里按“严禁”逻辑直接拦截）
        if todo_items:
            die(f"检测到 {len(todo_items)} 个未完成项，请完善后重试。")

        # 执行推进
        new_st = _service.advance_stage(name)
        new_label = STAGE_NAMES[new_st['stage_idx']]
        hdr(f"阶段推进 → {new_label}")
        print(f"  状态: Pending (等待运行)")
        print(f"  下一步: ./sw monitor")

    except TaskError as e:
        die(str(e))


def cmd_list(args):
    """列出任务"""
    show_trash = getattr(args, "trash", False)
    tasks = _service.list_tasks(from_trash=show_trash)
    
    if show_trash:
        hdr("已移除任务 (.trash)")
        if not tasks: print("  (空)")
        for t in tasks:
            print(f"  {t['id']:<30} {t['stage']:<20} 移除于: {t['removed_at']}")
    else:
        hdr("活跃任务")
        if not tasks: print("  (无活跃任务)")
        for t in tasks:
            print(f"  {t['id']:<30} {t['stage']:<20} [{t['status']}]")


def cmd_remove(args):
    """移入回收站"""
    if not args.name: die("缺少 --name")
    try:
        _service.remove_task(args.name)
        ok(f"任务已移入回收站: {args.name}")
    except TaskError as e:
        die(str(e))


def cmd_restore(args):
    """从回收站恢复"""
    if not args.name: die("缺少 --name")
    try:
        _service.restore_task(args.name)
        ok(f"任务已从回收站恢复: {args.name}")
    except TaskError as e:
        die(str(e))


def cmd_answer(args):
    """回复 Agent"""
    name = args.name or get_active_from_status()
    if not name or not args.text: die("缺少任务名或回复内容")
    try:
        _service.add_answer(name, args.text)
        ok(f"已回复: {args.text[:50]}...")
    except TaskError as e:
        die(str(e))


def cmd_monitor(args):
    """启动流式终端监控面板，直连 Agent 进程"""
    name = getattr(args, "name", "") or get_active_from_status()
    if not name or name == "无":
        die("缺少 --name")

    try:
        st = _service.get_task_state(name)
        stage = st.get("stage", "").strip('"')
        idx = int(st.get("stage_idx", 0))
        agent = st.get("agent", "").strip('"')
        if agent in ("N/A", ""):
            agent = ""

        MonitorTUI(name, stage, idx, agent).run()
    except TaskError as e:
        die(str(e))


def cmd_dashboard(args):
    """启动 Web Dashboard (FastAPI + HTMX)"""
    import uvicorn
    host = getattr(args, "host", "127.0.0.1")
    port = int(getattr(args, "port", 8080))
    app = create_app()
    print(f"🌐 Harness-Flow Dashboard: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_usage():
    """打印帮助信息"""
    print(__doc__)
