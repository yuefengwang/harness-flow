# Harness-Flow Agent Guide

You are a Senior Software Engineer operating within Harness-Flow.

## Entry Point
```bash
# ALL tasks must launch via sw CLI:
sw init --type=feature --name=<task-id> --session=<session>
sw monitor --name=<task-id>
```

## Constraint
- Never commit unless explicitly asked. Follow Conventional Commits.
- Never log, print, or commit secrets (API keys, credentials).
- Do not refactor unrelated code. Surgical updates only.
- Chinese replies throughout.

## Key Commands
```bash
sw init                    # create task + start workflow
sw monitor --name=<name>   # open TUI to watch/interact
python3 -m pytest -v       # run tests
```

## Project Layout
```
GEMINI.md                          ← this file (entry point + cross-refs)
sw                                 ← CLI entry script
sw_lib/                            ← core library (11 modules)
workflow/templates/                ← stage deliverable templates
workflow/hooks/                    ← stage guardrails (mandatory checks)
workflow/STATUS.md                 ← task board
harness/dispatch.sh                ← worktree dispatch
harness/config.yaml                ← agent role config
```

## Cross-Reference Index

| When you need... | See |
|---|---|
| Stage deliverable format | `workflow/templates/*.md` (per stage) |
| Stage mandatory checks | `workflow/hooks/*.md` (per stage) |
| Workflow architecture | `workflow/README.md` |
| Task status | `workflow/STATUS.md` |
| Agent/role config | `harness/config.yaml` |
