"""mock 签名域必须由 `.state` **自描述**，不能只靠环境变量维系。

**本 bug 由 e2e 抓出（第二个，同一条链的下游）。**

上一处修复让 `--mock` 通过 `SW_MOCK_AGENT` 传给子进程，五个阶段因此走通了。
但 `tests/e2e-flow/verify.py` 复查钩子时是**另起的进程、不带那个环境变量**，
于是同一份 `.state` 又被判成 `tampered`：

    [✗] Hard check (03-coding) — exit=1
    ❌ 证据签名校验未通过（tampered）

这不是 verify 的疏漏，而是设计问题：**签名用哪把密钥，是那份数据的属性，
不是读它的进程的属性。** 靠环境变量维系意味着任何一次环境丢失都会把
mock 产出的 `.state` 变成永久不可校验 —— 而它明明是合法写入的。

处置：`verify_evidence` 从 `.state` 自身判断签名域（`red_witness.mock`），
环境变量只决定**新写入**用哪把密钥。这样校验结果对读取环境不敏感。

注意方向性：mock 密钥是公开的固定串，所以「mock 签名」的可信度本就为零 ——
它只用来区分「合法的 mock 产出」与「被改坏的文件」。真实签名的可信度
不受影响，因为真实模式绝不接受 mock 密钥（下面单独有一条锁住它）。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _write_state_in_subprocess(task_dir, mock: bool):
    """在带/不带 mock 环境的子进程里写一份带 red_witness 的 .state。

    必须起子进程：本模块要证明的是「校验对读取环境不敏感」，
    在同一进程里 monkeypatch 会掩盖问题。
    """
    code = f'''
import json, os, pathlib
from sw_lib.core import evidence as ev

state = {{
    "id": "probe", "stage": "03-coding",
    "red_witness": {{"phase": "03b", "mock": {mock!r}, "failed_nodes": []}},
}}
ev.attach_signature(state)
pathlib.Path({str(task_dir)!r}).write_text(
    json.dumps(state, ensure_ascii=False), encoding="utf-8")
print(state[ev.SIG_FIELD][:12])
'''
    env = {**os.environ, "SW_MOCK_AGENT": "1" if mock else "0"}
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, f"写入探针失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout.strip()


def _verify_in_subprocess(state_path, env_mock):
    """在指定环境下校验，返回四态之一。"""
    code = f'''
import json, pathlib
from sw_lib.core.evidence import verify_evidence
state = json.loads(pathlib.Path({str(state_path)!r}).read_text(encoding="utf-8"))
print(verify_evidence(state).status)
'''
    env = dict(os.environ)
    if env_mock is None:
        env.pop("SW_MOCK_AGENT", None)
    else:
        env["SW_MOCK_AGENT"] = env_mock
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, f"校验探针失败:\n{r.stdout}\n{r.stderr}"
    return r.stdout.strip().splitlines()[-1]


def test_mock_signed_state_verifies_without_the_env_var(tmp_path):
    """mock 写入的 `.state`，在**不带**环境变量的进程里也必须校验通过。

    这正是 verify.py 的处境 —— 它另起进程复查钩子。
    """
    sp = tmp_path / ".state"
    _write_state_in_subprocess(sp, mock=True)

    assert _verify_in_subprocess(sp, env_mock=None) == "valid", \
        "mock 产出的合法 .state 在别的进程里被判成了 tampered"


def test_mock_signed_state_verifies_with_env_explicitly_off(tmp_path):
    """连 `SW_MOCK_AGENT=0` 也应校验通过 —— 签名域写在数据里。"""
    sp = tmp_path / ".state"
    _write_state_in_subprocess(sp, mock=True)

    assert _verify_in_subprocess(sp, env_mock="0") == "valid", \
        "签名域仍依赖读取进程的环境，而不是 .state 自身"


def test_real_signed_state_still_verifies(tmp_path):
    """对照：真实模式写入的 `.state` 不受这条改动影响。"""
    sp = tmp_path / ".state"
    _write_state_in_subprocess(sp, mock=False)

    assert _verify_in_subprocess(sp, env_mock="0") == "valid"


def test_mock_key_cannot_forge_a_non_mock_record(tmp_path):
    """**方向性**：不带 `mock: true` 的记录不接受 mock 密钥签名。

    这条是上面几条的安全边界。mock 密钥是源码里的公开常量，
    若真实记录也接受它，任何人都能伪造一份「已见证转绿」的证据。
    构造方式：用 mock 密钥签名，然后把 `mock` 标记抹掉。
    """
    sp = tmp_path / ".state"
    _write_state_in_subprocess(sp, mock=True)

    state = json.loads(sp.read_text(encoding="utf-8"))
    state["red_witness"]["mock"] = False        # 摘掉标记，签名保持不动
    sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    assert _verify_in_subprocess(sp, env_mock="0") == "tampered", \
        "用公开的 mock 密钥伪造出了一份「真实」证据 —— 签名机制被绕过"


def test_tampered_mock_record_is_still_detected(tmp_path):
    """改坏 mock 记录的内容仍必须检出 —— 放宽签名域不等于放弃校验。"""
    sp = tmp_path / ".state"
    _write_state_in_subprocess(sp, mock=True)

    state = json.loads(sp.read_text(encoding="utf-8"))
    state["red_witness"]["failed_nodes"] = ["伪造的判据节点"]
    sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    assert _verify_in_subprocess(sp, env_mock=None) == "tampered", \
        "mock 记录被改动却没检出 —— 签名域放宽成了不校验"
