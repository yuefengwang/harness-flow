"""sw_lib.opencode_agent — OpenCode CLI Agent

通过 opencode run --format json 与 opencode CLI 交互，
实现与 GeminiAPIAgent 相同的接口（start/send/shutdown/restart/inject_context/reader_loop）。

交互模式：
1. 首次消息：opencode run --format json --dangerously-skip-permissions "消息"
2. 后续消息：opencode run --format json --continue --dangerously-skip-permissions "消息"
3. JSON 事件流按行解析：step_start / text / tool_use / tool_result / step_finish

opencode run 输出格式（NDJSON）：
- {"type":"step_start", ...}  — Agent 开始处理
- {"type":"text", "part":{"text":"..."}} — Agent 文本输出
- {"type":"tool_use", "part":{"name":"...","input":{...}}} — Agent 调用工具
- {"type":"tool_result", "part":{"result":"..."}} — 工具返回结果
- {"type":"step_finish", "part":{"reason":"stop"}} — Agent 完成处理
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .config import ROOT, TASKS, STAGES, STAGE_NAMES
from .utils import now, sw_log


class OpenCodeAgent:
    STATUS_IDLE = "idle"
    STATUS_CONNECTING = "connecting"
    STATUS_ACTIVE = "active"
    STATUS_ERROR = "error"

    def __init__(self, tui_callbacks, name, stage, stage_idx, model_name="opencode"):
        self.callbacks = tui_callbacks
        self.name = name
        self.stage = stage
        self.stage_idx = stage_idx
        self.model_name = model_name
        self.running = False
        self.status = self.STATUS_IDLE
        self.agent_proc = None
        self._master_fd = None

        self._session_id = None
        self._current_proc = None
        self._send_lock = threading.Lock()
        self._reader_thread = None

        self._env = self._load_env()

    def _load_env(self):
        """从凭证文件加载环境变量"""
        import yaml
        env = os.environ.copy()
        paths = [
            ROOT / "harness" / "credentials.yaml",
            ROOT / "harness" / "config.yaml"
        ]
        for p in paths:
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = yaml.safe_load(f)
                        if not data:
                            continue
                        if p.name == "config.yaml" and "harness" in data:
                            creds = data.get("harness", {}).get("credentials", {})
                            env.update({str(k): str(v) for k, v in creds.items()})
                        elif p.name == "credentials.yaml":
                            env.update({str(k): str(v) for k, v in data.items()})
                except Exception:
                    continue
        return env

    def _close_master(self):
        """兼容测试"""
        self._master_fd = None

    @property
    def is_active(self):
        return self.running

    def _add_log(self, source, msg):
        if "add_log" in self.callbacks:
            self.callbacks["add_log"](source, msg)
        sw_log(self.name, msg[:500], source)

    def _is_running(self):
        if "is_running" in self.callbacks:
            return self.callbacks["is_running"]()
        return True

    def _build_command(self, message, is_continue=False):
        """构建 opencode run 命令列表"""
        cmd = [
            "opencode", "run",
            "--format", "json",
            "--dangerously-skip-permissions",
        ]

        # 指定模型（如果 model_name 不是 "opencode"）
        if self.model_name and self.model_name != "opencode":
            cmd.extend(["-m", self.model_name])

        # 继续会话
        if is_continue and self._session_id:
            cmd.extend(["-c", "-s", self._session_id])

        # 消息内容放在最后
        cmd.append(message)

        return cmd

    def _parse_json_line(self, line):
        """解析 opencode run 输出的单行 JSON"""
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _run_opencode(self, message, is_continue=False):
        cmd = self._build_command(message, is_continue)
        self._add_log("sw", f"⏳ opencode 连接中...")
        self._add_log("sw", f"输入command命令： {cmd}")
        self.status = self.STATUS_CONNECTING

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                cwd=str(ROOT),
            )
        except FileNotFoundError:
            self._add_log("error", "opencode 命令未找到，请确认已安装 opencode CLI")
            self.status = self.STATUS_ERROR
            return
        except Exception as e:
            self._add_log("error", f"opencode 启动异常: {e}")
            self.status = self.STATUS_ERROR
            return

        self._current_proc = proc
        agent_text_parts = []

        try:
            # 逐行读取 JSON 事件流
            for raw_line in iter(proc.stdout.readline, b""):
                if not self.running:
                    proc.terminate()
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                event = self._parse_json_line(line)
                if event is None:
                    continue

                event_type = event.get("type", "")
                part = event.get("part", {})

                if event_type == "step_start":
                    sid = part.get("sessionID", "")
                    is_new_session = sid and sid != self._session_id
                    if is_new_session:
                        self._session_id = sid
                        self._add_log("sw", f"opencode session: {sid[:16]}...")
                        self._add_log("sw", "✓ opencode 已连接，正在处理...")
                    self.status = self.STATUS_ACTIVE

                elif event_type == "text":
                    text = part.get("text", "")
                    if text:
                        agent_text_parts.append(text)
                        self._add_log("agent", text)

                elif event_type == "tool_use":
                    tool_name = part.get("tool") or part.get("name") or "unknown"
                    state = part.get("state", {})
                    tool_input = state.get("input") if isinstance(state, dict) else part.get("input", {})
                    if isinstance(tool_input, dict) and tool_input:
                        input_keys = ", ".join(f"{k}={v}" for k, v in list(tool_input.items())[:3])
                        self._add_log("system", f"🔧 {tool_name}({input_keys})")
                    else:
                        self._add_log("system", f"🔧 {tool_name}")

                elif event_type == "tool_result":
                    result = part.get("result", "")
                    if result:
                        result_summary = str(result)[:150]
                        self._add_log("system", f"🔧 → {result_summary}")

                elif event_type == "step_finish":
                    reason = part.get("reason", "unknown")
                    if reason == "stop":
                        self.status = self.STATUS_IDLE
                        self._add_log("sw", "✓ opencode 回复完成")
                    elif reason == "tool-calls":
                        self._add_log("sw", "tool calls 进行中...")
                    else:
                        self._add_log("sw", f"opencode step 完成 (reason={reason})")

        except Exception as e:
            self._add_log("error", f"opencode 输出解析异常: {e}")
            self.status = self.STATUS_ERROR
        finally:
            proc.wait()
            self._current_proc = None
            if self.status == self.STATUS_CONNECTING:
                self.status = self.STATUS_IDLE

        # 记录完整回复到日志文件
        if agent_text_parts:
            full_text = "\n".join(agent_text_parts)
            sw_log(self.name, f"complete reply ({len(full_text)} chars)", "agent")

        sw_log(self.name, "opencode run completed", "sw")

        # 通知 engine Agent 完成，可保存产出
        if "on_complete" in self.callbacks:
            try:
                self.callbacks["on_complete"]()
            except Exception:
                pass

    def start(self):
        self.running = True
        self.status = self.STATUS_IDLE

    def send(self, text, is_system=False):
        """发送消息给 opencode Agent"""
        if not self.running and not is_system:
            # 离线消息写入 .input 文件
            self._add_log("sw", "Agent 未运行，输入已写入 .input (sw next 后生效)")
            input_file = TASKS / self.name / ".input"
            input_file.parent.mkdir(parents=True, exist_ok=True)
            with open(input_file, "a") as f:
                f.write(f"[{now()}] user | {text}\n")
            sw_log(self.name, f"user input (offline): {text[:80]}", "user")
            return

        def _run():
            with self._send_lock:
                is_continue = self._session_id is not None
                self._run_opencode(text, is_continue=is_continue)

        threading.Thread(target=_run, daemon=True).start()

    def shutdown(self):
        self.running = False
        self.status = self.STATUS_IDLE
        if self._current_proc and self._current_proc.poll() is None:
            try:
                self._current_proc.terminate()
                self._current_proc.wait(timeout=5)
            except Exception:
                try:
                    self._current_proc.kill()
                except Exception:
                    pass
        self._current_proc = None

    def restart(self):
        """重启 Agent（新会话）"""
        self.shutdown()
        self._session_id = None
        self.start()

    def inject_context(self):
        """上下文注入（通过初始消息完成，此方法为接口兼容）"""
        self._add_log("system", "上下文已通过初始消息注入 (opencode 模式)")

    def reader_loop(self):
        """opencode 模式下不需要独立的 reader 线程（输出在 send 中处理）"""
        pass