"""Tests for sw_lib.core.health — HealthMonitor and HealthConfig"""
import os
import socket
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from sw_lib.core.config import TASKS
from sw_lib.core.health import HealthConfig, HealthMonitor


class TestHealthConfig(unittest.TestCase):
    """HealthConfig dataclass tests"""

    def test_default_values(self):
        cfg = HealthConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.check_interval, 10)
        self.assertEqual(cfg.failure_threshold, 3)
        self.assertFalse(cfg.auto_redeploy)
        self.assertEqual(cfg.max_redeploys, 5)
        self.assertEqual(cfg.redeploy_window_sec, 300)

    def test_custom_values(self):
        cfg = HealthConfig(
            enabled=False,
            check_interval=30,
            failure_threshold=5,
            auto_redeploy=True,
            max_redeploys=3,
            redeploy_window_sec=600,
        )
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.check_interval, 30)
        self.assertEqual(cfg.failure_threshold, 5)
        self.assertTrue(cfg.auto_redeploy)
        self.assertEqual(cfg.max_redeploys, 3)
        self.assertEqual(cfg.redeploy_window_sec, 600)


class TestHealthMonitorInit(unittest.TestCase):
    """HealthMonitor.__init__ tests"""

    def test_default_config(self):
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        self.assertEqual(hm.name, "test")
        self.assertEqual(hm.target_dir, "/tmp/test")
        self.assertEqual(hm.port, 8000)
        self.assertIsInstance(hm.config, HealthConfig)
        self.assertTrue(hm.config.enabled)
        self.assertIsNone(hm.log_callback)
        self.assertIsNotNone(hm._stop_event)
        self.assertEqual(hm._failure_count, 0)
        self.assertEqual(hm._on_redeploy_timestamps, [])

    def test_custom_config(self):
        cfg = HealthConfig(check_interval=60, auto_redeploy=True)
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=9000, config=cfg)
        self.assertEqual(hm.config.check_interval, 60)
        self.assertTrue(hm.config.auto_redeploy)

    def test_log_callback(self):
        logs = []
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000, log_callback=logs.append)
        hm._log("hello")
        self.assertTrue(any("hello" in l for l in logs))

    def test_stop_event_initial_state(self):
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        self.assertFalse(hm._stop_event.is_set())


class TestHealthMonitorCheckTcp(unittest.TestCase):
    """HealthMonitor._check_tcp() tests"""

    def setUp(self):
        self.hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)

    @patch("sw_lib.core.health.socket.create_connection")
    def test_tcp_success(self, mock_create_conn):
        mock_sock = MagicMock()
        mock_create_conn.return_value.__enter__.return_value = mock_sock
        result = self.hm._check_tcp()
        self.assertTrue(result)
        mock_create_conn.assert_called_once_with(("127.0.0.1", 8000), timeout=5)

    @patch("sw_lib.core.health.socket.create_connection")
    def test_tcp_connection_refused(self, mock_create_conn):
        mock_create_conn.side_effect = ConnectionRefusedError
        result = self.hm._check_tcp()
        self.assertFalse(result)

    @patch("sw_lib.core.health.socket.create_connection")
    def test_tcp_timeout(self, mock_create_conn):
        mock_create_conn.side_effect = socket.timeout
        result = self.hm._check_tcp()
        self.assertFalse(result)

    @patch("sw_lib.core.health.socket.create_connection")
    def test_tcp_os_error(self, mock_create_conn):
        mock_create_conn.side_effect = OSError("connection reset")
        result = self.hm._check_tcp()
        self.assertFalse(result)


class TestHealthMonitorCheckPid(unittest.TestCase):
    """HealthMonitor._check_pid() tests"""

    def setUp(self):
        self.hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)

    @patch("sw_lib.core.health.HealthMonitor._read_pid", return_value=12345)
    @patch("sw_lib.core.health.os.kill")
    def test_pid_success(self, mock_kill, mock_read_pid):
        result = self.hm._check_pid()
        self.assertTrue(result)
        mock_kill.assert_called_once_with(12345, 0)

    @patch("sw_lib.core.health.HealthMonitor._read_pid", return_value=12345)
    @patch("sw_lib.core.health.os.kill")
    def test_pid_process_lookup_error(self, mock_kill, mock_read_pid):
        mock_kill.side_effect = ProcessLookupError
        result = self.hm._check_pid()
        self.assertFalse(result)

    @patch("sw_lib.core.health.HealthMonitor._read_pid", return_value=12345)
    @patch("sw_lib.core.health.os.kill")
    def test_pid_os_error(self, mock_kill, mock_read_pid):
        mock_kill.side_effect = OSError("permission denied")
        result = self.hm._check_pid()
        self.assertFalse(result)

    @patch("sw_lib.core.health.HealthMonitor._read_pid", return_value=None)
    def test_pid_no_pid_file(self, mock_read_pid):
        result = self.hm._check_pid()
        self.assertFalse(result)


