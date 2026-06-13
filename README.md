# ChronoScope

![Tests](https://img.shields.io/badge/tests-511%20passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Data](https://img.shields.io/badge/data-10yr%20DSCOVR%20corpus-orange)
![License](https://img.shields.io/badge/license-proprietary-red)

**Causal root-cause analysis for space-weather telemetry — built on ten years of real spacecraft data.**

Built for space ground operations. Designed to extend to aviation, maritime, and industrial systems.

---

## The Problem

When something goes wrong in a complex mission, operations teams spend a long
time manually reconstructing what happened — digging through CSV exports,
scattered logs, shift notes, and emails — and even then, separating *cause* from
*coincidence* is hard. Was the anomaly driven by the space environment, or by
the hardware? Tools today flag *that* something deviated; they rarely explain
*why*.

ChronoScope is built to answer the "why": to attribute an event to its causal
driver, grounded in real data and validated against known physics.

---

## What's Built Today

Two layers, both running on real data. This section describes what exists and is
tested — not a roadmap.

### 1. Causal diagnosis engine — validated on ten years of real data

This is the core of the project and its novel contribution.

- **Ten-year DSCOVR corpus.** 271.4M magnetometer rows + 1.38M solar-wind plasma
  rows, 2016-07-27 → 2026-06-05 (3,601 days, zero failed days), stored as
  partitioned Parquet and queried via DuckDB. Validated against ground truth:
  the September 2017 G4 storm reads back at min Bz −33.99 nT, max |B| 34.52 nT,
  matching the published DSCOVR record.
- **Three cross-validated label layers.** Geomagnetic Kp and a derived G-scale
  (GFZ Potsdam), and 619 Richardson-Cane ICME intervals. The layers agree
  independently: the May 2024 Gannon superstorm shows up as Kp 9 / G5 *and* as
  the catalog's most geoeffective ICME (Dst −406) in the same window.
- **Causal discovery (PCMCI / Tigramite).** Run on the corpus, the engine
  recovers the textbook coupling — **southward interplanetary field drives
  geomagnetic activity** (bz → Kp, lag 1 hour, correct negative sign, the
  dominant link) — and a known-physics scorecard *rejects* physically-impossible
  reverse causation (the magnetosphere cannot drive the upstream solar wind).
- **Causal attribution.** The engine explains an event by its driver. Example:
  the May 2024 Gannon superstorm (Kp 9, the strongest in the corpus) is
  attributed to **sustained extreme southward IMF — Bz down to −59 nT** over the
  preceding hours, quantified per lag.

### 2. Operational platform — replay · audit · anomaly flags

- **Replay** any past mission moment with deterministic fidelity — same input
  always produces identical output, verified via SHA-256 session fingerprinting.
- **Audit** every operator action through a tamper-evident cryptographic chain —
  modifying or deleting any entry breaks the chain and is detectable.
- **Detect** anomalies with a rule-based engine (configurable thresholds,
  z-score deviation) where every flag carries a mandatory human-readable
  explanation and a suggested action. *(This layer is rule-based, not a trained
  model; its confidence weights are hand-authored, not empirical success rates.)*
- **Report** investigation summaries in structured JSON and Markdown.

---

## Validation

Real, reproducible results — every number below comes from running the code on
the real corpus, not from estimates.

**Corpus fidelity (EXP-001):** the Sept 7–8 2017 G4 storm reconstructs at
min Bz_GSE −33.99 nT, max |B| 34.52 nT, max speed 860 km/s; a quiet day reads
max |B| 11 nT.

**Label cross-validation (EXP-003/004):** the corpus-wide Kp maximum of 9.0
lands on the May 2024 Gannon storm, independently confirmed by the ICME catalog
(Dst −406). ~12.4M magnetometer rows fall inside catalogued ICME passages.

**Causal discovery (EXP-006):** PCMCI recovers bz → Kp at lag 1 (correct
negative sign), the strongest cross-variable link, passing a known-physics
scorecard with zero reverse-causation violations at a meaningful effect-size
threshold. The simple correlation floor it improves on is r = −0.549.

**Causal attribution (EXP-007):** the five highest-Kp hours in ten years are all
the Gannon storm; the driver-attribution model assigns them to sustained
southward Bz across the preceding six hours.

```
python -m src.chronoscope.causal.discovery   --root <corpus>            # discover the causal graph
python -m src.chronoscope.causal.explanation  --root <corpus> --exogenous # attribute the top events
```

---

## Honest Scope

What's **validated**: the causal engine recovers known space-weather physics
(solar-wind drivers → geomagnetic activity) from ten years of real data, and is
graded against a falsifiable known-physics scorecard.

What's **next / not yet built**:

- **Spacecraft-anomaly diagnosis.** Today the engine attributes *geomagnetic*
  events to *solar-wind* drivers. Attributing a *specific spacecraft's* anomaly
  requires that spacecraft's housekeeping telemetry — available through a pilot
  partnership, not yet in hand.
- **Nonlinear coupling.** The current attribution is linear; on extreme storms
  it over-predicts past the Kp ceiling (Kp saturates at 9). A saturating model
  is planned.
- **REST API for causal queries** and tighter coupling of the causal engine into
  the audit chain.

---

## Architecture

```
NOAA / NASA APIs (public, no API key required)
  │
  ▼
Ingestion Layer ──────── Pluggable adapters (DSCOVR, ACE, CelesTrak, OpenSky)
  │
  ▼
Corpus (Parquet + DuckDB) ─ 10yr partitioned telemetry + cross-validated labels
  │
  ▼
Causal Engine ────────── PCMCI discovery → CausalGraph → known-physics scorecard
  │                       → structural attribution of events to drivers
  ▼
Replay · Audit · Anomaly · Reporter ─ deterministic replay, SHA-256 audit chain,
  │                                    rule-based flags, JSON/Markdown reports
  ▼
CLI + REST API ───────── Unified operator interface
```

---

## Design Principles

**Immutability.** Every telemetry packet is a frozen dataclass; no packet can be
altered after ingestion.

**Determinism.** Same session + same timestamp = identical replay output,
guaranteed via SHA-256 session fingerprinting.

**Falsifiability.** Causal output is graded against a known-physics scorecard
that defines links the engine *must* find and reverse-causation links it must
*never* invent. Results are reported with effect sizes and honest caveats, not
as black-box scores.

**Tamper evidence.** Every audit entry is cryptographically chained to the
previous one; breaking any entry breaks the chain.

---

## Test Coverage

```
511 tests passing · 0 failures

tests/unit/           — core modules (models, replay, audit, detection, SDK,
                        corpus storage, labels, causal graph/discovery/
                        evaluation/explanation)
tests/integration/    — end-to-end API and reporter pipelines
tests/benchmarks/     — performance at scale (seek latency, throughput)
```

Replay seek performance: ~0.30 ms per seek on 10,000 packets.

---

## Project Structure

```
src/chronoscope/
├── domain/          Models, enums, exceptions, constants
├── ingestion/       Data source adapters (NOAA DSCOVR, CelesTrak, ACE, OpenSky)
├── corpus/          Parquet writer + DuckDB reader, validation, training matrix
├── labels/          Geomagnetic (Kp/G-scale) and Richardson-Cane ICME labels
├── causal/          CausalGraph, PCMCI discovery, known-physics scorecard,
│                    structural attribution / explanation
├── replay/          Deterministic replay engine + cursor
├── ai/              Rule-based anomaly detection (statistical + pattern)
├── audit/           Tamper-evident cryptographic audit log
├── api/             FastAPI REST endpoints + security
├── dashboard/       Health snapshot and status
├── reporting/       Report generation
├── observability/   Structured event logging
├── sdk/             Client SDK, webhook support
├── cli.py           Command-line interface
├── controller.py    Orchestration layer
└── reporter.py      JSON + Markdown report generation

scripts/             Corpus backfill + demo scripts (run on real data)
docs/                STATUS / DECISIONS / EXPERIMENTS — full build + decision log
tests/               511 automated tests (unit + integration + benchmarks)
```

The full engineering log — every decision and every validation experiment — is
in `docs/DECISIONS.md` and `docs/EXPERIMENTS.md`.

---

## Data Sources

| Source | Type | Status |
|---|---|---|
| NOAA DSCOVR | Solar wind plasma + magnetic field | ✅ Live + 10yr archive |
| GFZ Potsdam | Kp / ap geomagnetic indices | ✅ Labels |
| Richardson & Cane | ICME interval catalog | ✅ Labels |
| NOAA ACE | Solar wind backup source | ✅ Ready |
| CelesTrak | Satellite TLE orbital data | ✅ Ready |
| OpenSky Network | Aircraft ADS-B telemetry | ✅ Ready |

---

## Quick Start

```bash
git clone https://github.com/PELEDIYAS369/ChronoScope.git
cd ChronoScope
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Run all tests
pytest tests/ -q

# Build the corpus (one-time; resumable; ~17 GB of Parquet on disk)
python scripts/build_dscovr_corpus.py --root <corpus_path>

# Discover the causal graph and attribute the strongest events
python -m src.chronoscope.causal.discovery   --root <corpus_path> --save
python -m src.chronoscope.causal.explanation  --root <corpus_path> --top 5 --exogenous

# Operational CLI
python -m src.chronoscope.cli status
python -m src.chronoscope.cli ingest --spacecraft DSCOVR --hours 2
python -m src.chronoscope.cli audit
```

---

## Deployment

Runs fully on-premise. No cloud dependency. No API keys required for NOAA DSCOVR
data. Standard Python 3.13 environment. Designed to integrate alongside existing
ground operations tools (COSMOS, OpenMCT, YAMCS).

---

## License

Proprietary — © 2026 Utsav Sojitra. All rights reserved.
See [LICENSE](LICENSE) for details.

---

## Contact

chronoscope.ai@gmail.com

Built in Toronto, Canada.
