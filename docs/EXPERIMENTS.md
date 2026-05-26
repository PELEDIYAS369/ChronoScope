# ChronoScope — ML Experiments Log

A research notebook for every ML experiment we run: what we tried, what data,
what we found, what it means.

This is the equivalent of a researcher's lab notebook. Without it, we will
repeat failed experiments and forget hard-won insights.

Format: most recent experiments at the top. Number them sequentially
(EXP-001, EXP-002, ...).

---

## Status: No experiments yet (ingestion phase)

We are still in the foundation-building phase. No model has trained, no causal
discovery has run. The first experiments will begin once the historical DSCOVR
corpus is persisted to Parquet (see STATUS.md Phase 1 and DEC-004).

**Pre-experiment recon performed this session (2026-05-25):**

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
