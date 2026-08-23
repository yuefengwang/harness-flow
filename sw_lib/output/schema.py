"""Unified data types for agent output and stage results."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


@dataclass
class AgentResult:
    """Unified return type for all agent invocations.

    Replaces the ad-hoc (source, msg) log-line-based output collection
    with a typed result object.
    """

    content: str
    parsed: Optional[BaseModel] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    token_usage: Optional[Dict[str, int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.content.strip()


class StageOutputSchema(BaseModel):
    """Base class for all stage-specific output schemas."""
    stage: str
    task_name: str = ""
    raw_content: str = ""
