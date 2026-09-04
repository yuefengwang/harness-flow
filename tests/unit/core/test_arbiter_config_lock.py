"""A14：仲裁器缺口的配置锁 —— 「填了配置就静默卡住」必须在启动时被拦住。

实测背景（A5 的 11.1）：`harness.review.subjective` 填两个审查者后，
04 阶段的 fan-out 跑完、`arbiter` 节点 `return {}`，于是 `StageOutput.route`
为 `None`、TUI 推不动阶段。仲裁逻辑归 A9，尚未实施。

守护栏此前只存在于 `test_review_arbiter_contract.py` —— 那要求改配置的人
记得跑一遍实测 505 秒的全量套件。本模块把它挪到**配置层**，
让改完配置启动 harness 的人必然看到。

断言纪律：必须断言**抛 ConfigError**，而不是「返回值里带个 warn」——
warn 会被 `assert_config_valid` 过滤掉，等于没拦（A4 的 9.2 同一条）。
"""

import copy

import pytest

from sw_lib.core import config as C


@pytest.fixture(autouse=True)
def _restore_global_config():
    """deepcopy 还原全局单例（A4 的 9.1：嵌套 RoleConfig 浅拷贝还原不了）。"""
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _role():
    return C.RoleConfig(agent="opencode", model="opencode/big-pickle",
                        description="", tools=["read_file"])


def _set_reviewers(n, providers=None):
    """配 n 个主观审查者。默认给不同 provider，避免撞上异构性校验。"""
    names = ["adversary", "design_critic", "third_eye"][:n]
    provs = providers or ["opencode", "gemini", "claude"]
    cfg = C._manager.config
    cfg.roles = {
        x: C.RoleConfig(agent=provs[i % len(provs)],
                        model=f"{provs[i % len(provs)]}/m{i}",
                        description="", tools=["read_file"])
        for i, x in enumerate(names)
    }
    cfg.roles["developer"] = C.RoleConfig(agent="mistral", model="mistral/dev",
                                          description="", tools=["write_file"])
    cfg.roles.setdefault("reviewer", _role())
    cfg.stage_roles = {"04-review": "reviewer", "03-coding": "developer"}
    cfg.review = C.ReviewConfig(
        objective_enabled=True,
        subjective=[C.SubjectiveReviewer(role=x, model=f"{provs[i % len(provs)]}/m{i}",
                                         kind="counterexample" if i == 0 else "design_review")
                    for i, x in enumerate(names)])


def _codes(issues):
    return {i.code for i in issues}


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


CODE = "review_needs_unimplemented_arbiter"


# ── U14-1：多审查者 + 仲裁器未实现 = error ──

def test_multi_reviewer_without_arbiter_is_config_error():
    """实测会让 04 卡住的配置，必须在配置层被判为 error。"""
    _set_reviewers(2)
    assert CODE in _codes(_errors(C.validate_config()))


def test_multi_reviewer_without_arbiter_raises():
    """必须抛 ConfigError —— warn 会被 assert_config_valid 过滤掉，等于没拦。"""
    _set_reviewers(2)
    with pytest.raises(C.ConfigError):
        C.assert_config_valid()


def test_error_message_names_two_executable_next_steps():
    """失败信息必须给出可执行的下一步（criterion-design 的失败信息要求）。

    任务 qqqq 的两次 /advance 输出逐字相同，就是因为拦住一条路却不给替代
    路径（A2 的 10.6）。这里的两条路是「减到 1 个」与「实施 A9」。
    """
    _set_reviewers(2)
    msg = [i.message for i in C.validate_config() if i.code == CODE][0]
    assert "A9" in msg, "未指出实施 A9 这条路"
    assert "1" in msg, "未指出减到 1 个审查者这条路"


# ── U14-2：单审查者不受影响（既有行为不变）──

def test_single_reviewer_is_not_blocked():
    """单审查者路径自带 route 与 gate_passed（A5 验收 7），不得被拦。"""
    _set_reviewers(1)
    assert CODE not in _codes(C.validate_config())


