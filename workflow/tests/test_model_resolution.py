import sys
import unittest
from pathlib import Path
import yaml
import os
import shutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sw_lib.config import resolve_agent_model, resolve_agent_type

class TestModelResolution(unittest.TestCase):
    def setUp(self):
        self.config_path = ROOT / "harness" / "config.yaml"
        self.backup_path = ROOT / "harness" / "config.yaml.bak"
        if self.config_path.exists():
            shutil.copy2(self.config_path, self.backup_path)
        
        self.test_config = {
            "harness": {
                "roles": {
                    "analyst": {"agent": "gemini", "model": "gemini-analyst-model"},
                    "developer": {"agent": "opencode", "model": "gemini-dev-model"}
                },
                "stage_roles": {
                    "01-brainstorming": "analyst",
                    "03-coding": "developer"
                }
            }
        }
        with open(self.config_path, "w") as f:
            yaml.dump(self.test_config, f)

    def tearDown(self):
        if self.backup_path.exists():
            shutil.move(self.backup_path, self.config_path)
        elif self.config_path.exists():
            os.remove(self.config_path)

    def test_stage_resolution(self):
        self.assertEqual(resolve_agent_model("01-brainstorming"), "gemini-analyst-model")
        self.assertEqual(resolve_agent_model("03-coding"), "gemini-dev-model")

    def test_role_override(self):
        self.assertEqual(resolve_agent_model("01-brainstorming", "developer"), "gemini-dev-model")

    def test_model_direct_override(self):
        self.assertEqual(resolve_agent_model("01-brainstorming", "claude-3-opus"), "claude-3-opus")

    def test_gemini_fallback(self):
        self.assertEqual(resolve_agent_model("02-planning", "gemini"), "gemini-2.0-flash")

    def test_missing_config_error(self):
        with self.assertRaises(ValueError):
            resolve_agent_model("02-planning")


class TestAgentTypeResolution(unittest.TestCase):
    def setUp(self):
        self.config_path = ROOT / "harness" / "config.yaml"
        self.backup_path = ROOT / "harness" / "config.yaml.bak"
        if self.config_path.exists():
            shutil.copy2(self.config_path, self.backup_path)

        self.test_config = {
            "harness": {
                "roles": {
                    "analyst": {"agent": "gemini", "model": "gemini-3-flash"},
                    "developer": {"agent": "opencode", "model": "opencode"},
                    "reviewer": {"agent": "claudecode", "model": "claude-4-sonnet"},
                    "architect": {"model": "gemini-3-flash"}
                },
                "stage_roles": {
                    "01-brainstorming": "analyst",
                    "03-coding": "developer",
                    "04-review": "reviewer",
                    "02-planning": "architect"
                }
            }
        }
        with open(self.config_path, "w") as f:
            yaml.dump(self.test_config, f)

    def tearDown(self):
        if self.backup_path.exists():
            shutil.move(self.backup_path, self.config_path)
        elif self.config_path.exists():
            os.remove(self.config_path)

    def test_type_from_role_config(self):
        self.assertEqual(resolve_agent_type("01-brainstorming"), "gemini")
        self.assertEqual(resolve_agent_type("03-coding"), "opencode")
        self.assertEqual(resolve_agent_type("04-review"), "claudecode")

    def test_type_role_override(self):
        self.assertEqual(resolve_agent_type("01-brainstorming", "developer"), "opencode")

    def test_type_explicit_name(self):
        self.assertEqual(resolve_agent_type("01-brainstorming", "codex"), "codex")
        self.assertEqual(resolve_agent_type("01-brainstorming", "gemini"), "gemini")

    def test_type_fallback_from_model(self):
        self.assertEqual(resolve_agent_type("02-planning"), "gemini")

    def test_type_default_gemini(self):
        self.assertEqual(resolve_agent_type("99-unknown"), "gemini")


if __name__ == "__main__":
    unittest.main()
