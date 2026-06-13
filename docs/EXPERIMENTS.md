# ChronoScope — ML Experiments Log

A research notebook for every ML experiment we run: what we tried, what data,
what we found, what it means.

This is the equivalent of a researcher's lab notebook. Without it, we will
repeat failed experiments and forget hard-won insights.

Format: most recent experiments at the top. Number them sequentially
(EXP-001, EXP-002, ...).

---

## EXP-007: Plasma-era discovery + first causal attribution of the Gannon storm

**Date:** 2026-06-13
**Status:** Complete -- PASS (with documented caveats)

**Question:** (a) Does the richer plasma-era variable set recover more known
drivers? (b) Can the explanation layer attribute the Gannon superstorm to its
causal driver?

**Setup:**
- Discovery over [bz_min, bt_max, sw_speed_mean, density_mean, kp], tau_max 6,
  default 0.1 effect-size floor, 84,960-row matrix.
- Attribution via explanation.py over the top-5 Kp hours, both modes.

**Results -- richer discovery:**
- bz_min -> kp ROBUST: lag 1, val -0.182 (matches the 3-var -0.181) even with the
  bigger set. Scorecard PASS.
- Secondary drivers (sw_speed_mean, density_mean, bt_max) do NOT clear the 0.1
  floor. Honest reasons: plasma 72% missing (effective ~24k rows), and
  conditioning on Bz absorbs their shared variance (fast streams co-occur with
  southward Bz) so their DIRECT effect is weaker. One real cross-link appeared:
  density_mean -> bt_max (0.151).

**Results -- first attribution (Gannon, May 11 2024, Kp 9):**
- The 5 highest-Kp hours in 10 years are all Gannon; each preceded (1 h) by
  EXTREME southward Bz, -35 to -59 nT.
- FULL model (R^2 0.877): persistence-dominated (kp lag-1 coef 0.89); bz_min only
  ~+1.7 vs persistence ~+7.8 -- misleading, since persistence is itself downstream
  of sustained forcing.
- DRIVER model (--exogenous, R^2 0.470): bz_min at lags 1-6, all coefficients
  negative; the Kp-9 attributed to sustained extreme southward Bz summed over 6 h
  (correct causal direction, persistence removed).

**Interpretation:**
- The engine attributes the era's biggest storm to its actual cause: sustained
  extreme southward IMF. Correct sign, timing, dominant driver.
- The driver model OVER-predicts (Kp ~15-16 vs observed 9): physically
  meaningful, because Kp SATURATES at 9 while the linear model extrapolates the
  extraordinary forcing past the ceiling -- the forcing far exceeded what is
  needed to max out the index.
- Caveats: full-model attribution is persistence-confounded; the linear model
  ignores Kp saturation/nonlinearity; speed/density are not Kp parents at the
  conservative floor.

**Implications / next:** First product capability (causal attribution of
geomagnetic events). Next: saturating/nonlinear coupling for the Kp ceiling;
plasma-era refinement for secondary drivers; then spacecraft-anomaly diagnosis
(needs spacecraft telemetry) and the REST API.

**Reproducibility:**
- discovery --vars bz_min,bt_max,sw_speed_mean,density_mean,kp
- explanation --top 5            (full model)
- explanation --top 5 --exogenous (driver model)
- 511 tests (10 explanation: structural fit, event attribution, both modes, guards).

---

## EXP-006: PCMCI recovers southward-Bz -> Kp causation from real data

**Date:** 2026-06-10
**Status:** Complete -- PASS (with documented caveat)

**Question:** On ten years of real DSCOVR data, does PCMCI recover the known
directed coupling (southward IMF -> geomagnetic activity) and reject the
physically impossible reverse?

**Setup:** python -m src.chronoscope.causal.discovery --root <corpus> over
[bz_min, bt_max, kp], tau_max=6, alpha=0.01, on the 84,960-row hourly matrix
(EXP-005). Runtime ~20 s.

**Results (raw, --min-strength 0):**
- Gating link FOUND: bz_min -> kp at lag 1, val = -0.181, p ~ 0 -- negative sign
  (southward field -> higher Kp), the strongest cross-variable edge. Confirmed
  at lag 2 (val -0.055). bt_max -> kp also found.
- 6 forbidden reverse edges appeared (kp -> bz_min, kp -> bt_max), all weak
  (|val| <= 0.074) but flagged significant -> strict scorecard FAIL.

