"""GateValidator — wraps existing stage template and hook script validation."""

import subprocess

from ..core.config import HOOKS_DIR, ROOT, HOOK_TIMEOUT_SECONDS


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
        from .utils import check_stage_compliance
        from ..core.config import STAGES
        
        try:
            stage_idx = STAGES.index(stage)
        except ValueError:
            stage_idx = 0
            
        done, todo = check_stage_compliance(task_name, stage, stage_idx)
        if todo:
            return False

        if self.run_hook_script:
            if not self._run_hook(task_name, stage):
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
                timeout=HOOK_TIMEOUT_SECONDS,
            )
            return result.returncode == 0
        except Exception:
            return False