class TestHealthMonitorReadPid(unittest.TestCase):
    """HealthMonitor._read_pid() tests"""

    def setUp(self):
        self.hm = HealthMonitor(name="test-read-pid", target_dir="/tmp/test", port=8000)
        self.pid_file = TASKS / self.hm.name / ".deploy.pid"
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        if self.pid_file.exists():
            self.pid_file.unlink()

    def tearDown(self):
        if self.pid_file.exists():
            self.pid_file.unlink()
        parent = self.pid_file.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def test_read_valid_pid(self):
        self.pid_file.write_text("12345")
        pid = self.hm._read_pid()
        self.assertEqual(pid, 12345)

    def test_read_missing_file(self):
        pid = self.hm._read_pid()
        self.assertIsNone(pid)

    def test_read_invalid_content(self):
        self.pid_file.write_text("not-a-number")
        pid = self.hm._read_pid()
        self.assertIsNone(pid)

    def test_read_empty_file(self):
        self.pid_file.write_text("")
        pid = self.hm._read_pid()
        self.assertIsNone(pid)


class TestHealthMonitorRun(unittest.TestCase):
    """HealthMonitor.run() behavior tests"""

    def setUp(self):
        self.name = "test-run"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def tearDown(self):
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_tcp", return_value=True)
    def test_run_stop_cycle(self, mock_tcp, mock_pid):
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=HealthConfig(check_interval=1))
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.1)
        hm.stop()
        t.join(timeout=2)
        self.assertFalse(t.is_alive())

    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_tcp", return_value=False)
    def test_failure_count_increments(self, mock_tcp, mock_pid):
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=HealthConfig(check_interval=1))
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.1)
        hm.stop()
        t.join(timeout=2)
        self.assertGreater(hm._failure_count, 0)

    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_tcp")
    def test_healthy_resets_counter(self, mock_tcp, mock_pid):
        mock_tcp.side_effect = [False, False, True]
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=HealthConfig(check_interval=1))
        hm._failure_count = 2
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.1)
        hm.stop()
        t.join(timeout=2)
        self.assertEqual(hm._failure_count, 0)

    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_tcp", return_value=False)
    @patch("sw_lib.core.health.HealthMonitor._trigger_redeploy")
    def test_failure_threshold_triggers_redeploy(self, mock_trigger, mock_tcp, mock_pid):
        hm = HealthMonitor(
            name=self.name, target_dir="/tmp/test", port=8000,
            config=HealthConfig(check_interval=1, failure_threshold=2),
        )
        hm._failure_count = 1
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.1)
        hm.stop()
        t.join(timeout=2)
        mock_trigger.assert_called_once()

    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_tcp", return_value=True)
    def test_stop_event_set_on_stop(self, mock_tcp, mock_pid):
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=HealthConfig(check_interval=1))
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.1)
        hm.stop()
        t.join(timeout=2)
        self.assertTrue(hm._stop_event.is_set())


