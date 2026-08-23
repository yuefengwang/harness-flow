"""A0 第二层：证据签名与三态校验。

对应 docs/design/A0-state-integrity.md 的 D0-4 / D0-5，验收标准第 6、7、12、13、16 条。

为什么这一层最要紧：2.7 的实测推翻了 D0-6（写入侧阻止）的前提 ——
opencode 的 deny 规则能否真的拦住写入尚未被证明，而 `bash` 工具本就无法
按路径约束。因此**准出时的校验是唯一能兑现「篡改可检出」的机制**。

纪律（A0 的 9.1）：本文件会绕过受控入口直接改 `.state`。
这是**必要的** —— 不绕过就造不出被篡改的样本，验收 13/16 无从验证。
但绕过只用于制造失败；凡断言「通过」的用例一律走 update_state。
"""

import json

import pytest

from sw_lib.core import state as state_mod
from sw_lib.core import evidence as ev


@pytest.fixture(autouse=True)
def _isolate_key(tmp_path, monkeypatch):
    """把密钥文件重定向到 tmp，并清掉 lru_cache。

    autouse 是**必需**的，两个原因，都是实测踩出来的：

    1. `get_key()` 带 `lru_cache` —— 上一条用例填进去的密钥会跨用例残留，
       导致本文件单跑全绿、全量跑却红（顺序依赖的假绿/假红）。
    2. 不用 `iso` 的用例（走 strip_secrets）会调到真实 `get_key()`，
       在真实 `config/.evidence_key` 下**生成密钥**。实测发生过：
       跑一次测试就把生产密钥文件创建了出来。
    """
    monkeypatch.setattr(ev, "KEY_PATH", tmp_path / ".evidence_key")
    ev.get_key.cache_clear()
    yield
    ev.get_key.cache_clear()


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """隔离 TASKS —— 密钥隔离已由 _isolate_key 承担。"""
    monkeypatch.setattr(state_mod, "TASKS", tmp_path)
    name = "t-ev"
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return name


def _tamper(name: str, mutate):
    """绕过受控入口直接改 .state —— 模拟 agent 用 bash/sed 改文件。

    这正是 A0 要检出的行为，因此必须能在测试里造出来。
    """
    sf = state_mod.state_path(name)
    raw = json.loads(sf.read_text(encoding="utf-8"))
    mutate(raw)
    sf.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 密钥管理 ──

def test_key_is_created_with_owner_only_permission(iso):
    """密钥文件必须是 0600 —— 同机其他用户不得读取。"""
    ev.get_key()

    assert ev.KEY_PATH.exists()
    assert oct(ev.KEY_PATH.stat().st_mode)[-3:] == "600"


def test_key_is_stable_across_calls(iso):
    """密钥必须持久化。每次生成新密钥会让既有签名全部变成 tampered。"""
    assert ev.get_key() == ev.get_key()


def test_key_is_never_exposed_via_agent_env(monkeypatch):
    """验收 12：密钥不得出现在传给 agent 子进程的环境变量里。

    依据 2.6 实测：opencode.py 的 _load_env() 做 os.environ.copy() 并把
    整份环境交给 `opencode serve`，而 agent 的 bash 工具一条 `env` 就能读到。
    """
    from sw_lib.agents.opencode import OpenCodeAgent

    monkeypatch.setenv("HARNESS_EVIDENCE_KEY", "leaked-secret-value")
    env = OpenCodeAgent._load_env(object.__new__(OpenCodeAgent))

    assert "HARNESS_EVIDENCE_KEY" not in env, (
        "密钥出现在 agent 的环境变量里 —— agent 可 `env` 读取后伪造签名"
    )
    assert "leaked-secret-value" not in json.dumps(dict(env)), "密钥值仍在环境中"


def test_key_is_never_exposed_via_pty_agent_env(monkeypatch):
    """验收 12 的第二条出口：PtyAgent 也把整份环境交给子进程。

    只堵 opencode 一处是假绿 —— pty.py 的 _build_env() 同样做
    os.environ.copy() 后交给 `gemini` 子进程，gemini 也有 shell 工具。
    """
    from sw_lib.agents.pty import PtyAgent

    monkeypatch.setenv("HARNESS_EVIDENCE_KEY", "leaked-secret-value")
    agent = object.__new__(PtyAgent)
    monkeypatch.setattr(PtyAgent, "_load_credentials", lambda self: {})
    monkeypatch.setattr(PtyAgent, "_add_log", lambda self, *a, **k: None)
    env = agent._build_env()

    assert "HARNESS_EVIDENCE_KEY" not in env, (
        "密钥出现在 PtyAgent 的环境变量里"
    )
    assert "leaked-secret-value" not in json.dumps(dict(env)), "密钥值仍在环境中"


def test_strip_secrets_removes_key_by_value_not_only_by_name():
    """按名字剥离不够 —— 密钥值若被复制到别名变量下仍会泄露。

    真实场景：credentials.yaml 里写了个转发变量，或用户 export 了副本。
    """
    key = ev.get_key().decode("ascii")
    env = {"SOME_ALIAS": key, "PATH": "/usr/bin", "UNRELATED": "safe"}

    cleaned = ev.strip_secrets(env)

    assert key not in json.dumps(cleaned), "密钥值经别名变量泄露"
    assert cleaned["UNRELATED"] == "safe", "误删了无关变量"
    assert cleaned["PATH"] == "/usr/bin"