**Results (effect-size floor, default --min-strength 0.1):**
- Graph collapses to 7 edges. The ONLY surviving cross-variable causal edge is
  bz_min -> kp (lag 1, val -0.181); the rest are autocorrelations. ALL reverse
  edges vanish. Scorecard PASS.

**Interpretation:**
- Core known physics recovered from real data: southward IMF causally drives
  geomagnetic activity, 1-hour lag, correct sign, dominant strength -- beating
  the -0.549 contemporaneous correlation with a directed, confounder-conditioned
  edge.
- The reverse edges are NOT causal. They are the textbook artifact of linear
  PCMCI on strongly-autocorrelated geophysical series at very large N: effective
  N is far below 85k, so naive p-values call |corr| ~ 0.01 "p = 1e-90". Their
  disappearance at a 0.1 floor -- while the true driver (0.181) survives -- IS
  the proof they were significance-inflated noise, likely also fed by
  unconditioned solar-cycle/rotation common-mode.
- The evaluation framework did its job: the strict scorecard CAUGHT the
  artifacts instead of rubber-stamping them, forcing the correct effect-size
  analysis rather than a false all-clean.

**Implications / next:** Phase 2 core achieved -- engine recovers known physics
from real data, failure modes understood. Next: condition out common-mode
(solar-cycle/rotation) and/or adopt PCMCI+ to remove reverse artifacts at the
source; extend to plasma-era variables (sw_speed_mean, density_mean) on the
2016-2019 subset; then causal explanation + the diagnosis application (Phase 3).

**Reproducibility:**
- python -m src.chronoscope.causal.discovery --root <corpus> --save  (floored, PASS)
- ... --min-strength 0  (raw, shows the artifacts and a strict FAIL)
- 501 tests (29 causal: graph, scorecard, synthetic PCMCI recovering a known
  lag-2 driver + rejecting the reverse + passing the scorecard).

---

## EXP-005: Labeled training matrix built; Bz-Kp coupling confirmed (correlational)

**Date:** 2026-06-10
**Status:** Complete -- PASS

**Question:** Does the assembled pipeline (corpus + Kp + ICME, resampled and
aligned) produce a sane Phase-2 input, and does it reproduce the known
southward-Bz / geomagnetic-activity coupling at the correlational level?

**Setup:** Built <root>/derived/hourly_features.parquet via build_hourly_features
(DEC-009) on the real corpus. Build time ~45 s.

**Results:**
- 84,960 hourly buckets, 2016-07-27 -> 2026-04-05 (UTC). Complete regular spine.
- MAG coverage 90.7% (77,073 buckets); ~9% NULL gaps = real data outages.
- Kp on 100% of buckets (ASOF fill over the full-span Kp labels).
- Plasma on 23,672 buckets (pre-2019 only) -- matches the H1_FC cutoff.
- 3,837 buckets (4.5%) inside ICME passages -- matches the 1-second-level
  fraction (12.4M / 271M = 4.6%); resampling did not distort the labels.
- corr(bz_min, kp) = -0.549 over the full corpus.

**Interpretation:**
- NEGATIVE (southward field <-> stronger activity: physics holds) and MODERATE,
  which is correct. A same-hour linear correlation between hourly-peak Bz and
  3-hourly Kp cannot approach -1: Kp also depends on solar wind speed, dynamic
  pressure, the DURATION of southward Bz, and magnetospheric preconditioning,
  plus a propagation/response lag the same-hour alignment does not capture.
  Published Bz-Kp/Dst studies land in 0.4-0.7; -0.549 is in band. ~-0.95 would
  suggest leakage; ~0 would mean something broke.
- This is the correlational FLOOR for Phase 2. PCMCI tests LAGGED conditional
  independence (Bz at t -> Kp at t+1,t+2) while conditioning out confounders, so
  the causal analysis should recover a cleaner directed signal. The number is a
  sanity check, explicitly NOT a causal result.

**Implications / next:** Corpus is Phase-2-ready. Next: the evaluation framework
(how causal-diagnosis accuracy is measured), then integrate PCMCI/Tigramite and
run causal discovery, with Bz -> geomagnetic activity as the known-physics gate.