class TestHealthMonitorTriggerRedeploy(unittest.TestCase):
    """HealthMonitor._trigger_redeploy() tests"""

    def setUp(self):
        self.name = "test-trigger"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)
        self.task_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    @patch("sw_lib.core.health._service")
    def test_auto_mode_calls_redeploy(self, mock_service):
        cfg = HealthConfig(failure_threshold=3, auto_redeploy=True)
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=cfg)
        with patch.object(hm, "_redeploy") as mock_redeploy:
            with patch.object(hm, "_check_circuit_breaker", return_value=False):
                hm._trigger_redeploy()
                mock_redeploy.assert_called_once()

    @patch("sw_lib.core.health._service")
    def test_manual_mode_marks_health_failed(self, mock_service):
        cfg = HealthConfig(failure_threshold=3, auto_redeploy=False)
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=cfg)
        mock_service.get_task_state.return_value = {
            "health_status": "ok",
            "deploy_status": "deployed",
        }
        hm._trigger_redeploy()
        mock_service.get_task_state.assert_called_once_with(self.name)
        mock_service._write_state_safe.assert_called_once()
        written_state = mock_service._write_state_safe.call_args[0][1]
        self.assertEqual(written_state["health_status"], "health_failed")
        self.assertEqual(written_state["deploy_status"], "deployed_unhealthy")

    @patch("sw_lib.core.health._service")
    def test_manual_mode_does_not_overwrite_deploy_failed(self, mock_service):
        """deploy_status 非 'deployed' 时不应被覆盖"""
        cfg = HealthConfig(failure_threshold=3, auto_redeploy=False)
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=cfg)
        mock_service.get_task_state.return_value = {
            "health_status": "ok",
            "deploy_status": "deploy_failed",
        }
        hm._trigger_redeploy()
        written_state = mock_service._write_state_safe.call_args[0][1]
        self.assertEqual(written_state["health_status"], "health_failed")
        self.assertEqual(written_state["deploy_status"], "deploy_failed")

    @patch("sw_lib.core.health._service")
    def test_circuit_breaker_blocks_redeploy(self, mock_service):
        cfg = HealthConfig(failure_threshold=3, auto_redeploy=True)
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=cfg)
        mock_service.get_task_state.return_value = {"health_status": "ok"}
        with patch.object(hm, "_redeploy") as mock_redeploy:
            with patch.object(hm, "_check_circuit_breaker", return_value=True):
                hm._trigger_redeploy()
                mock_redeploy.assert_not_called()
                mock_service._write_state_safe.assert_called_once()
                written_state = mock_service._write_state_safe.call_args[0][1]
                self.assertEqual(written_state["health_status"], "circuit_broken")

    @patch("sw_lib.core.health._service")
    def test_service_error_does_not_crash(self, mock_service):
        mock_service.get_task_state.side_effect = Exception("state error")
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        with patch.object(hm, "_redeploy"):
            with patch.object(hm, "_check_circuit_breaker", return_value=False):
                hm._trigger_redeploy()


class TestHealthMonitorRedeploy(unittest.TestCase):
    """HealthMonitor._redeploy() tests"""

    def setUp(self):
        self.name = "test-redeploy"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)
        self.task_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    @patch("sw_lib.core.health.stop_tunnel")
    @patch("sw_lib.core.health.os.kill")
    @patch("sw_lib.core.health.DeployOrchestrator")
    @patch("sw_lib.core.health.HealthMonitor._find_free_port", return_value=8000)
    def test_redeploy_success(self, mock_find_port, mock_orch_class, mock_kill, mock_stop):
        mock_result = MagicMock()
        mock_result.service_url = "http://localhost:8000"
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.return_value = mock_result
        mock_orch_class.return_value = mock_orch_instance

        pid_file = TASKS / self.name / ".deploy.pid"
        pid_file.write_text("99999")

        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._redeploy()

        mock_stop.assert_called_once_with(self.name)
        mock_kill.assert_called_once_with(99999, 15)
        mock_orch_class.assert_called_once()
        self.assertEqual(len(hm._on_redeploy_timestamps), 1)  # timestamp appended after redeploy

    @patch("sw_lib.core.health.stop_tunnel")
    @patch("sw_lib.core.health.DeployOrchestrator")
    @patch("sw_lib.core.health.HealthMonitor._find_free_port", return_value=9000)
    def test_redeploy_port_change(self, mock_find_port, mock_orch_class, mock_stop):
        mock_result = MagicMock()
        mock_result.service_url = "http://localhost:9000"
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.return_value = mock_result
        mock_orch_class.return_value = mock_orch_instance

        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._redeploy()
        self.assertEqual(len(hm._on_redeploy_timestamps), 1)
        mock_orch_class.assert_called_once()

    @patch("sw_lib.core.health.stop_tunnel")
    @patch("sw_lib.core.health.DeployOrchestrator")
    @patch("sw_lib.core.health.HealthMonitor._find_free_port", return_value=8000)
    @patch("sw_lib.core.health._service")
    def test_redeploy_failure_updates_state(self, mock_service, mock_find, mock_orch_class, mock_stop):
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.side_effect = Exception("deploy failed")
        mock_orch_class.return_value = mock_orch_instance

        mock_service.get_task_state.return_value = {}

        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._redeploy()

        mock_service._write_state_safe.assert_called_once()
        written_state = mock_service._write_state_safe.call_args[0][1]
        self.assertEqual(written_state["deploy_status"], "deploy_failed")
        self.assertEqual(written_state["health_status"], "redeploy_failed")
        self.assertEqual(len(hm._on_redeploy_timestamps), 1)

    @patch("sw_lib.core.health.stop_tunnel")
    @patch("sw_lib.core.health.os.kill")
    @patch("sw_lib.core.health.DeployOrchestrator")
    @patch("sw_lib.core.health.HealthMonitor._find_free_port", return_value=8000)
    def test_redeploy_cleans_pid_file(self, mock_find, mock_orch_class, mock_kill, mock_stop):
        mock_result = MagicMock()
        mock_result.service_url = "http://localhost:8000"
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.return_value = mock_result
        mock_orch_class.return_value = mock_orch_instance

        pid_file = TASKS / self.name / ".deploy.pid"
        pid_file.write_text("12345")

        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._redeploy()
        self.assertFalse(pid_file.exists())

    @patch("sw_lib.core.health.stop_tunnel", side_effect=Exception("tunnel error"))
    @patch("sw_lib.core.health.DeployOrchestrator")
    @patch("sw_lib.core.health.HealthMonitor._find_free_port", return_value=8000)
    def test_redeploy_tunnel_error_does_not_block(self, mock_find, mock_orch_class, mock_stop):
        mock_result = MagicMock()
        mock_result.service_url = "http://localhost:8000"
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.return_value = mock_result
        mock_orch_class.return_value = mock_orch_instance

        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._redeploy()
        mock_orch_class.assert_called_once()


