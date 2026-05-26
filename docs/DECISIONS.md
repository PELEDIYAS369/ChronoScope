# ChronoScope — Decision Log

A record of significant architectural, technical, and strategic decisions and **why** we made them. Each entry includes alternatives considered and reasoning. This prevents us from re-litigating the same questions in future sessions.

Format: most recent decisions at the top.

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
