"""测试残留回收：三处产物必须一起清，且不依赖「本次新增」差集。

背景（实测）：一个任务有三处产物 —— `workspace/tasks/<name>`、
`repo/<name>`、`STATUS.json` 条目。过去清理散落在二十多个用例里各自
`rmtree`，删目录和摘 STATUS 条目是两个必须手工配对的动作，漏一半就留孤儿；
仓库里已经积到 13 条（`e2e-*` / `SMOKECHK` / `pytest-a1?-probe*`）。

`conftest._reap_workspace_residue` 曾用「跑前拍快照，跑后删新增」的差集兜底。
该策略有个致命漏洞：残留只要活过一次会话，就会进入下次的 before 快照，
从此被永久豁免 —— 实测预置 `leak-probe` 三处产物后跑全量，三处全部原样留存。

因此判定必须基于**名字模式**（测试自己造的名字有固定前缀），而不是差集。
反向要求同样重要：用户的真实任务（如 `ttt`）绝不能被误删。
"""
import json

import pytest

from sw_lib.core import residue


# ── 识别：哪些名字属于测试残留 ──

@pytest.mark.parametrize("name", [
    "e2e-83637",            # tests/e2e-flow/driver.py: f"e2e-{os.getpid()}"
    "e2e-1755900000",       # sw_lib/cli/test_cmd.py: f"e2e-{int(time.time())}"
    "web-engine-test",      # tests/unit/web/test_tasks_api.py
    "web-created-002",
    "pytest-dummy-task",    # tests/conftest.py
    "pytest-a12-probe",     # 手工实测遗留
    "test-app",             # sw_lib/cli/test_cmd.py 的 target_dir
    # 以下由「关掉兜底扫帚跑全量」实测曝光（SW_SKIP_REAP=1）
    "test-deploy-force",    # tests/unit/core/test_deploy.py
    "test-orch-svc",        # tests/unit/core/test_deploy_orchestrator.py
    "my-feature-task",      # tests/unit/web/test_tasks_api.py: sanitize 测试
    "no-such-task-xyz",     # tests/unit/workflow/test_stage_state.py
    "test",                 # tests/integration/test_opencode_http.py 的任务名
    "rw-gate-green",        # tests/unit/workflow/test_red_witness_*.py（A2，31 个 rw-* 名字）
    "rw-phase-idem",
])
def test_recognizes_test_owned_names(name):
    assert residue.is_test_residue(name), f"{name} 应被识别为测试残留"


@pytest.mark.parametrize("name", [
    "ttt",                  # 用户的真实任务，必须豁免
    "webhook-service",      # 前缀相近但不是 web-
    "e2ex",                 # 无分隔符，不是 e2e- 家族
    "latest-pytest",        # pytest 不在开头
    "testing-framework",    # test 后面不是分隔符
    "my-real-feature",
    "rwanda-project",       # rw 后面不是分隔符
    "",
    ".trash",
    ".DS_Store",
])
def test_spares_real_task_names(name):
    assert not residue.is_test_residue(name), f"{name} 是真实任务名，不该被当成残留"


# ── 清理：三处产物一起收 ──

@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    """把 tasks / trash / repo / STATUS 全部改指到 tmp_path。

    这个用例本身就是在测「清理」，绝不能拿真实 workspace 当靶子。
    """
    tasks = tmp_path / "workspace" / "tasks"
    trash = tasks / ".trash"
    repo = tmp_path / "repo"
    status = tmp_path / "workspace" / "STATUS.json"
    for d in (tasks, trash, repo):
        d.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps({"tasks": {}}), encoding="utf-8")

    monkeypatch.setattr(residue, "TASKS", tasks)
    monkeypatch.setattr(residue, "TRASH", trash)
    monkeypatch.setattr(residue, "STATUS", status)
    monkeypatch.setattr(residue, "_repo_root", lambda: repo)
    return tasks, trash, repo, status


