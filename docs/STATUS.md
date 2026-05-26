# ChronoScope — Current Status

**Last updated:** 2026-05-26
**Last session:** HAPI parser verification & rewrite (DEC-005)

---

## Where We Are Right Now

**Phase:** Phase 1 of causal-diagnosis engine — foundation for ML work.

**Codebase health:**
- 370 tests passing (was 364; +6 net new after parser rewrite)
- HAPI ingester column mapping verified against live CDAWeb `/info` responses
- Documentation up to date

**Strategic direction unchanged** — automated causal root-cause diagnosis (DEC-003). Storage strategy resolved (DEC-004). Ingester correctness now verified (DEC-005).

---

## What's Working

- Telemetry ingestion: NOAA DSCOVR live (7-day SWPC feed), ACE, CelesTrak, OpenSky
- **NOAA DSCOVR historical archive ingester** via NASA CDAWeb HAPI server.
  - `DSCOVR_H0_MAG` 1-sec magnetometer (2015 → present)
  - `DSCOVR_H1_FC` 1-min Faraday Cup plasma (2016-06-03 → 2019-06-27, definitive)
  - Parsers verified column-by-column against live `/info` responses (DEC-005)
  - DQF (data quality flag) preserved for downstream filtering
  - `THERMAL_TEMP` used directly (no more redundant thermal-speed math)
  - Post-2019 plasma requests get clear warnings instead of silent empty corpus
  - GSE coordinate frame keys (`bx_gse_nt`) distinct from live ingester's GSM keys
- Operational-window safety: pre-2016-07-27 requests clamped to commissioning date
- Deterministic replay, cryptographic audit chain, basic anomaly detection,
  REST API, CLI, reporter — unchanged

## What's NOT Working / Missing

- **No corpus persistence layer yet** — ingester yields `TelemetryPacket` objects but Parquet+DuckDB storage (per DEC-004) is not wired up. This is the next concrete step.
- **No bulk-backfill script** — single-day ingest works; multi-year orchestration with resumable checkpoints does not exist yet.
- **No post-2019 plasma source** — `DSCOVR_H1_FC` ends 2019-06-27. Post-2019 definitive plasma is at NOAA NCEI in a different format; integrating it is a separate work item (DEC-006 candidate).
- **HAPI fill-value filtering** — `_safe_float` currently keeps `-1.0E31` fill values. A pre-storage filter should drop fill rows before they hit the corpus.
- No causal inference, no ML models, no web UI — same as last session.

---

## Next Up

### Phase 1: Foundation for ML work (current focus)

- [x] Build historical DSCOVR archive ingester (HAPI path)
- [x] Validate ingester against live CDAWeb HAPI (column order verified; DEC-005)
- [ ] **Add `pyarrow` and `duckdb` to `requirements.txt`** (per DEC-004)
- [ ] **Corpus persistence layer** — `src/chronoscope/corpus/storage.py`:
  - `write_partitioned_parquet(packets, root_dir)` — writes year/month-partitioned Parquet files
  - `query(sql)` — DuckDB query helper over the partitioned dataset
  - Filter HAPI fill values (`-1.0E31`) and rows with `DQF != 0` for plasma
- [ ] **Bulk-backfill script** — `scripts/build_dscovr_corpus.py`:
  - Walks the date range a month at a time
  - Persists a JSON checkpoint file after each month
  - Resumes from checkpoint on restart
  - Honest progress logging (rows ingested, fill rows dropped, errors)
- [ ] **Validation-on-corpus** — once built, sanity-check the corpus:
  - Magnitude `|B|` ≈ `sqrt(Bx² + By² + Bz²)` within tolerance
  - `bulk_speed_km_s` ≈ `sqrt(Vx² + Vy² + Vz²)` within tolerance
  - Plasma timestamps cleanly 60s apart, MAG timestamps 1s apart
- [ ] Cross-reference with NOAA's published space weather event catalogs (G-storm catalog, Kp/Ap index series, Richardson & Cane ICME catalog)
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

- ~~**Storage strategy for historical corpus.**~~ Resolved by DEC-004.
- ~~**HAPI column-order verification.**~~ Resolved by DEC-005.
- **Post-2019 definitive plasma data.** `DSCOVR_H1_FC` ends 2019-06-27. The NOAA NCEI archive has later data but in a different format (NetCDF, different variable names). Integrating it adds significant scope. Decision deferred — likely DEC-006 once Parquet storage is working and we have a clear picture of how the limited plasma window affects causal-discovery experiments.
- **ML cofounder timeline.** Causal inference work past prototype stage genuinely needs a domain expert. When do we start recruiting?
- **Pilot customer pipeline.** Need to start cold outreach to CubeSat programs in parallel with technical work.

---

## Notes for Repo Maintenance

- GitHub repo's "About" blurb still says "246 tests" — should now read "370 tests" via the repo settings web UI.
- `raw.githubusercontent.com` caches aggressively — when fetching STATUS.md to start a session, the URL can serve stale content for several minutes after a push. If something looks off at session-start, a fresh `git clone` is the source of truth.

---

## How To Use This File

**At the START of every session:** Tell Claude "read STATUS.md" and we're caught up.

**At the END of every session:** Claude updates this file with:
- What was accomplished
- What's still in progress
- Any new blockers or open questions
- Next concrete action

Then commit and push the updated STATUS.md.
