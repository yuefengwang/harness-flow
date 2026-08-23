"""空转与缺文档必须被闸门拦住（任务 T3）。

T3 的 03-coding：agent 直接 "no text/tool in response"，repo/T3 下只有
create_task 写的 .sw-context。当时的钩子看到没有 pyproject.toml / test_*.py
就整段跳过测试，打印「✅ 通过」，用户签了 Gate 就推到 04-review —— 空转的
代价被推迟到评审才暴露，然后开始 03↔04 来回返工。

T3 的 04-review：缺 README.md 只算 ⚠️ 警告，钩子照样通过。用户只能靠一次次
选返工来表达「这里还缺文档」，而返工上下文又不带真实理由，agent 三轮都没补。
hook-04-06 自己写的规则是「README.md 存在且非空」—— 实现和规则不一致。
"""
import json
import shutil
import subprocess
from pathlib import Path

from sw_lib.core.config import HOOKS_DIR, TASKS
from sw_lib.workflow import stage_state as ss

ROOT = Path(__file__).resolve().parents[3]


def _make_task(name, target_dir, stage):
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({
        "id": name, "stage": stage, "stage_idx": 2, "stage_status": "running",
        "target_dir": str(target_dir),
    }), encoding="utf-8")
    return d


def _run_hook(hook_name, task_name):
    return subprocess.run(
        ["bash", str(HOOKS_DIR / hook_name), task_name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def _project(dir_path, with_readme=True):
    """最小可跑通的项目：一个模块 + 一个测试（+ README）。"""
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (dir_path / "test_mod.py").write_text(
        "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8")
    if with_readme:
        (dir_path / "README.md").write_text("# demo\n\n用法说明。\n", encoding="utf-8")
    return dir_path


def _review_md(task_dir):
    """喂饱 README 检查之前的所有前置校验，否则测试会提前退出成假绿。

    标题必须是 `### Reroute Evidence`，表格必须是完整 5 列 —— 钩子按这两个
    形状取数据行。
    """
    (task_dir / "04-review.md").write_text(
        "# 04-Review\n\n## Security\n- 无敏感数据\n\n"
        "### Reroute Evidence\n"
        "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
        "|---|------|---------|---------|-------------|\n"
        "| 1 | 缺少 README.md | high | coding | repo 根目录 |\n",
        encoding="utf-8")


# ── 03-coding: 空产出 ──

def test_empty_target_dir_blocks_coding(tmp_path):
    """目录里只有 .sw-context —— 正是 T3 的现场，必须拦住。"""
    target = tmp_path / "repo-empty"
    target.mkdir()
    (target / ".sw-context").write_text("{}", encoding="utf-8")
    task_dir = _make_task("pytest-hook-empty", target, "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", "pytest-hook-empty")
        assert r.returncode != 0, f"空转竟然过闸了:\n{r.stdout}"
        assert "没有任何代码产出" in r.stdout, r.stdout
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_totally_empty_dir_blocks_coding(tmp_path):
    target = tmp_path / "repo-void"
    target.mkdir()
    task_dir = _make_task("pytest-hook-void", target, "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", "pytest-hook-void")
        assert r.returncode != 0, f"完全空目录过闸了:\n{r.stdout}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_only_noise_dirs_block_coding(tmp_path):
    """.venv / __pycache__ / egg-info 都不是产出 —— 装个依赖不等于写了代码。"""
    target = tmp_path / "repo-noise"
    (target / ".venv" / "lib").mkdir(parents=True)
    (target / "__pycache__").mkdir()
    (target / "demo.egg-info").mkdir()
    (target / ".git").mkdir()
    (target / ".sw-context").write_text("{}", encoding="utf-8")
    task_dir = _make_task("pytest-hook-noise", target, "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", "pytest-hook-noise")
        assert r.returncode != 0, f"只有环境残留却过闸了:\n{r.stdout}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_real_output_passes_coding(tmp_path):
    """有真实产出且测试通过 —— 不能因为加了空转拦截就把正路堵死。"""
    target = _project(tmp_path / "repo-ok")
    task_dir = _make_task("pytest-hook-ok", target, "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", "pytest-hook-ok")
        assert r.returncode == 0, f"正常产出被误拦:\n{r.stdout}\n{r.stderr}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_missing_target_dir_still_skips(tmp_path):
    """目录根本不存在时保持原有的跳过行为（例如 target_dir 尚未建立）。"""
    task_dir = _make_task("pytest-hook-nodir", tmp_path / "nope", "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", "pytest-hook-nodir")
        assert r.returncode == 0, r.stdout
        assert "目标目录不存在" in r.stdout, r.stdout
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_only_hidden_dotfile_is_not_output(tmp_path):
    """隐藏文件里只有 harness 自己的记账文件时不算产出。"""
    target = tmp_path / "repo-dot"
    target.mkdir()
    (target / ".sw-context").write_text("{}", encoding="utf-8")
    (target / ".DS_Store").write_text("", encoding="utf-8")
    task_dir = _make_task("pytest-hook-dot", target, "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", "pytest-hook-dot")
        assert r.returncode != 0, r.stdout
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_hidden_source_file_counts_as_output(tmp_path):
    """`.env`、`.github/` 之类隐藏产出是真产出，不能被一并忽略。"""
    target = tmp_path / "repo-hidden"
    target.mkdir()
    (target / ".sw-context").write_text("{}", encoding="utf-8")
    (target / ".env.example").write_text("KEY=\n", encoding="utf-8")
    task_dir = _make_task("pytest-hook-hidden", target, "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", "pytest-hook-hidden")
        assert r.returncode == 0, f"隐藏产出被当成空转:\n{r.stdout}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


# ── 04-review: README ──

def test_missing_readme_blocks_archive(tmp_path):
    """走归档路由时缺 README 必须硬阻断 —— hook-04-06 的规则就是这么写的。"""
    target = _project(tmp_path / "repo-noreadme", with_readme=False)
    name = "pytest-hook-readme-block"
    task_dir = _make_task(name, target, "04-review")
    _review_md(task_dir)
    ss.write_route(name, "05-Archive")
    try:
        r = _run_hook("check_04-review.sh", name)
        assert r.returncode != 0, f"缺 README 却允许归档:\n{r.stdout}"
        assert "README" in r.stdout, r.stdout
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_missing_readme_allowed_on_reroute(tmp_path):
    """返工路由不拦：这一轮的目的本来就是回去补东西。"""
    target = _project(tmp_path / "repo-reroute", with_readme=False)
    name = "pytest-hook-readme-reroute"
    task_dir = _make_task(name, target, "04-review")
    _review_md(task_dir)
    ss.write_route(name, "03-Coding")
    try:
        r = _run_hook("check_04-review.sh", name)
        assert r.returncode == 0, f"返工路由被 README 检查拦住:\n{r.stdout}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_readme_present_passes_archive(tmp_path):
    target = _project(tmp_path / "repo-readme-ok")
    name = "pytest-hook-readme-ok"
    task_dir = _make_task(name, target, "04-review")
    _review_md(task_dir)
    ss.write_route(name, "05-Archive")
    try:
        r = _run_hook("check_04-review.sh", name)
        assert r.returncode == 0, f"README 齐备却被拦:\n{r.stdout}\n{r.stderr}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
