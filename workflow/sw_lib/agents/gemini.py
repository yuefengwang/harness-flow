"""sw_lib.agent_api — Gemini API-driven Agent (Modern SDK)"""

import os
import json
import time
import threading
from google import genai
from google.genai import types
from pathlib import Path

from ..core.config import ROOT, WORKFLOW, TASKS, STAGES, STAGE_NAMES
from ..core.utils import now, sw_log
from ..tools.toolbox import Toolbox

from .base import BaseAgent


class GeminiAgent(BaseAgent):
    def __init__(self, tui_callbacks, name, stage, stage_idx, model_name="gemini-2.0-flash"):
        super().__init__(tui_callbacks, name, stage, stage_idx, model_name)
        self.history_file = TASKS / name / ".history.json"
        self.toolbox = Toolbox(name, stage, callbacks=tui_callbacks)
        self.running = False
        
        self.api_key = self._get_api_key()
        self.client = None
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        
        self.history = []

    def _close_master(self):
        """为了测试兼容性"""
        self._master_fd = None

    @property
    def is_active(self):
        return self.running

    def _get_api_key(self):
        """从凭证文件中获取 GOOGLE_API_KEY"""
        import yaml
        paths = [WORKFLOW / "harness" / "credentials.yaml", WORKFLOW / "harness" / "config.yaml"]
        for p in paths:
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = yaml.safe_load(f)
                        if data:
                            if p.name == "config.yaml":
                                val = data.get("harness", {}).get("credentials", {}).get("GOOGLE_API_KEY")
                                if val: return val
                            else:
                                val = data.get("GOOGLE_API_KEY")
                                if val: return val
                except: continue
        return os.environ.get("GOOGLE_API_KEY")

    def _get_tools(self):
        """获取当前阶段允许的工具对象列表"""
        return self.toolbox.get_available_tools()

    def start(self):
        if not self.client:
            self._add_log("error", "未找到 GOOGLE_API_KEY，请检查凭证配置")
            self.status = self.STATUS_ERROR
            return

        self._add_log("sw", f"正在通过 Modern API 唤醒 Gemini Agent ({self.model_name})...")
        self.running = True
        self.status = self.STATUS_IDLE
        self.history = []

    def send(self, text, is_system=False):
        if not self.running and not is_system:
            self._add_log("sw", "Agent 未运行，输入已写入 .input (sw next 后生效)")
            input_file = TASKS / self.name / ".input"
            input_file.parent.mkdir(parents=True, exist_ok=True)
            with open(input_file, "a") as f:
                f.write(f"[{now()}] user | {text}\n")
            sw_log(self.name, f"user input (offline): {text[:80]}", "user")
            return

        if not self.client: return

        def _run_api():
            self.status = self.STATUS_CONNECTING
            self._add_log("sw", f"⏳ Gemini API 连接中...")

            # 超时保护：如果 API 在 60 秒内无响应则报错
            api_timeout = threading.Event()
            
            def _timeout_guard():
                if not api_timeout.wait(timeout=60):
                    if self.status == self.STATUS_CONNECTING:
                        self._add_log("error", "Gemini API 连接超时 (60秒无响应)，请检查网络或 API Key")
                        self.status = self.STATUS_ERROR
            
            threading.Thread(target=_timeout_guard, daemon=True).start()

            try:
                system_instruction = (
                    f"你是 Harness-Flow 平台的专职 AI Agent。\n"
                    f"当前任务: {self.name}\n"
                    f"当前阶段: {self.stage} ({STAGE_NAMES[self.stage_idx]})\n\n"
                    f"你的职责:\n"
                    f"1. 严格遵循本阶段的 Markdown 模板产出内容。\n"
                    f"2. 只能使用为你提供的工具，严禁尝试越权操作。\n"
                    f"3. 产出关键内容，避免冗余描述。\n"
                    f"4. 保持回答简洁专业。"
                )

                # 添加新消息到历史
                self.history.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

                self.status = self.STATUS_ACTIVE
                self._add_log("sw", "⏳ 等待 Gemini 回复中...")
                
                first_chunk_received = False
                for chunk in self.client.models.generate_content_stream(
                    model=self.model_name,
                    contents=self.history,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=self._get_tools(),
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False)
                    )
                ):
                    if not self.running: break
                    if chunk.text:
                        if not first_chunk_received:
                            first_chunk_received = True
                            api_timeout.set()
                            self._add_log("sw", "✓ Gemini 已连接，正在接收回复...")
                        self._add_log("agent", chunk.text)
                
                # 记录 Agent 的回复到历史中（为了保持上下文连贯）
                # 理想情况下应该获取完整的 response.candidates[0].content
                # 但流式输出下需要手动重组。此处简化处理。
                
            except Exception as e:
                api_timeout.set()
                self._add_log("error", f"Gemini API 交互异常: {e}")
                self.status = self.STATUS_ERROR
            else:
                api_timeout.set()
                self.status = self.STATUS_IDLE
                self._add_log("sw", "✓ Gemini 回复完成")
                if "on_complete" in self.callbacks:
                    try:
                        self.callbacks["on_complete"]()
                    except Exception:
                        pass

        threading.Thread(target=_run_api, daemon=True).start()

    def shutdown(self):
        self.running = False
        self.status = self.STATUS_IDLE

    def restart(self):
        self.shutdown()
        self.start()

    def inject_context(self):
        self._add_log("system", "核心指令已通过 API System Instruction 注入完毕")

    def reader_loop(self):
        pass
