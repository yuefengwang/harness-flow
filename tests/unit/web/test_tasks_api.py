"""
测试任务管理 API：创建、列表、推进与引擎集成。
覆盖 Web Dashboard 中 "新建任务后在活跃任务中不可见" 和 "engine 不推进" 的回归场景。
"""
import json
import shutil
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from sw_lib.web.app import create_app
from sw_lib.web.engine_manager import WebEngineManager
from sw_lib.core.config import TASKS, STAGES, STAGE_NAMES, get_repo_path
from sw_lib.core.state import read_state, write_state
from sw_lib.core.service import _service


def _rm_repo(name: str):
    """清理 repo 目录下的测试残留 (safe — ignores if not found)"""
    p = TASKS.parent.parent / "repo" / name
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


TEST_TASK = "web-test-task"
TEST_TASK_2 = "web-test-task-advance"


# ── Fixtures ──

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def clean_test_task():
    """创建测试任务并在结束后清理"""
    task_dir = TASKS / TEST_TASK
    trash_dir = TASKS / ".trash" / TEST_TASK
    # 清理可能的残留
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    if trash_dir.exists():
        shutil.rmtree(trash_dir, ignore_errors=True)

    _service.create_task(TEST_TASK, task_type="feature", context="test context")
    yield TEST_TASK

    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    if trash_dir.exists():
        shutil.rmtree(trash_dir, ignore_errors=True)


@pytest.fixture
def clean_engine_mgr():
    mgr = WebEngineManager()
    yield mgr
    for name in list(mgr._sessions.keys()):
        mgr.destroy_session(name)


# ── Dashboard 页面 ──

def test_dashboard_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "任务看板" in resp.text
    assert 'id="task-list"' in resp.text


def test_dashboard_page_has_create_form(client):
    resp = client.get("/")
    assert "创建新任务" in resp.text
    assert 'hx-post="/tasks/create"' in resp.text


# ── Task Table Partial ──

def test_task_table_partial_returns_html(client):
    resp = client.get("/tasks/table")
    assert resp.status_code == 200
    assert 'id="task-list"' in resp.text


def test_task_table_partial_shows_existing_tasks(client, clean_test_task):
    resp = client.get("/tasks/table")
    assert TEST_TASK in resp.text


# ── Task Create (核心 bug 场景) ──

def test_create_task_via_web_form(client):
    """新建任务后应立即出现在活跃任务列表中，且响应为 HTML"""
    task_name = "web-created-001"
    task_dir = TASKS / task_name
    trash_dir = TASKS / ".trash" / task_name

    # 清理残留
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    if trash_dir.exists():
        shutil.rmtree(trash_dir, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-001").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-001", ignore_errors=True)

    try:
        resp = client.post("/tasks/create", data={
            "name": task_name,
            "task_type": "feature",
            "context": "web test",
        })
        assert resp.status_code == 200, f"创建失败: {resp.text}"
        assert "text/html" in resp.headers.get("content-type", ""), (
            f"创建响应应为 text/html, 实际: {resp.headers.get('content-type')}"
        )
        assert task_name in resp.text, (
            f"任务 {task_name} 未出现在返回的 HTML 中"
        )

        # 验证文件系统上确实存在
        assert task_dir.is_dir(), f"任务目录未创建: {task_dir}"
        state = read_state(task_name)
        assert state, f".state 文件未创建: {task_name}"
        assert state.get("stage_status") == "pending"
        assert state.get("stage") == "01-brainstorming"

        # 验证通过 /tasks/table 端点也能看到
        resp2 = client.get("/tasks/table")
        assert resp2.status_code == 200
        assert task_name in resp2.text, (
            f"任务 {task_name} 未出现在 /tasks/table 返回的 HTML 中"
        )

    finally:
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
            (TASKS.parent.parent / get_repo_path() / task_name).exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / task_name, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-001").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-001", ignore_errors=True)


