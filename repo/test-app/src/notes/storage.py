import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Note

DEFAULT_NOTES_DIR = Path.home() / ".notes"


def _get_notes_path() -> Path:
    env = os.environ.get("NOTES_FILE")
    if env:
        return Path(env)
    return DEFAULT_NOTES_DIR / "notes.json"


class NotesStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or _get_notes_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[Note]:
        if not self.path.exists():
            return []
        with open(self.path) as f:
            data = json.load(f)
        return [self._dict_to_note(item) for item in data]

    def _save(self, notes: list[Note]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump([self._note_to_dict(n) for n in notes], f, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def _note_to_dict(note: Note) -> dict:
        return {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "tags": note.tags,
            "created_at": note.created_at.isoformat(),
            "updated_at": note.updated_at.isoformat(),
        }

    @staticmethod
    def _dict_to_note(d: dict) -> Note:
        return Note(
            id=d["id"],
            title=d["title"],
            content=d["content"],
            tags=d.get("tags", []),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
        )

    def add(self, note: Note) -> Note:
        notes = self._load()
        notes.append(note)
        self._save(notes)
        return note

    def list_all(self) -> list[Note]:
        return self._load()

    def delete(self, note_id: str) -> bool:
        notes = self._load()
        filtered = [n for n in notes if n.id != note_id]
        if len(filtered) == len(notes):
            return False
        self._save(filtered)
        return True

    def search(self, query: str, tag: Optional[str] = None) -> list[Note]:
        notes = self._load()
        query_lower = query.lower()
        result = []
        for note in notes:
            if query and query_lower not in note.title.lower() and query_lower not in note.content.lower():
                continue
            if tag and tag not in note.tags:
                continue
            result.append(note)
        return result