def test_zero_reviewer_is_not_blocked_by_this_rule():
    """零审查者由既有的 review_has_no_reviewer 管，不该被本判据重复拦。"""
    _set_reviewers(0)
    assert CODE not in _codes(C.validate_config())


# ── U14-3：反恒真 —— A9 落地后判据必须自动失效 ──

def test_rule_disappears_once_arbiter_is_implemented(monkeypatch):
    """判据非恒真：`ARBITER_IMPLEMENTED` 为 True 时不得再拦多审查者。

    这条是本模块最重要的测试。若实现漏读 ARBITER_IMPLEMENTED，
    A9 落地后守护栏会变成「拦住正确配置」的新缺陷 —— 而那种缺陷
    比今天的缺口更难查，因为它看起来像是判据在正常工作。
    """
    from sw_lib.workflow import review_graph as RG
    monkeypatch.setattr(RG, "ARBITER_IMPLEMENTED", True)
    _set_reviewers(2)
    assert CODE not in _codes(C.validate_config())


def test_three_reviewers_also_blocked():
    """不是只针对 2 个 —— 任何 >= 2 都会走 fan-in，都需要归约。"""
    _set_reviewers(3)
    assert CODE in _codes(_errors(C.validate_config()))


# ── U14-7：接线 —— 判据必须有生产调用点，否则是 S7 ──

def test_assert_config_valid_has_production_caller():
    """`assert_config_valid` 不得只被测试调用（形状 S7）。

    实测发现（A14 的 2.2）：改造前它是「定义 1 处、测试 3 处、生产 0 处」。
    `validate_config` 里躺着 6 条 error 级校验，从未在生产路径上执行过。
    只往里加第 7 条，等于交付第 5 个 S7 实例。
    """
    import subprocess
    from sw_lib.core.config import ROOT

    out = subprocess.run(
        ["rg", "-n", "assert_config_valid", "sw_lib/", "bin/", "hooks/"],
        cwd=str(ROOT), capture_output=True, text=True).stdout

    callers = [ln for ln in out.splitlines()
               if "def assert_config_valid" not in ln]
    assert callers, (
        "assert_config_valid 没有任何生产调用点 —— 判据存在但无人调用（S7）")


def test_bootstrap_rejects_invalid_config():
    """配置错误必须在**建图之前**拦住，而不是建完一张错的图再报错。"""
    import copy as _copy
    from sw_lib.core import bootstrap as B
    from sw_lib.workflow.runtime import WorkflowRuntime

    _set_reviewers(2)
    # bootstrap 是幂等的、带模块级缓存，测试须先清空缓存才能观察到校验。
    saved_exec, saved_rt = B._executor, WorkflowRuntime._executor
    B._executor = None
    WorkflowRuntime._executor = None
    try:
        with pytest.raises(C.ConfigError):
            B.bootstrap()
        assert WorkflowRuntime._executor is None, (
            "配置错误时不得已经建好图 —— 校验必须在 initialize 之前")
    finally:
        B._executor, WorkflowRuntime._executor = saved_exec, saved_rt


def test_cli_does_not_swallow_config_error():
    """`_ensure_bootstrapped` 的 `except: pass` 不得吞掉 ConfigError。

    这是本任务最可能的失败方式：校验加了、接线接了，第三处静默吞掉，
    于是判据永远不会被人看见 —— S7 换了个形态复发。
    """
    import copy as _copy
    from sw_lib.cli import main as M
    from sw_lib.core import bootstrap as B
    from sw_lib.workflow.runtime import WorkflowRuntime

    _set_reviewers(2)
    saved_exec, saved_rt = B._executor, WorkflowRuntime._executor
    B._executor = None
    WorkflowRuntime._executor = None
    try:
        with pytest.raises(C.ConfigError):
            M._ensure_bootstrapped()
    finally:
        B._executor, WorkflowRuntime._executor = saved_exec, saved_rt


