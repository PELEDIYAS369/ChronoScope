# ChronoScope

![Tests](https://img.shields.io/badge/tests-334%20passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Data](https://img.shields.io/badge/data-live%20NOAA%20DSCOVR-orange)
![License](https://img.shields.io/badge/license-proprietary-red)

**Telemetry replay, audit, and anomaly detection for mission-critical operations.**

Built for space ground operations. Designed to extend to aviation, maritime, and industrial systems.

---

## The Problem

When something goes wrong in a complex mission, operations teams spend
**days to weeks** manually reconstructing what happened — digging through
CSV exports, scattered logs, shift notes, and emails.

Investigation is slow because the tools are fragmented:

| Today's Workflow | With ChronoScope |
|---|---|
| Manual CSV reconstruction | Deterministic session replay |
| Scattered, mutable logs | Cryptographic audit chain |
| Threshold alarms only | Statistical anomaly detection with explainable output |
| 4–5 disconnected tools | Single integrated platform |
| 7–14 days to investigate | Hours to investigate |

---

## What It Does

ChronoScope is a platform that lets operations teams:

1. **Replay** any past mission moment with deterministic fidelity — same input always produces identical output, verified via SHA-256 session fingerprinting
2. **Audit** every operator action through a tamper-evident cryptographic chain — modifying or deleting any entry breaks the chain and is mathematically detectable
3. **Detect** anomalies using statistical analysis (z-score deviation, pattern matching, temporal correlation) with mandatory human-readable explanations for every flag
4. **Report** investigation summaries in structured JSON and Markdown

---

## Live Validation

Running on real NOAA DSCOVR spacecraft telemetry (L1 Lagrange point, 1.5 million km from Earth):

```
✓  Connected to NOAA DSCOVR — live solar wind data
✓  Ingested 223 real telemetry packets (plasma + magnetic field)
✓  Loaded session into deterministic replay engine
✓  Seeked to arbitrary timeline points (0.30 ms per seek)
✓  Verified determinism via SHA-256 session fingerprint
✓  Ran anomaly detection — flagged real deviation
✓  Logged 10 audit entries with SHA-256 chain
✓  Generated JSON and Markdown investigation report
```

Example anomaly detected during live demo:

```
[MEDIUM] ion_temperature_k
  Observed:   562,201 K
  Expected:   < 500,000 K (baseline threshold)
  Deviation:  12.4% above threshold — 2.4σ
  Confidence: 86%
  Pattern:    Matches high-speed solar wind stream signature
  Action:     Log HSS event and monitor (89% historical success rate)
```

---

## Architecture

```
NOAA / NASA APIs (public, no API key required)
  │
  ▼
Ingestion Layer ──────── Pluggable adapters per data source
  │
  ▼
Domain Models ────────── Immutable, validated telemetry packets (frozen dataclasses)
  │
  ▼
Replay Engine ────────── Deterministic playback, SHA-256 fingerprinted sessions
  │
  ▼
Anomaly Detector ─────── Z-score analysis, pattern matching, temporal correlation
  │                       Every flag carries a mandatory human-readable explanation
  ▼
Audit Log ────────────── Tamper-evident SHA-256 cryptographic chain
  │
  ▼
Reporter ─────────────── Structured JSON + Markdown investigation reports
  │
  ▼
CLI + REST API ───────── Unified operator interface
```

---

## Design Principles

**Immutability.** Every telemetry packet is a frozen dataclass. No packet can be altered after ingestion.

**Determinism.** Same session + same timestamp = identical replay output. Guaranteed via SHA-256 session fingerprinting.

**Explainability.** Every anomaly flag carries a mandatory human-readable reason, observed vs. expected values, confidence score, and suggested action with historical success rate. No black-box outputs.

**Tamper evidence.** Every audit entry is cryptographically chained to the previous entry. Breaking any entry breaks the entire chain. Tampering is mathematically detectable.

---

## Test Coverage

```
334 tests passing · 2.97 seconds · 0 failures

tests/unit/           — All core modules (models, replay, audit, detection, SDK)
tests/integration/    — End-to-end API and reporter pipelines
tests/benchmarks/     — Performance at scale (seek latency, throughput)
```

Replay seek performance: **0.30 ms per seek** on 10,000 packets.

---

## Project Structure

```
src/chronoscope/
├── domain/          Models, enums, exceptions, constants
├── ingestion/       Data source adapters (NOAA DSCOVR, CelesTrak, ACE, OpenSky)
├── replay/          Deterministic replay engine + cursor
├── ai/              Anomaly detection (statistical + pattern matching)
├── audit/           Tamper-evident cryptographic audit log
├── api/             FastAPI REST endpoints + security
├── dashboard/       Health snapshot and status
├── reporting/       Hourly report generation
├── observability/   Structured event logging
├── sdk/             Client SDK, webhook support
├── cli.py           Command-line interface
├── controller.py    Orchestration layer
└── reporter.py      JSON + Markdown report generation

tests/               334 automated tests (unit + integration + benchmarks)
scripts/             Demo scripts running on live NASA data
docs/                Architecture documentation
```

---

## Data Sources

| Source | Type | Status |
|---|---|---|
| NOAA DSCOVR | Solar wind plasma + magnetic field | ✅ Live |
| NOAA ACE | Solar wind backup source | ✅ Ready |
| CelesTrak | Satellite TLE orbital data | ✅ Ready |
| OpenSky Network | Aircraft ADS-B telemetry | ✅ Ready |
| Any CCSDS source | Standard space packet format | ✅ Adapter ready |

---

## Quick Start

```bash
# Clone and set up
git clone https://github.com/PELEDIYAS369/ChronoScope.git
cd ChronoScope
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt

# Run live demo on real NASA data
python scripts/sale_demo.py

# Run all tests
pytest tests/ -v

# CLI
python -m src.chronoscope.cli status
python -m src.chronoscope.cli ingest --spacecraft DSCOVR --hours 2
python -m src.chronoscope.cli audit
```

---

## Deployment

Runs fully on-premise. No cloud dependency. No API keys required for NOAA DSCOVR data. Standard Python 3.13 environment.

Designed to integrate alongside existing ground operations tools (COSMOS, OpenMCT, YAMCS).

---

## License

Proprietary — © 2026 Utsav Sojitra. All rights reserved.
See [LICENSE](LICENSE) for details.

---

## Contact

chronoscope.ai@gmail.com

Built in Toronto, Canada.
