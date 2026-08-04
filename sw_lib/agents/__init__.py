"""sw_lib.agents — Agent 后端抽象与工厂。

「选项 A 重构」分层：
  - base.py          : BaseAgent 抽象基类 + AgentFactory
  - transport.py     : OpenCodeTransport —— 隔离 opencode 私有 HTTP 协议（唯一吃私有协议处）
  - protocol.py      : AgentMessage / ToolCall / AgentCapability —— 与厂商无关的领域模型
  - mcp_tools.py     : McpToolBridge —— 把 harness 原子工具暴露为 MCP server，供 agent 自主调用
  - opencode.py      : OpenCodeAgent —— 仅依赖 transport + protocol，不解析私有协议
  - gemini.py/pty.py : 其他后端（仍可用）
"""
from .base import BaseAgent, AgentFactory
from .protocol import AgentMessage, ToolCall, AgentCapability
from .transport import OpenCodeTransport, OpenCodeTransportError

__all__ = [
    "BaseAgent",
    "AgentFactory",
    "AgentMessage",
    "ToolCall",
    "AgentCapability",
    "OpenCodeTransport",
    "OpenCodeTransportError",
]
