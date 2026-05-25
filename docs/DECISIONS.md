# ChronoScope — Decision Log

A record of significant architectural, technical, and strategic decisions and **why** we made them. Each entry includes alternatives considered and reasoning. This prevents us from re-litigating the same questions in future sessions.

Format: most recent decisions at the top.

---

## DEC-003: Build automated causal root-cause diagnosis as the next major capability

**Date:** 2026-05-25
**Status:** Accepted

**Context:** ChronoScope currently detects anomalies but doesn't diagnose them. Operators still spend days reconstructing causal chains manually. This is the actual bottleneck in the "weeks of investigation" problem.

**Decision:** Build a causal diagnosis engine that, given an anomaly, automatically determines: which parameter deviated first, how the failure propagated through correlated parameters, and produces a verifiable explanation. Use established causal inference techniques (PCMCI, Granger causality, transfer entropy, DoWhy) rather than novel research.

**Alternatives considered:**
- *Cross-organization federated intelligence platform* — Rejected as next step. Requires too much capital and timeline for current resources. Will revisit after causal diagnosis succeeds.
- *Better visualization / dashboard* — Rejected. Already exists in OpenMCT. Not differentiating.
- *Real-time alerting improvements* — Rejected. Incremental, not transformative.
- *Deeper ML on anomaly detection itself* — Deprioritized. Diagnosis is the actual customer pain, not detection.

**Reasoning:**
- Solves a long-standing (40+ year) industry problem
- Builds on ChronoScope's existing strengths (replay + audit)
- Achievable in 12-18 months with focused effort
- Acquisition-worthy combination (audit chain + causal AI is unique)
- Bridges to the federated moonshot (need causal infrastructure first anyway)

---

## DEC-002: Use Claude + GitHub workflow as primary engineering loop

**Date:** 2026-05-25
**Status:** Accepted

**Context:** Solo founder building complex ML system. Limited capital for hires. Need a workflow that maintains continuity across sessions.

**Decision:** Use Claude (via Claude Projects feature) as primary engineering collaborator. Use GitHub as the single source of truth. Maintain STATUS.md, DECISIONS.md, EXPERIMENTS.md as Claude's "memory" between sessions.

**Honest limitations of this approach:**
- No overnight iteration (each session is bounded)
- Less domain intuition than a dedicated ML researcher
- Acquisition due diligence value is lower than having named human experts on the team
- Still need to bring on a human ML cofounder before serious acquisition discussions

**Why we're doing it anyway:**
- Best available option given current capital
- Sufficient for prototype stage that proves the concept
- Generates traction needed to raise funds / recruit the right human teammate

---

## DEC-001: Repository cleanup and honest positioning

**Date:** 2026-05-25
**Status:** Accepted (implemented)

**Context:** Original README and documentation contained overclaims (TRL 7, "exclusive" public data, "AI" for threshold detection, "first unified platform"). Repository contained junk files, encoding issues, and committed generated output. CDL application reviewers and potential customers would see this.

**Decision:** Rewrite all public-facing documentation to be technically honest. Remove sensitive internal docs (pricing strategy, SBIR draft) from public repo. Fix encoding issues. Update test count to actual 334 (was variously 181, 246).

**Reasoning:** Credibility with sophisticated reviewers (CDL mentors, space industry technical people, potential acquirers) requires honest claims. Overclaims that get caught in due diligence cost more than the marketing benefit they provide.

---

## Template for Future Decisions

```
## DEC-NNN: [Short title]

**Date:** YYYY-MM-DD
**Status:** Proposed / Accepted / Superseded by DEC-XXX

**Context:** What problem are we solving? What constraints exist?

**Decision:** What we're doing.

**Alternatives considered:** Other options and why we rejected them.

**Reasoning:** Why this choice over the alternatives.

**Consequences:** What this commits us to, what becomes easier, what becomes harder.
```
