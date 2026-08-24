"""A2 的 R4 缺口：03a 见证不到红时，必须存在一条合法出路。

设计原文把「agent 在 03a 就写实现，导致测试直接绿」列为 R4，处置写的是
「退出码 0 被拒绝（验收第 4 条）即可覆盖；无需额外检测」。拒绝确实实现了，
**但只有拒绝，没有出路** —— 这是任务 `helloworld` 的真实死法：

    phase=03a，src/ 下实现已全部写完，20 个测试全部通过
    → 门禁「未能见证有效的红（退出码 0）」
    → 每次 /advance 都撞同一处，且 03a 没有任何合法出口

`request_rewitness` 只能从 03b 退回 03a，`leave_witness_flow` 没有 CLI 入口。
困在 03a 且测试已绿时，命令行上不存在能走通的路径，只能手改 `.state`。

这与 A2 的 10.6 第六行是同一条判例：「拦住一条路而不给替代路径，等于把人
推向绕过机制」。那次补的是 `--rewitness`，这次补 `--abandon-witness`。

四条约束缺一不可，否则出路本身会变成机制的后门：

1. 理由必填并留痕计数 —— 无理由的放弃就是静默跳过见证换个说法；
2. **不伪造通过**：记 `unavailable`（A6 三态的 ❓），绝不写 `green_at`；
3. phase 必须抹回 `none` —— 否则钩子不把判定交回 `run_project_tests`，
   等于整个测试判定消失（10.6 第二/七行同一个洞，已被踩过两次）；
4. **不清 `failed_nodes`** —— 判据集单调（A2 的 4.1）。

---

## 本文件已按 DEV-PROTOCOL 1.2 显式重做（勿静默回退）

初版的夹具 `_deadlocked` 用「实现已落盘 → 测试全绿」这一形态制造死锁，
并断言它必须被拒绝。**该形态现在会被门禁自动让路**（`_check_impl_first_bypass`，
见 test_red_witness_post_hoc.py），因此夹具与两条前提自检必须重做。

重做的依据是用户拍板：`bash` 绕过我们拦不住（session 权限规则只对
write/edit 有路径字段），要求人每轮手敲 `--abandon-witness` 去确认一件
harness 已经看清的事实，只是转嫁成本。A2 验收第 4 条中「实现先落盘」
这一支因此降级为事后处理，**其余各支未被放开**。

被重做的是「用什么形态制造 03a 拒绝」，不是「03a 能不能被放宽」：
新夹具改用**恒真测试且无实现文件**（`assert 1 == 1`），它现在仍然被拒绝 ——
自证的测试没有任何不可抗因素，让路没有理由。整条 `--abandon-witness`
契约（理由必填、留痕计数、记 unavailable、不做失败测试的后门）**一字未改**：
造红、全 skip、无测试、恒真全绿这四种拒绝仍然存在，人仍然需要那条出路。
"""

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from sw_lib.core.config import HOOKS_DIR, TASKS, is_mock_agent
from sw_lib.workflow import red_witness as rw

ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _requires_real_agent_mode():
    """真实见证行为的前提：非 mock 模式（理由同 test_red_witness_gate.py）。"""
    if is_mock_agent():
        pytest.skip("mock 模式不执行真实见证；相关契约见 test_red_witness_mock_mode.py")


@pytest.fixture
def task(tmp_path):
    created = []

    def _make(name, files):
        target = tmp_path / name
        for rel, body in files.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        d = TASKS / name
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / ".state").write_text(json.dumps({
            "id": name, "stage": "03-coding", "stage_idx": 2,
            "stage_status": "running", "target_dir": str(target),
        }), encoding="utf-8")
        created.append(d)
        return name, target

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