**Reproducibility:**
- python -m src.chronoscope.corpus.training --root <corpus>
- 472 tests (8 training-matrix tests: spine regularity, ASOF Kp fill on gaps,
  interval in_icme + attributes, plasma nulls, negative Bz-Kp corr, guards).

---

## EXP-004: ICME interval labels validated; cross-layer agreement on storms

**Date:** 2026-06-06
**Status:** Complete -- PASS

**Question:** Does the Richardson-Cane ICME catalog parse faithfully, do its
events match known superstorms, and do the ICME labels agree with the
independently-sourced Kp labels?

**Setup:**
- Built src/chronoscope/labels/icme.py: fetch the R&C HTML table, parse the
  18-meaningful-column grid (live table renders 19 cols incl. a trailing empty
  one; LASCO at index 17), write labels/icme/richardson_cane.parquet as an
  INTERVAL table. CorpusReader extended with an `icme` view + interval-join.
- Parsed the live table (revised 2025-11-07) into 619 ICME intervals,
  1996-05-27 -> 2025-09-08. Structure: 233 magnetic clouds, 206 partial,
  180 ejecta. (636 raw rows minus repeated yearly headers.)

**Results:**
- The three most geoeffective ICMEs (by min Dst) are real, documented
  superstorms, correctly ranked:
    1. 2003-11-20: Dst -422 nT (November 2003 superstorm)
    2. 2024-05-10: Dst -406 nT, V_max 960 km/s (Gannon / Mother's Day storm)
    3. 2001-03-31: Dst -387 nT (March 2001 storm)
- Cross-layer check on Gannon (the worst storm within the 2016+ corpus span):
  the ICME labels (Dst -406, V_max 960, magnetic cloud) and the Kp labels
  (max Kp 9.0 = G5 during the same ICME interval) INDEPENDENTLY agree it was
  the era's most extreme storm, with consistent timing.
- 12,375,959 MAG rows (~4.6% of 271M) fall inside ICME passages -- the
  causal-ready "ICME-active" labeled telemetry subset.

**Interpretation:**
- ICME labels are faithful: they rank the catalog era's biggest storms
  correctly, and they agree with a fully independent data source (Kp) on the
  flagship event. When an ICME passes, the geomagnetic response spikes in the
  same window -- exactly the physical coupling the causal engine must recover.
- Honest scope note: the ICME catalog spans 1996-2025 but telemetry + Kp start
  2016 (DSCOVR era). The usable labeled overlap is 2016+. Pre-2016 ICME rows
  simply don't join to telemetry (the Nov 2003 worst-event has no Kp to join,
  which is why a naive "worst ICME" Kp cross-check returns null -- expected,
  not a bug).

**Implications / next:**
- The labeling trio (Kp + derived G-scale + ICME) is complete and
  cross-validated. The corpus is fully labeled for Phase 2 causal discovery.
- Perf note: the BETWEEN interval-join over 271M rows x 619 intervals took
  ~6.5 min (nested scan, no index). For the causal pipeline, materialize an
  `in_icme` flag once rather than recomputing. Tracked, not urgent.

**Reproducibility:**
- python -m src.chronoscope.labels.icme --root <corpus>
- Cross-check: worst ICME since 2016 joined to kp on the interval -> Kp 9 / G5.
- 464 tests (24 ICME parser + reader icme-view tests).

---

## EXP-003: Geomagnetic (Kp) labels validated against ground-truth storms

**Date:** 2026-06-06
**Status:** Complete -- PASS

**Question:** Do the Kp/ap labels fetched from GFZ independently agree with
known geomagnetic storms -- in particular the Sept 2017 G4 event the telemetry
already validated against (EXP-001)?

**Setup:**
- Built src/chronoscope/labels/geomagnetic.py: fetch Kp + ap from the GFZ JSON
  webservice, derive G-scale (rounding Kp to nearest level), write
  labels/geomagnetic/kp_ap.parquet. CorpusReader extended with a `kp` view.
- Fetched the full corpus span (2016-07-26 -> 2026-06-07), ~29k 3-hourly
  intervals, into E:\chronoscope_corpus\labels.

**Results:**
- Sept 7-8 2017 window: max Kp 8.333 (= 8+, Kp level 8) -> g_scale 4 (G4).
  Matches the published G4 classification AND the telemetry signatures from
  EXP-001 (Bz -34 nT, |B| 34.5 nT, speed 860 km/s) for the same days.
