"""
sw_lib.tools — AI Agent 插件化工具系统。

该模块将原本硬编码在 Toolbox 中的方法解耦为独立的工具类，支持：
1. 声明式工具定义 (BaseTool)。
2. 基于阶段 (Stage) 的权限过滤。
3. 更好的异常处理和安全沙箱检查。
"""

import os
import subprocess
import queue
from pathlib import Path
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Type

from ..core.config import ROOT, TASKS, get_tools_for_stage


class BaseTool(ABC):
    """
    Agent 工具基类。所有新工具必须继承此类。
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一标识名，供 LLM 调用"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，指导 LLM 何时及如何使用"""
        pass

    @abstractmethod
    def __call__(self, **kwargs: Any) -> Any:
        """执行工具逻辑的入口"""
        pass

    def _safe_path(self, path: str) -> Path:
        """安全路径检查，确保不越权访问项目根目录外的内容"""
        target = (ROOT / path).resolve()
        if not str(target).startswith(str(ROOT.resolve())):
            raise PermissionError("禁止访问项目根目录以外的路径。")
        return target


class ListFilesTool(BaseTool):
    @property
    def name(self) -> str: return "list_files"
    
    @property
    def description(self) -> str: return "列出指定目录下的文件列表。参数: path (str, 可选)。"

    def __call__(self, path: str = ".") -> str:
        try:
            target = self._safe_path(path)
            items = os.listdir(target)
            return "\n".join(items)
        except Exception as e:
            return f"错误: {e}"


class ReadFileTool(BaseTool):
    @property
    def name(self) -> str: return "read_file"
    
    @property
    def description(self) -> str: return "读取指定文件的全文内容。参数: file_path (str)。"

    def __call__(self, file_path: str) -> str:
        try:
            target = self._safe_path(file_path)
            return target.read_text(encoding="utf-8")
        except Exception as e:
            return f"错误: {e}"


class WriteFileTool(BaseTool):
    @property
    def name(self) -> str: return "write_file"
    
    @property
    def description(self) -> str: return "写入或覆盖文件内容。参数: file_path (str), content (str)。"

    def __call__(self, file_path: str, content: str) -> str:
        try:
            target = self._safe_path(file_path)
            # 禁止 Agent 直接写入系统状态文件
            _PROTECTED_FILES = (".state", "STATUS.json")
            if target.name in _PROTECTED_FILES:
                return f"错误: 禁止直接修改系统文件 {target.name}。请使用 /advance 命令推进阶段。"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"成功写入 {file_path}"
        except Exception as e:
            return f"错误: {e}"


class RunCommandTool(BaseTool):
    @property
    def name(self) -> str: return "run_command"
    
    @property
    def description(self) -> str: return "执行 Shell 命令。参数: command (str)。"

    def __call__(self, command: str) -> str:
        try:
            # 禁止 Agent 通过命令行修改状态文件
            if ".state" in command or "STATUS.json" in command or "sw advance" in command:
                return "错误: 禁止通过命令行修改系统状态。请使用 /advance 命令。"
            res = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, cwd=str(ROOT), timeout=30
            )
            return f"Exit Code: {res.returncode}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        except subprocess.TimeoutExpired:
            return "错误: 命令执行超时。"
        except Exception as e:
            return f"错误: {e}"


class AskUserTool(BaseTool):
    def __init__(self, callbacks: Dict[str, Callable], stage: str):
        self.callbacks = callbacks
        self.stage = stage

    @property
    def name(self) -> str: return "ask_user"
    
    @property
    def description(self) -> str: return "向用户提出问题或寻求确认。参数: questions (list)。"

    def __call__(self, questions: List[Dict[str, Any]]) -> str:
        if "on_ask_user" not in self.callbacks:
            return "错误: 当前环境不支持交互式提问。"

        res_queue: queue.Queue = queue.Queue()
        # 转发给 UI (MonitorTUI)
        self.callbacks["on_ask_user"](questions, res_queue)

        try:
            # 阻塞等待 UI 收集完所有回答
            answers = res_queue.get(timeout=300) 
            return self._format_answers(questions, answers)
        except queue.Empty:
            return "错误: 用户响应超时。"

    def _format_answers(self, questions: List[Dict[str, Any]], answers: List[Any]) -> str:
        res = ["用户回答如下："]
        for i, (q, a) in enumerate(zip(questions, answers)):
            res.append(f"问题 {i+1} ({q.get('question')}): {a}")
        return "\n".join(res)


class Toolbox:
    """
    Agent 工具管理器，负责根据阶段加载并提供工具实例。
    """
    
    def __init__(self, task_name: str, stage: str, callbacks: Optional[Dict[str, Callable]] = None):
        self.task_name = task_name
        self.stage = stage
        self.callbacks = callbacks or {}
        self.allowed_tool_names = get_tools_for_stage(stage)
        
        # 实例化工具对象
        self._all_tools: Dict[str, BaseTool] = {
            "list_files": ListFilesTool(),
            "read_file": ReadFileTool(),
            "write_file": WriteFileTool(),
            "run_command": RunCommandTool(),
            "ask_user": AskUserTool(self.callbacks, self.stage)
        }

    def get_available_tools(self) -> List[Callable]:
        """
        获取当前阶段允许的所有工具调用对象。
        返回包装后的函数列表，确保 __name__ 和 __doc__ 符合 SDK 要求。
        """
        available = []
        for name in self.allowed_tool_names:
            if name in self._all_tools:
                tool_inst = self._all_tools[name]
                
                # 使用闭包创建包装函数，以避开 bound method 不可设置 __name__ 的限制
                def create_wrapper(inst: BaseTool):
                    def tool_wrapper(**kwargs: Any) -> Any:
                        return inst(**kwargs)
                    # 设置元数据供 AI SDK (如 Gemini) 识别
                    tool_wrapper.__name__ = inst.name
                    tool_wrapper.__doc__ = inst.description
                    return tool_wrapper
                
                available.append(create_wrapper(tool_inst))
        return available
