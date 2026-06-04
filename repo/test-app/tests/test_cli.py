import os
import json
from pathlib import Path
from notes.cli import main


def test_add_note(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        rc = main(["add", "Test Title", "Test content"])
        assert rc == 0
        assert notes_file.exists()
    finally:
        os.environ.pop("NOTES_FILE", None)


def test_add_note_with_tags(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        rc = main(["add", "Tagged", "With tags", "-t", "work", "urgent"])
        assert rc == 0
    finally:
        os.environ.pop("NOTES_FILE", None)


def test_list_empty(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        rc = main(["list"])
        assert rc == 0
    finally:
        os.environ.pop("NOTES_FILE", None)


def test_list_with_notes(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        main(["add", "Title1", "Content1"])
        main(["add", "Title2", "Content2"])
        rc = main(["list"])
        assert rc == 0
    finally:
        os.environ.pop("NOTES_FILE", None)


def test_delete_existing_note(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        main(["add", "Test", "Content"])
        notes = json.loads(notes_file.read_text())
        note_id = notes[0]["id"]
        rc = main(["delete", note_id])
        assert rc == 0
        assert json.loads(notes_file.read_text()) == []
    finally:
        os.environ.pop("NOTES_FILE", None)


def test_delete_nonexistent(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        rc = main(["delete", "badbadid"])
        assert rc == 1
    finally:
        os.environ.pop("NOTES_FILE", None)


def test_search(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        main(["add", "Meeting", "Discuss project"])
        main(["add", "Grocery", "Buy milk and eggs"])
        rc = main(["search", "meeting"])
        assert rc == 0
    finally:
        os.environ.pop("NOTES_FILE", None)


def test_search_with_tag(tmp_path: Path):
    notes_file = tmp_path / "notes.json"
    os.environ["NOTES_FILE"] = str(notes_file)
    try:
        main(["add", "Work", "Stuff", "-t", "work"])
        main(["add", "Home", "Stuff", "-t", "home"])
        rc = main(["search", "", "--tag", "work"])
        assert rc == 0
    finally:
        os.environ.pop("NOTES_FILE", None)