def _run_hook(name):
    return subprocess.run(
        ["bash", str(HOOKS_DIR / "check_03-coding.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def _enter_witness_flow(name):
    return subprocess.run(
        [str(HOOKS_DIR / "pre_check_03-coding.sh"), name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "sw_lib.workflow.red_witness", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


#: 03a 仍会被拒绝的一种形态：测试自证（恒真）且目标目录里没有实现文件。
#:
#: 刻意**不用**「实现已落盘 → 全绿」那一形态：它现在由
#: `_check_impl_first_bypass` 自动让路（本文件顶部的重做声明）。
#: 恒真测试没有「实现被 bash 先写进去」这个不可抗因素，让路无从谈起，
#: 因此它是验证「出路本身」时最干净的靶子。
_SELF_GREEN = {
    "tests/test_tautology.py": "def test_ok():\n    assert 1 == 1\n",
}


def _stuck_in_03a(task, name, files=None):
    """把任务置入「03a 被拒绝」的状态，并确认它确实卡住了。"""
    name, target = task(name, files if files is not None else _SELF_GREEN)
    _enter_witness_flow(name)
    assert rw.read_phase(name) == "03a", "前提不成立：未进入 03a"
    r = _run_hook(name)
    assert r.returncode != 0, f"前提不成立：03a 竟然过闸了:\n{r.stdout}"
    return name, target


# ── 死锁现场必须可复现（判据的前提）──

def test_self_green_in_03a_is_still_rejected(task):
    """出路的存在不能让「见证不到红也能过」——「自证全绿」仍必须拒绝。

    A2 验收第 4 条现在只对「实现先落盘」那一支降级（见文件顶部的重做
    声明）。其余各支必须原样拒绝，否则出路就成了后门：恒真测试是最省事
    的一种，`assert 1 == 1` 不依赖任何被测代码，放开它等于宣布 03a 不设防。
    """
    name, _ = _stuck_in_03a(task, "rw-abandon-still-rejected")
    r = _run_hook(name)
    assert r.returncode != 0
    assert "未能见证有效的红" in r.stdout, r.stdout


# ── 门禁必须告知出路 ──

def test_03a_rejection_tells_user_how_to_abandon(task):
    """03a 拒绝时必须打印出路 —— 出路存在但没人知道，等于不存在。

    降级为事后处理**没有**让这条失效：造红、全 skip、无测试、恒真全绿
    四种拒绝依然存在，人仍然需要一条留痕的出路。

    这是 A2 的 10.6 第六行判过的同一件事：哈希拒绝当初也是「拦住却不
    给下一步」，补 `--rewitness` 时一并要求门禁把命令打出来。
    """
    name, _ = _stuck_in_03a(task, "rw-abandon-hint")
    r = _run_hook(name)
    assert r.returncode != 0
    assert "--abandon-witness" in r.stdout, \
        f"03a 拒绝时没告诉用户任何合法出路:\n{r.stdout}"


def test_03a_no_test_rejection_also_tells_user_the_way_out(task):
    """另一条 03a 拒绝路径（有 pytest 面但没写测试）同样要给出路。

    否则「该写测试却写不出来」的任务仍然只能手改 .state。
    """
    name, _ = task("rw-abandon-hint-notest", {
        "src/__init__.py": "",
        "src/models.py": "def amount():\n    return 100\n",
    })
    _enter_witness_flow(name)
    r = _run_hook(name)
    assert r.returncode != 0
    assert "--abandon-witness" in r.stdout, \
        f"「无测试」拒绝路径没给出路:\n{r.stdout}"


# ── CLI 入口 ──

def test_abandon_returns_phase_to_none(task):
    """`--abandon-witness` 把 phase 抹回 `none`。

    必须是 `none` 而不是留在 03a：钩子按 `--phase` 决定要不要把测试判定
    交回 `run_project_tests`，停在 03a 等于判定消失（10.6 第二/七行）。
    """
    name, _ = _stuck_in_03a(task, "rw-abandon-phase")

    r = _cli(name, "--abandon-witness", "03a 期间实现已写完，无法再见证红")

    assert r.returncode == 0, f"--abandon-witness 失败:\n{r.stdout}\n{r.stderr}"
    assert rw.read_phase(name) == "none", rw.read_witness(name)


def test_abandon_records_reason_and_counts(task):
    """放弃必须留痕并计数 —— 与 `--rewitness` 同一条纪律。"""
    name, _ = _stuck_in_03a(task, "rw-abandon-trace")

    assert _cli(name, "--abandon-witness", "实现先落盘了").returncode == 0

    record = rw.read_witness(name)
    assert record.get("abandon_reason") == "实现先落盘了", record
    assert record.get("abandon_count") == 1, record
    assert record.get("abandoned_at"), record


def test_abandon_requires_a_reason(task):
    """无理由的放弃等于静默跳过见证 —— 必须拒绝，且不得改动 phase。"""
    name, _ = _stuck_in_03a(task, "rw-abandon-noreason")

    r = _cli(name, "--abandon-witness")

    assert r.returncode != 0, "无理由的放弃被接受了"
    assert rw.read_phase(name) == "03a", "被拒绝的放弃竟然改了 phase"


def test_abandon_rejects_empty_reason(task):
    """空串理由同样不算理由。"""
    name, _ = _stuck_in_03a(task, "rw-abandon-blankreason")

    r = _cli(name, "--abandon-witness", "   ")

    assert r.returncode != 0, "空串理由被接受了"
    assert rw.read_phase(name) == "03a", rw.read_witness(name)


# ── 不得伪造通过 ──

def test_abandon_marks_unavailable_not_pass(task):
    """放弃记 `unavailable`（❓），绝不能伪造成「见证过并转绿」。

    `mark_unavailable` 的 docstring 写明：写 `green_at` / `failed_nodes`
    会给 A6/A10 一份假证据，让 ❓ 被静默升级成 ✅。
    """
    name, _ = _stuck_in_03a(task, "rw-abandon-unavailable")

    assert _cli(name, "--abandon-witness", "见证不到红").returncode == 0

    record = rw.read_witness(name)
    assert record.get("status") == "unavailable", record
    assert not record.get("green_at"), f"放弃伪造了转绿证据:\n{record}"
    assert record.get("unavailable_reason"), record


def test_abandon_keeps_failed_nodes_monotonic(task):
    """放弃**不得**清掉 `failed_nodes` —— 判据集单调（A2 的 4.1）。

    与 `--rewitness` 同一条边界：这一轮的见证可以放弃，但已经见证过的
    bug 不能因此从判据集里消失。
    """
    name, target = task("rw-abandon-mono", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0, "03a 见证失败，后续无意义"
    witnessed = rw.read_witness(name)["failed_nodes"]
    assert witnessed == ["test_y.py::test_f"]

    assert _cli(name, "--abandon-witness", "本轮放弃").returncode == 0

    assert rw.read_witness(name)["failed_nodes"] == witnessed, \
        "放弃把判据集清空了 —— 同一个 bug 可以再犯一次"


# ── 放弃之后：测试判定必须交回 run_project_tests ──

def test_abandon_then_green_tests_can_advance(task):
    """放弃之后，全绿的项目可以过闸 —— 死锁真的解开了。

    这是整条改动的目的：`helloworld` 那样的现场要能继续往下走。
    """
    name, _ = _stuck_in_03a(task, "rw-abandon-unblocks")

    assert _cli(name, "--abandon-witness", "实现已先落盘").returncode == 0
    r = _run_hook(name)

    assert r.returncode == 0, f"放弃之后仍然卡死:\n{r.stdout}\n{r.stderr}"


def test_abandon_then_failing_tests_still_blocked(task):
    """放弃**不是**让失败的测试过闸的后门。

    phase 抹回 `none` 之后，判定交回 `run_project_tests`，「失败的测试
    不许过闸」这条既有契约必须继续成立。这正是 10.6 第七行踩过的洞：
    让路时忘了抹 phase，npm 的失败测试直接过闸。
    """
    name, target = _stuck_in_03a(task, "rw-abandon-notbackdoor")
    assert _cli(name, "--abandon-witness", "放弃见证").returncode == 0

    # 放弃之后把测试改成真的失败
    (target / "tests" / "test_tautology.py").write_text(
        "def test_ok():\n    assert 1 == 999\n", encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"放弃成了「失败测试过闸」的后门:\n{r.stdout}"


def test_abandon_reports_stay_questionable_downstream(task):
    """放弃后门禁输出必须明说这不是通过（A6 三态：记 ❓ 而非 ✅）。"""
    name, _ = _stuck_in_03a(task, "rw-abandon-says-unavailable")
    assert _cli(name, "--abandon-witness", "见证不到红").returncode == 0

    r = _run_hook(name)

    assert r.returncode == 0
    assert "unavailable" in r.stdout or "❓" in r.stdout, \
        f"放弃之后门禁没说明「见证未发生」:\n{r.stdout}"


# ── 幂等与边界 ──

def test_abandon_is_idempotent_on_count(task):
    """连续放弃两次：计数累加，不炸，phase 仍是 `none`。"""
    name, _ = _stuck_in_03a(task, "rw-abandon-twice")

    assert _cli(name, "--abandon-witness", "第一次").returncode == 0
    assert _cli(name, "--abandon-witness", "第二次").returncode == 0

    record = rw.read_witness(name)
    assert record.get("abandon_count") == 2, record
    assert rw.read_phase(name) == "none", record
    assert record.get("abandon_reason") == "第二次", record


def test_abandon_cannot_combine_with_other_flags(task):
    """与既有 CLI 契约一致：多个标志同时出现必须报错。"""
    name, _ = _stuck_in_03a(task, "rw-abandon-combo")

    r = _cli(name, "--abandon-witness", "理由", "--rewitness")

    assert r.returncode == 2, f"多标志组合没被拒绝:\n{r.stdout}\n{r.stderr}"