def _seed(tasks, trash, repo, status, name, *, in_trash=False):
    """造出一个任务的三处产物。"""
    (trash if in_trash else tasks).joinpath(name).mkdir(parents=True, exist_ok=True)
    (repo / name).mkdir(parents=True, exist_ok=True)
    data = json.loads(status.read_text(encoding="utf-8"))
    data["tasks"][name] = {"id": name, "stage": "01-brainstorming"}
    status.write_text(json.dumps(data), encoding="utf-8")


def _status_names(status):
    return set(json.loads(status.read_text(encoding="utf-8"))["tasks"])


def test_reap_removes_all_three_artifacts(fake_workspace):
    """任务目录、repo 目录、STATUS 条目 —— 少清任何一处都算漏。"""
    tasks, trash, repo, status = fake_workspace
    _seed(tasks, trash, repo, status, "e2e-83637")

    reaped = residue.reap()

    assert not (tasks / "e2e-83637").exists(), "任务目录未清"
    assert not (repo / "e2e-83637").exists(), "repo 目录未清"
    assert "e2e-83637" not in _status_names(status), "STATUS.json 条目未清"
    assert "e2e-83637" in reaped, "应报告清掉了什么"


def test_reap_ignores_preexisting_residue_is_wrong(fake_workspace):
    """核心回归：不依赖「本次新增」差集。

    残留在 reap() 被调用前就已存在（模拟活过上一次会话），仍必须被清掉。
    """
    tasks, trash, repo, status = fake_workspace
    for name in ("e2e-1", "web-smoke-test", "pytest-a12-probe"):
        _seed(tasks, trash, repo, status, name)

    residue.reap()

    assert list(tasks.iterdir()) == [trash], f"仍有残留: {list(tasks.iterdir())}"
    assert list(repo.iterdir()) == [], f"repo 仍有残留: {list(repo.iterdir())}"
    assert _status_names(status) == set()


def test_reap_spares_real_tasks(fake_workspace):
    """用户的真实任务必须原封不动 —— 这比清得干净重要得多。"""
    tasks, trash, repo, status = fake_workspace
    _seed(tasks, trash, repo, status, "ttt")
    _seed(tasks, trash, repo, status, "e2e-99")

    reaped = residue.reap()

    assert (tasks / "ttt").is_dir(), "真实任务目录被误删"
    assert (repo / "ttt").is_dir(), "真实任务的 repo 目录被误删"
    assert "ttt" in _status_names(status), "真实任务的 STATUS 条目被误删"
    assert reaped == ["e2e-99"]


def test_reap_cleans_trash_copies(fake_workspace):
    """回收站里的测试残留同样要收，否则 remove 过的测试任务永久留存。"""
    tasks, trash, repo, status = fake_workspace
    _seed(tasks, trash, repo, status, "web-test-task", in_trash=True)

    residue.reap()

    assert not (trash / "web-test-task").exists(), "回收站残留未清"


def test_reap_clears_orphan_status_entry_without_dirs(fake_workspace):
    """孤儿条目：目录早被 rmtree 掉、只剩 STATUS 条目。这正是现存 13 条的形态。"""
    tasks, trash, repo, status = fake_workspace
    data = json.loads(status.read_text(encoding="utf-8"))
    data["tasks"]["e2e-85111"] = {"id": "e2e-85111"}
    status.write_text(json.dumps(data), encoding="utf-8")

    residue.reap()

    assert _status_names(status) == set(), "孤儿 STATUS 条目未清"


def test_reap_is_idempotent(fake_workspace):
    """空 workspace 上重复调用不该报错，也不该报告清了东西。"""
    tasks, trash, repo, status = fake_workspace
    assert residue.reap() == []
    assert residue.reap() == []


# ── 接线：兜底扫帚必须真的挂上 ──

def test_conftest_uses_reaper_not_snapshot_diff():
    """conftest 的 session 级兜底必须调 residue.reap()。

    差集实现（`after - before`）必须消失：它让活过一次会话的残留永久豁免。
    """
    from sw_lib.core.config import ROOT

    body = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "residue.reap()" in body or "reap()" in body, (
        "conftest 未调用 residue.reap()，测试残留将无人回收")
    assert 'after["status"] - before["status"]' not in body, (
        "conftest 仍在用「本次新增」差集，预先存在的残留会被永久豁免")


