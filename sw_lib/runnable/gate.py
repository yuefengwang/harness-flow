"""GateValidator — wraps existing stage template and hook script validation."""

import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from ..core.config import TASKS, HOOKS_DIR, ROOT


class GateValidator:
    """Validates that a stage's Gate section meets requirements.

    Two-phase check:
    1. Soft check: template checkboxes must all be [x]
    2. Hard check (optional): hook shell script must exit 0
    """

    def __init__(self, run_hook_script: bool = False):
        self.run_hook_script = run_hook_script

    def check(self, task_name: str, stage: str) -> bool:
        """Return True if all gate conditions pass."""
        if not self._check_template(task_name, stage):
            return False

        if self.run_hook_script:
            if not self._run_hook(task_name, stage):
                return False

        return True

    def _check_template(self, task_name: str, stage: str) -> bool:
        """Verify all checkboxes in the ## Gate section are [x]."""
        task_dir = TASKS / task_name
        tpl = task_dir / f"{stage}.md"
        if not tpl.exists():
            return False

        content = tpl.read_text(encoding="utf-8")
        in_gate = False
        has_checkbox = False

        for line in content.splitlines():
            if line.strip().startswith("## Gate"):
                in_gate = True
                continue
            if in_gate and line.strip().startswith("##"):
                break
            if in_gate:
                match = re.match(r"^\s*- \[([ xX])\]", line)
                if match:
                    has_checkbox = True
                    if match.group(1) != "x":
                        return False

        if not has_checkbox:
            return False

        # For review stage, also check Route is filled
        if stage == "04-review":
            route_match = re.search(r"\*\*Route\*\*:\s*`([^`]+)`", content)
            if not route_match or route_match.group(1).strip() in ("", "___"):
                return False

        return True

    def _run_hook(self, task_name: str, stage: str) -> bool:
        """Execute the hook shell script. Return True on exit 0."""
        hook_script = HOOKS_DIR / f"check_{stage}.sh"
        if not hook_script.exists():
            hook_script = HOOKS_DIR / f"post_check_{stage}.sh"
        if not hook_script.exists():
            return True

        try:
            result = subprocess.run(
                [str(hook_script), task_name],
                cwd=str(ROOT),
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.returncode == 0
        except Exception:
            return False
