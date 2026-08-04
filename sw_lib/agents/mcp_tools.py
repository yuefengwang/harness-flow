"""MCP 工具桥接层 — 把 harness 的 5 个原子工具暴露为 MCP server。

「选项 A 重构」的核心收益点：opencode（及任何支持 MCP 的 agent）不再是
被你架空成"纯聊天 API"的傀儡，而是通过标准 MCP 协议**自主调用**你的
list_files / read_file / write_file / run_command / ask_user 工具。

这样：
  - opencode 升级只动它自己的 MCP client，本协议层（基于标准 MCP）零改动；
  - 工具权限、上下文、多轮执行全归 agent 管，harness 只维护"工具清单 + 阶段门禁"。

启动方式：作为独立子进程由 opencode 通过 `mcpServers` 配置拉起（stdio）。
本文件可被直接 `python -m sw_lib.agents.mcp_tools` 运行，或 `run_mcp_server()` 嵌入。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List

from ..tools.toolbox import Toolbox

# MCP SDK 为「可选运行时依赖」：仅当 opencode 作为子进程拉起本模块（stdio）时才需要。
# 主进程（harness 自身）永不 import 本模块，因此缺失 mcp 不会阻断主流程。
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    _HAS_MCP = True
except ImportError:  # pragma: no cover - 仅子进程缺失依赖时触发
    _HAS_MCP = False

    def _missing_mcp_exit() -> None:
        sys.stderr.write(
            "ERROR: 未安装 MCP SDK。opencode 的 MCP 工具桥接需要 `pip install mcp`。\n"
            "若不想启用 MCP 工具，请在 AgentFactory 构造 OpenCodeAgent 时传 use_mcp_tools=False。\n"
        )
        sys.exit(1)


# ── 工具 schema（标准 MCP input_schema，与 Toolbox 工具一一对应）──

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "列出指定目录下的文件列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对项目根的路径，默认 '.'"}
            },
        },
    },
    {
        "name": "read_file",
        "description": "读取指定文件内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "offset": {"type": "integer", "description": "起始行（可选）"},
                "limit": {"type": "integer", "description": "读取行数（可选）"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "写入/覆盖文件内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": "在沙箱内执行 shell 命令。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "命令字符串"},
                "cwd": {"type": "string", "description": "工作目录（可选）"},
                "timeout": {"type": "integer", "description": "超时秒数（可选）"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "ask_user",
        "description": "向用户提问并等待回答。",
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "问题列表",
                    "items": {"type": "object"},
                }
            },
            "required": ["questions"],
        },
    },
]


def _build_toolbox(task_name: str, stage: str, callbacks: Dict[str, Any]) -> Toolbox:
    return Toolbox(task_name=task_name, stage=stage, callbacks=callbacks)


class McpToolBridge:
    """把 Toolbox 适配为 MCP Server。

    通过环境变量 HARNESS_TASK / HARNESS_STAGE / HARNESS_CALLBACKS_JSON 接收
    调用方（opencode 启动脚本）注入的上下文。交互式 ask_user 的回调通过
    callbacks 注入（如 Web 场景），CLI 场景可留空由工具自身降级。
    """

    def __init__(self, task_name: str = "mcp", stage: str = "03-coding",
                 callbacks: Dict[str, Any] = None):
        if not _HAS_MCP:
            _missing_mcp_exit()
        self.task_name = task_name
        self.stage = stage
        self.callbacks = callbacks or {}
        self.server = Server("harness-flow-tools")
        self._register_handlers()

    def _toolbox(self) -> Toolbox:
        return _build_toolbox(self.task_name, self.stage, self.callbacks)

    def _register_handlers(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return [Tool(**s) for s in TOOL_SCHEMAS]  # type: ignore[arg-type]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            tb = self._toolbox()
            if name not in tb._all_tools:
                return [TextContent(type="text", text=f"错误: 未知工具 {name}")]
            try:
                result = tb._all_tools[name](**arguments)
                return [TextContent(type="text", text=str(result))]
            except Exception as e:  # 工具异常不应打断 agent 循环
                return [TextContent(type="text", text=f"工具执行异常: {e}")]

    async def run(self) -> None:
        if not _HAS_MCP:
            _missing_mcp_exit()
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.server.create_initialization_options())


def run_mcp_server(task_name: str = "mcp", stage: str = "03-coding",
                   callbacks: Dict[str, Any] = None) -> None:
    """阻塞运行 MCP server（供子进程入口调用）。"""
    bridge = McpToolBridge(task_name=task_name, stage=stage, callbacks=callbacks)
    asyncio.run(bridge.run())


def _env_ctx() -> Dict[str, Any]:
    """从环境变量读取调用方注入的上下文。"""
    import os
    task = os.environ.get("HARNESS_TASK", "mcp")
    stage = os.environ.get("HARNESS_STAGE", "03-coding")
    callbacks: Dict[str, Any] = {}
    cb_json = os.environ.get("HARNESS_CALLBACKS_JSON")
    if cb_json:
        try:
            callbacks = json.loads(cb_json)
        except Exception:
            callbacks = {}
    return {"task_name": task, "stage": stage, "callbacks": callbacks}


if __name__ == "__main__":
    ctx = _env_ctx()
    run_mcp_server(**ctx)
