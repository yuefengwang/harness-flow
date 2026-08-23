"""
sw_lib.web.cloudflared — Cloudflare Tunnel 管理

为已部署的应用自动创建 trycloudflare.com 公网穿透地址。
每个 tunnel 的生命周期与对应任务绑定。
"""
import os
import re
import signal
import subprocess
import time
from typing import Optional
from sw_lib.core.config import TASKS


TIMEOUT_SEC = 30
URL_PATTERN = re.compile(r'https://[a-zA-Z0-9.-]+\.trycloudflare\.com')


def _tunnel_files(task_name: str):
    task_dir = TASKS / task_name
    return (
        task_dir / ".cloudflared.pid",
        task_dir / ".cloudflared.url",
    )


def _cloudflared_available() -> bool:
    try:
        subprocess.run(
            ["cloudflared", "--version"],
            capture_output=True, timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def start_tunnel(port: int, task_name: str) -> Optional[str]:
    """
    为指定端口启动 Cloudflare Tunnel，返回公网 URL。
    失败时返回 None。
    """
    if not _cloudflared_available():
        return None

    pid_file, url_file = _tunnel_files(task_name)
    stop_tunnel(task_name)

    url_file.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--protocol", "http2",
         "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    pid_file.write_text(str(proc.pid))

    deadline = time.time() + TIMEOUT_SEC
    url = None

    try:
        for line in proc.stdout:
            if proc.poll() is not None:
                break
            match = URL_PATTERN.search(line)
            if match:
                url = match.group(0)
                url_file.write_text(url)
                break
            if time.time() > deadline:
                break
    except Exception:
        pass

    if url is None:
        stop_tunnel(task_name)

    return url


def stop_tunnel(task_name: str):
    pid_file, url_file = _tunnel_files(task_name)

    if url_file.exists():
        url_file.unlink(missing_ok=True)

    if not pid_file.exists():
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, OSError):
        pass
    finally:
        pid_file.unlink(missing_ok=True)