class TestHealthMonitorCircuitBreaker(unittest.TestCase):
    """HealthMonitor._check_circuit_breaker() tests"""

    def setUp(self):
        self.hm = HealthMonitor(
            name="test-cb",
            target_dir="/tmp/test",
            port=8000,
            config=HealthConfig(max_redeploys=3, redeploy_window_sec=300),
        )

    def test_below_threshold_allows(self):
        self.hm._on_redeploy_timestamps = [time.time() - 10, time.time() - 5]
        result = self.hm._check_circuit_breaker()
        self.assertFalse(result)

    def test_at_threshold_activates(self):
        now_ts = time.time()
        self.hm._on_redeploy_timestamps = [now_ts - 10, now_ts - 5, now_ts - 1]
        result = self.hm._check_circuit_breaker()
        self.assertTrue(result)

    def test_exceeds_threshold_activates(self):
        now_ts = time.time()
        self.hm._on_redeploy_timestamps = [now_ts - 10, now_ts - 8, now_ts - 5, now_ts - 1]
        result = self.hm._check_circuit_breaker()
        self.assertTrue(result)

    def test_old_timestamps_cleaned(self):
        now_ts = time.time()
        self.hm._on_redeploy_timestamps = [
            now_ts - 400,  # outside window
            now_ts - 10,
            now_ts - 5,
        ]
        self.hm._check_circuit_breaker()
        self.assertEqual(len(self.hm._on_redeploy_timestamps), 2)

    def test_empty_timestamps_allows(self):
        self.hm._on_redeploy_timestamps = []
        result = self.hm._check_circuit_breaker()
        self.assertFalse(result)

    def test_one_timestamp_below_threshold(self):
        self.hm._on_redeploy_timestamps = [time.time() - 1]
        result = self.hm._check_circuit_breaker()
        self.assertFalse(result)


