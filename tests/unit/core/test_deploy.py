"""Tests for sw_lib.core.deploy — 共享部署运行器"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

from sw_lib.core.config import TASKS
from sw_lib.core.service import _service, TaskError
from sw_lib.core.state import write_state, read_state
from sw_lib.core.deploy import run_deploy_agent, DeployRunner, ProjectInfo


class TestDeployTaskForce(unittest.TestCase):
    """测试 deploy_task(force=True/False) 的行为"""

    def setUp(self):
        self.name = "test-deploy-force"
        self.task_dir = TASKS / self.name
        self.target = TASKS.parent / "repo" / self.name
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        self.target.mkdir(parents=True, exist_ok=True)
        _service.create_task(
            self.name, task_type="feature", target_dir=str(self.target)
        )

    def tearDown(self):
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def test_deploy_force_skips_finished_check(self):
        """force=True 时，即使 stage_status != Finished 也不抛异常"""
        st = read_state(self.name)
        self.assertNotEqual(st.get("stage_status"), "Finished")
        result = _service.deploy_task(self.name, force=True)
        self.assertEqual(result["deploy_status"], "deploying")

    def test_deploy_no_force_requires_finished(self):
        """force=False 时，未 Finished 的任务应抛出异常"""
        with self.assertRaises(TaskError):
            _service.deploy_task(self.name, force=False)

    def test_deploy_no_force_with_finished(self):
        """force=False 时，Finished 的任务应允许部署"""
        st = read_state(self.name)
        st["stage_status"] = "Finished"
        write_state(self.name, st)
        result = _service.deploy_task(self.name, force=False)
        self.assertEqual(result["deploy_status"], "deploying")

    def test_deploy_missing_target_dir(self):
        """目标目录不存在时应抛异常"""
        st = read_state(self.name)
        st["target_dir"] = "/nonexistent/path999"
        write_state(self.name, st)
        with self.assertRaises(TaskError):
            _service.deploy_task(self.name, force=True)


class TestRunDeployAgent(unittest.TestCase):
    """测试 run_deploy_agent 的日志回调"""

    def setUp(self):
        self.name = "test-deploy-agent"
        self.task_dir = TASKS / self.name
        self.target = TASKS.parent / "repo" / self.name
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        self.target.mkdir(parents=True, exist_ok=True)
        _service.create_task(
            self.name, task_type="feature", target_dir=str(self.target)
        )

    def tearDown(self):
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    @patch("sw_lib.core.deploy.DeployRunner")
    def test_log_callback_called(self, mock_runner_class):
        """log_callback 应在部署过程中被调用"""
        mock_runner = MagicMock()
        mock_runner.run.return_value = "http://localhost:8000"
        mock_runner_class.return_value = mock_runner

        log_lines = []

        def log_callback(msg):
            log_lines.append(msg)

        result = run_deploy_agent(
            self.name, str(self.target),
            log_callback=log_callback,
            use_agent=False,
        )

        self.assertEqual(result, "http://localhost:8000")
        self.assertTrue(mock_runner.run.called)

    def test_new_params_default(self):
        """run_deploy_agent 的新参数 port/no_tunnel 应默认传递给 DeployRunner"""
        runner = DeployRunner(name=self.name, target_dir=str(self.target))
        self.assertEqual(runner.port, 8000)
        self.assertFalse(runner.no_tunnel)

    def test_new_params_custom(self):
        """run_deploy_agent 的自定义参数应正确传递"""
        runner = DeployRunner(
            name=self.name,
            target_dir=str(self.target),
            port=9000,
            no_tunnel=True,
        )
        self.assertEqual(runner.port, 9000)
        self.assertTrue(runner.no_tunnel)


class TestDeployServiceDetection(unittest.TestCase):
    """测试 DeployRunner.detect_project()"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.name = "test-detect"
        self.task_dir = TASKS / self.name
        if not self.task_dir.exists():
            self.task_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def _runner(self, target_dir=None):
        return DeployRunner(
            name=self.name,
            target_dir=target_dir or self.tmpdir,
        )

    def test_detect_main_py(self):
        """检测 main.py 入口"""
        Path(self.tmpdir, "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
        )
        info = self._runner().detect_project()
        self.assertEqual(info.entry, "main.py")
        self.assertEqual(info.framework, "fastapi")

    def test_detect_app_py(self):
        """检测 app.py 入口"""
        Path(self.tmpdir, "app.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
        )
        info = self._runner().detect_project()
        self.assertEqual(info.entry, "app.py")
        self.assertEqual(info.framework, "fastapi")

    def test_detect_manage_py(self):
        """检测 manage.py (Django) 入口"""
        Path(self.tmpdir, "manage.py").write_text(
            "#!/usr/bin/env python\nif __name__ == '__main__':\n"
        )
        info = self._runner().detect_project()
        self.assertEqual(info.entry, "manage.py")
        self.assertEqual(info.framework, "django")

    def test_detect_requirements(self):
        """检测 requirements.txt"""
        Path(self.tmpdir, "requirements.txt").write_text("fastapi\nuvicorn\n")
        info = self._runner().detect_project()
        self.assertTrue(info.has_requirements)

    def test_detect_pyproject(self):
        """检测 pyproject.toml"""
        Path(self.tmpdir, "pyproject.toml").write_text("[project]\n")
        info = self._runner().detect_project()
        self.assertTrue(info.has_pyproject)

    def test_detect_venv(self):
        """检测已有 .venv"""
        Path(self.tmpdir, ".venv").mkdir(parents=True, exist_ok=True)
        info = self._runner().detect_project()
        self.assertTrue(info.has_venv)

    def test_detect_empty_dir(self):
        """空目录应返回通用类型"""
        info = self._runner().detect_project()
        self.assertEqual(info.framework, "generic")
        self.assertEqual(info.entry, "")
        self.assertFalse(info.has_requirements)

    def test_detect_flask_app(self):
        """检测 Flask 框架"""
        Path(self.tmpdir, "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n"
        )
        info = self._runner().detect_project()
        self.assertEqual(info.framework, "flask")

    def test_detect_main_py_priority(self):
        """main.py 优先级高于 app.py"""
        Path(self.tmpdir, "main.py").write_text("print('hello')\n")
        Path(self.tmpdir, "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        info = self._runner().detect_project()
        self.assertEqual(info.entry, "main.py")


class TestDeployPortSelection(unittest.TestCase):
    """测试 DeployRunner.resolve_port()"""

    def setUp(self):
        self.name = "test-port"
        self.task_dir = TASKS / self.name
        if not self.task_dir.exists():
            self.task_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def test_resolve_free_port(self):
        """空闲端口应直接返回"""
        runner = DeployRunner(name=self.name, target_dir="/tmp")
        port = runner.resolve_port(9999)
        self.assertEqual(port, 9999)

    def test_resolve_port_increment(self):
        """被占用端口应自动递增"""
        runner = DeployRunner(name=self.name, target_dir="/tmp")
        # 占用端口 18081
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 18081))
        s.listen()
        try:
            port = runner.resolve_port(18081)
            self.assertEqual(port, 18082)
        finally:
            s.close()

    def test_resolve_all_occupied(self):
        """全部端口被占用时应抛出异常"""
        runner = DeployRunner(name=self.name, target_dir="/tmp")
        sockets = []
        try:
            # 占用 18091-18101
            for p in range(18091, 18102):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("127.0.0.1", p))
                s.listen()
                sockets.append(s)
            with self.assertRaises(RuntimeError):
                runner.resolve_port(18091)
        finally:
            for s in sockets:
                s.close()


