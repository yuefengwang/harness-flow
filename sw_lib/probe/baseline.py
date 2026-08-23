"""sw_lib.probe.baseline — 改造前基线采集（B0）。

设计依据：docs/design/B0-baseline-capture.md

⚠️⚠️ **本模块采集的正确结果是「什么都没检出」** ⚠️⚠️

对照组是**改造前**的 harness：04-review 只有单个 reviewer 角色，
没有客观轨、没有攻击者、没有设计审视者、没有仲裁器。
往代码里注入一个存活变异（现有测试抓不住的真缺陷）之后，
它**应当毫无反应**，把任务照原样放行到 05-archive。

因此：

    detection_rate == 0.0   是成功，不是失败
    route_targets 全为 05-archive   是成功，不是失败

写这段代码时每一条直觉都会喊「不对，缺陷应该被发现」。
那个直觉会让人去调注入方式、调判定条件、调 prompt，
直到「基线也能检出缺陷」—— 而那一刻这份数据就被永久毁掉了，
毁掉的方式还是「看起来变好了」（B0 的 9.4）。

**这份数据无法重新生成。** A11 的对照实验只有这一个对照组来源；
改造一旦落地，旧行为再也无法重现。宁可采得少，不可采得晚（B0 第 10 节）。

> ⚠️ 本文件当前是**骨架**（DEV-PROTOCOL 步 1 的产物）：
> 所有入口一律返回空值/放行，好让测试看到的红是**断言失败**
> 而不是 ImportError —— 后者是「造红」（协议 1.1）。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import ROOT, STAGES, is_mock_agent, _manager


# ── 第 1 步：前置检查与环境指纹（B0 的 2.1 / 2.3）──
#
# 这一步单独先做是刻意的：即便后面步骤来不及，
# 「采集时刻 A6-A9 尚未实施」这个事实也必须先钉在时间线上 ——
# 那是事后无法补证的（B0 第 6 节）。

# 审查侧四件套。判据是**文件是否存在**，不是文档里的标记 ——
# PARALLEL.md 的教训是 README 的 ✅ 只表示设计写完，不表示代码落地。
REVIEW_SIDE_MODULES: Tuple[str, ...] = (
    "sw_lib/workflow/objective_check.py",   # A6 客观轨
    "sw_lib/workflow/counterexample.py",    # A7 攻击者
    "sw_lib/workflow/design_review.py",     # A8 设计审视者
    "sw_lib/workflow/arbiter.py",           # A9 仲裁器
)

_MODULE_TO_TASK = {
    "objective_check.py": "A6",
    "counterexample.py": "A7",
    "design_review.py": "A8",
    "arbiter.py": "A9",
}

# 采集时点已落盘的改造。它们构成基线的既有污染，必须如实记录 ——
# 严格的「改造前」应在 A0 之前，那个时点已经错过（B0 的 1.3）。
IMPLEMENTED_BEFORE_CAPTURE: Tuple[str, ...] = ("A0", "A1", "A2")

_PURITY_NOTE = (
    "严格的改造前基线应在 A0 之前，该时点已错过。"
    "本基线仅对『审查能力』这一观测量有效："
    "采集时 04-review 仍是单 reviewer 角色，"
    "客观轨 / 攻击者 / 设计审视者 / 仲裁器均未实施。"
    "A11 引用本数据时只能表述为「审查侧改造带来的提升」，"
    "不得表述为「全套设计带来的提升」。"
)


def check_review_side_clean() -> Dict[str, Any]:
    """确认审查侧仍是原始状态，并如实标注污染维度（B0 的 2.1）。

    **发现污染时不放弃采集。** 一份标注了污染维度的基线远好过没有基线
    —— 这个时间窗口关掉就不再打开了。
    """
    contaminated: List[str] = []
    for rel in REVIEW_SIDE_MODULES:
        if (ROOT / rel).exists():
            task = _MODULE_TO_TASK.get(Path(rel).name)
            if task:
                contaminated.append(task)

    untouched = [t for t in _MODULE_TO_TASK.values() if t not in contaminated]
    return {
        "clean": not contaminated,
        "contaminated_dimensions": sorted(contaminated),
        "baseline_purity": ("review_side_clean" if not contaminated
                            else "partially_contaminated"),
        "purity_detail": {
            "implemented_before_capture": list(IMPLEMENTED_BEFORE_CAPTURE),
            "review_side_untouched": sorted(untouched),
            "note": _PURITY_NOTE,
        },
    }


def _git(*args: str) -> str:
    """只读地问 git，**经 A1 的 `run_git`**（harness 内唯一的 git 出口）。

    不自己 subprocess 调 git：那正是「忘了加 -C」这类缺陷的滋生地，
    而 A1 的 3.1 已把这条立为源码级契约
    （`test_git_calls_are_centralized_in_git_repo`）。
    `run_git` 强制拼 `-C <dir>`，不依赖进程 cwd。

    失败一律返回空串 —— 指纹缺一项好过整次采集中断，
    而这个时间窗口关掉就不再打开了。
    """
    from ..core.git_repo import GitRepoError, run_git

    try:
        return run_git(str(ROOT), *args)
    except (GitRepoError, OSError):
        return ""


def env_fingerprint() -> Dict[str, Any]:
    """采集时刻的环境指纹（B0 的 2.3 / 3.2）。

    `git_dirty` 与摘要必须一并记录：只记 sha 会让人误以为基线对应一个
    干净的提交，而事后无法复现那个状态。
    """
    porcelain = _git("status", "--porcelain")
    lines = [l for l in porcelain.splitlines() if l.strip()]
    summary = "; ".join(l.strip() for l in lines[:20])
    if len(lines) > 20:
        summary += f"; ...（共 {len(lines)} 项）"

    return {
        "git_sha": _git("rev-parse", "HEAD"),
        "git_dirty": bool(lines),
        "git_status_summary": summary,
        # mock 下采到的是夹具而非能力，因此 mode 必须如实记（B0 第 4 节）。
        "mode": "mock" if is_mock_agent() else "real",
        "config_snapshot": _config_snapshot(),
        "review_stage_roles": {"04-review": _role_for_stage("04-review")},
    }


def _role_for_stage(stage: str) -> Optional[str]:
    """某阶段当前挂的角色名。`_manager.config` 本身就是 harness 层。"""
    try:
        roles = _manager.config.stage_roles
    except Exception:
        return None
    return roles.get(stage) if isinstance(roles, dict) else None


def _config_snapshot() -> Dict[str, Any]:
    """五个角色的 agent / model 原样归档（B0 的 2.2）。

    这是「改造前五角色同 agent 同模型」这一事实的唯一留证 ——
    A4 的 U4-3 把独立性的证明推给了 A11，而 A11 需要这份快照做对照。
    """
    snapshot: Dict[str, Any] = {"roles": {}, "stage_roles": {}}
    try:
        roles = _manager.config.roles or {}
        for name, spec in roles.items():
            # RoleConfig 是 dataclass/模型，dict 形态也要能读 —— 配置结构
            # 正是 A4 要改造的对象，这里不假设它永远是当前形状。
            get = (spec.get if isinstance(spec, dict)
                   else lambda k, d=None, _s=spec: getattr(_s, k, d))
            snapshot["roles"][name] = {"agent": get("agent"),
                                       "model": get("model")}
        for stage in STAGES:
            snapshot["stage_roles"][stage] = _role_for_stage(stage)
    except Exception:
        pass
    return snapshot


# ── 第 3 步：存活性判定（B0 的 3.1 第 4 步）──

def is_surviving(target_dir, backup: Optional[Dict[str, Any]] = None,
                 ) -> Dict[str, Any]:
    """注入之后跑现有测试，判断这个变异是否**存活**。

    存活 = 现有测试抓不住 = 这是一个真缺陷但套件沉默 ——
    只有存活的变异才有资格进入基线观测：被测试抓住的变异检验的是
    测试套件，不是 reviewer（A11 的 2.1，M1 因此被排除）。

    复用 A2 的 pytest 执行层而不是重写一遍：它已经处理了 src-layout 的
    PYTHONPATH 与项目自带 `.venv`（A2 的 5.3，任务 T2/T3 的教训）。

    三态：没有测试可跑时返回 ``surviving=None`` + ``unavailable``。
    那种情形下任何变异都「不被捕获」，若记成存活，基线的分母会被
    一堆无意义样本灌大。
    """
    from ..workflow.red_witness import run_tests

    target = Path(str(target_dir))
    exit_code, nodes = run_tests(target)

    # 退出码 5 = 没收集到测试；-1 = 超时或解释器起不来。都是「未测量」。
    if exit_code in (5, -1) or not nodes:
        return {
            "surviving": None,
            "captured_by_tests": None,
            "status": "unavailable",
            "reason": ("没有可执行的测试" if exit_code == 5
                       else "测试执行未能完成（超时或解释器不可用）"),
            "exit_code": exit_code,
        }

    failed = sorted(n for n, o in nodes.items() if o == "failed")
    captured = bool(failed)
    return {
        "surviving": not captured,
        "captured_by_tests": captured,
        "status": "ok",
        "exit_code": exit_code,
        "failing_nodes": failed,
    }


# ── 第 4 步：重复采样的三态判定（B0 的 3.4）──

# 「没有返工」的标志：Route 指向归档，即缺陷被放过。
_PASS_THROUGH_ROUTE = "05-archive"

VERDICT_NOT_DETECTED = "not_detected"
VERDICT_DETECTED = "detected"
VERDICT_UNSTABLE = "unstable"
VERDICT_UNAVAILABLE = "unavailable"


def classify_runs(route_targets: List[str]) -> str:
    """把 N 次运行的 Route 归成三态 —— 加上「未测量」共四种。

    ⚠️ **`not_detected` 是对照组的期望结果。** 改造前的 04-review 只有
    单 reviewer，注入存活变异后它应当毫无反应、照原样放行到 05-archive。
    看到全 `05-archive` 时不要去「修」采集脚本（B0 的 9.4）。

    `unstable` 是刻意保留的第三态：LLM 采样波动会让「偶然发现」看起来
    像能力，二态化会把波动直接记成检出，虚高整个基线。
    """
    runs = [r for r in (route_targets or []) if r]
    if not runs:
        # 一次都没跑成 ≠ 跑了但没发现。后者才是基线的正确结果，
        # 混淆两者会让一次失败的采集看起来像一份成功的基线。
        return VERDICT_UNAVAILABLE

    rerouted = [r for r in runs if r != _PASS_THROUGH_ROUTE]
    if not rerouted:
        return VERDICT_NOT_DETECTED
    if len(rerouted) == len(runs):
        return VERDICT_DETECTED
    return VERDICT_UNSTABLE


def detection_rate(samples: List[Dict[str, Any]]) -> Optional[float]:
    """检出率。**`0.0` 是预期结果，`None` 才是「没测到」。**

    分母只算「存活且判定明确」的样本：

    * `surviving is not True` —— 被现有测试抓住的不算（验收 8）；
    * `unstable` / `unavailable` —— 判定不明确的不算（3.4）。

    这一对区分（0.0 与 None）是本任务最需要守住的东西：
    正确结果恰好是 0.0，若「未测量」也写 0.0，
    这份不可重采的数据就无法自证它到底测过没有。
    """
    countable = [s for s in (samples or [])
                 if s.get("surviving") is True
                 and s.get("verdict") in (VERDICT_NOT_DETECTED,
                                          VERDICT_DETECTED)]
    if not countable:
        return None
    detected = sum(1 for s in countable
                   if s.get("verdict") == VERDICT_DETECTED)
    return detected / len(countable)


# ── 第 5 步：归档落盘与只读保护（B0 的 3.2 / 第 10 节）──
#
# 这份产物**无法重新生成**，且严禁删除。因此这一层的重点不是「写出去」，
# 而是「防住被写坏」：落盘即只读，同日重采另建文件，绝不静默合并。

PROBE_DIR = ROOT / "workspace" / "probe"


def archive_path(captured_at: Optional[str] = None) -> Path:
    """归档文件路径：`baseline-<date>.json`，已存在则加序号后缀。

    加序号而不是覆盖：覆盖会让第一次采集的数据永久消失，
    而它对应的那个「改造前」时点再也回不去了（验收 11）。
    """
    stamp = (captured_at or "")[:10] or _today()
    base = PROBE_DIR / f"baseline-{stamp}.json"
    if not base.exists():
        return base
    n = 2
    while True:
        candidate = PROBE_DIR / f"baseline-{stamp}-{n}.json"
        if not candidate.exists():
            return candidate
        n += 1


def _today() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def write_archive(payload: Dict[str, Any],
                  captured_at: Optional[str] = None) -> Path:
    """落盘并设为只读。

    mock 下强制把 `detection_rate` 写成 `None`（JSON 的 null）：
    mock 的 reviewer 对任何变异都给同样输出，采到的是夹具而非能力
    （B0 第 4 节）。**不能写 0.0** —— 那个值在真实模式下是有意义的
    测量结果（「测了，没检出」），而 mock 下是「根本没测」。
    两者混为一谈，这份不可重采的数据就无法自证测过没有。
    """
    record = dict(payload)
    record.setdefault("captured_at", captured_at or _now_iso())
    record["read_only"] = True

    env = dict(record.get("env") or {})
    if is_mock_agent():
        env["mode"] = "mock"
        record["detection_rate"] = None
        record["mock_notice"] = (
            "mock 模式下 reviewer 产出固定文本，对任何变异给同样输出 —— "
            "本文件不是有效基线，detection_rate 为 null 表示未测量。")
    else:
        env.setdefault("mode", "real")
    record["env"] = env

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    target = archive_path(record.get("captured_at"))
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    _make_read_only(target)
    return target


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _make_read_only(path: Path) -> None:
    """去掉写位。失败不抛 —— 只读是保护措施，拿不到它也不该丢掉数据。"""
    try:
        mode = path.stat().st_mode
        path.chmod(mode & ~0o222)
    except OSError:
        pass


# ── 编排：把五步串起来（B0 的 3.1）──

def build_sample(sample_id: str, rel_path: str, operator: str,
                 survival: Dict[str, Any],
                 route_targets: List[str],
                 reviewer_mentioned_defect: Optional[bool] = None,
                 ) -> Dict[str, Any]:
    """一条样本记录。

    `reviewer_mentioned_defect` 默认 `None` 而不是 `False`：
    它是**人工标注**项（B0 的 3.5 定案不做自动解析）。默认 `False`
    会让一份从未标注的基线看起来像「已确认 reviewer 没提到缺陷」——
    那是伪造出来的结论，而这份数据不可重采。

    被现有测试抓住的变异**也留在归档里**：A11 需要知道哪些算子在这个
    项目上不存活，否则它会重复尝试同一批无效样本。
    """
    return {
        "id": sample_id,
        "file": rel_path,
        "operator": operator,
        "surviving": survival.get("surviving"),
        "captured_by_tests": survival.get("captured_by_tests"),
        "survival_status": survival.get("status"),
        "runs": len(route_targets or []),
        "route_targets": list(route_targets or []),
        "verdict": classify_runs(route_targets or []),
        "reviewer_mentioned_defect": reviewer_mentioned_defect,
        "annotation_pending": reviewer_mentioned_defect is None,
        # 对照组没有结构化产出 —— 那是 A7/A8 才有的东西。恒为 0 是事实，
        # 不是缺失：改造前根本不存在产出反例或设计意见的轨道。
        "counterexamples": 0,
        "opinions": 0,
    }


def capture(samples: Optional[List[Dict[str, Any]]] = None,
            partial: bool = False) -> Dict[str, Any]:
    """组装一份完整的基线归档（不落盘，落盘走 `write_archive`）。

    ⚠️ **`detection_rate == 0.0` 是本函数的期望结果。**
    对照组是改造前的 04-review —— 单 reviewer、无客观轨、无攻击者、
    无设计审视者、无仲裁器。注入存活变异后它应当照原样放行。

    若你看到非 0 的检出率，**先怀疑采集脚本，不要去调注入方式或 prompt**
    让基线「变好看」。那一刻这份不可重采的数据就被毁掉了，
    而且毁掉的方式是「看起来变好了」（B0 的 9.4）。
    """
    rows = list(samples or [])
    precheck = check_review_side_clean()

    record: Dict[str, Any] = {
        "captured_at": _now_iso(),
        "baseline_purity": precheck.get("baseline_purity"),
        "purity_detail": precheck.get("purity_detail"),
        "review_side_check": {
            "clean": precheck.get("clean"),
            "contaminated_dimensions": precheck.get("contaminated_dimensions"),
        },
        "env": env_fingerprint(),
        "samples": rows,
        "detection_rate": detection_rate(rows),
        "sample_count": len(rows),
        "expected_result_note": (
            "detection_rate 为 0.0 是本基线的**预期结果**：改造前的 04-review "
            "只有单个 reviewer，注入存活变异后应当毫无反应。"
            "非 0 时应先怀疑采集脚本，不得通过调整注入方式或 prompt "
            "让基线『变好看』（B0 的 9.4）。"),
    }
    if partial:
        # 逐样本增量落盘后中断的情形（R4）。部分数据不能冒充完整基线。
        record["partial"] = True
    return record


# ── CLI：`python3 -m sw_lib.probe.baseline` ──
#
# 第 1 步（前置检查 + 环境指纹）必须能**单独**跑，这是刻意的：
# 即便后面的采集来不及做，也要先把「采集时刻 A6-A9 尚未实施」
# 这个事实钉在时间线上 —— 那是事后无法补证的（B0 第 6 节）。

_USAGE = """用法:
  python3 -m sw_lib.probe.baseline --precheck
      只做前置检查与环境指纹，打印结果不落盘。

  python3 -m sw_lib.probe.baseline --fingerprint
      把「采集时刻的状态」落盘为一份零样本基线（可先跑，事后补样本）。

  python3 -m sw_lib.probe.baseline --sites <file.py> [--operator M2]
      列出某个文件里可用的变异位点（不写入任何东西）。

