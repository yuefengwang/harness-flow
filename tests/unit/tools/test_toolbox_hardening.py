"""A0 第三层 D0-7：Toolbox 加固。

对应 docs/design/A0-state-integrity.md 的 D0-7，覆盖 2.4 实测的六条绕过路径。

**为什么当前不在主路径上也要修**：config.yaml 五个角色的 agent 全是
opencode，而 Toolbox 只被 agents/gemini.py:18 引用。但切回 gemini 或接入
自研 agent 时，缺口会一次性全部暴露。

**定性（必须写在这里，防止后人误解）**：命令白名单**不是安全边界**，
它是误操作护栏。`python -c "open('...','w')"` 这类路径无法用白名单堵住，
真正的兜底是 D0-5 的 HMAC 校验。
"""

import shutil

import pytest

from sw_lib.core.config import ROOT, WORKSPACE
from sw_lib.tools.toolbox import (
    DEFAULT_ALLOWED_COMMANDS,
    ReadFileTool,
    RunCommandTool,
    WriteFileTool,
)


@pytest.fixture
def probe_dir():
    """在真实 repo/ 下开一块探针目录 —— _safe_path 绑定真实 ROOT，无法用 tmp_path 替代。"""
    d = ROOT / "repo" / ".harness-d07-probe"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ── 2.4 第 1 条：保护范围按路径前缀，而非文件名 ──

def test_write_file_denies_facts_dir():
    """facts/ 是 A3 事实包落点，agent 写它等于伪造判据。

    实测（2.4 第 1 条）：改前 WriteFileTool 写 facts/tests.json 返回成功，
    因为 :129 只匹配 target.name。
    """
    res = WriteFileTool()("workspace/tasks/_d07_probe_task/facts/tests.json", '{"passed": 999}')

    assert "禁止" in res, f"facts/ 未受保护，返回: {res}"


def test_write_file_denies_evidence_dir():
    res = WriteFileTool()("workspace/tasks/_d07_probe_task/evidence/x.log", "all green")

    assert "禁止" in res, f"evidence/ 未受保护，返回: {res}"


def test_write_file_denies_state_in_nested_task_dir():
    """按名字匹配时这条本就是绿的，改成前缀匹配后必须保持绿（防回归）。"""
    res = WriteFileTool()("workspace/tasks/_d07_probe_task/.state", "{}")

    assert "禁止" in res


def test_write_file_still_allows_repo_code(probe_dir):
    """加固不得堵死 coding 阶段 —— 代码落 repo/，必须仍可写。

    这条是**反向验收**：只测「拦住了」会让人把 workspace 全禁当成成功，
    而那会让整个工作流死锁。
    """
    rel = probe_dir.relative_to(ROOT) / "hello.py"
    res = WriteFileTool()(str(rel), "print(1)\n")

    assert "成功" in res, f"正常代码写入被误拦: {res}"
    assert (probe_dir / "hello.py").read_text(encoding="utf-8") == "print(1)\n"


def test_write_file_denies_state_filename_anywhere():
    """`.state` / `STATUS.json` 这两个名字在任何目录下都禁写。

    前缀匹配之外再加一层名字匹配，原因是既有用例
    `test_run_command_state_file_protected` 用的是 ROOT 下的 `.state`
    （不在 workspace/ 内）。这两个名字是 harness 自己的状态文件名，
    agent 没有任何正当理由创建同名文件；任务目录若被搬迁，前缀规则会漏。
    """
    assert "禁止" in WriteFileTool()("repo/.state", "{}")
    assert "禁止" in WriteFileTool()("repo/sub/STATUS.json", "{}")


# ── 密钥不得被 agent 读走（文档 D0-7 未列，实测新增）──

def test_read_file_denies_evidence_key():
    """签名密钥可读 = HMAC 形同虚设。

    D0-5 已阻止密钥随环境下传，但 ReadFileTool 对任意 ROOT 内文件无限制，
    `read_file("config/.evidence_key")` 能直接把密钥取走。
    """
    res = ReadFileTool()("config/.evidence_key")

    assert "禁止" in res, f"密钥文件可被 read_file 读走，返回: {res[:80]}"


def test_run_command_denies_reading_key_via_cat():
    """`cat` 在白名单内，因此必须在路径层面拦。"""
    res = RunCommandTool()("cat config/.evidence_key")

    assert "禁止" in res, f"密钥可经 cat 读走，返回: {res[:80]}"


# ── 2.4 第 2 条：从命令字符串匹配改为 argv 路径前缀校验 ──

def test_run_command_denies_writing_state_via_sed():
    res = RunCommandTool()("sed -i s/a/b/ workspace/tasks/_d07_probe_task/.state")

    assert "禁止" in res, f"sed 改 .state 未被拦: {res[:80]}"


def test_run_command_allows_state_word_in_commit_message():
    """字符串匹配的代价是误拦。

    改前 `".state" in command` 会把 `git commit -m "fix .state parsing"` 拦掉。
    路径前缀校验必须放行它 —— 否则 agent 无法正常提交。
    """
    res = RunCommandTool()('git commit -m "fix .state parsing" --dry-run')

    assert "禁止" not in res, f"提交信息里的 .state 被误拦: {res[:120]}"


# ── 2.4 第 4 条：restricted 不得作为 agent 可关闭的开关 ──

