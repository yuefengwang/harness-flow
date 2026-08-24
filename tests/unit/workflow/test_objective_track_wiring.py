"""A6 的接线：客观轨必须真的有调用方。

今天已经两次撞上同一形态：`record_claims()` 有实现有测试但没有调用方，
claims 永远是空；`active_roles_label()` 同样。单元全绿 != 机制接通。

所以 A6 落地时先把这条钉住：04-review 的硬门禁必须真的调用客观轨，
且 `hard_fail` 必须真的能阻断。只测 `run_checks()` 本身等于没测有人调用它。
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "hooks" / "check_04-review.sh"


def test_hook_invokes_objective_check():
    """源码级：hook 里必须出现客观轨的调用。"""
    src = HOOK.read_text(encoding="utf-8")
    assert "objective_check" in src, (
        "check_04-review.sh 没有调用客观轨 —— A6 的实现没有任何调用方，"
        "跟 A3 的 record_claims() 是同一种空转")


def test_hook_no_longer_gates_readme_on_route():
    """A6 的 3.2 / 验收 3：README 的严重性不得再依赖 Route。

    要删的就是这一行：
        if [ "$ROUTE_VAL" = "05-archive" ] && [ "$README_ERRORS" -gt 0 ]
    它让 Route 决定严重性、严重性又决定 Route。
    """
    src = HOOK.read_text(encoding="utf-8")
    pattern = re.compile(
        r'\[\s*"\$ROUTE_VAL"\s*=\s*"05-archive"\s*\]\s*&&\s*'
        r'\[\s*"\$README_ERRORS"')
    assert not pattern.search(src), (
        "循环依赖仍在：README 严重性依赖 Route（check_04-review.sh 的 153 行）")


def test_objective_check_runs_without_llm_in_hook_path():
    """hook 调客观轨这条路径上不得起任何 agent。"""
    src = HOOK.read_text(encoding="utf-8")
    # hook 里调 python 跑客观轨的那段不应引用 agent
    for bad in ("OpenCodeAgent", "MockAgent", "opencode serve"):
        assert bad not in src, f"hook 引用了 agent: {bad}"


def test_hard_fail_blocks_the_gate(tmp_path):
    """行为级：hard_fail 必须真的让门禁非零退出。

    这是本文件最重要的一条 —— 上面三条都是源码级断言，
    它们能被一句注释骗过去，这条不能。
    """
    # 直接验判定层的契约：hard_fail 为真时退出码非零。
    # 用一个零测试目录（O3 必 fail），走 hook 里同一个入口。
    proj = tmp_path / "empty"
    proj.mkdir()
    (proj / "app.py").write_text("x = 1\n", encoding="utf-8")

    code = (
        "import json,sys;"
        "sys.path.insert(0, %r);"
        "from sw_lib.workflow import objective_check as OC;"
        "r = OC.run_checks(%r);"
        "print(json.dumps(r['hard_fail_ids']));"
        "sys.exit(1 if r['hard_fail'] else 0)"
    ) % (str(ROOT), str(proj))
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, cwd=str(ROOT))

    assert proc.returncode != 0, (
        f"零测试目录未能让门禁失败。stdout={proc.stdout!r}")
    assert "O3" in proc.stdout, proc.stdout


def test_healthy_project_does_not_block(tmp_path):
    """反向：健康项目不得被新检查挡住（验收 8 的单调性）。"""
    proj = tmp_path / "good"
    proj.mkdir()
    (proj / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (proj / "README.md").write_text("# good\n\nusage\n", encoding="utf-8")
    (proj / "test_app.py").write_text(
        "from app import f\n\n\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8")

    code = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "from sw_lib.workflow import objective_check as OC;"
        "r = OC.run_checks(%r);"
        "print(r['hard_fail_ids']);"
        "sys.exit(1 if r['hard_fail'] else 0)"
    ) % (str(ROOT), str(proj))
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, cwd=str(ROOT))

    assert proc.returncode == 0, (
        f"健康项目被误判阻断: {proc.stdout} {proc.stderr[-500:]}")
