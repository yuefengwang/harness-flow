from notes.models import Note


def test_note_creation_with_minimal_fields():
    note = Note(title="Test Meeting", content="Discuss project plan")
    assert note.title == "Test Meeting"
    assert note.content == "Discuss project plan"
    assert note.tags == []
    assert len(note.id) == 8  # uuid4 hex[:8]
    assert note.created_at is not None
    assert note.updated_at is not None


def test_note_with_tags():
    note = Note(title="Work Note", content="Stuff", tags=["work", "urgent"])
    assert "work" in note.tags
    assert "urgent" in note.tags


def test_note_id_uniqueness():
    note1 = Note(title="A", content="1")
    note2 = Note(title="B", content="2")
    assert note1.id != note2.id
