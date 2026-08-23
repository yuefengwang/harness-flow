"""Tests for sw_lib.core.deploy — v3: 全 Agent 驱动 (DeployOrchestrator)

DeployRunner 已移除。部署功能统一由 DeployOrchestrator 接管。
DeployOrchestrator 测试见 test_deploy_orchestrator.py。
"""

import shutil
import unittest

from sw_lib.core.config import TASKS
from sw_lib.core.service import _service, TaskError
from sw_lib.core.state import write_state


# ── deploy_task 服务层测试 ──

class TestDeployTaskForce(unittest.TestCase):
    """测试 deploy_task(force=True/False) 的行为"""

    def setUp(self):
        self.name = "test-deploy-force"
        self.task_dir = TASKS / self.name
        self.target = TASKS.parent / "repo" / self.name
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.target.mkdir(parents=True, exist_ok=True)
        write_state(self.name, {
            "id": self.name,
            "stage": "05-archive",
            "stage_idx": 4,
            "stage_status": "Finished",
            "agent": "cat",
            "target_dir": str(self.target),
            "deploy_status": "",
        })

    def tearDown(self):
        for d in [self.task_dir, self.target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def test_deploy_force_skips_finished_check(self):
        """force=True 时跳过 Finished 检查"""
        try:
            _service.deploy_task(self.name, force=True)
        except TaskError as e:
            self.fail(f"deploy_task(force=True) should not raise: {e}")

    def test_deploy_no_force_requires_finished(self):
        """未 force 且任务非 Finished → 抛异常"""
        write_state(self.name, {
            "id": self.name,
            "stage": "03-coding",
            "stage_idx": 2,
            "stage_status": "running",
            "target_dir": str(self.target),
        })
        with self.assertRaises(TaskError):
            _service.deploy_task(self.name, force=False)

    def test_deploy_no_force_with_finished(self):
        """任务 Finished + force=False → 应允许部署"""
        write_state(self.name, {
            "id": self.name,
            "stage": "05-archive",
            "stage_idx": 4,
            "stage_status": "Finished",
            "target_dir": str(self.target),
        })
        try:
            _service.deploy_task(self.name, force=False)
        except TaskError as e:
            self.fail(f"Finished task should allow deploy: {e}")

    def test_deploy_missing_target_dir(self):
        """target_dir 缺失 → 抛异常"""
        shutil.rmtree(self.target, ignore_errors=True)
        write_state(self.name, {
            "id": self.name,
            "stage": "05-archive",
            "stage_idx": 4,
            "stage_status": "Finished",
            "target_dir": str(self.target),
        })
        with self.assertRaises(TaskError):
            _service.deploy_task(self.name, force=True)
