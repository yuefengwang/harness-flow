"""A2 见证用的 pytest 插件：把「节点 id → 结果」结构化落盘。

为什么不解析 `-rf` 文本输出：skip 的摘要行是 ``SKIPPED [1] test_y.py:3: x``
——**没有节点 id**。而 A2 的 10.4 指出最容易的假绿正是「全 skip」：
它的退出码是 0，若 03b 只看退出码就会把 skipped 当成转绿。
要区分 passed 与 skipped，必须拿到每个节点的真实 outcome，
文本解析给不了这个信息。

插件本身不做任何判定 —— 判定在 `sw_lib/workflow/red_witness.py`。
这里只负责如实记录 pytest 说了什么。

输出路径由环境变量 ``RW_OUT`` 给出。collection error（退出码 2）时
``pytest_sessionfinish`` 仍会被调用，此时 nodes 为空 —— 这正是
「造红」可被识别的形态。
"""

import json
import os

_RESULTS = {}


def pytest_runtest_logreport(report):
    """记录每个测试节点的最终结果。

    三个阶段都要看，否则会漏判：

    * ``setup`` —— skip 标记与 fixture 报错都在这里出现，不看就丢了 skipped；
    * ``call`` —— 正常的 passed / failed；
    * ``teardown`` —— call 通过但清理时炸掉，同样不算绿。
    """
    node = report.nodeid
    if report.when == "setup":
        if report.outcome in ("skipped", "failed"):
            _RESULTS[node] = report.outcome
    elif report.when == "call":
        _RESULTS[node] = report.outcome
    elif report.when == "teardown" and report.outcome == "failed":
        _RESULTS[node] = "failed"


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("RW_OUT")
    if not out:
        return
    payload = {"exit_code": int(exitstatus), "nodes": _RESULTS}
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError:
        # 写不出结果时保持静默：判定侧读不到文件会判为 unavailable，
        # 而不是把「不知道」当成通过。
        pass
