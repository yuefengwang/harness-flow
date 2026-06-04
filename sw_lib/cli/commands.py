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

import signal
import sys
import os
import subprocess
import time
from pathlib import Path

from ..core.config import ROOT, TASKS, STAGES, STAGE_NAMES, HOOKS_DIR, load_harness_config, resolve_agent_type
from ..web.app import create_app
from ..core.state import get_active_from_status, write_state, upsert_task_summary, find_context_from_cwd
from ..core.deploy_orchestrator import DeployOrchestrator
from ..core.health import HealthMonitor, HealthConfig
from ..core.utils import (
    green, yellow, blue,
    ok, warn, hdr, die,
    prompt, prompt_yn,
    now,
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
            # 主动进入归档完成流程：执行校验后将状态置为 Finished
            hdr(f"归档阶段校验: {STAGES[idx]} ({STAGE_NAMES[idx]})")
            done_items, todo_items = _service.validate_stage(name)
            for item in done_items: print(f"  {green('[✓]')} {item}")
            for item in todo_items: print(f"  {yellow('[!]')} {item}")
            if todo_items:
                die(f"检测到 {len(todo_items)} 个未完成项，请完善后重试。")

            st["stage_status"] = "Finished"
            st["updated_at"] = now()
            write_state(name, st)
            upsert_task_summary(name, stage_status="Finished")
            ok("🏁 任务已完成 (Finished)")
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

        # 返工时自动启动目标阶段（无需用户手动 ./sw monitor）
        if new_st.get("reroute_count", 0) > 0:
            if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("SW_NON_INTERACTIVE") != "1":
                print(f"  🔄 返工至 {new_label}，自动启动中...")
                args.name = name
                cmd_monitor(args)
                return
            else:
                print(f"  状态: Running (已就绪，可运行 ./sw monitor 查看)")
        else:
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


def cmd_health(args):
    """启动服务健康监控"""
    name = getattr(args, "name", "") or ""
    if not name:
        ctx = find_context_from_cwd()
        if ctx and ctx.get("project"):
            name = ctx["project"]
        else:
            name = get_active_from_status()
        if not name or name == "无":
            die("未指定任务名。请使用 --name 指定。")

    try:
        st = _service.get_task_state(name)
    except TaskError as e:
        die(str(e))

    health_config = st.get("health_config", {})
    target_dir = st.get("target_dir", "")

    if not target_dir:
        die(f"任务 {name} 未设置目标目录")

    # 从 deploy_url 解析端口
    deploy_url = st.get("deploy_url", "")
    port = 8000
    if deploy_url and ":" in deploy_url:
        try:
            port = int(deploy_url.rsplit(":", 1)[-1].rstrip("/"))
        except (ValueError, IndexError):
            pass

    # CLI 参数覆盖 health_config 中的间隔
    cli_interval = getattr(args, "interval", 0)
    config = HealthConfig(
        enabled=health_config.get("enabled", True),
        check_interval=cli_interval if cli_interval > 0 else health_config.get("check_interval", 10),
        failure_threshold=health_config.get("failure_threshold", 3),
        auto_redeploy=health_config.get("auto_redeploy", False),
        max_redeploys=health_config.get("max_redeploys", 5),
        redeploy_window_sec=health_config.get("redeploy_window_sec", 300),
    )

    check_url = deploy_url if deploy_url.startswith("http") else ""

    hdr(f"服务健康监控: {name}")
    print(f"目标目录: {target_dir}")
    print(f"服务端口: {port}")
    print(f"检查间隔: {config.check_interval}s")
    print(f"自动恢复: {'开启' if config.auto_redeploy else '关闭'}")
    if check_url:
        print(f"HTTP 检测: {check_url}")
    print()

    monitor = HealthMonitor(
        name=name,
        target_dir=target_dir,
        port=port,
        config=config,
        log_callback=print,
        check_url=check_url,
    )

    import signal as _signal

    def _handle_stop(signum=None, frame=None):
        print()
        warn("正在停止健康监控...")
        monitor.stop()

    _signal.signal(_signal.SIGINT, _handle_stop)
    _signal.signal(_signal.SIGTERM, _handle_stop)

    try:
        monitor.run()
    except KeyboardInterrupt:
        monitor.stop()

    ok("健康监控已停止")


def cmd_deploy(args):
    """部署应用：通过 Agent 一键部署 sw init 生成的项目"""
    name = getattr(args, "name", "") or ""
    deploy_port = getattr(args, "port", 8000)
    no_tunnel = getattr(args, "no_tunnel", False)

    if not name:
        ctx = find_context_from_cwd()
        if ctx and ctx.get("project"):
            name = ctx["project"]
            target_dir = ctx.get("target_dir", "")
            hdr(f"检测到项目上下文: {name}")
        else:
            name = get_active_from_status()
            if not name or name == "无":
                die("未指定任务名，且当前目录无 .sw-context 标记。请使用 --name 指定。")
            target_dir = ""
    else:
        target_dir = ""

    try:
        st = _service.get_task_state(name)
        if not target_dir:
            target_dir = st.get("target_dir", "")

        if not target_dir:
            die(f"任务 {name} 未设置目标目录")

        target_path = Path(target_dir)
        if not target_path.is_dir():
            die(f"目标目录不存在: {target_dir}")

        _service.deploy_task(name, force=True)
        hdr(f"正在部署: {name}")
        print(f"目标目录: {target_dir}")
        print(f"首选端口: {deploy_port}")
        if no_tunnel:
            print(f"Cloudflare Tunnel: 已禁用")
        print()

        orchestrator = DeployOrchestrator(
            name=name,
            target_dir=target_dir,
            port=deploy_port,
            no_tunnel=no_tunnel,
            log_callback=print,
        )
        result = orchestrator.run()

        if result.service_url:
            ok(f"部署成功!")
            print(f"服务地址: {green(result.service_url)}")
        else:
            warn("部署完成，但未检测到服务地址。请查看日志。")
            return

        # ── 前台阻塞模式 ──
        from ..core.config import TASKS as _TASKS

        deploy_log = _TASKS / name / ".deploy_log"

        def _cleanup(signum=None, frame=None):
            """清理子进程和 tunnel"""
            print()
            warn("正在停止服务...")
            orchestrator.stop()
            ok("服务已停止")
            sys.exit(0)

        # 注册信号处理
        signal.signal(signal.SIGINT, _cleanup)
        signal.signal(signal.SIGTERM, _cleanup)

        hdr("服务运行中 (Ctrl+C 停止)")
        print(f"本地地址: {green(result.service_url)}")
        print()

        # 实时 tail 日志
        try:
            if deploy_log.exists():
                with open(deploy_log, "r") as f:
                    lines = f.readlines()
                    tail_lines = lines[-5:] if len(lines) > 5 else lines
                    for line in tail_lines:
                        print(f"  {line.strip()}")

            last_size = deploy_log.stat().st_size if deploy_log.exists() else 0
            while True:
                time.sleep(0.5)
                if deploy_log.exists():
                    current_size = deploy_log.stat().st_size
                    if current_size > last_size:
                        with open(deploy_log, "r") as f:
                            f.seek(last_size)
                            new_data = f.read()
                            if new_data:
                                print(new_data, end="")
                            last_size = f.tell()

                # 检查进程存活
                pid_file = _TASKS / name / ".deploy.pid"
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text().strip())
                        os.kill(pid, 0)
                    except (ProcessLookupError, ValueError, OSError):
                        warn("服务进程已意外退出")
                        _cleanup()
                        break
        except KeyboardInterrupt:
            _cleanup()

    except TaskError as e:
        die(str(e))


def cmd_usage():
    """打印帮助信息"""
    print(__doc__)
