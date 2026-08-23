#!/usr/bin/env python3
"""验收脚本 — 读取任务目录，逐阶段检查 acceptance.md 中的验收标准。

用法:
    python3 tests/e2e-flow/verify.py --task-dir workspace/tasks/e2e-xxxxx

输出:
    输出 JSON 格式的验收报告到 stdout 和 .verify-report.json
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = ROOT / "hooks"
STAGES = ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]


class Verifier:
    """验收器：逐阶段检查。"""

    def __init__(self, task_dir: Path):
        self.task_dir = task_dir
        self.name = task_dir.name
        self.results: List[dict] = []
        self.errors: int = 0

    def check(self, category: str, item: str, ok: bool, detail: str = ""):
        """记录一条检查结果。"""
        self.results.append({
            "category": category,
            "item": item,
            "ok": ok,
            "detail": detail,
        })
        if not ok:
            self.errors += 1
            status = "✗"
        else:
            status = "✓"
        print(f"  [{status}] {item}" + (f" — {detail}" if detail else ""))

    def stage_file(self, stage: str) -> str:
        f = self.task_dir / f"{stage}.md"
        return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""

    def state(self) -> dict:
        sf = self.task_dir / ".state"
        if sf.exists():
            try:
                return json.loads(sf.read_text())
            except Exception:
                return {}
        return {}

    def gate_signed(self, stage: str) -> Tuple[bool, str]:
        """该阶段的 Gate 是否已签署。判定源是 `.state`，不是 Markdown。

        文件里的 `## Gate` 区只是状态的映像（由 sw 单向渲染），agent 的产出
        也可能包含同样的字样 —— 拿它做验收等于验收一个可被伪造的东西。
        """
        gate = ((self.state().get("stages") or {})
                .get(stage, {}).get("gate") or {})
        items = gate.get("items") or []
        if not items:
            return False, "no gate items in .state"
        pending = [i for i in items if not i.get("checked")]
        if pending:
            return False, f"{len(pending)} unsigned"
        return True, f"signed_by={gate.get('signed_by')}"

    def route_value(self) -> str:
        """04-review 的 Route 决策（归一化的小写阶段名）。

        推进离开 04-review 时当前决策会被作废并归档到 ``route_history``
        （否则上一轮的 target 会一直生效，造成 03↔04 死循环）。所以事后验收
        要回看历史里最后一条 —— 那才是"这一轮实际怎么路由的"。
        """
        bucket = (self.state().get("stages") or {}).get("04-review", {})
        route = bucket.get("route") or {}
        if route.get("target"):
            return route["target"]
        history = bucket.get("route_history")
        if isinstance(history, list):
            for entry in reversed(history):
                if isinstance(entry, dict) and entry.get("target"):
                    return entry["target"]
        return ""

    def log_content(self) -> str:
        lf = self.task_dir / ".log"
        if lf.exists():
            return lf.read_text(encoding="utf-8", errors="replace")
        return ""

    def run_hook(self, stage: str) -> Tuple[int, str]:
        """运行 hook 脚本，返回 (exit_code, output)。"""
        hook = HOOKS_DIR / f"check_{stage}.sh"
        if not hook.exists():
            hook = HOOKS_DIR / f"post_check_{stage}.sh"
        if not hook.exists():
            return (-1, "Hook not found")
        try:
            r = subprocess.run(
                [str(hook), self.name],
                cwd=str(ROOT),
                capture_output=True, text=True, timeout=30,
            )
            return (r.returncode, r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            return (-1, "Timeout")
        except Exception as e:
            return (-1, str(e))

    def verify(self) -> bool:
        """执行全部验收检查。"""
        st = self.state()
        log = self.log_content()

        print(f"\n{'='*50}")
        print(f"Verifying: {self.name}")
        print(f"{'='*50}")

        # ── 通用检查 ├─
        print(f"\n--- General ---")
        for stage in STAGES:
            sf = self.stage_file(stage)
            exists = bool(sf)
            self.check(stage, "Stage file exists", exists)
            if exists:
                has_ai = "🤖 AI Output" in sf
                self.check(stage, "AI Output present", has_ai)

                # 门禁签署：读 .state
                signed, detail = self.gate_signed(stage)
                self.check(stage, "Gate signed in .state", signed, detail)

                # 渲染出来的 Gate 区应与状态一致（人读文件能看出签署结果）
                pos = sf.rfind("## Gate")
                if pos >= 0 and signed:
                    gate_body = sf[pos:]
                    self.check(stage, "Rendered Gate reflects signature",
                               "[ ]" not in gate_body,
                               "已签署但渲染出未勾选项" if "[ ]" in gate_body else "")

            # 硬校验
            hook_ec, hook_out = self.run_hook(stage)
            if hook_ec >= 0:
                self.check(stage, f"Hard check ({stage})",
                           hook_ec == 0,
                           f"exit={hook_ec}" if hook_ec != 0 else "")

        # ── 阶段推进检查 ──
        print(f"\n--- Stage Progression ---")
        current_stage = st.get("stage", "unknown")
        stage_status = st.get("stage_status", "unknown")
        stage_idx = int(st.get("stage_idx", 0))
        self.check("progression", "Final stage status",
                   stage_status == "Finished" or (stage_idx >= len(STAGES) - 1 and stage_status == "Finished"),
                   f"stage={current_stage}, status={stage_status}")

        # 检查是否有 error 日志
        error_lines = [l for l in log.splitlines() if "error" in l.lower()]
        # 过滤掉已知的非关键错误
        known_errors = ["deploy", "Failed to parse"]
        critical_errors = [
            e for e in error_lines
            if not any(k in e.lower() for k in known_errors)
        ]
        self.check("progression", "No critical errors in log",
                   len(critical_errors) == 0,
                   f"{len(critical_errors)} error lines" if critical_errors else "")

        # ── 阶段专项检查 ──
        print(f"\n--- Stage-Specific ---")

        # 01-brainstorming
        sf01 = self.stage_file("01-brainstorming")
        if sf01:
            # 「设计已批准」= Gate 已签署（读 .state），不是文件里有个 [x]
            approved, detail = self.gate_signed("01-brainstorming")
            self.check("01-brainstorming", "Design approved (gate signed)",
                       approved, detail)
            # 选项组是内容检查：用户拍板的方案必须被记录进产出
            unresolved = len(re.findall(r"-\s*\*\*Chosen\*\*:\s*_+\s*$",
                                        sf01, re.MULTILINE))
            self.check("01-brainstorming", "Choice groups resolved",
                       unresolved == 0,
                       f"{unresolved} unresolved **Chosen** placeholders")

        # 02-planning
        sf02 = self.stage_file("02-planning")
        if sf02:
            # 检查 WBS 条目（AI Output 中的 [ ]）未被替换
            if "## 🤖 AI Output" in sf02:
                ai_part = sf02.split("## 🤖 AI Output")[1]
                wbs_unchecked = re.findall(r"^\d+\.\s*\[ \]", ai_part, re.MULTILINE)
                # 允许 AI Output 中有未勾选的 WBS 条目
                # （这些是 MockAgent 输出的，不是模板 checkbox）
                self.check("02-planning", "WBS items preserved (unchecked OK)",
                           True,
                           f"{len(wbs_unchecked)} unchecked WBS items (expected)")

        # 04-review
        sf04 = self.stage_file("04-review")
        if sf04:
            has_security = "## Security" in sf04
            self.check("04-review", "Security section exists", has_security)
            # Route 决策读 .state：写进 Markdown 的不算决定
            route_val = self.route_value()
            if route_val:
                self.check("04-review", "Route value valid",
                           route_val in STAGES,
                           f"Route={route_val}")
                # 返工路由才需要 Evidence 表（那是给人看的证据，仍读 Markdown）
                if route_val != "05-archive":
                    evidence_section = re.search(
                        r"### Reroute Evidence(.*?)(?=\n##|\Z)", sf04, re.DOTALL
                    )
                    if evidence_section:
                        ev_content = evidence_section.group(1)
                        data_rows = [l for l in ev_content.splitlines()
                                     if "|" in l and "---" not in l and "#" not in l]
                        has_data = any("___" not in l for l in data_rows)
                        self.check("04-review", "Reroute Evidence has data",
                                   has_data,
                                   f"{len(data_rows)} rows" if data_rows else "no data rows")
                    else:
                        self.check("04-review", "Reroute Evidence section exists",
                                   False)
            else:
                self.check("04-review", "Route decision recorded", False,
                           "no route in .state")

        # ── 事实包（A3）与 03 声明 ──
        # A3 交付时 e2e 完全不看 facts，事实包是否正常只有手工验证过 ——
        # 那不是自动回归。这里补上。
        self.verify_fact_pack()

        # ── 汇总 ──
        total = len(self.results)
        passed = total - self.errors
        print(f"\n{'='*50}")
        print(f"Results: {passed}/{total} passed, {self.errors} failed")
        print(f"{'='*50}")

        return self.errors == 0

    def verify_fact_pack(self):
        """04 的事实包必须真的生成、且 manifest 与磁盘一致（A3）。

        判据不是「facts 目录存在」—— 空目录也存在。要数文件、验哈希、
        并确认 03 的结构化声明真的落了盘。
        """
        print(f"\n--- Fact Pack (A3) ---")
        facts = self.task_dir / "facts"
        if not facts.is_dir():
            self.check("facts", "Fact pack generated", False, "facts/ 不存在")
            return

        manifest_file = facts / "manifest.json"
        self.check("facts", "manifest.json exists", manifest_file.is_file())
        if not manifest_file.is_file():
            return

        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as e:
            self.check("facts", "manifest.json parseable", False, str(e))
            return

        digests = manifest.get("files") or {}
        self.check("facts", "Fact files recorded in manifest",
                   len(digests) >= 8, f"{len(digests)} files")

        # 哈希逐个复算：manifest 声称的摘要必须与磁盘一致，
        # 否则「事实包未被篡改」这句话没有依据。
        mismatched = []
        missing = []
        for name, expect in digests.items():
            p = facts / name
            if not p.is_file():
                missing.append(name)
                continue
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            if actual != expect:
                mismatched.append(name)
        self.check("facts", "All manifest files present", not missing,
                   f"missing={missing}" if missing else "")
        self.check("facts", "Manifest hashes match disk", not mismatched,
                   f"mismatched={mismatched}" if mismatched else "")

        # tests.json 必须给出**显式**判据。三态不得二态化：mock 模式记
        # `parse_status: mock` 且 `ran: false`，那是「未执行」不是「通过」。
        tj = facts / "tests.json"
        if tj.is_file():
            try:
                tests = json.loads(tj.read_text(encoding="utf-8"))
                status = tests.get("parse_status")
                self.check("facts", "tests.json has explicit status",
                           status not in (None, ""), f"parse_status={status}")
                # `ran` 必须存在且是布尔 —— 缺失意味着无法分辨「跑了没发现问题」
                # 与「根本没跑」，而后者被当成前者正是要防的失效模式。
                self.check("facts", "tests.json declares whether it ran",
                           isinstance(tests.get("ran"), bool),
                           f"ran={tests.get('ran')}")
                if tests.get("ran") is False:
                    self.check("facts", "Not-run is not reported as pass",
                               tests.get("passed") is None
                               and status in ("mock", "unavailable", "not_run"),
                               f"status={status} passed={tests.get('passed')}")
            except Exception as e:
                self.check("facts", "tests.json parseable", False, str(e))

        # 03 的结构化声明（A3 的 3.5）。mock 场景下 MockAgent 未必填写，
        # 所以只要求「字段存在」而不要求非空 —— 但字段缺失说明没人调用
        # record_claims()，那是真缺口。
        claims = ((self.state().get("stages") or {})
                  .get("03-coding", {}).get("claims"))
        self.check("facts", "03 claims recorded in .state",
                   isinstance(claims, dict),
                   f"claims={claims}" if claims is not None else "claims 字段缺失")

    def save_report(self):
        """保存验收报告到任务目录。"""
        report = {
            "task": self.name,
            "passed": len(self.results) - self.errors,
            "total": len(self.results),
            "failed": self.errors,
            "results": self.results,
        }
        report_file = self.task_dir / ".verify-report.json"
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        return report


def main():
    parser = argparse.ArgumentParser(description="E2E Flow 验收脚本")
    parser.add_argument("--task-dir", required=True, type=Path,
                        help="任务目录路径 (workspace/tasks/<name>)")
    args = parser.parse_args()

    task_dir = args.task_dir.resolve()
    if not task_dir.exists():
        print(f"Error: {task_dir} not found")
        sys.exit(1)

    verifier = Verifier(task_dir)
    ok = verifier.verify()
    verifier.save_report()

    if ok:
        print(f"\n✅ All checks passed!")
    else:
        print(f"\n❌ {verifier.errors} check(s) failed — see details above")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
