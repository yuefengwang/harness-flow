"""sw_lib.workflow.mock_fixups — Mock 模式下的模板回填。

存在的唯一理由：让 MockAgent 能驱动全流程而不需要真实 LLM。生产路径不感知
mock —— `StageRunnable` 只调用 `apply_mock_template_fixups()`，它在非 mock
模式下是恒等函数。

**不做**的事：不碰 Gate 复选框，也不填 Route。那两样是判定依据，存在 `.state`
里（见 docs/design-json-state-source.md）；改 Markdown 不会影响任何判定，
写了只会让文件与状态不一致。自动模式下的签署与选路由 TUI 的 `_auto_sign_off`
走正规入口完成。

**做**的事：填 01 的 `- **Chosen**: ___`。选项组属于内容检查 —— 它验证用户
拍板的方案有没有被记录进产出。真实 agent 会自己写（提示词有要求），
MockAgent 不会，所以这里补上。
"""
import re

from ..core.config import is_mock_agent
from .stage_state import split_output_region

_CHOSEN_PLACEHOLDER_RE = re.compile(
    r'^(\s*[-*]\s*\*\*Chosen\*\*:\s*)_+\s*$', re.M)


def _fill_chosen_placeholders(content: str) -> str:
    """把 `- **Chosen**: ___` 填成 `- **Chosen**: A`。"""
    return _CHOSEN_PLACEHOLDER_RE.sub(r'\1A', content)


def apply_mock_template_fixups(content: str, stage: str) -> str:
    """Mock 模式下回填模板占位符；非 mock 模式原样返回。

    产出区（nonce 围栏之内）一概不动：那是 agent 的原话，改写它就等于污染
    产出记录。
    """
    if not is_mock_agent():
        return content
    if stage != "01-brainstorming":
        return content

    region = split_output_region(content)
    if region is None:
        return _fill_chosen_placeholders(content)
    before, after = region
    fenced = content[len(before):len(content) - len(after)]
    return _fill_chosen_placeholders(before) + fenced + _fill_chosen_placeholders(after)
