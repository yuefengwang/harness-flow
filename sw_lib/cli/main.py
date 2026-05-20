"""
sw_lib.main — 命令行入口与指令分发。

该模块负责解析命令行参数并将其路由到对应的业务指令函数。
使用 argparse 提供规范的子命令支持。
"""

import argparse
import os
import sys
from types import SimpleNamespace
from typing import List, Optional

from .commands import (
    cmd_init, cmd_status, cmd_advance, cmd_resume,
    cmd_list, cmd_remove, cmd_restore, cmd_answer, cmd_monitor,
    cmd_dashboard, cmd_usage,
)


def main():
    parser = argparse.ArgumentParser(
        prog="sw",
        description="Harness-Flow Agent Workflow CLI",
        add_help=False  # 我们手动处理 help 以保持与旧版输出一致或使用自定义输出
    )

    # 全局标志
    parser.add_argument("--yes", "-y", action="store_true", help="自动确认所有提示")
    parser.add_argument("--non-interactive", "--no-input", action="store_true", help="非交互模式")
    parser.add_argument("-h", "--help", action="store_true", help="显示帮助信息")

    subparsers = parser.add_subparsers(dest="command")

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

    # status
    p_status = subparsers.add_parser("status", help="显示任务状态")
    p_status.add_argument("--name", help="任务名称")

    # advance
    p_advance = subparsers.add_parser("advance", help="推进阶段")
    p_advance.add_argument("--name", help="任务名称")
    p_advance.add_argument("--no-next", action="store_true", help="不自动开始下一阶段")

    # resume
    p_resume = subparsers.add_parser("resume", help="恢复任务")
    p_resume.add_argument("--name", help="任务名称")

    # list
    p_list = subparsers.add_parser("list", help="列出任务")
    p_list.add_argument("--trash", action="store_true", help="查看回收站")

    # remove
    p_remove = subparsers.add_parser("remove", help="移除任务到回收站")
    p_remove.add_argument("--name", help="任务名称")

    # restore
    p_restore = subparsers.add_parser("restore", help="从回收站恢复任务")
    p_restore.add_argument("--name", help="任务名称")

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="启动 TUI 监控面板")
    p_monitor.add_argument("--name", help="任务名称")

    # answer
    p_answer = subparsers.add_parser("answer", help="回复 Agent 提问")
    p_answer.add_argument("--name", help="任务名称")
    p_answer.add_argument("--text", help="回复内容")

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="启动 Web Dashboard")
    p_dash.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    p_dash.add_argument("--port", default=8080, type=int, help="监听端口 (默认 8080)")

    # 兼容性处理：如果没有任何参数，打印 usage
    if len(sys.argv) < 2:
        cmd_usage()
        sys.exit(1)

    # 预处理全局标志（允许放在命令前后）
    # argparse 默认支持命令前后的可选参数，但我们要设置环境变量以供其他模块使用
    args, unknown = parser.parse_known_args()

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

    elif cmd == "resume":
        cmd_resume(args)

    elif cmd == "list":
        cmd_list(args)

    elif cmd == "remove":
        cmd_remove(args)

    elif cmd == "restore":
        cmd_restore(args)

    elif cmd == "monitor":
        cmd_monitor(args)

    elif cmd == "answer":
        cmd_answer(args)

    elif cmd == "dashboard":
        cmd_dashboard(args)

    else:
        cmd_usage()
        sys.exit(1)
