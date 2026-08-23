# Harness-Flow — LinkedIn Introduction

---

**Introducing Harness-Flow: Bringing Engineering Discipline to AI-Driven Development**

The problem with AI coding isn't that models aren't smart enough. It's that there's no process.

You describe a feature. The AI generates code. Maybe it works, maybe it doesn't. Maybe it wrote tests, maybe it skipped them. Maybe it refactored something unrelated. You review, fix, re-prompt, re-review. The loop is unstructured, unenforceable, and entirely dependent on your vigilance.

Harness-Flow solves this by imposing a **standardized, gated development pipeline** on AI-driven work — the same process discipline that professional engineering teams rely on, adapted for human-AI collaboration.

---

**Gated workflows, not one-size-fits-all**

A feature task moves through five locked stages: **Brainstorming → Planning → Coding → Review → Archive**. A bugfix follows a different rhythm. Future task types — refactors, migrations, experiments — will each get their own structured flow. The pipeline adapts to the work, not the other way around.

What's constant across all workflows: each stage produces a specific deliverable against a standardized template, and each stage boundary runs mandatory compliance checks that block progress until quality standards are met.

- **Soft checks** — Are all template fields filled? Are all checkboxes checked?
- **Hard checks** — Do builds pass? Do tests pass? Is the diff surgically clean?

`/advance` runs both. Failure means you stay put. This is CI/CD thinking applied to the AI development lifecycle itself.

---

**Multiple agents, coordinated — not just a single chat window**

Harness-Flow treats each pipeline stage as a distinct role — analyst, architect, developer, reviewer, archivist — each backed by an independently configured AI agent with its own model, its own tools, and its own permission set. A reasoning-heavy model handles design; a fast, economical model executes against a locked spec. The agents collaborate across stages, handing off structured deliverables rather than loose context. It's not one model doing everything — it's a team.

---

**Built on Karpathy's AI coding principles — enforced, not suggested**

- *Think Before Coding* — Ambiguity unresolved? You're not entering Planning.
- *Simplicity First* — Hooks reject unnecessary abstraction during Coding.
- *Surgical Changes* — Review audits diffs. Unrelated edits? Rejected.
- *Goal-Driven Execution* — Every stage has pass/fail criteria. No "looks good to me."

These aren't prompt tips. They're wired into the framework.

---

**What it ships with**

🖥️ **Dual interface** — Terminal TUI (`sw monitor`) for real-time conversation streaming and keyboard-driven control, plus a Web Dashboard with SSE for remote supervision and multi-task management.

🔌 **Agent-agnostic backends** — Gemini, OpenCode, PTY, or bring your own. Configure per stage, per role. No vendor lock-in.

🗑️ **Soft-delete task system** — Tasks go to `.trash`, not `/dev/null`. Full state preserved. Restore anytime.

---

**What Harness-Flow is (and isn't)**

It's not an AI coding assistant. It's an **AI development execution engine** — it manages the entire process by which AI takes a requirement from ambiguity to deployed code, and holds every step accountable. You supervise. The framework enforces.

---

**Python. Open source. Actively dogfed.**

Every project under the `repo/` directory was built with the same engine. The framework develops itself.

Repo is public. Feedback and contributions welcome. 🔗

---

## LinkedIn-Ready Version (Plain Text)

---

Introducing Harness-Flow: Bringing Engineering Discipline to AI-Driven Development

The problem with AI coding isn't the models — it's the lack of process. You describe a feature, AI generates code, you review, fix, re-prompt, repeat. Unstructured, expensive, exhausting.

Harness-Flow changes this with a gated development pipeline that AI can't skip:

🧠 Brainstorming → 📐 Planning → 💻 Coding → 🔍 Review → 📦 Archive

Every stage produces a deliverable. Every stage boundary runs compliance checks — templates must be complete, builds must pass, diffs must be clean. You can't advance until quality standards are met. CI/CD discipline, applied to AI development itself.

🔧 Different stages, different agents. A reasoning-heavy model handles design; a fast, cheap model executes against a locked spec. It's not one AI doing everything — it's a coordinated team handing off structured deliverables.

🖥️ Terminal TUI + Web Dashboard for real-time monitoring. Agent-agnostic backends — Gemini, OpenCode, PTY, bring your own. No vendor lock-in.

Harness-Flow isn't an AI coding assistant. It's an execution engine that takes a requirement from ambiguity to deployed code, holds every step accountable, and scales across multiple projects. You supervise. The framework enforces.

Python. Open source. Actively dogfed — the framework develops itself.

🔗 [repo link]
