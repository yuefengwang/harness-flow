"""
sw_lib.main — 命令行入口与指令分发。

该模块负责解析命令行参数并将其路由到对应的业务指令函数。
使用 argparse 提供规范的子命令支持。
"""

import argparse
import os
import sys

from .commands import (
    cmd_init, cmd_status, cmd_advance,
    cmd_list, cmd_remove, cmd_remove_all, cmd_restore, cmd_answer, cmd_monitor,
    cmd_dashboard, cmd_usage, cmd_deploy, cmd_health, cmd_purge_trash,
    cmd_state_get, cmd_reap,
)
from .test_cmd import cmd_test
from ..core.config import ConfigError


def _ensure_bootstrapped():
    """Activate Phase 1-3 workflow chain on first CLI invocation.

    `ConfigError` **刻意不吞**（A14 的 3.2.1）：它的语义是「配置写错了，
    人必须来改」，与「某个可选模块没装好」不是一类事。此前这里是
    `except Exception: pass`，会把配置校验的结论静默丢弃 —— 那样
    `assert_config_valid` 就成了一条永远不会被人看见的判据，
    形状 S7 换个形态复发。

    其余异常保持原有的非致命语义：bootstrap 失败时系统回落到旧路径。
    """
    try:
        from ..core.bootstrap import bootstrap
        bootstrap()
    except ConfigError:
        raise
    except Exception:
        pass  # bootstrap failure is non-fatal; system falls back to legacy


