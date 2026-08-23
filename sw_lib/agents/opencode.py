"""OpenCodeAgent — 通过 OpenCodeTransport 接入 opencode，遵循选项 A 能力协议层。

本类不再直接 import requests / subprocess，也不解析 opencode 的私有 parts 字段。
所有协议耦合集中在 `transport.py`（OpenCodeTransport）与 `protocol.py`
（AgentMessage/ToolCall）。本类只负责：
  - 把 harness 的回调映射到结构化 AgentMessage；
  - 把阶段允许的工具翻译成 opencode 原生工具开关；
  - 暴露与 BaseAgent 一致的生命周期接口（start/send/shutdown/restart）。

opencode 升级改协议 → 只动 transport.py。
"""
import os
import queue
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import ROOT, CONFIG_DIR, get_repo_path, get_tools_for_stage
from ..core.utils import sw_log
from .base import BaseAgent
from .transport import OpenCodeTransport, OpenCodeTransportError
from .protocol import AgentMessage


# opencode 免费模型（实测 cost.input/output 均为 0）。
# 顺序即回退优先级，依据同一个真实调试任务（跑测试→读文件→改代码→复跑）实测：
#   mimo-v2.5-free              20-26s，多次重复稳定，输出干净  ← 默认
#   hy3-free                    17-46s，稳定
#   big-pickle                  ~46s，稳定
#   nemotron-3.5-lightning-free 24-74s，偶发输出乱码
#   nemotron-3-ultra-free       ~127s，偏慢
#   x-preview-f-free            ~26s，偶有非 ASCII 噪声
FREE_MODELS: Tuple[str, ...] = (
    "mimo-v2.5-free",
    "hy3-free",
    "big-pickle",
    "nemotron-3.5-lightning-free",
    "nemotron-3-ultra-free",
    "x-preview-f-free",
)

DEFAULT_MODEL = FREE_MODELS[0]

# harness 原子工具 → opencode 原生工具名。
# opencode 自带 read/write/edit/bash/grep/glob/list 等，比通过 MCP 反向暴露
# harness 的 5 个工具更可靠（无需额外装 mcp SDK，也无子进程握手）。
TOOL_MAP: Dict[str, Tuple[str, ...]] = {
    "list_files": ("list", "glob"),
    "read_file": ("read", "grep"),
    "write_file": ("write", "edit", "apply_patch"),
    "run_command": ("bash",),
    "ask_user": ("question",),
}

# 需要显式声明开关的 opencode 工具全集（未列出的按 opencode 默认处理）。
_MANAGED_TOOLS: Tuple[str, ...] = (
    "list", "glob", "read", "grep", "write", "edit", "apply_patch", "bash", "question",
)

# 非工具类权限：opencode 的内置 agent 默认表里就写着 `ask`，因此**必须**由
# session 规则显式压掉，否则触发即死锁（任务 newtask 实测卡死 26 分钟）。
#
# 取值依据 —— 反编译 opencode 1.18.20 二进制读到的默认表：
#
#     external_directory: {"*": "ask", <worktree>/*: "allow", ...}
#     doom_loop:          "ask"
#     read:               {"*": "allow", "*.env": "ask", "*.env.*": "ask"}
#
# 注意三件与直觉相反的事，都是实测/反编译坐实的：
#
# 1. `external_directory` 与 `doom_loop` 都**不在** `_MANAGED_TOOLS` 里 ——
#    它们不是工具，`_tool_switches()` 永远不会为它们产生规则。这正是
#    「纪律写对了、覆盖面漏了」的位置。
# 2. `doom_loop` 的 ask 在服务端用的 ruleset 是 `agent.permission` 单独一份，
#    **不含 session 规则**（`ruleset: le.permission`），所以这里的底规则
#    大概率压不住它。列进来是为了「有总比没有好」，真正兜住它的是
#    A1-fix 的第二层：transport 订阅 `permission.asked` 后自动应答。
#    ——**不要**因为它在这张表里就以为它已被关掉。
# 3. `read` 的 `*.env` ask 只在读 env 文件时触发。harness 的 strip_secrets
#    已把密钥摘出环境，但 agent 仍可能顺手 read 一个 .env，故一并兜底。
_ASK_CAPABLE_PERMISSIONS: Tuple[str, ...] = (
    "external_directory",
    "doom_loop",
)


