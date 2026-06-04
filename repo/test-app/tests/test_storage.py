import json
from pathlib import Path
from notes.models import Note
from notes.storage import NotesStore


def test_add_and_list(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    note = Note(title="Test", content="Content")
    store.add(note)
    notes = store.list_all()
    assert len(notes) == 1
    assert notes[0].id == note.id
    assert notes[0].title == "Test"


def test_list_empty(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    assert store.list_all() == []


def test_delete_existing(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    note = Note(title="Test", content="Content")
    store.add(note)
    assert store.delete(note.id) is True
    assert store.list_all() == []


def test_delete_nonexistent(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    assert store.delete("badid") is False


def test_add_persists_to_disk(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    store = NotesStore(notes_file)
    note = Note(title="Persist", content="Check disk")
    store.add(note)
    raw = json.loads(notes_file.read_text())
    assert len(raw) == 1
    assert raw[0]["title"] == "Persist"


def test_search_by_title(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    store.add(Note(title="Meeting Notes", content="discuss project"))
    store.add(Note(title="Grocery List", content="milk, eggs"))
    results = store.search("meeting")
    assert len(results) == 1
    assert results[0].title == "Meeting Notes"


def test_search_by_content(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    store.add(Note(title="A", content="very important stuff"))
    store.add(Note(title="B", content="trivial stuff"))
    results = store.search("important")
    assert len(results) == 1


def test_search_by_tag(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    store.add(Note(title="Work", content="...", tags=["work"]))
    store.add(Note(title="Personal", content="...", tags=["personal"]))
    results = store.search("", tag="work")
    assert len(results) == 1
    assert results[0].title == "Work"


def test_search_empty_store(tmp_path: Path):
    store = NotesStore(tmp_path / "notes.json")
    assert store.search("anything") == []