def test_create_task_shows_in_list_immediately(client):
    """创建后立即 get /tasks/table 应包含新任务"""
    task_name = "web-created-002"
    task_dir = TASKS / task_name
    trash_dir = TASKS / ".trash" / task_name
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    if trash_dir.exists():
        shutil.rmtree(trash_dir, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-002").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-002", ignore_errors=True)

    try:
        # 创建
        client.post("/tasks/create", data={
            "name": task_name,
            "task_type": "feature",
        })

        # 立即列表
        resp = client.get("/tasks/table")
        assert task_name in resp.text, f"新建的任务 {task_name} 在列表中不可见"

    finally:
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
            (TASKS.parent.parent / get_repo_path() / task_name).exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / task_name, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-002").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-002", ignore_errors=True)


def test_create_task_with_whitespace_name(client):
    """名称含空格应被 sanitize_name 清理"""
    task_name = "my feature task"
    clean_name = "my-feature-task"
    task_dir = TASKS / clean_name
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)

    try:
        resp = client.post("/tasks/create", data={
            "name": task_name,
            "task_type": "feature",
        })
        assert resp.status_code == 200
        assert clean_name in resp.text
    finally:
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


def test_create_duplicate_task_rejected(client, clean_test_task):
    """创建同名任务应返回错误"""
    resp = client.post("/tasks/create", data={
        "name": TEST_TASK,
        "task_type": "feature",
    })
    assert resp.status_code == 400
    assert "已有" in resp.text or "存在" in resp.text


# ── Task Advance ──

class TestTaskAdvance:
    """任务推进相关测试"""

    @pytest.fixture(autouse=True)
    def setup_task(self):
        name = "web-advance-test"
        task_dir = TASKS / name
        trash_dir = TASKS / ".trash" / name
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
            (TASKS.parent.parent / get_repo_path() / task_name).exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / task_name, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-002").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-002", ignore_errors=True)

        _service.create_task(name, task_type="feature")
        yield name

        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-002").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-002", ignore_errors=True)

    def test_advance_blocks_on_unfilled_template(self, setup_task, client):
        """未填充模板时推进应被拒绝"""
        name = setup_task
        resp = client.post(f"/tasks/{name}/advance")
        assert resp.status_code == 400
        assert "未完成" in resp.text

    def test_advance_after_filling_gate(self, setup_task, client):
        """填充模板 Gate 后推进应成功"""
        name = setup_task
        task_dir = TASKS / name

        # 模拟 Agent 完成了头脑风暴并填充了模板
        tpl_file = task_dir / "01-brainstorming.md"
        tpl_file.write_text(
            "## 歧义分析结果\n- **理解准确度**: 高\n\n"
            "## Gate\n- [x] Design approved\n- [x] Ready for Planning\n",
            encoding="utf-8"
        )

        resp = client.post(f"/tasks/{name}/advance")
        assert resp.status_code == 200, f"推进失败: {resp.text}"
        assert "规划" in resp.text or "planning" in resp.text.lower()

        # 验证状态已更新
        state = read_state(name)
        assert state.get("stage") == "02-planning"
        assert state.get("stage_status") == "pending"


# ── Engine Integration (engine 不推进场景) ──

class TestEngineIntegration:
    """Engine 集成测试"""

    @pytest.fixture(autouse=True)
    def setup_task(self):
        name = "web-engine-test"
        task_dir = TASKS / name
        trash_dir = TASKS / ".trash" / name
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-002").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-002", ignore_errors=True)

        _service.create_task(name, task_type="feature", context="test engine")
        yield name

        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
        (TASKS.parent.parent / get_repo_path() / "web-created-002").exists() and shutil.rmtree(TASKS.parent.parent / get_repo_path() / "web-created-002", ignore_errors=True)

    def test_engine_start_creates_session(self, setup_task, client, clean_engine_mgr):
        name = setup_task
        resp = client.post(f"/tasks/{name}/engine/start")
        assert resp.status_code == 200
        session = clean_engine_mgr.get_session(name)
        assert session is not None, "Engine 启动后应创建 session"
        assert session.is_alive

    def test_engine_start_on_nonexistent_task_returns_404(self, client):
        resp = client.post("/tasks/nonexistent-xxx/engine/start")
        assert resp.status_code == 404

    def test_engine_start_sets_correct_stage(self, setup_task, client, clean_engine_mgr):
        name = setup_task
        resp = client.post(f"/tasks/{name}/engine/start")
        assert resp.status_code == 200
        session = clean_engine_mgr.get_session(name)
        assert session.engine.stage == "01-brainstorming"
        assert session.engine.stage_idx == 0

    def test_engine_start_respects_advance(self, setup_task, client, clean_engine_mgr):
        """推进后 engine 应在正确阶段启动"""
        name = setup_task
        task_dir = TASKS / name

        # 先推进到下一阶段
        tpl_file = task_dir / "01-brainstorming.md"
        tpl_file.write_text(
            "## Gate\n- [x] Design approved\n- [x] Ready for Planning\n",
            encoding="utf-8"
        )
        client.post(f"/tasks/{name}/advance")

        # 再启动 engine
        resp = client.post(f"/tasks/{name}/engine/start")
        assert resp.status_code == 200
        session = clean_engine_mgr.get_session(name)
        assert session.engine.stage == "02-planning"
        assert session.engine.stage_idx == 1


