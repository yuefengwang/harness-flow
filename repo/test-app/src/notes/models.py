from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass
class Note:
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