- Corpus-wide max Kp: 9.0 on 2024-05-10 -- the Gannon / Mother's Day superstorm,
  the most intense geomagnetic storm in ~20 years (G5, Kp pegged at maximum).
  Surfaced automatically as the single most intense interval in the decade with
  no hinting.

**Interpretation:**
- Two independent data sources (GFZ geomagnetic indices; CDAWeb L1 telemetry)
  agree the Sept 2017 days were G4. The labels are correctly time-aligned and
  the G-scale derivation (round, not floor) is right.
- The labels independently recover the largest documented storm of the era as
  their top value -- strong evidence the fetch/parse/derive pipeline is faithful.
- Because MAG telemetry runs to present (plasma stops 2019), the May 2024 G5 is
  also in the magnetometer corpus -- a future telemetry-vs-label cross-check.

**Implications / next:**
- Geomagnetic labels are trustworthy and joinable (ASOF join on interval start).
- Next label source: Richardson-Cane ICME catalog (interval labels, the
  solar-wind DRIVERS) -- completes the labeling needed for causal validation.

**Reproducibility:**
- python -m src.chronoscope.labels.geomagnetic --root <corpus> --start 2016-07-26 --end 2026-06-07
- Join check: ASOF LEFT JOIN kp ON telemetry.timestamp >= kp.timestamp
- 437 tests (28 new pin the labels + reader views).

---

## EXP-002: Whole-corpus data-quality validation + density cleanup

**Date:** 2026-06-06
**Status:** Complete -- PASS (after remediation)

**Question:** Is the ENTIRE corpus physically trustworthy, not just the one
storm spot-checked in EXP-001? (Precondition for causal work.)

**Setup:**
- Built src/chronoscope/corpus/validation.py: four checks over the full corpus
  -- coverage (per-day rows vs theoretical max), b_field_consistency (stored
  |B| vs sqrt(Bx2+By2+Bz2)), physical_ranges (values vs generous bounds), and
  optional precise cadence on one day. Console + JSON report, CI-style exit code.
- Ran against E:\chronoscope_corpus (271.4M MAG, 1.38M plasma).

**Initial results:**
- coverage PASS -- MAG avg 97.2% (2,711/3,233 days >=95%, 19 days <50%);
  plasma avg 90.6%. Sparse days are real gaps, reported not failed.
- b_field_consistency PASS -- across all 271M MAG rows, only 4,655 (0.0017%)
  exceed tolerance; max discrepancy 13.4 nT on a handful of rows. The DEC-005
  column mapping holds corpus-wide. (Confirms EXP-001's 4-dp agreement at scale.)
- cadence (2017-09-07) PASS -- MAG median 1s / 100% at cadence; plasma median 60s.
- physical_ranges FAIL -- 647 plasma proton_density rows out of bounds.

**Diagnosis of the failure:** 636 negative densities (min -82.3 cm^-3) and 11
absurd highs (max 3.57e10 cm^-3). All non-fill (not -1e31) and all DQF=0, so
they bypassed the fill and DQF gates -- genuine source-level corruption.
Clustered on storm-active days (late May 2018, Oct 2018, May 2019). MAG clean.

**Fix (see DEC-007):** Added a physical-plausibility gate to the storage layer
(third tier alongside fill/DQF) and remediated the existing corpus in place with
scripts/clean_corpus_plausibility.py -- 647 rows removed across 83 files, no
re-fetch. Density valid range [0, 200] cm^-3.

**Result after fix:** Re-ran validation. ALL CHECKS PASSED. Plasma rows
1,382,215 -> 1,381,568. physical_ranges: all quantities within bounds.

**Interpretation:** The corpus is now trustworthy both on disk and
by-construction. The validation layer earned its keep on day one -- it found
647 corrupt, flagged-good, non-fill rows hiding in 1.38M that the EXP-001 spot
check could never have surfaced, and which would have quietly poisoned any
correlation/causal analysis (a single 3.5e10 spike wrecks a covariance).

**Implications / next:**
- Phase 1 data-quality story is closed. Corpus ready for event labeling + PCMCI.
- 409 tests (7 new pin the plausibility gate).

**Reproducibility:**
- python src/chronoscope/corpus/validation.py --root <corpus> --report corpus_validation.json
- python scripts/clean_corpus_plausibility.py --root <corpus> [--dry-run]
- Report artifact: corpus_validation.json (local, not committed).

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
