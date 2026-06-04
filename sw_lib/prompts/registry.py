"""PromptRegistry — loads, caches, and validates stage prompt templates."""

import yaml
from pathlib import Path
from typing import Dict, Optional


class PromptRegistry:
    """Loads and caches YAML prompt templates for each workflow stage.

    Templates are stored in sw_lib/prompts/templates/ as .yaml files.
    Each template has:
    - version: schema version
    - stage: stage code (e.g. "01-brainstorming")
    - system_prompt: the system instruction text with Python format placeholders

    The system.yaml file provides shared orchestration rules.
    """

    def __init__(self, templates_dir: Path):
        self._templates_dir = Path(templates_dir)
        self._cache: Dict[str, dict] = {}
        self._system_rules: Optional[str] = None

    def get_system_rules(self) -> str:
        """Return shared orchestration rules (appended to every prompt)."""
        if self._system_rules is None:
            path = self._templates_dir / "system.yaml"
            if path.exists():
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                self._system_rules = data.get("rules", "")
            else:
                self._system_rules = ""
        return self._system_rules

    def get(self, stage: str) -> dict:
        """Load and cache a stage template. Returns dict with keys:
        - version, stage, system_prompt
        """
        if stage not in self._cache:
            self._cache[stage] = self._load(stage)
        return self._cache[stage]

    def _load(self, stage: str) -> dict:
        """Load a single YAML template file."""
        path = self._templates_dir / f"{stage}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Invalid template format: {path}")
        if "system_prompt" not in data:
            raise ValueError(f"Template missing 'system_prompt': {path}")
        return data

    def list_templates(self) -> Dict[str, dict]:
        """Return all loaded templates with metadata."""
        result = {}
        for f in sorted(self._templates_dir.glob("*.yaml")):
            if f.name == "system.yaml":
                continue
            stage_code = f.stem
            data = self.get(stage_code)
            result[stage_code] = {
                "version": data.get("version"),
                "stage": data.get("stage"),
                "description": data.get("description", ""),
            }
        return result
