"""`--mock` 必须能被子进程看到，否则证据签名域会分裂。

**这个 bug 是 e2e 抓出来的，全部单元测试当时都是绿的。**

现场（`tests/e2e-flow/driver.py` 跑到 03 阶段卡死）：

    [Red Witness] 见证 03 阶段红绿流程...
    ❌ 证据签名校验未通过（tampered）—— red_witness 可能被绕过受控入口修改

成因链：

1. `sw init --mock` 只改**主进程内存**里的 `_manager.config.mock_agent.enabled`
   （`commands.py:121`），不写 `config.yaml`；
2. `evidence.get_key()` 在 mock 下返回固定的 `_MOCK_KEY`，于是主进程用
   `_MOCK_KEY` 给 `.state` 签名；
3. 钩子是**独立子进程**，重新加载 `config.yaml` —— 那里 `enabled: false`，
   于是它用真实的 `config/.evidence_key` 校验；
4. 两个密钥不同 → `tampered` → 03 阶段永久无法准出。

单元测试测不到它，因为进程内跑测试时签名与校验用的是同一份内存配置 ——
**签名域分裂只在跨进程时出现**。这也是 e2e 不可替代的地方。

处置：`--mock` 同时写一个环境变量。子进程继承环境，于是签名域自洽。
用环境变量而不是回写 `config.yaml`：`--mock` 是一次性的命令行开关，
把它固化进用户的配置文件会让下一次不带标志的运行也悄悄走 MockAgent。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _probe_in_subprocess(env):
    """在干净的子进程里问一句「你觉得现在是 mock 模式吗」。

    必须起子进程：本模块要证明的正是「跨进程时判定一致」，
    在测试进程里 monkeypatch 恰好会掩盖这个 bug。
    """
    code = (
        "from sw_lib.core.config import is_mock_agent\n"
        "from sw_lib.core.evidence import get_key\n"
        "import json\n"
        "print(json.dumps({'mock': is_mock_agent(), "
        "'key': get_key().decode('ascii', 'ignore')[:12]}))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, **env})
    assert r.returncode == 0, f"探针子进程失败:\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_env_override_makes_subprocess_see_mock_mode():
    """设了环境变量后，子进程必须认为自己在 mock 模式。

    这是 e2e 那条卡死链的第 3 环 —— 钩子子进程看不到 mock。
    """
    out = _probe_in_subprocess({"SW_MOCK_AGENT": "1"})
    assert out["mock"] is True, \
        "子进程没看到 mock 模式 —— 钩子会用真实密钥校验 mock 签名（tampered）"


def test_signing_key_agrees_across_processes_in_mock_mode():
    """签名域必须自洽：两个子进程在 mock 下取到同一把密钥。

    这才是 `tampered` 的直接判据 —— 密钥不同就必然校验失败，
    与 `red_witness` 的逻辑对不对无关。
    """
    a = _probe_in_subprocess({"SW_MOCK_AGENT": "1"})
    b = _probe_in_subprocess({"SW_MOCK_AGENT": "1"})
    assert a["key"] == b["key"], "mock 模式下两个进程取到了不同的密钥"

    real = _probe_in_subprocess({"SW_MOCK_AGENT": "0"})
    assert real["key"] != a["key"], \
        "真实模式与 mock 模式用了同一把密钥 —— mock 夹具会污染真实签名"


def test_env_override_off_falls_back_to_config():
    """`SW_MOCK_AGENT=0` 必须回到配置文件的判定。

    否则一旦某处顺手设了这个变量，用户的真实运行会被静默切成 MockAgent。
    """
    out = _probe_in_subprocess({"SW_MOCK_AGENT": "0"})
    assert out["mock"] is False, \
        "显式关闭却仍是 mock 模式 —— 真实运行会被静默替换成 MockAgent"


def test_unset_env_does_not_change_existing_behaviour():
    """不设这个变量时，行为必须与引入它之前完全一致（读 config.yaml）。

    仓库里 `mock_agent.enabled` 是 false，所以这里应当是 False。
    锁住它是为了防止「新增开关顺手改了默认值」。
    """
    env = {k: v for k, v in os.environ.items() if k != "SW_MOCK_AGENT"}
    r = subprocess.run(
        [sys.executable, "-c",
         "from sw_lib.core.config import is_mock_agent; print(is_mock_agent())"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "False", \
        f"未设环境变量时的默认判定被改动了: {r.stdout!r}"


def test_cli_mock_flag_exports_env_for_children():
    """`sw init --mock` 的处理必须把开关导出到环境，供子进程继承。

    断言的是「命令行标志 → 环境变量」这一步真的发生了，
    而不只是改了内存配置 —— 后者钩子读不到，正是 e2e 卡死的第 1 环。
    """
    code = (
        "import argparse\n"
        "from sw_lib.cli import commands\n"
        "import os\n"
        "args = argparse.Namespace(mock=True, no_mock=False)\n"
        "commands.apply_mock_flags(args)\n"
        "print(os.environ.get('SW_MOCK_AGENT'))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert r.stdout.strip() == "1", \
        f"--mock 没有把开关导出到环境，子进程仍会读 config.yaml: {r.stdout!r}"
