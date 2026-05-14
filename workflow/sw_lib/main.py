"""sw_lib.main — argument parsing and command routing"""

import sys
from types import SimpleNamespace

from .commands import (
    cmd_init, cmd_status, cmd_advance, cmd_next, cmd_resume,
    cmd_list, cmd_remove, cmd_restore, cmd_answer, cmd_monitor, cmd_usage,
)


def parse_args(argv, specs):
    """手动解析参数（兼容 --key=value 和 --key value）"""
    result = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if "=" in a and a.startswith("--"):
            k, v = a.split("=", 1)
            k = k[2:].replace("-", "_")
            result[k] = v
        elif a in specs:
            k = a[2:].replace("-", "_")
            spec = specs[a]
            if spec.get("type") == bool:
                result[k] = True
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                i += 1
                result[k] = argv[i]
        else:
            # positional / unknown
            pass
        i += 1
    return result


def main():
    # 无参数 → usage
    if len(sys.argv) < 2:
        cmd_usage()
        sys.exit(1)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    # 简单路由
    if cmd in ("-h", "--help", "help"):
        cmd_usage()
        return

    if cmd == "init":
        flags = {"--type": {}, "--name": {}, "--session": {}, "--agent": {},
                 "--context": {}, "--interactive": {"type": bool}}
        args = parse_args(rest, flags)
        args = SimpleNamespace(
            type=args.get("type", ""),
            name=args.get("name", ""),
            session=args.get("session", ""),
            agent=args.get("agent", ""),
            context=args.get("context", ""),
            interactive=args.get("interactive", False),
        )
        # 如果没传任何参数，自动进入交互模式
        if not rest:
            args.interactive = True
        cmd_init(args)

    elif cmd == "status":
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_status(args)

    elif cmd == "advance":
        flags = {"--name": {}, "--force": {"type": bool}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(
            name=kwargs.get("name", ""),
            force=kwargs.get("force", False))
        cmd_advance(args)

    elif cmd == "next":
        flags = {"--name": {}, "--agent": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(
            name=kwargs.get("name", ""),
            agent=kwargs.get("agent", ""))
        cmd_next(args)

    elif cmd == "resume":
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_resume(args)

    elif cmd == "list":
        flags = {"--trash": {"type": bool}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(trash=kwargs.get("trash", False))
        cmd_list(args)

    elif cmd == "remove":
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_remove(args)

    elif cmd == "restore":
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_restore(args)

    elif cmd == "monitor":
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_monitor(args)

    elif cmd == "answer":
        flags = {"--name": {}, "--text": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(
            name=kwargs.get("name", ""),
            text=kwargs.get("text", ""))
        cmd_answer(args)

    else:
        cmd_usage()
        sys.exit(1)
