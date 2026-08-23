"""A3 事实包生成器 —— 让 04 阶段审查事实而不是审查自述。

全套设计的三个成因里，**C1 上下文污染**由本模块解决：developer 的结论以
权威文档形式传给 reviewer，reviewer 读到的是「我做完了，测试都过了」这类
自述，于是复核的对象是**叙述**而不是事实。

做法：04 阶段完全不读 `03-coding.md`。reviewer 拿到的全部输入由 harness
用确定性程序生成，agent 只读。

边界声明（A3 的 1.3，必须诚实）：本模块保证「事实由 harness 生成且 agent
未加工」，**不保证事实完整**。diff 只反映文件系统变化，agent 在会话里说过
什么、想过什么一概不在；`spec.md` 第三段本质仍是 agent 自述，只是被标了
低可信度。真正的规格判据来自 R8（可执行验收场景），本模块不假装解决。
"""

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import TASKS, is_mock_agent

#: `diff.patch` 的字节上限。超出即按风险排序截断，被截断的文件写入
#: `diff.truncated` 并进 warnings —— 截断必须可见（A3 的 3.8 第 4 条）。
PATCH_BYTE_LIMIT = 400_000

#: 事实包的固定构成。缺一个都算不完整（验收 1）。
FACT_FILES: Tuple[str, ...] = (
    "diff.stat", "diff.patch", "diff.numstat", "diff.truncated",
    "tests.json", "coverage.json", "spec.md", "plan.md",
)

MANIFEST_NAME = "manifest.json"

#: pytest 单轮执行上限。hook 侧已有 HOOK_TIMEOUT_SECONDS，这里独立设置是
#: 因为事实包也会被 04 入口直接调用，不总是经过 hook。
PYTEST_TIMEOUT = 120.0


def _facts_dir(task: str) -> Path:
    return Path(TASKS) / task / "facts"


def _task_dir(task: str) -> Path:
    return Path(TASKS) / task


def _project_python(target_dir: str) -> Optional[str]:
    """项目自己的解释器（与 `lib_run_tests.sh` 的 `_project_python` 同构）。

    优先用 `target_dir/.venv/bin/python`：PATH 上的 pytest 可能绑在另一个
    解释器上，跑出来的结果和 agent 看到的不一致。
    """
    base = Path(target_dir)
    for candidate in (base / ".venv" / "bin" / "python",
                      base / "venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate)
    return None

class FactPackError(Exception):
    """事实包生成失败。缺前提时抛此异常，不静默降级。"""


@dataclass
class FactPack:
    task: str = ""
    target_dir: str = ""
    baseline_sha: str = ""
    baseline_kind: str = ""
    files: Dict[str, Path] = field(default_factory=dict)
    truncated: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ── pytest 结果采集 ──

_SUMMARY_RE = re.compile(r"^=*\s*(?P<body>[^=]*?(?:passed|failed|error|skipped|"
                         r"no tests ran|xfailed|xpassed)[^=]*?)\s*=*$",
                         re.IGNORECASE | re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|"
                       r"deselected|warnings?)", re.IGNORECASE)


def parse_pytest_summary(output: str, exit_code: int) -> Dict[str, Any]:
    """解析 pytest 的摘要行。

    为什么不用 `pytest-json-report`（A3 的 2.6）：实测本机未安装该插件，
    而采集跑在**用户仓库的解释器**上（`.venv/bin/python` 优先），
    更不能假定插件存在。

    **解析失败一律记 `unparsed` 并保留原文，数字字段留 None，不猜数字**。
    写成 `except: return 0` 的话，0 collected 会被下游当成真实事实 ——
    那比拿不到数字危险得多。
    """
    result: Dict[str, Any] = {
        "collected": None, "passed": None, "failed": None, "skipped": None,
        "exit_code": exit_code, "parse_status": "unparsed",
        "raw": output[-4000:] if output else "",
    }

    matches = _SUMMARY_RE.findall(output or "")
    if not matches:
        return result

    body = matches[-1]
    if "no tests ran" in body.lower():
        result.update(collected=0, passed=0, failed=0, skipped=0,
                      parse_status="parsed")
        return result

    counts = {k.lower().rstrip("s"): int(n) for n, k in _COUNT_RE.findall(body)}
    if not counts:
        return result

    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0) + counts.get("error", 0)
    skipped = counts.get("skipped", 0)
    xfailed = counts.get("xfailed", 0) + counts.get("xpassed", 0)
    result.update(
        passed=passed, failed=failed, skipped=skipped,
        collected=passed + failed + skipped + xfailed,
        parse_status="parsed",
    )
    return result


