"""OpenCode transport — 隔离 opencode 私有 HTTP 协议。

这是「选项 A 重构」的核心解耦层：所有与 opencode serve 私有 API 耦合的细节
（端口探测、进程生命周期、/session 端点、parts 协议字段名）**只出现在本文件**。
上层 OpenCodeAgent 不再直接 import requests / subprocess，只消费本层暴露的
结构化接口（create_session / send_message / abort_session）。

opencode 升级改协议时，只需修改本文件，不波及 agent 业务逻辑与测试。
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

import requests

from .protocol import AgentMessage, ToolCall


class OpenCodeTransportError(RuntimeError):
    """Transport 层错误（连接/超时/协议异常）。"""


class OpenCodeTransport:
    """管理 opencode serve 进程生命周期与一次会话的 HTTP 通信。

    职责边界（严格只做"通道"）：
      - 启动/停止 opencode serve，探测空闲端口
      - 创建/复用/中止 session
      - 把 HTTP 响应原样转成 protocol.AgentMessage（不解读业务语义）
    """

    CHAT_TIMEOUT = 1800.0          # 单轮对话超时（秒）
    HEALTH_TIMEOUT = 10.0          # 健康检查超时
    ABORT_TIMEOUT = 10.0           # 中止会话超时
    STARTUP_POLL_INTERVAL = 0.3    # 启动轮询间隔
    STARTUP_MAX_RETRIES = 50       # 启动最大重试次数

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._proc: Optional[subprocess.Popen] = None
        self._port: Optional[int] = None
        self._server_url: Optional[str] = None
        self._session_id: Optional[str] = None
        self._model: str = "opencode/deepseek-v4-flash-free"

    # ── 公开属性 ──
    @property
    def server_url(self) -> Optional[str]:
        return self._server_url

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def port(self) -> Optional[int]:
        return self._port

    def set_model(self, model: str) -> None:
        self._model = model

    # ── 生命周期 ──

    def start(self) -> str:
        """启动 opencode serve，返回 server_url。"""
        self._cleanup_orphans()
        port = self._find_free_port()
        cmd = ["opencode", "serve", "--address", f"127.0.0.1:{port}", "--disable-network"]
        if self.verbose:
            proc = subprocess.Popen(cmd)
        else:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        self._proc = proc
        self._port = port
        self._server_url = f"http://127.0.0.1:{port}"

        # 轮询健康检查直到 /session 可创建
        for _ in range(self.STARTUP_MAX_RETRIES):
            try:
                requests.post(f"{self._server_url}/session", timeout=self.HEALTH_TIMEOUT)
                return self._server_url
            except requests.RequestException:
                if proc.poll() is not None:
                    raise OpenCodeTransportError(
                        f"opencode serve 进程已退出 (code={proc.returncode})"
                    )
                time.sleep(self.STARTUP_POLL_INTERVAL)
        raise OpenCodeTransportError("opencode serve 启动超时，无法建立会话")

    def shutdown(self) -> None:
        """停止 serve 进程并清理会话。"""
        if self._session_id and self._server_url:
            try:
                requests.post(
                    f"{self._server_url}/session/{self._session_id}/abort",
                    timeout=self.ABORT_TIMEOUT,
                )
            except requests.RequestException:
                pass
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._session_id = None
        self._server_url = None
        self._port = None

    # ── 会话通信 ──

    def ensure_session(self) -> str:
        """创建（或复用已存在）session，返回 session_id。"""
        if self._session_id:
            return self._session_id
        if not self._server_url:
            raise OpenCodeTransportError("transport 未启动，无法创建会话")
        resp = requests.post(f"{self._server_url}/session", timeout=self.HEALTH_TIMEOUT)
        resp.raise_for_status()
        self._session_id = resp.json().get("id")
        return self._session_id

    def send_message(self, text: str) -> AgentMessage:
        """向当前 session 发送一条消息，返回结构化 AgentMessage。"""
        if not self._server_url:
            raise OpenCodeTransportError("transport 未连接，无法发送消息")
        sid = self.ensure_session()
        url = f"{self._server_url}/session/{sid}/message"
        payload = {
            "text": text,
            "model": self._model,
            "history": "replace",
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.CHAT_TIMEOUT)
            resp.raise_for_status()
        except requests.Timeout:
            raise OpenCodeTransportError(f"HTTP timeout: {url}")
        except requests.ConnectionError as e:
            raise OpenCodeTransportError(f"HTTP connection error: {e}")
        return self._to_message(resp.json())

    def abort_session(self) -> None:
        if self._server_url and self._session_id:
            try:
                requests.post(
                    f"{self._server_url}/session/{self._session_id}/abort",
                    timeout=self.ABORT_TIMEOUT,
                )
            except requests.RequestException:
                pass
        self._session_id = None

    # ── 协议适配（私有，唯一解析 opencode parts 的地方）──

    @staticmethod
    def _to_message(raw: Dict[str, Any]) -> AgentMessage:
        """把 opencode 原始响应转成与具体实现无关的 AgentMessage。"""
        parts = raw.get("parts", [])
        msg = AgentMessage()
        for part in parts:
            ptype = part.get("type")
            if ptype == "step-start":
                msg.step_start = True
            elif ptype == "step-finish":
                msg.step_finish = True
                msg.finish_reason = part.get("reason", "stop")
            elif ptype == "text":
                msg.text_parts.append(part.get("text", ""))
            elif ptype == "reasoning":
                msg.reasoning_parts.append(part.get("text", ""))
            elif ptype == "tool":
                msg.tool_calls.append(
                    ToolCall(name=part.get("name", ""), input=part.get("input", {}))
                )
        return msg

    # ── 进程/端口工具（私有）──

    def _cleanup_orphans(self) -> None:
        """清理上一次未正常退出的 opencode serve 进程。"""
        try:
            out = subprocess.run(
                ["pgrep", "-f", "opencode serve"],
                capture_output=True, text=True,
            ).stdout.strip()
            for pid in out.splitlines():
                pid = pid.strip()
                if pid.isdigit():
                    try:
                        os.kill(int(pid), 9)
                    except Exception:
                        pass
        except Exception:
            pass

    def _find_free_port(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port