def test_protection_cannot_be_disabled_by_tool_argument():
    """实测（2.4 第 4 条）：restricted 出现在 description 里，是 LLM 可见入参，
    传 False 即关闭状态文件保护。判据不能由被判者开关。
    """
    res = RunCommandTool()("sed -i s/a/b/ workspace/tasks/_d07_probe_task/.state",
                           restricted=False)

    assert "禁止" in res, f"传 restricted=False 关掉了保护: {res[:80]}"


def test_restricted_not_advertised_in_description():
    assert "restricted" not in RunCommandTool().description, (
        "description 里仍在教 LLM 用 restricted 关闭保护"
    )


# ── 2.4 第 6 条：cwd 必须做 ROOT 前缀校验 ──

def test_run_command_denies_cwd_outside_root():
    """实测：cwd="/Users/yfwang" 成功列出了家目录。"""
    res = RunCommandTool()("ls", cwd="/Users/yfwang")

    assert "禁止" in res, f"cwd 越界未被拦: {res[:120]}"


def test_run_command_denies_cwd_escape_via_parent():
    res = RunCommandTool()("ls", cwd="../..")

    assert "禁止" in res, f"cwd 用 .. 逃逸未被拦: {res[:120]}"


def test_run_command_allows_cwd_inside_root(probe_dir):
    """反向验收：ROOT 内的 cwd 必须仍然可用。"""
    rel = str(probe_dir.relative_to(ROOT))
    res = RunCommandTool()("pwd", cwd=rel)

    assert "禁止" not in res, f"合法 cwd 被误拦: {res[:120]}"
    assert "Exit Code: 0" in res


# ── 2.4 第 3、5 条：白名单移除 shell ──

def test_shell_interpreters_not_whitelisted():
    """`sh -c` 可执行白名单外命令（实测 /bin/rm 可达），白名单只看首 token。"""
    for shell in ("sh", "bash", "zsh"):
        assert shell not in DEFAULT_ALLOWED_COMMANDS, (
            f"{shell} 在白名单内，等于白名单可被一层 -c 完全绕过"
        )


def test_run_command_denies_sh_dash_c():
    res = RunCommandTool()("sh -c 'echo hi'")

    assert "不在允许" in res, f"sh -c 未被拦: {res[:80]}"


# ── 已知缺口：护栏堵不住解释器（验收 14 未达成，验收 16 兜底）──

def test_python_dash_c_write_is_not_blocked_by_guard(tmp_path, monkeypatch):
    """**这条固化一个已知缺口，不是护栏生效的证明。**

    实测：以下两种写法均**未被拦下** ——

        python3 -c "open(chr(46)+'state','w').write('{}')"
        python3 -c "open('workspace/tasks/T1/.state','w')"

    原因是路径不在 argv 里，而在解释器的字符串字面量里。argv 前缀校验
    在原理上就看不到它。`python` 又必须留在白名单（跑测试要用）。

    A0 的 1.1 与 D0-7 注释都已把白名单定性为「误操作护栏」而非安全边界，
    这条测试把该定性钉成可执行的事实：**它转红意味着有人误以为堵住了。**
    真正兜底的是验收 16 —— 绕过去写入之后，签名校验必须报 tampered
    （见 test_evidence.py::test_tampered_evidence_is_detected）。
    """
    from sw_lib.tools.toolbox import RunCommandTool

    # 用 tmp 内的路径演示，不碰真实 workspace（实测中我曾因此截断真实 .state）
    victim = tmp_path / ".state"
    victim.write_text('{"important": true}', encoding="utf-8")
    code = f"open({str(victim)!r},'w').write('{{}}')"

    res = RunCommandTool()(f'python3 -c "{code}"')

    assert "禁止" not in res, (
        "护栏拦下了 python -c —— 若已实现解释器级约束，请更新本测试与 A0 的 1.1")
    assert victim.read_text(encoding="utf-8") == "{}", "写入未发生，用例前提失效"


def test_bypass_write_is_still_detected_by_signature(tmp_path, monkeypatch):
    """验收 16：越过护栏写入 .state 之后，签名校验必须报 tampered。

    这是「阻止失败时检出仍然工作」的唯一证明。若这条测不出 tampered，
    整个第二层就是装饰。
    """
    import json
    from sw_lib.core import evidence as ev
    from sw_lib.core import state as state_mod

    monkeypatch.setattr(state_mod, "TASKS", tmp_path)
    monkeypatch.setattr(ev, "KEY_PATH", tmp_path / ".evidence_key")
    ev.get_key.cache_clear()
    name = "t-bypass"
    (tmp_path / name).mkdir(parents=True, exist_ok=True)

    state_mod.update_state(name, lambda st: {
        **st, "id": name, "red_witness": {"failed_nodes": ["t.py::a"]}})
    assert ev.verify_evidence(state_mod.read_state(name)).status == "valid"

    # 模拟护栏之外的写入（python -c 那条路径最终就是这个效果）
    sf = state_mod.state_path(name)
    raw = json.loads(sf.read_text(encoding="utf-8"))
    raw["red_witness"]["failed_nodes"] = []
    sf.write_text(json.dumps(raw), encoding="utf-8")

    res = ev.verify_evidence(state_mod.read_state(name))
    assert res.status == "tampered", f"越界写入未被检出，实际 {res.status}"
    ev.get_key.cache_clear()
