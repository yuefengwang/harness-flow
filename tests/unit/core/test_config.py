import sys
import unittest
from pathlib import Path
import yaml
import os
import shutil

# 获取项目根目录 (harness-flow/)
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from sw_lib.core.config import resolve_agent_model, resolve_agent_type, CONFIG_DIR, _manager

class TestModelResolution(unittest.TestCase):
    def setUp(self):
        self.config_path = CONFIG_DIR / "config.yaml"
        self.backup_path = CONFIG_DIR / "config.yaml.bak"
        if self.config_path.exists():
            shutil.copy2(self.config_path, self.backup_path)
        
        self.test_config = {
            "harness": {
                "roles": {
                    "analyst": {"agent": "gemini", "model": "gemini-pro"},
                    "architect": {"agent": "opencode", "model": "code-model-1"},
                },
                "stage_roles": {
                    "01-brainstorming": "analyst",
                    "02-planning": "architect"
                }
            }
        }
        with open(self.config_path, "w") as f:
            yaml.dump(self.test_config, f)
        _manager.reload()

    def tearDown(self):
        if self.backup_path.exists():
            shutil.move(str(self.backup_path), str(self.config_path))
        elif self.config_path.exists():
            os.remove(self.config_path)
        _manager.reload()

    def test_stage_resolution(self):
        self.assertEqual(resolve_agent_model("01-brainstorming"), "gemini-pro")
        self.assertEqual(resolve_agent_model("02-planning"), "code-model-1")

    def test_role_override(self):
        self.assertEqual(resolve_agent_model("01-brainstorming", "architect"), "code-model-1")

    def test_model_direct_override(self):
        self.assertEqual(resolve_agent_model("01-brainstorming", "custom-model-99"), "custom-model-99")

    def test_missing_config_error(self):
        # 当阶段无配置时，应返回默认模型而非抛异常
        model = resolve_agent_model("03-coding")
        assert model is not None
        assert isinstance(model, str) and len(model) > 0


class TestAgentTypeResolution(unittest.TestCase):
    def setUp(self):
        self.config_path = CONFIG_DIR / "config.yaml"
        self.backup_path = CONFIG_DIR / "config.yaml.bak"
        if self.config_path.exists():
            shutil.copy2(self.config_path, self.backup_path)

        self.test_config = {
            "harness": {
                "roles": {
                    "analyst": {"agent": "gemini", "model": "gemini-pro"},
                    "developer": {"agent": "opencode", "model": "opencode-v1"},
                    "reviewer": {"agent": "claudecode", "model": "claude-3"}
                },
                "stage_roles": {
                    "01-brainstorming": "analyst",
                    "03-coding": "developer",
                    "04-review": "reviewer"
                }
            }
        }
        with open(self.config_path, "w") as f:
            yaml.dump(self.test_config, f)
        _manager.reload()

    def tearDown(self):
        if self.backup_path.exists():
            shutil.move(str(self.backup_path), str(self.config_path))
        elif self.config_path.exists():
            os.remove(self.config_path)
        _manager.reload()

    def test_type_from_role_config(self):
        self.assertEqual(resolve_agent_type("01-brainstorming"), "gemini")
        self.assertEqual(resolve_agent_type("03-coding"), "opencode")
        self.assertEqual(resolve_agent_type("04-review"), "claudecode")

    def test_type_role_override(self):
        self.assertEqual(resolve_agent_type("01-brainstorming", "developer"), "opencode")

    def test_type_explicit_name(self):
        self.assertEqual(resolve_agent_type("01-brainstorming", "opencode"), "opencode")
        self.assertEqual(resolve_agent_type("01-brainstorming", "Gemini"), "gemini")

    def test_type_fallback_from_model(self):
        self.assertEqual(resolve_agent_type("01-brainstorming", "gemini-ultime"), "gemini")
        self.assertEqual(resolve_agent_type("01-brainstorming", "opencode-2b"), "opencode")

    def test_type_default_gemini(self):
        self.assertEqual(resolve_agent_type("99-unknown-stage"), "gemini")