# ── 三态校验 ──

def test_evidence_written_via_controlled_entry_is_valid(iso):
    """验收 6：走 update_state 写入证据后校验为 valid。

    这条**必须**走受控入口 —— 若在这里绕过去手工造签名，
    验证的就只是我自己拼的字符串，而不是真实写入路径（A0 的 9.1）。
    """
    state_mod.update_state(iso, lambda st: {
        **st, "id": iso, "stage": "03-coding",
        "red_witness": {"failed_nodes": ["t.py::a"], "phase": "03b"},
    })

    assert ev.verify_evidence(state_mod.read_state(iso)).status == "valid"


def test_tampered_evidence_is_detected(iso):
    """验收 13：绕过受控入口修改 red_witness 后必须报 tampered。

    这是整个第二层唯一能证明「真的防住了」的判据。
    """
    state_mod.update_state(iso, lambda st: {
        **st, "id": iso, "red_witness": {"failed_nodes": ["t.py::a"]},
    })

    _tamper(iso, lambda raw: raw["red_witness"].__setitem__(
        "failed_nodes", []))          # 伪装成「没有失败的测试」

    res = ev.verify_evidence(state_mod.read_state(iso))
    assert res.status == "tampered", f"篡改未被检出，实际 {res.status}"
    assert not res.ok


def test_evidence_written_outside_controlled_entry_is_unsigned(iso):
    """验收 7：证据存在但无签名 → unsigned，且**不算通过**。

    对应 agent 直接用 bash 往 .state 里塞一份 red_witness 的情形。
    """
    state_mod.update_state(iso, lambda st: {**st, "id": iso,
                                            "stage": "03-coding"})
    _tamper(iso, lambda raw: raw.__setitem__(
        "red_witness", {"failed_nodes": ["fake::node"]}))

    res = ev.verify_evidence(state_mod.read_state(iso))
    assert res.status == "unsigned"
    assert not res.ok, "unsigned 被当成通过 —— 等于给伪造留了后门"


def test_deleting_evidence_is_detected(iso):
    """删掉证据也是篡改 —— 不能因为「没有证据了」就变成无事可查。

    否则最省事的攻击就是把 red_witness 整棵删掉。
    """
    state_mod.update_state(iso, lambda st: {
        **st, "id": iso, "red_witness": {"failed_nodes": ["t.py::a"]},
    })

    _tamper(iso, lambda raw: raw.pop("red_witness"))

    assert ev.verify_evidence(state_mod.read_state(iso)).status == "tampered"


def test_state_without_evidence_is_absent_not_valid(iso):
    """01/02 阶段还没有任何证据 —— 应为 absent，既非 valid 也非 unsigned。

    absent 不等于通过：要求证据的关卡（A2 准出、A6、A9、A10）看到 absent
    必须拒绝，这与 A6 的 3.3「禁止找不到就跳过」同构。
    """
    state_mod.update_state(iso, lambda st: {**st, "id": iso,
                                            "stage": "01-brainstorming"})

    res = ev.verify_evidence(state_mod.read_state(iso))
    assert res.status == "absent"
    assert not res.ok, "absent 被当成通过 —— 无证据不等于已验证"


def test_non_evidence_fields_are_not_covered(iso):
    """运行状态（stage 等）不在签名范围内，改动它不应误报 tampered。

    否则每次正常推进阶段都会触发假警报，机制会被当成噪音关掉。
    """
    state_mod.update_state(iso, lambda st: {
        **st, "id": iso, "stage": "03-coding",
        "red_witness": {"failed_nodes": ["t.py::a"]},
    })

    _tamper(iso, lambda raw: raw.__setitem__("stage_status", "running"))

    assert ev.verify_evidence(state_mod.read_state(iso)).status == "valid"


def test_signature_is_key_dependent(iso, tmp_path, monkeypatch):
    """换密钥后旧签名必须失效 —— 证明签名真的依赖密钥而非只是校验和。"""
    state_mod.update_state(iso, lambda st: {
        **st, "id": iso, "red_witness": {"failed_nodes": ["t.py::a"]},
    })
    assert ev.verify_evidence(state_mod.read_state(iso)).status == "valid"

    ev.get_key.cache_clear()
    monkeypatch.setattr(ev, "KEY_PATH", tmp_path / ".other_key")

    assert ev.verify_evidence(state_mod.read_state(iso)).status == "tampered"


def test_signature_survives_key_reordering(iso):
    """签名基于规范化 JSON，字段顺序变化不应影响结果。

    否则任何一次 dict 顺序变动都会造成假 tampered。
    """
    state_mod.update_state(iso, lambda st: {
        **st, "id": iso,
        "red_witness": {"phase": "03b", "failed_nodes": ["t.py::a"]},
    })

    def reorder(raw):
        rw = raw.pop("red_witness")
        raw["red_witness"] = {"failed_nodes": rw["failed_nodes"],
                              "phase": rw["phase"]}

    _tamper(iso, reorder)

    assert ev.verify_evidence(state_mod.read_state(iso)).status == "valid"
