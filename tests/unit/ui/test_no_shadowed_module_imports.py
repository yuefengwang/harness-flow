"""守卫：函数内 import 不得遮蔽模块级已导入的同名符号。

背景（真实故障，2026-08-23）：
A0 收尾时为走受控入口，在 `MonitorTUI.run` 的 `elif` 分支里加了
`from ..workflow.runtime import WorkflowRuntime`，
而模块级第 31 行早已导入同一个名字。

后果是 Python 作用域规则：函数体内任何位置出现 `import X`，
`X` 即成为**整个函数**的局部变量。于是同一函数里位置更靠前的
`WorkflowRuntime.get_executor()` 在赋值前被访问 ——
`sw init` 走 `pending` 分支时必然抛
`UnboundLocalError: cannot access local variable 'WorkflowRuntime'`。

这条缺陷单测全绿也发现不了：787 条里没有一条真正进入
`MonitorTUI.run`（它要接管终端、起 Live 渲染）。
所以用静态作用域分析来守，而不是指望运行时覆盖。
"""
import glob
import io
import symtable

import pytest


def _walk(table, path=""):
    for child in table.get_children():
        child_path = f"{path}/{child.get_name()}"
        yield child_path, child
        yield from _walk(child, child_path)


def _shadowed_imports(source: str, filename: str):
    """返回 [(函数路径, 被遮蔽的名字)]。"""
    table = symtable.symtable(source, filename, "exec")

    module_imports = set()
    for name in table.get_identifiers():
        if table.lookup(name).is_imported():
            module_imports.add(name)

    hits = []
    if not module_imports:
        return hits

    for func_path, func in _walk(table):
        if func.get_type() != "function":
            continue
        for name in module_imports:
            try:
                sym = func.lookup(name)
            except KeyError:
                continue
            # 局部 + 由 import 绑定 = 函数内 import 遮蔽了模块级导入
            if sym.is_local() and sym.is_imported():
                hits.append((func_path, name))
    return hits


def test_tui_run_does_not_shadow_workflow_runtime():
    """回归锚点：就是把 `sw init` 打挂的那一处。"""
    path = "sw_lib/ui/tui.py"
    hits = _shadowed_imports(io.open(path, encoding="utf-8").read(), path)
    assert hits == [], (
        f"{path} 存在函数内 import 遮蔽模块级导入：{hits}。\n"
        "模块级已有该导入时，函数内不要再 import —— 会让该名字\n"
        "变成整个函数的局部变量，导致更靠前的引用抛 UnboundLocalError。"
    )


@pytest.mark.parametrize("path", sorted(glob.glob("sw_lib/**/*.py", recursive=True)))
def test_no_module_wide_import_shadowing(path):
    """全仓扫描。

    注意：函数内 import 本身是合法且必要的（`base.py` 用它避免
    与 `runtime.py` 循环依赖）。这里只禁止**模块级已导入过**的名字
    被函数内 import 再次绑定 —— 那种情况没有收益，只有 shadow 风险。
    """
    hits = _shadowed_imports(io.open(path, encoding="utf-8").read(), path)
    assert hits == [], f"{path} 存在遮蔽模块级导入的函数内 import：{hits}"


def test_monitor_tui_run_reaches_executor_without_unbound_local(tmp_path, monkeypatch):
    """动态复现：走 `pending` 分支时不得抛 UnboundLocalError。

    静态守卫（上面两条）只能证明 shadow 消失，不能证明 `sw init`
    真的活了 —— 用户实际报的是运行时崩栈。这条从 `MonitorTUI.run()`
    真实入口进去，断言它**确实执行到**了 `get_executor()`。

    `assert reached` 不可删：本测试第一版没有它，于是把 bug 放回去
    仍然是绿的 —— `_create_layout` 早在第 452 行就退出了，
    压根没走到引用 `WorkflowRuntime` 的那行。
    「什么都没发生」被当成了「通过」。
    """
    from sw_lib.core import state as state_mod
    from sw_lib.ui import tui as tui_mod

    if not tui_mod.HAS_RICH:
        pytest.skip("未安装 rich，无法进入 run() 的渲染路径")

    task = "SHADOWCHECK"
    (tmp_path / "tasks" / task).mkdir(parents=True)
    monkeypatch.setattr(state_mod, "TASKS", tmp_path / "tasks")
    # stage_status=pending -> 进入引用 WorkflowRuntime 的第一个分支
    state_mod.update_state(task, lambda s: {
        **s, "id": task, "stage": "01-brainstorming",
        "stage_idx": 0, "stage_status": "pending",
    })

    class _Stop(Exception):
        pass

    reached = []

    def _fake_get_executor():
        reached.append(True)
        raise _Stop()

    class _FakeLive:
        """替掉 Live，避免接管终端；仍保留 with 语义让函数体继续执行。"""

        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def refresh(self):
            pass

    monkeypatch.setattr(tui_mod, "Live", _FakeLive)
    monkeypatch.setattr(tui_mod.WorkflowRuntime, "get_executor",
                        staticmethod(_fake_get_executor))

    ui = tui_mod.MonitorTUI(task, "01-brainstorming", 0, "opencode")
    # 不接管真实终端
    ui._old_term = None
    ui._is_tty = False

    try:
        ui.run()
    except UnboundLocalError as exc:  # 正是用户报的故障
        raise AssertionError(f"run() 仍存在作用域缺陷: {exc}")
    except _Stop:
        pass

    assert reached, (
        "run() 未执行到 get_executor()，本测试没有真正覆盖故障路径。"
        "不要通过放宽这条断言来让测试变绿。"
    )
