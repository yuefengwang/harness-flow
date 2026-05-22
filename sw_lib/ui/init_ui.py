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

        # Step 1: 任务定名 (包含实时预览和校验)
        self._render_header("定义任务名称", 1)
        while True:
            name = Prompt.ask(
                "[bold white]请输入任务名称[/]",
                default=default_name
            )
            if not name:
                self.console.print("[red]错误: 任务名称不能为空。[/]")
                continue
                
            clean_name = sanitize_name(name)
            if clean_name != name:
                self.console.print(f"[yellow]提示: 名称已规范化为: [bold]{clean_name}[/][/]")
            
            # 冲突检测
            if (TASKS / clean_name).exists():
                self.console.print(f"[red]错误: 任务 [bold]{clean_name}[/] 已存在。请换个名字。[/]")
                continue
            if (TRASH / clean_name).exists():
                self.console.print(f"[yellow]警告: 同名任务已在回收站中。[/]")
                if Confirm.ask("是否继续创建？(将覆盖回收站索引，旧任务将难以直接恢复)", default=False):
                    self.data["allow_trash_collision"] = True
                else:
                    continue
            
            self.data["name"] = clean_name
            break
            
        self.console.print()

        # Step 2: 任务类型
        self._render_header("选择任务类型", 2)
        task_type = Prompt.ask(
            "[bold white]任务类型[/]",
            choices=["feature", "bugfix", "refactor", "chore"],
            default=default_type
        )
        self.data["type"] = task_type
        self.console.print()

        # Step 3: 需求注入
        self._render_header("注入需求上下文", 3)
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
