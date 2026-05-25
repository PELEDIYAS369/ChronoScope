# ChronoScope — Technical Architecture

**Version:** 1.0  
**Tests:** 334 passing  
**Data Sources:** NOAA DSCOVR, ACE, CelesTrak, OpenSky Network

---

## Overview

ChronoScope ingests real-time telemetry from mission-critical systems, enables deterministic replay of any past moment, maintains a tamper-evident audit trail, and flags anomalies with explainable output.

---

## System Layers

```
External Data Sources (NOAA, CelesTrak, OpenSky)
  │
  ▼
┌──────────────────────────────────────────────────────┐
│  Ingestion Layer                                     │
│  BaseIngester (abstract)                             │
│    ├── NOAADscovrIngester   — solar wind plasma/mag  │
│    ├── ACEIngester          — solar wind backup      │
│    ├── CelesTrakIngester    — satellite TLE data     │
│    └── OpenSkyIngester      — aircraft ADS-B         │
└───────────────────┬──────────────────────────────────┘
                    │ TelemetryPacket (frozen dataclass)
                    ▼
┌──────────────────────────────────────────────────────┐
│  Domain Layer                                        │
│  TelemetryPacket   — immutable, validated per CCSDS  │
│  MissionSession    — bounded packet container        │
│  MissionEvent      — discrete event record           │
│  AnomalyFlag       — detection result with reason    │
│  SourceProvenance  — data lineage and trust level    │
└───────────────────┬──────────────────────────────────┘
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
┌──────────────┐ ┌───────────────┐ ┌──────────────┐
│ Replay       │ │ Anomaly       │ │ Audit Log    │
│ Engine       │ │ Detector      │ │              │
│              │ │               │ │ SHA-256      │
│ Deterministic│ │ Z-score +     │ │ chained      │
│ playback     │ │ pattern match │ │ entries      │
│ SHA-256      │ │ + temporal    │ │              │
│ fingerprint  │ │ correlation   │ │ Tamper =     │
│              │ │               │ │ chain break  │
└──────┬───────┘ └───────┬───────┘ └──────┬───────┘
       │                 │                │
       └─────────┬───────┘                │
                 ▼                        │
┌──────────────────────────────────────────┐
│  Controller (orchestration)              │
│  Coordinates replay, detection, audit    │
└───────────────────┬──────────────────────┘
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
┌──────────────┐ ┌──────────┐ ┌──────────────┐
│ REST API     │ │ CLI      │ │ Reporter     │
│ FastAPI      │ │ Click    │ │ JSON + MD    │
│ /docs        │ │          │ │              │
└──────────────┘ └──────────┘ └──────────────┘
```

---

## Key Implementation Details

### Immutability

All telemetry packets use `@dataclass(frozen=True)`. Once created, no field can be modified. This is enforced by Python's dataclass machinery at runtime — any mutation attempt raises `FrozenInstanceError`.

### Deterministic Replay

The replay engine sorts packets chronologically on load, then computes a SHA-256 fingerprint over the entire session (packet IDs, timestamps, spacecraft IDs, raw bytes). Seeking uses binary search — O(log n). The `verify_determinism()` method recomputes the fingerprint and compares against the original. Any discrepancy raises `DeterminismViolationError`.

### Cryptographic Audit Chain

Each `AuditEntry` contains a `previous_hash` field linking it to the prior entry. The hash is computed over the entry's content plus the previous hash, creating a chain where modifying any entry invalidates all subsequent hashes. Verification walks the chain and checks every link.

### Anomaly Detection

The detector uses three methods:
1. **Z-score analysis** — flags parameters deviating beyond configured sigma thresholds
2. **Pattern matching** — compares current telemetry signatures against a library of known event patterns (e.g., high-speed solar wind streams)
3. **Temporal correlation** — detects cascade effects across correlated parameters

Every anomaly flag must include a human-readable `reason` field. The domain model's `__post_init__` raises `ValueError` if the reason is empty.

### Data Source Abstraction

All ingesters inherit from `BaseIngester` (abstract). Adding a new data source requires implementing `ingest()` and `is_available()`. The domain layer is source-agnostic — it only sees validated `TelemetryPacket` objects.

---

## Performance

| Metric | Value | Conditions |
|---|---|---|
| Replay seek latency | 0.30 ms | 10,000 packets, binary search |
| Test suite runtime | 2.97 s | 334 tests, all passing |
| Memory footprint | < 500 MB | 10,000-packet session |

---

## Dependencies

Core: Python 3.13, FastAPI, Pydantic, structlog, cryptography, httpx, click  
Data: numpy, pandas, scipy  
Testing: pytest, pytest-cov, coverage
