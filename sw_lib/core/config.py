"""
sw_lib.config — 路径定义、阶段声明及配置模型化管理。

该模块负责：
1. 定义全局路径常量 (ROOT, TASKS, TPLS 等)。
2. 提供强类型的配置模型 (HarnessConfig) 封装 workflow/harness/config.yaml。
3. 提供向后兼容的配置查询接口。
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set

# ── 路径常量 ──

ROOT = Path(__file__).resolve().parent.parent.parent  # 项目根目录 (harness-flow/)
CONFIG_DIR = ROOT / "config"
HOOKS_DIR = ROOT / "hooks"
TPLS = ROOT / "templates"
WORKSPACE = ROOT / "workspace"
TASKS = WORKSPACE / "tasks"
STATUS = WORKSPACE / "STATUS.json"
STATUS_OLD = WORKSPACE / "STATUS.md"
TRASH = TASKS / ".trash"

# ── 阶段定义 ──

STAGES = ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]
STAGE_NAMES = ["头脑风暴", "规划", "编码", "评审", "归档"]

# ── 配置模型 ──

@dataclass
class RoleConfig:
    """单个 AI 角色/座席的配置"""
    agent: str = "gemini"
    model: str = "gemini-2.0-flash"
    description: str = ""
    tools: List[str] = field(default_factory=lambda: ["list_files", "read_file", "ask_user"])

@dataclass
class MockAgentConfig:
    """Mock Agent 相关配置"""
    enabled: bool = False
    response_delay: float = 1.0
    responses: Dict[str, str] = field(default_factory=dict)

@dataclass
class HarnessConfigModel:
    """config/config.yaml 的根数据模型"""
    roles: Dict[str, RoleConfig] = field(default_factory=dict)
    stage_roles: Dict[str, str] = field(default_factory=dict)
    auto_advance: bool = False
    mock_agent: MockAgentConfig = field(default_factory=MockAgentConfig)

class ConfigManager:
    """配置加载与管理单例"""
    _instance: Optional['ConfigManager'] = None
    _config: Optional[HarnessConfigModel] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    @property
    def config(self) -> HarnessConfigModel:
        """获取加载后的配置模型（带缓存）"""
        if self._config is None:
            self.reload()
        return self._config

    def reload(self):
        """重新从磁盘加载 YAML 配置文件"""
        raw_data = self._load_raw_yaml()
        harness_data = raw_data.get("harness", {})
        
        # 解析 roles
        roles = {}
        for role_id, role_dict in harness_data.get("roles", {}).items():
            roles[role_id] = RoleConfig(
                agent=role_dict.get("agent", "gemini"),
                model=role_dict.get("model", "gemini-2.0-flash"),
                description=role_dict.get("description", ""),
                tools=role_dict.get("tools", ["list_files", "read_file", "ask_user"])
            )
            
        # 解析 mock_agent
        mock_data = harness_data.get("mock_agent", {})
        mock_cfg = MockAgentConfig(
            enabled=mock_data.get("enabled", False),
            response_delay=mock_data.get("response_delay", 1.0),
            responses=mock_data.get("responses", {})
        )
        
        self._config = HarnessConfigModel(
            roles=roles,
            stage_roles=harness_data.get("stage_roles", {}),
            auto_advance=harness_data.get("auto_advance", False),
            mock_agent=mock_cfg
        )

    def _load_raw_yaml(self) -> Dict[str, Any]:
        config_path = CONFIG_DIR / "config.yaml"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}

# 初始化全局管理器
_manager = ConfigManager()

# ── 兼容性查询接口 ──

def load_harness_config() -> Dict[str, Any]:
    """【旧接口兼容】加载原始配置字典"""
    return _manager._load_raw_yaml()

KNOWN_AGENT_TYPES: Set[str] = {"gemini", "opencode", "claudecode", "codex"}

def resolve_agent_type(stage: str, agent_override: Optional[str] = None) -> str:
    """根据阶段或显式指定确定 agent 客户端类型。"""
    cfg = _manager.config
    
    # 1. 显式角色名 -> 取该角色的 agent 字段
    if agent_override and agent_override in cfg.roles:
        return cfg.roles[agent_override].agent

    # 2. 显式类型名 (gemini / opencode ...)
    if agent_override and agent_override.lower() in KNOWN_AGENT_TYPES:
        return agent_override.lower()

    # 3. 显式模型名推断 (从 agent_override 字符串推断)
    if agent_override and agent_override not in ("N/A", ""):
        m_lower = agent_override.lower()
        if "gemini" in m_lower: return "gemini"
        if "opencode" in m_lower: return "opencode"

    # 4. 阶段对应角色的 agent 字段
    role_id = cfg.stage_roles.get(stage)
    if role_id and role_id in cfg.roles:
        return cfg.roles[role_id].agent

    # 5. 从当前阶段角色绑定的 model 名称推断兜底
    model = _resolve_model_fallback(stage, agent_override)
    if model:
        m_lower = model.lower()
        if "gemini" in m_lower: return "gemini"
        if "opencode" in m_lower: return "opencode"

    return "gemini"

def _resolve_model_fallback(stage: str, agent_override: Optional[str]) -> Optional[str]:
    """内部兜底解析逻辑"""
    cfg = _manager.config
    
    if agent_override in cfg.roles:
        return cfg.roles[agent_override].model

    role_id = cfg.stage_roles.get(stage)
    if role_id in cfg.roles:
        return cfg.roles[role_id].model

    if agent_override and agent_override not in ("N/A", "gemini", "opencode"):
        if "-" in agent_override:
            return agent_override
    return None

def resolve_agent_model(stage: str, agent_override: Optional[str] = None) -> str:
    """
    根据当前阶段映射到具体的模型名称。
    优先级: agent_override (角色名) > stage_roles > agent_override (模型名) > 兜底
    """
    cfg = _manager.config
    
    # 1. 角色名匹配
    if agent_override and agent_override in cfg.roles:
        return cfg.roles[agent_override].model

    # 2. 阶段匹配
    role_id = cfg.stage_roles.get(stage)
    if role_id and role_id in cfg.roles:
        model = cfg.roles[role_id].model
        # 兼容性：如果 override 是 "gemini" 且阶段默认模型也是 gemini，则使用默认
        if agent_override == "gemini" and "gemini" in model.lower():
            return model
        if not agent_override or agent_override in ("N/A", "", role_id):
            return model

    # 3. 模型名匹配 (带横线的通常是模型名)
    if agent_override and agent_override not in ("N/A", "", "gemini", "opencode"):
        if "-" in agent_override:
            return agent_override

    # 4. 显式类型兜底
    if agent_override == "gemini": return "gemini-2.0-flash"
    if agent_override == "opencode": return "opencode"
    if agent_override and agent_override not in ("N/A", ""): return agent_override

    # 5. 错误抛出
    raise ValueError(f"无法为阶段 '{stage}' (角色 ID: {role_id}) 找到有效的模型配置。")

def get_tools_for_stage(stage: str) -> List[str]:
    """获取当前阶段允许的工具列表"""
    cfg = _manager.config
    role_id = cfg.stage_roles.get(stage)
    if role_id and role_id in cfg.roles:
        return cfg.roles[role_id].tools
    return ["list_files", "read_file", "ask_user"]

def is_auto_advance() -> bool:
    """检查是否启用自动推进"""
    return _manager.config.auto_advance

def is_mock_agent() -> bool:
    """检查是否启用 Mock Agent"""
    return _manager.config.mock_agent.enabled

# ── Rich 组件延迟加载 ──

try:
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Console
    from rich import box
    HAS_RICH = True
except ImportError:
    Layout = Live = Panel = Text = Console = box = None  # type: ignore
    HAS_RICH = False
