"""基线归档必须能进版本库（B0 第 10 节：不可回滚、严禁删除）。

`.gitignore` 里有一条 `workspace/*`，而归档正落在 `workspace/probe/` 下。
被忽略意味着：

- 它进不了任何提交，A11 在别的检出/worktree 里读不到对照组数据；
- 一次 `git clean -xdf` 就把这份**无法重新生成**的数据永久删掉。

B0 的第 10 节把它列为「不可回滚，且严禁删除」，A11 的验收 10、11
全部消费它。因此「能被 git 跟踪」是这份数据的生存条件，不是偏好。
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _is_ignored(rel: str) -> bool:
    """git 自己怎么看这个路径 —— 不靠解析 .gitignore 文本。

    `check-ignore` 的退出码 0 表示「被忽略」。
    """
    proc = subprocess.run(["git", "check-ignore", "-q", rel],
                          cwd=str(ROOT), capture_output=True)
    return proc.returncode == 0


def test_probe_archive_is_not_git_ignored():
    """`workspace/probe/baseline-*.json` 必须可被 git 跟踪。"""
    assert not _is_ignored("workspace/probe/baseline-2026-08-23.json"), (
        "基线归档被 .gitignore 排除了 —— 它无法重新生成，"
        "被忽略等于一次 git clean 就永久丢失（B0 第 10 节）")


def test_probe_dir_itself_is_not_ignored():
    assert not _is_ignored("workspace/probe/"), \
        "workspace/probe/ 整个目录被忽略"


def test_other_workspace_content_stays_ignored():
    """但 `workspace/` 下的其余内容仍应被忽略。

    例外只开给这一份不可重生成的数据。把整个 workspace 放进版本库会
    把用户的任务状态、日志、临时产物全带进去。
    """
    assert _is_ignored("workspace/tasks/some-task/.state"), \
        "例外开得过宽 —— 用户任务状态不该进版本库"
