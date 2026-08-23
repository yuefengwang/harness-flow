"""测试残留回收：把测试造出来的任务产物从真实 workspace 里收干净。

一个任务有三处产物，少清任何一处都留孤儿：

    workspace/tasks/<name>        任务目录（或 .trash/<name>）
    repo/<name>                   agent 的代码工作目录
    workspace/STATUS.json         全局汇总里的一条

为什么按**名字模式**判定，而不是按「本次会话新增」的差集：
差集策略（`conftest` 的前身实现）有个致命漏洞 —— 残留只要活过一次会话，
就会进入下次的 before 快照，从此被永久豁免。实测预置 `leak-probe` 的三处
产物后跑全量测试，三处全部原样留存。仓库里那 13 条 `e2e-*` / `pytest-*-probe`
就是这么积起来的。

名字模式是可靠的判据，因为测试任务名全部由测试自己生成，前缀固定：

    e2e-<pid>           tests/e2e-flow/driver.py
    e2e-<timestamp>     sw_lib/cli/test_cmd.py
    web-*               tests/unit/web/test_tasks_api.py
    pytest-*            tests/conftest.py 及各 unit 用例
    test-*              test-app / test-deploy-force / test-orch-svc ...
    rw-*                tests/unit/workflow/test_red_witness_*.py（31 个名字）

另有两个不带前缀的具名残留（`my-feature-task`、`no-such-task-xyz`），
靠精确匹配兜住 —— 它们是 sanitize 与「任务不存在」用例里写死的名字。

这份清单由实测得来：置空 STATUS.json、`SW_SKIP_REAP=1` 跑全量，
把泄漏出来的名字逐个回溯到源码位置，而不是凭印象猜前缀。

**豁免优先于清理**：误删用户的真实任务是不可接受的，漏清一个残留只是脏。
因此规则要求完整前缀加分隔符 —— `webhook-service` 和 `e2ex` 都不匹配。
"""
import json
import shutil
from pathlib import Path
from typing import List

from .config import TASKS, TRASH, STATUS

# 需要带分隔符匹配的前缀族。分隔符是关键：它让 `e2ex`、`webhook-*`、
# `testing-framework`、`rwanda-project` 这类真实名字不被误伤。
_RESIDUE_PREFIXES = ("e2e", "web", "pytest", "test", "rw")

# 不带可辨识前缀、只能精确匹配的名字。刻意保持极短：每一条都是一次
# 实测追溯的结果，模糊化会开始威胁用户的真实任务。
_RESIDUE_EXACT = frozenset({
    "test-app", "test-task",
    "my-feature-task",      # tests/unit/web/test_tasks_api.py: sanitize 用例
    "no-such-task-xyz",     # tests/unit/workflow, tests/unit/cli: 「不存在」用例
    "test",                 # tests/integration/test_opencode_http.py 的任务名
})


def _repo_root() -> Path:
    """agent 代码产物的根目录。

    做成函数而非模块常量，是为了让测试能 monkeypatch 掉它 ——
    这个模块的用例本身就在测删除，绝不能拿真实 repo/ 当靶子。
    """
    from .config import ROOT, get_repo_path

    repo = Path(get_repo_path())
    return repo if repo.is_absolute() else ROOT / repo


def is_test_residue(name: str) -> bool:
    """判定一个任务名是否由测试造出。

    保守优先：拿不准就返回 False，宁可留脏也不误删用户的任务。
    """
    if not name or name.startswith("."):
        return False
    if name in _RESIDUE_EXACT:
        return True
    for prefix in _RESIDUE_PREFIXES:
        # 必须是 "<prefix>-" 开头：`web-engine-test` 命中，`webhook-service` 不命中。
        if name.startswith(prefix + "-") and len(name) > len(prefix) + 1:
            return True
    return False


def _status_entries() -> List[str]:
    if not STATUS.exists():
        return []
    try:
        data = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(data.get("tasks", {}))


def _drop_status_entries(names) -> None:
    """从 STATUS.json 批量摘掉条目。

    一次读写完成，避免逐条 remove_task_summary 反复重写整个文件。
    """
    names = set(names)
    if not names or not STATUS.exists():
        return
    try:
        data = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    tasks = data.get("tasks", {})
    if not any(n in tasks for n in names):
        return
    for n in names:
        tasks.pop(n, None)
    try:
        STATUS.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def reap() -> List[str]:
    """清掉所有测试残留，返回被清理的任务名（已排序）。

    三处产物一起收；只剩 STATUS 条目的孤儿也算（现存 13 条正是这个形态）。
    """
    repo = _repo_root()
    victims = set()

    for base in (TASKS, TRASH, repo):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_dir() or not is_test_residue(entry.name):
                continue
            victims.add(entry.name)
            shutil.rmtree(entry, ignore_errors=True)

    orphans = {n for n in _status_entries() if is_test_residue(n)}
    victims |= orphans
    _drop_status_entries(victims)

    return sorted(victims)
