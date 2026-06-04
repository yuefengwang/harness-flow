"""sw_lib.prompts — Template-driven prompt construction.

Phase 2 of HarnessFlow × LangChain refactoring.
"""

from .registry import PromptRegistry
from .builder import PromptBuilder

__all__ = ["PromptRegistry", "PromptBuilder"]
