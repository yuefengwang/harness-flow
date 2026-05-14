"""sw_lib.engine — WorkflowEngine 自动编排引擎

核心职责：
1. 构建上下文（前序产出 + 当前模板 + 需求）
2. 创建 Agent 并发送系统消息
3. 收集 Agent 输出，提取关键内容保存为 stage.md
4. /advance 时验证 hooks 后推进阶段
5. 支持可配置的 auto_advance 自动推进
"""

import subprocess
import threading
from pathlib import Path

from .config import (
    ROOT, TASKS, STAGES, STAGE_NAMES,
    resolve_agent_model, resolve_agent_type, is_auto_advance,
)
from .state import read_state, write_state, update_status_md_stage
from .utils import now, sw_log


class WorkflowEngine:
    def __init__(self, name, stage, stage_idx, agent_name, callbacks):
        self.name = name
        self.stage = stage
        self.stage_idx = stage_idx
        self.agent_name = agent_name
        self.callbacks = callbacks
        self.agent = None
        self.model_name = "N/A"
        self._output_lines = []
        self._output_lock = threading.Lock()
        self._stage_output_saved = False

    def _add_log(self, source, msg):
        with self._output_lock:
            self._output_lines.append((source, msg))
        if "add_log" in self.callbacks:
            self.callbacks["add_log"](source, msg)

    def _is_running(self):
        if "is_running" in self.callbacks:
            return self.callbacks["is_running"]()
        return True

    def _on_agent_complete(self):
        if self._stage_output_saved:
            return
        saved = self.save_stage_output()
        if saved:
            self._stage_output_saved = True

    # ── 上下文构建 ──

    def _build_context(self):
        """构建系统消息：角色提示 + 前序产出 + 当前模板 + 需求"""
        task_dir = TASKS / self.name
        parts = []
        stage_name = STAGE_NAMES[self.stage_idx] if self.stage_idx < len(STAGE_NAMES) else "未知"

        # 0. 角色提示
        parts.append(
            f"你是 Harness-Flow 平台的 AI Agent。\n"
            f"当前任务: {self.name}\n"
            f"当前阶段: {self.stage} ({stage_name})\n\n"
            f"请开始 {stage_name} 阶段的工作。"
        )

        # 1. 前序阶段产出
        if self.stage_idx > 0:
            prev_stage = STAGES[self.stage_idx - 1]
            prev_file = task_dir / f"{prev_stage}.md"
            if prev_file.exists():
                content = prev_file.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"=== 前一阶段产出 ({prev_stage} / {STAGE_NAMES[self.stage_idx - 1]}) ===\n{content}")

        # 2. 当前阶段模板
        cur_tpl = task_dir / f"{self.stage}.md"
        if cur_tpl.exists():
            tpl_content = cur_tpl.read_text(encoding="utf-8").strip()
            original_len = len(tpl_content)
            if tpl_content:
                parts.append(f"=== 当前阶段模板 ({self.stage} / {stage_name}) ===\n{tpl_content}")

        # 3. 需求上下文
        ctx_file = task_dir / ".context"
        if ctx_file.exists():
            ctx = ctx_file.read_text(encoding="utf-8").strip()
            if ctx:
                parts.append(f"=== 任务需求 ===\n{ctx}")

        # 4. Hooks 强制规则
        hook_file = ROOT / "hooks" / f"{self.stage}.md"
        if hook_file.exists():
            hook_content = hook_file.read_text(encoding="utf-8").strip()
            if hook_content:
                parts.append(f"=== 强制规则 (hooks/{self.stage}.md) ===\n{hook_content}")

        if not parts:
            return None

        return "\n\n".join(parts)

    # ── Agent 创建 ──

    def _create_agent(self):
        """根据 resolve_agent_type 和 resolve_agent_model 创建对应 Agent"""
        try:
            self.model_name = resolve_agent_model(self.stage, self.agent_name)
        except Exception as e:
            self._add_log("error", f"解析角色配置失败: {e}")
            self.model_name = ""

        try:
            agent_type = resolve_agent_type(self.stage, self.agent_name)
        except Exception as e:
            self._add_log("error", f"解析 Agent 类型失败: {e}")
            agent_type = "gemini"

        from .agent_api import GeminiAPIAgent
        from .opencode_agent import OpenCodeAgent
        from .agent import AgentManager

        callbacks = {"add_log": self._add_log, "is_running": self._is_running, "on_complete": self._on_agent_complete}

        if agent_type == "gemini":
            self.agent = GeminiAPIAgent(callbacks, self.name, self.stage, self.stage_idx, self.model_name)
        elif agent_type == "opencode":
            self.agent = OpenCodeAgent(callbacks, self.name, self.stage, self.stage_idx, self.model_name)
        else:
            self.agent = AgentManager(callbacks, self.name, self.stage, self.stage_idx, self.model_name)

        return self.agent

    # ── 阶段编排 ──

    def run_stage(self):
        """编排当前阶段：构建上下文 → 创建 Agent → 启动"""
        context = self._build_context()

        if not context:
            self._add_log("sw", "无上下文可注入，请检查任务文件")
            return

        self._create_agent()

        if not self.agent:
            self._add_log("error", "Agent 创建失败")
            return

        self._add_log("sw", f"启动 Agent ({self.model_name}) — {self.stage} ({STAGE_NAMES[self.stage_idx]})")

        self.agent.start()

        # 注入上下文
        if hasattr(self.agent, 'send'):
            self.agent.send(context, is_system=True)

        # PTY Agent 需要 reader 线程
        from .agent import AgentManager
        if isinstance(self.agent, AgentManager):
            threading.Thread(target=self.agent.reader_loop, daemon=True).start()

    # ── 产出提取与保存 ──

    def _extract_output(self):
        """从 Agent 输出中提取关键内容"""
        with self._output_lock:
            lines = list(self._output_lines)

        # 筛选 agent 输出行
        agent_lines = []
        for source, msg in lines:
            if source == "agent":
                agent_lines.append(msg)

        if not agent_lines:
            return None

        # 提取关键内容：去掉过短的行，合并过长的行
        full_text = "\n".join(agent_lines)

        # 如果有 markdown 标记，提取标记之间的内容
        sections = self._extract_markdown_sections(full_text)
        if sections:
            return sections

        # 否则返回完整输出（截断到合理长度）
        if len(full_text) > 4000:
            return full_text[:4000] + "\n\n... (已截断)"
        return full_text

    def _extract_markdown_sections(self, text):
        """提取 markdown 标题段（## 开头的内容块）"""
        lines = text.splitlines()
        sections = []
        current_section = []
        current_title = None

        for line in lines:
            if line.startswith("## ") or line.startswith("# "):
                if current_section and current_title:
                    content = "\n".join(current_section).strip()
                    if len(content) > 20:
                        sections.append(f"{current_title}\n{content}")
                current_title = line.strip()
                current_section = []
            else:
                current_section.append(line)

        if current_section and current_title:
            content = "\n".join(current_section).strip()
            if len(content) > 20:
                sections.append(f"{current_title}\n{content}")

        return "\n\n".join(sections) if sections else None

    def save_stage_output(self):
        """将提取的关键内容保存到 stage.md"""
        output = self._extract_output()
        if not output:
            self._add_log("sw", "Agent 无有效产出，未保存")
            return False

        task_dir = TASKS / self.name
        stage_file = task_dir / f"{self.stage}.md"

        # 保留模板头部（如果存在）
        existing = ""
        if stage_file.exists():
            existing = stage_file.read_text(encoding="utf-8")
            # 找到模板的 "# 阶段" 标题行，保留标题
            for line in existing.splitlines():
                if line.startswith("# "):
                    existing = line + "\n\n"
                    break

        stage_file.write_text(existing + output, encoding="utf-8")
        self._add_log("sw", f"阶段产出已保存到 {self.stage}.md ({len(output)} 字符)")
        sw_log(self.name, f"stage output saved: {self.stage}.md", "sw")
        return True

    # ── 阶段推进 ──

    def advance_stage(self):
        """验证 hooks → 推进到下一阶段"""
        if self.stage_idx >= len(STAGES) - 1:
            self._add_log("sw", "已是最后阶段 (05-归档)")
            return False

        # 1. 保存当前产出
        self.save_stage_output()

        # 2. 验证 hooks
        if not self._validate_hooks():
            self._add_log("error", "Hooks 验证未通过，无法推进")
            return False

        # 3. 关闭当前 Agent
        if self.agent and hasattr(self.agent, 'shutdown'):
            self.agent.shutdown()

        # 4. 推进状态
        next_idx = self.stage_idx + 1
        next_stage = STAGES[next_idx]
        next_name = STAGE_NAMES[next_idx]

        st = read_state(self.name)
        st["stage"] = f'"{next_stage}"'
        st["stage_idx"] = str(next_idx)
        st["stage_status"] = "pending"
        st["updated_at"] = now()
        write_state(self.name, st)

        try:
            update_status_md_stage(next_idx)
        except Exception:
            pass

        sw_log(self.name, f"advance → {next_stage} ({next_name})")
        self._add_log("sw", f"阶段推进 → {next_name}")

        # 5. 更新自身状态
        self.stage = next_stage
        self.stage_idx = next_idx

        # 6. 启动新阶段（如果 auto_advance 或手动）
        if is_auto_advance():
            self._add_log("sw", "自动启动下一阶段...")
            self.run_stage()

        return True

    def _validate_hooks(self):
        """运行 hooks/check_XX.sh 校验脚本"""
        hook_script = ROOT / "hooks" / f"check_{self.stage}.sh"
        if not hook_script.exists():
            return True

        try:
            result = subprocess.run(
                [str(hook_script), self.name],
                cwd=str(ROOT), check=False,
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                self._add_log("error", f"Hooks 验证失败: {hook_script.name}")
                if result.stdout.strip():
                    self._add_log("error", result.stdout.strip()[:300])
                return False
            self._add_log("sw", f"Hooks 验证通过: {hook_script.name}")
            return True
        except Exception as e:
            self._add_log("error", f"Hooks 执行异常: {e}")
            return False

    # ── Agent 生命周期 ──

    def restart_agent(self):
        """重启当前 Agent"""
        if self.agent and hasattr(self.agent, 'restart'):
            self.agent.restart()
            from .agent import AgentManager
            if isinstance(self.agent, AgentManager):
                threading.Thread(target=self.agent.reader_loop, daemon=True).start()

    def shutdown(self):
        """关闭 Agent"""
        if self.agent and hasattr(self.agent, 'shutdown'):
            self.agent.shutdown()

    # ── 命令处理 ──

    def handle_command(self, cmd):
        """处理 TUI 转发的 /command"""
        cmd = cmd.strip()
        if not cmd:
            return

        parts = cmd.split()
        sub = parts[0]

        if sub == "advance":
            self.advance_stage()
        elif sub == "status":
            st = read_state(self.name)
            self._add_log("sw", f"stage={st.get('stage','?')} status={st.get('stage_status','?')}")
        elif sub == "restart":
            self._add_log("sw", "重启 Agent...")
            self.restart_agent()
        elif sub == "context":
            ctx = self._build_context()
            if ctx:
                preview = ctx[:300] + ("..." if len(ctx) > 300 else "")
                self._add_log("system", f"当前上下文预览:\n{preview}")
            else:
                self._add_log("sw", "无上下文")
        else:
            self._add_log("sw", f"未知命令: /{sub}  (可用: /advance /status /restart /context /q)")