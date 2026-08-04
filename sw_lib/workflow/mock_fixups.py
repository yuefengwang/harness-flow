"""sw_lib.workflow.mock_fixups — Mock-mode gate fixups (extracted from StageRunnable).

These post-processing rules exist ONLY to let the MockAgent drive a full flow
without a real LLM. The production StageRunnable must not know about mock mode;
it delegates to apply_mock_gate_fixups() which is a no-op unless mock is on.

Behavior (mirrors the original StageRunnable mock branch):
  * The AI Output region (between "## 🤖 AI Output" and "## Gate") is left
    untouched; the template region (before AI Output) and the Gate region
    (after "## Gate") get their `[ ]` checkboxes auto-filled to `[x]` so the
    stage gate validates.
  * For the review stage, the placeholder Route field
    ``- **Route**: `___` `` is normalized to ``- **Route**: `05-Archive` ``.
"""
from typing import Match
import re

from ..core.config import is_mock_agent

_AI_MARKER = "\n## 🤖 AI Output\n"
_GATE_MARKER = "\n## Gate"
_ROUTE_PLACEHOLDER = "- **Route**: `___`"
_ROUTE_DEFAULT = "- **Route**: `05-Archive`"


def _fill_checkboxes(content: str) -> str:
    """Fill `[ ]` -> `[x]` only outside the AI Output region."""
    ai_pos = content.find(_AI_MARKER)
    if ai_pos >= 0:
        gate_pos = content.find(_GATE_MARKER, ai_pos + len(_AI_MARKER))
        if gate_pos >= 0:
            before = content[:ai_pos].replace("[ ]", "[x]")
            output_area = content[ai_pos:gate_pos]
            gate_area = content[gate_pos:].replace("[ ]", "[x]")
            return before + output_area + gate_area
    return content.replace("[ ]", "[x]")


def _fix_review_route(content: str) -> str:
    """Normalize the review placeholder Route field to the default Archive route."""
    if _ROUTE_PLACEHOLDER in content:
        return content.replace(_ROUTE_PLACEHOLDER, _ROUTE_DEFAULT, 1)
    return content


def apply_mock_gate_fixups(content: str, stage: str) -> str:
    """Apply mock-mode gate fixups to a stage's saved output.

    Returns the content unchanged when mock mode is disabled, so the production
    path is never mutated by test-only logic.
    """
    if not is_mock_agent():
        return content
    content = _fill_checkboxes(content)
    if stage == "04-review":
        content = _fix_review_route(content)
    return content
