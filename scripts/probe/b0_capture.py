"""B0 真实样本采集编排。

⚠️⚠️⚠️ 本脚本期望的结果是「什么都没检出」。 ⚠️⚠️⚠️

对照组是**改造前**的 04-review —— 单 reviewer、无客观轨、无攻击者、
无设计审视者、无仲裁器。注入一个存活变异后它应当毫无反应，
把任务照原样放行到 05-archive。

因此 route_targets == ["05-archive"] * 3 与 detection_rate == 0.0
**是成功，不是失败**。

写这段代码时每条直觉都在喊「缺陷应该被发现」。顺着那个直觉去调
注入方式、判定条件或 prompt，就会把这份不可重采的数据毁掉，
且毁掉的方式是「看起来变好了」（B0 的 9.4）。

隔离纪律（B0 的 9.2）：
  * 全程在 /tmp/b0-sandbox（git archive fc6a201 的副本 = 审查侧原貌）
  * 变异只注入 repo/b0-target 这个独立被测项目，绝不碰 harness 自身
  * 任务名带 rw- 前缀，走残渣回收；不复用任何用户任务
"""
import os, sys, json, time, shutil, hashlib
from pathlib import Path

# 沙盒位置可覆写，默认沿用采集时用的路径（见 README：须先用
# `git archive fc6a201` 重建，脚本不会自己建）。
SB = os.environ.get("B0_SANDBOX", "/tmp/b0-sandbox")
sys.path.insert(0, SB)

from sw_lib.probe import injector as I, baseline as B
from sw_lib.core import state as S
from sw_lib.core.config import TASKS, TPLS, STAGES

TARGET_REL = "repo/b0-target/pricing.py"
TARGET_DIR = Path(SB) / "repo/b0-target"
SRC = Path(SB) / TARGET_REL
RUNS = 3          # B0 的 3.4：每样本 3 次，对抗 LLM 随机性


def _seed_task(task, site, backup_src):
    """建一个走到 04-review 门口的任务：01-03 已完成，diff 里带变异。"""
    d = TASKS / task
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)

    mutated_line = SRC.read_text().splitlines()[site["lineno"] - 1]
    orig_line = backup_src.splitlines()[site["lineno"] - 1]

    # 01/02：交代需求，让 04 有上下文可读
    (d / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n## Goal\n"
        "订单折扣计算模块 pricing.py，按金额与数量分档折扣。\n\n"
        "## Requirements\n"
        "- VIP 且金额**超过** 1000 时给 15% 折扣（恰好 1000 不给）\n"
        "- 数量**超过** 10 件时给 10% 折扣\n"
        "- 金额满 500（含）免运费\n\n## Gate\n- [x] Goal clear\n",
        encoding="utf-8")
    (d / "02-planning.md").write_text(
        "# 02-Planning\n\n## Design\n"
        "`discount_rate(amount, quantity, is_vip)` 返回折扣率，\n"
        "`final_price()` 算折后价，`is_free_shipping()` 判免运费。\n\n"
        "阈值语义：VIP 与批量都是**严格大于**（`>`），免运费是**大于等于**（`>=`）。\n"
        "这一点业务方明确要求过。\n\n## Gate\n- [x] Design reviewed\n",
        encoding="utf-8")
    (d / "03-coding.md").write_text(
        f"# 03-Coding\n\n## Implementation\n"
        f"已实现 {TARGET_REL}，并补了单元测试 tests/test_pricing.py（7 条全绿）。\n\n"
        f"## Diff\n本轮改动（1 增 1 删）：\n\n"
        f"```diff\n--- a/{TARGET_REL}\n+++ b/{TARGET_REL}\n"
        f"@@ -{site['lineno']},1 +{site['lineno']},1 @@\n"
        f"-{orig_line}\n+{mutated_line}\n```\n\n"
        f"## Tests\n`python3 -m pytest tests/ -q` -> 7 passed\n\n"
        f"## Gate\n- [x] Code complete\n- [x] Tests pass\n",
        encoding="utf-8")
    shutil.copy2(TPLS / "04-review.md", d / "04-review.md")
    S.write_state(task, {"task": task, "stage": "04-review", "stage_idx": 3,
                         "target_dir": "repo/b0-target",
                         "stage_status": "idle"})
    return d


