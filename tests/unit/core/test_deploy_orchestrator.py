"""Tests for sw_lib.core.deploy_orchestrator — Agent-driven deployment orchestrator"""

import json
import os
import shutil
import signal
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from sw_lib.core.config import TASKS
from sw_lib.core.service import _service, TaskError
from sw_lib.core.deploy_orchestrator import (
    DeployOrchestrator,
    DeployResult,
    run_deploy_orchestrator,
)


class TestDeployResult(unittest.TestCase):
    """DeployResult dataclass tests"""

    def test_default_values(self):
        result = DeployResult()
        self.assertEqual(result.service_url, "")
        self.assertEqual(result.pid, 0)
        self.assertEqual(result.tunnel_url, "")

    def test_custom_values(self):
        result = DeployResult(
            service_url="http://localhost:8000",
            pid=12345,
            tunnel_url="https://abc.trycloudflare.com",
        )
        self.assertEqual(result.service_url, "http://localhost:8000")
        self.assertEqual(result.pid, 12345)
        self.assertEqual(result.tunnel_url, "https://abc.trycloudflare.com")


class TestDeployOrchestratorInit(unittest.TestCase):
    """DeployOrchestrator.__init__ tests"""

    def setUp(self):
        self.name = "test-orch-init"

    def test_init_creates_tools_directly(self):
        orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test")
        self.assertIn("list_files", orch.tools)
        self.assertIn("read_file", orch.tools)
        self.assertIn("write_file", orch.tools)
        self.assertIn("run_command", orch.tools)
        self.assertEqual(orch.tools["list_files"].name, "list_files")
        self.assertEqual(orch.tools["run_command"].name, "run_command")

    def test_init_defaults(self):
        orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test")
        self.assertEqual(orch.name, self.name)
        self.assertEqual(orch.target_dir, "/tmp/test")
        self.assertEqual(orch.port, 8000)
        self.assertFalse(orch.no_tunnel)
        self.assertIsNone(orch.log_callback)
        self.assertIsNone(orch.agent)
        self.assertEqual(orch._agent_output, [])

    def test_init_custom_port(self):
        orch = DeployOrchestrator(
            name=self.name, target_dir="/tmp/test", port=9000, no_tunnel=True
        )
        self.assertEqual(orch.port, 9000)
        self.assertTrue(orch.no_tunnel)


class TestDeployOrchestratorParseResult(unittest.TestCase):
    """DeployOrchestrator._parse_result tests"""

    def setUp(self):
        self.name = "test-parse"
        self.orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test", port=8000)

    def test_parse_valid(self):
        lines = [
            "Detecting project...",
            '<<<RESULT>>>{"service_url":"http://localhost:8000","pid":12345}<<<END>>>',
        ]
        result = self.orch._parse_result(lines)
        self.assertIsNotNone(result)
        self.assertEqual(result.service_url, "http://localhost:8000")
        self.assertEqual(result.pid, 12345)

    def test_parse_no_match(self):
        result = self.orch._parse_result(["no result marker"])
        self.assertIsNone(result)

    def test_parse_empty(self):
        result = self.orch._parse_result([])
        self.assertIsNone(result)

    def test_parse_malformed_json(self):
        result = self.orch._parse_result(['<<<RESULT>>>bad-json<<<END>>>'])
        self.assertIsNone(result)

    def test_parse_with_extra_text(self):
        result = self.orch._parse_result([
            'prefix <<<RESULT>>>{"service_url":"http://localhost:9000","pid":99}<<<END>>> suffix',
        ])
        self.assertIsNotNone(result)
        self.assertEqual(result.service_url, "http://localhost:9000")
        self.assertEqual(result.pid, 99)

    def test_parse_multiline(self):
        result = self.orch._parse_result([
            '<<<RESULT>>>{"service_url":',
            '"http://localhost:8000",',
            '"pid": 42}',
            '<<<END>>>',
        ])
        self.assertIsNotNone(result)
        self.assertEqual(result.service_url, "http://localhost:8000")
        self.assertEqual(result.pid, 42)