# ── Task Detail ──

def test_task_detail_page_shows_info(client, clean_test_task):
    resp = client.get(f"/tasks/{TEST_TASK}")
    assert resp.status_code == 200
    assert TEST_TASK in resp.text
    assert "01-brainstorming" in resp.text


def test_task_detail_nonexistent_returns_404(client):
    resp = client.get("/tasks/zzz-nonexistent")
    assert resp.status_code == 404


# ── Task Remove / Restore ──

def test_remove_task_via_web(client, clean_test_task):
    resp = client.post(f"/tasks/{TEST_TASK}/remove")
    assert resp.status_code == 200
    # 确认响应是 HTML (不是 JSON)
    ct = resp.headers.get("content-type", "")
    assert "text/html" in ct, f"删除响应应为 text/html, 实际: {ct}"
    # 确认 HTML 可在浏览器中渲染 (不以 " 开头, 即非 JSON 字符串)
    assert not resp.text.strip().startswith('"'), "响应不应为 JSON 字符串"

    # 移除后任务应出现在回收站中
    assert "回收站" in resp.text


def test_restore_task_via_web(client, clean_test_task):
    resp1 = client.post(f"/tasks/{TEST_TASK}/remove")
    assert resp1.status_code == 200
    ct = resp1.headers.get("content-type", "")
    assert "text/html" in ct, f"删除响应应为 text/html, 实际: {ct}"

    resp2 = client.post(f"/tasks/{TEST_TASK}/restore")
    assert resp2.status_code == 200
    ct2 = resp2.headers.get("content-type", "")
    assert "text/html" in ct2, f"恢复响应应为 text/html, 实际: {ct2}"

    # 恢复后任务应在活跃列表中
    assert TEST_TASK in resp2.text


# ── 全生命周期冒烟测试 ──

def test_full_lifecycle_smoke(client, clean_engine_mgr):
    """创建 → 可见 → 推进 → engine → 完成 → 归档 的全流程"""
    name = "web-smoke-test"
    task_dir = TASKS / name
    trash_dir = TASKS / ".trash" / name

    # 清理
    for d in [task_dir, trash_dir]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    try:
        # 1. 创建
        r = client.post("/tasks/create", data={
            "name": name,
            "task_type": "feature",
            "context": "smoke test",
        })
        assert r.status_code == 200, f"创建失败: {r.text}"
        assert name in r.text, "创建后任务应在列表中可见"

        # 2. 确认文件系统状态
        assert task_dir.is_dir()
        state = read_state(name)
        assert state["stage"] == "01-brainstorming"
        assert state["stage_status"] == "pending"

        # 3. 填充模板并推进 (重复推进所有阶段)
        current_stage_idx = 0
        while current_stage_idx < len(STAGES) - 1:
            stage = STAGES[current_stage_idx]
            tpl_file = task_dir / f"{stage}.md"

            # 为每个阶段填充足够的 Gate 勾选
            if "brainstorming" in stage:
                tpl_file.write_text(
                    "## 歧义分析\n- 测试填写的分析内容\n\n"
                    "## Gate\n- [x] Design approved\n- [x] Ready for Planning\n",
                    encoding="utf-8"
                )
            elif "planning" in stage:
                tpl_file.write_text(
                    "## Task Breakdown\n- Task 1: test\n\n"
                    "## Gate\n- [x] Plan reviewed\n- [x] Ready for Coding\n",
                    encoding="utf-8"
                )
            elif "coding" in stage:
                tpl_file.write_text(
                    "## Implementation\n- [x] Code written\n\n"
                    "## Gate\n- [x] Tests pass\n- [x] Ready for Review\n",
                    encoding="utf-8"
                )
            elif "review" in stage:
                tpl_file.write_text(
                    "## Review Decision\n- **Route**: `05-Archive`\n- **Reason**: All criteria met\n\n"
                    "## Review Summary\n- [x] Code reviewed\n\n"
                    "## Gate\n- [x] Review passed\n- [x] Ready for Archive\n",
                    encoding="utf-8"
                )

            r = client.post(f"/tasks/{name}/advance")
            assert r.status_code == 200, f"推进阶段 {stage} 失败: {r.text}"
            current_stage_idx += 1

        # 4. 所有阶段已推进完毕
        state = read_state(name)
        assert state["stage"] == "05-archive", f"应到达归档阶段，实际: {state['stage']}"
        assert state["stage_status"] == "pending"

        # 5. 归档推进 → 标记 Finished
        archive_tpl = task_dir / "05-archive.md"
        archive_tpl.write_text(
            "## Summary\n- [x] Project archived\n\n"
            "## Gate\n- [x] Archive complete\n",
            encoding="utf-8"
        )
        r = client.post(f"/tasks/{name}/advance")
        state = read_state(name)
        assert state.get("stage_status") == "Finished", (
            f"归档后 stage_status 应为 Finished，实际: {state.get('stage_status')}"
        )

    finally:
        for d in [task_dir, trash_dir]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)


