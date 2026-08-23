"""
sw_lib.config — 路径定义、阶段声明及配置模型化管理。

该模块负责：
1. 定义全局路径常量 (ROOT, TASKS, TPLS 等)。
2. 提供强类型的配置模型 (HarnessConfig) 封装 workflow/harness/config.yaml。
3. 提供向后兼容的配置查询接口。
"""

import os

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
TRASH = TASKS / ".trash"

# ── 阶段定义 ──

STAGES = ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]
STAGE_NAMES = ["头脑风暴", "规划", "编码", "评审", "归档"]
MAX_REROUTE = 3  # 最大返工循环次数

# ── 钩子超时（分钟）──
# check_03-coding.sh / check_04-review.sh 会在任务目录里跑 pytest / npm test，
# 必须有上限，否则钩子挂住会让 CLI 与 gate 校验永久等待。
HOOK_TIMEOUT_MINUTES = 2.0
HOOK_TIMEOUT_SECONDS = HOOK_TIMEOUT_MINUTES * 60.0

# ── 自动推进 ──
# 自动模式下单次会话最多连续推进的阶段数，防止配置错误导致无限循环。
# 5 个阶段跑完一轮即到 archive，留一点余量给 04-review 的返工。
AUTO_ADVANCE_MAX_STAGES = 12

# ── 配置模型 ──

@dataclass
class RoleConfig:
    """单个 AI 角色/座席的配置"""
    agent: str = "gemini"
    model: str = "gemini-2.0-flash"
    description: str = ""
    tools: List[str] = field(default_factory=lambda: ["list_files", "read_file", "ask_user"])
    # 显式声明的 provider。留空时由 model / agent 推断（见下）。
    # 允许显式覆盖是为了 A4 的 R3：自建网关可能用一个前缀代理多家模型，
    # 那时字符串推断给出的答案是错的。
    provider_override: Optional[str] = None

    def __init__(self, agent: str = "gemini", model: str = "gemini-2.0-flash",
                 description: str = "", tools: Optional[List[str]] = None,
                 provider: Optional[str] = None,
                 provider_override: Optional[str] = None):
        self.agent = agent
        self.model = model
        self.description = description
        self.tools = list(tools) if tools is not None else [
            "list_files", "read_file", "ask_user"]
        self.provider_override = provider_override or provider

    @property
    def provider(self) -> str:
        """角色所属的模型供应方（A4 的 3.5）。

        `opencode/mimo-v2.5-free` -> `opencode`；无 `/` 时回落到 `agent` 字段。

        ⚠️ 这是**廉价代理指标，不是先验独立性的证明**（A4 的 U4-3）：
        同一家的两个模型 provider 相同会被拒，而不同家的模型若共享训练数据，
        盲区仍可能重合。真正的证明只能来自 A11 的变异探针。
        """
        if self.provider_override:
            return self.provider_override
        if "/" in self.model:
            return self.model.split("/", 1)[0]
        return self.agent


class ConfigError(Exception):
    """配置存在 error 级问题，拒绝按兜底值继续（A4 的 3.4）。"""


@dataclass
class ConfigIssue:
    """一条配置问题。severity 为 `error` 或 `warn`（A4 的 3.4，三态不得二态化）。"""
    severity: str
    code: str
    message: str


@dataclass
class SubjectiveReviewer:
    """04-review 的一个主观审查者（A4 的 3.2）。"""
    role: str
    model: str = ""
    kind: str = ""
    tools: Optional[List[str]] = None


@dataclass
class ReviewConfig:
    """harness.review 的解析结果。供 A5 的图构建消费。"""
    objective_enabled: bool = True
    subjective: List[SubjectiveReviewer] = field(default_factory=list)
    require_heterogeneous: bool = False
    heterogeneous_status: str = "unknown"

@dataclass
class MockAgentConfig:
    """Mock Agent 相关配置"""
    enabled: bool = False
    response_delay: float = 1.0
    responses: Dict[str, str] = field(default_factory=dict)
    review_route: str = "05-Archive"

