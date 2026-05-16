# 04-Review Hooks

## hook-04-01: Impact Analysis
- **When**: during
- **Rule**: assess side effects on other components
- **Check**: API contract? Performance? Upstream/downstream?

## hook-04-02: Security Audit
- **When**: during
- **Rule**: no secrets, injection risk, or missing validation
- **Check**: all inputs validated, no hardcoded keys

## hook-04-03: Zero-Memory Review
- **When**: during
- **Rule**: can a newcomer understand the diff without context?
- **Check**: self-explanatory names and commit messages

## hook-04-04: Full Build
- **When**: post
- **Rule**: full CI pipeline must pass
- **Check**: `mvn verify` / `npm test` / equivalent → green

## hook-04-05: Deliverable Consistency
- **When**: post
- **Rule**: code, test, doc, config in sync
- **Check**: README / task.yaml reflect latest state
