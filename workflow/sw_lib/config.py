"""sw_lib.config — paths, stage definitions, and optional rich imports"""

import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # project root (harness-flow/)
WORKFLOW = ROOT / "workflow"
TASKS = WORKFLOW / "tasks"
TPLS = WORKFLOW / "templates"
STATUS = WORKFLOW / "STATUS.md"
TRASH = TASKS / ".trash"

STAGES = ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]
STAGE_NAMES = ["头脑风暴", "规划", "编码", "评审", "归档"]

def load_harness_config():
    """从 workflow/harness/config.yaml 加载配置"""
    config_path = WORKFLOW / "harness" / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}

KNOWN_AGENT_TYPES = {"gemini", "opencode", "claudecode", "codex"}


def resolve_agent_type(stage, agent_override=None):
    """根据阶段或显式指定确定 agent 客户端类型。

    优先级: agent_override (作为角色名 → 从 roles 中取 agent 字段)
            > agent_override (作为已知类型名如 'gemini')
            > config.yaml stage_roles → roles → agent 字段
            > 从 model 名称推断（gemini-*→gemini, opencode→opencode）
    """
    cfg = load_harness_config()
    harness = cfg.get("harness", {})
    stage_roles = harness.get("stage_roles", {})
    roles = harness.get("roles", {})

    # 1. 显式角色名 → 取该角色的 agent 字段
    if agent_override and agent_override in roles:
        agent = roles[agent_override].get("agent")
        if agent:
            return agent

    # 2. 显式类型名（gemini / opencode / claudecode / codex）
    if agent_override and agent_override.lower() in KNOWN_AGENT_TYPES:
        return agent_override.lower()

    # 3. 阶段对应角色的 agent 字段
    role_id = stage_roles.get(stage)
    if role_id and role_id in roles:
        agent = roles[role_id].get("agent")
        if agent:
            return agent

    # 4. 从 model 名称推断兜底
    model = _resolve_model_fallback(stage, agent_override, cfg)
    if model:
        if model.lower().startswith("gemini"):
            return "gemini"
        if model.lower().startswith("opencode"):
            return "opencode"

    return "gemini"


def _resolve_model_fallback(stage, agent_override, cfg=None):
    """内部兜底：返回 model 字符串，仅供 resolve_agent_type 和 resolve_agent_model 复用"""
    if cfg is None:
        cfg = load_harness_config()
    harness = cfg.get("harness", {})
    stage_roles = harness.get("stage_roles", {})
    roles = harness.get("roles", {})

    if agent_override in roles:
        model = roles[agent_override].get("model")
        if model:
            return model

    role_id = stage_roles.get(stage)
    if role_id in roles:
        model = roles[role_id].get("model")
        if model:
            return model

    if agent_override and agent_override not in ("N/A", "gemini", "opencode"):
        if "-" in agent_override:
            return agent_override

    return None


def resolve_agent_model(stage, agent_override=None):
    """
    根据当前阶段映射到具体的模型名称。
    优先级: agent_override (作为角色名) > config.yaml stage_roles > agent_override (作为模型名) > 报错
    """
    cfg = load_harness_config()
    harness = cfg.get("harness", {})
    stage_roles = harness.get("stage_roles", {})
    roles = harness.get("roles", {})

    # 1. 如果 override 是具体的角色名，则优先使用该角色的模型
    if agent_override in roles:
        model = roles[agent_override].get("model")
        if model:
            return model

    # 2. 查找当前阶段对应的角色模型
    role_id = stage_roles.get(stage)
    if role_id in roles:
        model = roles[role_id].get("model")
        if model:
            if agent_override == "gemini" and "gemini" in model.lower():
                return model
            if not agent_override or agent_override in ("N/A", "", role_id):
                return model

    # 3. 如果还是没找到，但用户指定了看起来像模型的东西（排除纯 "gemini"/"opencode"）
    if agent_override and agent_override not in ("N/A", "", "gemini", "opencode"):
        if "-" in agent_override:
            return agent_override

    # 4. 最后的兜底
    if agent_override == "gemini":
        return "gemini-2.0-flash"

    if agent_override == "opencode":
        return "opencode"

    if agent_override and agent_override not in ("N/A", ""):
        return agent_override

    # 5. 最终失败检查
    msg = f"无法为阶段 '{stage}' (角色: {role_id or '未定义'}) 找到配置的模型。"
    if not roles:
        msg += " harness/config.yaml 中未定义 roles。"
    raise ValueError(msg)


DEFAULT_TOOLS = ["list_files", "read_file"]


def get_tools_for_stage(stage):
    """从 config.yaml 获取当前阶段允许的工具列表"""
    cfg = load_harness_config()
    harness = cfg.get("harness", {})
    stage_roles = harness.get("stage_roles", {})
    roles = harness.get("roles", {})

    role_id = stage_roles.get(stage)
    if role_id and role_id in roles:
        tools = roles[role_id].get("tools")
        if tools:
            return tools
    return DEFAULT_TOOLS


def is_auto_advance():
    """检查 config.yaml 中的 auto_advance 配置"""
    cfg = load_harness_config()
    return cfg.get("harness", {}).get("auto_advance", False)


# Optional rich imports for MonitorTUI
try:
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Console
    from rich import box
    HAS_RICH = True
except ImportError:
    Layout = Live = Panel = Text = Console = box = None
    HAS_RICH = False