# ── Deploy Tests ──

class TestDeploy:
    """部署功能测试"""

    @pytest.fixture(autouse=True)
    def setup_finished_task(self):
        name = "web-deploy-test"
        task_dir = TASKS / name
        trash_dir = TASKS / ".trash" / name
        target = TASKS.parent / "repo" / name
        for d in [task_dir, trash_dir, target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        _service.create_task(name, task_type="feature", target_dir=str(target))
        st = read_state(name)
        st["stage_status"] = "Finished"
        write_state(name, st)
        from sw_lib.core.state import upsert_task_summary
        upsert_task_summary(name, stage_status="Finished")
        yield name
        for d in [task_dir, trash_dir, target]:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def test_deploy_on_finished_task(self, setup_finished_task, client):
        name = setup_finished_task
        resp = client.post(f"/tasks/{name}/deploy")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/html" in ct
        # deploy_status may be "deploying" or "deployed" (agent may finish quickly in tests)
        st = read_state(name)
        assert st["deploy_status"] in ("deploying", "deployed", "deploy_failed")

    def test_deploy_on_unfinished_task(self, client):
        name = "web-deploy-pending"
        task_dir = TASKS / name
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        try:
            _service.create_task(name, task_type="feature")
            resp = client.post(f"/tasks/{name}/deploy")
            assert resp.status_code == 400
            assert "未完成" in resp.text or "无法" in resp.text
        finally:
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    def test_deploy_already_deploying(self, setup_finished_task, client):
        name = setup_finished_task
        st = read_state(name)
        st["deploy_status"] = "deploying"
        write_state(name, st)
        resp = client.post(f"/tasks/{name}/deploy")
        assert resp.status_code == 400
        assert "正在" in resp.text or "进行中" in resp.text

    def test_deploy_missing_target_dir(self, setup_finished_task, client):
        name = setup_finished_task
        st = read_state(name)
        st["target_dir"] = "/nonexistent/path999"
        write_state(name, st)
        resp = client.post(f"/tasks/{name}/deploy")
        assert resp.status_code == 400

    def test_deploy_button_visible_for_finished(self, setup_finished_task, client):
        name = setup_finished_task
        resp = client.get("/tasks/table")
        assert f'hx-post="/tasks/{name}/deploy"' in resp.text

    def test_deploy_button_hidden_for_pending(self, client):
        name = "web-deploy-visible-test"
        task_dir = TASKS / name
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        try:
            _service.create_task(name, task_type="feature")
            resp = client.get("/tasks/table")
            assert f'hx-post="/tasks/{name}/deploy"' not in resp.text
            assert "对话" in resp.text
        finally:
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)
