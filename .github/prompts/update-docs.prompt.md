---
description: "Run after adding a feature. Reviews and updates all doc files: copilot-instructions.md (LLM context), README.md (feature showcase), SETUP.md (onboarding), .env.example (config template). Orchestrates llm-coach, docs-writer, and setup-reviewer agents."
agent: "agent"
argument-hint: "Describe the feature you just added"
tools: [read, edit, search, agent]
---

A feature was just added or modified. Review all documentation and update it to reflect the change.

## Detect What Changed

Use `git diff HEAD~1` and the user's description to identify: new commands, new env vars, new behavior, changed defaults, new dependencies, renamed/deleted items.

## Update Sequence

Read each file before editing. Make minimal targeted edits — do NOT rewrite entire files.

### 1. `.github/copilot-instructions.md` — LLM context

Delegate to @llm-coach. Optimize for **token efficiency** — future LLMs consume this file.

- Add new commands, env vars, defaults, architectural facts
- Remove anything stale (renamed functions, deleted features, changed defaults)
- Dense format: tables, terse prose, concrete values. Every token must change LLM behavior
- No prose explanations, no commentary, no filler

### 2. `README.md` — Feature showcase

Delegate to @docs-writer. Optimize for **selling the project** to someone seeing it for the first time.

- Update feature table if there's a new user-facing capability
- Update example questions/commands if relevant
- Update architecture diagram if the stack changed
- Lead with what's cool — this is marketing, not a manual
- Do NOT duplicate SETUP.md content

### 3. `SETUP.md` — First-time onboarding

Delegate to @docs-writer. Optimize for **a new user going from zero to running in one pass**.

- New required config: add to the numbered flow where it naturally fits
- New optional config: add to "Advanced Configuration" section at the bottom (create if missing)
- Keep the main happy path simple — no decision branches for power-user features
- Every new step needs a completion signal ("you should now see X")
- Advanced/power-user settings stay out of the main walkthrough

### 4. `.env.example` — Config template

Delegate to @docs-writer. Optimize for **self-documenting config that works on first `cp .env.example .env`**.

- Required vars: uncommented with `YOUR_` placeholder
- Optional vars: commented out with default value shown
- Match existing `# ── Section ──` grouping
- One-line comment per var

### 5. Consistency audit

Delegate to @setup-reviewer. **Read-only** — report findings, do NOT auto-fix.

- Cross-reference new env vars across .env.example, SETUP.md, README.md, and copilot-instructions.md
- Verify no broken cross-references between docs
- Check that the happy path in SETUP.md still works without the new feature (progressive disclosure)
- Flag any contradictions introduced by the updates above

## Output

After all updates, provide a brief summary:
- Files changed and what was updated in each
- Findings from the consistency audit
- Decisions made (e.g., "put X in Advanced section because it's not needed for basic setup")
