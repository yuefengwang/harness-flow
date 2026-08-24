"""prompt 里的路径必须与 agent 的 cwd 同一个参照系。

任务 `helloworld` 的现场：agent 的 workdir 是 `repo/helloworld`
（`OpenCodeAgent._default_workdir`），而 prompt 的「项目信息」段写着

    代码生成目录: repo/helloworld

这是**相对 harness 根**的路径。agent 在自己的 cwd 里解析它，得到的是
`repo/helloworld/repo/helloworld`。日志里随之出现一串在错路上的探路：

    read  {'filePath': '.../repo/helloworld'}                    ← 把它当子目录
    glob  {'pattern': 'workspace/tasks/helloworld/**/*'}         ← 找不到
    read  {'filePath': '.../repo/helloworld/workspace'}          ← 不存在
    ...

`.sw-context` 里的 `target_dir` 又是**绝对路径**（`service.py` 用了
`.resolve()`），`.state` 里是**相对路径**，两处表示法不一致，进一步坐实
了「同一个概念有两种写法」这件事。

这与 `test_prompt_self_sufficiency.py` 是同一族问题：那次是「给了路径就会
被去 glob」，这次是「给的路径在 agent 的参照系里根本不对」。修法同源 ——
prompt 必须说 agent 能直接用的话。

判据：项目信息段不得把 `target_dir` 的**相对**形式当成 agent 可用的路径。
agent 的 cwd 就是目标目录，正确的表述是「你当前就在目标目录」。
"""

import json
import shutil

import pytest

from sw_lib.core.config import ROOT, TASKS
from sw_lib.core.state import write_state
from sw_lib.prompts import PromptBuilder, PromptRegistry

TPL_DIR = ROOT / "sw_lib" / "prompts" / "templates"


@pytest.fixture
def builder():
    return PromptBuilder(PromptRegistry(TPL_DIR))


@pytest.fixture
def task():
    """建一个 target_dir 为相对路径的任务（create_task 的真实写法）。"""
    created = []

    def _make(name="prompt-path-frame", target_dir="repo/prompt-path-frame"):
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        write_state(name, {
            "id": name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock",
            "target_dir": target_dir,
        })
        created.append(d)
        return name, target_dir

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def test_project_info_does_not_hand_agent_a_harness_relative_path(builder, task):
    """项目信息段不得把 harness 相对路径当成 agent 可用的路径。

    agent 的 cwd 已经是 `repo/<task>`；再告诉它「代码生成目录:
    repo/<task>」，它只能理解成 cwd 下还有一层同名目录。
    """
    name, target_dir = task()
    info = builder._build_project_info(name)
    assert info, "前提不成立：项目信息段为空"

    assert target_dir not in info, (
        f"项目信息段把 harness 相对路径 {target_dir!r} 交给了 agent，"
        f"而 agent 的 cwd 就是该目录 —— 它会拼出 {target_dir}/{target_dir}。\n"
        f"实际内容:\n{info}")


def test_project_info_tells_agent_it_is_already_in_target(builder, task):
    """必须明确告知「你当前的工作目录就是目标目录」。

    只删掉错路径不够 —— agent 仍然需要知道该往哪写。锁语义不锁文案：
    判据要求出现「当前工作目录」这一意思的表述。
    """
    name, _ = task()
    info = builder._build_project_info(name)

    assert "当前工作目录" in info or "当前目录" in info, (
        f"没有告诉 agent 它已经在目标目录里，它只能靠猜:\n{info}")


def test_project_info_survives_absolute_target_dir(builder, task):
    """`target_dir` 为绝对路径时同样不得原样交给 agent。

    `.sw-context` 里存的就是绝对路径（service.py 用了 `.resolve()`），
    若有人改成从那里取值，这条判据保证行为不回退。
    """
    abs_dir = str((ROOT / "repo" / "prompt-path-frame-abs").resolve())
    name, _ = task(name="prompt-path-frame-abs", target_dir=abs_dir)
    info = builder._build_project_info(name)

    assert abs_dir not in info, (
        f"绝对路径被原样交给 agent，它会以为要在 cwd 下再拼一层:\n{info}")


def test_full_prompt_contains_no_harness_relative_task_paths(builder, task):
    """整条 prompt 里不得出现 `workspace/tasks/<task>/...` 这类 harness 相对路径。

    agent 对这类路径的唯一合理解释是「在我 cwd 下」，而那里什么都没有。
    任务 helloworld 因此连续 8 次 read/glob 在错路上打转。

    注意：`system.yaml` 的「严禁读取 workspace/tasks/*/.state」是**禁令**，
    不是让它去访问的路径，所以判据只针对带具体任务名的路径。
    """
    name, _ = task()
    prompt = builder.build(task_name=name, stage="01-brainstorming", stage_idx=0)
    assert prompt, "build 返回空"

    bad = f"workspace/tasks/{name}"
    assert bad not in prompt, (
        f"prompt 里出现了 harness 相对的任务路径 {bad!r} —— "
        f"agent 的 cwd 是 repo/{name}，它解析不到这个位置。")


def test_context_marker_and_state_agree_on_target_dir():
    """`.sw-context` 与 `.state` 对 `target_dir` 的表示法必须一致。

    现场：`.sw-context` 是绝对路径（`.resolve()`），`.state` 是相对路径。
    同一个概念两种写法，agent 读到哪个就按哪个拼，行为不可预期。

    判据用真实的 create 路径验证，而不是断言某一种写法 —— 两边一致即可。
    """
    from sw_lib.core.service import _write_context_marker

    name = "prompt-path-frame-marker"
    target = ROOT / "repo" / name
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    d.mkdir(parents=True, exist_ok=True)
    try:
        rel = f"repo/{name}"
        write_state(name, {
            "id": name, "stage": "01-brainstorming", "stage_idx": 0,
            "stage_status": "pending", "agent": "mock", "target_dir": rel,
        })
        _write_context_marker(rel, name, "feature")

        marker = json.loads((target / ".sw-context").read_text(encoding="utf-8"))
        from sw_lib.core.state import read_state
        state_dir = read_state(name).get("target_dir")

        from pathlib import Path as _P
        # 先确认两处指向同一个实际目录（表示法不同也应等价）
        assert _P(marker["target_dir"]).resolve() == _P(state_dir).resolve(), (
            f"两处指向的实际目录不同：\n"
            f"  .sw-context: {marker['target_dir']!r}\n"
            f"  .state:      {state_dir!r}")

        both_absolute = _P(marker["target_dir"]).is_absolute() and \
            _P(state_dir).is_absolute()
        both_relative = not _P(marker["target_dir"]).is_absolute() and \
            not _P(state_dir).is_absolute()
        assert both_absolute or both_relative, (
            f"`.sw-context` 与 `.state` 的 target_dir 表示法不一致：\n"
            f"  .sw-context: {marker['target_dir']!r}"
            f" ({'绝对' if _P(marker['target_dir']).is_absolute() else '相对'})\n"
            f"  .state:      {state_dir!r}"
            f" ({'绝对' if _P(state_dir).is_absolute() else '相对'})")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(target, ignore_errors=True)
