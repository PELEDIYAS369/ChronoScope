# ChronoScope AI — Technical Architecture

**Version:** 1.0.0  
**Status:** Production-ready prototype  
**Data:** Tested on real NASA/NOAA DSCOVR spacecraft telemetry

---
# ChronoScope AI — System Architecture

**Version:** 1.0.0  
**Tests:** 246 passing  
**Data Sources:** DSCOVR, ACE, OpenSky, CelesTrak  
**Dashboard:** http://localhost:8000/dashboard  
**World Map:** http://localhost:8000/map  
**API Docs:** http://localhost:8000/docs  

---

## What ChronoScope AI Does

ChronoScope AI is a universal telemetry replay, audit, and anomaly detection platform. It ingests real sensor data from any complex system, enables deterministic replay of any past moment, maintains a tamper-evident audit trail, and uses explainable AI to flag anomalies with ranked suggested actions and historical success rates.

**Primary market:** Space mission ground operations  
**Expansion markets:** Aviation, maritime, industrial, defense

---

## Core Capabilities

| Capability | Description | Proof |
|---|---|---|
| Deterministic replay | Same input always produces identical output | SHA-256 session fingerprint verified |
| Tamper-evident audit | Cryptographic hash chain — any modification detected | AuditChainBrokenError on any tampering |
| Explainable AI | Every anomaly flag carries mandatory human-readable reason | ExplainabilityError if reason missing |
| Suggested actions | Ranked actions with historical success rates per event type | Operator decision + outcome recorded |
| Real data support | Tested on live NASA DSCOVR solar wind telemetry | 684 real packets ingested in 0.4s |
| REST API | Full authenticated web service with rate limiting | FastAPI + Pydantic, Swagger docs at /docs |

---

## System Architecture
External Data Sources
│
▼
┌─────────────────────────────────────────────────┐
│  Ingestion Layer                                 │
│  BaseIngester (abstract)                         │
│    ├── NOAADscovrIngester  (live, operational)  │
│    ├── [Aviation adapter]  (planned)            │
│    └── Industrial adapter            │
└──────────────────┬──────────────────────────────┘
│ TelemetryPacket (immutable)
▼
┌─────────────────────────────────────────────────┐
│  Domain Layer                                    │
│  TelemetryPacket  — immutable, validated         │
│  MissionSession   — packet container             │
│  AnomalyFlag      — detection result             │
│  MissionEvent     — discrete event record        │
└──────────────────┬──────────────────────────────┘
│
┌─────────┼──────────┐
▼         ▼          ▼
┌──────────────┐ ┌──────────┐ ┌─────────────────┐
│ Replay Engine│ │ Audit Log│ │ AI Detector     │
│              │ │          │ │                 │
│ Deterministic│ │ Hash     │ │ DetectionRule   │
│ cursor       │ │ chain    │ │ per parameter   │
│ Seek O(logN) │ │ SHA-256  │ │                 │
│ Stream       │ │ Export   │ │ AnomalyReport   │
│ Verify       │ │ Verify   │ │ SuggestedAction │
└──────────────┘ └──────────┘ │ Success rates   │
└─────────────────┘
│
▼
┌─────────────────────────┐
│  ChronoScopeController  │
│  Single entry point     │
│  All ops audit-logged   │
└────────────┬────────────┘
│
▼
┌─────────────────────────┐
│  FastAPI REST Service   │
│  API key auth           │
│  Rate limiting 60/min   │
│  Swagger docs at /docs  │
└─────────────────────────┘
---

## Key Design Decisions

### Immutability for Determinism
`TelemetryPacket` is `frozen=True` — it cannot be modified after creation. This is the foundation of deterministic replay. If packets cannot change, the same sequence always produces the same output.

### Hash Chain for Audit Integrity
The audit log uses the same principle as blockchain: each entry hashes all its content plus the previous entry's hash. Any modification to any entry breaks the chain. This is mathematically provable, not just a policy.

### Mandatory Explainability
`AnomalyFlag.reason` cannot be empty. `AnomalyReport.what_happened` and `why_it_matters` are required fields. The system raises `ExplainabilityError` if AI output cannot be explained. This is enforced at the architecture level, not just by convention.

