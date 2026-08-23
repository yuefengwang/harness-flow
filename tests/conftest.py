import pytest
import shutil
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state, remove_task_summary
from sw_lib.core import residue
from sw_lib.web.engine_manager import WebEngineManager


@pytest.fixture(scope="session", autouse=True)
def _reap_workspace_residue():
    """兜底清理测试在真实 workspace 里留下的残渣。

    大部分用例自己会 rmtree，但清理散落在二十多个文件里，且一个任务有三处
    产物（任务目录、`repo/<name>`、STATUS.json 条目）—— 必须手工配对的动作，
    漏一处就留孤儿。理想做法是所有用例都隔离到 tmp_path（见
    tests/unit/core/test_remove_all.py 的 isolated_workspace），但 TASKS
    被二十多个模块在导入期各自绑定，逐个 monkeypatch 反而更容易漏。

    判定交给 `sw_lib.core.residue`，按**名字模式**识别。此前这里用的是
    「跑前拍快照、跑后删新增」的差集策略，它有个致命漏洞：残留只要活过一次
    会话就进入下次的 before 快照，从此被永久豁免 —— 实测预置三处产物后跑
    全量，三处原样留存，仓库里因此积了 13 条孤儿。

    收尾时清一次就够：开场不清，避免与用户正在跑的任务抢文件。

    `SW_SKIP_REAP=1` 可跳过回收 —— 用于诊断「哪个用例在污染真实 workspace」：
    兜底扫帚一开，源头泄漏就被掩盖，看不出是谁漏的。
    """
    yield
    import os

    if os.environ.get("SW_SKIP_REAP") == "1":
        return
    residue.reap()


@pytest.fixture(scope="session", autouse=True)
def _isolate_evidence_key(tmp_path_factory):
    """把 A0 签名密钥重定向到 tmp，防止测试碰生产密钥。

    实测：`config.yaml` 的 `mock_agent.enabled` 为 false，因此本地跑测试走
    真实分支 —— `write_state` 会签名，`get_key()` 于是在真实
    `config/.evidence_key` 下**创建密钥**。删掉再跑全量会重新生成。

    两个后果都不能接受：测试污染生产配置；万一某用例写入了不同密钥，
    用户既有 `.state` 的签名会集体变成 tampered。

    session 作用域：密钥要在整个会话内保持一致，逐用例换密钥会让跨用例
    写入/校验的签名对不上。
    """
    from sw_lib.core import evidence as ev

    key_dir = tmp_path_factory.mktemp("evidence-key")
    original = ev.KEY_PATH
    ev.KEY_PATH = key_dir / ".evidence_key"
    ev.get_key.cache_clear()
    yield
    ev.KEY_PATH = original
    ev.get_key.cache_clear()


@pytest.fixture(autouse=True)
def _reset_web_engine_manager():
    """WebEngineManager is a process-wide singleton; clear its session registry
    between tests so create_session/get_session assertions don't see stale
    state left by a previous test."""
    yield
    mgr = WebEngineManager._instance
    if mgr is not None:
        mgr._sessions.clear()


@pytest.fixture
def dummy_task():
    """创建一个临时任务并在测试结束后清理"""
    name = "pytest-dummy-task"
    task_dir = TASKS / name
    task_dir.mkdir(parents=True, exist_ok=True)
    
    write_state(name, {
        "id": name,
        "stage": "01-brainstorming",
        "stage_idx": 0,
        "stage_status": "pending",
        "agent": "cat"
    })
    
    yield name
    
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    # 删目录不会自动清 STATUS.json 条目，漏了就留孤儿。
    remove_task_summary(name)
    # OpenCodeAgent 会为任务在 repo/<name> 下建工作目录，一并清理，
    # 否则 repo/ 里会长期堆积 pytest-dummy-task 之类的空目录。
    repo_dir = TASKS.parent.parent / "repo" / name
    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)


# ── Gate/Route 状态源辅助 ──
#
# 门禁判定读 .state 的 stages 字段（docs/design-json-state-source.md），
# 不再解析 Markdown。因此测试要表达「Gate 已签署」这个前提时，必须显式写
# JSON —— 在阶段文件里写 `- [x]` 是无效的，那正是本次设计要废除的语义。

@pytest.fixture
def make_task():
    """建任务目录 + .state，并返回一个 (name) -> None 的注册器。

    自动清理创建过的任务。用于需要真实 .state 的门禁测试。
    """
    created = []

    def _make(name, stage="01-brainstorming", stage_idx=0, **extra):
        d = TASKS / name
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": name, "stage": stage, "stage_idx": stage_idx,
            "stage_status": "running",
        }
        payload.update(extra)
        write_state(name, payload)
        created.append(name)
        return name

    yield _make

    for name in created:
        shutil.rmtree(TASKS / name, ignore_errors=True)
        remove_task_summary(name)


@pytest.fixture
def sign_gate():
    """签署指定阶段的 Gate（写 .state）。

    返回 (task, stage) -> bool。测试若只想验证「非门禁项不该拦路」，
    需要先用它把 Gate 签掉，否则待办里必然留着未签署的门禁项。
    """
    from sw_lib.workflow import stage_state as ss

    def _sign(task, stage, by="user"):
        return ss.sign_gate(task, stage, by=by)

    return _sign

@pytest.fixture
def agent_callbacks():
    """提供 Agent 基础回调"""
    return {
        "add_log": lambda s, m: None,
        "is_running": lambda: True
    }

@pytest.fixture
def workflow_state():
    """提供一个初始化的 WorkflowState 字典 (用于 LangGraph 测试)"""
    return {
        "task_name": "test-task",
        "current_stage": "01-brainstorming",
        "stage_idx": 0,
        "history_outputs": [],
        "last_output": None,
        "next_route": None,
        "reroute_count": 0,
        "gate_passed": False
    }
