import argparse
import sys
from typing import Optional

from .models import Note
from .storage import NotesStore


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notes",
        description="CLI note manager — store, search, and organize notes from the terminal",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a new note")
    add_p.add_argument("title", help="Note title")
    add_p.add_argument("content", help="Note body text")
    add_p.add_argument("-t", "--tags", nargs="*", default=[], help="Space-separated tags")

    sub.add_parser("list", help="List all notes")

    del_p = sub.add_parser("delete", help="Delete a note by ID")
    del_p.add_argument("id", help="Note ID to delete (8-char hex)")

    search_p = sub.add_parser("search", help="Search notes by text and/or tag")
    search_p.add_argument("query", nargs="?", default="", help="Search text (optional — omit to match all)")
    search_p.add_argument("-t", "--tag", default=None, help="Filter by tag")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    store = NotesStore()

    if args.command == "add":
        note = Note(title=args.title, content=args.content, tags=args.tags)
        store.add(note)
        print(f"Note added: {note.id}")
        print(f"  {note.title}")
        return 0

    elif args.command == "list":
        notes = store.list_all()
        if not notes:
            print("No notes found.")
            return 0
        for note in notes:
            tags = f" [{', '.join(note.tags)}]" if note.tags else ""
            preview = note.content[:60] + "..." if len(note.content) > 60 else note.content
            created = note.created_at.strftime("%Y-%m-%d %H:%M")
            print(f"{note.id}  {note.title}{tags}")
            print(f"      {created}  {preview}")
            print()
        return 0

    elif args.command == "delete":
        if store.delete(args.id):
            print(f"Note {args.id} deleted.")
            return 0
        else:
            print(f"Note {args.id} not found.", file=sys.stderr)
            return 1

    elif args.command == "search":
        notes = store.search(args.query, tag=args.tag)
        if not notes:
            print("No matching notes found.")
            return 0
        for note in notes:
            tags = f" [{', '.join(note.tags)}]" if note.tags else ""
            preview = note.content[:60] + "..." if len(note.content) > 60 else note.content
            created = note.created_at.strftime("%Y-%m-%d %H:%M")
            print(f"{note.id}  {note.title}{tags}")
            print(f"      {created}  {preview}")
            print()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