def main():
    # 配置错误以可读文本 + 非零退出码呈现（A14）。
    #
    # 裸 traceback 把「你的配置写错了」表述成「程序崩了」，而退出码 0
    # 会让脚本与 CI 认为一切正常 —— 判据拦住了却没人知道。
    # 与 A2 的 10.6 同一条纪律：拦一条路时必须让人看懂发生了什么。
    try:
        _ensure_bootstrapped()
    except ConfigError as exc:
        print(f"\n配置错误 —— harness 拒绝启动：\n{exc}\n", file=sys.stderr)
        return 2
    # 全局标志同时挂到主 parser 和每个子命令上。只挂主 parser 的话，
    # `sw remove-all --yes` 里的 --yes 会被 argparse 当作未知参数 ——
    # 而 --yes 决定破坏性操作能否执行，静默丢弃是最坏的失败方式：
    # 用户以为自己已经确认过了，实际被闸门拦住且没有任何解释。
    global_flags = argparse.ArgumentParser(add_help=False)
    global_flags.add_argument("--yes", "-y", action="store_true",
                              help="自动确认所有提示")
    global_flags.add_argument("--non-interactive", "--no-input",
                              action="store_true", help="非交互模式")

    parser = argparse.ArgumentParser(
        prog="sw",
        description="Harness-Flow Agent Workflow CLI",
        add_help=False,  # 我们手动处理 help 以保持与旧版输出一致或使用自定义输出
        parents=[global_flags],
    )

    parser.add_argument("-h", "--help", action="store_true", help="显示帮助信息")

    # 让每个 add_parser() 自动继承全局标志
    class _SubParser(argparse.ArgumentParser):
        def __init__(self, **kwargs):
            kwargs.setdefault("parents", []).append(global_flags)
            super().__init__(**kwargs)

    subparsers = parser.add_subparsers(dest="command", parser_class=_SubParser)

    # help
    subparsers.add_parser("help", help="显示帮助信息")

    # init
    p_init = subparsers.add_parser("init", help="创建新任务")
    p_init.add_argument("--type", default="feature", help="任务类型 (feature/bugfix/...)")
    p_init.add_argument("--name", help="任务名称")
    p_init.add_argument("--session", help="Session ID")
    p_init.add_argument("--agent", help="指定 AI 角色/模型")
    p_init.add_argument("--context", help="需求描述文本")
    p_init.add_argument("--target", help="生成代码的目录 (默认: repo/<任务名>)")
    p_init.add_argument("--self", action="store_true", help="开发 Harness-Flow 自身 (target=.)")
    p_init.add_argument("--interactive", action="store_true", help="进入交互式创建模式")
    p_init.add_argument("--no-mock", action="store_true", help="使用真实 Agent（默认由 config.yaml 控制）")
    p_init.add_argument("--mock", action="store_true", help="使用 MockAgent（默认由 config.yaml 控制）")
    p_init.add_argument("--auto", action="store_true", help="自动推进阶段（覆写 config.yaml 的 auto_advance）")
    p_init.add_argument("--manual", action="store_true", help="每个阶段等待 /advance 手动推进")
    p_init.add_argument("--unattended", action="store_true",
                        help="无人值守：连 Agent 的提问也自动代答（默认提问仍交还用户）")

    # status
    p_status = subparsers.add_parser("status", help="显示任务状态")
    p_status.add_argument("--name", help="任务名称")

    # advance
    p_advance = subparsers.add_parser("advance", help="推进阶段")
    p_advance.add_argument("--name", help="任务名称")
    p_advance.add_argument("--no-next", action="store_true", help="不自动开始下一阶段")

    # list
    p_list = subparsers.add_parser("list", help="列出任务")
    p_list.add_argument("--trash", action="store_true", help="查看回收站")

    # remove
    p_remove = subparsers.add_parser("remove", help="移除任务到回收站")
    p_remove.add_argument("--name", help="任务名称")

    # remove-all（别名兼容驼峰写法）
    for _alias in ("remove-all", "removeall", "removeAll"):
        _p = subparsers.add_parser(_alias, help="移除所有任务到回收站")
        _p.add_argument("--purge", action="store_true",
                        help="连回收站一起物理删除（不可恢复）")

    # restore（resume 是别名 —— 「恢复」在本项目里只有回收站这一个含义）
    for _alias in ("restore", "resume"):
        _p = subparsers.add_parser(_alias, help="从回收站恢复任务")
        _p.add_argument("--name", help="任务名称")

    # purge-trash（别名兼容驼峰写法）
    for _alias in ("purge-trash", "purgetrash", "purgeTrash", "empty-trash"):
        subparsers.add_parser(_alias, help="清空回收站（物理删除，不可恢复）")

    # reap：清理测试残留（被 Ctrl+C 打断时自动收尾不会执行）
    subparsers.add_parser("reap", help="清理测试残留任务（e2e-* / web-* / pytest-*）")

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="启动 TUI 监控面板")
    p_monitor.add_argument("--name", help="任务名称")
    p_monitor.add_argument("--auto", action="store_true", help="自动推进阶段")
    p_monitor.add_argument("--manual", action="store_true", help="每个阶段等待 /advance 手动推进")
    p_monitor.add_argument("--unattended", action="store_true",
                           help="无人值守：连 Agent 的提问也自动代答")

    # answer
    p_answer = subparsers.add_parser("answer", help="回复 Agent 提问")
    p_answer.add_argument("--name", help="任务名称")
    p_answer.add_argument("--text", help="回复内容")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="部署应用")
    p_deploy.add_argument("--name", help="任务名称 (默认从 .sw-context 检测)")
    p_deploy.add_argument("--port", type=int, default=8000, help="首选端口 (默认 8000)")
    p_deploy.add_argument("--no-tunnel", action="store_true", help="跳过 Cloudflare Tunnel")
    p_deploy.add_argument("--agent", help="指定 Agent 类型/角色 (如 opencode, gemini)")

    # health
    p_health = subparsers.add_parser("health", help="启动服务健康监控")
    p_health.add_argument("--name", help="任务名称（默认从 .sw-context 检测）")
    p_health.add_argument("--interval", type=int, default=0, help="健康检查间隔秒数 (覆盖 health_config 默认值)")
    p_health.add_argument("--daemon", action="store_true", help="后台运行模式")

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="启动 Web Dashboard")
    p_dash.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    p_dash.add_argument("--port", default=8080, type=int, help="监听端口 (默认 8080)")

    p_test = subparsers.add_parser("test", help="运行端到端集成测试")
    p_test.add_argument("--name", help="指定测试任务名 (默认自动生成)")

    # state —— 供 hook 与外部工具读 JSON 状态源
    p_state = subparsers.add_parser("state", help="查询任务的门禁/路由状态")
    _state_sub = p_state.add_subparsers(dest="state_command")
    p_state_get = _state_sub.add_parser("get", help="打印某阶段的状态字段")
    p_state_get.add_argument("task", help="任务名称")
    p_state_get.add_argument("stage", help="阶段名（如 04-review）")
    p_state_get.add_argument("field", nargs="?", default="gate",
                             help="字段: gate | route（默认 gate）")
    p_state_get.add_argument("--json", action="store_true", help="输出 JSON 结构")

    # 兼容性处理：如果没有任何参数，打印 usage
    if len(sys.argv) < 2:
        cmd_usage()
        sys.exit(1)

    # 全局标志允许放在子命令前后，两处都要认。
    # 子命令 parser 也声明了同名标志，它的默认值 False 会**覆盖**主 parser 已经
    # 解析出的 True（argparse 的既有行为），所以不能只看最终结果 —— 用一个只认
    # 全局标志的 parser 单独扫一遍前置写法，再与子命令的结果取或。
    pre_args, _ = global_flags.parse_known_args()
    args = parser.parse_args()
    args.yes = args.yes or pre_args.yes
    args.non_interactive = args.non_interactive or pre_args.non_interactive

    if args.help or (args.command in ("help", "-h", "--help")):
        cmd_usage()
        return

    if args.yes:
        os.environ["SW_YES"] = "1"
    if args.non_interactive:
        os.environ["SW_NON_INTERACTIVE"] = "1"

    if not args.command:
        cmd_usage()
        return

    cmd = args.command

    # 执行命令映射
    if cmd == "init":
        # 兼容旧逻辑：如果没传 --name 且没传其他关键参数，自动开启 interactive
        # 这里判断逻辑稍作调整以适应 argparse
        init_args_passed = any([
            args.name, args.type != "feature", args.session, args.agent, args.context
        ])
        if not init_args_passed and not args.interactive:
            args.interactive = True
        cmd_init(args)

    elif cmd == "status":
        cmd_status(args)

    elif cmd == "advance":
        cmd_advance(args)

    elif cmd == "list":
        cmd_list(args)

    elif cmd == "remove":
        cmd_remove(args)

    elif cmd in ("remove-all", "removeall", "removeAll"):
        cmd_remove_all(args)

    elif cmd in ("restore", "resume"):
        cmd_restore(args)

    elif cmd in ("purge-trash", "purgetrash", "purgeTrash", "empty-trash"):
        cmd_purge_trash(args)

    elif cmd == "reap":
        cmd_reap(args)

    elif cmd == "monitor":
        cmd_monitor(args)

    elif cmd == "answer":
        cmd_answer(args)

    elif cmd == "deploy":
        cmd_deploy(args)

    elif cmd == "health":
        cmd_health(args)

    elif cmd == "dashboard":
        cmd_dashboard(args)

    elif cmd == "test":
        cmd_test(args)

    elif cmd == "state":
        if getattr(args, "state_command", None) != "get":
            cmd_usage()
            return 1
        # 退出码是 hook 的判定依据，必须原样交回 shell
        return cmd_state_get(args)

    else:
        cmd_usage()
        sys.exit(1)
