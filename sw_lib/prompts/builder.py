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
        role_id: Optional[str] = None,
    ) -> Optional[str]:
        """Build the complete agent prompt for a stage.

        Returns None if no content can be assembled.
        """
        stage_name = STAGE_NAMES[stage_idx] if stage_idx < len(STAGE_NAMES) else "未知"
        template = self.registry.get(stage)
        parts = []

        # 阶段指令先落地，全局规范排在它**之后**（判例 2.9.19 的 sweep）。
        #
        # 位置由这里统一决定，不交给各 yaml 自己摆放。任务 `44444` 的现场：
        # 五份 yaml 全都用 `{global_rules}` 打头，展开后 17747 字符的
        # INSTRUCTIONS.md 占掉整份 prompt 的前 61%，阶段自己那 839 字符的
        # 指令被埋在中段，「先写测试」直到 92% 处才第一次出现。
        #
        # 修 03 一个文件不解决问题 —— 位置只要还由 yaml 各自决定，
        # 下一个新阶段照旧会写在开头（S10「修了一半」，同 2.9.9 第 2 条）。
        # 保留占位符支持是为了兼容：yaml 若显式写了 `{global_rules}`，
        # 就按它的位置渲染，不在末尾重复追加。
        global_rules = self._read_global_rules() or "（未定义全局规范）"
        raw_system = template["system_prompt"]

        system_prompt = raw_system.format(
            task_name=task_name,
            stage=stage,
            stage_name=stage_name,
            global_rules=global_rules,
            stage_file=str(TASKS / task_name / f"{stage}.md"),
        )
        parts.append(system_prompt)
        if "{global_rules}" not in raw_system:
            parts.append(
                "以下是全局项目规范（背景信息，"
                f"本阶段的具体要求以上面为准）：\n\n{global_rules}")

        orchestration = self.registry.get_system_rules()
        if orchestration:
            parts.append(orchestration)

        # 工具说明必须在规则之后、任务内容之前：先让模型知道自己能调什么，
        # 再给它规则。缺了这段，模型只能靠猜，然后撞 invalid tool
        # （任务 helloworld 的 `ask_user` / `bash` 两次）。
        tools_desc = self.describe_tools(stage, role_id=role_id)
        if tools_desc:
            parts.append(tools_desc)

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

            carryover = self._read_ambiguity_carryover(task_name, stage_idx)
            if carryover:
                parts.append(carryover)

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

    def describe_tools(self, stage: str, role_id: Optional[str] = None) -> str:
        """如实告知本阶段可调用的工具（按 role 解析后的真实工具面）。

        存在的理由是任务 `helloworld` 的两条现场：

        * prompt 写「**必须**使用 `ask_user`」，而那是 harness 的**抽象名**，
          agent 侧真实工具叫 `question`。模型照 prompt 调用，先撞一次
          `invalid tool 'ask_user'` 才纠正。
        * 01 阶段的 analyst 没有 `run_command`，prompt 里没有任何地方说明
          本阶段能用什么，于是模型尝试 `bash` 又撞一次 `invalid`。

        所以工具名**必须从 `TOOL_MAP` 推导**而不是写死在文案里：映射表是
        权限规则实际使用的那一份（`_tool_switches()` 也读它），两边共用
        一个来源，就不会各自漂移。

        `role_id` 透传给 `get_tools_for_stage`，因此多角色审查（A4/A5）下
        每个角色看到的是自己那份权限，而不是 stage 默认角色的。
        """
        from ..agents.opencode import TOOL_MAP
        from ..core.config import get_tools_for_stage

        try:
            allowed = list(get_tools_for_stage(stage, role_id=role_id) or [])
        except Exception:
            # 解析失败时**不猜**：给一句明确的「未知」比编一份工具清单安全。
            # 编出来的清单会让模型去调不存在的工具，正是这段要消除的故障。
            return ("=== 可用工具 ===\n"
                    "（本阶段工具面解析失败 —— 请只使用你确认可用的工具，"
                    "不要凭猜测调用。）")

        # 抽象名 → agent 真能调用的原生名。顺序稳定，便于判据与人眼比对。
        lines = ["=== 可用工具（本阶段实际生效，调用时请用下列名字）==="]
        for harness_tool in sorted(allowed):
            native = TOOL_MAP.get(harness_tool, ())
            if not native:
                continue
            shown = " / ".join(f"`{n}`" for n in native)
            lines.append(f"- {harness_tool}: {shown}")

        lines.append("")
        lines.append("未列出的工具本阶段**不可用** —— 调用它只会拿到 "
                     "invalid tool，请勿尝试。")
        return "\n".join(lines)

    def _build_project_info(self, task_name: str) -> Optional[str]:
        """项目信息段。**不给路径，只说参照系**。

        原实现把 `.state` 里的 `target_dir`（harness 相对路径，如
        `repo/helloworld`）直接写进 prompt。而 agent 的 cwd 已经**就是**
        那个目录（`OpenCodeAgent._default_workdir`），于是它把这行理解成
        「cwd 下还有一层 repo/helloworld」，一连串 read/glob 全打在空处
        （任务 helloworld 实测 8 次）。

        与 `_read_hook_rules` 的自足化同一条纪律：agent 需要的是能直接用的
        信息，不是需要它自己换算参照系的路径。cwd 就是目标目录，说这句即可。
        """
        st = read_state(task_name)
        target_dir = st.get("target_dir", "")
        if not target_dir:
            return None
        return (
            "=== 项目信息 ===\n"
            "**你当前的工作目录就是本任务的代码目录。**\n"
            "所有业务代码、模板、静态文件都直接写在当前工作目录下"
            "（用相对路径，例如 `src/main.py`、`tests/test_main.py`）。\n"
            "不要在当前目录下再创建以任务名或 `repo/` 开头的子目录。"
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

    def _read_ambiguity_carryover(self, task_name: str,
                                  stage_idx: int) -> Optional[str]:
        """把 01 的歧义自评结论带给下一阶段；达标或无记录则不输出。

        A13 把自评从硬拦降级为「放行 + 记账」。降级立刻带来一个新缺口：
        **记了账没人读就等于没记** —— 那是 S7（判据存在、无人调用）。
        2.9.16 的教训正是「修复引入的新代码也要过形状库」，这里补上。

        读的是 harness 自己写进 `.state` 的记录，不是 agent 的自述：
        证据流向下游，自述留在原地（A13）。

        自评达标时**不输出任何东西**。对正常情况也喊一句话，等于没有警告
        （A2 的失败信息纪律）。
        """
        if stage_idx < 1:
            return None
        prev_stage = STAGES[stage_idx - 1]
        if prev_stage != "01-brainstorming":
            return None

        from ..workflow import stage_state as ss

        record = ss.read_ambiguity_record(task_name, prev_stage)
        status = record.get("status")
        if not status or status == ss.AMBIGUITY_OK:
            return None

        lines = ["=== ⚠️ 上一阶段的歧义自评未达标 ==="]
        if status == ss.AMBIGUITY_UNAVAILABLE:
            lines.append(
                "01 阶段**未给出**歧义自评（记为 unavailable）——"
                "需求是否已澄清，无人验证过。")
        else:
            score = record.get("score")
            lines.append(
                f"01 阶段的歧义自评为 {score}，低于目标 8 ——"
                "需求里仍有未澄清的假设。")
        lines.append(
            "请在动手规划之前，先用 `question` 工具把剩下的不确定点问清楚，"
            "不要带着假设排期。")
        return "\n".join(lines)

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
        # 事实的**可信度限定**必须先于事实本身出现（A2 的 10.6）。
        # `manifest.json` 不在 `_FACT_ORDER` 里，于是 warnings 此前从未进过
        # prompt —— 采集到却没人读，与没采集是同一回事。
        # 先于 diff/tests 出现是刻意的：reviewer 读到 19 passed 之前就该知道
        # 那 19 个绿是否有见证支撑。
        caveats = self._read_fact_caveats(task_name)
        if caveats:
            chunks.append(caveats)
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

    def _read_fact_caveats(self, task_name: str) -> Optional[str]:
        """事实包 manifest 里的 warnings，渲染成 prompt 里的可信度限定。

        读磁盘上的 manifest 而不是重新调 `witness_warnings()`：注入的必须
        是**这一份事实包生成时**记录的限定，而不是读 prompt 那一刻重算的
        结果。两者可能不同（例如生成后有人动了 `.state`），而 reviewer
        看到的应当与它手上的事实同源。
        """
        import json

        path = TASKS / task_name / "facts" / "manifest.json"
        if not path.is_file():
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        warnings = manifest.get("warnings")
        if not isinstance(warnings, list) or not warnings:
            return None
        lines = ["--- 事实的可信度限定（harness 生成，必须先读）---"]
        lines += [f"- {w}" for w in warnings if str(w).strip()]
        lines.append("以上任一项成立时，**不得**据该事实判定通过；"
                     "应按 unavailable（❓）处置并在产出中写明。")
        return "\n".join(lines)

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