class TestHealthMonitorCheckUrl(unittest.TestCase):
    """HealthMonitor._check_url() tests"""

    def setUp(self):
        self.hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)

    def test_no_url_returns_true(self):
        """check_url 为空时直接跳过"""
        self.assertTrue(self.hm._check_url())

    def test_no_url_returns_true(self):
        """state 中无 deploy_url 时跳过检测"""
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        with patch("sw_lib.core.health._service.get_task_state", return_value={}):
            self.assertTrue(hm._check_url())

    def test_with_check_url_returns_true(self):
        """deploy_url 可访问时返回 True"""
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        with patch("sw_lib.core.health._service.get_task_state",
                   return_value={"deploy_url": "http://localhost:8000"}):
            with patch("sw_lib.core.health.urllib.request.urlopen") as mock_urlopen:
                result = hm._check_url()
                self.assertTrue(result)
                mock_urlopen.assert_called_once()

    def test_with_check_url_connection_error(self):
        """deploy_url 不可访问时返回 False"""
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        with patch("sw_lib.core.health._service.get_task_state",
                   return_value={"deploy_url": "http://localhost:9999"}):
            with patch("sw_lib.core.health.urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.side_effect = ConnectionError("connection refused")
                result = hm._check_url()
                self.assertFalse(result)

    def test_check_url_uses_head_method(self):
        """_check_url 用 HEAD 而不是 GET"""
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        with patch("sw_lib.core.health._service.get_task_state",
                   return_value={"deploy_url": "http://localhost:8000"}):
            with patch("sw_lib.core.health.urllib.request.urlopen") as mock_urlopen:
                hm._check_url()
                call_req = mock_urlopen.call_args[0][0]
                self.assertEqual(call_req.method, "HEAD")
                self.assertEqual(call_req.full_url, "http://localhost:8000")


class TestHealthMonitorFindFreePort(unittest.TestCase):
    """HealthMonitor._find_free_port() tests"""

    @patch("sw_lib.core.health.socket.socket")
    def test_preferred_port_available(self, mock_sock_class):
        mock_sock = MagicMock()
        mock_sock_class.return_value.__enter__.return_value = mock_sock
        result = HealthMonitor._find_free_port(8000)
        self.assertEqual(result, 8000)
        mock_sock.bind.assert_called_once_with(("127.0.0.1", 8000))

    @patch("sw_lib.core.health.socket.socket")
    def test_preferred_port_unavailable(self, mock_sock_class):
        mock_sock = MagicMock()
        mock_sock.bind.side_effect = [OSError("port in use"), None]
        mock_sock.getsockname.return_value = ("127.0.0.1", 9000)
        mock_sock_class.return_value.__enter__.return_value = mock_sock
        result = HealthMonitor._find_free_port(8000)
        self.assertEqual(result, 9000)

    @patch("sw_lib.core.health.socket.socket")
    def test_preferred_zero_uses_random(self, mock_sock_class):
        mock_sock = MagicMock()
        mock_sock.getsockname.return_value = ("127.0.0.1", 12345)
        mock_sock_class.return_value.__enter__.return_value = mock_sock
        result = HealthMonitor._find_free_port(0)
        self.assertEqual(result, 12345)
        mock_sock.bind.assert_called_once_with(("127.0.0.1", 0))


class TestHealthMonitorLog(unittest.TestCase):
    """HealthMonitor._log() tests"""

    def setUp(self):
        self.name = "test-log"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)
        self.task_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def test_log_with_callback(self):
        logs = []
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, log_callback=logs.append)
        hm._log("test message")
        self.assertTrue(any("test message" in l for l in logs))

    def test_log_without_callback(self):
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._log("no callback test")

    @patch("sw_lib.core.health.sw_log")
    def test_log_calls_sw_log(self, mock_sw_log):
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._log("sw log test")
        mock_sw_log.assert_called_once_with(self.name, "sw log test", "health")

    @patch("sw_lib.core.health.sw_log", side_effect=Exception("log error"))
    def test_log_handles_sw_log_error(self, mock_sw_log):
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000)
        hm._log("this should not crash")


