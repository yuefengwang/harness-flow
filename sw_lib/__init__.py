"""sw_lib — Harness-Flow Agent Workflow 库。

为避免「胖入口」问题（顶层 import 触发整条 web+langgraph+fastapi 链，
导致缺 web 依赖时整个包不可用），本 __init__ 仅暴露轻量符号。
具体子模块（cli/web/langgraph）一律惰性导入。
"""

def main():
    """命令行入口（惰性加载 cli.main，避免未用 web 时也拉起 web 依赖链）。"""
    from .cli.main import main as _main
    return _main()


__all__ = ["main"]

