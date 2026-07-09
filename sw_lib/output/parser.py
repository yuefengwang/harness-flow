"""StageOutputParser — parses agent text into typed stage output schemas."""

import json
import re
from typing import Optional, Type

from pydantic import ValidationError

from .schema import StageOutputSchema


class StageOutputParser:
    """Parses raw agent output text into a validated Pydantic model.

    Handles three formats:
    1. Raw JSON — agent outputs a JSON object directly
    2. JSON code block — agent outputs ```json ... ```
    3. Markdown-wrapped — key-value pairs in markdown sections

    Designed to work without langchain-core dependency. The parser
    accepts any Pydantic StageOutputSchema subclass and validates
    the parsed output.
    """

    def __init__(self, schema_cls: Type[StageOutputSchema]):
        self.schema_cls = schema_cls

    def parse(self, text: str) -> StageOutputSchema:
        """Parse text into the schema. Raises ValueError on failure."""
        result = self._try_parse(text)
        if result is None:
            raise ValueError(
                f"Failed to parse output as {self.schema_cls.__name__}. "
                f"Raw output (first 200 chars): {text[:200]}"
            )
        return result

    def try_parse(self, text: str) -> Optional[StageOutputSchema]:
        """Parse text, return None on failure (no exception)."""
        return self._try_parse(text)

    def _try_parse(self, text: str) -> Optional[StageOutputSchema]:
        for strategy in [self._parse_json, self._parse_json_block,
                          self._parse_markdown_fields]:
            try:
                result = strategy(text)
                if result is not None:
                    return result
            except Exception:
                continue
        # All strategies failed — create schema instance with raw text only
        try:
            return self.schema_cls(raw_output=text[:5000])
        except Exception:
            return None

    def _parse_json(self, text: str) -> Optional[StageOutputSchema]:
        """Try parsing the entire text as raw JSON."""
        text = text.strip()
        if not text.startswith("{"):
            return None
        try:
            data = json.loads(text)
            return self.schema_cls(**data)
        except (json.JSONDecodeError, ValidationError):
            return None

    def _parse_json_block(self, text: str) -> Optional[StageOutputSchema]:
        """Try extracting from ```json ... ``` block."""
        m = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(1).strip())
            return self.schema_cls(**data)
        except (json.JSONDecodeError, ValidationError):
            return None

    def _parse_markdown_fields(self, text: str) -> Optional[StageOutputSchema]:
        """Extract key-value pairs from markdown sections."""
        fields = {}
        for line in text.splitlines():
            stripped = line.strip()
            m = re.match(r'(?:-\s+)?\*{0,2}(\w[\w\s]*)\*{0,2}\s*[:：]\s*(.*)', stripped)
            if not m:
                continue
            key = m.group(1).strip().lower().replace(" ", "_")
            val = m.group(2).strip()
            val = re.sub(r'`', '', val)
            val = re.sub(r'\(.*\)', '', val).strip()
            fields[key] = self._coerce_value(key, val)

        if not fields:
            return None
        try:
            return self.schema_cls(**fields)
        except ValidationError:
            return None

    def _coerce_value(self, key: str, val: str):
        """Coerce string values to int/bool based on schema field type."""
        try:
            field = self.schema_cls.model_fields.get(key)
        except Exception:
            return val
        if field is None:
            return val
        anno = field.annotation
        if anno is bool:
            return val.lower() in ("true", "yes", "1", "✅", "pass", "passed")
        if anno is int:
            try:
                return int(re.sub(r'[^\d]', '', val))
            except ValueError:
                return 0
        return val