def _strip_leading_label(label: str, prefix: str) -> str:
    """剥掉 label 开头由模型自己写的选项编号。

    只认本轮该有的那个编号（第 0 项只剥 "A"），并且只接受 ``A.`` / ``A、``/
    ``A)`` / ``A:`` 这几种紧跟分隔符的写法。这样 "B. 含美股" 在第 0 项不会被
    误剥 —— 那种错位说明模型给的编号本身有问题，保留原样更容易发现。
    """
    body = label.lstrip()
    if not body.upper().startswith(prefix.upper()):
        return label
    rest = body[len(prefix):].lstrip()
    if not rest[:1] in (".", "、", ")", ":", "："):
        return label
    stripped = rest[1:].strip()
    # 剥完不能变成空串，否则 label 只有一个编号，剥掉就没内容了。
    return stripped or label


class OpenCodeAgent(BaseAgent):
    """OpenCode 后端 agent — 基于私有协议解耦的 transport 层。

    Responsibilities（与旧版对比，已下沉的部分）：
      - 进程/端口/会话 HTTP 通信  → OpenCodeTransport
      - parts 协议解析             → OpenCodeTransport._to_message
      - 工具执行                   → opencode 原生工具（按阶段权限开关）
    """

    # 等待用户回答 question 的上限；比 transport.CHAT_TIMEOUT(300s) 短，
    # 这样超时后还能主动 reject 让 agent 继续，而不是让整轮请求烂在服务端。
    #
    # 随 CHAT_TIMEOUT 1800→300 一并下调。这里**不照抄原比例**（1500/1800≈83%，
    # 换算过来是 250s，只剩 50s 余量）：reject 要发一次 HTTP，agent 还要收到
    # 拒绝后自行决定并把回答写完。余量按绝对值留 120s，比按比例缩放更安全。
    QUESTION_TIMEOUT = 180.0

    def __init__(self, tui_callbacks, name, stage, stage_idx, model_name="opencode",
                 use_native_tools: bool = True, verbose: bool = False,
                 workdir: Optional[str] = None):
        super().__init__(tui_callbacks, name, stage, stage_idx, model_name)
        self.running = False
        self.agent_proc = None
        self.use_native_tools = use_native_tools

        self.workdir = workdir or self._default_workdir()
        self._env = self._load_env()
        provider, model = self._parse_model()
        self._transport = OpenCodeTransport(verbose=verbose, directory=self.workdir,
                                           env=self._env)
        self._transport.set_model(model, provider)
        if use_native_tools:
            self._transport.set_tools(self._tool_switches())
            # D0-6：路径粒度 deny 随 POST /session 带入（有效性 ❓ 未验证，
            # 兑现「篡改可检出」的是 evidence.py 的 HMAC 校验）。
            self._transport.set_permission_rules(self._permission_rules())

        self._seen_tools: set = set()
        self._seen_calls: set = set()

    # ── 工作目录 ──

    def _default_workdir(self) -> str:
        """让 opencode 在真实代码目录里工作，否则它读不到项目文件，无法调试代码。"""
        repo = Path(get_repo_path())
        if not repo.is_absolute():
            repo = ROOT / repo
        target = repo / self.name if self.name else repo
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            return str(ROOT)
        return str(target)

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
        env.pop("NODE_EXTRA_CA_CERTS", None)
        # A0/D0-5：整份环境会交给 `opencode serve` 子进程，而 agent 的 bash
        # 工具一条 `env` 就能读到签名密钥并伪造任意证据签名。
        from ..core.evidence import strip_secrets
        return strip_secrets(env)

    # ── Model parsing ──

    def _parse_model(self) -> Tuple[str, str]:
        """Parse model_name into (provider_id, model_id)。

        未指定或写了 `opencode` 占位时回退到已验证的免费模型，避免把
        不存在的 model id 发给 server（会静默换模型或直接 400）。
        """
        name = (self.model_name or "").strip()
        if not name or name == "opencode":
            return "opencode", DEFAULT_MODEL
        provider, _, model = name.partition("/")
        if not model:
            provider, model = "opencode", provider
        return provider, model

    # ── 工具权限映射 ──

    def _tool_switches(self) -> Dict[str, bool]:
        """把阶段允许的 harness 工具翻译成 opencode 原生工具开关。

        显式关掉未授权工具，这样 04-review 之类只读阶段无法写盘或跑命令。
        """
        try:
            allowed = set(get_tools_for_stage(self.stage) or [])
        except Exception:
            allowed = set()
        enabled: set = set()
        for harness_tool in allowed:
            enabled.update(TOOL_MAP.get(harness_tool, ()))
        return {t: (t in enabled) for t in _MANAGED_TOOLS}

    def _permission_rules(self) -> List[Dict[str, str]]:
        """生成 session 级权限规则表（D0-6）。

        **有效性状态：❓ 未验证。** 依据 A0 的 2.7.2 实测 ——
        `POST /api/session/{id}/permission` 不是纯规则求值器（即使只有一条
        `write:* deny` 也返回 allow，服务端日志无 `evaluated` 行），
        真实判定发生在工具执行路径，需真实 LLM 会话触发 write 才走到。
        因此**不得**以本方法为由声称证据已受保护 —— 那个承诺由
        `core/evidence.py` 的 HMAC 校验兑现。

        五条纪律：

        1. 只产出 `allow` / `deny`，**绝不产出 `ask`** —— harness 不订阅
           `permission.asked`，一旦产生 ask 会死等到 CHAT_TIMEOUT（300 秒）。
           注意 opencode 无匹配时默认就是 ask，所以每种权限都要显式给底规则。
        2. deny **只覆盖 `write` / `edit`**。`bash` 无法按路径约束，
           给它写路径 deny 是自欺。
        3. 规则随 `POST /session` 一次性带入，禁用 PATCH（merge 语义会累积，
           叠加 `findLast` 后者优先会让 deny 被后来的 allow 覆盖）。
        4. deny 排在 allow **之后** —— `findLast` 语义下后者优先。
        5. 底规则必须覆盖**非工具类权限**（`_ASK_CAPABLE_PERMISSIONS`）。
           纪律 1 早就写明「每种权限都要显式给底规则」，但原实现只遍历了
           `_MANAGED_TOOLS`（9 个工具），路径域的 `external_directory`
           因此一条规则都没有 —— 任务 `newtask` 卡死 26 分钟即由此而来。

        为什么 session 规则能压住 agent 默认表里的 `ask`：服务端求值是
        `merge(agent.permission, session.permission)` 后 `findLast`，
        session 规则排在数组后面，故后者优先。（`doom_loop` 是例外，
        见 `_ASK_CAPABLE_PERMISSIONS` 第 2 条注释。）
        """
        switches = self._tool_switches()

        rules: List[Dict[str, str]] = [
            {"permission": tool, "pattern": "*",
             "action": "allow" if on else "deny"}
            for tool, on in switches.items()
        ]

        # 非工具类权限的底规则：取 allow 而非 deny。
        #
        # 理由不是「越界读无害」，而是三条具体的权衡：
        #   - agent 越界读 harness 目录的**需求**（找阶段规范）已由 prompt
        #     自足消除，剩下的越界读多是探路，deny 只会让它反复试探；
        #   - opencode 的 external_directory 判定发生在**读之前**，deny 会让
        #     整个工具调用报错，而报错文本本身又会诱导 agent 换路径重试；
        #   - 判据保护不依赖这条规则 —— 由下面 write/edit 的路径 deny
        #     加 evidence.py 的 HMAC 校验兑现，与 bash 同理（纪律 2）。
        #
        # 注意 pattern 的 `*` 在服务端被编译成带 `s` 标志的 `.*`（反编译确认：
        # `replace(/\*/g,".*")` + `new RegExp("^"+l+"$","s")`），**能跨斜杠**，
        # 所以单条 `*` 足以兜住绝对路径，无需再列 `**/`。
        for perm in _ASK_CAPABLE_PERMISSIONS:
            rules.append({"permission": perm, "pattern": "*",
                          "action": "allow"})

        # 判据区禁写。pattern 的 glob 语义仍未验证（2.7.2 第 2 条：assert 端点
        # 不可用作判据），因此同一目标冗余覆盖多种写法，宁可重复也不漏。
        guarded = (
            ".state", "**/.state", "**/.state.lock",
            "STATUS.json", "**/STATUS.json",
            "workspace/**", "**/workspace/**",
            "**/facts/**", "**/evidence/**",
            "config/.evidence_key", "**/.evidence_key",
        )
        for perm in ("write", "edit"):
            for pattern in guarded:
                rules.append({"permission": perm, "pattern": pattern,
                              "action": "deny"})

        rules.extend(self._substage_write_rules())
        return rules

    # ── A2 的 3.3：03a / 03b 的写入约束（硬层）──

    # 测试路径的判定与 `red_witness.hash_test_files` 保持同一套形状，
    # 否则会出现「硬层允许写、准出时却算进冻结哈希」的错位。
    _TEST_PATTERNS: Tuple[str, ...] = (
        "test_*.py", "*_test.py",
        "**/test_*.py", "**/*_test.py",
        "tests/**", "**/tests/**",
    )

    def _witness_substage(self) -> Optional[str]:
        """当前的 red_witness 子阶段；不在见证流程内时返回 None。

        读失败一律当作「不在流程内」：这一层是提前反馈，不是主防线
        （主防线是准出时的哈希校验）。为读状态失败而挡住 agent 写文件，
        代价明显大于收益。
        """
        # 阶段检查在最前面：子阶段是 03-coding **内部**的概念，
        # 其他阶段绝不该套上它的写入约束（哪怕测试注入了覆写值）。
        if self.stage != "03-coding":
            return None
        override = getattr(self, "_witness_phase", "__unset__")
        if override != "__unset__":
            return override
        try:
            from ..workflow import red_witness as rw

            phase = rw.read_phase(self.name)
            return phase if phase in (rw.PHASE_TEST, rw.PHASE_IMPL) else None
        except Exception:
            return None

    def _substage_write_rules(self) -> List[Dict[str, str]]:
        """把 03a / 03b 的写入约束翻译成 write / edit 规则。

        **有效性 ❓ 未验证**，与 D0-6 同一条定性（A0 的 2.7.2：assert 端点
        一律返回 allow，真实判定在工具执行路径）。兑现「改测试可检出」的是
        A2 的哈希冻结 —— 3.3 末尾写明它是**长期主防线**，因为 `bash`
        无法按路径约束（A0 的 U0-1），agent 始终可以绕过工具写文件。

        这一层的价值是**时机**：让「不该写」在写的那一刻就被拒，
        而不是等到准出才报错，省掉一整轮返工。

        `phase == none` 时返回空列表 —— 存量任务与返工轮次落在这一态，
        多下发一条规则就会把「见证机制只是不观测」变成
        「见证机制悄悄改了 agent 的写权限」。
        """
        phase = self._witness_substage()
        if phase is None:
            return []

        from ..workflow import red_witness as rw

        rules: List[Dict[str, str]] = []
        if phase == rw.PHASE_IMPL:
            # 03b：实现文件照常写，测试文件禁改。
            # deny 追加在最后 —— findLast 后者优先。
            for perm in ("write", "edit"):
                for pattern in self._TEST_PATTERNS:
                    rules.append({"permission": perm, "pattern": pattern,
                                  "action": "deny"})
            return rules

        # 03a：只写测试。顺序是刻意的 —— 先 deny 所有 .py，再 allow 测试路径，
        # 因为 findLast 是后者优先；反过来写会把测试文件一起挡掉，03a 死锁。
        for perm in ("write", "edit"):
            for pattern in ("*.py", "**/*.py"):
                rules.append({"permission": perm, "pattern": pattern,
                              "action": "deny"})
            for pattern in self._TEST_PATTERNS:
                rules.append({"permission": perm, "pattern": pattern,
                              "action": "allow"})
        return rules

    # ── 结构化消息分发（不再解析裸 parts）──

    def _dispatch_message(self, msg: AgentMessage) -> None:
        """把结构化 AgentMessage 转交 harness 回调。"""
        try:
            if msg.step_start:
                self.status = self.STATUS_ACTIVE
                cb = self.callbacks.get("on_step_start")
                if cb:
                    cb()

            for t in msg.text_parts:
                if t:
                    cb = self.callbacks.get("on_text")
                    if cb:
                        cb(t)

            for t in msg.reasoning_parts:
                if t:
                    cb = self.callbacks.get("on_reasoning")
                    if cb:
                        cb(t)

            for tc in msg.tool_calls:
                if tc.name in self._seen_tools:
                    continue  # 事件流已上报过，避免重复
                cb = self.callbacks.get("on_tool")
                if cb:
                    cb({"name": tc.name, "input": tc.input})

            if msg.step_finish:
                if msg.finish_reason == "stop":
                    self.status = self.STATUS_IDLE
                cb = self.callbacks.get("on_step_finish")
                if cb:
                    cb(msg.finish_reason)
        except Exception as e:
            sw_log(self.name, f"error dispatching message: {e}", "error")

        if msg.text:
            sw_log(self.name, f"complete reply ({len(msg.text)} chars)", "agent")
        elif msg.has_tool:
            sw_log(self.name, f"tool calls: {[t.name for t in msg.tool_calls]}", "agent")
        else:
            sw_log(self.name, "no text/tool in response", "sw")
        sw_log(self.name, "opencode completed", "sw")

    # ── BaseAgent interface ──

    @property
    def is_active(self) -> bool:
        return self.running

    def start(self):
        """拉起 opencode server。失败时明确置为 error 并冒泡日志，不静默挂起。"""
        if self.running and self._transport.server_url:
            return
        if self._transport.server_url is None:
            self.status = self.STATUS_CONNECTING
            try:
                self._transport.start()
            except OpenCodeTransportError as e:
                self.running = False
                self.status = self.STATUS_ERROR
                self._add_log("error", f"opencode 启动失败: {e}")
                sw_log(self.name, f"opencode start failed: {e}", "error")
                return
            provider, model = self._parse_model()
            self._add_log(
                "sw",
                f"opencode 就绪 @ {self._transport.server_url} "
                f"[model={provider}/{model}, cwd={self.workdir}]",
            )
        self.running = True
        self.status = self.STATUS_IDLE

    def send(self, text: str, is_system: bool = False):
        """Send message to OpenCode. Blocks until response received."""
        if not self.running:
            if is_system:
                self.start()
            else:
                return
        if not self._transport.server_url:
            self._add_log("error", "opencode 未连接，消息未发送")
            self.status = self.STATUS_ERROR
            self._fire_complete()
            return

        self._seen_tools = set()
        self._seen_calls = set()
        if is_system:
            text += (
                "\n\n[SYSTEM] You MAY ask questions to clarify requirements. "
                "Ask one question at a time. Incorporate the user's answers "
                "into your analysis before finalizing."
            )

        self.status = self.STATUS_CONNECTING
        # 订阅 SSE：最终 HTTP 响应里没有中间 tool part，只有事件流能看到
        # agent 实际调用了哪些工具，长任务期间界面也才有反馈。
        self._transport.start_events(
            on_delta=self._on_delta,
            on_tool=self._on_stream_tool,
            on_question=self._on_question_asked,
            on_permission=self._on_permission_asked,
        )

        try:
            msg = self._transport.send_message(text)
            self._dispatch_message(msg)
            if self.status != self.STATUS_ERROR:
                self.status = self.STATUS_IDLE
        except OpenCodeTransportError as e:
            self._add_log("error", str(e))
            sw_log(self.name, f"opencode send failed: {e}", "error")
            self.status = self.STATUS_ERROR

        self._fire_complete()

    def _on_delta(self, delta: str) -> None:
        """流式增量：优先走 on_delta，没有就退回 on_text（保持 UI 有反馈）。"""
        cb = self.callbacks.get("on_delta")
        if cb:
            cb(delta)
            return
        self.status = self.STATUS_ACTIVE

    def _on_stream_tool(self, name: str, tool_input: Dict[str, Any]) -> None:
        """把事件流里的工具调用上报，并记入日志（供 code debugging 审计）。

        opencode 对同一次调用会在 running/completed 两个阶段各推一次事件，
        因此按 (工具名, 入参) 去重，避免 UI 里出现重复条目。
        """
        self.status = self.STATUS_ACTIVE
        key = (name, repr(sorted(tool_input.items())) if tool_input else "")
        if key in self._seen_calls:
            return
        self._seen_calls.add(key)
        if name:
            self._seen_tools.add(name)
            self._add_log("agent", f"🔧 {name} {str(tool_input)[:120]}")
        cb = self.callbacks.get("on_tool")
        if cb:
            cb({"name": name, "input": tool_input})

    # ── 权限审批（服务端 permission.asked）──

    # 等待权限审批的上限。取值远小于 QUESTION_TIMEOUT：question 是 agent
    # 主动问人、值得等真人；权限 ask 是它无意踩到边界，等下去只是白耗。
    PERMISSION_TIMEOUT = 30.0

    def _on_permission_asked(self, request_id: str,
                             props: Dict[str, Any]) -> None:
        """服务端就某个权限请求等待审批时被事件流唤起。

        这里修的是一个**可观测性缺陷**，不只是死锁：任务 `newtask` 卡了
        26 分钟，期间 UI 显示「Agent 正在处理中」—— 技术上没说错，但把
        「等待外部审批」和「正在计算」混成了同一个状态，于是用户只能猜，
        最后猜成了「免费模型限额」。所以本方法的第一职责是**说清在等什么**，
        第二职责才是让流程继续。

        应答用 `once` 而非 `always`：见 transport.respond_permission。
        """
        permission = str(props.get("permission") or "?")
        patterns = props.get("patterns") or []
        shown = ", ".join(str(p) for p in patterns[:3]) or "(无模式)"
        if len(patterns) > 3:
            shown += f" 等 {len(patterns)} 项"

        prev_status = self.status
        self.status = self.STATUS_WAITING
        self._add_log(
            "sw",
            f"⏸ 正在等待权限审批: permission={permission} 模式={shown} "
            f"—— 自动放行本次（{self.PERMISSION_TIMEOUT:.0f}s 内无响应则拒绝）",
        )
        sw_log(self.name,
               f"permission.asked id={request_id} permission={permission} "
               f"patterns={patterns}", "sw")

        ok = self._transport.respond_permission(request_id, "once")
        if ok:
            self._add_log("sw", f"✓ 已放行权限 {permission}（仅本次）")
        else:
            # 应答失败就明说。静默失败会让请求继续挂到 CHAT_TIMEOUT，
            # 又变回那个「没有任何提示的 26 分钟」。
            self._add_log(
                "error",
                f"权限应答提交失败: permission={permission} "
                f"id={request_id} —— 该请求可能挂起到超时",
            )
            sw_log(self.name,
                   f"permission respond failed id={request_id}", "error")
        self.status = prev_status

    # ── 结构化提问（opencode 原生 question 工具）──

    @staticmethod
    def _normalize_question(q: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """把 opencode 的 question 结构翻译成 harness ask_user 约定。

        opencode 的 options 是 ``{label, description}`` 字典，而 harness 的
        on_ask_user / TUI 约定 options 是字符串列表，且 TUI 依赖 "A." 前缀切出
        可输入的选项标号（见 tui._update_agent_status）。所以这里给每个选项加
        字母编号，并返回「展示文本 -> opencode 原始 label」映射，用于把用户
        回答还原成服务端认的 label。
        """
        mapping: Dict[str, str] = {}
        options: List[str] = []
        for i, o in enumerate(q.get("options") or []):
            if isinstance(o, dict):
                label = str(o.get("label", "")).strip()
                desc = str(o.get("description", "")).strip()
            else:
                label, desc = str(o).strip(), ""
            if not label:
                continue
            prefix = chr(ord("A") + i) if i < 26 else str(i + 1)
            # 模型经常自己就把编号写进 label（"A. 仅中国A股交易日"）。无条件再加
            # 一次前缀会显示成 "A. A. 仅中国A股交易日"（任务 T3）。已经带了本轮
            # 该有的编号就不再重复加。
            body = _strip_leading_label(label, prefix)
            shown = f"{prefix}. {body} - {desc}" if desc else f"{prefix}. {body}"
            options.append(shown)
            mapping[shown] = label
        text = q.get("question") or q.get("header") or "Agent 请求补充信息"
        return {"question": text, "options": options}, mapping

    def _on_question_asked(self, request_id: str,
                           questions: List[Dict[str, Any]]) -> None:
        """agent 调用 question 工具时被事件流唤起。

        opencode 的 question 是服务端阻塞式的：不回复，POST /message 会一直挂到
        CHAT_TIMEOUT（5 分钟）。所以这里必须把问题交给 harness，并把用户回答
        POST 回去；拿不到回答时明确 reject，让 agent 自行决定而不是死等。
        """
        if not questions:
            self._transport.reject_question(request_id)
            return

        cb = self.callbacks.get("on_ask_user")
        if not cb:
            self._add_log("sw", "⚠️ 当前环境不支持 ask_user，已让 agent 自行决定")
            self._transport.reject_question(request_id)
            return

        normalized = []
        label_maps: List[Dict[str, str]] = []
        for q in questions:
            nq, mapping = self._normalize_question(q)
            normalized.append(nq)
            label_maps.append(mapping)

        prev_status = self.status
        self.status = self.STATUS_WAITING
        res_queue: "queue.Queue" = queue.Queue()
        try:
            cb(normalized, res_queue)
            answers = res_queue.get(timeout=self.QUESTION_TIMEOUT)
        except queue.Empty:
            self._add_log("error", "用户回答超时，已让 agent 自行决定")
            self._transport.reject_question(request_id)
            self.status = prev_status
            return
        except Exception as e:
            sw_log(self.name, f"ask_user dispatch failed: {e}", "error")
            self._transport.reject_question(request_id)
            self.status = prev_status
            return

        payload = self._to_answer_payload(answers, label_maps)
        if not self._transport.answer_question(request_id, payload):
            self._add_log("error", "回答提交失败，已让 agent 自行决定")
            self._transport.reject_question(request_id)
        else:
            self._add_log("sw", f"✓ 已提交 {len(payload)} 个回答")
        self.status = prev_status

    @staticmethod
    def _to_answer_payload(answers: Any,
                           label_maps: List[Dict[str, str]]) -> List[List[str]]:
        """把 UI 回答对齐成 opencode 要求的「每问一组已选 label」。

        UI 返回的是展示文本（形如 "A. label - description"），需要还原成
        opencode 原始 label；匹配不上就原样透传，让 agent 至少能看到用户的
        自由输入。
        """
        if not isinstance(answers, list):
            answers = [answers]
        payload: List[List[str]] = []
        for i, mapping in enumerate(label_maps):
            raw = answers[i] if i < len(answers) else ""
            if isinstance(raw, list):
                payload.append([str(x) for x in raw])
                continue
            text = str(raw).strip()
            payload.append([mapping.get(text) or text])
        return payload

    def _fire_complete(self) -> None:
        """无论成功或失败都要通知上层，否则 workflow 会等到超时才醒。"""
        cb = self.callbacks.get("on_complete")
        if cb:
            cb()

    def shutdown(self):
        self.running = False
        self.status = self.STATUS_IDLE
        self._transport.shutdown()

    def restart(self):
        self.shutdown()
        self.start()

    def inject_context(self):
        self._add_log("system", "Context injected via initial message")

    def reader_loop(self):
        pass  # no async reader needed with synchronous transport
