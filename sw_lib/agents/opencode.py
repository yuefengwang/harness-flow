"""OpenCode transport — HTTP API + server lifecycle.

Stripped to bare minimum: persistent server, HTTP session, send/receive.
Response processing, tool handling, context prep belong in layers above.
"""

import json, os, socket, subprocess, threading, time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from ..core.config import ROOT, CONFIG_DIR
from ..core.utils import sw_log
from .base import BaseAgent


class OpenCodeAgent(BaseAgent):
    """Minimal OpenCode transport agent — HTTP API only, no CLI fallback.

    Responsibilities:
    - Start/stop opencode serve process
    - Create HTTP session, send messages, receive responses
    - Dispatch response parts via callbacks (on_text, on_tool, on_step, etc.)

    Does NOT handle:
    - Context preparation / message formatting (→ PromptBuilder layer)
    - CLI fallback (→ separate transport if needed)
    - Tool execution / question-asking (→ Engine layer)
    - Output persistence (→ Engine layer)
    """

    def __init__(self, tui_callbacks, name, stage, stage_idx, model_name="opencode"):
        super().__init__(tui_callbacks, name, stage, stage_idx, model_name)
        self.running = False
        self.agent_proc = None

        self._server_proc: Optional[subprocess.Popen] = None
        self._server_port: Optional[int] = None
        self._server_url: Optional[str] = None
        self._http_session_id: Optional[str] = None

        self._env = self._load_env()

    # ── Environment ──

    def _load_env(self):
        import yaml
        env = os.environ.copy()
        for p in [CONFIG_DIR / "credentials.yaml", CONFIG_DIR / "config.yaml"]:
            if p.exists():
                try:
                    data = yaml.safe_load(open(p))
                    if not data:
                        continue
                    if p.name == "config.yaml" and "harness" in data:
                        creds = data.get("harness", {}).get("credentials", {})
                        for k, v in creds.items():
                            if str(k) not in env:  # shell env takes precedence
                                env[str(k)] = str(v)
                    elif p.name == "credentials.yaml":
                        for k, v in data.items():
                            if str(k) not in env:  # shell env takes precedence
                                env[str(k)] = str(v)
                except Exception:
                    continue
        cert = env.get("NODE_EXTRA_CA_CERTS")
        if cert:
            cf = Path(cert)
            if not cf.exists() or cf.stat().st_size == 0:
                env.pop("NODE_EXTRA_CA_CERTS", None)
        # Always remove: agent doesn't use Node.js TLS, only causes opencode serve crashes
        env.pop("NODE_EXTRA_CA_CERTS", None)
        return env

    # ── Server lifecycle ──

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _kill_orphaned_servers(self):
        import signal
        try:
            r = subprocess.run(["pgrep", "-f", "opencode serve"], capture_output=True,
                                text=True, timeout=5)
            if r.returncode != 0: return
            for pid_str in r.stdout.strip().split("\n"):
                pid = int(pid_str.strip())
                if pid == os.getpid(): continue
                try: os.kill(pid, signal.SIGKILL)
                except Exception: pass
        except Exception: pass

    def _start_server(self):
        self._kill_orphaned_servers()
        debug = True
        try:
            self._server_port = self._find_free_port()
            self._server_url = f"http://127.0.0.1:{self._server_port}"

            # Log env keys for debugging (without exposing secrets)
            if debug:
                for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                          "NODE_EXTRA_CA_CERTS", "GOOGLE_API_KEY"):
                    v = self._env.get(k, "")
                    self._add_log("sw", f"  env[{k}] = {'[set]' if v else '[unset]'}")

            self._server_proc = subprocess.Popen(
                ["opencode", "serve", "--port", str(self._server_port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                env=self._env, cwd=str(ROOT), text=True,
            )
            deadline = time.monotonic() + 30  # extended from 10 to 30s
            while time.monotonic() < deadline:
                if self._server_proc.poll() is not None:
                    stderr_out = self._server_proc.stderr.read() if self._server_proc.stderr else ""
                    self._add_log("error", f"opencode server exited prematurely")
                    if stderr_out:
                        self._add_log("error", f"stderr: {stderr_out[:500]}")
                    self._stop_server(); return
                try:
                    with urlopen(Request(f"{self._server_url}/global/health"), timeout=2) as r:
                        if r.status == 200:
                            self._add_log("sw", f"opencode server ready on port {self._server_port}")
                            return
                except (URLError, OSError): pass
                time.sleep(0.5)
            # Timeout — dump stderr
            stderr_out = ""
            if self._server_proc and self._server_proc.stderr:
                try:
                    stderr_out = self._server_proc.stderr.read()
                except Exception: pass
            self._add_log("error", "opencode server startup timed out")
            if stderr_out:
                self._add_log("error", f"stderr: {stderr_out[:500]}")
            self._stop_server()
        except FileNotFoundError:
            self._add_log("error", "opencode command not found")
            self._stop_server()
        except Exception as e:
            self._add_log("error", f"Failed to start opencode server: {e}")
            self._stop_server()

    def _stop_server(self):
        self._server_url = None; self._server_port = None
        if self._server_proc is None: return
        try:
            self._server_proc.terminate()
            try: self._server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired: self._server_proc.kill(); self._server_proc.wait(timeout=2)
        except Exception:
            try: self._server_proc.kill()
            except Exception: pass
        self._server_proc = None

    # ── HTTP session ──

    def _create_http_session(self):
        try:
            body = json.dumps({"title": f"agent-{self.name}"}).encode("utf-8")
            req = Request(f"{self._server_url}/session", data=body,
                           headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=30) as r:
                self._http_session_id = json.loads(r.read()).get("id")
            self._add_log("sw", f"opencode HTTP session: {self._http_session_id[:16]}...")
        except Exception as e:
            self._add_log("error", f"Failed to create HTTP session: {e}")
            self._http_session_id = None
            raise

    def _parse_model_param(self) -> Optional[Dict[str, str]]:
        if not self.model_name or self.model_name == "opencode":
            return None
        parts = self.model_name.split("/", 1)
        if len(parts) == 2:
            provider = parts[0]
            if provider == "opencode":
                return None  # use server default
            return {"providerID": provider, "modelID": parts[1]}
        return None

    def _send_http(self, message: str, is_continue: bool = False):
        """Send message via HTTP API. Returns response parts dict or None."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not self._http_session_id or not is_continue:
                    self._create_http_session()
                    is_continue = True
                body: Dict[str, Any] = {"parts": [{"type": "text", "text": message}]}
                model = self._parse_model_param()
                if model: body["model"] = model
                req = Request(
                    f"{self._server_url}/session/{self._http_session_id}/message",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                timeout = 180 if attempt == 0 else 180 * (attempt + 1)
                with urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read())
            except URLError as e:
                self._add_log("error", f"HTTP error (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                self._http_session_id = None; return None
            except json.JSONDecodeError:
                self._add_log("error", f"Non-JSON response (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1: time.sleep(2 ** attempt); continue
                self._http_session_id = None; return None
            except Exception as e:
                self._add_log("error", f"HTTP exception: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                self._http_session_id = None; return None
        return None

    def _dispatch_parts(self, parts: List[Dict[str, Any]]):
        """Iterate response parts and call appropriate callbacks.

        Callbacks used (all optional):
        - on_step_start()
        - on_text(text: str)
        - on_reasoning(text: str)
        - on_tool(part: dict)
        - on_step_finish(reason: str)
        """
        text_parts = []
        for part in parts:
            pt = part.get("type", "")
            if pt == "step-start":
                self.status = self.STATUS_ACTIVE
                if "on_step_start" in self.callbacks: self.callbacks["on_step_start"]()
            elif pt == "text":
                t = part.get("text", "")
                if t:
                    text_parts.append(t)
                    if "on_text" in self.callbacks: self.callbacks["on_text"](t)
            elif pt == "reasoning":
                t = part.get("text", "")
                if t and "on_reasoning" in self.callbacks: self.callbacks["on_reasoning"](t)
            elif pt in ("tool_use", "tool"):
                if "on_tool" in self.callbacks: self.callbacks["on_tool"](part)
            elif pt == "step-finish":
                reason = part.get("reason", "unknown")
                if reason == "stop": self.status = self.STATUS_IDLE
                if "on_step_finish" in self.callbacks: self.callbacks["on_step_finish"](reason)
        if text_parts:
            joined = "\n".join(text_parts)
            sw_log(self.name, f"complete reply ({len(joined)} chars)", "agent")
        sw_log(self.name, "opencode HTTP completed", "sw")

    # ── BaseAgent interface ──

    @property
    def is_active(self) -> bool:
        return self.running

    def start(self):
        if self.running: return
        self.running = True
        self.status = self.STATUS_IDLE
        if self._server_proc is None:
            self._start_server()

    def send(self, text: str, is_system: bool = False):
        """Send message to OpenCode. Blocks until response received.
        
        In HTTP mode, the server cannot handle interactive question tools.
        System messages are prefixed with a hint to avoid using question/ask_user.
        """
        if not self.running:
            if is_system: self.start()
            else: return
        if not self._server_url:
            self._add_log("error", "No opencode server running")
            self.status = self.STATUS_ERROR; return

        if is_system:
            text += "\n\n[SYSTEM] Do NOT use question or ask_user tools. "
            text += "If you need to ask something, just output it as text. "
            text += "Do NOT wait for any interactive input."

        self.status = self.STATUS_CONNECTING
        result = self._send_http(text, is_continue=bool(self._http_session_id))
        if result:
            self._dispatch_parts(result.get("parts", []))
        else:
            self.status = self.STATUS_ERROR

    def shutdown(self):
        self.running = False
        self.status = self.STATUS_IDLE
        if self._server_url and self._http_session_id:
            try: urlopen(Request(f"{self._server_url}/session/{self._http_session_id}/abort", method="POST"), timeout=5)
            except Exception: pass
        self._http_session_id = None
        self._stop_server()
        self._kill_orphaned_servers()

    def restart(self):
        self.shutdown()
        self._http_session_id = None
        self.start()

    def inject_context(self):
        self._add_log("system", "Context injected via initial message")

    def reader_loop(self):
        pass  # no async reader needed with synchronous HTTP
