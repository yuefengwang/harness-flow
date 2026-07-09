"""OpenCode transport — direct HTTP via requests.

Uses requests library for server communication due to httpx compatibility
issues with opencode serve. The API contract is identical to the SDK.
"""

import os
import signal
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..core.config import ROOT, CONFIG_DIR
from ..core.utils import sw_log
from .base import BaseAgent


class OpenCodeAgent(BaseAgent):
    """OpenCode transport via direct HTTP requests.

    Responsibilities:
    - Start/stop opencode serve process
    - Manage HTTP session lifetime (session create → chat → abort)
    - Dispatch response parts via callbacks (on_text, on_tool, on_step, etc.)

    Does NOT handle:
    - Context preparation / message formatting (→ PromptBuilder layer)
    - Tool execution / question-asking (→ Engine layer)
    - Output persistence (→ Engine layer)
    """

    CHAT_TIMEOUT = 1800.0  # long-running local agent responses can exceed 15 min
    HEALTH_TIMEOUT = 2.0
    DEFAULT_PORT = 65535

    def __init__(self, tui_callbacks, name, stage, stage_idx, model_name="opencode"):
        super().__init__(tui_callbacks, name, stage, stage_idx, model_name)
        self.running = False
        self.agent_proc = None

        self._server_proc: Optional[subprocess.Popen] = None
        self._server_port: Optional[int] = None
        self._server_url: Optional[str] = None
        self._session_id: Optional[str] = None

        self._env = self._load_env()

    # ── Environment ──

    def _load_env(self):
        """Load credentials from yaml, stripping unsafe env vars."""
        import yaml
        env = os.environ.copy()
        for p in [CONFIG_DIR / "credentials.yaml", CONFIG_DIR / "config.yaml"]:
            if p.exists():
                try:
                    data = yaml.safe_load(open(p))
                    if not data:
                        continue
                    if p.name == "config.yaml" and "harness" in data:
                        creds = data["harness"].get("credentials", {})
                        for k, v in creds.items():
                            if str(k) not in env:
                                env[str(k)] = str(v)
                    elif p.name == "credentials.yaml":
                        for k, v in data.items():
                            if str(k) not in env:
                                env[str(k)] = str(v)
                except Exception:
                    continue
        # NODE_EXTRA_CA_CERTS causes opencode serve crashes on macOS
        env.pop("NODE_EXTRA_CA_CERTS", None)
        return env

    # ── Server lifecycle ──

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @staticmethod
    def _cleanup_orphans():
        """Kill any leftover opencode serve processes from prior runs."""
        try:
            r = subprocess.run(
                ["pgrep", "-f", "opencode serve"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return
            for pid_str in r.stdout.strip().split("\n"):
                pid = int(pid_str.strip())
                if pid == os.getpid():
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
        except Exception:
            pass

    def _start_server(self):
        """Start opencode serve, wait for healthy via HTTP."""
        if self._server_proc is not None:
            return
        self._cleanup_orphans()
        port = self.DEFAULT_PORT
        try:
            proc = subprocess.Popen(
                ["opencode", "serve", "--port", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._env, cwd=str(ROOT), text=True,
            )
        except FileNotFoundError:
            self._add_log("error", "opencode command not found in PATH")
            return

        self._server_proc = proc
        self._server_port = port
        self._server_url = f"http://127.0.0.1:{port}"

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self._add_log("error", "opencode server exited prematurely (check PATH / opencode install)")
                self._stop_server()
                return
            try:
                r = requests.post(
                    f"{self._server_url}/session",
                    timeout=self.HEALTH_TIMEOUT,
                )
                if r.status_code == 200:
                    self._add_log("sw", f"opencode server ready on port {port}")
                    return
            except requests.RequestException:
                time.sleep(0.5)

        self._add_log("error", "opencode server startup timed out (30s)")
        self._stop_server()

    def _stop_server(self):
        self._session_id = None
        self._server_port = None
        self._server_url = None
        proc = self._server_proc
        self._server_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ── Model parsing ──

    def _parse_model(self) -> Tuple[str, str]:
        """Parse model_name into (provider_id, model_id).

        Accepts formats:
          - "opencode/deepseek-v4-flash-free"  → ("opencode", "deepseek-v4-flash-free")
          - "opencode" or ""                    → ("opencode", "deepseek-v4-flash-free")  (fallback)
        """
        name = (self.model_name or "").strip()
        if not name or name == "opencode":
            return "opencode", "deepseek-v4-flash-free"
        parts = name.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "opencode", name

    # ── Message exchange ──

    def _dispatch_parts(self, parts: List[Dict[str, Any]]):
        """Iterate response parts and call appropriate callbacks."""
        text_parts = []
        try:
            for part in parts or []:
                if not isinstance(part, dict):
                    sw_log(self.name, f"skipped non-dict part: {type(part).__name__}", "sw")
                    continue
                pt = part.get("type", "")
                if pt == "step-start":
                    self.status = self.STATUS_ACTIVE
                    cb = self.callbacks.get("on_step_start")
                    if cb:
                        cb()
                elif pt == "text":
                    t = part.get("text", "")
                    if t:
                        text_parts.append(t)
                        cb = self.callbacks.get("on_text")
                        if cb:
                            cb(t)
                elif pt == "reasoning":
                    t = part.get("text", "")
                    if t:
                        cb = self.callbacks.get("on_reasoning")
                        if cb:
                            cb(t)
                elif pt in ("tool_use", "tool"):
                    cb = self.callbacks.get("on_tool")
                    if cb:
                        cb(part)
                elif pt == "step-finish":
                    reason = part.get("reason", "unknown")
                    if reason == "stop":
                        self.status = self.STATUS_IDLE
                    cb = self.callbacks.get("on_step_finish")
                    if cb:
                        cb(reason)
        except Exception as e:
            sw_log(self.name, f"error dispatching parts: {e}", "error")

        if text_parts:
            joined = "\n".join(text_parts)
            sw_log(self.name, f"complete reply ({len(joined)} chars)", "agent")
        else:
            ptypes = [p.get("type", "unknown") for p in (parts or [])]
            sw_log(self.name, f"no text in response. parts: {ptypes}", "sw")
        sw_log(self.name, "opencode SDK completed", "sw")

    def _send_via_http(self, text: str) -> List[Dict[str, Any]]:
        """Send message via HTTP. Returns list of response parts."""
        provider_id, model_id = self._parse_model()

        if not self._session_id:
            r = requests.post(f"{self._server_url}/session", timeout=10)
            r.raise_for_status()
            self._session_id = r.json()["id"]
            self._add_log("sw", f"opencode session: {self._session_id[:16]}...")

        r = requests.post(
            f"{self._server_url}/session/{self._session_id}/message",
            json={
                "providerID": provider_id,
                "modelID": model_id,
                "parts": [{"type": "text", "text": text}],
            },
            timeout=self.CHAT_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("parts", [])

    # ── BaseAgent interface ──

    @property
    def is_active(self) -> bool:
        return self.running

    def start(self):
        if self.running:
            return
        self.running = True
        self.status = self.STATUS_IDLE
        if self._server_proc is None:
            self._start_server()

    def send(self, text: str, is_system: bool = False):
        """Send message to OpenCode. Blocks until response received."""
        if not self.running:
            if is_system:
                self.start()
            else:
                return
        if not self._server_url:
            self._add_log("error", "No opencode server connected")
            self.status = self.STATUS_ERROR
            return

        if is_system:
            text += (
                "\n\n[SYSTEM] You MAY ask questions to clarify requirements. "
                "Ask one question at a time. Incorporate the user's answers "
                "into your analysis before finalizing."
            )

        self.status = self.STATUS_CONNECTING
        try:
            parts = self._send_via_http(text)
            self._dispatch_parts(parts)
            self.status = self.STATUS_IDLE
        except requests.Timeout as e:
            self._add_log("error", f"HTTP timeout: {e}")
            self.status = self.STATUS_ERROR
        except requests.ConnectionError as e:
            self._add_log("error", f"HTTP connection error: {e}")
            self.status = self.STATUS_ERROR
        except requests.HTTPError as e:
            self._add_log("error", f"OpenCode API error (HTTP {e.response.status_code}): {e}")
            self.status = self.STATUS_ERROR
        except Exception as e:
            self._add_log("error", f"HTTP send failed: {e}")
            self.status = self.STATUS_ERROR

        cb = self.callbacks.get("on_complete")
        if cb:
            cb()

    def shutdown(self):
        self.running = False
        self.status = self.STATUS_IDLE

        if self._server_url and self._session_id:
            try:
                requests.post(
                    f"{self._server_url}/session/{self._session_id}/abort",
                    timeout=10,
                )
            except Exception:
                pass
        self._session_id = None
        self._stop_server()
        self._cleanup_orphans()

    def restart(self):
        self.shutdown()
        self.start()

    def inject_context(self):
        self._add_log("system", "Context injected via initial message")

    def reader_loop(self):
        pass  # no async reader needed with synchronous HTTP
