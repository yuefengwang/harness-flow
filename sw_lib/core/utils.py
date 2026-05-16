"""sw_lib.utils — helper functions (colors, logging, prompts)"""

import os
import sys
from datetime import datetime

from .config import TASKS


def green(s):
    return f"\033[0;32m{s}\033[0m"


def red(s):
    return f"\033[0;31m{s}\033[0m"


def yellow(s):
    return f"\033[1;33m{s}\033[0m"


def blue(s):
    return f"\033[0;34m{s}\033[0m"


def ok(msg):
    print(f"{green('[✓]')} {msg}")


def err(msg):
    print(f"{red('[✗]')} {msg}", file=sys.stderr)


def warn(msg):
    print(f"{yellow('[!]')} {msg}")


def hdr(msg):
    print(f"\n{blue('━━━ ' + msg + ' ━━━')}")


def die(msg):
    err(msg)
    sys.exit(1)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def sanitize_name(name: str) -> str:
    """清理任务名: 移除 macOS 下 Python 3.9 的 surrogate 字符, 只保留安全字符"""
    # 处理 surrogate escape (macOS Python 3.9 已知问题)
    name = name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    # 只保留 ASCII 字母数字 + 短横 + 下划线 + 点
    safe = "".join(c for c in name if c.isascii() and (c.isalnum() or c in "-_."))
    return safe.strip("-_. ") or "task"


def prompt(prompt_text: str, default: str = "") -> str:
    """交互式输入，带默认值提示"""
    if default:
        sys.stdout.write(f"  {blue('→')} {prompt_text} [{default}]: ")
    else:
        sys.stdout.write(f"  {blue('→')} {prompt_text}: ")
    sys.stdout.flush()
    try:
        response = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        response = ""
    # macOS Python 3.9 surrogate workaround
    response = response.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return response if response else default


def prompt_yn(prompt_text: str, default: str = "y") -> bool:
    """交互式 Y/n 确认"""
    if os.environ.get("SW_YES") == "1":
        return True
    if os.environ.get("SW_NON_INTERACTIVE") == "1":
        return default.lower() in ("y", "yes")

    hint = "Y/n" if default == "y" else "y/N"
    sys.stdout.write(f"  {blue('→')} {prompt_text} [{hint}]: ")
    sys.stdout.flush()
    try:
        resp = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        resp = ""
    resp = resp if resp else default
    return resp in ("y", "yes")


def sw_log(name: str, message: str, source: str = "sw"):
    """向任务日志文件追加一行事件（仅在目录已存在时写入）"""
    log_dir = TASKS / name
    if not log_dir.is_dir():
        return  # 目录不存在（如已移除），跳过
    log_file = log_dir / ".log"
    ts = now()
    # 清理 surrogate 字符，防止写入时编码崩溃
    safe_msg = message.encode("utf-8", errors="replace").decode("utf-8")
    line = f"[{ts}] {source:<6}| {safe_msg}\n"
    with open(log_file, "a") as f:
        f.write(line)
