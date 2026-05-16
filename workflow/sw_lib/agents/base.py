"""sw_lib.base_agent — Abstract base class and factory for Agents"""

from abc import ABC, abstractmethod
from typing import Dict, Callable, Any, Optional, List, Union

class BaseAgent(ABC):
    """
    Agent 抽象基类，定义统一的通信和生命周期接口。
    
    所有具体的 Agent（如 PtyAgent, GeminiAgent 等）都必须继承此类并实现其抽象方法。
    """

    # 状态常量定义
    STATUS_IDLE = "idle"             # 空闲/就绪
    STATUS_CONNECTING = "connecting" # 连接中
    STATUS_ACTIVE = "active"         # 活跃/正在处理
    STATUS_WAITING = "waiting"       # 等待中
    STATUS_ERROR = "error"           # 异常状态

    def __init__(self, callbacks: Dict[str, Callable], name: str, stage: str, stage_idx: int, model_name: str):
        """
        初始化 Agent 基类。
        
        Args:
            callbacks: 回调函数字典，需包含 'add_log', 'is_running', 'on_complete' 等
            name: 任务名称 (ID)
            stage: 当前阶段标识 (如 "01-brainstorming")
            stage_idx: 阶段索引
            model_name: 使用的 AI 模型或命令名称
        """
        self.callbacks = callbacks
        self.name = name
        self.stage = stage
        self.stage_idx = stage_idx
        self.model_name = model_name
        self.status: str = self.STATUS_IDLE

    def _add_log(self, source: str, msg: str):
        """
        内部辅助：向 UI 发送日志并记录到任务日志文件。
        
        Args:
            source: 日志来源 (sw, agent, system, user, error)
            msg: 日志内容
        """
        from ..core.utils import sw_log
        if "add_log" in self.callbacks:
            self.callbacks["add_log"](source, msg)
        sw_log(self.name, msg, source)

    def _is_running(self) -> bool:
        """检查 UI 是否仍在运行，用于中断长时间操作"""
        if "is_running" in self.callbacks:
            return self.callbacks["is_running"]()
        return True

    @property
    @abstractmethod
    def is_active(self) -> bool:
        """返回 Agent 当前是否处于活动连接状态"""
        pass

    @abstractmethod
    def start(self):
        """异步或同步启动 Agent 进程/连接"""
        pass

    @abstractmethod
    def send(self, text: str, is_system: bool = False):
        """
        向 Agent 发送消息。
        
        Args:
            text: 消息文本
            is_system: 是否为系统指令（部分 Agent 会特殊处理）
        """
        pass

    @abstractmethod
    def shutdown(self):
        """安全关闭 Agent 进程或断开连接"""
        pass

    @abstractmethod
    def restart(self):
        """重启 Agent，通常用于刷新上下文或从错误中恢复"""
        pass

    @abstractmethod
    def inject_context(self):
        """向 Agent 注入当前阶段的上下文信息"""
        pass

    @abstractmethod
    def reader_loop(self):
        """
        循环读取 Agent 输出。对于流式或 PTY Agent，这通常在独立线程中运行。
        """
        pass


class AgentFactory:
    """Agent 工厂类，负责根据配置和类型创建具体的 Agent 实例。"""

    @staticmethod
    def create(agent_type: str, callbacks: Dict[str, Callable], name: str, stage: str, stage_idx: int, model_name: str) -> BaseAgent:
        """
        创建并返回一个具体的 Agent 实例。
        
        Args:
            agent_type: Agent 类型 (gemini, opencode, pty 等)
            callbacks: UI 回调函数
            name: 任务名称
            stage: 当前阶段
            stage_idx: 阶段索引
            model_name: 模型名称
            
        Returns:
            BaseAgent 的子类实例
        """
        from ..core.config import is_mock_agent
        
        # 如果全局配置启用了 Mock 模式，则强制返回 MockAgent
        if is_mock_agent():
            from .mock import MockAgent
            return MockAgent(callbacks, name, stage, stage_idx, model_name)

        if agent_type == "gemini":
            from .gemini import GeminiAgent
            return GeminiAgent(callbacks, name, stage, stage_idx, model_name)
        
        elif agent_type == "opencode":
            from .opencode import OpenCodeAgent
            return OpenCodeAgent(callbacks, name, stage, stage_idx, model_name)
        
        else:
            # 默认返回 PtyAgent (基础 PTY 封装)
            from .pty import PtyAgent
            return PtyAgent(callbacks, name, stage, stage_idx, model_name)
