# ChronoScope — ML Experiments Log

A research notebook for every ML experiment we run: what we tried, what data,
what we found, what it means.

This is the equivalent of a researcher's lab notebook. Without it, we will
repeat failed experiments and forget hard-won insights.

Format: most recent experiments at the top. Number them sequentially
(EXP-001, EXP-002, ...).

---

## EXP-001: Corpus validation against the September 2017 G4 storm

**Date:** 2026-06-06
**Status:** Complete — PASS

**Question:** Does the freshly-backfilled 10-year DSCOVR corpus faithfully
reproduce a real, independently-documented space-weather event? (A go/no-go
trust check before any causal work is built on top of this data.)

**Setup:**
- Data: full corpus at data/corpus (271.4M MAG rows, 1.38M plasma rows,
  2016-07-27 -> 2026-06-05), built by scripts/build_dscovr_corpus.py.
- Target event: 7-8 September 2017 G4 severe geomagnetic storm — strongest of
  solar cycle 24, driven by the X9.3 flare CME. Inside the H1_FC plasma window,
  so both MAG and plasma are available.
- Method: DuckDB aggregate query over the 2017-09-07 -> 2017-09-09 window for
  min Bz_GSE, max |B|, max |B| recomputed from components, and plasma extrema;
  plus a quiet-day contrast (2017-09-01).

**Results (measured in corpus vs published DSCOVR record):**
- min Bz_GSE: -33.99 nT   (published ~ -32.9 nT, GSM) — match
- max Bt (|B|): 34.52 nT   (published peak ~ 34 nT) — match
- max |B| from sqrt(Bx²+By²+Bz²): 34.519197 nT vs stored 34.51962 — agree to 4 dp
- max bulk speed: 859.9 km/s   (published ~700+ km/s at first shock; stream
  intensified over the 2-day window) — consistent
- max proton density: 11.1 /cc; max ion temp: 1.55e6 K — shock signatures present
- Quiet day 2017-09-01: max |B| 11.08 nT, no storm signature

**Interpretation:**
- The corpus reproduces a documented G4 storm at the correct time with the
  correct magnitudes. Ingester + parser + storage carry real physics intact,
  end to end. The Bz sign convention is GSE here (published values often GSM);
  the dramatic southward swing and |B| spike are unmistakable in either frame.
- The |B|-vs-components agreement to 4 decimal places confirms the DEC-005
  column mapping is correct (no axis swap, no unit error).
- The quiet-day contrast confirms the storm signature is real signal, not a
  pipeline artifact.

**Implications:**
- The corpus is trustworthy enough to build labeled datasets and run causal
  discovery against (Phase 2). This was the precondition.
- Next: package the ad-hoc |B|/cadence/drop-rate checks into
  src/chronoscope/corpus/validation.py; cross-reference NOAA storm catalogs to
  label events; then PCMCI.

