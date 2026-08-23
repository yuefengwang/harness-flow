"""门禁钩子不得在 harness 根目录跑测试（否则递归执行 harness 自身套件）。

回归背景：check_03-coding.sh / check_04-review.sh 原来直接 `pytest`，而 cwd 是
harness 根目录 —— `sw advance` 会递归跑整套 harness 测试并无限期挂住，
tests/integration/test_sw_cli.py 因此永远超时。
"""
import re
from pathlib import Path

import pytest

from sw_lib.core.config import HOOKS_DIR

HOOKS_WITH_TESTS = ["check_03-coding.sh", "check_04-review.sh"]


@pytest.mark.parametrize("hook_name", HOOKS_WITH_TESTS)
def test_hook_scopes_tests_to_task_dir(hook_name):
    """跑测试的命令必须先切到任务目录（cd "$TARGET_DIR" 或 ( cd ... && pytest )）。"""
    hook = HOOKS_DIR / hook_name
    assert hook.exists(), f"缺少钩子: {hook_name}"
    body = hook.read_text(encoding="utf-8")

    # 必须先解析出任务目录，再在其中跑测试
    assert "TARGET_DIR" in body or "TEST_DIR" in body, \
        f"{hook_name} 应通过任务的 target_dir 限定测试范围"

    # 每处 pytest / npm test 调用，要么自身就在 `( cd "$DIR" && ... )` 子 shell 里，
    # 要么之前已经 cd 到任务目录。
    dir_cd = re.compile(r'cd "\$(TARGET_DIR|TEST_DIR)"')
    seen_cd = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        has_cd = bool(dir_cd.search(stripped))
        # 只看真正的命令调用，排除 `-f "$DIR/pytest.ini"` 这类路径判断
        runs_tests = re.search(r"(^|[;&|(]\s*)(pytest|npm test)\b", stripped)
        if runs_tests and not has_cd:
            assert seen_cd, (
                f"{hook_name} 在切到任务目录前就执行 `{stripped}`，"
                "会递归跑 harness 自身测试"
            )
        if has_cd:
            seen_cd = True


def test_advance_hook_has_timeout():
    """cmd_advance 执行钩子必须带超时，否则钩子卡住 CLI 就永久挂起。"""
    src = (Path(__file__).resolve().parents[3] / "sw_lib" / "cli" / "commands.py").read_text(encoding="utf-8")
    assert "HOOK_TIMEOUT" in src, "cmd_advance 应定义钩子超时上限"
    m = re.search(r"subprocess\.run\(\s*\[str\(hook_script\), name\].*?\)\n", src, re.S)
    assert m, "未找到 cmd_advance 中的钩子调用"
    assert "timeout=" in m.group(0), f"钩子调用缺少 timeout 参数: {m.group(0)!r}"
