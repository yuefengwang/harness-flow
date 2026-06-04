"""Tests for PromptRegistry — template loading, caching, and validation."""
import pytest
import tempfile
from pathlib import Path

from sw_lib.prompts.registry import PromptRegistry


@pytest.fixture
def templates_dir():
    """Create temporary templates directory with realistic prompt files."""
    with tempfile.TemporaryDirectory() as d:
        tpldir = Path(d)
        (tpldir / "system.yaml").write_text(
            "version: 1\n"
            "rules: |\n"
            "  === ORCH RULES ===\n"
            "  1. Do not advance stage.\n",
            encoding="utf-8",
        )
        (tpldir / "01-brainstorming.yaml").write_text(
            "version: 1\n"
            "stage: \"01-brainstorming\"\n"
            "description: \"Brainstorming\"\n"
            "system_prompt: |\n"
            "  You are an AI for task {task_name}, stage {stage} ({stage_name}).\n"
            "  Ask one question at a time.\n",
            encoding="utf-8",
        )
        (tpldir / "03-coding.yaml").write_text(
            "version: 1\n"
            "stage: \"03-coding\"\n"
            "system_prompt: |\n"
            "  You are coding agent for {task_name}.\n",
            encoding="utf-8",
        )
        yield tpldir


@pytest.fixture
def registry(templates_dir):
    return PromptRegistry(templates_dir)


class TestPromptRegistryLoad:
    def test_loads_valid_template(self, registry):
        """Registry loads a valid YAML template."""
        tmpl = registry.get("01-brainstorming")
        assert tmpl["stage"] == "01-brainstorming"
        assert "Ask one question" in tmpl["system_prompt"]

    def test_caches_loaded_templates(self, registry):
        """Second get() returns cached template (no file re-read)."""
        first = registry.get("03-coding")
        second = registry.get("03-coding")
        assert first is second

    def test_raises_on_missing_template(self, registry):
        """Missing template file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="99-unknown"):
            registry.get("99-unknown")

    def test_raises_on_missing_system_prompt(self, templates_dir):
        """Template without system_prompt key raises ValueError."""
        (templates_dir / "broken.yaml").write_text(
            "version: 1\nstage: broken\n", encoding="utf-8",
        )
        registry = PromptRegistry(templates_dir)
        with pytest.raises(ValueError, match="system_prompt"):
            registry.get("broken")

    def test_loads_system_rules(self, registry):
        """Registry loads the shared orchestration rules."""
        rules = registry.get_system_rules()
        assert "ORCH RULES" in rules

    def test_returns_empty_string_when_no_system_file(self, templates_dir):
        """No system.yaml → get_system_rules returns ''."""
        (templates_dir / "system.yaml").unlink()
        registry = PromptRegistry(templates_dir)
        assert registry.get_system_rules() == ""


class TestPromptRegistryList:
    def test_lists_all_templates(self, registry):
        """list_templates returns metadata for all stage templates (not system)."""
        listed = registry.list_templates()
        assert "01-brainstorming" in listed
        assert "03-coding" in listed
        assert "system.yaml" not in [listed[k].get("stage") for k in listed]
        # system.yaml should NOT be listed as a stage template
        assert all(k != "system" for k in listed)

    def test_list_contains_metadata(self, registry):
        """Each entry has version, stage, description."""
        entry = registry.list_templates()["01-brainstorming"]
        assert entry["version"] == 1
        assert entry["stage"] == "01-brainstorming"
        assert "Brainstorming" in entry["description"]
