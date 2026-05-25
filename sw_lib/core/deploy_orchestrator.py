import json
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

from ..agents.base import AgentFactory
from ..core.config import resolve_deploy_agent_type, resolve_deploy_agent_model, TASKS
from ..core.utils import now
from ..core.service import _service
from ..web.cloudflared import start_tunnel, stop_tunnel
from ..tools.toolbox import ListFilesTool, ReadFileTool, WriteFileTool, RunCommandTool


@dataclass
class DeployResult:
    service_url: str = ""
    pid: int = 0
    tunnel_url: str = ""


DEPLOY_SYSTEM_PROMPT = """\
You are a DevOps engineer. Deploy {target_dir} to http://localhost:{port}

Available tools:
- list_files(path: str) — List files in a directory
- read_file(file_path: str) — Read file contents
- write_file(file_path: str, content: str) — Write file contents
- run_command(command: str, cwd: str = None, timeout: int = 300) — Execute shell commands

Rules:
1. You are fully autonomous — DO NOT ask questions, figure things out yourself
2. ModuleNotFoundError — run pip install <package> automatically
3. command not found — install the missing tool automatically
4. port conflict — use lsof to check what's using the port, then kill or change port
5. Frontend projects (React, Vite, Vue, Next.js):
   - Vite: update vite.config.js to add `server.allowedHosts: ['.trycloudflare.com', 'localhost']` before starting
   - Then start with: npx vite --host --port {port}
   - React CRA: PORT={port} npm start
   - Next.js: npx next dev -p {port}
   - General: try npm run dev first, then npm start
6. Verify the service is running with curl after starting
7. CRITICAL: You MUST output the deployment result at the very end using this EXACT format:
<<<RESULT>>>{{"service_url":"http://localhost:{port}","pid":PID}}<<<END>>>
Replace {{port}} with the actual port used, and PID with the server process ID from the run_command output."""


class DeployOrchestrator:
    def __init__(
        self,
        name: str,
        target_dir: str,
        port: int = 8000,
        no_tunnel: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.name = name
        self.target_dir = target_dir
        self.port = port
        self.no_tunnel = no_tunnel
        self.log_callback = log_callback
        self.agent = None
        self._agent_output: list[str] = []

        self.tools = {
            "list_files": ListFilesTool(),
            "read_file": ReadFileTool(),
            "write_file": WriteFileTool(),
            "run_command": RunCommandTool(),
        }

    def _log(self, msg: str):
        timestamp = now()
        formatted = f"[{timestamp}] {msg}"
        # Write to deploy log file for SSE and CLI tailing
        deploy_log = TASKS / self.name / ".deploy_log"
        deploy_log.parent.mkdir(parents=True, exist_ok=True)
        with open(deploy_log, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
        if self.log_callback:
            self.log_callback(formatted)

    def run(self) -> DeployResult:
        try:
            self._log(f"Starting deployment: {self.target_dir}")

            agent_type = resolve_deploy_agent_type()
            model_name = resolve_deploy_agent_model()
            self._log(f"Agent type: {agent_type}, Model: {model_name}")

            system_prompt = DEPLOY_SYSTEM_PROMPT.format(
                target_dir=self.target_dir, port=self.port
            )

            agent_output_lines = self._agent_output

            def add_log(source: str, msg: str):
                agent_output_lines.append(msg)
                formatted = f"[{source}] {msg}"
                if self.log_callback:
                    self.log_callback(formatted)

            import threading
            import queue as _queue
            agent_done = threading.Event()

            callbacks = {
                "add_log": add_log,
                "is_running": lambda: True,
                "on_complete": lambda: agent_done.set(),
                "on_ask_user": lambda q, r: r.put([""] * len(q)),
            }

            self.agent = AgentFactory.create(
                agent_type, callbacks, self.name, "deploy", -1, model_name
            )

            self.agent.start()
            if hasattr(self.agent, "send"):
                self.agent.send(system_prompt, is_system=True)

            from ..agents.pty import PtyAgent

            if isinstance(self.agent, PtyAgent):
                t = threading.Thread(target=self.agent.reader_loop, daemon=True)
                t.start()

            if hasattr(self.agent, "wait"):
                self.agent.wait()

            # 等待 agent 完成：真实 agent（有真正的 _send_queue.Queue）通过
            # on_complete 回调通知；mock agent 直接标记完成
            if not isinstance(getattr(self.agent, "_send_queue", None), _queue.Queue):
                agent_done.set()

            agent_done.wait(timeout=300)

            result = self._parse_result(agent_output_lines)
            if not result:
                self._log(f"Agent output lines ({len(agent_output_lines)}): " +
                          "\n".join(agent_output_lines[-3:])[:500])
                raise RuntimeError("Failed to parse deploy result from agent output")

            self._log(
                f"Agent result: service_url={result.service_url}, pid={result.pid}"
            )

            if not self.no_tunnel:
                self._log("Creating Cloudflare Tunnel...")
                tunnel_url = start_tunnel(self.port, self.name)
                if tunnel_url:
                    result.tunnel_url = tunnel_url
                    self._log(f"Tunnel created: {tunnel_url}")
                else:
                    self._log("Tunnel unavailable, using local address")

            pid_file = TASKS / self.name / ".deploy.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(result.pid))

            final_url = result.tunnel_url or result.service_url
            try:
                _service.complete_deploy(
                    self.name, success=True, deploy_url=final_url
                )
            except Exception as e:
                self._log(f"Warning: failed to update service status: {e}")

            self._log(f"Deployment complete: {final_url}")
            return result

        except Exception as e:
            self._log(f"Deployment failed: {e}")
            try:
                _service.complete_deploy(self.name, success=False)
            except Exception:
                pass
            raise

    def _parse_result(self, lines: list[str]) -> Optional[DeployResult]:
        text = "\n".join(lines)
        match = re.search(r"<<<RESULT>>>(.*?)<<<END>>>", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
            return DeployResult(
                service_url=data.get(
                    "service_url", f"http://localhost:{self.port}"
                ),
                pid=int(data.get("pid", 0)),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            self._log(f"Failed to parse result JSON: {e}")
            return None

    def stop(self):
        self._log("Stopping deployment...")

        try:
            stop_tunnel(self.name)
        except Exception as e:
            self._log(f"Warning: tunnel stop error: {e}")

        pid_file = TASKS / self.name / ".deploy.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                self._log(f"Stopped process {pid}")
            except (ValueError, ProcessLookupError, OSError) as e:
                self._log(f"Warning: process stop error: {e}")
            finally:
                pid_file.unlink(missing_ok=True)

        if self.agent:
            try:
                self.agent.shutdown()
            except Exception as e:
                self._log(f"Warning: agent shutdown error: {e}")

        self._log("Deployment stopped")


def run_deploy_orchestrator(
    name: str,
    target_dir: str,
    log_callback: Optional[Callable[[str], None]] = None,
    port: int = 8000,
    no_tunnel: bool = False,
) -> DeployResult:
    orchestrator = DeployOrchestrator(
        name=name,
        target_dir=target_dir,
        port=port,
        no_tunnel=no_tunnel,
        log_callback=log_callback,
    )
    return orchestrator.run()