class TestHealthMonitorIntegration(unittest.TestCase):
    """Integration tests with service state"""

    def setUp(self):
        self.name = "test-health-int"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)
        self.task_dir.mkdir(parents=True, exist_ok=True)

        from sw_lib.core.state import write_state
        write_state(self.name, {
            "id": self.name,
            "deploy_status": "deployed",
            "deploy_url": "http://localhost:8000",
            "health_config": {
                "enabled": True,
                "check_interval": 5,
                "failure_threshold": 2,
                "auto_redeploy": False,
                "max_redeploys": 3,
                "redeploy_window_sec": 300,
            },
        })

    def tearDown(self):
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    @patch("sw_lib.core.health._service")
    def test_trigger_redeploy_updates_state_manual(self, mock_service):
        mock_service.get_task_state.return_value = {
            "id": self.name,
            "deploy_status": "deployed",
        }
        cfg = HealthConfig(failure_threshold=2, auto_redeploy=False)
        hm = HealthMonitor(name=self.name, target_dir="/tmp/test", port=8000, config=cfg)
        hm._trigger_redeploy()
        mock_service._write_state_safe.assert_called_once()
        written = mock_service._write_state_safe.call_args[0][1]
        self.assertEqual(written["health_status"], "health_failed")
        self.assertEqual(written["deploy_status"], "deployed_unhealthy")

    def test_state_has_health_config(self):
        from sw_lib.core.state import read_state
        st = read_state(self.name)
        hc = st.get("health_config", {})
        self.assertEqual(hc["check_interval"], 5)
        self.assertEqual(hc["failure_threshold"], 2)
        self.assertEqual(hc["auto_redeploy"], False)
        self.assertEqual(hc["max_redeploys"], 3)

    def test_read_state_backward_compatible(self):
        from sw_lib.core.state import read_state
        import json
        sf = self.task_dir / ".state"
        old_data = {
            "id": self.name,
            "deploy_status": "deployed",
        }
        sf.write_text(json.dumps(old_data), encoding="utf-8")
        st = read_state(self.name)
        self.assertIn("health_config", st)
        hc = st["health_config"]
        self.assertTrue(hc["enabled"])
        self.assertEqual(hc["check_interval"], 10)
        self.assertEqual(hc["failure_threshold"], 3)
        self.assertFalse(hc["auto_redeploy"])


class TestHealthMonitorStop(unittest.TestCase):
    """HealthMonitor.stop() tests"""

    def test_stop_sets_event(self):
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        hm.stop()
        self.assertTrue(hm._stop_event.is_set())

    def test_stop_twice_no_error(self):
        hm = HealthMonitor(name="test", target_dir="/tmp/test", port=8000)
        hm.stop()
        hm.stop()
        self.assertTrue(hm._stop_event.is_set())


class TestHealthMonitorRunFullFlow(unittest.TestCase):
    """End-to-end run flow tests"""

    def setUp(self):
        self.name = "test-full-flow"
        self.task_dir = TASKS / self.name
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def tearDown(self):
        if self.task_dir.exists():
            import shutil
            shutil.rmtree(self.task_dir, ignore_errors=True)

    @patch("sw_lib.core.health.HealthMonitor._check_tcp", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    def test_run_loop_healthy(self, mock_pid, mock_tcp):
        logs = []
        hm = HealthMonitor(
            name=self.name, target_dir="/tmp/test", port=8000,
            config=HealthConfig(check_interval=1),
            log_callback=logs.append,
        )
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.15)
        hm.stop()
        t.join(timeout=2)
        self.assertEqual(hm._failure_count, 0)
        self.assertTrue(any("started" in l for l in logs))
        self.assertTrue(any("stopped" in l for l in logs))

    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_tcp")
    @patch("sw_lib.core.health.HealthMonitor._trigger_redeploy")
    def test_run_loop_triggers_redeploy(self, mock_trigger, mock_tcp, mock_pid):
        mock_tcp.side_effect = [False, False, False]
        hm = HealthMonitor(
            name=self.name, target_dir="/tmp/test", port=8000,
            config=HealthConfig(check_interval=1, failure_threshold=2),
        )
        hm._failure_count = 1
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.1)
        hm.stop()
        t.join(timeout=2)
        mock_trigger.assert_called_once()

    @patch("sw_lib.core.health.HealthMonitor._check_pid", return_value=True)
    @patch("sw_lib.core.health.HealthMonitor._check_tcp")
    def test_run_loop_healthy_resets_after_failure(self, mock_tcp, mock_pid):
        mock_tcp.side_effect = [False, False, True, True]
        hm = HealthMonitor(
            name=self.name, target_dir="/tmp/test", port=8000,
            config=HealthConfig(check_interval=1),
        )
        hm._failure_count = 2
        t = threading.Thread(target=hm.run, daemon=True)
        t.start()
        time.sleep(0.1)
        hm.stop()
        t.join(timeout=2)
        self.assertEqual(hm._failure_count, 0)


if __name__ == "__main__":
    unittest.main()
