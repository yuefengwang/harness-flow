# 05-Archive Hooks

## hook-05-01: Project Memory Update
- **When**: during
- **Rule**: write new patterns, pitfalls, decisions to GEMINI.md / MEMORY.md
- **Check**: memory files updated with actionable content

## hook-05-02: Session Retro
- **When**: during
- **Rule**: reflect on tooling, speed, friction; record 1-2 improvements
- **Check**: Retro section filled in template

## hook-05-03: Doc Consistency
- **When**: during
- **Rule**: workflow/README.md and repo/README.md match reality
- **Check**: no stale files, no temp junk

## hook-05-04: Clean Exit
- **When**: post
- **Rule**: destroy build artifacts, temp files, worktrees if merged
- **Check**: `git worktree list` clean; archive/history/ contains session record
