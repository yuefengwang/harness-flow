"""sw — Simple Workflow CLI (统一入口)

用法: ./sw <command> [options]

全局标志（可放在子命令前或后）:
  --yes, -y                    自动确认所有提示（破坏性操作必需）
  --non-interactive            非交互模式

任务生命周期:
  ./sw init    --type=feature --name=<id> [--context=<text>] [--agent=<agent>] [--target=<dir>] [--self]
  ./sw init                              # 交互模式
  ./sw monitor --name=<id>               # 进入实时面板，观看/介入后台任务
  ./sw status  [--name=<id>]
  ./sw advance [--name=<id>] [--no-next]         # 校验+推进阶段
  ./sw answer  --name=<id> --text=<reply>   # 回复 Agent 提问

任务管理:
  ./sw list    [--trash]
  ./sw remove  --name=<id>
  ./sw remove-all [--purge]              # 全部移入回收站；--purge 物理删除
  ./sw restore --name=<id>               # 从回收站恢复（别名: resume）
  ./sw resume  --name=<id>               # 同 restore
  ./sw purge-trash                        # 清空回收站（物理删除，不可恢复）

状态查询:
  ./sw state get <task> <stage> [gate|route] [--json]   # 门禁/路由状态（供 hook 调用）

运维:
  ./sw deploy    [--name=<id>] [--port=<n>] [--no-tunnel]
  ./sw health    [--name=<id>] [--interval=<s>] [--daemon]
  ./sw dashboard [--host=<h>] [--port=<n>]   # 启动 Web Dashboard
  ./sw test      [--name=<id>]               # 端到端集成测试（MockAgent）
  ./sw reap                                  # 清理测试残留（e2e-* / web-* / pytest-* / test-*）
"""

import signal
import sys
import os
import subprocess
import time
from pathlib import Path

from ..core.config import (
    ROOT, STAGES, STAGE_NAMES, HOOKS_DIR,
    HOOK_TIMEOUT_MINUTES, HOOK_TIMEOUT_SECONDS,
)
from ..core.state import get_active_from_status, write_state, upsert_task_summary, find_context_from_cwd
from ..core.deploy_orchestrator import DeployOrchestrator
from ..core.health import HealthMonitor, HealthConfig
from ..core.utils import (
    green, yellow,
    ok, warn, hdr, die,
    now,
)
from ..ui.init_ui import InitializationUI
from ..core.service import _service, TaskError

# 硬校验钩子上限来自 core.config，以分钟为单位配置。
HOOK_TIMEOUT = HOOK_TIMEOUT_SECONDS


def _apply_auto_advance_flags(args) -> None:
    """把 --auto / --manual / --unattended 应用到运行期配置。

    --auto / --manual 只影响阶段边界（是否免去 /advance）；
    --unattended 是独立维度，决定阶段内 agent 的提问是否代答。
    两者都没给时沿用 config.yaml；--manual 优先于 --auto，
    这样在配置默认开启自动的项目里也能一次性退回手动。
    """
    from ..core.config import set_auto_advance, set_auto_answer
    if getattr(args, "manual", False):
        set_auto_advance(False)
    elif getattr(args, "auto", False):
        set_auto_advance(True)
    if getattr(args, "unattended", False):
        set_auto_answer(True)


# ── commands ──