**Reproducibility:**
- Query: ad-hoc CorpusReader.query_df over data/corpus (storm_check.py, not
  committed — trivial to regenerate; see this session's transcript).
- Corpus build commit: 9d773a2 (scripts/build_dscovr_corpus.py).
- Published reference: SpaceWeatherLive / AGU Space Weather (Redmon et al. 2018)
  and the swsc-journal 07-08 Sep 2017 DSCOVR L1 analysis.

---

## Status: corpus built, first validation done (entering experiment phase)

We are still in the foundation-building phase. No model has trained, no causal
discovery has run. The first experiments will begin once the historical DSCOVR
corpus is persisted to Parquet (see STATUS.md Phase 1 and DEC-004).

**Infrastructure milestone (2026-05-26, this session):**

- *Corpus persistence layer built and unit-tested.* `src/chronoscope/corpus/storage.py` writes `TelemetryPacket` streams to year/month-partitioned Parquet with zstd compression and reads them back via DuckDB. Two instruments (`mag`, `plasma`) get separate trees with distinct schemas. HAPI fill values (`-1.0E31`) and bad plasma DQF rows are filtered at the storage boundary by default; both filters can be disabled for data exploration. Writes are idempotent — re-running a backfill window doesn't duplicate data. 28 new tests, all in `tests/unit/test_corpus_storage.py`. Honest caveat: zero real DSCOVR data has flowed through this code yet (sandbox network limits); first real-data run is Utsav's next session task.

**Pre-experiment recon performed this session (2026-05-26):**

- *HAPI column order verified against live `/info` responses.* Discovered three things the documented-schema-based parser got wrong (vector flattening, position of DQF, B-field stddev mistaken for GSE component). Discovered that `DSCOVR_H1_FC` ends 2019-06-27; only `DSCOVR_H0_MAG` covers the full ~11-year operational period. Parser rewritten with verified mappings; +6 net new tests including a regression test that asserts stddev/RTN columns can't leak into the GSE parameters dict. Full details in DECISIONS.md DEC-005.
- *Coverage matrix for the corpus we will build:*

  | Product | Cadence | Coverage | Records (estimate) |
  |---|---|---|---|
  | `DSCOVR_H0_MAG` | 1 sec | 2015-06-08 → present (~11 yr) | ~350M |
  | `DSCOVR_H1_FC`  | 1 min | 2016-06-03 → 2019-06-27 (~3 yr) | ~1.5M |

  The asymmetry matters: any joined plasma+MAG corpus is limited to the ~3-year plasma window until we ingest NOAA NCEI post-2019 plasma (deferred, possible DEC-006).
- *HAPI fill value confirmed:* `-1.0E31` is the documented fill for both datasets. The current `_safe_float` keeps these values; pre-corpus filtering is queued in STATUS.md Phase 1.

**Pre-experiment recon performed last session (2026-05-25):**

- *Archive access path identified:* NASA SPDF/CDAWeb HAPI server at
  `https://cdaweb.gsfc.nasa.gov/hapi/` serves DSCOVR_H0_MAG (1-sec mag) and
  DSCOVR_H1_FC (1-min plasma) as ASCII CSV over HTTPS. No auth, no API key.
  Documented in DEC-004 and `src/chronoscope/ingestion/noaa_dscovr_archive.py`.
- *Corpus volume estimate:* ~6–11 GB raw for 10 years of both products,
  ~1–3 GB on disk as zstd-compressed Parquet. Inferred from sibling-dataset
  file sizes at SPDF (THEMIS 1 Hz magnetometer ≈ 1.7 MB/day) and cadence
  arithmetic; **not yet verified by an actual download** because the sandbox
  used in this session cannot reach NASA domains.
- *Operational date:* DSCOVR commissioned 2016-07-27. Earlier data is
  ground-test only and must be excluded from any training corpus. Enforced in
  the ingester (`DSCOVR_OPERATIONAL_DATE` constant + test coverage).

**Anticipated first experiments (after corpus build):**

- **EXP-001:** Baseline — run PCMCI on a single year of DSCOVR data, see what
  causal relationships are discovered. Validate against known solar wind
  physics (e.g., Bz southward → enhanced geomagnetic activity).
- **EXP-002:** Sensitivity to window size — does causal discovery quality
  depend on how long a time window we feed PCMCI?
- **EXP-003:** Validation against NOAA event catalog — when a documented space
  weather event occurred (G-storm of e.g. May 2024), did our causal graph
  identify the correct triggering parameter (Bz, V, Np)?

---

## Template for Future Experiments

```
## EXP-NNN: [Short title]

**Date:** YYYY-MM-DD
**Hypothesis:** What we expected to happen and why.

**Setup:**
- Data: [which corpus, what date range, how much]
- Method: [algorithm, library, key parameters]
- Compute: [single machine, time taken]

**Results:**
- [Concrete numbers, charts, examples]
- [Both positive and negative findings]

**Interpretation:**
- What does this mean?
- Did it match the hypothesis? Why or why not?

**Implications:**
- What we change going forward
- What we should try next
- What we definitively rule out

**Reproducibility:**
- Script location: scripts/experiments/expNNN_*.py
- Commit hash: [git SHA]
- Random seed: [value]
```

---

## Key Practices

1. **Log everything, including failures.** Failed experiments are more
   informative than successful ones if we record why they failed.

2. **One experiment = one question.** Don't bundle multiple variables in one
   experiment. Hard to interpret results.

3. **Save the data and the code.** Every experiment should be reproducible.
   Commit the script. Note the data version.

4. **Write interpretation BEFORE checking results.** Force ourselves to predict
   outcomes — this calibrates intuition over time.

5. **Cite sources.** When applying a technique from a paper, link the paper.
   When borrowing code from a library, link the docs.
