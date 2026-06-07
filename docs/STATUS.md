# ChronoScope — Current Status

**Last updated:** 2026-06-06
**Last session:** Geomagnetic (Kp/ap) labels built + validated vs Sept 2017 G4 and May 2024 G5 storms (EXP-003, DEC-008)

---

## Where We Are Right Now

**Phase:** Phase 1 of causal-diagnosis engine — foundation for ML work.

**Codebase health:**
- 437 tests passing (was 409; +24 geomagnetic labels, +4 CorpusReader label-view)
- HAPI ingester column mapping verified (DEC-005)
- Corpus storage layer implemented, unit-tested, AND populated with real data (DEC-004 fully executed)
- **Historical DSCOVR corpus built: 271.4M MAG rows + 1.38M plasma rows across 3,601 days (2016-07-27 -> 2026-06-05), zero failed days**
- Corpus validated against ground truth (Sept 2017 G4 storm) — see EXPERIMENTS.md EXP-001
- `pyarrow==24.0.0` and `duckdb==1.5.3` added to `requirements.txt`

**Strategic direction unchanged** — automated causal root-cause diagnosis (DEC-003).

---

## What's Working

- Telemetry ingestion: live DSCOVR (SWPC), archive DSCOVR (HAPI), ACE, CelesTrak, OpenSky
- HAPI archive ingester with verified column mapping (DEC-005)
- **NEW: Corpus persistence layer** (`src/chronoscope/corpus/storage.py`):
  - `write_partitioned_parquet(packets, root, ...)` — writes year/month-partitioned Parquet files with zstd compression
  - `CorpusReader(root)` — DuckDB-backed SQL query helper; exposes `mag` and `plasma` views, plus `count()` and `time_range()` convenience methods
  - HAPI fill-value filtering (`-1.0E31` and NaN) — on by default, can be disabled
  - Plasma DQF gate (drop rows with `data_quality_flag != 0`) — on by default
  - Honest `WriteReport` per instrument with `rows_seen / rows_written / rows_dropped_fill / rows_dropped_dqf`
  - Idempotent writes (re-writing the same data overwrites cleanly, no duplicates)
  - Empty-corpus reads return zero rows (no crash on fresh trees)
- Deterministic replay, audit chain, anomaly detection, REST API, CLI, reporter — unchanged

## What's NOT Working / Missing

- ~~Storage layer not yet exercised against real DSCOVR data.~~ DONE 2026-06-03: smoke-tested against real CDAWeb data for 2018-03-15. MAG 86,378 rows written (22 fill-dropped), plasma 1,440 rows written (0 dropped), both round-trip cleanly via CorpusReader. This surfaced and fixed the plasma nanosecond-timestamp bug (see below).
- ~~No bulk-backfill script.~~ DONE 2026-06-06: `scripts/build_dscovr_corpus.py` (commit 9d773a2), daily chunking, resumable per-day checkpoint. Ran the full window clean: 3,601 days, 0 failures.
- **No post-2019 plasma source** — `DSCOVR_H1_FC` ends 2019-06-27; corpus plasma stops there. Now formalized in DEC-006 (deferred to NOAA NCEI).
- ~~No packaged corpus sanity checks.~~ DONE 2026-06-06: `src/chronoscope/corpus/validation.py` runs coverage / |B|-consistency / physical-range checks over the whole corpus (console + JSON, CI exit code). It found 647 implausible plasma density rows; fixed via DEC-007 gate + in-place cleanup. Corpus now passes ALL checks.
- No causal inference, no ML models, no web UI.

---

## Next Up

### Phase 1: Foundation for ML work (current focus)

- [x] Build historical DSCOVR archive ingester (HAPI path)
- [x] Validate ingester against live CDAWeb HAPI (DEC-005)
- [x] Add `pyarrow` and `duckdb` to `requirements.txt`
- [x] Corpus persistence layer (Parquet writer + DuckDB query helper + filtering)
- [x] **Smoke-test storage against real data** (DONE 2026-06-03):
  - Pulled 2018-03-15 (inside H1_FC window), piped into `write_partitioned_parquet`
  - MAG: 86,400 seen / 86,378 written / 22 fill-dropped / 0.0% drop. File 5.7 MB.
  - Plasma: 1,440 seen / 1,440 written / 0 dropped / 0.0% drop. File 81 KB.
  - **Found + fixed plasma nanosecond-timestamp bug** (commit fec9770): CDAWeb emits 9-digit fractional seconds; strptime %f caps at 6, so every plasma row was silently dropped. Rewrote `_parse_hapi_time` via fromisoformat + truncation. 4 regression tests added.
- [x] **Bulk-backfill script** — `scripts/build_dscovr_corpus.py` (DONE 2026-06-06, commit 9d773a2):
  - Walks the date range ONE DAY at a time (month-sized requests time out at CDAWeb)
  - Per-day JSON checkpoint; resumes on restart; retry-then-skip with --retry-failed
  - Honors exact --start/--end (fixed a month-flooring bug from the first draft)
  - FULL RUN COMPLETE: 3,601 days, 271.4M MAG + 1.38M plasma rows, 0 failures.
    cumulative_mag_dropped_fill=7,871,567 (real data gaps); plasma_dropped_dqf=60,315.
  - Tail (~last 2 weeks) writes zero rows: definitive data lags real-time, HAPI
    returns a 1201 'no data' status. Cosmetic TODO: skip that status quietly
    instead of emitting magnetic_parse_failed warnings on the header lines.
