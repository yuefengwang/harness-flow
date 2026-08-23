"""A5 的 3.5：max_parallel 必须在**真实执行路径**上限住并发。

为什么单有 plan_batches 不够：它是纯函数，返回批次列表也不代表图真的分批跑了。
探针实测 4 个分支默认起 4 个线程、峰值并发 4（A5 的 11.1）。
所以这里数**运行时峰值**，不数批次长度 —— 后者可以在图完全不限流的情况下通过。
"""

import copy
import threading
import time

import pytest

from sw_lib.core import config as C
from sw_lib.workflow import review_graph as RG


@pytest.fixture(autouse=True)
def _restore_global_config():
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _configure(n: int, max_parallel: int):
    names = [f"r{i}" for i in range(n)]
    cfg = C._manager.config
    cfg.roles = {n_: C.RoleConfig(agent="opencode", model="m", description="", tools=[])
                 for n_ in names}
    cfg.stage_roles = {}
    cfg.review = C.ReviewConfig(
        objective_enabled=False,
        subjective=[C.SubjectiveReviewer(role=n_, model=f"m{i}", kind="k")
                    for i, n_ in enumerate(names)],
        max_parallel=max_parallel,
    )


class _PeakCounter:
    def __init__(self):
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def run(self, task_name, role_id, model, kind):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.05)
        with self._lock:
            self.active -= 1
        return {"role_id": role_id}


def test_runtime_peak_concurrency_respects_max_parallel():
    _configure(4, max_parallel=2)
    counter = _PeakCounter()
    graph = RG.build_review_graph(runner=counter.run,
                                  objective_runner=lambda t: {"ok": True})

    out = RG.run_review(graph, {"task_name": "t", "stage_idx": 3,
                                "review_findings": [], "objective_result": None})

    assert counter.peak <= 2, f"峰值并发 {counter.peak} 超过 max_parallel=2"
    # 限流不得吃掉分支：4 个审查者仍须全部跑完。
    assert len(out["review_findings"]) == 4


def test_max_parallel_read_from_config_not_hardcoded():
    """把上限改成 1 应当真的串行 —— 证明读的是配置而非写死的常数。"""
    _configure(3, max_parallel=1)
    counter = _PeakCounter()
    graph = RG.build_review_graph(runner=counter.run,
                                  objective_runner=lambda t: {"ok": True})

    out = RG.run_review(graph, {"task_name": "t", "stage_idx": 3,
                                "review_findings": [], "objective_result": None})

    assert counter.peak == 1, f"max_parallel=1 应串行，实际峰值 {counter.peak}"
    assert len(out["review_findings"]) == 3