def apply_mock_flags(args) -> None:
    """把 `--mock` / `--no-mock` 落到进程内存**和环境变量**两处。

    优先级：CLI 标志 > `config.yaml`。

    必须同时写环境变量，否则钩子等**子进程**重新加载配置后看不到这个开关 ——
    主进程用 mock 的固定密钥签名、子进程用真实密钥校验，`.state` 会被判成
    `tampered`，03 阶段永久无法准出（`tests/e2e-flow/driver.py` 实测卡死）。
    详见 `core/config.is_mock_agent()` 的 docstring。
    """
    from ..core.config import set_mock_agent

    if getattr(args, "no_mock", False):
        set_mock_agent(False)
    elif getattr(args, "mock", False):
        set_mock_agent(True)


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

    # 处理 mock/no-mock 参数（优先级: CLI 输入 > config.yaml）
    apply_mock_flags(args)

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
        if os.environ.get("SW_NON_INTERACTIVE") != "1":
            args.name = clean_name
            cmd_monitor(args)
        else:
            hdr(f"任务已创建: {clean_name}")
            ok("Stage: 01-头脑风暴 (Pending)")
            print(f"  请稍后运行 ./sw monitor 启动 Agent")

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
            # 钩子脚本内部用 workspace/tasks/<name> 这类相对路径定位产出，
            # 因此 cwd 必须保持 harness 根目录；但要有超时上限，
            # 否则钩子里的 pytest/npm test 一挂，CLI 就永久卡住。
            try:
                res = subprocess.run([str(hook_script), name], cwd=str(ROOT),
                                     check=False, timeout=HOOK_TIMEOUT)
            except subprocess.TimeoutExpired:
                die(f"硬校验超时（>{HOOK_TIMEOUT_MINUTES:g} 分钟）: {hook_script.name}")
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
            if os.environ.get("SW_NON_INTERACTIVE") != "1":
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


def cmd_remove_all(args):
    """批量移除所有活跃任务（默认进回收站，--purge 物理删除）"""
    purge = getattr(args, "purge", False)
    tasks = _service.list_tasks()
    trashed = _service.list_tasks(from_trash=True) if purge else []

    if not tasks and not trashed:
        ok("没有需要移除的任务")
        return

    hdr("将物理删除以下任务（不可恢复）" if purge else "将移入回收站的任务")
    for t in tasks:
        print(f"  {t['id']:<30} {t['stage']:<20} [{t['status']}]")
    if purge:
        for t in trashed:
            print(f"  {t['id']:<30} {'(回收站)':<20}")

    total = len(tasks) + len(trashed)
    if not _confirm_destructive(f"确认移除 {total} 个任务?"):
        warn("已取消")
        return

    results = _service.remove_all_tasks(purge=purge)
    failed = [(n, why) for n, why in results if why]
    for name, why in failed:
        warn(f"{name}: {why}")

    moved = len(results) - len(failed)
    if purge:
        ok(f"已物理删除 {moved + len(trashed)} 个任务")
    else:
        ok(f"已移入回收站 {moved} 个任务，可用 ./sw restore --name=<id> 恢复")
    if failed:
        die(f"{len(failed)} 个任务移除失败")


def cmd_purge_trash(args):
    """清空回收站（物理删除，不可恢复）"""
    trashed = _service.list_tasks(from_trash=True)
    if not trashed:
        ok("回收站已是空的")
        return

    hdr("将从回收站物理删除（不可恢复）")
    for t in trashed:
        print(f"  {t['id']:<30} 移除于: {t['removed_at']}")

    if not _confirm_destructive(f"确认清空回收站中的 {len(trashed)} 个任务?"):
        warn("已取消")
        return

    purged = _service.purge_trash()
    ok(f"回收站已清空，物理删除 {len(purged)} 个任务")

    remaining = _service.list_tasks(from_trash=True)
    if remaining:
        for t in remaining:
            warn(f"未能删除: {t['id']}")
        die(f"{len(remaining)} 个任务清理失败")


def cmd_reap(args):
    """清理测试残留（任务目录 + repo 目录 + STATUS.json 条目）。

    自动收尾有覆盖不到的情况：运行被 Ctrl+C 或 kill 打断时，pytest 的
    session fixture 和 e2e driver 的 finally 都不会跑完，残留就留在仓库里。
    这个命令是那时的手动补救。

    只删名字匹配测试模式的任务（`e2e-*` / `web-*` / `pytest-*` / `test-app`），
    用户的真实任务不受影响，所以不设确认闸门。
    """
    from ..core import residue

    reaped = residue.reap()
    if not reaped:
        ok("没有测试残留")
        return

    hdr(f"已清理 {len(reaped)} 个测试残留")
    for name in reaped:
        print(f"  {name}")


