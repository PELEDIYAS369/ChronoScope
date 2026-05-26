# ChronoScope — Current Status

**Last updated:** 2026-05-25
**Last session:** Phase 1 kickoff — historical ingester (HAPI) + storage decision (DEC-004)

---

## Where We Are Right Now

**Phase:** Phase 1 of causal-diagnosis engine — foundation for ML work.

**Codebase health:**
- 364 tests passing (was 334; +30 new for the HAPI archive ingester)
- Repository hygiene from DEC-001 still in good shape
- Documentation up to date

**Strategic direction unchanged** — automated causal root-cause diagnosis (DEC-003).
The storage-strategy blocker is now resolved (DEC-004).

---

## What's Working

- Telemetry ingestion: NOAA DSCOVR live (7-day SWPC feed), ACE, CelesTrak, OpenSky
- **NEW: NOAA DSCOVR historical archive ingester** via NASA CDAWeb HAPI server
  (`DSCOVR_H0_MAG` 1-sec MAG, `DSCOVR_H1_FC` 1-min Faraday Cup plasma).
  Implements `BaseIngester`, returns `TelemetryPacket` objects compatible with
  the existing replay / audit / detection pipeline. Same APIDs as the live
  ingester, distinguished by the `source` field (`noaa_dscovr_archive` vs
  `noaa_dscovr`). Uses GSE coordinate-frame keys (`bx_gse_nt`) distinct from
  live GSM keys (`bx_gsm_nt`) — explicit, not silently converted.
- Operational-window safety: requests for pre-2016-07-27 data are silently
  clamped to the DSCOVR commissioning date so ground-test data can't poison
  any training corpus.
- Deterministic replay, cryptographic audit chain, basic anomaly detection,
  REST API, CLI, reporter — all unchanged from last session.

## What's NOT Working / Missing

- **No corpus persistence layer yet** — the ingester yields `TelemetryPacket`
  objects but we haven't wired up Parquet+DuckDB storage (per DEC-004). This
  is the next concrete step.
- **No bulk-backfill script** — single-day ingest works; multi-year orchestration
  with resumable checkpoints does not exist yet.
- **No causal inference** — same as last session; this is Phase 2.
- **No ML models** — same as last session.
- **No web UI** — same as last session.
- **HAPI ingester has not been verified against a real live NASA HAPI
  response** — sandbox network restrictions block the NASA domains. Tests
  use mocked CSV bodies following the documented column order. First action
  next session should be a live one-day pull from Utsav's machine to validate
  the column-order assumptions hold against the actual server.

---

## Next Up

### Phase 1: Foundation for ML work (current focus)

- [x] **Build historical DSCOVR archive ingester** (HAPI path; CDF fallback deferred)
- [ ] **Validate ingester against live CDAWeb HAPI** — Utsav runs a one-day pull
  from a non-sandboxed machine; we confirm the documented column order matches
  reality and the parameter values are sane.
- [ ] **Add `pyarrow` and `duckdb` to `requirements.txt`** (per DEC-004)
- [ ] **Build the corpus persistence layer** — `src/chronoscope/corpus/storage.py`
  with `write_parquet(packets, partition_root)` and `query(sql)` helpers
- [ ] **Bulk-backfill script** — `scripts/build_dscovr_corpus.py` that walks the
  date range, writes Parquet in monthly partitions, and persists a checkpoint
  file so it can resume after interruption
- [ ] Cross-reference with NOAA's published space weather event catalogs
  (G-storm catalog, Kp/Ap index series, Richardson & Cane ICME catalog)
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

- ~~**Storage strategy for historical corpus.**~~ **Resolved by DEC-004**:
  Parquet files partitioned by year/month, queried via DuckDB. HAPI for fetch,
  CDF/SPDF as future fallback. New dependencies: `pyarrow`, `duckdb`.
- **HAPI column-order verification.** The ingester's column mapping is based
  on documented dataset schemas, not on a real response from the CDAWeb HAPI
  server (sandbox network limits). Next session must validate against a real
  pull before we trust corpus contents.
- **ML cofounder timeline.** Causal inference work past prototype stage
  genuinely needs a domain expert. When do we start recruiting?
- **Pilot customer pipeline.** Need to start cold outreach to CubeSat programs
  in parallel with technical work.

---

## Notes for Repo Maintenance

- The GitHub repo's "About" blurb still says "246 tests" — should be updated to
  "364 tests" via the repo settings web UI when next convenient. STATUS.md and
  README.md are now both authoritative on 364.

---

## How To Use This File

**At the START of every session:** Tell Claude "read STATUS.md" and we're caught up.

**At the END of every session:** Claude updates this file with:
- What was accomplished
- What's still in progress
- Any new blockers or open questions
- Next concrete action

Then commit and push the updated STATUS.md.
