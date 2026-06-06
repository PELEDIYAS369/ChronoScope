# ChronoScope — Decision Log

A record of significant architectural, technical, and strategic decisions and **why** we made them. Each entry includes alternatives considered and reasoning. This prevents us from re-litigating the same questions in future sessions.

Format: most recent decisions at the top.

---

## DEC-007: Physical-plausibility filter as a third storage-layer gate

**Date:** 2026-06-06
**Status:** Accepted

**Context:** Whole-corpus validation (validation.py / EXP-002) found 647 plasma
rows with physically impossible proton_density_n_cc values: 636 negative (down
to -82.3 cm^-3) and 11 absurdly high (up to 3.57e10 cm^-3). These were NOT fill
values (-1e31) and were flagged data-quality-good (DQF=0), so the existing
fill-value and DQF gates did not catch them -- they are genuine source-level
corruption. 647 of 1.38M plasma rows (0.047%); MAG (271M rows) was clean.

**Decision:** Add a physical-plausibility filter as a THIRD gate in the storage
layer (write_partitioned_parquet), alongside the fill-value and DQF gates.
Rows whose physical quantities fall outside generous bounds (PHYSICAL_BOUNDS in
storage.py) are dropped at write time and never enter the corpus. Bounds:
  - proton_density_n_cc: [0, 200] cm^-3   (real L1 <100 even in extreme sheaths)
  - bulk_speed_km_s:     [150, 1500] km/s
  - ion_temperature_k:   [0, 1e8] K
  - bt_nt / b*_gse_nt:   [0,500] / [-500,500] nT
The filter is on by default (apply_plausibility_filter=True) and can be disabled
for raw data exploration. The existing corpus was remediated in place by
scripts/clean_corpus_plausibility.py (same bounds, imported from storage.py so
they cannot drift) -- 647 rows removed, no CDAWeb re-fetch needed.

**Alternatives considered:**
- Read-side filtering only (each consumer excludes bad rows): rejected -- pushes
  the problem downstream and every consumer must remember to filter. Corpus
  should be trustworthy as stored.
- Widen validation bounds until the check passes: rejected -- that hides
  corruption rather than removing it.
- Re-fetch the whole corpus with the new gate: rejected -- 10+ hours for no
  benefit over an in-place rewrite of 83 affected files.

**Bound rationale:** 200 cm^-3 for density catches 100% of the garbage (bad
positives were all >=500 except one at ~250) while never clipping a real
measurement (L1 density essentially never exceeds ~100). Tightening to 100 would
clip rare-but-real high-density events for zero additional garbage caught.

**Consequences:**
- Corpus is clean-by-construction for all future backfills.
- A small number of legitimate-but-extreme readings could in principle be
  clipped, but the bounds are set well beyond any physically real value.
- WriteReport now carries rows_dropped_implausible. 7 new unit tests pin the
  gate's behavior (409 tests total).

---

## DEC-006: Defer post-2019 definitive plasma to NOAA NCEI; corpus plasma stops at 2019-06-27

**Date:** 2026-06-06
**Status:** Accepted

**Context:** The CDAWeb HAPI plasma dataset DSCOVR_H1_FC was not updated past
2019-06-27 (the dataset effectively froze after the DSCOVR safe-mode incident).
The 1-second magnetometer dataset DSCOVR_H0_MAG is unaffected and runs to the
present. The 2026-06-06 backfill therefore produced a corpus with MAG coverage
2016-07-27 -> present (~271M rows) but plasma only through mid-2019 (~1.38M
rows). Post-2019 definitive plasma exists, but in a different product/format at
NOAA NCEI, not via the same HAPI endpoint.

**Decision:** Accept the asymmetric corpus as-is for now. MAG runs full-length;
plasma stops at 2019-06-27. Do NOT block Phase 2 causal work on acquiring
post-2019 plasma. The ingester already short-circuits plasma requests past the
H1_FC end date and logs a clear warning, so this is explicit, not silent.

**Alternatives considered:**
- Ingest NOAA NCEI post-2019 plasma now: rejected for this phase — different
  format/endpoint, real integration cost, and the 2016-2019 plasma+MAG window
  is already enough to develop and validate the causal pipeline.
