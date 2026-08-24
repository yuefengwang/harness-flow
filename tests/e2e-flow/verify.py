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

    def output_region(self, stage: str) -> str:
        """按 sw 写的围栏取出产出区正文；没有产出区返回空串。

        不能按 ``## 🤖 AI Output`` 标题 split：那个标题在文件里出现两次
        （sw 写的那个 + agent 在正文里自己写的那个），split 出来的 ``[1]``
        只是夹在两者之间的围栏注释行，正文全在后面。围栏标记带 nonce、
        agent 猜不到，是唯一可靠的边界。
        """
        content = self.stage_file(stage)
        m = re.search(r"<!-- sw:ai-output:start ([0-9a-f]{8,}) -->", content)
        if not m:
            return ""
        end = f"<!-- sw:ai-output:end {m.group(1)} -->"
        e = content.rfind(end)
        if e < m.end():
            return ""
        return content[m.end():e]

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
            # 产出区里的 WBS 条目必须保持 `[ ]` 原样。
            #
            # 这条判据守的是「产出区不被全局 [ ]→[x] 替换污染」（历史事故：
            # 模板 checkbox 的勾选逻辑误伤了 AI Output 区）。
            #
            # 改前它有两个 bug 叠在一起，合起来让判据恒真：
            #   1. ok 参数写死成 True —— 数出几条都记 [✓]；
            #   2. 按 `## 🤖 AI Output` 标题 split 取 [1] —— 那个标题出现两次
            #      （sw 写的 + agent 正文里自己写的），[1] 只是中间那行围栏
            #      注释，正文一个字都没取到。
            # 症状是它打印「0 unchecked WBS items」而 mock 明明输出了 3 条。
            region = self.output_region("02-planning")
            self.check("02-planning", "Output region located by fence",
                       bool(region.strip()),
                       "围栏内取不到产出正文" if not region.strip() else "")
            if region.strip():
                unchecked = re.findall(r"^\d+\.\s*\[ \]", region, re.MULTILINE)
                checked = re.findall(r"^\d+\.\s*\[[xX]\]", region, re.MULTILINE)
                self.check("02-planning", "WBS items preserved unchecked",
                           bool(unchecked) and not checked,
                           f"{len(unchecked)} unchecked / {len(checked)} checked"
                           + ("（产出区的 WBS 被勾选了，疑似全局替换误伤）"
                              if checked else ""))

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

        # 03 的结构化声明（A3 的 3.5）。
        #
        # 原判据只要求「字段存在」，理由写的是「mock 未必填写」—— 那条判据
        # 让空 claims 一路绿到底：实测落盘的是 {'task_ids': [], 'verify_cmd':
        # '', 'files_touched': []}，字段在、内容空。空 claims 与「没有 claims」
        # 在下游同义，验收 18 的 claims-vs-diff 对照一次都不会触发。
        # MockAgent 现已产出结构化声明段，故判据升级为**内容非空**。
        claims = ((self.state().get("stages") or {})
                  .get("03-coding", {}).get("claims"))
        self.check("facts", "03 claims recorded in .state",
                   isinstance(claims, dict),
                   f"claims={claims}" if claims is not None else "claims 字段缺失")

        if isinstance(claims, dict):
            self.check("facts", "claims declare a task id",
                       bool(claims.get("task_ids")),
                       f"task_ids={claims.get('task_ids')}")
            self.check("facts", "claims declare a verify cmd",
                       bool(claims.get("verify_cmd")),
                       f"verify_cmd={claims.get('verify_cmd')!r}")

            declared = claims.get("files_touched") or []
            self.check("facts", "claims declare files touched",
                       bool(declared), f"files_touched={declared}")

            # 声明必须对得上真实产出。多报比漏报更危险：对照会通过，
            # 而它对照的是假数据。
            target = str(self.state().get("target_dir") or "").strip()
            if declared and target and target != ".":
                tdir = Path(target)
                if not tdir.is_absolute():
                    tdir = ROOT / tdir
                if tdir.is_dir():
                    actual = {p.name for p in tdir.iterdir() if p.is_file()}
                    fabricated = {d.split("/")[-1] for d in declared} - actual
                    self.check("facts", "no fabricated files in claims",
                               not fabricated,
                               f"声明了未写入的文件: {sorted(fabricated)}"
                               if fabricated else f"{len(declared)} 项均对得上")

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
