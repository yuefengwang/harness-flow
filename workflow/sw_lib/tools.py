"""sw_lib.tools — AI Agent 物理工具箱 (Function Calling)

工具列表由 config.yaml 中每个 role 的 tools 字段驱动，
通过 get_tools_for_stage() 动态获取。
"""

import os
import subprocess
from pathlib import Path
from .config import ROOT, TASKS, get_tools_for_stage


class Toolbox:
    def __init__(self, task_name, stage):
        self.task_name = task_name
        self.task_dir = TASKS / task_name
        self.stage = stage
        self.allowed_tools = get_tools_for_stage(stage)

    def get_available_tools(self):
        """返回当前阶段允许的工具方法列表，供 GeminiAPIAgent 使用"""
        mapping = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "run_command": self.run_command,
        }
        return [mapping[name] for name in self.allowed_tools if name in mapping]

    def list_files(self, path: str = "."):
        """列出指定目录下的文件列表。"""
        target = ROOT / path
        if not str(target.resolve()).startswith(str(ROOT.resolve())):
            return "错误: 禁止访问根目录以外的范围。"
        try:
            items = os.listdir(target)
            return "\n".join(items)
        except Exception as e:
            return f"错误: {e}"

    def read_file(self, file_path: str):
        """读取指定路径文件的全文内容。"""
        target = ROOT / file_path
        if not str(target.resolve()).startswith(str(ROOT.resolve())):
            return "错误: 禁止访问根目录以外的范围。"
        try:
            return target.read_text(encoding="utf-8")
        except Exception as e:
            return f"错误: {e}"

    def write_file(self, file_path: str, content: str):
        """写入或覆盖指定路径的文件内容。"""
        target = ROOT / file_path
        if not str(target.resolve()).startswith(str(ROOT.resolve())):
            return "错误: 禁止访问根目录以外的范围。"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"成功写入 {file_path}"
        except Exception as e:
            return f"错误: {e}"

    def run_command(self, command: str):
        """在项目根目录下执行 Shell 命令并返回标准输出。"""
        try:
            res = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, cwd=str(ROOT), timeout=30
            )
            output = f"Exit Code: {res.returncode}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
            return output
        except subprocess.TimeoutExpired:
            return "错误: 命令执行超时。"
        except Exception as e:
            return f"错误: {e}"