- Drop plasma entirely and go MAG-only: rejected — the 2016-2019 joined
  plasma+MAG window is the richest part of the corpus for causal discovery.

**Reasoning:** The ~3-year overlapping plasma+MAG window spans real storms
(incl. the validated Sept 2017 G4 event, EXP-001) — sufficient to build labeled
datasets and run/validate causal discovery. Post-2019 plasma is an enhancement,
not a prerequisite.

**Consequences:**
- Any analysis requiring BOTH plasma and MAG is limited to 2016-07-27 ->
  2019-06-27 until NCEI ingestion is built.
- MAG-only analyses (and MAG-only anomaly windows) can use the full ~10 years.
- A future NCEI plasma ingester would extend the joined window; tracked as a
  follow-on, not scheduled.

---

## DEC-005: HAPI column-order verification + DSCOVR_H1_FC end-date acknowledgement

**Date:** 2026-05-26
**Status:** Accepted (implemented)

**Context:** DEC-004 committed to using the CDAWeb HAPI server for historical DSCOVR ingest, but the column mapping in the initial parser was based on documented dataset schemas rather than a live `/info` response (the sandbox where the code was written can't reach NASA domains). STATUS.md flagged this as the first thing to validate before any corpus-building work began.

A 30-second `curl` against `https://cdaweb.gsfc.nasa.gov/hapi/info?id=DSCOVR_H1_FC` and `DSCOVR_H0_MAG` surfaced three things the documented-schema approach got wrong, plus one additional finding about dataset coverage.

**What we learned:**

1. **HAPI flattens vector parameters across multiple CSV columns.** A parameter declared with `size: [3]` (e.g. `V_GSE`, `B1GSE`) is published as three consecutive columns. The CSV column count is therefore much larger than the number of named parameters in `/info`. Real layouts:

   `DSCOVR_H1_FC` — **14 CSV columns**:
   `Time, DQF, V_GSE[0], V_GSE[1], V_GSE[2], V_GSE_ErrorBars[0..2], THERMAL_SPD, THERMAL_SPD_ErrorBars, Np, Np_ErrorBars, THERMAL_TEMP, THERMAL_TEMP_ErrorBars`

   `DSCOVR_H0_MAG` — **15 CSV columns**:
   `Time, B1F1, B1SDF1, B1GSE[0..2], B1SDGSE[0..2], B1RTN[0..2], B1SDRTN[0..2]`

2. **The original parser had every column wrong after `Time`.** Plasma parser assumed `[1]=Np` when it's actually `[1]=DQF`. Mag parser assumed `[2]=Bx` when it's actually `[2]=stddev of |B|`. If we had built corpus storage on top of this, every Parquet file would have contained silently wrong data — DQF values stored as density, stddev stored as Bx, RTN-frame values mixed in, etc. The errors would have surfaced weeks later as nonsense causal-discovery results, costing a re-run of the full multi-hour backfill.

3. **`THERMAL_TEMP` is published directly in Kelvin.** The original parser computed temperature from thermal speed via `T = v_th² × 60.5`. That math is now redundant and removed; we use the published value directly.

4. **`DSCOVR_H1_FC` was not updated past 2019-06-27.** The dataset's `stopDate` in `/info` is `2019-06-27T23:58:59Z`. This corresponds to the DSCOVR safe-mode incident in June 2019; later definitive plasma data exists at NOAA NCEI in a different format, but not on CDAWeb HAPI. The `DSCOVR_H0_MAG` dataset is unaffected — its `stopDate` is current (`2026-04-05` at time of verification) and continues updating.

**Decision:**

- Rewrite both parsers to match the verified column order.
- Drop the thermal-speed-to-temperature math; use published `THERMAL_TEMP` directly. Keep thermal speed as a separate parameter for downstream use.
- Preserve `DQF` (data quality flag) in the parameters dict so downstream filters can drop bad-quality rows.
- Add a `DSCOVR_H1_FC_END_DATE` constant (`2019-06-28`, exclusive upper bound) and gate plasma fetches on it. Post-2019 plasma requests get a clear `WARNING` log entry and skip the HTTP round-trip instead of silently returning an empty corpus.
- MAG fetches are unconditional (other than the operational-date floor from DEC-004) since `DSCOVR_H0_MAG` covers the full operational period.

**Coverage consequences for the planned corpus:**

| Product | Coverage | Years available |
|---|---|---|
| `DSCOVR_H0_MAG` (1-sec magnetometer) | 2015-06-08 → present | ~11 years |
| `DSCOVR_H1_FC` (1-min Faraday Cup plasma, definitive) | 2016-06-03 → 2019-06-27 | ~3 years |

Post-2019 plasma backfill is a separate work item — see EXPERIMENTS.md and STATUS.md for the open question.

**Alternatives considered:**

- *Continue building corpus storage on the unverified parser* — Rejected. Compounding investment on a wrong foundation. The cost of verification was 30 seconds of `curl`; the cost of discovering the bug after a multi-hour backfill would be the entire backfill.
- *Use only MAG for now, defer plasma to later* — Tempting but rejected. Causal discovery between plasma and field parameters (e.g. `Bz` → enhanced geomagnetic activity, `density × velocity` → dynamic pressure) is the whole scientific point. We need both products in the corpus from the start, even if the plasma window is shorter.
- *Switch to NOAA NCEI plasma archive immediately to get post-2019 coverage* — Deferred. NCEI uses NetCDF in a different schema; integrating it is significant work that would push back Phase 1. Better to ship the 3-year plasma corpus now and add post-2019 as DEC-006 once basic ingest + storage works.

**Reasoning:**

- Verifying assumptions at the lowest layer of the stack early is dramatically cheaper than discovering wrongness after layers of code depend on it.
- The 3-year plasma window is still 3 × 365 × 1440 ≈ 1.5M plasma records, plenty for initial causal discovery experiments. The full MAG corpus (11 years × ~86k records/day ≈ 350M records) is also more than enough.
- Preserving `DQF` is cheap and protects future ML experiments from silently consuming bad-quality rows.

**Consequences:**

- *Easier:* Corpus we build now is trustworthy by construction. No silent data corruption to chase later.
- *Easier:* Operators get a clear warning instead of mysterious silence when requesting post-2019 plasma. The H1_FC end-date being a named constant makes the constraint discoverable.
- *Harder:* The 3-year plasma window vs 11-year MAG window means any joined plasma+mag corpus is limited to ~3 years until we ingest NCEI post-2019 plasma. We accept this for now.
- *Harder:* `_safe_float` still treats HAPI fill values (`-1.0E31`) as valid data. A future filter pass needs to drop fill rows before training. Tracked in STATUS.md.

---

## DEC-004: Use HAPI for historical DSCOVR ingest; Parquet + DuckDB for the corpus

**Date:** 2026-05-25
**Status:** Accepted

**Context:** Phase 1 of the causal-diagnosis work (DEC-003) requires a multi-year historical DSCOVR corpus. STATUS.md flagged the storage approach as an open blocker. Two sub-questions had to be resolved:

1. *How do we pull historical DSCOVR data?* The live ingester uses the SWPC 7-day rolling JSON files, which are useless for archive work.
2. *Where do we store the resulting corpus?*

**Decision:**

**Fetch path:** Use the CDAWeb HAPI server (`https://cdaweb.gsfc.nasa.gov/hapi/`) as the primary access mechanism for historical DSCOVR data. Two datasets are relevant:

- `DSCOVR_H0_MAG` — 1-second definitive fluxgate magnetometer data (Bx/By/Bz in GSE, magnitude)
- `DSCOVR_H1_FC` — 1-minute Faraday Cup solar wind plasma (proton density, velocity vector in GSE, thermal speed)

HAPI is a standardized REST/CSV interface (`/info`, `/data` endpoints) with no auth required. It returns ASCII CSV streamed over HTTPS — no CDF/netCDF binary parsing required for the MVP.

**Storage path:** Persist the corpus as **partitioned Parquet files** on local disk under `data/corpus/dscovr/{instrument}/year={YYYY}/month={MM}/*.parquet`. Query via **DuckDB** (zero-config, embedded) over the Parquet files. No database server, no orchestration layer.

**Coordinate-frame note:** Archive MAG data is published in GSE; the live SWPC ingester publishes in GSM. We will NOT convert in the ingester — we will store under explicitly-named `bx_gse_nt` / `bx_gsm_nt` parameter keys so downstream code can choose. A coordinate-conversion layer can be added later if needed for cross-source comparison.

**Alternatives considered:**

- *Parse CDF files directly from SPDF mirror (`spdf.gsfc.nasa.gov/pub/data/dscovr/`)* — Rejected as primary path. Adds `cdflib` dependency and CDF metadata parsing complexity. Kept as future fallback for resilience if HAPI is down or for sub-second sampling we don't need yet.
- *TimescaleDB / PostgreSQL with time-series extension* — Rejected. Adds a daemon, requires schema migrations, operational burden. Real benefit (concurrent writes, multi-tenant queries) is irrelevant at our current scale and team size.
- *Raw JSON or CSV files in the repo* — Rejected. No compression, no columnar pruning, slow to scan.
- *DuckDB native tables (`.duckdb` file)* — Rejected as the storage format. Parquet is more portable, can be read by anything (pandas, polars, Spark), and DuckDB queries Parquet natively. Best of both worlds: open format + fast queries.

**Reasoning:**

- **HAPI over CDF**: A 10-line `requests.get(...)` call returning CSV is dramatically simpler than parsing CDF binary files. We can iterate fast. The CDF path remains available if we ever need it.
- **Parquet over alternatives**: At our estimated corpus size (see below), columnar Parquet gives ~5–10× compression versus raw CSV, predicate pushdown via DuckDB, and zero infrastructure. We are not write-heavy; we ingest once and query many times.
- **DuckDB over a server DB**: Single-machine analytical workloads with a small team. Embedded DuckDB removes an entire layer of operational complexity for no measurable cost at our scale.
- **Partition by year/month/instrument**: keeps individual files small (tens of MB), makes time-range queries fast via partition pruning.

**Estimated corpus volume (grounded but not yet measured):**

DSCOVR has been operational since 2016-07-27. Through 2026, that's ~10 years.

| Dataset | Cadence | Daily est. | Yearly est. | 10-year est. |
|---|---|---|---|---|
| `DSCOVR_H0_MAG` (1-sec MAG) | 86,400 records/day | ~1.5–3 MB CDF (per THEMIS 1 Hz mag sibling at SPDF: 1.7 MB/day) | ~0.5–1 GB | ~5–10 GB raw |
| `DSCOVR_H1_FC` (1-min FC plasma) | 1,440 records/day | ~100–300 KB | ~50–100 MB | ~0.5–1 GB raw |
| Combined raw | | | | **~6–11 GB raw** |
| Combined Parquet (zstd) | | | | **~1–3 GB on disk** (estimate, 5× compression) |

These are inferred from sibling datasets at SPDF and standard cadence × bytes-per-record arithmetic — we have not yet downloaded a real DSCOVR file from the sandbox used in this session (NOAA/NASA domains are not in our network allowlist). First actual ingestion will validate or correct these numbers; the conclusion (fits comfortably on a laptop, no need for cloud or server DB) is robust to even a 3× miss.

**New dependencies required:**

- `pyarrow` (Parquet read/write) — well-maintained, standard
- `duckdb` (embedded analytical query) — single-binary install, no daemon

Both are pure-Python-installable and have no native build steps on standard Linux/macOS. To be added to `requirements.txt` when the corpus storage layer ships (next session per STATUS.md).

**Consequences:**

- *Easier:* Building Phase 1 (historical ingest, labeled training data, eval framework) is now a straight line — write code, run it, write Parquet.
- *Easier:* Anyone can inspect the corpus with any Parquet-aware tool (pandas, polars, command-line `duckdb` CLI). No vendor lock-in.
- *Harder:* If we ever need concurrent writes from multiple processes or true real-time append, we'll need to revisit (Parquet append patterns are awkward). Not a near-term concern.
- *Harder:* HAPI is one external service. If CDAWeb is down during bulk ingestion, we'd need to fall back to the SPDF mirror or wait. We should plan an offline-resumable bulk ingest script with checkpoint files for the multi-year backfill.

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
