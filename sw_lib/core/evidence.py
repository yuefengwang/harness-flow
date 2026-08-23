"""sw_lib.core.evidence — 证据字段的 HMAC 签名与三态校验。

设计依据：docs/design/A0-state-integrity.md 的 D0-4 / D0-5。

**这一层为什么是主防线**：2.7 的实测推翻了写入侧阻止的前提 ——
opencode 的 deny 规则能否真的拦住写入尚未证明，且 `bash` 工具本就无法按
路径约束。因此「篡改可检出」只能由准出时的校验兑现，而不是由写入拦截兑现。

**它挡不住什么（必须说清楚）**：agent 拥有 shell，密钥文件在 ROOT 内，
`bash` 可达。因此 HMAC 防的是**误改与顺手改**，不防**定向攻击**。
定向攻击只能靠进程隔离（容器化）。把本模块说成「不可伪造」是过度承诺。
"""

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Tuple

from .config import CONFIG_DIR

KEY_PATH = CONFIG_DIR / ".evidence_key"

# 签名字段本身
SIG_FIELD = "_evidence_sig"

# 受签名保护的字段（D0-4）。这些是「判据」—— 机器据此放行或拒绝。
# 其余字段（stage / stage_idx / stage_status 等）是运行状态，正常流程会频繁
# 改动，纳入签名会导致每次推进阶段都误报，机制会被当噪音关掉。
EVIDENCE_FIELDS: Tuple[str, ...] = (
    "red_witness",     # A2：测试冻结与 Red 见证
    "facts",           # A3：事实包
    "review",          # 含 baseline_sha
    "objective",       # A6：客观轨结论
    "route",           # A9：仲裁决策
    "archive_report",  # A10：归档达成度
)

# mock 模式用固定密钥：CI 每次生成新密钥会让夹具里的既有签名全部失效。
_MOCK_KEY = b"harness-mock-evidence-key-do-not-use-in-production"


@dataclass
class VerifyResult:
    """三态校验结果。

    四种 status，**只有 valid 算通过**：

    - `valid`    —— 有签名且匹配。
    - `tampered` —— 有签名但不匹配（含证据被删）。
    - `unsigned` —— 有证据但无签名（agent 绕过受控入口塞进来的）。
    - `absent`   —— 尚无任何证据（01/02 阶段的正常状态）。

    `absent` 不等于通过：要求证据的关卡看到 absent 必须拒绝。
    这与 A6 的 3.3「禁止找不到就跳过」同构 ——
    「没查到」和「查过没问题」是两件事。
    """
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "valid"


