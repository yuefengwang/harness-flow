"""OpenCodeAgent — 通过 OpenCodeTransport 接入 opencode，遵循选项 A 能力协议层。

本类不再直接 import requests / subprocess，也不解析 opencode 的私有 parts 字段。
所有协议耦合集中在 `transport.py`（OpenCodeTransport）与 `protocol.py`
（AgentMessage/ToolCall）。本类只负责：
  - 把 harness 的回调映射到结构化 AgentMessage；
  - 把允许的原子工具清单以 MCP server 形式注入 opencode，让其自主调用；
  - 暴露与 BaseAgent 一致的生命周期接口（start/send/shutdown/restart）。

opencode 升级改协议 → 只动 transport.py；加新工具 → 只动 mcp_tools.py + Toolbox。
"""
import os
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import ROOT, CONFIG_DIR, get_tools_for_stage
from ..core.utils import sw_log
from .base import BaseAgent
from .transport import OpenCodeTransport, OpenCodeTransportError
from .protocol import AgentMessage, ToolCall


class OpenCodeAgent(BaseAgent):
    """OpenCode 后端 agent — 基于私有协议解耦的 transport 层。

    Responsibilities（与旧版对比，已下沉的部分）：
      - 进程/端口/会话 HTTP 通信  → OpenCodeTransport
      - parts 协议解析             → OpenCodeTransport._to_message
      - 工具执行 / 提问            → 经 MCP 交由 opencode 自主调度（mcp_tools.py）
    """

    def __init__(self, tui_callbacks, name, stage, stage_idx, model_name="opencode",
                 use_mcp_tools: bool = True, verbose: bool = False):
        super().__init__(tui_callbacks, name, stage, stage_idx, model_name)
        self.running = False
        self.agent_proc = None

        self._transport = OpenCodeTransport(verbose=verbose)
        self._transport.set_model(self._parse_model()[1])
        self.use_mcp_tools = use_mcp_tools

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
        env.pop("NODE_EXTRA_CA_CERTS", None)
        return env

    # ── Model parsing ──

    def _parse_model(self) -> Tuple[str, str]:
        """Parse model_name into (provider_id, model_id)."""
        name = (self.model_name or "").strip()
        if not name or name == "opencode":
            return "opencode", "deepseek-v4-flash-free"
        parts = name.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "opencode", name

    # ── MCP 工具注入 ──

    def _mcp_launch_env(self) -> Dict[str, str]:
        """为 opencode 拉起的 MCP server 子进程注入 harness 上下文。"""
        import json, os
        env = dict(os.environ)
        env["HARNESS_TASK"] = str(getattr(self, "task_name", "mcp"))
        env["HARNESS_STAGE"] = self.stage
        # ask_user 在 CLI 场景无 UI 时降级，由工具自身处理
        return env

    def _write_mcp_config(self) -> Optional[str]:
        """生成 opencode 的 mcpServers 配置路径（若启用 MCP 工具）。

        返回配置文件路径；opencode serve 启动时通过 --mcp-config 读取，
        使 agent 能自主调用 harness 的 5 个原子工具。
        """
        if not self.use_mcp_tools:
            return None
        import json
        from pathlib import Path
        cfg = {
            "mcpServers": {
                "harness-flow-tools": {
                    "command": "python",
                    "args": ["-m", "sw_lib.agents.mcp_tools"],
                    "env": self._mcp_launch_env(),
                }
            }
        }
        path = ROOT / ".mcp_harness.json"
        Path(path).write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return str(path)

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
        if self.running:
            return
        self.running = True
        self.status = self.STATUS_IDLE
        if self._transport.server_url is None:
            self._transport.start()
            self._add_log("sw", f"opencode server ready at {self._transport.server_url}")

    def send(self, text: str, is_system: bool = False):
        """Send message to OpenCode. Blocks until response received."""
        if not self.running:
            if is_system:
                self.start()
            else:
                return
        if not self._transport.server_url:
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
            msg = self._transport.send_message(text)
            self._dispatch_message(msg)
            self.status = self.STATUS_IDLE
        except OpenCodeTransportError as e:
            self._add_log("error", str(e))
            self.status = self.STATUS_ERROR

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
