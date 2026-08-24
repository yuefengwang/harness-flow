"""sw_lib.workflow.objective_check — A6 客观轨：纯程序判定，不含 LLM。

04-review 现在把客观核查与主观判断混在一个 agent 里。客观的那部分
**完全不需要 LLM**：测试绿没绿、README 齐不齐、改动有没有越界，
这些都是确定性的程序判定。让 LLM 参与只会引入两个害处：
它可能把「测试失败」读成「测试基本通过」，而且同一份输入两次运行可得不同答案。

本模块**不产出自然语言结论、不给出 Route**（A6 第 4 节的输出契约）。
一旦它开始写「建议返工」，A9 就会去读那句话而不是读结构化字段，
仲裁又退化成读散文 —— 那正是整套设计要摆脱的形态。

三条贯穿全模块的纪律：

1. **三态，不得二态化**（A6 的 3.3）。前提缺失时记 `unavailable`，
   **绝不计为通过**。「找不到就跳过」在现有 hook 里反复出现且已造成多次
   误判（`check_04-review.sh` 与 `lib_run_tests.sh` 的历史注释均有记录）。
2. **严重性与 Route 无关**（A6 的 3.2）。现有 `check_04-review.sh:153` 是
   `if ROUTE_VAL = 05-archive && README_ERRORS > 0` —— Route 决定严重性、
   严重性又决定 Route。本模块一律按最严标准判，返工时的宽容由 A9 的
   优先级顺序体现。
3. **不猜数字**。测试计数解析失败时按 `unavailable` 处理，
   不写 `except: return 0` —— 那会让 0 collected 被下游当成真实事实，
   比拿不到数字危险得多。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

# skip 比例超过它记 warn（不是 fail）。fail 会让存量任务大面积挂，
# 而 A6 的 R1/9.3 要求「真实检出」与「误判」逐个分辨，
# 不得用放宽阈值让存量变绿 —— 但也不该用一刀切的 fail 制造一堆待分辨项。
SKIP_RATIO_WARN = 0.5

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_WARN = "warn"
VERDICT_UNAVAILABLE = "unavailable"

SEVERITY_HIGH = "high"
SEVERITY_MED = "med"


def find_check(result: Dict[str, Any], check_id: str) -> Dict[str, Any]:
    """按 id 取一项检查。取不到返回空字典而不是抛 —— 调用方多是断言。"""
    for c in result.get("checks", []):
        if c.get("id") == check_id:
            return c
    return {}


def _which(name: str) -> Optional[str]:
    """独立成函数是为了可被测试替换：验收 4 要构造「工具缺失」。"""
    import shutil
    return shutil.which(name)


def _check(check_id: str, name: str, verdict: str,
           detail: str = "", severity: Optional[str] = None,
           reason: str = "") -> Dict[str, Any]:
    row: Dict[str, Any] = {"id": check_id, "name": name, "verdict": verdict,
                           "detail": detail}
    if severity:
        row["severity"] = severity
    if reason:
        row["reason"] = reason
    return row


# ── O2 / O3：测试 ──

def _tests_checks(target: Path) -> List[Dict[str, Any]]:
    """消费 A3 的事实包采集结果，只做判定。

    刻意不自己跑 pytest：采集已经处理了 src-layout 的 PYTHONPATH 与项目
    自带 `.venv`（A3 的 2.6 / A2 的 5.3）。重写一遍会得出与 agent 相反的
    结论 —— 任务 T2 就是这么卡死的。
    """
    from .fact_pack import collect_tests

    facts = collect_tests(str(target))
    status = facts.get("parse_status")
    collected = facts.get("collected")
    skipped = facts.get("skipped")
    failed = facts.get("failed")

    # 「没有测试面」不是「测试通过」。现有 run_project_tests 在这种情形下
    # 整段 return 0，于是零测试的退出码 5 永远不会出现 —— 即「没测试」
    # 当前不是被误判，而是根本没被检查（A3 的 2.4）。
    if status == "no_test_surface":
        return [
            _check("O2", "tests", VERDICT_UNAVAILABLE,
                   reason="target_dir 下没有 pytest 语义的测试面"),
            _check("O3", "test_validity", VERDICT_FAIL,
                   detail="collected=0（无测试面）", severity=SEVERITY_HIGH),
        ]

    if status in ("unavailable", "unparsed") or collected is None:
        # 解析不出计数就不判 —— 不猜数字。
        return [
            _check("O2", "tests", VERDICT_UNAVAILABLE,
                   reason=f"测试结果不可得（parse_status={status}）"),
            _check("O3", "test_validity", VERDICT_UNAVAILABLE,
                   reason=f"无法取得测试计数（parse_status={status}）"),
        ]

    o2 = (_check("O2", "tests", VERDICT_FAIL,
                 detail=f"{failed} failed / {collected} collected",
                 severity=SEVERITY_HIGH)
          if failed else
          _check("O2", "tests", VERDICT_PASS,
                 detail=f"{facts.get('passed')} passed"))

    if collected == 0:
        o3 = _check("O3", "test_validity", VERDICT_FAIL,
                    detail="collected=0", severity=SEVERITY_HIGH)
    elif skipped is not None and skipped == collected:
        # 全部 skip 时 pytest 返回 0 —— 退出码判定的盲区（A6 的 3.1）。
        o3 = _check("O3", "test_validity", VERDICT_FAIL,
                    detail=f"collected={collected} 全部 skipped",
                    severity=SEVERITY_HIGH)
    elif skipped and collected and (skipped / collected) > SKIP_RATIO_WARN:
        o3 = _check("O3", "test_validity", VERDICT_WARN,
                    detail=f"skipped={skipped}/{collected} 超过阈值 "
                           f"{SKIP_RATIO_WARN}", severity=SEVERITY_MED)
    else:
        o3 = _check("O3", "test_validity", VERDICT_PASS,
                    detail=f"collected={collected} skipped={skipped}")

    return [o2, o3]


# ── O5：改动范围 ──

def _scope_check(target: Path,
                 files_touched: Optional[List[str]]) -> Dict[str, Any]:
    """声明的改动是否都落在 target_dir 内。

    没有声明时记 `unavailable` 而不是 pass：无从判定与判定通过是两件事，
    混为一谈会让「agent 什么都没声明」变成一次免费的通过。
    """
    if not files_touched:
        return _check("O5", "scope", VERDICT_UNAVAILABLE,
                      reason="03 未声明 files_touched，无从判定改动范围")

    root = target.resolve()
    outside = []
    for rel in files_touched:
        p = Path(rel)
        # 相对路径按 target_dir 解析；绝对路径直接看是否在其下。
        cand = p if p.is_absolute() else (root / p)
        try:
            cand.resolve().relative_to(root)
        except (ValueError, OSError):
            # 声明常带 target_dir 自身前缀（"scoped/app.py"），
            # 那种形态也算在内。
            try:
                (root.parent / p).resolve().relative_to(root)
            except (ValueError, OSError):
                outside.append(rel)

    if outside:
        return _check("O5", "scope", VERDICT_FAIL,
                      detail=f"越界改动 {len(outside)} 项: {sorted(outside)}",
                      severity=SEVERITY_HIGH)
    return _check("O5", "scope", VERDICT_PASS,
                  detail=f"{len(files_touched)} 项均在 target_dir 内")


# ── O6：README ──

def _readme_check(target: Path) -> Dict[str, Any]:
    """README 一律按最严标准判，**与 Route 无关**（A6 的 3.2）。

    这是本模块存在的一个直接理由：现有 hook 让缺 README 在归档路由下是
    错误、返工路由下是警告，形成「Route 决定严重性、严重性又决定 Route」
    的闭环。判定不该知道 Route 是什么。
    """
    f = target / "README.md"
    if not f.is_file():
        return _check("O6", "readme", VERDICT_FAIL,
                      detail=f"{target.name}/ 下无 README.md",
                      severity=SEVERITY_HIGH)
    body = f.read_text(encoding="utf-8", errors="replace")
    if not body.strip():
        return _check("O6", "readme", VERDICT_FAIL, detail="README.md 为空",
                      severity=SEVERITY_HIGH)

    holes = sum(body.count(t) for t in ("TODO", "___", "FIXME"))
    if holes:
        return _check("O6", "readme", VERDICT_WARN,
                      detail=f"README 含 {holes} 个占位符",
                      severity=SEVERITY_MED)
    return _check("O6", "readme", VERDICT_PASS, detail=f"{len(body)} 字")


# ── O8：覆盖率 ──

def _coverage_check(target: Path) -> Dict[str, Any]:
    """diff-cover 未安装时记 `unavailable`，不计通过、不阻断（验收 4）。"""
    tool = _which("diff-cover")
    if not tool:
        return _check("O8", "coverage", VERDICT_UNAVAILABLE,
                      reason="diff-cover 未安装")
    return _check("O8", "coverage", VERDICT_UNAVAILABLE,
                  reason="diff-cover 已安装但覆盖率基线未接入（留给 A11）")


# ── 编排 ──

def run_checks(target_dir: str,
               route: Optional[str] = None,
               files_touched: Optional[List[str]] = None,
               ) -> Dict[str, Any]:
    """跑全部客观检查，返回结构化结果供 A9 消费。

    `route` 参数**刻意接收但不使用**。保留它是为了让调用方（04-review 的
    hook）无需区分调用形态，而签名里的存在本身就是一句声明：
    判定不看 Route。守护测试 `test_route_is_not_an_input_to_severity`
    钉住这一点 —— 传任意 route 结果必须完全一致。
    """
    target = Path(target_dir)
    checks: List[Dict[str, Any]] = []

    if not target.is_dir():
        # 前提缺失。不得静默通过 —— 这是 A6 的 3.3 明令禁止的形态。
        for cid, name in (("O2", "tests"), ("O3", "test_validity"),
                          ("O5", "scope"), ("O6", "readme"),
                          ("O8", "coverage")):
            checks.append(_check(cid, name, VERDICT_UNAVAILABLE,
                                 reason=f"target_dir 不存在: {target_dir}"))
        # 目标目录都不存在，客观核查整体无法进行 —— 这本身是硬失败。
        checks.append(_check("O1", "preconditions", VERDICT_FAIL,
                             detail=f"target_dir 不存在: {target_dir}",
                             severity=SEVERITY_HIGH))
        return _finalize(checks)

    checks.append(_check("O1", "preconditions", VERDICT_PASS,
                         detail=str(target)))
    checks.extend(_tests_checks(target))
    checks.append(_scope_check(target, files_touched))
    checks.append(_readme_check(target))
    checks.append(_coverage_check(target))
    return _finalize(checks)


def _finalize(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总。`hard_fail_ids` 只收「高危且失败」项。

    warn 不进 hard_fail，`unavailable` 也不进 —— 后者不是「通过」，
    但它也不该阻断流程：无法执行的检查不构成缺陷证据。
    它的去处是 A10 报告的「不确定」分类。
    """
    counts = {v: 0 for v in (VERDICT_PASS, VERDICT_FAIL, VERDICT_WARN,
                             VERDICT_UNAVAILABLE)}
    for c in checks:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1

    hard = sorted(c["id"] for c in checks
                  if c["verdict"] == VERDICT_FAIL
                  and c.get("severity") == SEVERITY_HIGH)
    return {
        "checks": checks,
        "counts": counts,
        "hard_fail": bool(hard),
        "hard_fail_ids": hard,
    }