@lru_cache(maxsize=1)
def get_key() -> bytes:
    """读取或首次生成签名密钥。

    密钥落 `config/.evidence_key`，权限 0600。

    **刻意不支持从环境变量读取。** `opencode.py` 的 `_load_env()` 会
    `os.environ.copy()` 并把整份环境交给 `opencode serve` 子进程，
    agent 一条 `env` 就能读到密钥并伪造任意签名。
    """
    from .config import is_mock_agent
    if is_mock_agent():
        return _MOCK_KEY

    if KEY_PATH.exists():
        data = KEY_PATH.read_bytes().strip()
        if data:
            return data

    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode("ascii")
    # 先以 0600 创建再写入，避免默认权限下存在可被读取的时间窗口。
    fd = os.open(str(KEY_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    os.chmod(KEY_PATH, 0o600)
    return key


# 明令不得下传给 agent 子进程的变量名。
SECRET_ENV_NAMES: Tuple[str, ...] = ("HARNESS_EVIDENCE_KEY",)


def strip_secrets(env: Dict[str, str]) -> Dict[str, str]:
    """剥掉签名密钥，返回可以安全交给 agent 子进程的环境副本。

    两道剥离，因为只按名字删是假绿：

    1. 按名字删 `SECRET_ENV_NAMES`；
    2. **按值扫描** —— 密钥值可能被复制到任意别名下（credentials.yaml 里
       写了转发变量，或用户 export 了副本），此时按名字删不掉。

    读密钥失败时只做第 1 步，不因为拿不到密钥而放弃剥离。

    注意这挡的是「agent 顺手 `env` 就拿到密钥」，不挡定向攻击 ——
    密钥文件仍在 ROOT 内且 `bash` 可达（见模块 docstring）。
    """
    cleaned = {k: v for k, v in env.items() if k not in SECRET_ENV_NAMES}

    try:
        key = get_key().decode("ascii", "ignore")
    except Exception:
        return cleaned

    # 长度下限：避免密钥异常为空或极短时把无关变量一并删光。
    if len(key) < 16:
        return cleaned

    return {k: v for k, v in cleaned.items() if key not in str(v)}


def _canonical(state: Dict[str, Any]) -> bytes:
    """把证据子集规范化为确定的字节串。

    `sort_keys=True` 让字段顺序无关 —— 否则任何一次 dict 顺序变动
    都会造成假 tampered。缺席的字段不写入载荷，因此**删除证据会改变载荷**，
    从而被检出（否则最省事的攻击就是把 red_witness 整棵删掉）。
    """
    subset = {k: state[k] for k in EVIDENCE_FIELDS if k in state}
    return json.dumps(subset, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def has_evidence(state: Dict[str, Any]) -> bool:
    return any(k in state for k in EVIDENCE_FIELDS)


def sign_evidence(state: Dict[str, Any]) -> str:
    """对状态中的证据字段计算 HMAC-SHA256（用当前进程的密钥）。"""
    return hmac.new(get_key(), _canonical(state), hashlib.sha256).hexdigest()


def _is_mock_record(state: Dict[str, Any]) -> bool:
    """这份状态是否由 mock 模式写出 —— 判据取自 `.state` **自身**。

    存在的理由（由 e2e 实测抓出）：签名用哪把密钥是**那份数据的属性**，
    不是读它的进程的属性。此前 mock 签名域只靠 `SW_MOCK_AGENT` 环境变量
    维系，于是 `tests/e2e-flow/verify.py` 另起进程复查钩子时，
    同一份合法的 `.state` 被判成 `tampered` —— 而它是正常写入的。

    只认 `red_witness.mock is True` 这一处显式标记。不做「猜」：
    字段缺失、类型不对、写着别的值，一律当作真实模式，
    因为放宽的方向必须是「更严」而不是「更松」。
    """
    record = state.get("red_witness")
    if not isinstance(record, dict):
        return False
    return record.get("mock") is True


def attach_signature(state: Dict[str, Any]) -> Dict[str, Any]:
    """就地更新签名字段；无证据时移除签名。由 write_state 调用。"""
    if has_evidence(state):
        state[SIG_FIELD] = sign_evidence(state)
    else:
        state.pop(SIG_FIELD, None)
    return state


def verify_evidence(state: Dict[str, Any]) -> VerifyResult:
    """校验证据签名。返回四态之一，只有 valid 算通过。"""
    present = has_evidence(state)
    sig = state.get(SIG_FIELD)

    if not present:
        if sig:
            # 有签名却没有证据 → 证据被删掉了
            return VerifyResult("tampered", "存在签名但证据字段已不存在")
        return VerifyResult("absent", "尚无任何证据字段")

    if not sig:
        return VerifyResult(
            "unsigned",
            "存在证据但无签名 —— 可能是绕过受控入口写入的")

    # 签名域由 `.state` 自描述：mock 产出的记录用固定的 _MOCK_KEY 校验，
    # 与当前进程是否处在 mock 模式无关（见 `_is_mock_record` 的 docstring）。
    #
    # 安全性方向：这**不会**削弱真实签名。mock 密钥是源码里的公开常量，
    # 因此只有**明确标了 `mock: true`** 的记录才允许用它校验；
    # 抹掉标记想蒙过去，签名立刻对不上（`_canonical` 覆盖整棵 red_witness 子树）。
    if _is_mock_record(state):
        expected = hmac.new(_MOCK_KEY, _canonical(state),
                            hashlib.sha256).hexdigest()
    else:
        expected = sign_evidence(state)

    # compare_digest：避免以字符串比较泄露时序信息
    if hmac.compare_digest(str(sig), expected):
        return VerifyResult("valid")
    return VerifyResult("tampered", "签名与证据内容不匹配")