### Pluggable Ingesters
`BaseIngester` defines the interface. Any data source — spacecraft, aircraft, ship, factory — implements the same three methods. The rest of the system doesn't change.

---

## Performance Benchmarks

Measured on Windows 11, Python 3.13, standard hardware:

| Operation | Scale | Performance |
|---|---|---|
| Packet creation | 10,000 packets | < 2 seconds |
| Replay engine load | 10,000 packets | < 500ms |
| Seek (binary search) | 10,000 packets | < 5ms per seek |
| AI detection | 1,000 packets | < 500ms |
| Audit chain verify | 1,000 entries | < 100ms |
| Live data ingestion | DSCOVR 6-hour window | 684 packets in 0.4s |

---

## Data Sources

**Current (operational):**
- NOAA DSCOVR solar wind plasma (proton density, bulk speed, ion temperature)
- NOAA DSCOVR magnetic field (Bx, By, Bz GSM, Bt)
- Public, no API key required, updated every minute

**Planned adapters:**
- NASA CDAWeb (multi-mission space physics)
- ESA ESAC public archives
- CCSDS binary telemetry files
- Aviation ARINC 429 format
- Maritime NMEA 0183 format

---

## Anomaly Detection Rules (DSCOVR)

| Rule | Parameter | Threshold | Severity | Urgency |
|---|---|---|---|---|
| Solar wind speed high | bulk_speed_km_s | > 600 km/s | HIGH | 2 hours |
| Proton density high | proton_density_n_cc | > 15 p/cc | MEDIUM | 4 hours |
| Bz strongly southward | bz_gsm_nt | < -20 nT | CRITICAL | 30 min |
| Ion temperature extreme | ion_temperature_k | > 500,000 K | MEDIUM | 8 hours |

All thresholds based on NOAA Space Weather Prediction Center operational values.

---

## API Reference

Base URL: `http://localhost:8000/api/v1`  
Auth: `X-API-Key` header required (except `/health`)  
Demo key: `chronoscope-demo-key-2026`

| Method | Endpoint | Description |
|---|---|---|
| GET | /health | Health check |
| GET | /status | System status |
| POST | /sessions | Create session |
| GET | /sessions | List sessions |
| GET | /sessions/{id} | Get session |
| POST | /sessions/{id}/ingest | Ingest telemetry |
| POST | /sessions/{id}/replay/load | Load replay |
| POST | /sessions/{id}/replay/play | Play |
| POST | /sessions/{id}/replay/pause | Pause |
| POST | /sessions/{id}/replay/seek | Seek to time |
| POST | /sessions/{id}/replay/step-forward | Step +1 |
| POST | /sessions/{id}/replay/step-back | Step -1 |
| POST | /sessions/{id}/replay/speed | Set speed |
| GET | /sessions/{id}/replay/cursor | Cursor state |
| POST | /sessions/{id}/replay/verify | Verify determinism |
| POST | /sessions/{id}/analyze | Run AI analysis |
| GET | /anomalies | List anomaly reports |
| POST | /anomalies/decide | Record operator decision |
| POST | /anomalies/outcome | Record outcome |
| GET | /audit/summary | Audit summary |
| GET | /audit/export | Full audit export |
| POST | /audit/verify | Verify chain |

Full interactive docs: `http://localhost:8000/docs`

---

## Test Coverage

| Module | Tests | Status |
|---|---|---|
| Domain models | 12 | ✅ All passing |
| Ingestion layer | 15 | ✅ All passing |
| Replay engine | 24 | ✅ All passing |
| Audit log | 20 | ✅ All passing |
| AI detector | 25 | ✅ All passing |
| API integration | 16 | ✅ All passing |
| Performance benchmarks | 10 | ✅ All passing |
| **Total** | **122** | **✅ 0 failures** |

---

## IP Statement

All code in this repository is original work. No proprietary libraries or licensed third-party code. Dependencies are all open-source with permissive licenses (MIT, Apache 2.0, BSD).

**Owner:** ChronoScope AI Inc.  
**Jurisdiction:** Ontario, Canada  
**License:** Proprietary — All rights reserved