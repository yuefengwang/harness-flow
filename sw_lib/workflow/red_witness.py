"""sw_lib.workflow.red_witness — 测试冻结与 Red 见证（A2）。

设计依据：docs/design/A2-red-witness.md

核心原则：**红不是 agent 声称的状态，是 harness 亲自观测并留痕的事件。**

> ⚠️ 本文件当前是**骨架**（DEV-PROTOCOL 第 1 节步 1 的产物）：
> 所有判定入口一律放行。这样写测试时看到的红是**断言失败**，
> 而不是 ImportError —— 后者是「造红」，会让整套机制形同虚设（A2 的 1.2/F3）。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import HOOKS_DIR, HOOK_TIMEOUT_SECONDS, is_mock_agent
from ..core.state import read_state, update_state, NO_CHANGE
from ..core.evidence import verify_evidence


# ── 退出码语义（A2 第 2 节，已本机实测）──

EXIT_ASSERTION_FAILED = 1   # 有效的红
EXIT_COLLECTION_ERROR = 2   # 造红：ImportError / SyntaxError
EXIT_USAGE_ERROR = 4        # pytest 用法/内部错误：conftest 塌了最常见
EXIT_NO_TESTS = 5           # 无测试
EXIT_ALL_PASSED = 0         # 绿（也可能是「全 skip」的假绿）

# 见证超时（复用钩子上限，A2 的 5.1 第二个坑）
RUN_TIMEOUT = HOOK_TIMEOUT_SECONDS

# 结果采集插件。它不做判定，只如实记录 pytest 的 logreport。
_PLUGIN_NAME = "rw_pytest_plugin"

# 冻结范围的判定依据是**路径**（A2 的 3.3）。
_TEST_DIR_NAMES = ("tests", "test")

# 不算产出的目录：冻结它们会让格式化/装依赖误伤准出（A2 的 R6 的反面）。
_IGNORED_DIRS = frozenset({
    "__pycache__", ".venv", "venv", ".git", ".pytest_cache",
    "node_modules", ".mypy_cache", ".ruff_cache",
})

PHASE_TEST = "03a"
PHASE_IMPL = "03b"

# 「尚未进入见证流程」。存量任务与 04→03 返工轮次都停在这里 ——
# 它们的实现早已写完，测试本来就是绿的，按 03a 判定会永久无法准出。
PHASE_NONE = "none"


@dataclass
class WitnessVerdict:
    """见证结论。三态，`unavailable` 不算通过（A2 的 R2/R3）。"""
    status: str = "ok"
    reason: str = ""
    exit_code: Optional[int] = None
    failed_nodes: List[str] = field(default_factory=list)
    passed_nodes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# ── 观测：真实执行 pytest ──
#
# 这一层刻意**不含任何判定**，只把 pytest 说的话如实带回来。
# 判定全在下面的 classify_* / verify_* 里，于是「pytest 的真实行为」与
# 「我们怎么解读它」可以分别被测试 —— A2 的 10.3 要求前者不得 mock。

def run_tests(target_dir: Any,
              extra_env: Optional[Dict[str, str]] = None,
              ) -> Tuple[int, Dict[str, str]]:
    """在 ``target_dir`` 里真实跑一次 pytest。

    返回 ``(退出码, {节点 id: outcome})``。

    outcome 取自 pytest 自己的 report，因此 ``skipped`` 与 ``passed``
    是可区分的 —— 这是 03b 不被「全 skip」骗过的前提（A2 的 10.4）。
    退出码 2（collection error）时节点表为空。

    超时或解释器不可用时返回 ``(-1, {})``，由调用方判为 ``unavailable``
    而**不是**通过（A2 的 R3）。
    """
    target = Path(str(target_dir))
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    # 插件目录进 PYTHONPATH；同时保留调用方给的 PYTHONPATH（src-layout 用）。
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HOOKS_DIR)] + ([existing] if existing else []))

    fd, out_path = tempfile.mkstemp(prefix="rw-result-", suffix=".json")
    os.close(fd)
    env["RW_OUT"] = out_path

    py = env.pop("RW_PYTHON", None) or sys.executable
    try:
        proc = subprocess.run(
            [py, "-m", "pytest", "-p", _PLUGIN_NAME, "-q"],
            cwd=str(target), env=env, capture_output=True, text=True,
            timeout=RUN_TIMEOUT, check=False,
        )
        exit_code = proc.returncode
        nodes: Dict[str, str] = {}
        try:
            with open(out_path, encoding="utf-8") as f:
                payload = json.load(f)
            raw = payload.get("nodes")
            if isinstance(raw, dict):
                nodes = {str(k): str(v) for k, v in raw.items()}
        except (OSError, ValueError):
            # 插件没能落盘（如解释器根本没起来）。不编造节点表。
            nodes = {}
        return exit_code, nodes
    except subprocess.TimeoutExpired:
        return -1, {}
    except OSError:
        return -1, {}
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def classify_exit_code(exit_code: int, nodes: Dict[str, str]) -> WitnessVerdict:
    """把一次 pytest 运行判成「有效的红」或具体的拒绝理由。

    只有退出码 1 且**确有节点 failed** 才算有效的红。

    为什么不能只看退出码：退出码 0 有两种来源（全通过 / 全 skip），
    而全 skip 的套件在 03b 会被「只看退出码」的实现当成全部转绿
    （A2 的 10.4）。判定因此一律以节点 outcome 为准，退出码只用于
    识别 collection error 与「无测试」这两种拿不到节点表的情形。
    """
    failed = sorted(n for n, o in nodes.items() if o == "failed")
    passed = sorted(n for n, o in nodes.items() if o == "passed")
    skipped = sorted(n for n, o in nodes.items() if o == "skipped")

    if exit_code == EXIT_COLLECTION_ERROR:
        return WitnessVerdict(
            "invalid",
            "造红：收集期就报错（ImportError / SyntaxError），断言从未被执行。"
            "红必须是断言失败，不是 import 失败。",
            exit_code)

    if exit_code == EXIT_NO_TESTS:
        return WitnessVerdict(
            "invalid", "无测试：pytest 未收集到任何测试用例。", exit_code)

    if exit_code == EXIT_USAGE_ERROR:
        # 与「无测试」分开说。实测 `repo/newworld`：3 个测试文件都在，但
        # `tests/conftest.py` 写 `from main import app` 而 `main.py` 没写，
        # conftest 一塌 pytest 就以 4 退出、一个测试也不跑。落到下面的兜底
        # 分支会说「无测试」，把人推去补测试 —— 该补的是 main.py。
        return WitnessVerdict(
            "invalid",
            "pytest 未能启动（退出码 4）：conftest.py 导入失败或命令行/配置有误，"
            "收集期就中断了，一个测试也没跑。",
            exit_code)

    if exit_code < 0:
        # 超时 / 解释器起不来。三态里的 ❓，绝不当成通过（A2 的 R3）。
        return WitnessVerdict(
            "unavailable", "测试执行未能完成（超时或解释器不可用），无法见证。",
            exit_code)

    if failed:
        return WitnessVerdict("ok", "", exit_code,
                              failed_nodes=failed, passed_nodes=passed)

    if skipped and not passed:
        return WitnessVerdict(
            "invalid",
            f"全部 skip（{len(skipped)} 个）：退出码是 0，但没有任何断言被执行，"
            f"无法见证红。",
            exit_code)

    if skipped:
        return WitnessVerdict(
            "invalid",
            f"测试未失败（{len(passed)} 通过 / {len(skipped)} skip），无法见证红。",
            exit_code)

    if passed:
        return WitnessVerdict(
            "invalid",
            f"测试未失败（{len(passed)} 个全部通过），无法见证红。"
            f"03a 只写测试、不写实现，测试应当是失败的。",
            exit_code)

    return WitnessVerdict(
        "invalid", "无测试：未采集到任何测试节点结果。", exit_code)


def hash_test_files(target_dir: Any) -> Dict[str, str]:
    """扫描目标目录下的测试文件，返回 ``相对路径 -> sha256``。

    只覆盖测试文件 —— 实现文件必须能在 03b 自由改动。
    识别依据是路径（A2 的 3.3）：``test_*.py`` / ``*_test.py``，
    以及 ``tests/`` 目录下的全部 ``.py``（含 ``conftest.py`` ——
    它能改变测试行为，不冻结它等于留了一扇后门）。

    **刻意不做任何规范化**（换行、空白一律按原字节算），依据 A2 的 R6：
    宁可被格式化工具误伤并记录为摩擦点，也不放宽判据。
    """
    root = Path(str(target_dir))
    if not root.is_dir():
        return {}

    out: Dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if any(p in _IGNORED_DIRS for p in rel_parts):
            continue
        if not _is_test_path(rel_parts):
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        out["/".join(rel_parts)] = digest
    return out


def _is_test_path(rel_parts: Tuple[str, ...]) -> bool:
    """路径是否属于「测试文件」。"""
    name = rel_parts[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    # tests/ 目录下的全部 .py：conftest.py 与工具模块同样能左右测试结果
    return any(p in _TEST_DIR_NAMES for p in rel_parts[:-1])


def find_impl_files(target_dir: Any, limit: int = 0) -> List[str]:
    """目标目录下的**实现**文件（`hash_test_files` 的反面）。

    用途只有一个：在 03a 判断「测试全绿」的成因是不是「实现已先落盘」。
    与冻结共用同一套路径判据（`_is_test_path` / `_IGNORED_DIRS`），
    两侧错位会让「哪些算测试」在冻结和绕过检测里得出不同答案。

    `limit > 0` 时只返回前若干个 —— 门禁文案不该被 200 个文件名淹没，
    但**留痕不截断**：调用方要完整清单时传 0。
    """
    root = Path(str(target_dir))
    if not root.is_dir():
        return []

    out: List[str] = []
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if any(p in _IGNORED_DIRS for p in rel_parts):
            continue
        if _is_test_path(rel_parts):
            continue
        rel = "/".join(rel_parts)
        # 空的包声明文件不算实现 —— `src/__init__.py` 到处都有，
        # 拿它当「实现已落盘」的证据会让判定形同虚设。
        if rel_parts[-1] == "__init__.py":
            try:
                if not path.read_bytes().strip():
                    continue
            except OSError:
                continue
        out.append(rel)
        if limit and len(out) >= limit:
            break
    return out


def compare_hashes(frozen: Dict[str, str],
                   current: Dict[str, str]) -> WitnessVerdict:
    """比对冻结的测试文件哈希与当前哈希。

    三类改动：

    * **内容变了** → 拒绝，并指名文件（A2 的 3.4：必须提示哪个文件被改动）。
    * **文件不见了** → 同样拒绝。只查「内容变了」的话，删掉测试就是一条
      免费的绕过路径。
    * **新增文件** → 放行。冻结要防的是改已有判据，补测试是好事。

    发现问题时**列全部**受影响文件，不只报第一个 ——
    否则用户要来回好几轮才知道改了几处。
    """
    modified = sorted(k for k, v in frozen.items()
                      if k in current and current[k] != v)
    missing = sorted(k for k in frozen if k not in current)

    if not modified and not missing:
        return WitnessVerdict("ok")

    parts: List[str] = []
    if modified:
        parts.append("被改动: " + ", ".join(modified))
    if missing:
        parts.append("已删除或改名: " + ", ".join(missing))
    return WitnessVerdict(
        "invalid",
        "测试文件在 03a 见证之后发生了改动 —— " + "；".join(parts) +
        "。若测试确实写错了，必须显式回退到 03a 重新见证红，"
        "不得在实现阶段静默修改测试（DEV-PROTOCOL 1.2）。")


def verify_green(frozen_nodes: List[str], nodes: Dict[str, str],
                 exit_code: int) -> WitnessVerdict:
    """03b 的转绿判定：退出码 0 **且**每个已见证节点确实 ``passed``。

    两个条件都要，缺一不可（A2 的 10.4）：

    * 只看退出码 → 全 skip 的套件退出码是 0，会被当成转绿；
    * 只看节点 → 已见证节点绿了，但套件里新写的测试红着，
      单调性就破了（A2 的 4.1）。
    """
    if exit_code < 0:
        return WitnessVerdict(
            "unavailable", "测试执行未能完成（超时或解释器不可用），无法确认转绿。",
            exit_code)

    problems: List[str] = []
    for node in frozen_nodes:
        outcome = nodes.get(node)
        if outcome == "passed":
            continue
        if outcome is None:
            problems.append(f"{node}（本次运行中不存在：被删除或改名）")
        elif outcome == "skipped":
            problems.append(f"{node}（skipped —— skip 不是绿）")
        else:
            problems.append(f"{node}（{outcome}）")

    still_failing = sorted(n for n, o in nodes.items() if o == "failed")

    if problems:
        return WitnessVerdict(
            "invalid",
            "已见证的失败节点未全部转绿: " + "; ".join(problems),
            exit_code,
            failed_nodes=still_failing)

    if exit_code != EXIT_ALL_PASSED or still_failing:
        return WitnessVerdict(
            "invalid",
            f"套件整体未转绿（退出码 {exit_code}）"
            + (f"，仍在失败: {', '.join(still_failing)}" if still_failing else "")
            + "。已见证节点绿了但别处红着 —— 单调性要求既有测试不许变红。",
            exit_code,
            failed_nodes=still_failing)

    return WitnessVerdict(
        "ok", "", exit_code,
        passed_nodes=sorted(n for n, o in nodes.items() if o == "passed"))


# ── `.state` 的 red_witness 子树 ──
#
# 写入一律走 `update_state`（A0 的 D0-3）。裸 `read_state` + 改 +
# `write_state` 在并发下会丢更新，而这里丢掉的是**判据**本身。
# 源码断言测试 `test_writer_does_not_call_write_state_directly` 守住这条。

_FIELD = "red_witness"


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def read_witness(task: str) -> Dict[str, Any]:
    """读取 `red_witness` 子树。无记录或写坏时返回空字典。

    读路径一律降级而不抛异常：判定逻辑崩溃会让 /advance 直接不可用，
    比门禁误判更糟（与 `stage_state._read_bucket` 同一条纪律）。
    """
    state = read_state(task)
    raw = state.get(_FIELD) if isinstance(state, dict) else None
    return raw if isinstance(raw, dict) else {}


def read_phase(task: str) -> str:
    """当前子阶段。

    **无记录时返回 ``none``，不是 ``03a``。**

    这是对 A2 原文（R1「无记录时视为 03a」）的一处修正，依据实施期实测：
    从测试结果反推子阶段是不可能的 —— 「测试红」既可能是「03a 刚写完测试」
    也可能是「03b 实现没写对」，而两者的正确处置恰好相反。
    若无记录即按 03a 判定，既有契约「失败的测试不许过闸」会被推翻
    （实测 `test_real_failure_still_blocks` 转红）。

    因此 phase 只由 harness 显式设定：`begin_test_phase` 在阶段启动时写入。
    详见 `tests/unit/workflow/test_red_witness_phase_explicit.py`。
    """
    phase = read_witness(task).get("phase")
    return phase if phase in (PHASE_TEST, PHASE_IMPL) else PHASE_NONE


def begin_test_phase(task: str) -> Dict[str, Any]:
    """显式进入 03a（写测试、见证红）。由 pre hook 在阶段启动时调用。

    **幂等**：已有见证记录时什么都不做。pre hook 每次启动阶段都会调它，
    若不幂等，03b 的返工重跑会把冻结哈希抹掉 ——
    「改测试让它过」就重新变成一条免费路径。
    """
    if read_witness(task).get("witnessed_at"):
        return read_witness(task)

    def fn(raw):
        if raw.get("witnessed_at"):
            return
        raw["phase"] = PHASE_TEST
        raw["entered_at"] = _now()
        raw.setdefault("failed_nodes", [])
        raw.setdefault("test_files", {})
        raw.setdefault("green_nodes", [])
        raw.setdefault("rewitness_count", 0)
        raw.setdefault("mock", False)

    return _mutate_witness(task, fn)


def _mutate_witness(task: str, fn) -> Dict[str, Any]:
    """在受控入口内就地修改 `red_witness` 子树，返回修改后的子树。"""
    result: Dict[str, Any] = {}

    def mutate(state):
        nonlocal result
        if not state:
            return NO_CHANGE
        raw = state.get(_FIELD)
        if not isinstance(raw, dict):
            raw = {}
        fn(raw)
        state[_FIELD] = raw
        state["updated_at"] = _now()
        result = raw
        return state

    update_state(task, mutate)
    return result


def record_red(task: str, verdict: "WitnessVerdict",
               test_files: Dict[str, str]) -> Dict[str, Any]:
    """记录一次有效的红见证，并把 phase 推到 03b。

    `failed_nodes` 用**并集**而不是覆盖：A2 的 4.1 要求判据集单调 ——
    一旦见证过的节点永久留在集合里，同一个 bug 不能反复出现。
    """
    def fn(raw):
        existing = raw.get("failed_nodes")
        nodes = set(existing if isinstance(existing, list) else [])
        nodes.update(verdict.failed_nodes)
        raw["phase"] = PHASE_IMPL
        raw["witnessed_at"] = _now()
        raw["exit_code"] = verdict.exit_code
        raw["failed_nodes"] = sorted(nodes)
        raw["test_files"] = dict(test_files)
        # 见证真的发生了 → 清掉上一轮留下的 unavailable。
        # `status` 是 A3 / A6(O4) / A10 读的那个字段，陈旧值会让一次
        # 货真价实的见证在报告里显示成 ❓ —— 若 ❓ 既可能是真没见证、
        # 也可能是残留，这个字段就没法用了。
        _mark_status_ok(raw)
        raw.setdefault("green_nodes", [])
        raw.setdefault("rewitness_count", 0)
        raw.setdefault("mock", False)

    return _mutate_witness(task, fn)


def record_green(task: str, verdict: "WitnessVerdict") -> Dict[str, Any]:
    """记录转绿。**不动** `failed_nodes` —— 那是永久判据集（A2 的 4.1）。"""
    def fn(raw):
        raw["green_at"] = _now()
        raw["green_nodes"] = sorted(verdict.passed_nodes)
        _mark_status_ok(raw)

    return _mutate_witness(task, fn)


def request_rewitness(task: str, reason: str = "") -> Dict[str, Any]:
    """显式回退到 03a 重新见证（A2 的 3.4 允许的例外）。

    存在的意义是给「测试写错了」一条**留痕**的出路。没有它，
    唯一可行的做法就是静默改测试 —— 那正是 F4 要拦的行为。
    冻结哈希一并清掉，否则回到 03a 改测试立刻撞上旧哈希。
    """
    def fn(raw):
        raw["phase"] = PHASE_TEST
        raw["rewitness_count"] = int(raw.get("rewitness_count") or 0) + 1
        raw["rewitness_reason"] = reason
        raw["rewitness_at"] = _now()
        raw["test_files"] = {}
        raw.pop("green_at", None)
        raw["green_nodes"] = []
        # 回退把「已转绿」这个结论一并作废。留着 ok 会让它在
        # 「测试已解冻、红还没重新见证」的窗口里继续对下游生效。
        raw.pop("status", None)

    return _mutate_witness(task, fn)


def abandon_witness(task: str, reason: str = "") -> Dict[str, Any]:
    """放弃本轮红见证，把判定交回 `run_project_tests`（A2 的 R4 出路）。

    存在的意义与 `request_rewitness` 完全对称 —— 那条是「03b 发现测试写错」
    的出路，这条是「03a 见证不到红」的出路。R4 当初判定「退出码 0 被拒绝
    即可覆盖；无需额外检测」，只做了拒绝：agent 一旦在 03a 就把实现写完
    （`bash` 绕过写入约束，A0 的 U0-1），测试再也不可能红，而 03a 没有任何
    合法出口 —— 任务 `helloworld` 因此永久卡死，只能手改 `.state`。

    这不是「放宽见证」：`_check_03a` 仍然拒绝全绿的 03a（验收第 4 条不变）。
    放弃是**人的显式动作**，留痕、计数、并且如实记成 `unavailable`。

    三处刻意的选择：

    * `leave_witness_flow` 必须调 —— phase 停在 `03a` 会让钩子不把判定交回
      `run_project_tests`，失败的测试反而过闸（10.6 第二/七行同一个洞）。
    * `mark_unavailable` 而非伪造 `green_at` —— 放弃的结论是 ❓ 不是 ✅。
    * **不动 `failed_nodes`** —— 判据集单调（4.1）。这一轮可以放弃，
      已经见证过的 bug 不能因此从判据集里消失。
    """
    leave_witness_flow(task)
    mark_unavailable(task, f"人为放弃本轮 Red 见证 —— {reason}")

    def fn(raw):
        raw["abandon_count"] = int(raw.get("abandon_count") or 0) + 1
        raw["abandon_reason"] = reason
        raw["abandoned_at"] = _now()

    return _mutate_witness(task, fn)


def record_bypass(task: str, impl_files: List[str]) -> Dict[str, Any]:
    """记下一次「实现先落盘导致见证不到红」的绕过，并让路（A2 的 10.6 修正）。

    与 `abandon_witness` 是同一件事的两个版本：那条要人手敲命令，这条由
    门禁自己做。前一版只有手动版，实测证明那不成立 —— `bash` 绕过是
    **agent 的常规行为**（`helloworld` / `helloworld2` 各一次，同一死法），
    要求人每轮敲一条命令，等于把机制的运转成本转嫁给用户，而机制本身
    并没有因此多拦住任何东西。

    与手动放弃的三处不同：

    * `bypassed` / `bypass_files` 是**结构化字段**，不只是一句中文理由 ——
      下游（TUI / 04 事实包 / 05 报告）要按字段判定，而不是正则匹配文案。
    * `bypass_files` 记**完整**清单（门禁文案里才截断）——
      留痕被截断等于证据不全。
    * 计数独立于 `abandon_count`：人为放弃与 agent 绕过是两回事，
      混在一个计数里就分不清「用户按了几次」和「agent 绕了几次」。

    其余纪律与手动放弃完全一致：phase 抹回 `none`（否则测试判定消失）、
    记 `unavailable` 而非伪造 `green_at`、**不动 `failed_nodes`**（判据集单调）。
    """
    shown = impl_files[:5]
    tail = " ..." if len(impl_files) > 5 else ""
    reason = (
        "03a 期间实现文件已先落盘，测试因此全绿，红无法被见证 —— "
        f"共 {len(impl_files)} 个实现文件，含: {', '.join(shown)}{tail}。"
        "harness 无法阻止 agent 经 bash 写文件（session 权限规则只对 "
        "write/edit 有路径字段），故按事后处理如实记录。")
    leave_witness_flow(task)
    mark_unavailable(task, reason)

    def fn(raw):
        raw["bypassed"] = True
        raw["bypass_files"] = list(impl_files)
        raw["bypass_count"] = int(raw.get("bypass_count") or 0) + 1
        raw["bypassed_at"] = _now()

    return _mutate_witness(task, fn)


def append_witnessed_nodes(task: str, nodes: List[str]) -> Dict[str, Any]:
    """把新节点追加进判据集（A9 接口）。

    A9 的 3.9 已定案触发时机：**不是反例成立时，而是返工后该反例转绿时**。
    成立时就追加会让 A6 的 O4（全部节点转绿）立刻失败，把返工路径堵死。
    """
    def fn(raw):
        existing = raw.get("failed_nodes")
        merged = set(existing if isinstance(existing, list) else [])
        merged.update(nodes)
        raw["failed_nodes"] = sorted(merged)

    return _mutate_witness(task, fn)


def leave_witness_flow(task: str) -> Dict[str, Any]:
    """退出见证子阶段，phase 回到 `none`（A2 的 R2 让路路径）。

    只清 `phase`：`failed_nodes` 是永久判据集（4.1），
    `rewitness_count` 是留痕，都不能被让路顺手抹掉。

    为什么必须清：钩子读 `--phase`，只有 `none` 才会把测试判定交回
    `run_project_tests`。停在 `03a` 会让 npm 项目的失败测试直接过闸。
    """
    def fn(raw):
        raw.pop("phase", None)
        raw.setdefault("failed_nodes", [])
        raw.setdefault("test_files", {})
        raw.setdefault("green_nodes", [])
        raw.setdefault("rewitness_count", 0)
        raw.setdefault("mock", False)

    return _mutate_witness(task, fn)


def _mark_status_ok(raw: Dict[str, Any]) -> None:
    """把结论标成 `ok`，并清掉上一轮的 `unavailable` 痕迹。

    原因串必须一起清：留着它会出现「status=ok 却附着一条未见证的理由」
    这种自相矛盾的记录，读它的人只能靠猜哪个字段更新。
    """
    raw["status"] = "ok"
    raw.pop("unavailable_reason", None)


def mark_unavailable(task: str, reason: str) -> Dict[str, Any]:
    """记下「见证未发生」及原因（A2 的 R2/R3 三态）。

    **刻意不写** `green_at` / `failed_nodes` —— 那会伪造出一份
    「见证过并转绿」的证据，下游 A6 / A10 会据此报 ✅。
    `unavailable` 必须在报告里显示为 ❓，而不是被静默升级为通过。

    **也刻意不写 `phase`。** 曾经这里有一句 `setdefault("phase", PHASE_TEST)`，
    后果是存量任务过闸一次就被静默推进到 03a，下一轮按 03a 判定
    「测试必须是红的」—— 而存量任务的测试是绿的，于是永久卡死。
    phase 只能由 `begin_test_phase` 显式设定，这里记的恰恰是「没进入见证流程」。
    """
    def fn(raw):
        raw["status"] = "unavailable"
        raw["unavailable_reason"] = reason
        raw["checked_at"] = _now()
        raw.setdefault("failed_nodes", [])
        raw.setdefault("test_files", {})
        raw.setdefault("green_nodes", [])
        raw.setdefault("rewitness_count", 0)
        raw.setdefault("mock", False)

    return _mutate_witness(task, fn)


#: 展示层的单一来源。三处显示（TUI 头部、04 事实包、05 报告）都从
#: `witness_summary` 取值 —— 各处自己拼文案会立刻分叉：改了一处忘了另一处，
#: 两边说法不一致而用户无从判断哪个是真的（A5 的 R2 正是这么裂开的）。

def witness_summary(task: str) -> Dict[str, Any]:
    """把 `red_witness` 子树压成展示层要用的几个字段。

    降级为事后处理之后，「见证被绕过」成为常态，因此它必须**处处可见**。
    记录在 `.state` 里而无人显示，与机制不存在的区别只有一个：
    `.state` 里多了一份「我们检查过」的痕迹，反而让人以为有人在管。

    标签一律走三态（A6 的 3.3 / DEV-PROTOCOL 第 2 节）：

    * `ok` → ✅ 真见证过红并转绿；
    * `unavailable` → ❓ 见证未发生（绕过 / 非 Python 栈 / 存量 / mock）；
    * `absent` → ❓ 无任何记录，**不编造**「大概没问题」。

    `detail` 里带上实现文件名：不拦人，但绕过要留下能追责的具体痕迹。
    """
    record = read_witness(task)
    if not record:
        return {"status": "absent", "bypassed": False, "bypass_count": 0,
                "label": "❓ 红绿见证：无记录", "detail": "", "files": []}

    bypassed = bool(record.get("bypassed"))
    files = record.get("bypass_files")
    files = list(files) if isinstance(files, list) else []
    count = int(record.get("bypass_count") or 0)
    status = str(record.get("status") or "")

    if record.get("mock"):
        return {"status": "unavailable", "bypassed": False, "bypass_count": count,
                "label": "❓ 红绿见证：mock 模式（未执行真实见证）",
                "detail": "mock 模式写入合成记录，红绿流程未被观测",
                "files": []}

    if bypassed:
        shown = ", ".join(files[:5]) + (" ..." if len(files) > 5 else "")
        return {
            "status": "unavailable", "bypassed": True, "bypass_count": count,
            "label": f"❓ 红绿见证：被绕过 {count} 次（unavailable）",
            "detail": (f"03a 期间实现已先落盘（{len(files)} 个文件：{shown}），"
                       f"红无法被见证；本轮测试的绿不构成「实现已被验证」"),
            "files": files,
        }

    if status == "ok" and record.get("green_at"):
        nodes = record.get("failed_nodes")
        nodes = nodes if isinstance(nodes, list) else []
        return {"status": "ok", "bypassed": False, "bypass_count": count,
                "label": f"✅ 红绿见证：{len(nodes)} 个判据节点已红→绿",
                "detail": "", "files": []}

    if status == "unavailable":
        return {"status": "unavailable", "bypassed": False, "bypass_count": count,
                "label": "❓ 红绿见证：未发生（unavailable）",
                "detail": str(record.get("unavailable_reason") or ""),
                "files": []}

    # 见证进行中（03a 已进入 / 03b 待转绿）。**不算通过**。
    phase = record.get("phase") or PHASE_NONE
    return {"status": "in_progress", "bypassed": False, "bypass_count": count,
            "label": f"❓ 红绿见证：进行中（phase={phase}）",
            "detail": "", "files": []}


def witness_report_line(task: str) -> str:
    """给 05 归档报告用的一行留痕。

    归档是任务的最终产物，也是最容易把 ❓ 静默升级成 ✅ 的地方 ——
    人写总结时倾向写「测试通过」，而「测试通过」与「红绿流程走过」
    在绕过的情形下完全是两件事。
    """
    summary = witness_summary(task)
    detail = summary.get("detail") or ""
    line = str(summary.get("label") or "")
    return f"{line} —— {detail}" if detail else line


def ensure_mock_witness(task: str) -> Dict[str, Any]:
    """mock 模式下写合成记录（A2 第 7 节）。

    `MockAgent` 是 CI 主力，见证机制若硬失败会挂掉全部 mock 测试。
    但**结构必须保留** —— 下游任务（A3 / A6 / A10）读到的字段形状
    与真实模式一致，否则 mock 会掩盖下游的字段缺失。
    """
    if not is_mock_agent():
        return read_witness(task)

    def fn(raw):
        raw["mock"] = True
        raw["phase"] = PHASE_IMPL
        raw["witnessed_at"] = raw.get("witnessed_at") or _now()
        raw["exit_code"] = EXIT_ASSERTION_FAILED
        raw.setdefault("failed_nodes", [])
        raw.setdefault("test_files", {})
        raw["green_at"] = _now()
        raw.setdefault("green_nodes", [])
        raw.setdefault("rewitness_count", 0)

    return _mutate_witness(task, fn)


# ── 门禁入口（供 hooks/check_03-coding.sh 调用）──

def _resolve_target(task: str) -> Optional[Path]:
    """从 `.state` 读 target_dir，与 `lib_run_tests.sh` 用同一个源。"""
    raw = read_state(task).get("target_dir") or ""
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = Path.cwd() / path
    return path if path.is_dir() else None


def _project_python(target: Path) -> Optional[str]:
    """项目自带的虚拟环境解释器（任务 T3 的教训）。

    agent 常在 target_dir 下建 `.venv` 装依赖再跑通测试。用 harness 的
    python3 会 ModuleNotFoundError —— 那会被判成 collection error（退出码 2），
    也就是「造红」，于是门禁与 agent 对同一份代码给出相反结论。

    解释器跟着**项目根**走，不只看 `target_dir`：任务 `welll` 把后端放在
    `backend/`，依赖也装在 `backend/venv` 里。只看顶层会连解释器都对不上，
    这是同一次误判的第二层成因（T3 的教训原先只落了一半）。

    返回**绝对路径**：`run_tests` 会 `cd` 到项目根，相对路径到那里就不存在了
    （`.state` 里存的 target_dir 就是 `repo/welll` 这种相对路径）。
    `lib_run_tests.sh` 早就显式处理过同一件事，Python 侧原先漏了 ——
    实测的后果是退出码 -1（unavailable），比原来的误判更糟。

    绝对化用 `os.path.abspath` 而**不是** `Path.resolve()`：venv 里的
    `bin/python` 是指向 `python3.12` 乃至系统解释器的符号链接，`resolve()`
    会把它解析成真身，于是 venv 的 `site-packages` 整个失效 ——
    实测 `repo/welll` 因此从 17 passed 退回退出码 2。虚拟环境靠的正是
    「从哪个路径启动」，这条链接不能跟。
    """
    roots = [target]
    real_root = resolve_pytest_root(target)
    if real_root != target:
        roots.append(real_root)
    for root in roots:
        for candidate in (root / ".venv" / "bin" / "python",
                          root / "venv" / "bin" / "python"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return os.path.abspath(str(candidate))
    return None


def _local_test_files(root: Path) -> bool:
    """``root`` 自己是否直接持有 pytest 测试文件。

    与 `_has_pytest_surface` 的分工是刻意的，两者不能互换：

    * `_has_pytest_surface` 回答「这个技术栈算不算 Python/pytest」，
      递归看到任意 `.py` 都算 —— 它决定「要不要让路」；
    * 这里回答「pytest 从这个目录跑起来能不能收集到测试」，只认
      顶层 `test_*.py` 与 `tests/` 下的测试文件 —— 它决定「在哪里跑」。

    用前者选执行目录会把 `repo/welll` 判成命中（`backend/` 下有 `.py`），
    于是仍在仓库根跑 pytest，也就复现不出修复。

    配置文件（`pytest.ini` / `pyproject.toml`）**刻意不算**：它们说明
    「这里配置了 pytest」，不说明「这里有测试」。`welll` 的仓库根两者
    都没有，靠配置文件判会一路空手。
    """
    if not root.is_dir():
        return False
    for path in root.glob("*.py"):
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            return True
    for name in _TEST_DIR_NAMES:
        sub = root / name
        if not sub.is_dir():
            continue
        for path in sub.rglob("*.py"):
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                return True
    return False


def resolve_pytest_root(target: Any) -> Path:
    """项目真正的 pytest 根目录（任务 `welll` 的教训）。

    `target_dir` 是 harness 分配的仓库根，**不一定**是项目根。agent 常写
    `backend/` + `frontend/` 这种布局：测试在 `backend/tests/` 下、写
    `from main import app`，只有 cwd 在 `backend/` 时那个 import 才成立。
    在仓库根跑 pytest 会收集期 ImportError → 退出码 2 → 判「造红」，
    而「造红」是被硬拦的。于是 agent 报 17 passed、门禁报造红，
    两边都没说谎 —— 只是跑在不同的地方。

    探测有意收得很窄，宁可退回既有行为也不猜：

    * 只有仓库根**自己没有**可收集的测试时才往下找 —— 平铺布局
      （e2e 的主路径）与 `tests/` 布局的结论完全不变；
    * 只找**一层**子目录，且跳过 `_IGNORED_DIRS`（`venv/`、
      `node_modules/` 里全是别人的测试）；
    * 命中的子目录**恰好一个**时才切换。两个以上说明这是 monorepo，
      选谁都可能错，那时留在仓库根 —— 这类项目通常在根上配了
      `pytest.ini` / `pyproject.toml` 来指路。

    「能收集到测试」用文件存在性判定，不用真跑一次 `--collect-only`：
    `welll` 的 `backend/main.py` 依赖 fastapi，只装在 `backend/venv` 里，
    拿 harness 的 python3 去试收集必然失败 —— 而选执行目录这一步
    正是为了之后能用对解释器，用「跑得通」当判据会形成循环依赖。
    """
    root = Path(str(target))
    if not root.is_dir():
        return root
    if _local_test_files(root):
        return root

    candidates: List[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in _IGNORED_DIRS:
            continue
        if child.name in _TEST_DIR_NAMES:
            # `root/tests` 本身不是项目根，它是 root 的测试目录 ——
            # 已由上面的 `_local_test_files(root)` 覆盖。
            continue
        if _local_test_files(child):
            candidates.append(child)

    if len(candidates) == 1:
        return candidates[0]
    return root


def _pytest_env(target: Path) -> Dict[str, str]:
    """src-layout 的 PYTHONPATH 与项目解释器（复用 lib_run_tests.sh 的结论）。"""
    env: Dict[str, str] = {}
    # `PYTHONPATH=src` 是相对 cwd 的，而 cwd 是项目根而非 target_dir ——
    # 子目录布局里要看的是 `backend/src`，不是 `<repo>/src`。
    if (resolve_pytest_root(target) / "src").is_dir():
        env["PYTHONPATH"] = "src"
    py = _project_python(target)
    if py:
        env["RW_PYTHON"] = py
    return env


def _run_in_target(target: Path) -> Tuple[int, Dict[str, str]]:
    """在项目**真正的** pytest 根上跑一次见证。

    冻结（`hash_test_files`）仍以 `target_dir` 为基准，两者刻意不同源：
    冻结路径是写进 `.state` 的既有记录，改基准会让存量任务的哈希键全变。
    执行目录变、记录口径不变。
    """
    return run_tests(resolve_pytest_root(target), extra_env=_pytest_env(target))


@dataclass
class GateResult:
    """门禁结论。`lines` 是要打印给用户看的原因，`ok` 决定退出码。"""
    ok: bool
    lines: List[str] = field(default_factory=list)


def check_gate(task: str) -> GateResult:
    """03 阶段门禁：按 `red_witness.phase` 分流（A2 的 5.2）。

    见证逻辑放在 post 侧而不是 pre 侧，两个原因（A2 的 5.1，已核实）：
    `_run_pre_hooks` 的失败被吞（`check=False` 且异常被 `except: pass`），
    且它的超时只有 30 秒 —— 跑测试会超。
    """
    target = _resolve_target(task)
    if target is None:
        return GateResult(True, ["跳过见证：任务目标目录不存在"])

    if is_mock_agent():
        # mock 免的是「红绿流程要真的走过一遍」，**不是**「测试要通过」。
        # 初版这里直接 return True，等于让合成见证接管整个 03 门禁 ——
        # 实测 `test_real_failure_still_blocks` 在 mock 下转红：
        # 失败的测试过闸了。mock 是 CI 主力，这个洞会让 CI 对坏实现报绿。
        # 现在与 `phase == none` 同一处置：只记录，把测试判定交回
        # `run_project_tests`（钩子按 `--phase` 的返回值决定）。
        ensure_mock_witness(task)
        return GateResult(True, [
            "⚠️ mock 模式：写入合成 red_witness（`mock: true`），未执行真实见证",
            "   测试结果仍由常规门禁判定；下游报告中该项应记为 ❓ 而非 ✅。",
        ])

    # 证据签名先校验。tampered / unsigned 都不算通过（A0 的 D0-5）。
    # absent 是正常的：首次进入 03a 时还没有任何证据。
    status = verify_evidence(read_state(task)).status
    if status in ("tampered", "unsigned"):
        return GateResult(False, [
            f"❌ 证据签名校验未通过（{status}）—— red_witness 可能被绕过受控入口修改",
            "   请检查 .state，或用 scripts/sign_existing_state.py 处理存量任务",
        ])

    phase = read_phase(task)
    if phase == PHASE_TEST:
        return _check_03a(task, target)
    if phase == PHASE_IMPL:
        return _check_03b(task, target)
    return _check_not_witnessed(task, target)


def _check_03a(task: str, target: Path) -> GateResult:
    """见证红：必须有测试文件，且退出码为 1（断言失败）。

    只在**显式进入见证流程**后才走这里（`begin_test_phase` 已写过 phase）。
    因此这里可以放心地把「测试全部通过」判为拒绝 —— 03a 的语义就是
    「只写测试、不写实现」，测试本该是红的。
    """
    frozen = hash_test_files(target)
    if not frozen:
        # 非 Python 项目要让路，不能拒绝（A2 的 R2：第一批只支持 pytest）。
        # 这里的顺序是刻意的：先问「这个项目本来就没有 pytest 语义吗」，
        # 再判「该写测试却没写」。反过来会把 npm 项目**永久堵死** ——
        # 它的测试是 *.test.js，hash_test_files 永远返空，agent 写多少
        # 测试都不会被看见，于是不存在「补上测试就能过」的自救办法。
        if not _has_pytest_surface(target):
            return _check_unsupported_stack(task, target)
        return GateResult(False, [
            "❌ 无测试：03a 阶段必须先写测试文件（test_*.py / *_test.py / tests/）",
            "   红绿流程要求测试先于实现 —— 没有测试就没有判据。",
        ] + _abandon_hint(task))

    exit_code, nodes = _run_in_target(target)
    verdict = classify_exit_code(exit_code, nodes)
    if not verdict.ok:
        bypass = _bypass_files(target, exit_code, nodes)
        if bypass:
            return _check_impl_first_bypass(task, bypass)
        return GateResult(False, [
            f"❌ 未能见证有效的红（退出码 {exit_code}）",
            f"   {verdict.reason}",
        ] + _abandon_hint(task))

    record_red(task, verdict, frozen)
    lines = [
        f"✅ 已见证有效的红（退出码 {exit_code}）",
        f"   失败节点 {len(verdict.failed_nodes)} 个: "
        + ", ".join(verdict.failed_nodes[:5])
        + (" ..." if len(verdict.failed_nodes) > 5 else ""),
        f"   已冻结测试文件 {len(frozen)} 个，进入 03b（实现阶段，禁止改测试）",
    ]
    return GateResult(True, lines)


def _check_03b(task: str, target: Path) -> GateResult:
    """实现准出：哈希未变 + 已见证节点全部转绿。

    哈希先查、测试后跑。顺序是刻意的：测试被改过时，跑出来的绿毫无意义，
    先报「你改了测试」比先报一堆通过更有信息量。
    """
    record = read_witness(task)
    frozen = record.get("test_files")
    frozen = frozen if isinstance(frozen, dict) else {}

    hash_verdict = compare_hashes(frozen, hash_test_files(target))
    if not hash_verdict.ok:
        # 拒绝必须附带可操作的下一步。只说「你改了测试」会把人逼向
        # 反复试探或改回去硬凑 —— 而测试可能真的写错了（任务 T2 的教训：
        # 门禁不给下一步，用户就只能猜）。
        return GateResult(False, [
            "❌ 测试文件哈希校验未通过",
            f"   {hash_verdict.reason}",
            "   若测试确实需要修改，用下面这条显式回退（会留痕并计数）：",
            f"     python3 -m sw_lib.workflow.red_witness {task} "
            f"--rewitness '<为什么要改测试>'",
            "   回退后仍须重新见证到真实的红 —— 它不是跳过冻结的捷径。",
        ])

    exit_code, nodes = _run_in_target(target)
    witnessed = record.get("failed_nodes")
    witnessed = witnessed if isinstance(witnessed, list) else []
    verdict = verify_green(witnessed, nodes, exit_code)
    if not verdict.ok:
        return GateResult(False, [
            f"❌ 未转绿（退出码 {exit_code}，判定 {verdict.status}）",
            f"   {verdict.reason}",
        ])

    record_green(task, verdict)
    return GateResult(True, [
        f"✅ 已见证转绿：{len(witnessed)} 个判据节点全部 passed，测试文件未被改动",
    ])


def _has_pytest_surface(target: Path) -> bool:
    """项目是否具备 pytest 语义（A2 的 R2）。

    判据与 `lib_run_tests.sh` 的 `run_project_tests` 保持一致：
    `pytest.ini` / `pyproject.toml` / 顶层 `test_*.py`，另外补上
    `tests/` 目录与 `setup.py` / `setup.cfg`（src-layout 与 tests/ 布局
    在那边是靠 pyproject 命中的，这里直接认路径更稳）。

    **不用「有没有 .py 文件」来判。** 一个 npm 项目里放个构建脚本
    `build.py` 就会被误判成 Python 项目，然后被要求写 pytest 测试。
    判据必须是「这个项目声明了自己用 pytest」，而不是「这里有 Python 代码」。
    """
    if (target / "pytest.ini").is_file() or (target / "pyproject.toml").is_file():
        return True
    if (target / "setup.py").is_file() or (target / "setup.cfg").is_file():
        return True
    for name in _TEST_DIR_NAMES:
        if (target / name).is_dir():
            return True
    # 有 Python 源码就算 Python 栈：03a 只写了 impl.py 而漏了测试，
    # 必须照旧被拦（既有契约 test_03a_no_tests_is_rejected）——
    # 让路只针对「这个栈根本没有 pytest 可言」，不是「这轮忘了写测试」。
    for path in target.rglob("*.py"):
        if not any(p in _IGNORED_DIRS for p in path.relative_to(target).parts):
            return True
    return False


def _bypass_files(target: Path, exit_code: int,
                  nodes: Dict[str, str]) -> List[str]:
    """本次「见证不到红」是否属于「实现先落盘」，是则返回实现文件清单。

    成因判定必须**收窄**，否则事后处理会变成一句「凡是见证不到就放行」：

    * 退出码必须是 0（真的跑完了、全部通过）。造红（2）与无测试（5）
      不在其中 —— 那两种不是我们拦不住，是 agent 做错了，且都有自救办法
      （改 import / 补测试），放行它们等于把 A2 整条判据交出去。
    * 必须**有节点真的 passed**。全 skip 的退出码也是 0，但没有任何断言
      被执行过；「有实现文件」不能单独构成让路的理由，否则写一堆 skip
      就是比恒真测试更省事的捷径。
    * 必须**存在实现文件**。测试全绿而目标目录里一个实现文件都没有，
      说明这些测试自证（`assert 1 == 1` 之类），没有不可抗因素可言。

    三条同时成立，才是我们确实无力阻止的那一种形态。
    """
    if exit_code != EXIT_ALL_PASSED:
        return []
    if not any(o == "passed" for o in nodes.values()):
        return []
    return find_impl_files(target)


def _check_impl_first_bypass(task: str, impl_files: List[str]) -> GateResult:
    """实现先落盘：如实记录并放行（A2 的 R4 定案修正，用户拍板）。

    **这不是「见证通过」**，是「见证被绕过且我们记下了是谁绕的」。
    前一版在这里拒绝并打印 `--abandon-witness`，实测两个真实任务都卡死 ——
    要求用户每轮手敲一条命令去确认一件 harness 已经看得很清楚的事实，
    只是把成本转嫁出去，没有多拦住任何东西。

    放行的同时守住三条（与其它让路路径同构）：phase 抹回 `none` 让测试
    判定回到 `run_project_tests`、记 `unavailable`（❓）不伪造 `green_at`、
    **理由与门禁输出都指名文件** —— 不拦人，但绕过必须留下具体痕迹。
    """
    record = record_bypass(task, impl_files)
    shown = impl_files[:5]
    tail = f" ...（共 {len(impl_files)} 个）" if len(impl_files) > 5 else ""
    return GateResult(True, [
        "⚠️ Red 见证未发生（unavailable）—— 03a 期间实现已先落盘，红无法被见证",
        f"   实现文件: {', '.join(shown)}{tail}",
        f"   本任务累计绕过 {record.get('bypass_count')} 次。"
        "红绿流程未被 harness 观测，下游报告中该项应记为 ❓ 而非 ✅。",
        "   测试结果仍由常规门禁判定（失败的测试照旧不许过闸）。",
    ])


def _check_unsupported_stack(task: str, target: Path) -> GateResult:
    """非 Python 项目：见证让路，如实记 `unavailable`（A2 的 R2）。

    与 `_check_not_witnessed` 同一条纪律，但成因不同 ——
    那边是「这一轮没走见证流程」，这边是「这个技术栈还不支持见证」。
    两者都**不接管测试判定**：`request_rewitness` 之外的所有让路路径，
    结论都要交回 `run_project_tests`，否则 npm 的失败测试会过闸。

    交回的办法是把 phase 抹回 `none` —— 钩子按 `--phase` 的返回值决定
    要不要跑 `run_project_tests`，停在 `03a` 等于让整个测试判定消失。
    """
    reason = ("非 Python 项目（未发现 pytest 判据）—— Red 见证第一批只支持 "
              "pytest，本阶段的红绿流程未被 harness 观测")
    leave_witness_flow(task)
    mark_unavailable(task, reason)
    return GateResult(True, [
        "⚠️ Red 见证未发生（unavailable）—— 该技术栈暂不支持见证",
        f"   {reason}",
        "   测试结果仍由常规门禁判定（npm test 等）；下游报告中该项应记为 ❓。",
    ])


def _check_not_witnessed(task: str, target: Path) -> GateResult:
    """尚未进入见证流程（`phase == none`）。

    命中场景：存量任务、04→03 返工的每一轮、以及见证开关刚被打开的任务。
    它们的实现早已写完，测试本来就是绿的 —— 按 03a 判定会永久无法准出
    （实测：引入见证后 9 条既有用例转红，含整条返工链路）。

    这里**不接管测试执行**：能不能过闸仍由既有的 `run_project_tests` 决定，
    于是「失败的测试不许过闸」这条既有契约保持不变。
    见证机制只做一件事 —— 如实记下「见证未发生」及原因。

    `unavailable` 不是通过（A2 的 R2/R3）：下游报告里必须显示为 ❓。
    把它记成「已见证转绿」才是真正危险的做法，那会给 A6/A10 一份假证据。
    """
    reason = ("任务未进入 Red 见证流程（存量任务、返工轮次，"
              "或见证开关刚开启）—— 本阶段的红绿流程未被 harness 观测")
    mark_unavailable(task, reason)
    return GateResult(True, [
        "⚠️ Red 见证未发生（unavailable）—— 不代表红绿流程已执行",
        f"   {reason}",
        "   测试结果仍由常规门禁判定；下游报告中该项应记为 ❓ 而非 ✅。",
    ])


_USAGE = ("用法: python3 -m sw_lib.workflow.red_witness <task-name> "
          "[--phase | --begin | --rewitness <理由> | --abandon-witness <理由>]")

_FLAGS = ("--phase", "--begin", "--rewitness", "--abandon-witness")


def _abandon_hint(task: str) -> List[str]:
    """03a 拒绝时附上的合法出路。

    拒绝必须给下一步 —— A2 的 10.6 第六行已就哈希拒绝判过同一件事：
    「拦住一条路而不给替代路径，等于把人推向绕过机制」。03a 的拒绝
    此前一条出路都没给，任务 `helloworld` 因此只能手改 `.state`。
    """
    return [
        "   若本轮确实无法见证红（例如实现已先落盘），走这条显式放弃："
        f"\n     python3 -m sw_lib.workflow.red_witness {task} "
        f"--abandon-witness '<为什么见证不到红>'",
        "   放弃会留痕并把本项记为 unavailable（❓，不是通过），"
        "测试判定交回常规门禁。",
    ]


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 入口。bash 侧的接口就是「退出码 + stdout」，所以两者都是契约。

    三种调用形态：

    * 无标志 —— 执行门禁判定。退出码 0 = 放行，1 = 拒绝。
      原因一律打印到 stdout：只回显「未通过」等于让用户去猜（任务 T2 的教训）。
    * ``--phase`` —— 只打印当前 phase（``03a`` / ``03b`` / ``none``），退出码恒 0。
      钩子据此决定要不要把测试判定交回 `run_project_tests`。
      **绝不跑测试**：它在 `set -e` 的钩子里被调用，且 pre 侧只有 30 秒。
    * ``--begin`` —— 显式进入 03a（幂等）。给 `pre_check_03-coding.sh` 用。
    * ``--abandon-witness <理由>`` —— 放弃本轮见证（理由必填，留痕并计数）。
      03a 见证不到红时唯一的合法出路；记 `unavailable` 而非通过。

    未知标志必须报错退出，不能被当成任务名吞掉 —— 前一版按位置参数解析，
    `--phase` 被当成任务名，钩子读到空串却毫无报错，分流从未生效。
    """
    args = list(argv if argv is not None else sys.argv[1:])
    flags = [a for a in args if a.startswith("-")]
    positional = [a for a in args if not a.startswith("-")]

    unknown = [f for f in flags if f not in _FLAGS]
    if unknown:
        print(f"未知参数: {' '.join(unknown)}\n{_USAGE}", file=sys.stderr)
        return 2
    if len(flags) > 1:
        print(f"{' 与 '.join(flags)} 不能同时使用\n{_USAGE}", file=sys.stderr)
        return 2

    # --rewitness 后面跟理由，因此位置参数是 <task> <理由...>
    if "--rewitness" in flags:
        if len(positional) < 2:
            print("--rewitness 必须给出理由，例如：\n"
                  "  python3 -m sw_lib.workflow.red_witness <task> "
                  "--rewitness '断言的期望值写错了'\n"
                  "理由会留痕在 .state 里 —— 没有理由的回退，"
                  "与静默改测试没有区别（A2 的 3.4）。", file=sys.stderr)
            return 2
    elif "--abandon-witness" in flags:
        # 理由必填，同 --rewitness：没有理由的放弃，与静默跳过见证无区别。
        if len(positional) < 2:
            print("--abandon-witness 必须给出理由，例如：\n"
                  "  python3 -m sw_lib.workflow.red_witness <task> "
                  "--abandon-witness '03a 期间实现已落盘，无法再见证红'\n"
                  "理由会留痕在 .state 里，并作为 unavailable 的原因"
                  "出现在下游报告中。", file=sys.stderr)
            return 2
    elif len(positional) != 1:
        print(_USAGE, file=sys.stderr)
        return 2

    task = positional[0]

    if "--phase" in flags:
        # 退出码恒 0：钩子在 `set -e` 下用 $(...) 取值，非零会中断整个门禁。
        #
        # mock 模式一律报 `none`：合成记录里的 phase 是 `03b`，但那只是为了
        # 保住字段形状，见证从未真的发生。钩子读到 `none` 才会把测试判定
        # 交回 `run_project_tests` —— 否则 mock 下失败的测试会过闸
        # （实测 `test_real_failure_still_blocks` 转红）。
        print(PHASE_NONE if is_mock_agent() else read_phase(task))
        return 0

    if "--begin" in flags:
        record = begin_test_phase(task)
        print(f"[Red Witness] phase = {record.get('phase', PHASE_NONE)}")
        return 0

    if "--rewitness" in flags:
        reason = " ".join(positional[1:]).strip()
        if not reason:
            print("--rewitness 的理由不能是空串", file=sys.stderr)
            return 2
        record = request_rewitness(task, reason)
        print(f"[Red Witness] 已回退到 03a（第 {record.get('rewitness_count')} 次）")
        print(f"   理由: {reason}")
        print("   冻结哈希已清空，判据节点保留 —— 请改好测试并重新见证到红。")
        return 0

    if "--abandon-witness" in flags:
        reason = " ".join(positional[1:]).strip()
        if not reason:
            print("--abandon-witness 的理由不能是空串", file=sys.stderr)
            return 2
        record = abandon_witness(task, reason)
        print(f"[Red Witness] 已放弃本轮见证"
              f"（第 {record.get('abandon_count')} 次）")
        print(f"   理由: {reason}")
        print("   phase 已回到 none —— 测试判定交回常规门禁。")
        print("   本项在下游报告中记为 unavailable（❓），**不是**通过。")
        return 0

    result = check_gate(task)
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1


if __name__ == "__main__":       # pragma: no cover - CLI 入口
    raise SystemExit(main())