class TestDeployOrchestratorLog(unittest.TestCase):
    """DeployOrchestrator._log tests"""

    def setUp(self):
        self.name = "test-orch-log"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def tearDown(self):
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def test_writes_to_deploy_log(self):
        log_lines = []
        orch = DeployOrchestrator(
            name=self.name, target_dir="/tmp/test",
            log_callback=lambda m: log_lines.append(m),
        )
        orch._log("hello deploy")
        log_file = TASKS / self.name / ".deploy_log"
        self.assertTrue(log_file.exists())
        content = log_file.read_text(encoding="utf-8")
        self.assertIn("hello deploy", content)

    def test_calls_callback(self):
        log_lines = []
        orch = DeployOrchestrator(
            name=self.name, target_dir="/tmp/test",
            log_callback=lambda m: log_lines.append(m),
        )
        orch._log("callback msg")
        self.assertTrue(any("callback msg" in l for l in log_lines))

    def test_works_without_callback(self):
        orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test")
        orch._log("no callback")
        log_file = TASKS / self.name / ".deploy_log"
        self.assertTrue(log_file.exists())


class TestDeployOrchestratorRun(unittest.TestCase):
    """DeployOrchestrator.run() with mocked AgentFactory"""

    def setUp(self):
        self.name = "test-orch-run"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def tearDown(self):
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    @patch("sw_lib.core.deploy_orchestrator.AgentFactory.create")
    @patch("sw_lib.core.deploy_orchestrator._service.complete_deploy")
    def test_run_success(self, mock_complete, mock_factory):
        mock_agent = MagicMock()
        mock_agent.wait.return_value = None
        mock_factory.return_value = mock_agent

        orch = DeployOrchestrator(
            name=self.name, target_dir="/tmp/test", port=8888, no_tunnel=True,
        )
        orch._agent_output = [
            '<<<RESULT>>>{"service_url":"http://localhost:8888","pid":54321}<<<END>>>',
        ]
        result = orch.run()
        self.assertEqual(result.service_url, "http://localhost:8888")
        self.assertEqual(result.pid, 54321)
        mock_complete.assert_called_once_with(self.name, success=True, deploy_url="http://localhost:8888")

    @patch("sw_lib.core.deploy_orchestrator.AgentFactory.create")
    @patch("sw_lib.core.deploy_orchestrator._service.complete_deploy")
    def test_run_with_tunnel(self, mock_complete, mock_factory):
        mock_agent = MagicMock()
        mock_agent.wait.return_value = None
        mock_factory.return_value = mock_agent

        orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test", port=8888, no_tunnel=False)
        orch._agent_output = ['<<<RESULT>>>{"service_url":"http://localhost:8888","pid":123}<<<END>>>']

        with patch("sw_lib.core.deploy_orchestrator.start_tunnel", return_value="https://tun.dev"):
            result = orch.run()
        self.assertEqual(result.tunnel_url, "https://tun.dev")

    @patch("sw_lib.core.deploy_orchestrator.AgentFactory.create")
    @patch("sw_lib.core.deploy_orchestrator._service.complete_deploy")
    def test_run_no_result_raises(self, mock_complete, mock_factory):
        mock_agent = MagicMock()
        mock_agent.wait.return_value = None
        mock_factory.return_value = mock_agent

        orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test", port=8888, no_tunnel=True)
        orch._agent_output = ["agent did not produce result"]

        with self.assertRaises(RuntimeError):
            orch.run()
        mock_complete.assert_called_once_with(self.name, success=False)

    @patch("sw_lib.core.deploy_orchestrator.AgentFactory.create")
    @patch("sw_lib.core.deploy_orchestrator._service.complete_deploy")
    def test_run_writes_pid_file(self, mock_complete, mock_factory):
        mock_agent = MagicMock()
        mock_agent.wait.return_value = None
        mock_factory.return_value = mock_agent

        orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test", port=8888, no_tunnel=True)
        orch._agent_output = ['<<<RESULT>>>{"service_url":"http://localhost:8888","pid":99999}<<<END>>>']
        orch.run()

        pid_file = TASKS / self.name / ".deploy.pid"
        self.assertTrue(pid_file.exists())
        self.assertEqual(pid_file.read_text().strip(), "99999")


