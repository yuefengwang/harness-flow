"""
sw_lib.tools — AI Agent 插件化工具系统。

该模块将原本硬编码在 Toolbox 中的方法解耦为独立的工具类，支持：
1. 声明式工具定义 (BaseTool)。
2. 基于阶段 (Stage) 的权限过滤。
3. 更好的异常处理和安全沙箱检查。
"""

import os
import shlex
import subprocess
import queue
from pathlib import Path
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Set

from ..core.config import ROOT, get_tools_for_stage


# ── 命令白名单 ──
# run_command 仅允许执行白名单内的可执行文件（首 token 匹配）。
# 这是 best-effort 沙箱；未来拓展容器隔离时，本集合映射为容器内可用命令。
DEFAULT_ALLOWED_COMMANDS: Set[str] = {
    # 版本控制
    "git", "git-lfs",
    # Python 生态
    "python", "python3", "pip", "pip3",
    # JS/前端
    "node", "npm", "npx", "yarn", "pnpm",
    # JVM
    "mvn", "gradle", "java", "javac",
    # Go / Rust
    "go", "cargo", "rustc",
    # 构建
    "make", "cmake",
    # 容器 / 编排
    "docker", "docker-compose", "kubectl", "helm",
    # 常用 CLI 工具
    "cat", "ls", "pwd", "echo", "mkdir", "cp", "mv", "head", "tail",
    "grep", "sed", "awk", "find", "tar", "unzip", "jq", "wc", "sort", "uniq",
    "curl", "wget", "lsof", "ps", "kill", "which", "touch", "chmod", "date",
    "sh", "bash", "zsh",
    # 测试
    "pytest", "unittest",
}


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
        """安全路径检查，确保不越权访问项目根目录外的内容。

        使用真实路径（resolve 后）比较前缀，避免 `..` / 符号链接 / 大小写
        造成的越权逃逸。
        """
        root_resolved = ROOT.resolve()
        target = (root_resolved / path).resolve()
        if target != root_resolved and root_resolved not in target.parents:
            raise PermissionError(
                f"禁止访问项目根目录以外的路径: {path}"
            )
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
    # 单次输出最大字符数（防止 Agent 上下文被冲垮）
    MAX_OUTPUT_CHARS = 5000

    # 允许覆盖/扩展的白名单（类级，便于测试与未来容器化映射）
    allowed_commands: Set[str] = DEFAULT_ALLOWED_COMMANDS

    @property
    def name(self) -> str: return "run_command"
    
    @property
    def description(self) -> str: return (
        "执行 Shell 命令（仅限白名单命令）。参数: command (str), cwd (str, 可选, 工作目录), "
        "timeout (int, 可选, 超时秒数, 默认 300), "
        "restricted (bool, 可选, 是否限制修改系统状态文件, 默认 True)。"
    )

    def _check_whitelist(self, command: str) -> Optional[str]:
        """校验命令首 token 在白名单内。返回 None 表示通过，否则返回错误文案。"""
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return f"错误: 命令解析失败: {e}"
        if not parts:
            return "错误: 空命令。"
        exe = Path(parts[0]).name  # 取 basename，屏蔽路径前缀绕过（如 /bin/../../usr/bin/rm）
        if exe not in self.allowed_commands:
            return (
                f"错误: 命令 '{exe}' 不在允许的白名单内。"
                f"允许的命令: {', '.join(sorted(self.allowed_commands))}"
            )
        return None

    def __call__(self, command: str, cwd: Optional[str] = None,
                 timeout: Optional[int] = None,
                 restricted: Optional[bool] = None) -> str:
        try:
            # 白名单校验（best-effort 沙箱，不受 restricted 降级影响）
            deny = self._check_whitelist(command)
            if deny:
                return deny

            # 禁止 Agent 在 ROOT 层面修改系统状态文件（仅 restricted=True 时）
            if restricted is None or restricted:
                if ".state" in command or "STATUS.json" in command or "sw advance" in command:
                    return "错误: 禁止通过命令行修改系统状态。请使用 /advance 命令。"

            exec_timeout = timeout or 300
            if cwd:
                cwd_path = Path(cwd)
                if not cwd_path.is_absolute():
                    cwd_path = ROOT / cwd_path
                cwd_str = str(cwd_path.resolve())
            else:
                cwd_str = str(ROOT)

            # 以参数列表方式执行，避免 shell 注入
            argv = shlex.split(command)
            res = subprocess.run(
                argv, shell=False, capture_output=True,
                text=True, cwd=cwd_str, timeout=exec_timeout
            )
            output = f"Exit Code: {res.returncode}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
            if len(output) > self.MAX_OUTPUT_CHARS:
                output = output[:self.MAX_OUTPUT_CHARS] + "\n... (输出已截断)"
            return output
        except subprocess.TimeoutExpired:
            return "错误: 命令执行超时。"
        except FileNotFoundError as e:
            return f"错误: 命令不存在: {e}"
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
