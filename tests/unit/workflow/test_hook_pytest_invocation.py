"""门禁钩子跑 pytest 的方式必须与 agent 一致。

现场 bug（任务 T2）：agent 用 `PYTHONPATH=src python3 -m pytest` 跑出 25 passed，
门禁钩子裸跑 `pytest` 却在收集期 ModuleNotFoundError，报「❌ pytest 失败」。
两者对同一份代码给出相反结论，用户反复 /advance 都过不去，而且钩子把输出
`>/dev/null 2>&1` 全丢了 —— 屏幕上只有四个字，无从下手。

这里用真实临时项目实跑钩子，断行为而不是断脚本文本：脚本形状能改出无数种写法，
能过闸和不能过闸才是要锁住的契约。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS

ROOT = Path(__file__).resolve().parents[3]
HOOKS_WITH_TESTS = ["check_03-coding.sh", "check_04-review.sh"]


def _make_project(tmp_path, layout, failing=False):
    """建一个最小 python 项目。layout='src' 走 src-layout，'flat' 走平铺。"""
    if layout == "src":
        pkg = tmp_path / "src" / "mypkg"
    else:
        pkg = tmp_path / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")

    tests = tmp_path / "tests"
    tests.mkdir()
    expected = 2 if failing else 1
    (tests / "test_it.py").write_text(
        f"from mypkg import f\n\n\ndef test_f():\n    assert f() == {expected}\n",
        encoding="utf-8")
    # 04-review 在归档路由上要求 README 存在（hook-04-06）。这批用例关心的是
    # pytest 的调用方式，所以把文档补齐，别让它们卡在无关的检查上。
    (tmp_path / "README.md").write_text("# demo\n\n用法说明。\n", encoding="utf-8")
    return tmp_path


def _make_task(name, target_dir, stage):
    d = TASKS / name
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


@pytest.fixture
def review_ready():
    """04-review 钩子在跑测试前有若干文档校验，先把它们喂饱。

    否则用例会在 Security/Route 检查处提前退出，根本走不到 pytest ——
    那就成了假绿。
    """
    def _prep(task_dir, task_name):
        (task_dir / "04-review.md").write_text(
            "# 04-Review\n\n## Security\n- 无敏感数据\n\n"
            "## Reroute Evidence\n\n| # | 问题 |\n|---|---|\n| 1 | 具体问题描述 |\n",
            encoding="utf-8")
        from sw_lib.workflow import stage_state as ss
        ss.write_route(task_name, "05-Archive")
    return _prep


@pytest.mark.parametrize("hook_name", HOOKS_WITH_TESTS)
def test_src_layout_project_passes(hook_name, tmp_path, review_ready):
    """src-layout 且包未安装时，钩子必须能跑通 —— 这正是 T2 卡死的形状。"""
    proj = _make_project(tmp_path / "proj", layout="src")
    name = f"pytest-hook-src-{hook_name.split('_')[1][:2]}"
    task_dir = _make_task(name, proj, "03-coding")
    if hook_name.startswith("check_04"):
        review_ready(task_dir, name)
    try:
        r = _run_hook(hook_name, name)
        assert r.returncode == 0, f"src-layout 项目被误判失败:\n{r.stdout}\n{r.stderr}"
        assert "✅ 通过" in r.stdout, r.stdout
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


@pytest.mark.parametrize("hook_name", HOOKS_WITH_TESTS)
def test_real_failure_still_blocks(hook_name, tmp_path, review_ready):
    """真失败仍要拦住 —— 修 src-layout 不能把闸门一起放开。"""
    proj = _make_project(tmp_path / "proj", layout="src", failing=True)
    name = f"pytest-hook-fail-{hook_name.split('_')[1][:2]}"
    task_dir = _make_task(name, proj, "03-coding")
    if hook_name.startswith("check_04"):
        review_ready(task_dir, name)
    try:
        r = _run_hook(hook_name, name)
        assert r.returncode != 0, f"失败的测试竟然过闸了:\n{r.stdout}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


@pytest.mark.parametrize("hook_name", HOOKS_WITH_TESTS)
def test_failure_output_is_visible(hook_name, tmp_path, review_ready):
    """失败时必须回显 pytest 的真实输出。

    只说「pytest 失败」等于让用户去猜，T2 就卡在这上面。
    """
    proj = _make_project(tmp_path / "proj", layout="src", failing=True)
    name = f"pytest-hook-out-{hook_name.split('_')[1][:2]}"
    task_dir = _make_task(name, proj, "03-coding")
    if hook_name.startswith("check_04"):
        review_ready(task_dir, name)
    try:
        r = _run_hook(hook_name, name)
        combined = r.stdout + r.stderr
        assert "test_f" in combined, f"未回显失败的测试名:\n{combined}"
        assert "assert" in combined.lower(), f"未回显断言细节:\n{combined}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


@pytest.mark.parametrize("hook_name", HOOKS_WITH_TESTS)
def test_flat_layout_still_works(hook_name, tmp_path, review_ready):
    """平铺布局（无 src/）不能因为这次修改而回退。"""
    proj = _make_project(tmp_path / "proj", layout="flat")
    name = f"pytest-hook-flat-{hook_name.split('_')[1][:2]}"
    task_dir = _make_task(name, proj, "03-coding")
    if hook_name.startswith("check_04"):
        review_ready(task_dir, name)
    try:
        r = _run_hook(hook_name, name)
        assert r.returncode == 0, f"平铺项目被误判失败:\n{r.stdout}\n{r.stderr}"
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


@pytest.mark.parametrize("hook_name", HOOKS_WITH_TESTS)
def test_hook_uses_module_invocation(hook_name):
    """必须用 `python3 -m pytest` 而不是裸 `pytest`。

    PATH 上的 pytest 脚本可能绑在另一个解释器上（本机 3.9 vs python3 3.12），
    裸调用会让门禁与 agent 看到不同的依赖环境。
    """
    body = (HOOKS_DIR / hook_name).read_text(encoding="utf-8")
    # 钩子现在把跑测试委托给 lib_run_tests.sh
    if "lib_run_tests.sh" in body:
        body += (HOOKS_DIR / "lib_run_tests.sh").read_text(encoding="utf-8")
    assert "python3 -m pytest" in body, f"{hook_name} 应使用 python3 -m pytest"


def _make_venv_project(tmp_path):
    """项目自带 .venv，且依赖只装在那个 venv 里。

    用一个假模块冒充"只有 venv 才有的依赖"：真跑 pip install 太慢，而这里
    要验证的是"钩子用哪个解释器"，不是 pip 能不能装东西。
    """
    proj = tmp_path / "venvproj"
    proj.mkdir()
    (proj / "app.py").write_text(
        "import onlyinvenv\n\n\ndef f():\n    return onlyinvenv.VALUE\n",
        encoding="utf-8")
    (proj / "test_app.py").write_text(
        "from app import f\n\n\ndef test_f():\n    assert f() == 42\n",
        encoding="utf-8")
    (proj / "README.md").write_text("# venvproj\n\n说明。\n", encoding="utf-8")

    # 手搓一个最小 venv 布局：bin/python 转发到系统解释器，并把只在 venv 里
    # 可见的依赖目录塞进 PYTHONPATH。
    site = proj / ".venv" / "site"
    site.mkdir(parents=True)
    (site / "onlyinvenv.py").write_text("VALUE = 42\n", encoding="utf-8")
    bindir = proj / ".venv" / "bin"
    bindir.mkdir(parents=True)
    shim = bindir / "python"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{site}:$PYTHONPATH"\n'
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8")
    shim.chmod(0o755)
    return proj


def test_hook_uses_project_venv(tmp_path):
    """依赖装在项目 .venv 里时，钩子必须用那个解释器。

    任务 T3：agent 在 repo/T3/.venv 里装了 pandas 并跑通测试，钩子用 harness
    的 python3 直接 ModuleNotFoundError —— 门禁与 agent 又一次对同一份代码
    给出相反结论，而用户没有任何可操作的下一步。
    """
    proj = _make_venv_project(tmp_path)
    name = "pytest-hook-venv"
    task_dir = _make_task(name, proj, "03-coding")
    try:
        r = _run_hook("check_03-coding.sh", name)
        assert r.returncode == 0, f"未使用项目 venv:\n{r.stdout}\n{r.stderr}"
        assert "使用项目虚拟环境" in r.stdout, r.stdout
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_review_readme_check_reads_state_not_status(tmp_path, review_ready):
    """README 校验的 target_dir 要和跑测试用同一个源（.state）。

    此前它读 workspace/STATUS.json —— 那是汇总缓存，target_dir 未必落进去，
    于是同一个钩子里 pytest 正常跑、README 校验却报「target_dir 为空」直接跳过，
    整段检查静默失效。
    """
    proj = _make_project(tmp_path / "proj", layout="src")
    name = "pytest-hook-readme-src"
    task_dir = _make_task(name, proj, "04-review")   # 只写 .state，不碰 STATUS.json
    review_ready(task_dir, name)
    try:
        r = _run_hook("check_04-review.sh", name)
        combined = r.stdout + r.stderr
        assert "无法确定目标目录" not in combined, \
            f"README 校验没读到 .state 里的 target_dir:\n{combined}"
        assert "[README Check] 验证文档" in combined, combined
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