class TestDeployOrchestratorStop(unittest.TestCase):
    """DeployOrchestrator.stop() tests"""

    def setUp(self):
        self.name = "test-orch-stop"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)
        self.orch = DeployOrchestrator(name=self.name, target_dir="/tmp/test")

    def tearDown(self):
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    @patch("sw_lib.core.deploy_orchestrator.stop_tunnel")
    @patch("sw_lib.core.deploy_orchestrator.os.kill")
    def test_stop_cleans_up(self, mock_kill, mock_stop_tunnel):
        pid_file = TASKS / self.name / ".deploy.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("12345")
        self.orch.agent = MagicMock()
        self.orch.stop()
        mock_stop_tunnel.assert_called_once_with(self.name)
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
        self.assertFalse(pid_file.exists())

    @patch("sw_lib.core.deploy_orchestrator.stop_tunnel")
    def test_stop_no_agent(self, mock_stop_tunnel):
        self.orch.stop()
        mock_stop_tunnel.assert_called_once_with(self.name)


class TestDeployServiceIntegration(unittest.TestCase):
    """Integration tests with TaskService"""

    def setUp(self):
        self.name = "test-orch-svc"
        self.task_dir = TASKS / self.name
        self.target = TASKS.parent / "repo" / self.name
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        self.target.mkdir(parents=True, exist_ok=True)
        _service.create_task(self.name, task_type="feature", target_dir=str(self.target))

    def tearDown(self):
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def test_deploy_task_sets_deploying(self):
        st = _service.deploy_task(self.name, force=True)
        self.assertEqual(st["deploy_status"], "deploying")

    def test_complete_deploy_success(self):
        _service.deploy_task(self.name, force=True)
        _service.complete_deploy(self.name, success=True, deploy_url="http://localhost:8000")
        st = _service.get_task_state(self.name)
        self.assertEqual(st["deploy_status"], "deployed")
        self.assertEqual(st["deploy_url"], "http://localhost:8000")

    def test_complete_deploy_failed(self):
        _service.deploy_task(self.name, force=True)
        _service.complete_deploy(self.name, success=False)
        st = _service.get_task_state(self.name)
        self.assertEqual(st["deploy_status"], "deploy_failed")

    @patch("sw_lib.core.deploy_orchestrator.AgentFactory.create")
    @patch("sw_lib.core.deploy_orchestrator._service.complete_deploy")
    def test_orchestrator_updates_status(self, mock_complete, mock_factory):
        mock_agent = MagicMock()
        mock_agent.wait.return_value = None
        mock_factory.return_value = mock_agent

        _service.deploy_task(self.name, force=True)
        orch = DeployOrchestrator(name=self.name, target_dir=str(self.target), port=8000, no_tunnel=True)
        orch._agent_output = ['<<<RESULT>>>{"service_url":"http://localhost:8000","pid":777}<<<END>>>']
        orch.run()
        mock_complete.assert_called_once_with(self.name, success=True, deploy_url="http://localhost:8000")


class TestRunDeployOrchestrator(unittest.TestCase):
    """run_deploy_orchestrator convenience function"""

    @patch("sw_lib.core.deploy_orchestrator.DeployOrchestrator")
    def test_creates_and_runs(self, mock_orch_class):
        mock_instance = MagicMock()
        mock_instance.run.return_value = DeployResult(service_url="http://localhost:8000", pid=123)
        mock_orch_class.return_value = mock_instance

        result = run_deploy_orchestrator(
            name="test", target_dir="/tmp/test", port=8000, no_tunnel=True,
        )
        self.assertEqual(result.service_url, "http://localhost:8000")
        self.assertEqual(result.pid, 123)
        mock_orch_class.assert_called_once_with(
            name="test", target_dir="/tmp/test", port=8000, no_tunnel=True, log_callback=None,
        )
        mock_instance.run.assert_called_once()
