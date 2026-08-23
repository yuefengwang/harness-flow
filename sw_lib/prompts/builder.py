"""PromptBuilder — builds agent context from YAML templates.

All prompt text lives in YAML files under sw_lib/prompts/templates/.
"""

from typing import Optional, Dict, Any

from .registry import PromptRegistry
from ..core.config import TASKS, STAGES, STAGE_NAMES, HOOKS_DIR
from ..core.state import read_state


class PromptBuilder:
    """Builds the full agent prompt by composing YAML templates with runtime data.

    Usage:
        registry = PromptRegistry(templates_dir)
        builder = PromptBuilder(registry)
        prompt = builder.build(
            task_name="my-task",
            stage="01-brainstorming",
            stage_idx=0,
            previous_output={"ambiguity_score": 8},
        )
    """

    def __init__(self, registry: PromptRegistry):
        self.registry = registry

    def build(
        self,
        task_name: str,
        stage: str,
        stage_idx: int,
        previous_output: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Build the complete agent prompt for a stage.

        Returns None if no content can be assembled.
        """
        stage_name = STAGE_NAMES[stage_idx] if stage_idx < len(STAGE_NAMES) else "未知"
        template = self.registry.get(stage)
        parts = []

        global_rules = self._read_global_rules() or "（未定义全局规范）"

        system_prompt = template["system_prompt"].format(
            task_name=task_name,
            stage=stage,
            stage_name=stage_name,
            global_rules=global_rules,
        )
        parts.append(system_prompt)

        orchestration = self.registry.get_system_rules()
        if orchestration:
            parts.append(orchestration)

        project_info = self._build_project_info(task_name)
        if project_info:
            parts.append(project_info)

        previous = self._read_previous_stage(task_name, stage_idx)
        if previous:
            parts.append(previous)

        current_tpl = self._read_current_template(task_name, stage, stage_name)
        if current_tpl:
            parts.append(current_tpl)

        task_ctx = self._read_task_context(task_name)
        if task_ctx:
            parts.append(task_ctx)

        hook_rules = self._read_hook_rules(stage)
        if hook_rules:
            parts.append(hook_rules)

        if not parts:
            return None

        return "\n\n".join(parts)

    def _build_project_info(self, task_name: str) -> Optional[str]:
        st = read_state(task_name)
        target_dir = st.get("target_dir", "")
        if not target_dir:
            return None
        return (
            "=== 项目信息 ===\n"
            f"代码生成目录: {target_dir}\n"
            "所有的业务代码、模板、静态文件等都应生成到此目录下。"
        )

    def _read_global_rules(self) -> Optional[str]:
        """探测并读取通用的全局项目规则文件。"""
        from ..core.config import ROOT
        
        # 兼容列表，按优先级探测
        possible_rule_files = [
            "INSTRUCTIONS.md", 
            "PROJECT_RULES.md", 
            "GEMINI.md", 
            "Claude.skills",
            ".cursorrules"
        ]
        
        for filename in possible_rule_files:
            rule_path = ROOT / filename
            if rule_path.exists():
                content = rule_path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    return f"=== 全局项目规范 ({filename}) ===\n{content}"
        return None

    def _read_previous_stage(self, task_name: str, stage_idx: int) -> Optional[str]:
        if stage_idx == 0:
            return None
        prev_stage = STAGES[stage_idx - 1]
        path = TASKS / task_name / f"{prev_stage}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                return f"=== 前一阶段产出 ({prev_stage} / {STAGE_NAMES[stage_idx - 1]}) ===\n{content}"
        return None

    def _read_current_template(self, task_name: str, stage: str, stage_name: str) -> Optional[str]:
        path = TASKS / task_name / f"{stage}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                return f"=== 当前阶段模板 ({stage} / {stage_name}) ===\n{content}"
        return None

    def _read_task_context(self, task_name: str) -> Optional[str]:
        path = TASKS / task_name / ".context"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                return f"=== 任务需求 ===\n{content}"
        return None

    def _read_hook_rules(self, stage: str) -> Optional[str]:
        path = HOOKS_DIR / f"{stage}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                # 段标题**不带文件路径**：agent 的 workdir 是 repo/<task>，
                # 一个指向 harness 根的路径会诱导它 glob 越界 —— 任务
                # `newtask` 就是这样卡死 26 分钟的（触发 opencode 的
                # external_directory 判定 → ask → 无人应答）。
                # 讽刺的是规则全文就在下面，那次 glob 完全没必要。
                # 所以这里明说「已完整内联」，堵掉它去找文件的动机。
                return (f"=== 强制规则 ({stage}) ===\n"
                        f"（以下为本阶段全部强制规则，已完整内联，"
                        f"无需读取或查找任何规则文件）\n{content}")
        return None
