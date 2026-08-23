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

        # 04 阶段读事实包，其余阶段读上一阶段产出（A3 的 1.1 / 3.5）。
        if stage == "04-review":
            facts = self._read_fact_pack(task_name)
            if facts:
                parts.append(facts)
        else:
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

    #: 注入 04 prompt 的事实文件，及其顺序。
    #: `03-coding.md` **不在其中，也不得被加入** —— 那正是 C1 的来源。
    _FACT_ORDER = ("spec.md", "plan.md", "diff.stat", "diff.numstat",
                   "tests.json", "diff.truncated", "diff.patch")

    def _read_fact_pack(self, task_name: str) -> Optional[str]:
        """把 `facts/` 注入 04 的 prompt，替代 developer 的自述。

        C1（上下文污染）的直接落点：改造前这里读 `03-coding.md`，于是 04
        拿到的是「我做完了，测试都过了」这类叙述，复核的对象是**叙述**
        而不是事实。现在只注入 harness 用确定性程序生成的内容。

        事实包不存在时返回**显式的缺失说明**，不静默回落到读 md ——
        回落等于 C1 复原，且无人知晓（A3 的第 10 节把这条列为决策记录项）。
        """
        facts_dir = TASKS / task_name / "facts"
        if not facts_dir.is_dir():
            return ("=== 事实包（缺失）===\n"
                    "harness 未能生成本任务的事实包。**不得据此判定通过** ——\n"
                    "审查所需的客观输入不可得，应按 unavailable 处置并汇报。")

        chunks = [
            "=== 审查事实（由 harness 生成，developer 未参与编辑）===",
            "本阶段**不提供** 03-coding 阶段的自由叙述：审查对象是事实，不是自述。",
        ]
        for name in self._FACT_ORDER:
            path = facts_dir / name
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                continue
            chunks.append(f"--- facts/{name} ---\n{content}")
        if len(chunks) == 2:
            chunks.append("（事实包为空 —— 同样不得据此判定通过）")
        return "\n\n".join(chunks)

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