def test_e2e_driver_uses_shared_reaper():
    """e2e driver 失败时保留现场，但下次启动前必须先收上一轮的残留。

    它是独立脚本（不经 pytest conftest），过去自己抄了一份 `_drop_status_entry`，
    而失败路径直接 return，`repo/e2e-<pid>` 就永久留在仓库里 —— 用户是手工清的。
    """
    from sw_lib.core.config import ROOT

    body = (ROOT / "tests" / "e2e-flow" / "driver.py").read_text(encoding="utf-8")
    assert "residue" in body, "driver 应复用 sw_lib.core.residue，不要各自抄清理逻辑"
    assert "reap()" in body, "driver 未调用 reap()，失败留下的现场无人回收"


# ── 手动入口：被 Ctrl+C 打断时没有任何收尾会执行 ──

def test_reap_command_is_registered():
    """`sw reap` 必须可用。

    自动收尾覆盖不到一种情况：运行被 Ctrl+C / kill 打断时，conftest 的
    session fixture 和 driver 的 finally 都不会执行完。这时需要一个手动入口。
    """
    from sw_lib.cli.commands import cmd_reap
    from sw_lib.core.config import ROOT

    assert callable(cmd_reap)
    main_body = (ROOT / "sw_lib" / "cli" / "main.py").read_text(encoding="utf-8")
    assert "cmd_reap" in main_body, "cmd_reap 未接入 main.py 的分派"
    assert '"reap"' in main_body, "reap 子命令未注册到 argparse"


def test_reap_command_reports_when_nothing_to_clean(capsys, fake_workspace):
    """空 workspace 上 `sw reap` 不该报错。"""
    import sw_lib.cli.commands as cmds

    cmds.cmd_reap(object())
    out = capsys.readouterr().out
    assert out.strip(), "应给出反馈而不是静默退出"


# ── 半隔离：把 TASKS 指向 tmp_path 却漏掉 STATUS ──

def test_substage_routing_suite_does_not_touch_real_status(tmp_path, monkeypatch):
    """行为断言：驱动 advance 成功时，STATUS 条目不得落到真实文件。

    实测确认的漏网形态（全仓唯一一个）：
    `test_red_witness_substage_routing.py` 把 `state_mod.TASKS` 改到 tmp_path，
    任务目录确实进了临时目录，但 `STATUS` 仍指向真实的
    `workspace/STATUS.json` —— `WorkflowRuntime.advance` 内部会
    `upsert_task_summary`（runtime.py:184），于是 `t-substage` / `t-other`
    被写进真实文件，tmp_path 一销毁就成了永久孤儿。

    这类残留连名字模式都兜不住（`t-` 是任意前缀），所以必须从源头堵。

    为什么用行为断言而不是扫源码：只按「文件里出现 advance」判定会误报 ——
    `test_state_integrity.py` 的 advance 调用故意撞异常，从没走到
    `upsert_task_summary`，实测为 clean。判据必须是「真的写没写」。
    """
    from sw_lib.core import state as state_mod
    from sw_lib.core.config import STAGES
    from sw_lib.workflow import stage_state as ss
    from sw_lib.workflow.runtime import WorkflowRuntime

    real_status = state_mod.STATUS
    fake_status = tmp_path / "STATUS.json"
    monkeypatch.setattr(state_mod, "TASKS", tmp_path)
    monkeypatch.setattr(ss, "TASKS", tmp_path)
    monkeypatch.setattr(state_mod, "STATUS", fake_status)

    before = real_status.read_text(encoding="utf-8") if real_status.exists() else None

    name = "t-status-isolation"
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    state_mod.write_state(name, {
        "id": name, "stage": "02-planning",
        "stage_idx": STAGES.index("02-planning"), "stage_status": "running",
    })
    WorkflowRuntime.advance(name)

    after = real_status.read_text(encoding="utf-8") if real_status.exists() else None
    assert after == before, "advance 写进了真实 STATUS.json —— STATUS 未被隔离"
    assert fake_status.exists(), "隔离后的 STATUS 应承接写入，否则这条断言是假绿"
    assert name in fake_status.read_text(encoding="utf-8")
