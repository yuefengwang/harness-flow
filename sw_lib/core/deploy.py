"""
sw_lib.core.deploy — 部署模块 (v3: 全 Agent 驱动)

部署功能已统一由 DeployOrchestrator 接管（Agent 全权负责检测 + 执行）。
本模块保留向后兼容导出。
"""

from .deploy_orchestrator import (
    DeployOrchestrator,
    DeployResult,
    run_deploy_orchestrator,
)

# 向后兼容：run_deploy_agent → run_deploy_orchestrator
run_deploy_agent = run_deploy_orchestrator
