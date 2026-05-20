"""sw_lib.opencode_agent — OpenCode CLI Agent

通过 opencode run --format json 与 opencode CLI 交互，
实现与 GeminiAPIAgent 相同的接口（start/send/shutdown/restart/inject_context/reader_loop）。

交互模式：
1. 首次消息：opencode run --format json --dangerously-skip-permissions "消息"
2. 后续消息：opencode run --format json --continue --dangerously-skip-permissions "消息"
3. JSON 事件流按行解析：step-start(step_start) / text / tool(tool_use) / step-finish(step_finish)

opencode run 输出格式（NDJSON，新版常见为顶层字段，旧版可能包在 part 内）：
- {"type":"step-start", ...}  — Agent 开始处理
- {"type":"text", "text":"..."} — Agent 文本输出
- {"type":"tool", "tool":"read", "state":{...}} — Agent 调用工具
- {"type":"step-finish", "reason":"stop"} — Agent 完成处理
"""

import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import ROOT, CONFIG_DIR, TASKS, STAGES, STAGE_NAMES
from ..core.utils import now, sw_log
from ..tools.toolbox import Toolbox


from .base import BaseAgent


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class OpenCodeAgent(BaseAgent):
    def __init__(self, tui_callbacks, name, stage, stage_idx, model_name="opencode"):
        super().__init__(tui_callbacks, name, stage, stage_idx, model_name)
        self.running = False
        self.agent_proc = None
        self._master_fd = None

        self.toolbox = Toolbox(name, stage, callbacks=tui_callbacks)
        self._session_id = None
        self._has_session = False
        self._current_proc = None
        self._send_queue = queue.Queue()
        self._send_worker = None
        self._reader_thread = None
        self._lock = threading.RLock()  # 使用递归锁，防止同一线程内的死lock
        self._no_output_timeout = float(os.environ.get("SW_OPENCODE_NO_OUTPUT_TIMEOUT", "180"))

        self._env = self._load_env()

    def _load_env(self):
        """从凭证文件加载环境变量"""
        import yaml
        env = os.environ.copy()
        paths = [
            CONFIG_DIR / "credentials.yaml",
            CONFIG_DIR / "config.yaml"
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

        # 继续会话。新版 opencode 的 JSON 事件不稳定提供 session id，
        # 因此有 id 时精确续接，没有 id 时退回到当前项目的上一会话。
        if is_continue:
            cmd.append("-c")
            if self._session_id:
                cmd.extend(["-s", self._session_id])

        # 消息内容放在最后
        cmd.append(message)

        return cmd

    def _parse_json_line(self, line):
        """解析 opencode run 输出的单行 JSON"""
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _prepare_message(self, message: str, is_system: bool = False) -> str:
        """为 opencode CLI 补充兼容提示，避免只提示 Gemini 风格的 ask_user。"""
        if not is_system:
            return message

        hint = (
            "\n\n=== OpenCode 交互兼容规则 ===\n"
            "如果需要向用户提问，请优先使用 opencode 内置的 `question` 工具；"
            "如果当前运行环境无法调用该工具，请直接输出清晰的问题和可选项，"
            "等待用户在 Harness-Flow 面板中回复后再继续。"
        )
        if "OpenCode 交互兼容规则" in message:
            return message
        return message + hint

    def _event_type(self, event: Dict[str, Any]) -> str:
        """兼容 opencode 新旧事件名：step-start/step_start 等。"""
        return str(event.get("type", "")).replace("-", "_")

    def _event_payload(self, event: Dict[str, Any]) -> Dict[str, Any]:
        part = event.get("part")
        if isinstance(part, dict):
            return part
        return event

    def _remember_session(self, payload: Dict[str, Any]):
        sid = (
            payload.get("sessionID")
            or payload.get("sessionId")
            or payload.get("session_id")
            or payload.get("session")
        )
        if isinstance(sid, dict):
            sid = sid.get("id")
        if sid and sid != self._session_id:
            self._session_id = str(sid)
            self._add_log("sw", f"opencode session: {self._session_id[:16]}...")
        self._has_session = True

    def _summarize_mapping(self, value: Any, max_items: int = 3) -> str:
        if not isinstance(value, dict) or not value:
            return ""
        items = []
        for k, v in list(value.items())[:max_items]:
            text = str(v).replace("\n", " ")
            if len(text) > 60:
                text = text[:57] + "..."
            items.append(f"{k}={text}")
        return ", ".join(items)

    def _ask_user(self, questions: List[Dict[str, Any]]) -> List[Any]:
        """通过 TUI 的结构化提问通道等待用户回答。"""
        if "on_ask_user" not in self.callbacks:
            self._add_log("error", "当前环境不支持交互式提问")
            return []

        res_queue: queue.Queue = queue.Queue()
        self.callbacks["on_ask_user"](questions, res_queue)
        self.status = self.STATUS_WAITING

        while self.running:
            try:
                answers = res_queue.get(timeout=0.5)
                self.status = self.STATUS_ACTIVE
                return list(answers or [])
            except queue.Empty:
                continue
        return []

    def _write_answers_to_proc(self, proc: Optional[subprocess.Popen], answers: List[Any]) -> bool:
        if not proc or not proc.stdin or proc.poll() is not None:
            return False
        try:
            for answer in answers:
                proc.stdin.write(str(answer) + "\n")
            proc.stdin.flush()
            return True
        except Exception as e:
            self._add_log("error", f"写入 opencode 提问回答失败: {e}")
            return False

    def _handle_tool_event(self, payload: Dict[str, Any], proc: Optional[subprocess.Popen]):
        tool_name = payload.get("tool") or payload.get("name") or "unknown"
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        status = state.get("status", "started")
        tool_input = state.get("input") if isinstance(state.get("input"), dict) else payload.get("input", {})

        if tool_name in ("question", "ask_user"):
            questions = tool_input.get("questions", []) if isinstance(tool_input, dict) else []
            if status in ("pending", "running", "started") and questions:
                self._add_log("system", f"❓ opencode 提出 {len(questions)} 个问题")
                answers = self._ask_user(questions)
                if answers:
                    if self._write_answers_to_proc(proc, answers):
                        self._add_log("system", "已将用户回答写回 opencode")
                    else:
                        self.send(self._format_answers(questions, answers), is_system=True)
                return

            if questions:
                self._add_log("system", f"❓ question ({len(questions)} 个问题, {status})")
            else:
                self._add_log("system", f"❓ {tool_name} ({status})")
            return

        summary = self._summarize_mapping(tool_input)
        if summary:
            self._add_log("system", f"🛠 {tool_name} {status}: {summary}")
        else:
            self._add_log("system", f"🛠 {tool_name} {status}")

        if status == "error":
            err = state.get("error") or payload.get("error")
            if err:
                self._add_log("error", f"{tool_name} 失败: {err}")

    def _format_answers(self, questions: List[Dict[str, Any]], answers: List[Any]) -> str:
        lines = ["用户回答如下："]
        for idx, answer in enumerate(answers):
            question = questions[idx].get("question", f"问题 {idx + 1}") if idx < len(questions) else f"问题 {idx + 1}"
            lines.append(f"{question}: {answer}")
        return "\n".join(lines)

    def _is_sent_message_echo(self, text: str, sent_message: Optional[str]) -> bool:
        if not sent_message:
            return False
        lhs = text.strip().strip('"')
        rhs = sent_message.strip().strip('"')
        return lhs == rhs

    def _handle_event(
        self,
        event: Dict[str, Any],
        agent_text_parts: List[str],
        proc: Optional[subprocess.Popen] = None,
        sent_message: Optional[str] = None,
    ):
        event_type = self._event_type(event)
        payload = self._event_payload(event)

        if event_type in ("session", "step_start"):
            self._remember_session(payload)
            self.status = self.STATUS_ACTIVE
            if event_type == "step_start":
                self._add_log("sw", "opencode step started")
            return

        if event_type == "text":
            text = payload.get("text") or event.get("text") or ""
            if text:
                if self._is_sent_message_echo(text, sent_message):
                    return
                agent_text_parts.append(text)
                self._add_log("agent", text)
            return

        if event_type == "reasoning":
            text = payload.get("text") or event.get("text") or ""
            if text:
                preview = text.strip().replace("\n", " ")
                if len(preview) > 160:
                    preview = preview[:157] + "..."
                self._add_log("system", f"thinking: {preview}")
            return

        if event_type in ("tool", "tool_use"):
            self._handle_tool_event(payload, proc)
            return

        if event_type == "tool_result":
            result = payload.get("result", "")
            if result:
                result_summary = str(result)[:150]
                self._add_log("system", f"🔧 → {result_summary}")
            return

        if event_type == "patch":
            path = payload.get("path") or payload.get("file") or payload.get("title")
            self._add_log("system", f"📝 patch: {path or 'updated'}")
            return

        if event_type == "file":
            path = payload.get("path") or payload.get("title") or payload.get("file")
            self._add_log("system", f"📄 file: {path or 'updated'}")
            return

        if event_type == "error":
            err = payload.get("error") or payload.get("message") or payload
            self._add_log("error", f"opencode error: {err}")
            self.status = self.STATUS_ERROR
            return

        if event_type == "step_finish":
            reason = payload.get("reason", "unknown")
            if reason == "stop":
                self.status = self.STATUS_IDLE
                self._add_log("sw", "✓ opencode 回复完成")
            elif reason == "tool-calls":
                self.status = self.STATUS_ACTIVE
                self._add_log("sw", "opencode tool calls 进行中...")
            else:
                self._add_log("sw", f"opencode step 完成 (reason={reason})")
            return

        if event_type:
            self._add_log("sw", f"opencode event: {event_type}")

    def _run_opencode(self, message, is_continue=False, is_system=False):
        message = self._prepare_message(message, is_system=is_system)
        cmd = self._build_command(message, is_continue)
        self._add_log("sw", f"⏳ opencode 连接中... (会话继续: {is_continue})")
        self.status = self.STATUS_CONNECTING

        try:
            # 切换到 text=True (等同于 universal_newlines=True) 简化处理
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL, # 用 DEVNULL 防止 opencode 因 stdin 非 TTY 而挂起
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                cwd=str(ROOT),
                text=True,
                bufsize=1 # 行缓冲
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
        last_output_at = {"value": time.monotonic()}
        timed_out = {"value": False}

        # 启动 stderr 读取线程
        def _read_stderr(p):
            try:
                for line in p.stderr:
                    last_output_at["value"] = time.monotonic()
                    err_msg = _ANSI_ESCAPE_RE.sub("", line).strip()
                    if err_msg:
                        # 过滤掉一些常见的证书警告，避免干扰
                        if "ca-bundle.crt" in err_msg and "load failed" in err_msg:
                            continue
                        self._add_log("error", f"opencode stderr: {err_msg}")
            except Exception:
                pass

        threading.Thread(target=_read_stderr, args=(proc,), daemon=True).start()

        def _watchdog():
            while self.running and proc.poll() is None:
                if time.monotonic() - last_output_at["value"] > self._no_output_timeout:
                    timed_out["value"] = True
                    self._add_log("error", f"opencode {int(self._no_output_timeout)} 秒无输出，已终止本次请求")
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break
                time.sleep(1.0)

        threading.Thread(target=_watchdog, daemon=True).start()

        try:
            # 直接迭代 stdout 即可获取行
            for line in proc.stdout:
                last_output_at["value"] = time.monotonic()
                if not self.running:
                    proc.terminate()
                    break

                line = line.strip()
                if not line:
                    continue

                event = self._parse_json_line(line)
                if event is None:
                    # 如果不是 JSON，记录到 sw 日志
                    if not line.startswith("{"):
                        self._add_log("sw", f"opencode stdout: {line}")
                    continue

                self._handle_event(event, agent_text_parts, proc=proc, sent_message=message)

        except Exception as e:
            self._add_log("error", f"opencode 输出解析异常: {e}")
            self.status = self.STATUS_ERROR
        finally:
            returncode = proc.wait()
            self._current_proc = None
            if timed_out["value"]:
                self.status = self.STATUS_ERROR
            elif returncode != 0 and self.status != self.STATUS_ERROR:
                self.status = self.STATUS_ERROR
                self._add_log("error", f"opencode 退出码 {returncode}")
            elif self.status in (self.STATUS_CONNECTING, self.STATUS_ACTIVE, self.STATUS_WAITING):
                self.status = self.STATUS_IDLE


        # 记录完整回复到日志文件
        if agent_text_parts:
            full_text = "\n".join(agent_text_parts)
            sw_log(self.name, f"complete reply ({len(full_text)} chars)", "agent")

        sw_log(self.name, "opencode run completed", "sw")

        # 仅当正常完成（非异常退出）时通知 engine 保存产出
        if self.status != self.STATUS_ERROR and "on_complete" in self.callbacks:
            try:
                self.callbacks["on_complete"]()
            except Exception:
                pass

    def start(self):
        """启动 Agent，初始化 worker 线程"""
        if self.running: return
        self.running = True
        self.status = self.STATUS_IDLE
        
        # 启动唯一的 worker 线程
        if self._send_worker is None or not self._send_worker.is_alive():
            self._send_worker = threading.Thread(target=self._send_loop, daemon=True)
            self._send_worker.start()

    def send(self, text, is_system=False):
        """发送消息给 opencode Agent（非阻塞，自动排队）"""
        if not self.running:
            if is_system:
                # 如果是启动时的 context 注入，自动启动
                self.start()
            else:
                self._add_log("sw", "Agent 未运行，输入已写入 .input (sw next 后生效)")
                input_file = TASKS / self.name / ".input"
                input_file.parent.mkdir(parents=True, exist_ok=True)
                with open(input_file, "a") as f:
                    f.write(f"[{now()}] user | {text}\n")
                sw_log(self.name, f"user input (offline): {text[:80]}", "user")
                return

        # 入队消息
        self._send_queue.put((text, is_system))
        # 确保处于等待处理状态
        if self.status == self.STATUS_IDLE:
            self.status = self.STATUS_WAITING

    def _send_loop(self):
        while self.running:
            try:
                # 增加更长的超时
                text, is_system = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                if not self.running: break
                continue

            with self._lock: # 确保串行执行
                try:
                    self._run_opencode(text, is_continue=self._has_session or self._session_id is not None, is_system=is_system)
                except Exception as e:
                    self._add_log("error", f"发送异常: {e}")
                    self.status = self.STATUS_IDLE
                finally:
                    self._send_queue.task_done()

    def _is_busy(self):
        """是否有正在运行的 opencode 子进程"""
        return self._current_proc is not None and self._current_proc.poll() is None

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
