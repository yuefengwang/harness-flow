"""仓库实际配置的异构性：三个角色必须来自三个不同模型族。

这不是重复 test_provider_model_family.py（那里测解析规则），
这里测**仓库当前 config.yaml 的实际取值** —— 判据是「异构真的开起来了」，
而不是「异构能被表达」。A4 交付后异构性长期停在 degraded，就是因为没有
任何测试盯着实际配置。

另附一份实测可用模型清单。2026-08-24 用 OpenCodeAgent 逐个探测 16 个候选
free 模型，**只有 3 个真的返回文本**，其余全部 HTTP 500（UnknownError）。
模型列表里「存在」不等于账号下「可用」—— 配一个 500 的模型会让审查者
静默失败，而失败若被记成「无发现」，故障就变成了放行理由。
"""

import pytest

from sw_lib.core import config as C

# 2026-08-24 实测可调通（返回 PONG）。换模型前请重新探测。
VERIFIED_CALLABLE = {
    "opencode/mimo-v2.5-free",   # 4.9s
    "opencode/big-pickle",       # 10.0s
    "opencode/hy3-free",         # 6.1s
}

# 同日实测 HTTP 500 的，留作反向清单避免有人再配回去。
VERIFIED_BROKEN = {
    "opencode/mimo-v2-pro-free", "opencode/glm-4.7-free", "opencode/glm-5-free",
    "opencode/kimi-k2.5-free", "opencode/minimax-m2.5-free",
    "opencode/minimax-m3-free", "opencode/deepseek-v4-flash-free",
    "opencode/qwen3.6-plus-free", "opencode/grok-code",
    "opencode/nemotron-3-super-free", "opencode/ling-3.0-flash-free",
    "opencode/longcat-2.0-free", "opencode/ring-2.6-1t-free",
    "opencode/trinity-large-preview-free",
}


@pytest.fixture
def live():
    C._manager.reload()
    return C._manager.config


def test_author_and_reviewers_use_distinct_model_families(live):
    """developer / adversary / design_critic 三者模型族互不相同。

    这是 C2「审查者与作者盲区不重合」在配置层的最低要求。
    """
    families = {r: live.roles[r].provider
                for r in ("developer", "adversary", "design_critic")}
    assert len(set(families.values())) == 3, \
        f"三个角色只有 {len(set(families.values()))} 个模型族：{families}"


def test_configured_models_are_verified_callable(live):
    """配置里用到的模型必须在实测可用清单内。

    实测 16 个候选只有 3 个能通 —— 从模型列表里随手挑一个大概率是 500。
    """
    used = {r.model for r in live.roles.values()}
    broken = used & VERIFIED_BROKEN
    assert not broken, f"这些模型实测 HTTP 500，不可用：{sorted(broken)}"
    unverified = used - VERIFIED_CALLABLE - VERIFIED_BROKEN
    assert not unverified, (
        f"这些模型未经实测：{sorted(unverified)} —— "
        "请用 OpenCodeAgent 探测能否返回文本后再更新 VERIFIED_CALLABLE")


def test_live_config_has_no_heterogeneity_violation(live):
    """仓库配置不得存在异构违规。"""
    codes = {i.code for i in C._heterogeneity_issues(live)}
    assert codes == set(), f"仓库配置存在异构违规：{codes}"


def test_heterogeneous_status_is_ok_not_degraded():
    """异构性必须真的兑现为 ok。

    停在 degraded 意味着 A10 的报告会一直标注「审查者同构」。
    """
    C._manager.reload()
    assert C.resolve_review_config().heterogeneous_status == "ok"


def test_require_heterogeneous_is_enabled():
    """开关必须真的打开 —— 否则配了异构模型也没有强制力。"""
    C._manager.reload()
    assert C._manager.config.review.require_heterogeneous is True, \
        "require_heterogeneous 仍为 false，异构性没有强制力"