- [x] **Corpus sanity-check helpers** — `src/chronoscope/corpus/validation.py` (DONE 2026-06-06):
  - coverage (per-day rows vs theoretical max), b_field_consistency (|B| vs
    components, whole corpus), physical_ranges (DEC-007 bounds), optional
    precise cadence on one day. Console summary + JSON report + CI exit code.
  - Found 647 implausible plasma density rows (EXP-002); fixed via DEC-007
    storage gate + scripts/clean_corpus_plausibility.py (in-place, no re-fetch).
  - Corpus now PASSES all checks. Plasma 1,382,215 -> 1,381,568 after cleanup.
  - Run: python src/chronoscope/corpus/validation.py --root E:\chronoscope_corpus --report corpus_validation.json
- [x] **Validate corpus against ground truth (EXP-001, 2026-06-06):** queried the
  Sept 7-8 2017 G4 storm. Measured min Bz_GSE -33.99 nT, max |B| 34.52 nT, max
  speed 860 km/s vs published DSCOVR record (Bz ~-32.9 GSM, |B| ~34 nT, speed
  ~700+ km/s). Quiet day 2017-09-01 showed max |B| 11 nT. Corpus is faithful.
- [~] Cross-reference NOAA event catalogs (G-storm, Kp/Ap, Richardson & Cane ICME):
  - [x] **Kp/ap geomagnetic labels** (DONE 2026-06-06, DEC-008/EXP-003):
    src/chronoscope/labels/geomagnetic.py fetches GFZ Kp+ap, derives G-scale,
    writes labels/geomagnetic/kp_ap.parquet. CorpusReader registers a `kp` view;
    join via ASOF. Validated: Sept 2017 -> G4, corpus-max Kp 9.0 = May 2024 G5.
    G-storm scale is derived from Kp (not a separate source).
  - [ ] Richardson & Cane ICME catalog (HTML-table parse -> labels/icme/) -- NEXT
- [ ] Create labeled training dataset
- [ ] Set up evaluation framework for causal diagnosis accuracy

### Phase 2: Initial causal inference

- [ ] Integrate PCMCI (Tigramite)
- [ ] Run initial causal discovery on the corpus
- [ ] Validate against known space weather physics
- [ ] Causal graphs as first-class domain objects

### Phase 3: Production integration

- [ ] Causal engine → audit chain
- [ ] Causal explanation generator
- [ ] REST API for causal queries
- [ ] Performance optimization

---

## How to Smoke-Test the Storage Layer (Utsav-side, this/next session)

Quick script you can run from PowerShell once you've pulled this session's changes:

```powershell
# from repo root
.venv\Scripts\activate
pip install -r requirements.txt   # picks up new pyarrow + duckdb

python -c @"
from datetime import datetime, timezone
from pathlib import Path
from src.chronoscope.ingestion.noaa_dscovr_archive import NOAADscovrArchiveIngester
from src.chronoscope.corpus.storage import write_partitioned_parquet, CorpusReader

# One day from inside the H1_FC coverage window (so we get both MAG and plasma)
start = datetime(2018, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
end   = datetime(2018, 3, 16, 0, 0, 0, tzinfo=timezone.utc)

ingester = NOAADscovrArchiveIngester()
packets = ingester.fetch_packets('DSCOVR', start, end)

root = Path('data/corpus_smoke')
reports = write_partitioned_parquet(packets, root)

for inst, r in reports.items():
    print(f'{inst}: seen={r.rows_seen} written={r.rows_written} '
          f'fill_dropped={r.rows_dropped_fill} dqf_dropped={r.rows_dropped_dqf} '
          f'drop_rate={r.drop_rate:.1%}')

with CorpusReader(root) as reader:
    print('mag count :', reader.count('mag'))
    print('plasma count:', reader.count('plasma'))
    print('mag time range:', reader.time_range('mag'))
"@
```

What "sensible" looks like:
- **MAG**: 86,400 rows seen (one per second), most written, a small fraction dropped to fill values during data gaps. File size ~1–3 MB per day under zstd.
- **Plasma**: ~1,440 rows seen (one per minute), most written. File size ~50 KB per day.
- **Drop rate**: anything under ~10% is plausible. Higher than 50% means there's a major data gap that day, which is real but worth knowing about.

If anything looks weird, paste the output back to me and we debug before building the backfill script on top.

---

## Open Questions / Blockers

- ~~Storage strategy.~~ Resolved by DEC-004.
- ~~HAPI column verification.~~ Resolved by DEC-005.
- ~~Post-2019 definitive plasma data.~~ Formalized in DEC-006: deferred to NOAA NCEI; corpus plasma intentionally stops at 2019-06-27 for now. MAG runs to present.
- **ML cofounder timeline.** Same as last session.
- **Pilot customer pipeline.** Same as last session.

---

## Notes for Repo Maintenance

- GitHub "About" blurb still says "246 tests" — should now read "437 tests".
- Corpus is local only, NOT committed (~17 GB of Parquet). Moved off the full C: drive on 2026-06-06; now lives at `E:\chronoscope_corpus` on Utsav's machine. All backfill/query commands must pass `--root E:\chronoscope_corpus`. The per-day checkpoint moved with it, so re-running resumes (shows already_completed=3601, days_to_process=0). Rebuild from scratch on any machine with `python scripts/build_dscovr_corpus.py --root <path>`.
- `raw.githubusercontent.com` caches aggressively; fresh `git clone` is the source of truth.

---

## How To Use This File

**START of every session:** "read STATUS.md and let's continue."
**END of every session:** Claude updates STATUS.md, DECISIONS.md, EXPERIMENTS.md as needed. Utsav commits and pushes.
