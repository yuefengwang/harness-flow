"""
sw_lib.ui.init_ui — 专业的任务初始化交互界面。

该模块基于 Rich 提供了一个分步式的任务创建向导，取代了原始的命令行提问。
它会自动展示当前配置下的初始角色，并对输入进行实时引导。
"""

import sys
import os
from datetime import datetime
from typing import Dict, Any, Optional, List
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich import box

from ..core.config import STAGES, STAGE_NAMES, ConfigManager, load_harness_config, TASKS, TRASH
from ..core.utils import now, sanitize_name


class InitializationUI:
    """任务初始化向导界面类"""

    def __init__(self):
        self.console = Console()
        self.config_manager = ConfigManager()
        self.data: Dict[str, Any] = {
            "name": "",
            "type": "feature",
            "context": "",
            "allow_trash_collision": False
        }

    def _render_header(self, step_name: str, step_idx: int, total_steps: int = 3):
        """渲染步骤头部"""
        header_text = Text.from_markup(
            f"🚀 [bold blue]Harness-Flow[/] | [white]任务初始化向导[/] [dim]({step_idx}/{total_steps})[/]\n"
            f"[bold cyan]正在进行:[/] {step_name}"
        )
        self.console.print(Panel(header_text, box=box.HORIZONTALS, style="blue"))

    def run(self, default_name: str = "", default_type: str = "feature") -> Optional[Dict[str, Any]]:
        """
        启动初始化向导。
        
        Returns:
            收集到的数据字典，如果用户取消则返回 None。
        """
        self.console.clear()
        
        # 0. 欢迎信息
        welcome_panel = Panel(
            Text.from_markup(
                f"[bold white]欢迎使用 Harness-Flow！[/]\n\n"
                f"我们将引导您快速创建一个新的 Agent 任务。\n"
                f"[dim]提示：任务创建后将立即为您启动监控面板。[/]"
            ),
            title="[bold blue]初始化向导[/]",
            box=box.DOUBLE,
            border_style="blue",
            padding=(1, 2)
        )
        self.console.print(welcome_panel)
        self.console.print()

        # Step 1: 开发目标 (New!)
        self._render_header("选择开发目标", 1, total_steps=4)
        target_mode = Prompt.ask(
            "[bold white]您打算开发哪个项目？[/]",
            choices=["self", "existing", "new"],
            default="new"
        )
        
        if target_mode == "self":
            self.data["target_dir"] = "."
            self.console.print("[cyan]目标: Harness-Flow (自身开发)[/]")
        elif target_mode == "existing":
            # 探测 repo/ 目录
            from ..core.config import ROOT
            repo_dir = ROOT / "repo"
            existing_projects = [d.name for d in repo_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
            if not existing_projects:
                self.console.print("[yellow]警告: repo/ 目录下暂无项目。自动切换到 'new' 模式。[/]")
                target_mode = "new"
            else:
                project = Prompt.ask(
                    "[bold white]请选择已有项目[/]",
                    choices=existing_projects
                )
                self.data["target_dir"] = f"repo/{project}"
                self.console.print(f"[cyan]目标项目: {project}[/]")
        
        if target_mode == "new":
            self.console.print("[cyan]目标: 从 0 到 1 开发新项目[/]")
            # 标记为新项目，稍后根据任务名生成 target_dir
            self.data["is_new_project"] = True

        self.console.print()

        # Step 2: 任务定名
        self._render_header("定义任务名称", 2, total_steps=4)
        while True:
            name_prompt = "[bold white]请输入项目名称 (从0到1)[/]" if self.data.get("is_new_project") else "[bold white]请输入任务名称[/]"
            name = Prompt.ask(
                name_prompt,
                default=default_name
            )
            if not name:
                self.console.print("[red]错误: 名称不能为空。[/]")
                continue
                
            clean_name = sanitize_name(name)
            if clean_name != name:
                self.console.print(f"[yellow]提示: 名称已规范化为: [bold]{clean_name}[/][/]")
            
            # 冲突检测
            if (TASKS / clean_name).exists():
                self.console.print(f"[red]错误: 任务 [bold]{clean_name}[/] 已存在。请换个名字。[/]")
                continue
            
            self.data["name"] = clean_name
            
            # 如果是新项目，自动设置 target_dir
            if self.data.get("is_new_project"):
                self.data["target_dir"] = f"repo/{clean_name}"
                
            break
            
        self.console.print()

        # Step 3: 任务类型
        self._render_header("选择任务类型", 3, total_steps=4)
        task_type = Prompt.ask(
            "[bold white]任务类型[/]",
            choices=["feature", "bugfix", "refactor", "chore"],
            default=default_type
        )
        self.data["type"] = task_type
        self.console.print()

        # Step 4: 需求注入
        self._render_header("注入需求上下文", 4, total_steps=4)
        self.console.print("[bold white]请输入详细的任务需求描述:[/]")
        self.console.print("[dim](输入完成后按 Ctrl+D 结束，支持多行粘贴和方向键)[/]")

        context = sys.stdin.read().strip()
        self.data["context"] = context
        self.console.print()

        # Final: 最终确认
        summary_text = Text()
        summary_text.append(f"任务 ID   : ", style="bold white")
        summary_text.append(f"{self.data['name']}\n", style="cyan")
        summary_text.append(f"任务类型  : ", style="bold white")
        summary_text.append(f"{self.data['type']}\n", style="cyan")
        summary_text.append(f"需求详情  : ", style="bold white")
        summary_text.append(f"{len(self.data['context'])} 字符\n", style="cyan")
        
        summary_panel = Panel(
            summary_text,
            title="[bold yellow] 📋 任务确认 [/]",
            box=box.ROUNDED,
            border_style="yellow",
            padding=(1, 2)
        )
        self.console.print(summary_panel)
        self.console.print()

        if Confirm.ask("[bold green]确认创建任务并立即开始工作?[/]", default=True):
            return self.data
        
        return None