def _confirm_destructive(prompt: str) -> bool:
    """破坏性操作的确认闸门。

    --yes 显式放行；非交互模式下没人能回答，一律拒绝而不是默认执行 ——
    批量删除误触的代价远高于多敲一次命令。
    """
    if os.environ.get("SW_YES") == "1":
        return True
    if os.environ.get("SW_NON_INTERACTIVE") == "1":
        warn("非交互模式下不执行破坏性操作，请显式加 --yes")
        return False
    try:
        return input(f"{yellow('[?]')} {prompt} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def cmd_restore(args):
    """从回收站恢复"""
    if not args.name: die("缺少 --name")
    try:
        _service.restore_task(args.name)
        ok(f"任务已从回收站恢复: {args.name}")
    except TaskError as e:
        die(str(e))


# `resume` 是 `restore` 的别名，共用同一实现。
#
# 历史上 `resume` 是个独立命令，但它什么都不 resume —— 只打印三行状态然后
# 提示你去跑 monitor。那份「查看状态」的功能 `cmd_status` 已经提供，
# 而「恢复」这个词在本项目里唯一的真实含义就是把任务从回收站捞回来。
#
# 用别名而非复制实现：回收站恢复要移目录、清 removed_at、重建 STATUS.json
# 条目，两份逻辑一旦漂移就是数据不一致。同 remove-all / purge-trash 的做法。
cmd_resume = cmd_restore


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
    # 惰性导入 TUI（避免非 monitor 命令也拉起 workflow.runtime/langgraph 链）
    from ..ui.tui import MonitorTUI
    _apply_auto_advance_flags(args)
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
    # 惰性导入 web 依赖，避免非 dashboard 命令也拉起 fastapi/langgraph 链
    import uvicorn
    from ..web.app import create_app
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


# ── state 查询（供 hook 与外部工具读 JSON 状态源）──

_STATE_FIELDS = ("gate", "route")


def cmd_state_get(args) -> int:
    """打印某阶段的门禁/路由状态。返回进程退出码。

    shell 侧读状态的唯一正当入口。hook 曾直接 grep 阶段文件里的
    ``[x] Design approved`` —— 那等于把 agent 能写的文本当门禁凭据，
    agent 复述一句勾选过的 Gate 就能骗过硬校验。

    输出裸值（``signed`` / ``unsigned`` / 路由名），退出码表示「是否已就绪」，
    这样 hook 里可以直接写 ``sw state get t 04-review route || exit 1``。
    """
    import json as _json
    from ..workflow import stage_state as ss

    field = getattr(args, "field", None) or "gate"
    if field not in _STATE_FIELDS:
        print(f"未知字段: {field}（可用: {', '.join(_STATE_FIELDS)}）")
        return 2

    from ..core.config import TASKS

    task, stage = args.task, args.stage
    if not (TASKS / task).is_dir():
        print(f"任务不存在: {task}")
        return 2

    as_json = bool(getattr(args, "json", False))

    if field == "gate":
        gate = ss.read_gate(task, stage)
        if as_json:
            print(_json.dumps({
                "signed": gate.signed,
                "signed_by": gate.signed_by,
                "signed_at": gate.signed_at,
                "items": [{"key": i.key, "label": i.label, "checked": i.checked}
                          for i in gate.items],
            }, ensure_ascii=False))
        else:
            print("signed" if gate.signed else "unsigned")
        return 0 if gate.signed else 1

    route = ss.read_route(task, stage)
    if as_json:
        print(_json.dumps({"route": route}, ensure_ascii=False))
    else:
        print(route or "")
    return 0 if route else 1