⚠️ 本采集的期望结果是「什么都没检出」。detection_rate 为 0.0 是成功。
   看到非 0 时先怀疑采集脚本，不要去让基线『变好看』（B0 的 9.4）。"""


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else __import__("sys").argv[1:])

    if not args or "--help" in args or "-h" in args:
        print(_USAGE)
        return 0

    if "--precheck" in args:
        precheck = check_review_side_clean()
        env = env_fingerprint()
        print(json.dumps({"review_side_check": precheck, "env": env},
                         ensure_ascii=False, indent=2))
        if not precheck.get("clean"):
            # 污染**不阻止**采集：一份标注了污染维度的基线远好过没有基线
            # （B0 的 2.1）。所以这里只警告，退出码仍是 0。
            print("\n⚠️ 审查侧已有改造落地，污染维度: "
                  f"{precheck.get('contaminated_dimensions')}\n"
                  "   仍应继续采集 —— 标注了污染的基线远好过没有基线。")
        return 0

    if "--fingerprint" in args:
        record = capture(samples=[], partial=True)
        target = write_archive(record)
        print(f"已落盘（零样本，partial）: {target}")
        print(f"  purity   : {record.get('baseline_purity')}")
        print(f"  git_sha  : {record['env'].get('git_sha')}")
        print(f"  git_dirty: {record['env'].get('git_dirty')}")
        print(f"  mode     : {record['env'].get('mode')}")
        print("  detection_rate: "
              f"{record.get('detection_rate')}（None = 未测量，非 0.0）")
        return 0

    if "--sites" in args:
        from . import injector
        idx = args.index("--sites")
        if idx + 1 >= len(args):
            print("--sites 需要一个文件路径", file=__import__("sys").stderr)
            return 2
        path = Path(args[idx + 1])
        operator = "M2"
        if "--operator" in args:
            operator = args[args.index("--operator") + 1]
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"读不到文件: {e}", file=__import__("sys").stderr)
            return 2
        sites = injector.find_sites(source, operator)
        print(f"{path} 的 {operator} 位点: {len(sites)} 个")
        for site in sites:
            print(f"  行 {site.get('lineno')}: "
                  f"{site.get('original')} → {site.get('mutated')}")
        return 0

    print(_USAGE, file=__import__("sys").stderr)
    return 2


if __name__ == "__main__":       # pragma: no cover - CLI 入口
    raise SystemExit(main())