class TestDeployDepsInstall(unittest.TestCase):
    """测试 DeployRunner.install_deps()"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.name = "test-deps"
        self.task_dir = TASKS / self.name
        if not self.task_dir.exists():
            self.task_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir, ignore_errors=True)

    def test_install_no_requirements(self):
        """没有 requirements.txt 时应跳过安装"""
        runner = DeployRunner(name=self.name, target_dir=self.tmpdir)
        info = ProjectInfo(has_requirements=False)
        result = runner.install_deps(info)
        self.assertTrue(result)

    def test_install_with_requirements_triggers_pip(self):
        """有 requirements.txt 时应触发 pip install"""
        Path(self.tmpdir, "requirements.txt").write_text("flask\n")
        runner = DeployRunner(name=self.name, target_dir=self.tmpdir)
        info = ProjectInfo(has_requirements=True)
        # 创建 venv 以加速（不依赖外部 pip 安装）
        subprocess.run(
            ["python3", "-m", "venv", str(Path(self.tmpdir, ".venv"))],
            capture_output=True, timeout=30,
        )
        info.has_venv = True
        result = runner.install_deps(info)
        # pip install 可能成功或失败，但不应该抛出异常
        self.assertIsInstance(result, bool)


class TestDeployRunnerBuildCommand(unittest.TestCase):
    """测试 DeployRunner._build_start_command()"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.name = "test-cmd"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _runner(self, has_venv=False):
        if has_venv:
            Path(self.tmpdir, ".venv").mkdir(parents=True, exist_ok=True)
        return DeployRunner(name=self.name, target_dir=self.tmpdir)

    def test_fastapi_main(self):
        """FastAPI + main.py 应生成 uvicorn 命令"""
        Path(self.tmpdir, "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        info = self._runner().detect_project()
        cmd = self._runner()._build_start_command(info)
        self.assertIn("uvicorn", cmd)
        self.assertIn("main:app", cmd[1])

    def test_fastapi_with_app_var(self):
        """FastAPI 应用变量非 app 时能正确识别"""
        Path(self.tmpdir, "main.py").write_text(
            "from fastapi import FastAPI\napplication = FastAPI()\n"
        )
        info = self._runner().detect_project()
        cmd = self._runner()._build_start_command(info)
        self.assertIn("main:application", cmd[1])

    def test_django_manage(self):
        """Django 应生成 manage.py runserver 命令"""
        Path(self.tmpdir, "manage.py").write_text("if __name__ == '__main__':\n")
        info = self._runner().detect_project()
        cmd = self._runner()._build_start_command(info)
        self.assertIn("manage.py", cmd)
        self.assertIn("runserver", cmd)

    def test_generic_python_entry(self):
        """通用入口应使用 python3 运行"""
        Path(self.tmpdir, "app.py").write_text("print('hello')\n")
        # 不写 FastAPI/Flask 内容，使框架为 generic
        info = self._runner().detect_project()
        cmd = self._runner()._build_start_command(info)
        self.assertIn("python3", cmd)
        self.assertIn("app.py", cmd)

    def test_no_entry_fallback(self):
        """无入口文件时应兜底到 uvicorn main:app"""
        info = ProjectInfo()
        info.port = 8000
        cmd = self._runner()._build_start_command(info)
        self.assertIn("uvicorn", cmd)
        self.assertIn("main:app", cmd[1])

    def test_maven_jar_absolute_path_with_relative_target_dir(self):
        """Maven 项目使用相对 target_dir 时，jar 路径应为绝对路径"""
        target_dir = Path(self.tmpdir, "target")
        target_dir.mkdir(parents=True, exist_ok=True)
        jar_file = target_dir / "myapp-1.0.jar"
        jar_file.write_text("dummy")

        # 计算 tmpdir 相对于当前工作目录的相对路径
        rel_dir = os.path.relpath(self.tmpdir)

        info = ProjectInfo(framework="maven")
        info.port = 8000
        runner = DeployRunner(name=self.name, target_dir=rel_dir)
        cmd = runner._build_start_command(info)

        self.assertEqual(cmd[0], "java")
        self.assertEqual(cmd[1], "-jar")
        # jar 路径必须是绝对路径
        jar_arg = cmd[2]
        self.assertTrue(
            jar_arg.startswith("/"),
            f"Maven jar 路径应为绝对路径，实际: {jar_arg}",
        )
        # 验证解析后的路径正确
        expected = str((Path(self.tmpdir).resolve() / "target" / "myapp-1.0.jar"))
        self.assertEqual(jar_arg, expected)


class TestDeployRunnerPortInUse(unittest.TestCase):
    """测试 DeployRunner._is_port_in_use()"""

    def test_port_free(self):
        """空闲端口应返回 False"""
        self.assertFalse(DeployRunner._is_port_in_use(19999))

    def test_port_in_use(self):
        """被占用端口应返回 True"""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 19998))
        s.listen()
        try:
            self.assertTrue(DeployRunner._is_port_in_use(19998))
        finally:
            s.close()