@dataclass
class HarnessConfigModel:
    """config/config.yaml 的根数据模型"""
    roles: Dict[str, RoleConfig] = field(default_factory=dict)
    stage_roles: Dict[str, str] = field(default_factory=dict)
    auto_advance: bool = False
    auto_answer: bool = False
    mock_agent: MockAgentConfig = field(default_factory=MockAgentConfig)
    repo_path: str = "repo"
    review: ReviewConfig = field(default_factory=ReviewConfig)

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
                tools=role_dict.get("tools", ["list_files", "read_file", "ask_user"]),
                provider=role_dict.get("provider"),
            )
            
        # 解析 mock_agent
        mock_data = harness_data.get("mock_agent", {})
        mock_cfg = MockAgentConfig(
            enabled=mock_data.get("enabled", False),
            response_delay=mock_data.get("response_delay", 1.0),
            responses=mock_data.get("responses", {})
        )

        review_cfg = self._parse_review(harness_data.get("review", {}))
        
        self._config = HarnessConfigModel(
            roles=roles,
            stage_roles=harness_data.get("stage_roles", {}),
            auto_advance=harness_data.get("auto_advance", False),
            auto_answer=harness_data.get("auto_answer", False),
            mock_agent=mock_cfg,
            repo_path=harness_data.get("repo_path", "repo"),
            review=review_cfg,
        )

    @staticmethod
    def _parse_review(review_data: Dict[str, Any]) -> ReviewConfig:
        """解析 harness.review（A4 的 3.2）。

        缺省即回落到单角色：`subjective` 为空时 `resolve_stage_roles` 走
        `stage_roles`，与引入本段之前行为一致（A4 的第 10 节回滚路径）。
        """
        if not isinstance(review_data, dict):
            return ReviewConfig()
        objective = review_data.get("objective", {})
        if not isinstance(objective, dict):
            objective = {}
        reviewers: List[SubjectiveReviewer] = []
        for item in review_data.get("subjective") or []:
            if not isinstance(item, dict):
                continue
            reviewers.append(SubjectiveReviewer(
                role=str(item.get("role", "")),
                model=str(item.get("model", "")),
                kind=str(item.get("kind", "")),
                tools=item.get("tools"),
            ))
        return ReviewConfig(
            objective_enabled=bool(objective.get("enabled", True)),
            subjective=reviewers,
            # 默认 false 是为了不阻塞既有的同构配置（A4 的 R2），
            # **不是因为同构可接受** —— 关闭时 resolve_review_config()
            # 会把状态标成 degraded，供 A10 的报告标注「审查者同构」。
            require_heterogeneous=bool(review_data.get("require_heterogeneous", False)),
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

KNOWN_AGENT_TYPES: Set[str] = {"gemini", "opencode", "claudecode", "codex"}

def resolve_agent_type(stage: str, agent_override: Optional[str] = None,
                       role_id: Optional[str] = None) -> str:
    """根据阶段或显式指定确定 agent 客户端类型。"""
    cfg = _manager.config

    # 0. 显式 role_id（A4 的 3.3）。它比 agent_override 更明确：
    #    调用方已经知道自己要哪个角色，不需要再走字符串推断。
    #    未知 role_id **抛错而不回落** —— 静默回落会让「纯只读的
    #    design_critic」悄悄拿到 stage 默认角色的 run_command（A4 的 R5）。
    if role_id is not None:
        return _role_or_raise(role_id).agent

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
    stage_role = cfg.stage_roles.get(stage)
    if stage_role and stage_role in cfg.roles:
        return cfg.roles[stage_role].agent

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

def resolve_agent_model(stage: str, agent_override: Optional[str] = None,
                        role_id: Optional[str] = None) -> str:
    """
    根据当前阶段映射到具体的模型名称。
    优先级: agent_override (角色名) > stage_roles > agent_override (模型名) > 兜底
    """
    cfg = _manager.config

    # 0. 显式 role_id（A4 的 3.3）。subjective[].model 覆盖 roles[role].model，
    #    这样「同一个角色跑两种模型」不必新增角色定义。
    if role_id is not None:
        role = _role_or_raise(role_id)
        entry = _subjective_entry(role_id)
        if entry is not None and entry.model:
            return entry.model
        return role.model

    # 1. 角色名匹配
    if agent_override and agent_override in cfg.roles:
        return cfg.roles[agent_override].model

    # 2. 阶段匹配
    stage_role = cfg.stage_roles.get(stage)
    if stage_role and stage_role in cfg.roles:
        model = cfg.roles[stage_role].model
        # 兼容性：如果 override 是 "gemini" 且阶段默认模型也是 gemini，则使用默认
        if agent_override == "gemini" and "gemini" in model.lower():
            return model
        if not agent_override or agent_override in ("N/A", "", stage_role):
            return model

    # 3. 模型名匹配 (带横线的通常是模型名)
    if agent_override and agent_override not in ("N/A", "", "gemini", "opencode"):
        if "-" in agent_override:
            return agent_override

    # 4. 显式类型兜底
    if agent_override == "gemini": return "gemini-2.0-flash"
    if agent_override == "opencode": return "opencode"
    if agent_override and agent_override not in ("N/A", ""): return agent_override

    # 5. 完全匹配不到时的最终兜底
    return "gemini-2.0-flash"

def get_tools_for_stage(stage: str, role_id: Optional[str] = None) -> List[str]:
    """获取当前阶段允许的工具列表。

    `role_id` 非空时按角色解析（A4 的 3.3）；为 None 时行为与引入该参数
    之前逐字节相同（验收 9 的判据）。
    """
    if role_id is not None:
        return get_tools_for_role(role_id, stage)
    cfg = _manager.config
    stage_role = cfg.stage_roles.get(stage)
    if stage_role and stage_role in cfg.roles:
        return cfg.roles[stage_role].tools
    return ["list_files", "read_file", "ask_user"]


# ── A4：多角色解析与配置校验 ──

#: `review.subjective[].kind` 的已知取值。A5 按 kind 决定派发哪一路审查，
#: 写错了图会少一条分支 —— 所以是 error 而非 warn。
KNOWN_REVIEW_KINDS: Set[str] = {"counterexample", "design_review"}

#: 不含 `/` 也合法的模型别名。除此以外无 `/` 的模型名记 warn（A4 的 3.4）。
_KNOWN_BARE_MODELS: Set[str] = {"opencode", "mock"}


def _role_or_raise(role_id: str) -> RoleConfig:
    """按 role_id 取角色，未知即抛（A4 的 2.3 / 3.4）。

    改造前这里静默返回兜底值，导致「配置声称异构、实际同构，且无人知晓」。
    """
    cfg = _manager.config
    role = cfg.roles.get(role_id)
    if role is None:
        raise ConfigError(
            f"未知角色 {role_id!r}：harness.roles 中没有这个键。"
            f"已定义的角色：{sorted(cfg.roles)}")
    return role


def _subjective_entry(role_id: str) -> Optional[SubjectiveReviewer]:
    """取 review.subjective 中该角色的条目；不在其中返回 None。"""
    for item in _manager.config.review.subjective:
        if item.role == role_id:
            return item
    return None


def _model_looks_suspicious(model: str) -> bool:
    name = (model or "").strip()
    if not name:
        return True
    if "/" in name:
        return False
    return name.lower() not in _KNOWN_BARE_MODELS


def _heterogeneity_issues(cfg: "HarnessConfigModel") -> List[ConfigIssue]:
    """审查者异构性校验（A4 的 3.5）。

    两条规则，第二条最容易被漏：
      1. 各 subjective 审查者的 provider 互不相同。
      2. 每个审查者的 provider 都与 **developer**（作者）不同 ——
         C2 的原始诉求是「审查者与作者盲区不重合」，只校验审查者之间
         互异是不够的。
    """
    review = cfg.review
    if len(review.subjective) < 1:
        return []

    known = [r for r in review.subjective if r.role in cfg.roles]
    if not known:
        return []  # 角色未知已由 subjective_role_unknown 报过，不重复报

    def provider_of(entry: SubjectiveReviewer) -> str:
        role = cfg.roles[entry.role]
        if entry.model and not role.provider_override:
            return entry.model.split("/", 1)[0] if "/" in entry.model else role.agent
        return role.provider

    violations: List[str] = []

    seen: Dict[str, str] = {}
    for entry in known:
        provider = provider_of(entry)
        if provider in seen:
            violations.append("reviewers_share_provider")
            break
        seen[provider] = entry.role

    author_role = cfg.stage_roles.get("03-coding")
    author = cfg.roles.get(author_role) if author_role else None
    if author is not None:
        for entry in known:
            if provider_of(entry) == author.provider:
                violations.append("reviewer_shares_provider_with_developer")
                break

    if not violations:
        return []

    # mock 只有一个 provider，校验无意义 —— 但**不记为通过**（A4 的第 4 节）。
    if cfg.mock_agent.enabled:
        return []

    if not review.require_heterogeneous:
        # 降级不得静默：A10 的达成度报告须据此标注「审查者同构」（A4 的 3.5）。
        return [ConfigIssue(
            "warn", "reviewers_homogeneous_degraded",
            "审查者与作者/彼此共用同一 provider，而 require_heterogeneous 为 false："
            f"{sorted(set(violations))}。C2 的盲区重合风险未被消除，报告须标注同构。")]

    return [ConfigIssue(
        "error", code,
        "require_heterogeneous 为 true，但审查者的 provider 与"
        + ("其他审查者" if code == "reviewers_share_provider" else "developer")
        + "相同 —— 同权重模型盲区重合，审查会为实现的误解背书。")
        for code in sorted(set(violations))]


def validate_config() -> List[ConfigIssue]:
    """检查配置，返回问题清单（A4 的 3.4）。空列表表示无问题。

    三态，不得二态化：`error` 拒绝启动，`warn` 记录后继续。
    兜底本身不是错的设计，错的是**兜底而不告知** —— 所以解析函数仍保留
    兜底值以免既有流程崩，但问题必须在这里被列出来。
    """
    cfg = _manager.config
    issues: List[ConfigIssue] = []

    for stage, role_id in (cfg.stage_roles or {}).items():
        if isinstance(role_id, (list, tuple, set)):
            issues.append(ConfigIssue(
                "error", "stage_role_not_scalar",
                f"stage_roles[{stage}] 是集合类型。多审查者请写在 "
                f"harness.review.subjective 下（A4 的 3.1），"
                f"stage_roles 仍是一阶段一角色。"))
            continue
        if role_id not in (cfg.roles or {}):
            issues.append(ConfigIssue(
                "error", "stage_role_unknown",
                f"stage_roles[{stage}] = {role_id!r} 不存在于 harness.roles。"
                f"改造前此处静默退化为 gemini 兜底（A4 的 2.3）。"))

    for role_id, role in (cfg.roles or {}).items():
        if _model_looks_suspicious(role.model):
            issues.append(ConfigIssue(
                "warn", "model_format_suspicious",
                f"roles[{role_id}].model = {role.model!r} 不含 provider 前缀，"
                f"也不是已知别名。模型是否真实存在无法在配置层校验（U4-2）。"))

    review = cfg.review
    for entry in review.subjective:
        if entry.role not in (cfg.roles or {}):
            issues.append(ConfigIssue(
                "error", "subjective_role_unknown",
                f"review.subjective 中的 role={entry.role!r} 不存在于 harness.roles。"))
        if entry.kind not in KNOWN_REVIEW_KINDS:
            issues.append(ConfigIssue(
                "error", "subjective_kind_unknown",
                f"review.subjective[{entry.role}].kind = {entry.kind!r} 不是已知值。"
                f"已知：{sorted(KNOWN_REVIEW_KINDS)}。"))

    if not review.subjective and not review.objective_enabled:
        issues.append(ConfigIssue(
            "error", "review_has_no_reviewer",
            "review.subjective 为空且 objective.enabled 为 false —— "
            "04 阶段将没有任何审查者，等于无条件放行。"))

    issues.extend(_heterogeneity_issues(cfg))
    return issues


def assert_config_valid() -> None:
    """有 error 级问题就抛 `ConfigError`（A4 的 3.4）。

    为什么「指向不存在的角色」必须响亮失败：A4 的全部目的是用不同 provider
    消除先验盲区，而一个拼写错误就让机制失效且无人知晓。
    """
    issues = validate_config()
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        detail = "\n".join(f"  [{i.code}] {i.message}" for i in errors)
        raise ConfigError(f"config.yaml 存在 {len(errors)} 处配置错误：\n{detail}")


def resolve_stage_roles(stage: str) -> List[str]:
    """一个 stage 的全部角色 id（A4 的 3.3）。

    04-review 且 review.subjective 非空时返回其中的 role 列表；
    否则返回 stage_roles 的单元素列表（向后兼容）。
    """
    cfg = _manager.config
    if stage == "04-review" and cfg.review.subjective:
        return [r.role for r in cfg.review.subjective]
    stage_role = (cfg.stage_roles or {}).get(stage)
    if isinstance(stage_role, str) and stage_role:
        return [stage_role]
    return []


def resolve_review_config() -> ReviewConfig:
    """解析 harness.review，并填好异构性状态。供 A5 的图构建消费。

    `heterogeneous_status` 四态：
      - `ok`           校验通过（或无审查者可比）
      - `degraded`     存在同构，但 require_heterogeneous 为 false
      - `mock_skipped` mock 模式下跳过，**不记为通过**
      - `error`        require_heterogeneous 为 true 且被违反
    """
    cfg = _manager.config
    review = cfg.review
    status = "ok"
    if cfg.mock_agent.enabled and review.subjective:
        providers = [
            cfg.roles[r.role].provider
            for r in review.subjective if r.role in cfg.roles
        ]
        author_role = cfg.stage_roles.get("03-coding")
        author = cfg.roles.get(author_role) if author_role else None
        clashes = len(set(providers)) < len(providers) or (
            author is not None and author.provider in providers)
        status = "mock_skipped" if clashes else "ok"
    else:
        codes = {i.code for i in _heterogeneity_issues(cfg)}
        if "reviewers_homogeneous_degraded" in codes:
            status = "degraded"
        elif codes:
            status = "error"
    return ReviewConfig(
        objective_enabled=review.objective_enabled,
        subjective=list(review.subjective),
        require_heterogeneous=review.require_heterogeneous,
        heterogeneous_status=status,
    )


def get_tools_for_role(role_id: Optional[str], stage: str) -> List[str]:
    """按角色解析工具权限（A4 的 3.3）。

    `role_id` 为 None 时回落到 `get_tools_for_stage`。
    `review.subjective[].tools` 非空时覆盖 `roles[role].tools` ——
    这让「同一角色在不同阶段拿不同权限」不必复制角色定义。

    ⚠️ 强度声明（A4 的 1.3）：本函数只**解析**权限，不实施。
    真正拦住写入的是 agent 侧的规则，而 A0 的 2.9.5 实测 `bash` 可绕过
    路径 deny —— 「纯只读」是约定而非保证。
    """
    if role_id is None:
        return get_tools_for_stage(stage)
    role = _role_or_raise(role_id)
    entry = _subjective_entry(role_id)
    if entry is not None and entry.tools:
        return list(entry.tools)
    return list(role.tools)

def is_auto_advance() -> bool:
    """检查是否启用自动推进"""
    return _manager.config.auto_advance

def set_auto_advance(enabled: bool) -> None:
    """运行期覆写自动推进开关（供 CLI 的 --auto / --manual 使用）。

    只改内存中的配置，不回写 config.yaml —— 命令行开关应当是一次性的。
    """
    _manager.config.auto_advance = bool(enabled)

def is_auto_answer() -> bool:
    """检查是否自动代答 agent 的结构化提问（无人值守模式）。

    与 auto_advance 正交：auto_advance 决定阶段边界是否等 /advance，
    auto_answer 决定阶段内的提问是否交还用户。
    """
    return _manager.config.auto_answer

def set_auto_answer(enabled: bool) -> None:
    """运行期覆写自动代答开关（供 CLI 的 --unattended 使用）。"""
    _manager.config.auto_answer = bool(enabled)

# `sw init --mock` 的跨进程载体。见 is_mock_agent() 的 docstring。
MOCK_ENV_NAME = "SW_MOCK_AGENT"


def is_mock_agent() -> bool:
    """检查是否启用 Mock Agent。环境变量优先于 config.yaml。

    **为什么需要环境变量这一层**（由 e2e 实测抓出，单元测试全绿时漏掉了）：

    `sw init --mock` 原先只改主进程内存里的 `mock_agent.enabled`，不写
    `config.yaml`。而钩子是**独立子进程**，重新加载配置文件后看到的是
    `enabled: false` —— 于是主进程用 mock 的固定密钥给 `.state` 签名，
    钩子却用真实 `config/.evidence_key` 校验，必然 `tampered`：

        ❌ 证据签名校验未通过（tampered）

    03 阶段因此永久无法准出（`tests/e2e-flow/driver.py` 实测卡死）。
    签名域分裂只在跨进程时出现，所以进程内的单元测试测不到它。

    用环境变量而不是回写 `config.yaml`：`--mock` 是一次性的命令行开关，
    固化进用户配置会让下一次**不带标志**的运行也悄悄走 MockAgent。

    显式的 `"0"` 表示关闭并回落到配置文件，不是「未设置」—— 否则一旦某处
    顺手设了这个变量，用户的真实运行会被静默替换成 MockAgent。
    """
    raw = os.environ.get(MOCK_ENV_NAME)
    if raw is not None and raw != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return _manager.config.mock_agent.enabled


def set_mock_agent(enabled: bool) -> None:
    """运行期切换 MockAgent，并把开关导出给子进程继承。

    两处一起写：内存配置供当前进程用，环境变量供钩子等子进程用。
    只改一处就是前述 `tampered` 的成因。
    """
    _manager.config.mock_agent.enabled = bool(enabled)
    os.environ[MOCK_ENV_NAME] = "1" if enabled else "0"

def get_repo_path() -> str:
    """获取生成代码的默认输出目录"""
    return _manager.config.repo_path

# ── Deploy 角色配置解析 ──

DEPLOY_ROLE_ID = "ops"

def resolve_deploy_agent_type() -> str:
    """解析部署角色使用的 Agent 类型"""
    cfg = _manager.config
    if DEPLOY_ROLE_ID in cfg.roles:
        return cfg.roles[DEPLOY_ROLE_ID].agent
    return "opencode"

def resolve_deploy_agent_model() -> str:
    """解析部署角色使用的模型名称"""
    cfg = _manager.config
    if DEPLOY_ROLE_ID in cfg.roles:
        return cfg.roles[DEPLOY_ROLE_ID].model
    return "opencode/mimo-v2.5-free"

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
