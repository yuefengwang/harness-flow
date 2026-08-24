"""A2 的 R4 定案修正：03a 见证不到红时，harness 自己让路并留痕。

前一版的处置是「拒绝 + 打印 `--abandon-witness` 出路」，要求人手敲一条
命令才能继续。实测两个真实任务（`helloworld`、`helloworld2`）证明那条路
走不通 —— **拒绝拦不住的东西，靠提示也拦不住**：

    13:17:17  agent 用 `bash` heredoc: cat > src/models.py << 'EOF'
    13:20:07  同上，又两个模块
    → 9 个实现模块全部落盘 → 19 个测试全部通过 → 红永远见证不到

`bash` 是我们自己发给 developer 角色的工具（`config.yaml` 的 `tools`
列了 `run_command`），而 opencode 的 session 权限规则只对 `write` / `edit`
有路径字段，对 `bash` 的参数（一整条 shell 命令）无从匹配。**我们没有
拦住 `bash` 写文件的手段**，除非彻底不给它 —— 那会废掉装依赖、跑测试。

因此定案（用户拍板）：**不再假装能拦，改为事后处理。**
03a 全绿且实现已落盘时，门禁自己 `leave_witness_flow` + `mark_unavailable`
并放行，测试判定交回 `run_project_tests`。`--abandon-witness` 退化为兜底。

代价必须写明：R1 在 Python 项目上不再有强制力 —— 任何 agent 先写实现即可
把见证降成 ❓。我们换到的是**如实**：不再有「机制存在但被绕过且无人知晓」
这种最坏形态，绕过会在 `.state` 里留下指名到文件的记录。

四条边界缺一不可，否则事后处理会变成静默放行：

1. **只对「实现已落盘」这一种成因让路。** 造红（ImportError，退出码 2）、
   全 skip、无测试都仍然拒绝 —— 那些不是「拦不住」，是 agent 做错了，
   且都有可行的自救办法。
2. **理由里必须指名实现文件。** 不拦人，但要让绕过留下具体痕迹。
   一句笼统的「无法见证红」等于把证据也一起放弃了。
3. **不伪造通过。** 记 `unavailable`（❓），不写 `green_at`。
4. **不成为「失败测试过闸」的后门。** phase 抹回 `none` 之后，
   判定交回 `run_project_tests`（10.6 第二/七行踩过两次的洞）。
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
    """走子进程调 CLI。

    **不在测试进程里直接调 `rw.request_rewitness`** —— conftest 把签名密钥
    重定向到 tmp，而钩子子进程用真实密钥，那样写出的 `.state` 会被判成
    `tampered`，红的原因就不再是被测行为（同 test_red_witness_legacy_tasks
    的 `_enter_witness_flow` 注释）。
    """
    return subprocess.run(
        [sys.executable, "-m", "sw_lib.workflow.red_witness", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


#: `helloworld2` 的现场缩影：实现先落盘，于是测试全绿，phase 停在 03a。
_IMPL_FIRST = {
    "src/__init__.py": "",
    "src/models.py": "def amount():\n    return 100\n",
    "tests/test_models.py": (
        "from src.models import amount\n\n\n"
        "def test_amount():\n    assert amount() == 100\n"
    ),
}


# ── 主路径：实现已落盘 → 自动让路 ──

def test_impl_first_in_03a_is_let_through(task):
    """03a 全绿且实现已落盘 → 放行，不再要求人手敲 `--abandon-witness`。

    这是本轮定案的核心：`bash` 绕过拦不住，拒绝只会让任务卡死在
    同一处（`helloworld` 与 `helloworld2` 各卡一次，都只能手改 `.state`）。
    """
    name, _ = task("rw-posthoc-letthrough", _IMPL_FIRST)
    _enter_witness_flow(name)
    assert rw.read_phase(name) == "03a", "前提不成立：未进入 03a"

    r = _run_hook(name)

    assert r.returncode == 0, \
        f"实现已落盘的 03a 仍然卡死 —— 事后处理未生效:\n{r.stdout}\n{r.stderr}"


def test_impl_first_is_marked_unavailable_not_pass(task):
    """让路记 `unavailable`（❓），绝不能伪造成「见证过并转绿」。

    这条是整个降级的底线。若放行顺手写了 `green_at`，A6 / A10 会读到
    一份「红绿流程走过了」的假证据 —— 那比卡死危险得多。
    """
    name, _ = task("rw-posthoc-unavailable", _IMPL_FIRST)
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0

    record = rw.read_witness(name)
    assert record.get("status") == "unavailable", record
    assert not record.get("green_at"), f"让路伪造了转绿证据:\n{record}"
    assert not record.get("witnessed_at"), f"让路伪造了见证证据:\n{record}"


def test_bypass_reason_names_the_impl_files(task):
    """理由必须**指名**是哪些实现文件先落盘了。

    不拦人，但要留下能追责的痕迹。一句笼统的「无法见证红」会让下游
    只知道「没见证」而不知道「因为实现被 bash 写进去了」——
    而后者才是 A11 变异探针与 A0 的绕过审计需要的信息。
    """
    name, _ = task("rw-posthoc-names", _IMPL_FIRST)
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0

    reason = rw.read_witness(name).get("unavailable_reason") or ""
    assert "src/models.py" in reason, \
        f"unavailable_reason 没有指名先落盘的实现文件:\n{reason}"


def test_bypass_is_recorded_as_structured_field(task):
    """绕过必须是**结构化字段**，不能只藏在一句中文理由里。

    下游（TUI 面板、04 事实包、05 报告）要按字段判定该不该显示 ❓。
    让它们去正则匹配一句中文，等于把可见性建在文案上 —— 文案一改就没了。
    """
    name, _ = task("rw-posthoc-field", _IMPL_FIRST)
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0

    record = rw.read_witness(name)
    assert record.get("bypassed") is True, record
    assert "src/models.py" in (record.get("bypass_files") or []), record


def test_let_through_returns_phase_to_none(task):
    """phase 必须抹回 `none` —— 否则测试判定整个消失。

    钩子按 `--phase` 的返回值决定要不要把判定交回 `run_project_tests`，
    停在 `03a` 等于放行之后再没人跑测试（10.6 第二/七行同一个洞）。
    """
    name, _ = task("rw-posthoc-phase", _IMPL_FIRST)
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0

    assert rw.read_phase(name) == "none", rw.read_witness(name)


def test_gate_output_says_witness_did_not_happen(task):
    """门禁输出必须明说这不是通过（A6 三态：❓ 而非 ✅）。"""
    name, _ = task("rw-posthoc-says", _IMPL_FIRST)
    _enter_witness_flow(name)

    r = _run_hook(name)

    assert r.returncode == 0
    assert "unavailable" in r.stdout or "❓" in r.stdout, \
        f"让路时门禁没说明「见证未发生」:\n{r.stdout}"
    assert "src/models.py" in r.stdout, \
        f"门禁输出没指名先落盘的实现文件:\n{r.stdout}"


# ── 边界：其它「见证不到红」的成因仍然拒绝 ──

def test_fabricated_red_is_still_rejected(task):
    """造红（ImportError，退出码 2）仍必须拒绝。

    这不是「拦不住」，是 agent 写错了 —— 测试引用了不存在的模块，
    断言从未被执行。让路只针对 `bash` 绕过这一种我们确实无力阻止的成因。
    """
    name, _ = task("rw-posthoc-fabricated", {
        "tests/test_missing.py": (
            "from src.nope import gone\n\n\n"
            "def test_gone():\n    assert gone() == 1\n"
        ),
    })
    _enter_witness_flow(name)

    r = _run_hook(name)

    assert r.returncode != 0, \
        f"造红被事后处理放过了 —— 让路的成因判定太宽:\n{r.stdout}"
    assert "造红" in r.stdout, r.stdout


def test_all_skipped_is_still_rejected(task):
    """全 skip（退出码 0 但无断言执行）仍必须拒绝。

    退出码与「实现已落盘」的情形完全相同，靠退出码分不开 ——
    必须按「有没有实现文件」来分。全 skip 的自救办法是去掉 skip 标记，
    不需要让路。
    """
    name, _ = task("rw-posthoc-skipped", {
        "tests/test_skip.py": (
            "import pytest\n\n\n"
            "@pytest.mark.skip(reason='未实现')\n"
            "def test_later():\n    assert False\n"
        ),
    })
    _enter_witness_flow(name)

    r = _run_hook(name)

    assert r.returncode != 0, \
        f"全 skip 被事后处理放过了:\n{r.stdout}"


def test_all_skipped_with_impl_present_is_still_rejected(task):
    """实现已落盘 + 测试全 skip → 仍然拒绝。

    这一条把让路的成因收窄到「测试真的跑了断言、只是全绿」。
    「有实现文件」不能单独构成让路的理由，否则任何 03a 只要 src/ 下有
    一个 .py，写一堆 skip 的测试就能过 —— 那是比恒真测试更省事的捷径。
    """
    name, _ = task("rw-posthoc-skip-impl", {
        "src/__init__.py": "",
        "src/models.py": "def amount():\n    return 100\n",
        "tests/test_skip.py": (
            "import pytest\n\n\n"
            "@pytest.mark.skip(reason='未实现')\n"
            "def test_later():\n    assert False\n"
        ),
    })
    _enter_witness_flow(name)

    r = _run_hook(name)

    assert r.returncode != 0, \
        f"「有实现 + 全 skip」被事后处理放过了 —— 让路成因判定太宽:\n{r.stdout}"


def test_green_without_impl_files_is_still_rejected(task):
    """测试全绿但**没有**实现文件 → 仍然拒绝。

    这种测试不依赖任何被测代码（`assert 1 == 1` 之类），它是自证的 ——
    没有「实现先落盘」这个不可抗因素，让路就没有理由。
    放开它等于「写个恒真测试即可跳过见证」。
    """
    name, _ = task("rw-posthoc-selfgreen", {
        "tests/test_tauto.py": "def test_ok():\n    assert 1 == 1\n",
    })
    _enter_witness_flow(name)

    r = _run_hook(name)

    assert r.returncode != 0, \
        f"无实现文件的全绿被放过了 —— 恒真测试成了跳过见证的捷径:\n{r.stdout}"


def test_no_tests_at_all_is_still_rejected(task):
    """只有实现、一个测试都没写 → 仍然拒绝。

    这里实现文件同样存在，但**没有任何判据**可言。让路的语义是
    「测试写了、红见证不到」，不是「测试没写也算」。
    """
    name, _ = task("rw-posthoc-notest", {
        "src/__init__.py": "",
        "src/models.py": "def amount():\n    return 100\n",
    })
    _enter_witness_flow(name)

    r = _run_hook(name)

    assert r.returncode != 0, \
        f"「有实现、无测试」被事后处理放过了:\n{r.stdout}"


# ── 边界：不得成为失败测试的后门 ──

def test_let_through_is_not_a_backdoor_for_failing_tests(task):
    """让路之后，真正失败的测试仍然不许过闸。

    这是 10.6 第七行判过的洞：让路时忘了抹 phase，npm 的失败测试直接
    过闸。事后处理走的是同一条让路逻辑，必须同样守住。
    """
    name, target = task("rw-posthoc-notbackdoor", _IMPL_FIRST)
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0, "前提不成立：让路本身失败了"

    # 让路之后把测试改成真的失败
    (target / "tests" / "test_models.py").write_text(
        "from src.models import amount\n\n\n"
        "def test_amount():\n    assert amount() == 999\n",
        encoding="utf-8")
    r = _run_hook(name)

    assert r.returncode != 0, \
        f"事后处理成了「失败测试过闸」的后门:\n{r.stdout}"


def test_let_through_keeps_failed_nodes_monotonic(task):
    """让路**不得**清掉已见证的 `failed_nodes` —— 判据集单调（A2 的 4.1）。

    场景：上一轮真的见证过红（判据集非空），04 返工回到 03 又撞上
    实现已落盘。这一轮的见证可以放弃，已经见证过的 bug 不能因此消失。
    """
    name, target = task("rw-posthoc-mono", {
        "test_y.py": "from impl import f\n\n\ndef test_f():\n    assert f() == 2\n",
        "impl.py": "def f():\n    return 1\n",
    })
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0, "03a 见证失败，后续无意义"
    witnessed = rw.read_witness(name)["failed_nodes"]
    assert witnessed == ["test_y.py::test_f"]

    # 回到 03a（模拟返工），此时实现已经是对的 → 见证不到红
    assert _cli(name, "--rewitness", "返工重写测试").returncode == 0
    (target / "impl.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    assert _run_hook(name).returncode == 0, "返工轮次的事后处理未生效"

    assert rw.read_witness(name)["failed_nodes"] == witnessed, \
        "事后让路把判据集清空了 —— 同一个 bug 可以再犯一次"


def test_bypass_count_accumulates_across_rounds(task):
    """多轮绕过要累加计数 —— 一次绕过和五次绕过不是一回事。

    降级之后放弃成为常态，计数是唯一能反映「这个任务有多不受约束」的量。
    只留布尔标记会让第五次绕过看起来和第一次一样干净。
    """
    name, target = task("rw-posthoc-count", _IMPL_FIRST)
    _enter_witness_flow(name)
    assert _run_hook(name).returncode == 0
    assert rw.read_witness(name).get("bypass_count") == 1, rw.read_witness(name)

    assert _cli(name, "--rewitness", "再来一轮").returncode == 0
    assert _run_hook(name).returncode == 0

    assert rw.read_witness(name).get("bypass_count") == 2, rw.read_witness(name)
