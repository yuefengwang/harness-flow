"""
sw_lib.core.health — 部署后健康监控模块 (HealthMonitor).

核心职责：
1. TCP 端口侦听检查 + PID 进程存活检查
2. 连续失败计数 → 达到阈值触发重新部署
3. 自动/手动模式切换 + 熔断保护
"""

import os
import socket
import time
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from .config import TASKS
from .service import _service
from .deploy_orchestrator import DeployOrchestrator
from .utils import now, sw_log
from ..web.cloudflared import stop_tunnel


@dataclass
class HealthConfig:
    """健康检查配置"""
    enabled: bool = True
    check_interval: int = 10        # 检查间隔秒数
    failure_threshold: int = 3      # 连续失败触发阈值
    auto_redeploy: bool = False     # 是否自动重新部署
    max_redeploys: int = 5          # 窗口内最大重部署次数
    redeploy_window_sec: int = 300  # 熔断统计窗口（秒）


class HealthMonitor:
    """部署后健康监控器。

    通过 TCP 端口侦听、PID 进程存活、可选的 HTTP URL 三维度检查服务健康状态。
    连续失败达到阈值时，根据配置决定自动重新部署或标记失败。
    """

    def __init__(
        self,
        name: str,
        target_dir: str,
        port: int,
        config: Optional[HealthConfig] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        check_url: str = "",
    ):
        self.name = name
        self.target_dir = target_dir
        self.port = port
        self.config = config or HealthConfig()
        self.log_callback = log_callback
        self.check_url = check_url

        self._stop_event = threading.Event()
        self._failure_count = 0
        self._on_redeploy_timestamps: list[float] = []

    # ── 公开接口 ──

    def run(self):
        """阻塞式健康检查主循环，直到 stop() 被调用。"""
        deploy_url = self._read_deploy_url()
        port = self._resolve_port()
        url_info = f", url={deploy_url}" if deploy_url.startswith("http") else ""
        self._log(f"HealthMonitor started (interval={self.config.check_interval}s, "
                  f"threshold={self.config.failure_threshold}, "
                  f"auto_redeploy={self.config.auto_redeploy}{url_info})")
        self._log(f"Monitoring port={port}, deploy_url={deploy_url or '(none)'}")

        while not self._stop_event.is_set():
            healthy = self._check_tcp() and self._check_pid() and self._check_url()

            if healthy:
                if self._failure_count > 0:
                    self._log("Health check passed, resetting failure counter")
                self._failure_count = 0
            else:
                self._failure_count += 1
                self._log(f"Health check failed ({self._failure_count}/{self.config.failure_threshold})")

                if self._failure_count >= self.config.failure_threshold:
                    self._trigger_redeploy()
                    self._failure_count = 0  # 触发后重置计数器

            self._stop_event.wait(self.config.check_interval)

        self._log("HealthMonitor stopped")

    def stop(self):
        self._log("Stopping HealthMonitor...")
        self._stop_event.set()

    # ── 健康检查 ──

    def _read_deploy_url(self) -> str:
        """实时从 state 读取当前 deploy_url"""
        try:
            st = _service.get_task_state(self.name)
            return st.get("deploy_url", "")
        except Exception:
            return ""

    def _resolve_port(self) -> int:
        """从 state 的 deploy_url 解析端口，不存在则回退到初始 self.port"""
        deploy_url = self._read_deploy_url()
        if deploy_url and ":" in deploy_url:
            try:
                return int(deploy_url.rsplit(":", 1)[-1].rstrip("/"))
            except (ValueError, IndexError):
                pass
        return self.port

    def _check_tcp(self) -> bool:
        port = self._resolve_port()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def _check_pid(self) -> bool:
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False

    def _read_pid(self) -> Optional[int]:
        pid_file = TASKS / self.name / ".deploy.pid"
        if not pid_file.exists():
            return None
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def _check_url(self) -> bool:
        deploy_url = self._read_deploy_url()
        if not deploy_url.startswith("http"):
            return True
        try:
            req = urllib.request.Request(deploy_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False

    # ── 重新部署 ──

    def _trigger_redeploy(self):
        """达到失败阈值后的处理入口。"""
        self._log(f"Failure threshold reached ({self.config.failure_threshold})")

        if self.config.auto_redeploy:
            if self._check_circuit_breaker():
                self._log("Circuit breaker active — auto-redeploy blocked")
                try:
                    st = _service.get_task_state(self.name)
                    st["health_status"] = "circuit_broken"
                    st["updated_at"] = now()
                    _service._write_state_safe(self.name, st)
                except Exception:
                    pass
                return

            self._log("Auto-redeploy triggered")
            self._redeploy()
        else:
            self._log("Manual mode — marking health_failed (user intervention required)")
            try:
                st = _service.get_task_state(self.name)
                st["health_status"] = "health_failed"
                if st.get("deploy_status") == "deployed":
                    st["deploy_status"] = "deployed_unhealthy"
                st["updated_at"] = now()
                _service._write_state_safe(self.name, st)
            except Exception:
                pass

    def _redeploy(self):
        """执行完整重新部署流程。"""
        self._log("Starting redeploy...")

        # 1. 停止旧 tunnel
        try:
            stop_tunnel(self.name)
            self._log("Old tunnel stopped")
        except Exception as e:
            self._log(f"Warning: tunnel stop error: {e}")

        # 2. 清理旧 PID
        pid_file = TASKS / self.name / ".deploy.pid"
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                os.kill(old_pid, 15)  # SIGTERM
                self._log(f"Old process {old_pid} terminated")
            except (ValueError, ProcessLookupError, OSError):
                pass
            finally:
                pid_file.unlink(missing_ok=True)

        # 3. 确定端口（尽量复用原端口，被占用则随机）
        new_port = self._find_free_port(self.port)
        if new_port != self.port:
            self._log(f"Preferred port {self.port} unavailable, using {new_port}")
        else:
            self._log(f"Using preferred port {self.port}")

        # 4. 重新部署
        try:
            orchestrator = DeployOrchestrator(
                name=self.name,
                target_dir=self.target_dir,
                port=new_port,
                log_callback=self._log,
            )
            result = orchestrator.run()
            self._log(f"Redeploy complete: {result.service_url}")
        except Exception as e:
            self._log(f"Redeploy failed: {e}")
            try:
                st = _service.get_task_state(self.name)
                st["deploy_status"] = "deploy_failed"
                st["health_status"] = "redeploy_failed"
                st["updated_at"] = now()
                _service._write_state_safe(self.name, st)
            except Exception:
                pass

        # 5. 记录此次重部署时间戳（无论成功失败都计入熔断统计）
        self._on_redeploy_timestamps.append(time.time())

    # ── 熔断保护 ──

    def _check_circuit_breaker(self) -> bool:
        """检查熔断：清理过时时间戳，判断是否超限。

        Returns:
            True 表示熔断已激活（阻止后续 redeploy），False 表示可继续。
        """
        now_ts = time.time()
        window_start = now_ts - self.config.redeploy_window_sec

        # 清理窗口外的时间戳
        self._on_redeploy_timestamps = [
            ts for ts in self._on_redeploy_timestamps if ts > window_start
        ]

        if len(self._on_redeploy_timestamps) >= self.config.max_redeploys:
            self._log(
                f"CIRCUIT BROKEN: {len(self._on_redeploy_timestamps)} redeploys "
                f"in {self.config.redeploy_window_sec}s window "
                f"(max={self.config.max_redeploys})"
            )
            return True

        return False

    # ── 工具方法 ──

    @staticmethod
    def _find_free_port(preferred: int) -> int:
        # 先检查 preferred 是否可用
        if preferred > 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", preferred))
                    return preferred
                except OSError:
                    pass

        # preferred 不可用，获取随机端口
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _log(self, msg: str):
        timestamp = now()
        formatted = f"[{timestamp}] [HealthMonitor/{self.name}] {msg}"
        if self.log_callback:
            self.log_callback(formatted)
        try:
            sw_log(self.name, msg, "health")
        except Exception:
            pass
