"""OpenCode transport — 隔离 opencode 私有 HTTP 协议。

这是「选项 A 重构」的核心解耦层：所有与 opencode serve 私有 API 耦合的细节
（CLI 参数、端口发现、/session 端点、parts 协议字段名、SSE 事件流）**只出现在本文件**。
上层 OpenCodeAgent 不再直接 import requests / subprocess，只消费本层暴露的
结构化接口（ensure_session / send_message / abort_session）。

契约来源：opencode 1.17.20 的 `GET /doc`（OpenAPI）与真实 server 实测，而非猜测。
关键事实（改动前请先用 /doc 复核）：
  - `opencode serve` 只认 `--port` / `--hostname` / `--pure` / `--print-logs` 等；
    不存在 `--address` 与 `--disable-network`，传入会导致进程打印 help 后立即退出。
  - `--port 0` 让 opencode 自选端口，并在 stdout 打印
    `opencode server listening on http://127.0.0.1:<port>`；解析该行可避免抢端口竞态。
  - 发消息体必须是 {"model": {"providerID","modelID"}, "parts":[{"type":"text",...}]}；
    把 model 传成字符串会得到 HTTP 400。
  - tool part 的字段是 `tool` 与 `state.input`（不是 `name`/`input`）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from .protocol import AgentMessage, ToolCall


class OpenCodeTransportError(RuntimeError):
    """Transport 层错误（连接/超时/协议异常）。"""


class OpenCodeTransport:
    """管理 opencode serve 进程生命周期与一次会话的 HTTP 通信。

    职责边界（严格只做"通道"）：
      - 启动/停止 opencode serve，从其 stdout 读出真实监听端口
      - 创建/复用/中止 session，并把 session 钉在指定工作目录
      - 把 HTTP 响应原样转成 protocol.AgentMessage（不解读业务语义）
      - 可选订阅 SSE 事件流，把增量 token 交给回调（供 TUI 实时显示）
    """

    CHAT_TIMEOUT = 1800.0          # 单轮对话超时（秒）
    HEALTH_TIMEOUT = 10.0          # 健康检查超时
    ABORT_TIMEOUT = 10.0           # 中止会话超时
    STARTUP_TIMEOUT = 60.0         # 启动总超时（秒）
    STARTUP_POLL_INTERVAL = 0.2    # 启动轮询间隔

    # opencode 启动横幅：`opencode server listening on http://127.0.0.1:49373`
    _LISTEN_RE = re.compile(r"listening on\s+(http://[\d.]+:(\d+))")

    def __init__(self, verbose: bool = False, pure: bool = True,
                 directory: Optional[str] = None,
                 env: Optional[Dict[str, str]] = None):
        self.verbose = verbose
        # pure=True 关闭用户全局插件。实测用户的 ~/.config/opencode 插件会把
        # 任意请求模型强制改写成插件自带 agent 的模型（如 big-pickle），
        # 使 harness 的模型选择完全失效；隔离掉才能保证可复现。
        self.pure = pure
        self.directory = directory
        # 传入的 env 会原样交给 `opencode serve` 子进程，让 config/credentials.yaml
        # 里的 API key 能被 server 读到；None 表示继承当前进程环境。
        self.env = env

        self._proc: Optional[subprocess.Popen] = None
        self._port: Optional[int] = None
        self._server_url: Optional[str] = None
        self._session_id: Optional[str] = None
        self._provider: str = "opencode"
        self._model: str = "nemotron-3.5-lightning-free"
        self._startup_log: List[str] = []
        self._log_thread: Optional[threading.Thread] = None
        self._event_thread: Optional[threading.Thread] = None
        self._event_stop = threading.Event()
        self._tools: Optional[Dict[str, bool]] = None

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

    @property
    def startup_log(self) -> str:
        return "".join(self._startup_log).strip()

    def set_model(self, model: str, provider: str = "opencode") -> None:
        self._model = model
        self._provider = provider

    def set_tools(self, tools: Optional[Dict[str, bool]]) -> None:
        """设置本 session 的原生工具开关（None 表示交给 opencode 默认）。"""
        self._tools = tools

    # ── 生命周期 ──

    def start(self) -> str:
        """启动 opencode serve，返回 server_url。

        与旧实现的关键差异：
          - 使用真实存在的 CLI 参数；
          - 用 `--port 0` + 解析 stdout 得到端口，不再自己 bind 探测（消除竞态）；
          - 保留 stdout/stderr，启动失败时把 opencode 的原始报错抛给上层，
            而不是静默 DEVNULL 让 UI 永远停在"启动中"。
        """
        if self._server_url:
            return self._server_url

        exe = shutil.which("opencode")
        if not exe:
            raise OpenCodeTransportError(
                "未找到 opencode 可执行文件。请先安装（npm i -g opencode-ai）"
                "并确保它在 PATH 中。"
            )

        cmd = [exe, "serve", "--port", "0", "--hostname", "127.0.0.1"]
        if self.pure:
            cmd.append("--pure")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.directory or None,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            raise OpenCodeTransportError(f"无法启动 opencode serve: {e}") from e

        self._proc = proc
        url = self._await_listening(proc)
        self._server_url = url
        self._port = int(url.rsplit(":", 1)[1])
        self._drain_logs_async(proc)
        return url

    def _await_listening(self, proc: subprocess.Popen) -> str:
        """阻塞读取 stdout 直到出现监听横幅，或进程退出/超时。"""
        deadline = time.monotonic() + self.STARTUP_TIMEOUT
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if line:
                self._startup_log.append(line)
                if self.verbose:
                    print(line, end="")
                m = self._LISTEN_RE.search(line)
                if m:
                    return m.group(1)
                continue
            if proc.poll() is not None:
                raise OpenCodeTransportError(
                    f"opencode serve 启动失败 (exit={proc.returncode})："
                    f"{self.startup_log or '无输出'}"
                )
            time.sleep(self.STARTUP_POLL_INTERVAL)

        self._kill_proc()
        raise OpenCodeTransportError(
            f"opencode serve 启动超时（{self.STARTUP_TIMEOUT:.0f}s）："
            f"{self.startup_log or '无输出'}"
        )

    def _drain_logs_async(self, proc: subprocess.Popen) -> None:
        """持续消费 server 日志，避免 PIPE 缓冲写满导致 opencode 卡死。"""
        def _drain() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if self.verbose:
                        print(line, end="")
            except Exception:
                pass

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        self._log_thread = t

    def health(self) -> bool:
        """轻量健康检查（GET /global/health）。"""
        if not self._server_url:
            return False
        try:
            r = requests.get(
                f"{self._server_url}/global/health", timeout=self.HEALTH_TIMEOUT
            )
            return r.ok and bool(r.json().get("healthy"))
        except (requests.RequestException, ValueError):
            return False

    def shutdown(self) -> None:
        """停止 serve 进程并清理会话。"""
        self.stop_events()
        if self._session_id and self._server_url:
            try:
                requests.post(
                    f"{self._server_url}/session/{self._session_id}/abort",
                    timeout=self.ABORT_TIMEOUT,
                )
            except requests.RequestException:
                pass
        self._kill_proc()
        self._session_id = None
        self._server_url = None
        self._port = None
        self._startup_log = []

    def _kill_proc(self) -> None:
        """只终止本实例拉起的进程（不再 pkill 全局 opencode）。"""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    # ── 会话通信 ──

    def _params(self) -> Dict[str, str]:
        """opencode 用 `directory` query 参数决定 session 的工作目录。"""
        return {"directory": self.directory} if self.directory else {}

    def ensure_session(self) -> str:
        """创建（或复用已存在）session，返回 session_id。"""
        if self._session_id:
            return self._session_id
        if not self._server_url:
            raise OpenCodeTransportError("transport 未启动，无法创建会话")
        try:
            resp = requests.post(
                f"{self._server_url}/session",
                params=self._params(),
                timeout=self.HEALTH_TIMEOUT,
            )
            resp.raise_for_status()
            self._session_id = resp.json().get("id")
        except requests.RequestException as e:
            raise OpenCodeTransportError(f"创建 opencode 会话失败: {e}") from e
        if not self._session_id:
            raise OpenCodeTransportError("opencode 未返回 session id")
        return self._session_id

    def send_message(self, text: str, system: Optional[str] = None) -> AgentMessage:
        """向当前 session 发送一条消息，返回结构化 AgentMessage。"""
        if not self._server_url:
            raise OpenCodeTransportError("transport 未连接，无法发送消息")
        sid = self.ensure_session()
        url = f"{self._server_url}/session/{sid}/message"
        payload: Dict[str, Any] = {
            "model": {"providerID": self._provider, "modelID": self._model},
            "parts": [{"type": "text", "text": text}],
        }
        if system:
            payload["system"] = system
        if self._tools:
            payload["tools"] = self._tools
        try:
            resp = requests.post(
                url, json=payload, params=self._params(), timeout=self.CHAT_TIMEOUT
            )
            resp.raise_for_status()
        except requests.Timeout:
            raise OpenCodeTransportError(f"opencode 响应超时: {url}")
        except requests.HTTPError as e:
            body = ""
            if e.response is not None:
                body = e.response.text[:400]
            raise OpenCodeTransportError(
                f"opencode 拒绝请求 (HTTP {getattr(e.response, 'status_code', '?')}): {body}"
            ) from e
        except requests.ConnectionError as e:
            raise OpenCodeTransportError(f"opencode 连接中断: {e}") from e
        try:
            return self._to_message(resp.json())
        except ValueError as e:
            raise OpenCodeTransportError(f"opencode 返回非 JSON 响应: {e}") from e

    # ── 结构化提问应答（question 工具）──

    def answer_question(self, request_id: str, answers: List[List[str]]) -> bool:
        """回答 agent 的 question 请求。

        opencode 的 question 工具是服务端阻塞式的：POST /session/{id}/message
        会一直挂着直到有人调用本接口。answers 与 questions 一一对应，每项本身
        是一个「已选标签」列表。
        """
        return self._question_call(request_id, "reply", {"answers": answers})

    def reject_question(self, request_id: str) -> bool:
        """拒绝回答，让 agent 自行决定，避免请求永久挂起。"""
        return self._question_call(request_id, "reject", None)

    def pending_questions(self) -> List[Dict[str, Any]]:
        """列出待回答的提问（跨 session），用于兜底轮询。"""
        if not self._server_url:
            return []
        try:
            r = requests.get(f"{self._server_url}/question",
                             params=self._params(), timeout=self.HEALTH_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            return []
        if not isinstance(data, list):
            return []
        if not self._session_id:
            return data
        return [q for q in data if q.get("sessionID") == self._session_id]

    def _question_call(self, request_id: str, action: str,
                       payload: Optional[Dict[str, Any]]) -> bool:
        if not self._server_url:
            return False
        url = f"{self._server_url}/question/{request_id}/{action}"
        try:
            r = requests.post(url, json=payload, params=self._params(),
                              timeout=self.HEALTH_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException:
            return False
        return True

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

    # ── SSE 事件流（可选，供 TUI 实时回显）──

    def start_events(self, on_delta: Optional[Callable[[str], None]] = None,
                     on_tool: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                     on_idle: Optional[Callable[[], None]] = None,
                     on_question: Optional[Callable[[str, List[Dict[str, Any]]], None]] = None) -> None:
        """订阅 /event，把增量文本与工具调用实时交给回调。

        必要性：POST /session/{id}/message 只返回**最终**助手消息，中间步骤的
        tool part 不在其中。实测 agent 明明调用了 bash/edit，最终响应里却没有
        tool part —— 只有订阅 SSE 才能看见工具活动，长任务期间界面也才有反馈。

        ``on_question`` 同样只能从事件流拿到：agent 调用原生 question 工具后，
        POST /message 会一直阻塞等人回答，此时唯一的通知渠道就是
        ``question.asked`` 事件。不订阅就会死等到 CHAT_TIMEOUT。

        注意 ``on_question`` 会在独立线程里跑：它内部要等真人回答（可能几分钟），
        若占住 pump 就再也收不到 delta / tool / idle 事件。
        """
        if self._event_thread and self._event_thread.is_alive():
            return
        if not self._server_url:
            return
        self._event_stop.clear()

        seen_tools: set = set()
        seen_questions: set = set()

        def _pump() -> None:
            try:
                with requests.get(
                    f"{self._server_url}/event",
                    params=self._params(),
                    stream=True,
                    timeout=(10, None),
                ) as r:
                    # /event 只声明 text/event-stream，没带 charset，requests 会
                    # 按 RFC 2616 退回 ISO-8859-1，中文日志与提问全变乱码。
                    r.encoding = "utf-8"
                    for raw in r.iter_lines(decode_unicode=True):
                        if self._event_stop.is_set():
                            return
                        if not raw or not raw.startswith("data:"):
                            continue
                        try:
                            evt = json.loads(raw[5:].strip())
                        except ValueError:
                            continue
                        etype = evt.get("type")
                        props = evt.get("properties", {}) or {}
                        if etype == "message.part.delta":
                            if on_delta and props.get("field") == "text":
                                delta = props.get("delta")
                                if delta:
                                    on_delta(delta)
                        elif etype == "message.part.updated":
                            part = props.get("part") or {}
                            if on_tool and part.get("type") == "tool":
                                state = part.get("state") or {}
                                if state.get("status") in ("running", "completed"):
                                    key = (part.get("id"), state.get("status"))
                                    if key not in seen_tools:
                                        seen_tools.add(key)
                                        on_tool(part.get("tool") or "",
                                                state.get("input") or {})
                        elif etype == "session.idle" and on_idle:
                            on_idle()
                        elif etype in ("question.asked", "question.v2.asked"):
                            if not on_question:
                                continue
                            qid = props.get("id")
                            if not qid or qid in seen_questions:
                                continue
                            sid = props.get("sessionID")
                            if sid and self._session_id and sid != self._session_id:
                                continue  # /event 是全局流，别抢别的会话的提问
                            seen_questions.add(qid)
                            threading.Thread(
                                target=on_question,
                                args=(qid, props.get("questions") or []),
                                daemon=True,
                            ).start()
            except Exception:
                return  # 事件流是增强项，断了不影响主请求

        t = threading.Thread(target=_pump, daemon=True)
        t.start()
        self._event_thread = t

    def stop_events(self) -> None:
        self._event_stop.set()
        self._event_thread = None

    # ── 协议适配（私有，唯一解析 opencode parts 的地方）──

    @staticmethod
    def _to_message(raw: Dict[str, Any]) -> AgentMessage:
        """把 opencode 原始响应转成与具体实现无关的 AgentMessage。

        兼容两种 tool part 形状：
          - 1.17.x 实际返回：{"type":"tool","tool":<name>,"state":{"input":{...}}}
          - 历史/简化形状：  {"type":"tool","name":<name>,"input":{...}}
        """
        parts = raw.get("parts", []) or []
        msg = AgentMessage()
        for part in parts:
            if not isinstance(part, dict):
                continue
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
                state = part.get("state") or {}
                name = part.get("tool") or part.get("name") or ""
                tool_input = state.get("input")
                if tool_input is None:
                    tool_input = part.get("input") or {}
                msg.tool_calls.append(ToolCall(name=name, input=tool_input))
        return msg
