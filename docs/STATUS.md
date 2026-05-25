# ChronoScope — Current Status

**Last updated:** 2026-05-25
**Last session:** Setup — repository cleanup and continuity infrastructure

---

## Where We Are Right Now

**Phase:** Pre-causal-engine (foundation cleanup complete)

**Codebase health:**
- 334 tests passing
- Repository cleaned up (junk files removed, README rewritten, encoding fixed)
- Documentation in good shape
- Ready to begin Phase 1 of causal diagnosis engine

**Strategic direction:**
We are building automated causal root-cause diagnosis for spacecraft telemetry anomalies. The goal is to detect not just *what* went wrong but *why* — tracing the causal chain backward through correlated parameters. This is a known unsolved problem in space ground operations (NASA's ISHM discipline, 20+ years of academic work, no deployable product).

See `DECISIONS.md` for the reasoning behind this direction.

---

## What's Working

- Telemetry ingestion (NOAA DSCOVR, ACE, CelesTrak, OpenSky)
- Deterministic replay engine with SHA-256 fingerprinting
- Cryptographic audit chain
- Basic anomaly detection (z-score + rule-based pattern matching)
- REST API (FastAPI)
- CLI interface
- Reporter (JSON + Markdown)

## What's NOT Working / Missing

- **No historical data corpus yet** — only live DSCOVR feed (~223 packets)
- **No causal inference** — anomaly detection flags but doesn't diagnose causes
- **No ML models** — current "AI" is statistical thresholds, not learned
- **No web UI** — only CLI and REST API
- **No deployment story beyond local Python** — not Dockerized, no install guide for non-developers

---

## Next Up

### Phase 1: Foundation for ML work (current focus)

- [ ] Build historical DSCOVR archive ingester (pull 10+ years of NOAA archived data)
- [ ] Cross-reference with NOAA's published space weather event catalogs
- [ ] Create labeled training dataset (telemetry windows + known events)
- [ ] Set up evaluation framework for measuring causal diagnosis accuracy

### Phase 2: Initial causal inference

- [ ] Integrate PCMCI library (Tigramite from Max Planck) into ChronoScope
- [ ] Run initial causal discovery on DSCOVR historical corpus
- [ ] Validate discovered relationships against known space weather physics
- [ ] Extend domain model to represent causal graphs as first-class objects

### Phase 3: Production integration

- [ ] Hook causal engine into audit chain
- [ ] Build causal explanation generator (human-readable output)
- [ ] Add REST API endpoints for causal queries
- [ ] Performance optimization

---

## Open Questions / Blockers

- **Storage strategy for historical corpus.** 10+ years of DSCOVR data is significant. Need to decide: DuckDB? TimescaleDB? Parquet files? (See DECISIONS.md when this gets resolved.)
- **ML cofounder timeline.** Causal inference work past prototype stage genuinely needs a domain expert. When do we start recruiting?
- **Pilot customer pipeline.** Need to start cold outreach to CubeSat programs in parallel with technical work.

---

## How To Use This File

**At the START of every session:** Tell Claude "read STATUS.md" and we're caught up.

**At the END of every session:** Claude updates this file with:
- What was accomplished
- What's still in progress
- Any new blockers or open questions
- Next concrete action

Then commit and push the updated STATUS.md.
