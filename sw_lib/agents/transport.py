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
from contextlib import contextmanager
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

    # 单轮对话超时（秒）。
    #
    # 这个数字同时是两件事的上限，而它们要求相反的方向：
    #   - **故障暴露延迟**：越短越好。1800s 下任务 newtask 的权限死锁静默了
    #     26 分钟（F11），而 26 分钟的沉默不会让人想到死锁，只会想到「模型慢」。
    #   - **单轮工作时长**：越长越安全。agent 一轮里会串行跑多步，每步一次
    #     LLM 往返，还会用 `task` 工具派子 agent **串行嵌套**。
    #
    # 取值史（两次都是实测驱动，不是拍脑袋）：
    #   1800 → 300：F11 权限死锁后按「暴露延迟」单方面优化。
    #   300 → 900：任务 ttt 实测 300s 砍掉了**正在正常干活**的 agent ——
    #              服务端日志 step 0..8 稳步推进，11:45:57 还在下一步，
    #              11:46:10 刚派出子 agent，11:46:44 被 abort，
    #              距上次活动仅 34 秒。它没卡住，是被误杀。
    #
    # 900s 是双向折中：显著大于实测工作量（约 5 分钟且仍在推进），
    # 又只有原值的一半，死锁最多静默 15 分钟。真正把「死锁」压到秒级的
    # 不是这个数字，而是 F11 那层 `permission.asked` 订阅 —— 超时是**兜底**，
    # 不该承担第一道防线的职责。
    #
    #   900 → 1980：等真人回答的上限改为可配置且默认 30 分钟
    #               （`harness.ask_user_timeout`）。这一层是真实的 HTTP
    #               超时：agent 调 question 期间 POST /message 一直挂着，
    #               所以它必须容得下「等人 + 一次 reject 的处置余量」，
    #               否则用户在第 20 分钟认真作答时请求早已断开，
    #               回答无处可投（reject_question 分支退化成死代码）。
    #               取值 = 1800 + 180，由 `_required_chat_timeout()` 算出。
    #
    # ⚠️ 抬高这个数字**没有**让死锁多沉默 —— 那件事由 IDLE_TIMEOUT 负责，
    # 而等人期间空闲计时被显式挂起（见 `waiting_for_human`）。
    # 换句话说：墙上时钟这一层放宽的只是「一轮允许多长」，
    # 「多久没动静算卡住」仍然是 5 分钟。这正是把两个语义拆开的收益 ——
    # 此前它们挤在同一个常数里，任何一方的需求都会伤到另一方。
    #
    # 改动它必须同时维持超时层级（见 tests/unit/agents/test_timeout_hierarchy.py）。
    CHAT_TIMEOUT = 1980.0

    #: 等人上限之外还要留给一次 reject + agent 收尾的余量（秒）。
    #: 按绝对值留而不按比例：比例缩放在小基数上会只剩几十秒（见
    #: opencode.QUESTION_TIMEOUT 的取值史）。
    HUMAN_WAIT_MARGIN = 180.0

    @classmethod
    def effective_chat_timeout(cls) -> float:
        """本次请求真正使用的墙上时钟上限。

        取 `CHAT_TIMEOUT` 与「配置的等人上限 + 处置余量」中的较大者。

        为什么不能只靠类属性：`harness.ask_user_timeout` 是用户可调的，
        有人把它设成 60 分钟时，写死的 `CHAT_TIMEOUT` 会先断 ——
        配置看起来生效了，实际被外层悄悄截断。那种「参数存在但不起作用」
        正是本项目反复出现的形状。

        只向上取、不向下调：配置调小不该连带缩短正常编码轮次的上限，
        那两件事无关（`CHAT_TIMEOUT` 的 900s 下界由任务 ttt 实测标定）。
        """
        try:
            from ..core.config import ask_user_timeout
            need = ask_user_timeout() + cls.HUMAN_WAIT_MARGIN
        except Exception:
            need = 0.0
        return max(cls.CHAT_TIMEOUT, need)

    # 空闲上限（秒）：距**上次观测到活动**多久没动静就算卡住。
    #
    # 为什么需要它 —— CHAT_TIMEOUT 是墙上时钟，它无法区分下面两件事：
    #   - agent 卡在权限 ask 上，0 次工具调用，一直不动（F11 的 26 分钟）
    #   - agent 在写第 5 个测试文件，39 次工具调用稳步推进（helloworld）
    # 两者在墙上时钟看来都只是「这一轮很久」，于是只能靠调大常数来避免
    # 误杀，而调大常数又让真死锁沉默更久。两次事故（ttt 300→900、
    # helloworld 900 又被砍）都是这个形状。
    #
    # 取 300s 的依据是三条实测边界：
    #   - 下界 A：helloworld 实测工具调用最大间隔 92s（跑 pytest + LLM 往返），
    #     取两倍余量 → ≥ 184s
    #   - 下界 B：必须大于 QUESTION_TIMEOUT(180s) —— 等真人回答期间 agent
    #     本来就不动，那是在等人不是卡住（helloworld 里用户想了 65 秒）
    #   - 上界：< CHAT_TIMEOUT(900s)，否则这一层不产生任何新信息
    #
    # 关系由 tests/unit/agents/test_idle_based_timeout.py 锁定 ——
    # 锁的是**关系与区间**而非具体数字，教训见
    # test_timeout_hierarchy.py 第一条（曾把待标定取值写成 == 300.0）。
    IDLE_TIMEOUT = 300.0
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
        self._permission_rules: Optional[List[Dict[str, str]]] = None
        # 事件泵观察到的工具调用序列。用途只有一个：超时/失败时能说清
        # 「它当时干到哪了」，把「卡死」与「正在干活被砍」区分开。
        self._observed_tools: List[str] = []
        # 活动时间戳用单调时钟：系统时间被改（NTP 校正、手动调整）不应
        # 让一个正常会话突然被判成空闲。
        self._last_activity_at: float = time.monotonic()
        self._last_activity_what: str = ""
        # 「正在等真人回答」的嵌套深度与说明。见 waiting_for_human()。
        # 用计数而不是布尔：agent 会连问几轮，内层退出不该解除外层的挂起。
        self._human_wait_depth: int = 0
        self._human_wait_reason: str = ""
        self._human_wait_lock = threading.Lock()

    # ── 公开属性 ──
    @property
    def server_url(self) -> Optional[str]:
        return self._server_url

    # ── 活动观测（空闲判据的事实来源）──
    #
    # 与 A6 的客观轨同一条思路：判据取 harness **自己观测到**的事实
    # （SSE 事件流里的工具调用），不取 agent 的自述。

    @property
    def last_activity_at(self) -> float:
        """上次观测到活动的时间戳（单调时钟）。"""
        return self._last_activity_at

    def note_activity(self, what: str = "") -> None:
        """记下一次活动。由 SSE 事件处理在观测到工具调用时调用。"""
        self._last_activity_at = time.monotonic()
        if what:
            self._last_activity_what = what

    def idle_seconds(self) -> float:
        """距上次活动的秒数。**不是**会话总时长。"""
        return max(0.0, time.monotonic() - self._last_activity_at)

    def is_idle(self) -> bool:
        """是否已超过空闲上限没有任何动静。

        用它可以在 CHAT_TIMEOUT 到期**之前**就区分开「卡住」与「在干活」，
        从而让超时报错说出正确的诊断，而不是让读者去翻服务端日志。

        **等真人回答期间恒为 False**（见 `waiting_for_human`）：那段时间
        agent 零活动是**预期**表现，不是故障征兆。
        """
        if self.is_waiting_for_human:
            return False
        return self.idle_seconds() > self.IDLE_TIMEOUT

    # ── 等真人回答：把这段时间从空闲判据里摘出去 ──

    @property
    def is_waiting_for_human(self) -> bool:
        """当前是否正在等真人回答。"""
        return self._human_wait_depth > 0

    @property
    def waiting_reason(self) -> str:
        """正在等什么（供超时诊断使用）。不在等人时为空串。"""
        return self._human_wait_reason if self.is_waiting_for_human else ""

    @contextmanager
    def waiting_for_human(self, reason: str = "等用户回答"):
        """标记「这段时间在等人，别算空闲」。

        为什么必须有这一层：`IDLE_TIMEOUT` 问的是「agent 卡住了吗」，判据是
        有没有新的工具调用。而 agent 等人回答时**本来就零活动** ——
        那是在等人，不是卡住。此前靠 `IDLE_TIMEOUT(300s) > QUESTION_TIMEOUT
        (180s)` 这个数值关系掩盖了语义混淆；等人上限抬到 30 分钟后，
        障眼法就破了。

        替代方案是把 `IDLE_TIMEOUT` 一路抬到 30 分钟以上 —— 那会让真正的
        死锁多沉默 25 分钟，拿故障暴露能力换用户便利，方向反了。

        退出时刷新活动时间戳：否则用户答完的那一刻，距「上次活动」已经是
        半小时前，紧接着的第一次判定会把一个刚被唤醒的会话判成卡住。

        `finally` 是必须的 —— `reject_question` 那条路径本身可能抛异常，
        挂起状态一旦泄漏，这个会话此后永远不会被判为卡住。
        """
        with self._human_wait_lock:
            self._human_wait_depth += 1
            if reason:
                self._human_wait_reason = reason
        try:
            yield
        finally:
            with self._human_wait_lock:
                self._human_wait_depth = max(0, self._human_wait_depth - 1)
                if self._human_wait_depth == 0:
                    self._human_wait_reason = ""
                    # 人答完了 = 一次活动。
                    self.note_activity("user answered")

    @property
    def observed_tools(self) -> List[str]:
        """本轮已观察到的工具调用名（按首次出现顺序，已去重）。"""
        return list(self._observed_tools)

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

    def set_permission_rules(
            self, rules: Optional[List[Dict[str, str]]]) -> None:
        """设置 session 级权限规则，随 `POST /session` 一次性带入。

        **不提供 PATCH 更新入口**（A0 的 2.7.2 第 1 条实测）：
        `PATCH /session/{id}` 是 merge 而非 replace，连续下发会让规则累积；
        叠加 `findLast` 后者优先的判定语义，累积的结果是**先前的 deny 被
        后来的 allow 覆盖** —— 恰好是最危险的方向。需要换规则就换 session。
        """
        self._permission_rules = rules

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
        body: Dict[str, Any] = {}
        if self._permission_rules:
            body["permission"] = self._permission_rules
        try:
            resp = requests.post(
                f"{self._server_url}/session",
                params=self._params(),
                json=body,
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
        # **不发 tools。** 实测（真实 serve 1.18.20）：服务端 SessionPrompt.prompt
        # 会把 tools 翻译成 `pattern:"*"` 规则后**整体赋值** `O.permission = R`，
        # 把 POST /session 带入的路径级 deny 全部抹掉 —— 31 条降到 9 条，
        # 路径 deny 剩 0 条。tools 在 opencode 侧只用于生成权限规则，
        # 规则已在建 session 时带入，因此这里不发不丢任何功能。
        if self._tools and not self._permission_rules:
            # 没有规则表时退回旧行为（粗粒度总比无规则默认 ask 好）。
            payload["tools"] = self._tools
        chat_timeout = self.effective_chat_timeout()
        try:
            resp = requests.post(
                url, json=payload, params=self._params(), timeout=chat_timeout
            )
            resp.raise_for_status()
        except requests.Timeout:
            # 超时报错必须带上「它当时干到哪了」。任务 ttt 的事故里 UI 只有
            # 一句「响应超时」，读者无法区分「卡死」与「正在干活被砍」——
            # 那次是后者，而确认这件事需要去翻服务端日志。
            done = self.observed_tools
            progress = (f"超时前已完成 {len(done)} 次工具调用"
                        f"（{', '.join(done[-5:])}）" if done
                        else "超时前未观察到任何工具调用")

            # 诊断由**空闲时长**给出，不再让读者自己判断。
            #
            # 此前这里只说「若仍在推进说明上限偏小；若长时间无调用才是真卡住」
            # —— 把判断推给读者，而判断所需的信息（距上次活动多久）恰恰只有
            # harness 掌握。任务 helloworld 因此被误读成「模型卡住了」，
            # 实际是最后一次工具调用距断开仅 5 秒。
            idle = self.idle_seconds()
            if self.is_waiting_for_human:
                # 等人期间 idle_seconds() 被冻结，照旧的两分法会给出一句
                # 与事实相反的诊断（「疑似卡住」）。这里必须说实话：
                # 没人回答不是 agent 的故障（A6 第 3 条：不猜）。
                diagnosis = (
                    f"  诊断：**一直在等用户回答** —— {self.waiting_reason}。\n"
                    f"  agent 没有卡住，是提问始终没有人应答。"
                    f"等人上限可用 `harness.ask_user_timeout` 调整"
                    f"（当前 {chat_timeout - self.HUMAN_WAIT_MARGIN:.0f}s）。")
            elif idle > self.IDLE_TIMEOUT:
                diagnosis = (
                    f"  诊断：**疑似卡住** —— 距上次活动已 {idle:.0f}s"
                    f"（超过空闲上限 {self.IDLE_TIMEOUT:.0f}s）。"
                    f"常见成因是等待某个无人应答的审批或输入。")
            else:
                diagnosis = (
                    f"  诊断：**agent 当时仍在推进** —— 距上次活动仅 {idle:.0f}s"
                    f"（未达空闲上限 {self.IDLE_TIMEOUT:.0f}s），"
                    f"是被墙上时钟上限切断的，不是卡死。\n"
                    f"  这一轮的工作量超出了单轮上限：请把任务拆小，"
                    f"或调高 CHAT_TIMEOUT（当前 {chat_timeout:.0f}s）。")

            raise OpenCodeTransportError(
                f"opencode 响应超时（CHAT_TIMEOUT={chat_timeout:.0f}s）: {url}\n"
                f"  {progress}\n"
                f"{diagnosis}"
            )
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

    # ── 权限审批应答（permission 判定）──

    def respond_permission(self, request_id: str, response: str = "once") -> bool:
        """应答一个待批权限请求。

        与 question 同构的阻塞语义：只要服务端发出 `permission.asked`，
        POST /message 就一直挂着，直到有人应答或 CHAT_TIMEOUT。

        端点形状取自 opencode 1.18.20 二进制（反编译）：

            POST /session/{sessionID}/permissions/{permissionID}
            body: {"response": "once" | "always" | "reject"}

        `once` 而非 `always`：`always` 会把规则写进服务端的 approved 列表并
        持久化，等于让一次越界读悄悄放宽后续所有会话的判定面。每次都问、
        每次都记日志，是可观测性想要的方向。
        """
        if not self._server_url or not self._session_id:
            return False
        url = (f"{self._server_url}/session/{self._session_id}"
               f"/permissions/{request_id}")
        try:
            r = requests.post(url, json={"response": response},
                              params=self._params(),
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
                     on_question: Optional[Callable[[str, List[Dict[str, Any]]], None]] = None,
                     on_permission: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> None:
        """订阅 /event，把增量文本与工具调用实时交给回调。

        必要性：POST /session/{id}/message 只返回**最终**助手消息，中间步骤的
        tool part 不在其中。实测 agent 明明调用了 bash/edit，最终响应里却没有
        tool part —— 只有订阅 SSE 才能看见工具活动，长任务期间界面也才有反馈。

        ``on_question`` 同样只能从事件流拿到：agent 调用原生 question 工具后，
        POST /message 会一直阻塞等人回答，此时唯一的通知渠道就是
        ``question.asked`` 事件。不订阅就会死等到 CHAT_TIMEOUT。

        ``on_permission`` 是同一个坑的另一半，且代价更隐蔽：权限 ask 不是
        agent 主动发起的动作，而是它**不知情**地踩到判定边界时由服务端发出的。
        任务 `newtask` 实测：agent glob 越出 workdir → 服务端发
        `permission.asked` 并静默等待 → 无人应答 → 26 分钟里既没有报错、
        也没有任何提示，UI 一直显示「正在处理中」。

        底规则（`opencode._ASK_CAPABLE_PERMISSIONS`）是第一道防线，但它
        兜不住两种情况：opencode 将来新增权限类型，以及 `doom_loop` 这种
        求值时不带 session 规则的权限。所以这一层必须存在。

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
        seen_permissions: set = set()

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
                            if part.get("type") == "tool":
                                state = part.get("state") or {}
                                if state.get("status") in ("running", "completed"):
                                    key = (part.get("id"), state.get("status"))
                                    if key not in seen_tools:
                                        seen_tools.add(key)
                                        name = part.get("tool") or ""
                                        # `invalid` 是 opencode 对「模型没按
                                        # schema 产出工具调用」给的占位名（实测
                                        # 免费模型偶发）。把它算作进展会误导
                                        # 超时诊断 —— 那恰恰是**没有**进展。
                                        if (name and name != "invalid"
                                                and name not in self._observed_tools):
                                            self._observed_tools.append(name)
                                        # 每次观测到工具调用都刷新活动时间，
                                        # 包括重复调用同一个工具 —— 判「还在
                                        # 动」看的是有没有新事件，不是有没有
                                        # 新工具种类。
                                        self.note_activity(name)
                                        # 记录与回调分开：进展记录是诊断设施，
                                        # 不该因为没人订阅 on_tool 就丢失。
                                        if on_tool:
                                            on_tool(name, state.get("input") or {})
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
                        elif etype in ("permission.asked", "permission.v2.asked"):
                            if not on_permission:
                                continue
                            pid = props.get("id")
                            if not pid or pid in seen_permissions:
                                continue
                            sid = props.get("sessionID")
                            if sid and self._session_id and sid != self._session_id:
                                continue  # 同上：全局流，别应答别人的请求
                            seen_permissions.add(pid)
                            threading.Thread(
                                target=on_permission,
                                args=(pid, props),
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
