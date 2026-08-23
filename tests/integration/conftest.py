"""排除以脚本方式编写的集成检查文件。

`test_sw_cli.py` 是一个自上而下执行的 CLI 回归脚本：它在模块顶层直接跑
`sw` 子命令，并以 `sys.exit()` 结束。被 pytest 收集时 import 即执行，
`sys.exit` 会让整个 pytest 进程 INTERNALERROR。

它仍然是有价值的端到端检查，手工运行：
    SW_NON_INTERACTIVE=1 python3 tests/integration/test_sw_cli.py
"""
collect_ignore = ["test_sw_cli.py"]