# ── 兄弟实例：config.yaml 语法错误时静默退化为默认值 ──
#
# 本轮 sweep 发现（A14 的 2.4）。形状与 A14 主判据同源：
# 「读不到判据的输入时判通过」。
#
# 现场：实施 A14 期间手工编辑 config.yaml 造成一处缩进错误，
# `sw list` 照常运行、零报错。实测 `_load_raw_yaml` 的
# `except Exception: pass` 让整份配置退化为默认值：
# roles 从 6 个变 0 个、stage_roles 变空字典。
#
# 后果比「配错一个角色名」严重得多：A4 的全部目的是用不同 provider
# 消除盲区，而一处缩进错误让所有角色配置消失且无人知晓。

def test_broken_yaml_is_not_silently_ignored(tmp_path, monkeypatch):
    """`config.yaml` 语法错误必须响亮失败，不得退化为默认值。

    A4 的 2.3 已经消除了「配置指向不存在的角色时静默兜底到 gemini」，
    但语法错误这条路径留在原地 —— 它比前者更彻底：**整份配置**消失。
    """
    from sw_lib.core import config as CC

    # 真正的语法错误。注意 `review:` 后面接列表项在 YAML 里是**合法的**
    # （实施本测试时先踩了这个坑）—— 必须用 yaml.safe_load 真的抛
    # YAMLError 的样本，否则测的是「解析成功后字段缺失」，那是另一件事。
    broken = tmp_path / "config.yaml"
    broken.write_text("harness:\n  a: 1\n   b: 2\n", encoding="utf-8")
    monkeypatch.setattr(CC, "CONFIG_DIR", tmp_path)

    # ConfigManager 是单例且 `_config` 是**类属性** —— `ConfigManager()`
    # 拿到的是已缓存的全局配置，不会重新读盘（实施时踩到）。
    # 必须显式 reload() 才能观察加载路径。
    with pytest.raises(CC.ConfigError) as ei:
        CC.ConfigManager().reload()

    msg = str(ei.value)
    assert "config.yaml" in msg, "报错未指出是哪个文件"
    assert "语法" in msg or "解析" in msg, "报错未说明是解析失败"


def test_missing_config_file_still_uses_defaults(tmp_path, monkeypatch):
    """反恒真：文件**不存在**时仍按默认值走，不得一并变成硬失败。

    区分特征：「文件没有」是合法的首次运行状态（README 让用户从
    credentials-template.yaml 复制），而「文件有但语法坏」意味着
    有人改坏了它。两者处置不同。
    """
    from sw_lib.core import config as CC

    monkeypatch.setattr(CC, "CONFIG_DIR", tmp_path)  # 空目录，无 config.yaml
    m = CC.ConfigManager()
    m.reload()          # 不得抛
    assert m.config is not None


def test_cli_reports_config_error_readably_not_as_traceback():
    """配置错误必须以可读文本 + 非零退出码呈现，不是裸 traceback。

    实测（A14 实施期）：接上校验后 `./sw list` 打印 12 行 Python 调用栈，
    退出码却是 0。两个问题：
      - 裸 traceback 把「你的配置写错了」表述成「程序崩了」；
      - 退出码 0 让脚本与 CI 认为一切正常 —— 判据形同虚设。

    这与 A2 的 10.6 同一条纪律：拦住一条路时必须让人看懂发生了什么。
    """
    import os
    import subprocess
    import sys
    from sw_lib.core.config import ROOT

    # 用 sys.executable 而非字面 "python3"，并继承当前环境。
    # 实测（A14 实施期）：传极简 env 会让子进程落到系统 python，`import yaml`
    # 直接 ModuleNotFoundError，输出里出现 Traceback —— 断言随之变红，
    # 但红的原因是脚手架挑错了解释器，不是被测行为出问题。
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         "from sw_lib.core.config import ConfigError;"
         "from sw_lib.cli.main import main;"
         "sys.argv = ['sw', 'list'];"
         "sys.exit(main() or 0)"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        env={**os.environ, "SW_NON_INTERACTIVE": "1"})
    combined = r.stdout + r.stderr
    # 当前仓库配置是合法的，所以这里只钉「不崩栈」这一半；
    # 「拦得住」由 test_cli_does_not_swallow_config_error 负责。
    assert "Traceback (most recent call last)" not in combined, (
        f"CLI 以裸 traceback 呈现错误：\n{combined[:400]}")
