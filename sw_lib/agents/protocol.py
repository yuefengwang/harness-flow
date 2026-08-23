"""Agent 能力协议层 — 与具体 agent 实现无关的领域模型。

「选项 A 重构」的关键抽象：把 opencode 私有 HTTP parts 结构收敛成稳定的
Python 领域对象。上层 OpenCodeAgent / 未来任意 MCP/CLI 后端都只消费这些模型，
不接触任何具体厂商的协议字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolCall:
    """agent 发起的一次工具调用（与 opencode `tool` part 解耦）。"""
    name: str
    input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentMessage:
    """一轮 agent 回复的结构化表示。

    取代 opencode 裸 `parts` 列表，所有协议细节由 transport 层吸收。
    """
    step_start: bool = False
    step_finish: bool = False
    finish_reason: str = "stop"
    text_parts: List[str] = field(default_factory=list)
    reasoning_parts: List[str] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def reasoning(self) -> str:
        return "".join(self.reasoning_parts)

    @property
    def has_tool(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class AgentCapability:
    """描述一个 agent 后端对外暴露的能力契约。

    让 harness 在「不解析私有协议」的前提下，声明式地知道该后端
    支持哪些能力（如是否原生支持 MCP 工具调用）。
    """
    supports_mcp_tools: bool = False
    supports_native_tools: bool = True   # opencode 通过 messages 内的 tool part 暴露工具
    protocol: str = "http"                # http | mcp | cli