def _has_pytest_surface(target: Path) -> bool:
    """target 下是否存在 pytest 语义的测试面。

    与 `lib_run_tests.sh` 的判据保持一致，否则会出现「hook 跑了、事实包说
    没跑」这类错位。
    """
    if (target / "pytest.ini").is_file() or (target / "pyproject.toml").is_file():
        return True
    if any(target.glob("test_*.py")) or any(target.glob("*_test.py")):
        return True
    for sub in ("tests", "test"):
        d = target / sub
        if d.is_dir() and (any(d.rglob("test_*.py")) or any(d.rglob("*_test.py"))):
            return True
    return False


def collect_tests(target_dir: str) -> Dict[str, Any]:
    """真实运行 pytest 并采集结构化结果。

    两个现存缺陷由本函数填补（A3 的 2.4 / 2.5）：

    - `run_project_tests` 在无测试文件时**整段跳过并返回 0（通过）**，
      pytest 压根没跑，所以零测试的退出码 5 永远不会出现。
      因此「有没有跑过」这件事本身要落盘（`ran` 字段），不能只留退出码。
    - 成功时 pytest 的输出被整个丢弃，collected/passed/skipped 全部丢失。

    「pytest 不可用」记 `unavailable` 而非硬失败（A3 的 3.9 分界线）：
    事实来源本身不可信才硬失败，某项事实不可得进 A6 的三态。
    **`unavailable` 绝不计为通过。**
    """
    target = Path(target_dir)
    py = _project_python(target_dir) or "python3"
    base: Dict[str, Any] = {
        "collected": None, "passed": None, "failed": None, "skipped": None,
        "exit_code": None, "parse_status": "unavailable", "ran": False,
        "raw": "", "python": py, "pytest_version": None,
    }

    if not target.is_dir():
        base["raw"] = f"target_dir 不存在: {target_dir}"
        return base

    version = _pytest_version(py, target)
    if version is None:
        base["raw"] = f"无法执行 {py} -m pytest --version"
        return base
    base["pytest_version"] = version

    if not _has_pytest_surface(target):
        # 没有测试面：明确记「未跑」，不报 0 passed —— 那看起来像跑过了。
        base["parse_status"] = "no_test_surface"
        base["raw"] = "target_dir 下没有 pytest 语义的测试面"
        return base

    try:
        proc = subprocess.run(
            [py, "-m", "pytest", "--tb=no", "-q"],
            cwd=str(target), capture_output=True, text=True,
            timeout=PYTEST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        base["raw"] = f"pytest 执行失败: {exc}"
        return base

    output = (proc.stdout or "") + (proc.stderr or "")
    result = parse_pytest_summary(output, proc.returncode)
    result.update(ran=True, python=py, pytest_version=version)
    return result


def _pytest_version(py: str, cwd: Path) -> Optional[str]:
    """探测 pytest 版本。R2：摘要行格式随版本变化，版本号须落盘便于定位。"""
    try:
        proc = subprocess.run([py, "-m", "pytest", "--version"],
                              cwd=str(cwd), capture_output=True, text=True,
                              timeout=30.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    m = re.search(r"(\d+\.\d+[\w.]*)", text)
    return m.group(1) if m else (text.splitlines()[0] if text else None)


def _collect_coverage(target_dir: str) -> Dict[str, Any]:
    """覆盖率。工具缺失记 `unavailable`（A3 的 3.9），不硬失败。"""
    return {"status": "unavailable",
            "note": "覆盖率采集未实施；阈值策略属 A6 的 O8"}


# ── spec.md 三段拼装 ──

_PLACEHOLDER_RE = re.compile(r"_{3,}")


def _extract_section(md: str, heading_keywords: Tuple[str, ...]) -> str:
    """取 markdown 中标题含任一关键词的那一段（到下一个同级标题为止）。"""
    if not md:
        return ""
    lines = md.splitlines()
    start = None
    level = 2
    for i, line in enumerate(lines):
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            if any(k.lower() in title for k in heading_keywords):
                start = i + 1
                level = len(line) - len(line.lstrip("#"))
                break
    if start is None:
        return ""
    body: List[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            depth = len(line) - len(line.lstrip("#"))
            if depth <= level:
                break
        body.append(line)
    return "\n".join(body).strip()


def _is_placeholder_only(section: str) -> bool:
    """段内每个字段值都是 `___` 时视为未填写。

    **不做部分保留** —— 半填的模板同样会误导 reviewer（A3 的 3.4）。
    """
    if not section.strip():
        return False
    values: List[str] = []
    for line in section.splitlines():
        if ":" in line:
            values.append(line.split(":", 1)[1].strip())
    if not values:
        return bool(_PLACEHOLDER_RE.search(section))
    return all(_PLACEHOLDER_RE.fullmatch(v or "") for v in values)


def _render_decisions(decisions: Any) -> str:
    if not decisions:
        return ""
    if isinstance(decisions, dict):
        items = [f"- **{k}**: {v}" for k, v in decisions.items()]
    elif isinstance(decisions, list):
        items = []
        for d in decisions:
            if isinstance(d, dict):
                q = d.get("question") or d.get("topic") or d.get("label") or "?"
                a = d.get("answer") or d.get("chosen") or d.get("value") or "?"
                items.append(f"- **{q}**: {a}")
            else:
                items.append(f"- {d}")
    else:
        items = [f"- {decisions}"]
    return "\n".join(items)


def build_spec(task: str, state: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    """拼装 `spec.md`，并返回 `spec_availability` 三态。

    A3 的 2.7 用真实任务 T1 实测：三段里**两段常为空** ——
    `decisions` 五个阶段全部为空（`record_decision` 只在用户实际回答时写入，
    agent 调了 question 但无人应答则一条不落盘），ADR 段全是 `___` 占位符。

    因此空与缺失必须区分，且**占位符不得原样喂给 reviewer** ——
    否则它会当成真实的设计陈述去审，产出的是对空气的评价。
    """
    context = (state.get("context") or "").strip()
    decisions = state.get("decisions")
    brainstorm_path = _task_dir(task) / "01-brainstorming.md"
    brainstorm = brainstorm_path.read_text(encoding="utf-8", errors="replace") \
        if brainstorm_path.is_file() else ""
    adr = _extract_section(brainstorm, ("design decision", "adr", "设计决策"))

    availability = {
        "context": "present" if context else "empty",
        "decisions": "present" if decisions else "empty",
        "adr": "present",
    }
    if not adr.strip():
        availability["adr"] = "empty"
    elif _is_placeholder_only(adr):
        availability["adr"] = "placeholder_only"

    decisions_text = _render_decisions(decisions)

    parts = [
        "# 需求与决策事实",
        "",
        "> 本文件由 harness 生成，developer 未参与编辑。",
        "> 各段可信度已标注，**低可信度段落不得作为判据**。",
        "",
        "## 1. 原始需求（可信度：高 —— 用户原文）",
        context or "（用户未提供上下文）",
        "",
        "## 2. 已拍板决策（可信度：高 —— 用户经 ask_user 确认）",
        decisions_text or
        "（本任务无用户拍板记录 —— 可能是 ask_user 未被触发或无人应答）",
        "",
        "## 3. 设计陈述（可信度：低 —— agent 自述，仅作参考）",
    ]
    if availability["adr"] == "present":
        parts.append(adr)
    elif availability["adr"] == "placeholder_only":
        parts.append("（agent 未填写设计决策 —— 模板占位符原样留存）")
    else:
        parts.append("（agent 未填写设计决策）")
    parts.append("")
    return "\n".join(parts), availability


def build_plan(task: str) -> str:
    """从 `02-planning.md` 提取 Task DAG 与技术细节。**仅供设计轨**。

    设计审查需要对照物，否则退化为个人偏好（A3 的 3.2 脚注）。
    R7：结构化字段缺失时渲染显式空态，不静默交空文件。
    """
    path = _task_dir(task) / "02-planning.md"
    md = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    dag = _extract_section(md, ("task dag", "任务", "dag"))
    tech = _extract_section(md, ("tech detail", "技术"))
    strategy = _extract_section(md, ("test strategy", "测试策略"))

    def render(title: str, body: str) -> List[str]:
        if not body.strip():
            return [f"## {title}", "（规划阶段未产出该段）", ""]
        if _is_placeholder_only(body):
            return [f"## {title}", "（规划阶段仅留模板占位符，未实际填写）", ""]
        return [f"## {title}", body, ""]

    parts = [
        "# 规划事实（仅设计审查轨可见）",
        "",
        "> 由 harness 从 02-planning.md 提取。**不含 03-coding.md 的任何内容。**",
        "",
    ]
    parts += render("Task DAG", dag)
    parts += render("测试策略", strategy)
    parts += render("技术细节", tech)
    return "\n".join(parts)


# ── diff 截断与风险排序 ──

def _risk_rank(path: str) -> int:
    """风险序：生产代码 0 > 配置 1 > 测试 2 > 文档 3（A3 的 3.8 第 2 条）。"""
    p = path.lower()
    if "/test" in p or p.startswith("test") or "_test." in p or "/tests/" in p:
        return 2
    if p.endswith((".md", ".rst", ".txt")) or "/docs/" in p:
        return 3
    if p.endswith((".yaml", ".yml", ".json", ".toml", ".ini", ".cfg")):
        return 1
    return 0


def _parse_numstat(numstat: str) -> List[Tuple[str, Optional[int]]]:
    """逐文件 (路径, 变更行数)。二进制的行数为 None。

    二进制显示为 `-\t-\tsrc/b.bin`，不参与行数排序，
    但**必须出现在文件清单里**，否则二进制产出等于隐身（A3 的 2.8 / R6）。
    """
    out: List[Tuple[str, Optional[int]]] = []
    for line in numstat.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        path = parts[-1]
        try:
            churn: Optional[int] = int(parts[0]) + int(parts[1])
        except (ValueError, IndexError):
            churn = None
        out.append((path, churn))
    return out


def _split_patch_by_file(patch: str) -> List[Tuple[str, str]]:
    """把整份 patch 拆成 (路径, 该文件的 diff 片段)。"""
    if not patch.strip():
        return []
    chunks: List[Tuple[str, str]] = []
    current_path = ""
    buf: List[str] = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if buf:
                chunks.append((current_path, "".join(buf)))
                buf = []
            m = re.search(r" b/(.+?)\s*$", line)
            current_path = m.group(1) if m else ""
        buf.append(line)
    if buf:
        chunks.append((current_path, "".join(buf)))
    return chunks


def truncate_patch(patch: str, numstat: str,
                   limit: int = None) -> Tuple[str, List[str]]:
    """按风险排序注入 patch，超限的文件列入截断清单。

    返回 (裁剪后的 patch, 被截断的文件清单)。
    截断清单**必须**落盘并注入 prompt（A3 的 3.8 第 3 条）：
    审查范围不完整而 reviewer 不知道，比 diff 大更危险。
    """
    cap = PATCH_BYTE_LIMIT if limit is None else limit
    chunks = _split_patch_by_file(patch)
    if not chunks:
        return patch, []

    churn = {p: c for p, c in _parse_numstat(numstat)}

    def sort_key(item: Tuple[str, str]):
        path = item[0]
        c = churn.get(path)
        return (_risk_rank(path), -(c if c is not None else 0), path)

    kept: List[str] = []
    truncated: List[str] = []
    used = 0
    for path, body in sorted(chunks, key=sort_key):
        size = len(body.encode("utf-8"))
        if used + size > cap and kept:
            truncated.append(path or "(未知路径)")
            continue
        if used + size > cap and not kept:
            # 单个文件就超限：保留其头部，仍记为截断。
            budget = max(cap, 0)
            kept.append(body.encode("utf-8")[:budget].decode("utf-8", "ignore"))
            truncated.append(path or "(未知路径)")
            used = cap
            continue
        kept.append(body)
        used += size
    return "".join(kept), truncated


# ── claims：agent 的声明，不是事实 ──

def record_claims(task: str, *, task_ids: Optional[List[str]] = None,
                  verify_cmd: str = "",
                  files_touched: Optional[List[str]] = None) -> None:
    """把 03 准出的结构化声明写入 `.state`（A3 的 3.5）。

    命名刻意用 `claims` 而非 facts：这是 agent 的**声明**。
    A6 的职责之一是把 `claims.files_touched` 与真实 diff 对照。

    必须走 `update_state`（受控入口）—— 裸 `read_state` → 改 → `write_state`
    在并发下会抹掉别人刚签署的 Gate（A0 的 R1 实测）。
    """
    from ..core.state import update_state

    payload = {
        "task_ids": list(task_ids or []),
        "verify_cmd": verify_cmd,
        "files_touched": list(files_touched or []),
    }

    def mutate(state):
        stages = state.setdefault("stages", {})
        bucket = stages.setdefault("03-coding", {})
        bucket["claims"] = payload
        return state

    update_state(task, mutate)


# 模板占位符。把 `___` 当成真实声明会让对照凭空多出不存在的条目。
_PLACEHOLDERS = {"___", "[task id]", "n/a", "none", "-"}


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip("`").strip().lower()
    return (not v) or v in _PLACEHOLDERS or set(v) == {"_"}


def extract_claims(text: str) -> Dict[str, Any]:
    """从 03 产出里解析 agent 的结构化声明（A3 的 3.5）。

    方案是**由 agent 声明**，而不是从 diff 反推：后者会让「声明对照 diff」
    变成 diff 自己跟自己比，对照就失去了全部意义。

    命名沿用 `claims` 而非 facts —— 这是 agent 的说法，未经核实。
    A6 的职责之一是把 `files_touched` 与真实 diff 对照。
    """
    if not text:
        return {"task_ids": [], "verify_cmd": "", "files_touched": []}

    task_ids: List[str] = []
    for raw in re.findall(r"^\s*`([^`]+)`\s*:", text, re.MULTILINE):
        if not _is_placeholder(raw):
            task_ids.append(raw.strip())

    verify_cmd = ""
    m = re.search(r"\*\*Verify cmd\*\*\s*:\s*`?([^`\n]*)`?", text, re.IGNORECASE)
    if m and not _is_placeholder(m.group(1)):
        verify_cmd = m.group(1).strip()

    files: List[str] = []
    section = re.search(
        r"^##+\s*Files?\s+Touched\s*$(.*?)(?=^##|\Z)",
        text, re.MULTILINE | re.IGNORECASE | re.DOTALL)
    if section:
        for line in section.group(1).splitlines():
            item = line.strip()
            if not item.startswith(("-", "*")):
                continue
            item = item.lstrip("-* ").strip()
            # 两种写法都认：有人写反引号，有人不写。
            item = item.strip("`").strip()
            if item and not _is_placeholder(item) and item not in files:
                files.append(item)

    return {"task_ids": task_ids, "verify_cmd": verify_cmd, "files_touched": files}


def read_claims(task: str) -> Dict[str, Any]:
    from ..core.state import read_state

    stages = (read_state(task) or {}).get("stages") or {}
    return ((stages.get("03-coding") or {}).get("claims") or {})


# ── manifest 与完整性 ──

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(task: str, pack: FactPack, availability: Dict[str, str],
                    mock: bool) -> Path:
    from ..core.utils import now

    facts = _facts_dir(task)
    digests = {name: _sha256_file(facts / name)
               for name in FACT_FILES if (facts / name).is_file()}
    manifest = {
        "task": task,
        "generated_at": now(),
        "baseline_sha": pack.baseline_sha,
        "baseline_kind": pack.baseline_kind,
        "files": digests,
        "truncated": pack.truncated,
        "warnings": pack.warnings,
        "spec_availability": availability,
        "mock": mock,
    }
    path = facts / MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                               sort_keys=True), encoding="utf-8")
    return path


def _record_manifest_hash(task: str, manifest_path: Path) -> None:
    """把 manifest 的哈希写进 `.state` 的 `facts` 键。

    落点是 `facts` 而非新增顶层键（A3 的 3.6）：`facts` 已在
    `EVIDENCE_FIELDS` 中，写在这里签名自动覆盖。
    **不要去改 `EVIDENCE_FIELDS`** —— 它是元组常量，并行改动时
    后提交者容易整体替换掉前者。
    """
    from ..core.state import update_state
    from ..core.utils import now

    digest = _sha256_file(manifest_path)

    def mutate(state):
        bucket = state.setdefault("facts", {})
        bucket["manifest_sha256"] = digest
        bucket["generated_at"] = now()
        return state

    update_state(task, mutate)


def manifest_matches_state(task: str) -> bool:
    """磁盘上的 manifest 与 `.state` 里签名覆盖的哈希是否一致。

    `verify()` 只能证明「各事实文件与 manifest 自洽」。连 manifest 一起改
    就骗过了它 —— 挡住那一层的是本函数 + A0 的 HMAC。
    """
    from ..core.state import read_state

    path = _facts_dir(task) / MANIFEST_NAME
    if not path.is_file():
        return False
    recorded = ((read_state(task) or {}).get("facts") or {}).get("manifest_sha256")
    if not recorded:
        return False
    return recorded == _sha256_file(path)


def verify(task: str) -> List[str]:
    """校验事实包完整性，返回问题清单。空列表表示完整。"""
    facts = _facts_dir(task)
    problems: List[str] = []
    manifest_path = facts / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"缺失 {MANIFEST_NAME}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{MANIFEST_NAME} 不可解析: {exc}"]

    for name in FACT_FILES:
        if not (facts / name).is_file():
            problems.append(f"缺失 {name}")

    for name, digest in (manifest.get("files") or {}).items():
        path = facts / name
        if not path.is_file():
            problems.append(f"manifest 记录了 {name} 但文件不存在")
            continue
        if _sha256_file(path) != digest:
            problems.append(f"{name} 哈希不匹配 —— 内容已被改动")
    return problems


# ── 主流程 ──

def generate(task: str, *, force: bool = True) -> FactPack:
    """生成 `facts/` 全部内容。每次进入 04 都重新生成（上游 4.2）。

    **前提缺失时硬失败，不降级**（A3 的 3.9）。分界线：
    「事实来源本身不可信」（缺基线、归属错误、目标目录不存在）硬失败；
    「某项事实不可得」（pytest 缺失、覆盖率工具缺失）记 `unavailable`。
    后者进 A6 的三态与 A10 的「不确定」分类，**绝不计为通过**。

    全部前提校验都在**落盘之前**完成：先生成后校验的写法会在抛异常时
    已把半个事实包写进磁盘，而半成品会被下游当成完整的读（验收 12 / 20）。
    """
    from ..core.git_repo import GitRepoError, baseline_diff, read_baseline
    from ..core.state import read_state

    state = read_state(task) or {}
    if state.get("_corrupted"):
        raise FactPackError(f"任务 {task} 的 .state 已损坏，拒绝生成事实包")

    target_dir = (state.get("target_dir") or "").strip()
    if not target_dir:
        raise FactPackError(f"任务 {task} 的 .state 无 target_dir —— 无从确定观察对象")
    if not Path(target_dir).is_dir():
        raise FactPackError(f"target_dir 不存在: {target_dir}")

    info = read_baseline(task)
    if info is None or not info.sha:
        raise FactPackError(
            f"任务 {task} 缺少 review.baseline_sha（A1 未生效或基线未建立）。"
            f"缺基线时 diff 没有确定语义，**不得当作『无改动』继续**。")

    # diff 采集整体复用 A1 的 baseline_diff（A3 的 3.3）：临时索引 +
    # 临时对象库那段逻辑有三个易错点（索引起点、对象库重定向、worktree 下的
    # objects 路径），各写一遍必然分叉，且 A1 侧的回归测试保护不到 A3 的副本。
    try:
        diff = baseline_diff(target_dir, info.sha, include_patch=True)
    except GitRepoError as exc:
        # 捕获后回落到裸 `git diff` 是最危险的写法 —— 那会静默退回 2.1 的
        # 「事实包是空的」，然后 reviewer 判「无改动，通过」。
        raise FactPackError(f"diff 采集失败，拒绝生成半个事实包: {exc}") from exc

    mock = bool(is_mock_agent())
    warnings: List[str] = []

    patch, truncated = truncate_patch(diff.patch, diff.numstat)
    if truncated:
        warnings.append(
            f"diff 超过 {PATCH_BYTE_LIMIT} 字节阈值，{len(truncated)} 个文件被截断："
            f"{', '.join(truncated)}。审查范围不完整，产出须标注。")

    if info.kind == "reconstructed":
        warnings.append(
            "基线为补建（reconstructed）—— diff 不完整，不得与 fresh 基线同等计为已达成。")

    if mock:
        tests = {"collected": None, "passed": None, "failed": None,
                 "skipped": None, "exit_code": None, "parse_status": "mock",
                 "ran": False, "raw": "", "mock": True,
                 "pytest_version": None,
                 "note": "mock 模式跳过真实 pytest 执行；基线校验照常执行"}
    else:
        tests = collect_tests(target_dir)
        tests["mock"] = False
    if tests.get("parse_status") in ("unavailable", "no_test_surface"):
        warnings.append(
            f"测试结果不可得（{tests.get('parse_status')}）—— 记 unavailable，不计为通过。")

    # claims 与真实 diff 的对照（验收 18）。A3 只做对照不做判定 ——
    # 判定归 A6，但事实层的对照必须由 A3 提供。
    claims = read_claims(task)
    declared = set(claims.get("files_touched") or [])
    actual = set(diff.files)
    if declared:
        ghosts = sorted(declared - actual)
        unreported = sorted(actual - declared)
        if ghosts:
            warnings.append(
                f"claims.files_touched 声明了 diff 中不存在的文件：{', '.join(ghosts)}")
        if unreported:
            warnings.append(
                f"diff 中存在未被 claims 声明的文件：{', '.join(unreported)}")

    spec_text, availability = build_spec(task, state)
    plan_text = build_plan(task)

    facts = _facts_dir(task)
    facts.mkdir(parents=True, exist_ok=True)

    payloads = {
        "diff.stat": diff.stat,
        "diff.patch": patch,
        "diff.numstat": diff.numstat,
        "diff.truncated": "\n".join(truncated) + ("\n" if truncated else ""),
        "tests.json": json.dumps(tests, ensure_ascii=False, indent=2, sort_keys=True),
        "coverage.json": json.dumps(_collect_coverage(target_dir),
                                    ensure_ascii=False, indent=2, sort_keys=True),
        "spec.md": spec_text,
        "plan.md": plan_text,
    }
    for name, body in payloads.items():
        (facts / name).write_text(body, encoding="utf-8")

    pack = FactPack(
        task=task, target_dir=target_dir,
        baseline_sha=info.sha, baseline_kind=info.kind,
        files={name: facts / name for name in payloads},
        truncated=truncated, warnings=warnings,
    )
    manifest_path = _write_manifest(task, pack, availability, mock)
    pack.files[MANIFEST_NAME] = manifest_path
    _record_manifest_hash(task, manifest_path)
    return pack
