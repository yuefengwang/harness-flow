"""A1：源码级断言 —— hook 的 git 调用范围与影子配置清理。

设计依据：docs/design/A1-task-git-repo.md 的 3.6 / 3.7（验收 10、11）。

这类断言只读源码，不执行 git，因此不存在污染真实仓库的风险。
"""
import re

import yaml

from sw_lib.core.config import CONFIG_DIR, HOOKS_DIR, ROOT


# ── 验收 11：hook 不得有裸 git diff ──

def test_archive_hook_has_no_bare_git_diff():
    """验收 11：出现 `git diff` 时必须同时出现 `-C`。

    这是 A1 的 1.1 那条从第一天就看错仓库的检查：
    裸 `git diff` 的 cwd 是 harness 根，查的是 harness 自己的 README。
    """
    hook = HOOKS_DIR / "check_05-archive.sh"
    body = hook.read_text(encoding="utf-8")

    offenders = []
    for lineno, line in enumerate(body.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or "git diff" not in stripped:
            continue
        if "-C" not in stripped:
            offenders.append(f"{lineno}: {stripped}")
    assert not offenders, (
        "check_05-archive.sh 存在裸 git diff（无 -C），会检查 harness 自身仓库:\n"
        + "\n".join(offenders))


def test_archive_hook_resolves_target_dir_from_state():
    """3.7：TARGET_DIR 必须从任务 .state 解析，而不是假定 cwd。"""
    body = (HOOKS_DIR / "check_05-archive.sh").read_text(encoding="utf-8")
    assert "TARGET_DIR" in body, "hook 应解析任务的 target_dir"
    assert "resolve_target_dir" in body, (
        "应复用 lib_run_tests.sh 的 resolve_target_dir，不要各自再解析一遍 .state")


def test_no_bare_git_diff_in_any_hook():
    """范围扩到全部 hook：同样的失效不该在别处复现。"""
    offenders = []
    for hook in sorted(HOOKS_DIR.glob("check_*.sh")):
        for lineno, line in enumerate(hook.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not re.search(r"\bgit (diff|status|add|commit)\b", stripped):
                continue
            if "-C" not in stripped:
                offenders.append(f"{hook.name}:{lineno}: {stripped}")
    assert not offenders, "hook 中存在不带 -C 的 git 调用:\n" + "\n".join(offenders)


# ── 验收 10：影子配置只删 yaml，不动 shell ──

def test_shadow_branch_keys_removed_from_yaml():
    """验收 10 前半：config.yaml 中不再有 base_branch / branch_prefix。

    Python 侧从不读这两个键（config.py 无对应解析），
    留着会让人以为改 yaml 能生效。
    """
    raw = yaml.safe_load((CONFIG_DIR / "config.yaml").read_text(encoding="utf-8"))
    harness = (raw or {}).get("harness", {})
    assert "base_branch" not in harness, "yaml 中的影子配置 base_branch 应删除"
    assert "branch_prefix" not in harness, "yaml 中的影子配置 branch_prefix 应删除"


def test_shell_branch_config_still_present():
    """验收 10 后半：**shell 侧是真配置，不许删**（A1 的 2.7 实测）。

    config.sh 的同名环境变量才是真正生效的那套，
    dispatch.sh 的 worktree 创建依赖它。
    """
    cfg_sh = (CONFIG_DIR / "config.sh").read_text(encoding="utf-8")
    assert "HARNESS_BASE_BRANCH" in cfg_sh, "删掉会打断 worktree 流程"
    assert "HARNESS_BRANCH_PREFIX" in cfg_sh, "删掉会打断 worktree 流程"

    dispatch = (ROOT / "bin" / "dispatch.sh").read_text(encoding="utf-8")
    assert "HARNESS_BASE_BRANCH" in dispatch, "dispatch.sh 仍须能取到 base 分支"
    assert re.search(r"worktree add -b .*BASE_BRANCH", dispatch), \
        "worktree 创建仍须使用 base 分支"


# ── 3.1：git 调用的唯一出口 ──

def test_git_calls_are_centralized_in_git_repo():
    """3.1：git_repo.py 是 harness 内 git 调用的唯一出口。

    其他 Python 模块不得自己 subprocess 调 git —— 那正是「忘了加 -C」
    这类缺陷的滋生地。

    判据只认**进程调用形态**（argv 列表/元组的首元素是 "git"）。
    此前的写法把 `toolbox.py` 的命令白名单字面量 `"git", "git-lfs",`
    也算作调用 —— 那是一份允许清单，不是调用点，属判据本身写错。
    """
    offenders = []
    for py in sorted((ROOT / "sw_lib").rglob("*.py")):
        if py.name == "git_repo.py":
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # argv 首元素为 "git"：["git", ...] / ("git", ...) / [ "git" ]
            if re.search(r"""[\[\(]\s*["']git["']""", stripped):
                rel = py.relative_to(ROOT)
                offenders.append(f"{rel}:{lineno}: {stripped}")
    assert not offenders, (
        "以下模块绕过 git_repo 直接调用 git:\n" + "\n".join(offenders))
