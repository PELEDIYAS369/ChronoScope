# ChronoScope — Working Session Protocol

This document explains how Utsav and Claude collaborate on ChronoScope.

---

## Why This Protocol Exists

Claude has no memory between conversations. Each new chat starts from zero.

To work effectively on a long-running project, we need to make Claude's "memory" live in the repository itself — so any session can pick up exactly where the last one left off.

This is not a workaround. It is the actual workflow.

---

## The Three Memory Files

| File | Purpose | Updated |
|---|---|---|
| `docs/STATUS.md` | Current state, what's next, blockers | Every session |
| `docs/DECISIONS.md` | Architectural decisions and *why* | When decisions are made |
| `docs/EXPERIMENTS.md` | ML experiment log (lab notebook) | When experiments run |

---

## Starting a Session

**Opening message to Claude:**

> "Read STATUS.md and let's continue."

That's it. Claude will read the latest STATUS.md, understand the current phase, and we proceed.

Optional — for token efficiency, set up a Claude Project named "ChronoScope" with the GitHub repo URL in project knowledge. Then STATUS.md, DECISIONS.md, and key code files are referenced automatically without re-pasting.

---

## Ending a Session

Before closing the conversation, Claude must:

1. **Update STATUS.md** — what we accomplished, what's still in progress, any new blockers, next concrete action.
2. **Update DECISIONS.md** — if any architectural choices were made or alternatives rejected.
3. **Update EXPERIMENTS.md** — if any ML experiments ran, log the results.

Utsav then commits and pushes:

```bash
git add docs/STATUS.md docs/DECISIONS.md docs/EXPERIMENTS.md
git commit -m "Session N: [brief summary]"
git push origin main
```

---

## Honest Limits of This Workflow

This approach gives Claude continuity but does NOT give Claude:

- The ability to run experiments overnight while Utsav sleeps
- Long-term intuition built up over months of staring at the same dataset
- The "scars" of having shipped similar systems before
- Credibility in acquisition due diligence (acquirers want named human experts on the team)

For the prototype-to-traction phase, this workflow is sufficient.

For acquisition or scaling beyond prototype, we will need to bring on a human ML cofounder. See DEC-002 in DECISIONS.md.

---

## Quality Standards

Whatever Claude builds in a session must:

- Pass all existing tests before the session ends
- Include new tests for new functionality
- Be committed with a clear message
- Have STATUS.md updated to reflect the new state

If a session ends with broken tests or no documentation update, the next session starts in confusion. Don't do this.