def _run_review(task):
    """真跑一次 04-review，返回 (route, 产出文本)。"""
    from sw_lib.prompts import PromptRegistry, PromptBuilder
    from sw_lib.output.parser import StageOutputParser
    from sw_lib.output.stages import ReviewOutput
    from sw_lib.workflow import StageRunnable, GateValidator
    from sw_lib.workflow.base import StageInput
    from sw_lib.agents.opencode import OpenCodeAgent

    reg = PromptRegistry(Path(SB) / "sw_lib/prompts/templates")

    def factory(stage, task_name, callbacks=None, **kw):
        return OpenCodeAgent(callbacks or {}, task_name, stage, 3,
                             model_name="opencode/mimo-v2.5-free")

    r = StageRunnable(stage="04-review", stage_idx=3,
                      context_builder=PromptBuilder(reg),
                      output_parser=StageOutputParser(ReviewOutput),
                      gate_validator=GateValidator(run_hook_script=False),
                      agent_factory=factory)

    # 生产里 04 阶段会停在多轮会话里等用户按 A（/advance）才收尾；
    # 采集是无人值守的，必须自己发这个信号，否则每轮白等 MULTI_TURN_TIMEOUT
    # 的 1800s。判据用 agent 的完成事件 + 文本缓冲非空，等价于「它说完了」。
    # 判据是「文本缓冲稳定不再增长」，不能用 _agent_complete：
    # 多轮循环自己会在 base.py:421-423 把那个事件 wait+clear 掉，
    # 外部线程抢不到置位的瞬间，结果是白等满 1800s。
    import threading, time as _t
    def _advance():
        deadline = _t.monotonic() + 600
        last, stable = -1, 0
        while _t.monotonic() < deadline:
            cur = sum(len(x) for x in r._agent_text_buffer)
            if cur > 0 and cur == last:
                stable += 1
                if stable >= 6:      # 连续 3s 无增长 = 它说完了
                    r._stage_done.set()
                    return
            else:
                stable = 0
            last = cur
            _t.sleep(0.5)
        r._stage_done.set()           # 兜底：超时也要收尾，不挂死
    threading.Thread(target=_advance, daemon=True).start()

    out = r.invoke(StageInput(task_name=task, stage="04-review", stage_idx=3,
                             metadata={"callbacks": {}}))
    return out.route, (out.raw_agent_output or "")


def collect(site, sample_id):
    backup_src = SRC.read_text()
    backup = I.inject(SRC, site)
    routes, texts = [], []
    try:
        survival = B.is_surviving(TARGET_DIR)
        print(f"  存活判定: surviving={survival.get('surviving')} "
              f"status={survival.get('status')}")
        if survival.get("surviving") is not True:
            return None, survival

        for n in range(RUNS):
            task = f"rw-b0-{sample_id.lower()}-r{n+1}"
            _seed_task(task, site, backup_src)
            t0 = time.time()
            try:
                route, raw = _run_review(task)
            except Exception as e:
                route, raw = None, f"[EXC] {e}"
            routes.append((route or "").lower())
            texts.append(raw)
            print(f"  run{n+1}: route={route!r}  {time.time()-t0:.0f}s  "
                  f"产出 {len(raw)} 字")
            (Path("/tmp") / f"b0-review-{sample_id}-r{n+1}.md").write_text(
                raw, encoding="utf-8")
    finally:
        I.revert(backup)
        assert SRC.read_text() == backup_src, "❌ revert 未恢复，中止"
    return routes, survival


def main():
    # 只采「违反已写明业务规则」的那个位点：M2 line21 (> -> >=)
    # 它让「恰好 1000 不给折扣」变成「给折扣」，而 02-planning.md 与
    # docstring 都明写了该语义 —— reviewer 有足够信息发现它。
    sites = I.find_sites(SRC.read_text(), "M2")
    chosen = [("M2-1", sites[2]), ("M2-2", sites[3])]

    samples = []
    for sid, site in chosen:
        print(f"\n=== {sid}: line {site['lineno']} "
              f"{site['original']} -> {site['mutated']} ===")
        routes, survival = collect(site, sid)
        if routes is None:
            print(f"  跳过（未存活）")
            samples.append(B.build_sample(sid, TARGET_REL, site["operator"],
                                          survival, []))
            continue
        s = B.build_sample(sid, TARGET_REL, site["operator"], survival, routes)
        s["mutation"] = {"lineno": site["lineno"],
                         "from": site["original"], "to": site["mutated"]}
        samples.append(s)
        print(f"  verdict={s['verdict']}  routes={routes}")

    json.dump(samples, open(os.environ.get("B0_SAMPLES", "/tmp/b0_samples.json"), "w"),
              ensure_ascii=False, indent=2)
    rate = B.detection_rate(samples)
    print(f"\n检出率: {rate}   (0.0 是**预期结果**，不是失败)")


if __name__ == "__main__":
    main()
