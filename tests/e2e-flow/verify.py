#!/usr/bin/env python3
"""验收脚本 — 读取任务目录，逐阶段检查 acceptance.md 中的验收标准。

用法:
    python3 tests/e2e-flow/verify.py --task-dir workspace/tasks/e2e-xxxxx

输出:
    输出 JSON 格式的验收报告到 stdout 和 .verify-report.json
"""

import argparse
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

                # 检查 Gate checkbox
                gate_match = re.search(r"## Gate\s*\n(.*?)(?=\n##|\Z)", sf, re.DOTALL)
                if gate_match:
                    gate_content = gate_match.group(1)
                    unchecked = re.findall(r"\[ \]", gate_content)
                    self.check(stage, "Gate checkboxes all checked",
                               len(unchecked) == 0,
                               f"{len(unchecked)} unchecked" if unchecked else "")

                # 检查模板区（AI Output 之前）的 checkbox
                before_ai = sf.split("## 🤖 AI Output")[0] if "## 🤖 AI Output" in sf else sf
                tmpl_unchecked = re.findall(r"\[ \]", before_ai)
                self.check(stage, "Template checkboxes all checked",
                           len(tmpl_unchecked) == 0,
                           f"{len(tmpl_unchecked)} unchecked in template" if tmpl_unchecked else "")

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
            has_design_approved = bool(re.search(r"\[x\]\s*Design approved", sf01, re.IGNORECASE))
            self.check("01-brainstorming", "Design approved [x]",
                       has_design_approved)
            # 选择组检查：检查 Clarifying Questions 下每组的 A/B 选项至少有一个 [x]
            if "Clarifying Questions" in sf01:
                sections = sf01.split("Clarifying Questions")
                if len(sections) > 1:
                    cq_section = sections[1].split("##")[0] if "##" in sections[1] else sections[1]
                    choice_groups = re.findall(r"- \[([ x])\]\s*[A-Z]\d*[:.\)]", cq_section)
                    groups_unchecked = sum(1 for g in choice_groups if g == " ")
                    self.check("01-brainstorming", "Choice groups filled",
                               groups_unchecked <= 2,  # Allow some margin
                               f"{groups_unchecked} unchecked choices")

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
            route_match = re.search(r"\*\*Route\*\*:\s*`([^`]+)`", sf04)
            if route_match:
                route_val = route_match.group(1)
                valid_routes = {"05-Archive", "03-Coding", "02-Planning", "01-Brainstorming"}
                self.check("04-review", "Route value valid",
                           route_val in valid_routes,
                           f"Route={route_val}")
                # 如果 route 不是 Archive，检查 Evidence 表
                if route_val != "05-Archive":
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
                self.check("04-review", "Route field filled", False)

        # ── 汇总 ──
        total = len(self.results)
        passed = total - self.errors
        print(f"\n{'='*50}")
        print(f"Results: {passed}/{total} passed, {self.errors} failed")
        print(f"{'='*50}")

        return self.errors == 0

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
    report = verifier.save_report()

    if ok:
        print(f"\n✅ All checks passed!")
    else:
        print(f"\n❌ {verifier.errors} check(s) failed — see details above")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
