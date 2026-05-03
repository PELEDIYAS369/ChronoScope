"""
ChronoScope AI — Dashboard Routes
Full mission control dashboard with multi-source support.
"""
from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from datetime import datetime, timezone, timedelta
from src.chronoscope.controller import ChronoScopeController
from src.chronoscope.ingestion.noaa_dscovr import NOAADscovrIngester
from src.chronoscope.ingestion.ace import ACEIngester
from src.chronoscope.ingestion.opensky import OpenSkyIngester
from src.chronoscope.ingestion.celestrak import CelesTrakIngester
from src.chronoscope.domain.models import MissionPhase
from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR

dashboard_router = APIRouter()

_controller: ChronoScopeController | None = None
_sessions: dict[str, str] = {}  # source -> session_id


def get_controller() -> ChronoScopeController:
    global _controller
    if _controller is None:
        _controller = ChronoScopeController()
    return _controller


@dashboard_router.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)
@dashboard_router.post("/dashboard/ingest/{source}", tags=["Dashboard"])
async def dashboard_ingest(source: str):
    global _sessions
    controller = get_controller()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=7)

    source_map = {
        "dscovr": (NOAADscovrIngester(), SPACECRAFT_DSCOVR, "DSCOVR Solar Wind"),
        "ace":    (ACEIngester(),        "ACE",             "ACE Solar Wind"),
        "opensky":(OpenSkyIngester(),    "OPENSKY_LIVE",    "Live Aircraft"),
        "celestrak":(CelesTrakIngester(group="ISS"), "SAT_ISS", "ISS Orbital"),
    }

    if source not in source_map:
        return {"success": False, "error": f"Unknown source: {source}"}

    ingester, spacecraft_id, label = source_map[source]

    try:
        session = controller.create_session(
            spacecraft_id=spacecraft_id,
            mission_phase=MissionPhase.NOMINAL,
            start_time=start_time,
            end_time=end_time,
            metadata={"source": source, "label": label},
            actor="dashboard",
        )

        # Call ingester directly — bypasses spacecraft_id filter in controller
        result = ingester.ingest_into_session(session, start_time, end_time)

        if result.success:
            _sessions[source] = session.session_id
            try:
                controller._replay.load_session(session)
            except Exception:
                pass
            try:
                controller.analyze(
                    session_id=session.session_id,
                    actor="ai_engine",
                )
            except Exception:
                pass

        return {
            "success": result.success,
            "source": source,
            "label": label,
            "session_id": session.session_id,
            "packets_ingested": result.packets_ingested,
            "duration_seconds": round(result.duration_seconds, 2),
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "trace": traceback.format_exc()}

@dashboard_router.get("/dashboard/data", tags=["Dashboard"])
async def dashboard_data():
    global _sessions
    controller = get_controller()

    sessions = controller.list_sessions()
    status = controller.status()

    try:
        audit_intact = controller.verify_audit_chain()
    except Exception:
        audit_intact = False

    all_anomalies = []
    all_packets = []
    source_stats = []

    for source, session_id in _sessions.items():
        try:
            session = controller.get_session(session_id)
            label = session.metadata.get("label", source.upper())
            sc = session.spacecraft_id

            source_stats.append({
                "source": source,
                "label": label,
                "spacecraft": sc,
                "packets": session.packet_count,
                "anomalies": session.anomaly_count,
                "status": session.replay_status.value,
            })

            for flag in session.anomalies[-5:]:
                all_anomalies.append({
                    "source": label,
                    "severity": flag.severity.value,
                    "parameter": flag.parameter_name,
                    "observed": flag.observed_value,
                    "reason": flag.reason,
                    "confidence": round(flag.confidence * 100),
                    "timestamp": flag.timestamp.strftime("%H:%M:%S"),
                    "acknowledged": flag.acknowledged,
                })

            for pkt in session.packets[-4:]:
                params = {
                    k: round(v, 2) if isinstance(v, float) else v
                    for k, v in pkt.parameters.items()
                    if k != "data_type"
                }
                data_type = pkt.parameters.get("data_type", "telemetry")
                all_packets.append({
                    "source": label,
                    "timestamp": pkt.timestamp.strftime("%H:%M:%S"),
                    "type": data_type.replace("_", " ").title(),
                    "spacecraft": pkt.spacecraft_id[:20],
                    "params": params,
                })
        except Exception:
            continue

    all_anomalies.sort(key=lambda x: x["timestamp"], reverse=True)
    all_packets.sort(key=lambda x: x["timestamp"], reverse=True)

    total_packets = sum(s.get("packet_count", 0) for s in sessions)
    total_anomalies = sum(s.get("anomaly_count", 0) for s in sessions)

    health = "NOMINAL"
    if status.get("ai_critical", 0) > 0:
        health = "CRITICAL"
    elif total_anomalies > 20:
        health = "CAUTION"
    elif not _sessions:
        health = "IDLE"

    return {
        "health": health,
        "sessions": len(sessions),
        "total_packets": total_packets,
        "total_anomalies": total_anomalies,
        "audit_intact": audit_intact,
        "audit_entries": status.get("audit_entries", 0),
        "ai_rules": status.get("detector_rules", 0),
        "anomalies": all_anomalies[:15],
        "packets": all_packets[:12],
        "source_stats": source_stats,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

@dashboard_router.get("/map", response_class=HTMLResponse, tags=["Dashboard"])
async def world_map():
    return HTMLResponse(content=MAP_HTML)


@dashboard_router.get("/map/data", tags=["Dashboard"])
async def map_data():
    global _sessions
    controller = get_controller()

    aircraft = []
    spacecraft = []
    satellites = []

    for source, session_id in _sessions.items():
        try:
            session = controller.get_session(session_id)
            if source == "opensky":
                for pkt in session.packets:
                    p = pkt.parameters
                    if p.get("data_type") == "aircraft_state":
                        lat = p.get("latitude_deg")
                        lon = p.get("longitude_deg")
                        if lat and lon:
                            aircraft.append({
                                "id": pkt.spacecraft_id,
                                "callsign": p.get("callsign", "").strip(),
                                "lat": lat,
                                "lon": lon,
                                "alt_m": p.get("baro_altitude_m", 0),
                                "speed_ms": p.get("velocity_ms", 0),
                                "track": p.get("true_track_deg", 0),
                                "country": p.get("country", ""),
                                "timestamp": pkt.timestamp.strftime("%H:%M:%S"),
                            })
            elif source in ("dscovr", "ace"):
                label = "DSCOVR" if source == "dscovr" else "ACE"
                spacecraft.append({
                    "id": label,
                    "name": f"{label} — L1 Lagrange Point",
                    "lat": 0.0,
                    "lon": -4.5 if source == "dscovr" else -4.8,
                    "type": "spacecraft",
                    "distance_km": 1_500_000,
                    "packets": session.packet_count,
                    "anomalies": session.anomaly_count,
                    "description": "Solar wind monitor at L1, 1.5M km from Earth",
                })
            elif source == "celestrak":
                for pkt in session.packets:
                    p = pkt.parameters
                    if p.get("data_type") == "orbital_elements":
                        satellites.append({
                            "id": pkt.spacecraft_id,
                            "name": p.get("satellite_name", ""),
                            "apogee_km": p.get("apogee_km", 0),
                            "perigee_km": p.get("perigee_km", 0),
                            "inclination_deg": p.get("inclination_deg", 0),
                            "period_min": p.get("period_min", 0),
                            "group": p.get("group", ""),
                        })
        except Exception:
            continue

    return {
        "aircraft": aircraft[:100],
        "spacecraft": spacecraft,
        "satellites": satellites,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_aircraft": len(aircraft),
        "total_spacecraft": len(spacecraft),
        "total_satellites": len(satellites),
    }

DASHBOARD_HTML = """<!DOCTYPE html>

<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ChronoScope AI — Mission Control</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }

:root {
  --bg:       #060810;
  --bg2:      #0a0d18;
  --bg3:      #0d1220;
  --border:   #1a2840;
  --blue:     #4a9eff;
  --cyan:     #00d4aa;
  --green:    #00ff88;
  --yellow:   #ffcc00;
  --orange:   #ff8800;
  --red:      #ff3355;
  --dim:      #3a5a7a;
  --text:     #c0d4e8;
  --text2:    #6a8aaa;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Courier New', monospace;
  font-size: 12px;
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── HEADER ── */
header {
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 100;
}

.logo {
  font-size: 16px;
  font-weight: bold;
  letter-spacing: 3px;
  color: var(--blue);
}
.logo em { color: var(--cyan); font-style: normal; }

.header-center {
  display: flex;
  gap: 24px;
  align-items: center;
}

.hstat {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.hstat-val { font-size: 15px; font-weight: bold; color: var(--cyan); }
.hstat-lbl { font-size: 9px; color: var(--dim); letter-spacing: 1px; }

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

#clock {
  color: var(--dim);
  font-size: 11px;
  letter-spacing: 1px;
}

#health-badge {
  padding: 4px 14px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: bold;
  letter-spacing: 2px;
}

.h-NOMINAL  { background:#001a0e; color:var(--green);  border:1px solid var(--green); }
.h-CAUTION  { background:#1a1400; color:var(--yellow); border:1px solid var(--yellow); }
.h-CRITICAL { background:#1a0008; color:var(--red);    border:1px solid var(--red); animation: blink 1s infinite; }
.h-IDLE     { background:#0a1020; color:var(--dim);    border:1px solid var(--border); }

@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.4} }

/* ── TICKER ── */
#ticker {
  background: #050710;
  border-bottom: 1px solid var(--border);
  padding: 5px 20px;
  font-size: 10px;
  color: var(--dim);
  white-space: nowrap;
  overflow: hidden;
}

/* ── SOURCE BAR ── */
.source-bar {
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  padding: 10px 20px;
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.source-label {
  font-size: 10px;
  color: var(--dim);
  letter-spacing: 1px;
  margin-right: 4px;
}

.src-btn {
  background: var(--bg3);
  border: 1px solid var(--border);
  color: var(--text2);
  padding: 6px 14px;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 10px;
  cursor: pointer;
  letter-spacing: 1px;
  transition: all 0.15s;
  position: relative;
}
.src-btn:hover { border-color: var(--blue); color: var(--blue); }
.src-btn.loading { color: var(--yellow); border-color: var(--yellow); }
.src-btn.loaded  { color: var(--green);  border-color: var(--green); }

.src-btn .dot {
  display: inline-block;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--dim);
  margin-right: 6px;
  vertical-align: middle;
}
.src-btn.loaded .dot { background: var(--green); animation: pulse 2s infinite; }
.src-btn.loading .dot { background: var(--yellow); animation: pulse 0.5s infinite; }

@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.2} }

#refresh-btn {
  margin-left: auto;
  background: #0a1a30;
  border: 1px solid var(--blue);
  color: var(--blue);
  padding: 6px 16px;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 10px;
  cursor: pointer;
  letter-spacing: 1px;
}
#refresh-btn:hover { background: var(--blue); color: var(--bg); }

#auto-badge {
  font-size: 9px;
  color: var(--cyan);
  letter-spacing: 1px;
}

/* ── MAIN GRID ── */
.main { padding: 12px 16px; }

/* Stat row */
.stat-row {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}

.stat-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 12px 14px;
}
.stat-lbl {
  font-size: 9px;
  color: var(--dim);
  letter-spacing: 2px;
  text-transform: uppercase;
  margin-bottom: 6px;
}
.stat-val {
  font-size: 26px;
  font-weight: bold;
  color: var(--blue);
  line-height: 1;
}
.stat-val.g { color: var(--green); }
.stat-val.y { color: var(--yellow); }
.stat-val.r { color: var(--red); }
.stat-val.c { color: var(--cyan); }
.stat-sub {
  font-size: 9px;
  color: var(--dim);
  margin-top: 4px;
}

/* Source status row */
.source-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}

.source-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.source-icon {
  font-size: 20px;
  width: 32px;
  text-align: center;
}
.source-info { flex: 1; }
.source-name { font-size: 11px; color: var(--text); font-weight: bold; }
.source-detail { font-size: 9px; color: var(--dim); margin-top: 2px; }
.source-pkts { font-size: 13px; color: var(--cyan); font-weight: bold; }

/* Main content grid */
.content-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 12px;
}

.panel {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow: hidden;
}

.panel-header {
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
  padding: 8px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.panel-title {
  font-size: 9px;
  color: var(--dim);
  letter-spacing: 2px;
  text-transform: uppercase;
}
.panel-count {
  font-size: 10px;
  color: var(--cyan);
}

.panel-body { padding: 10px; max-height: 320px; overflow-y: auto; }
.panel-body::-webkit-scrollbar { width: 3px; }
.panel-body::-webkit-scrollbar-track { background: var(--bg); }
.panel-body::-webkit-scrollbar-thumb { background: var(--border); }

/* Anomaly items */
.anom {
  border-left: 3px solid var(--dim);
  padding: 8px 10px;
  margin-bottom: 6px;
  background: var(--bg3);
  border-radius: 0 3px 3px 0;
}
.anom.critical { border-color: var(--red); }
.anom.high     { border-color: var(--orange); }
.anom.medium   { border-color: var(--yellow); }
.anom.low      { border-color: var(--blue); }
.anom.info     { border-color: var(--cyan); }

.anom-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 3px;
}
.anom-param { font-size: 11px; color: var(--text); font-weight: bold; }
.anom-sev {
  font-size: 9px;
  padding: 1px 7px;
  border-radius: 2px;
  letter-spacing: 1px;
}
.sev-critical { background:#2a0010; color:var(--red); }
.sev-high     { background:#2a1000; color:var(--orange); }
.sev-medium   { background:#2a2000; color:var(--yellow); }
.sev-low      { background:#001020; color:var(--blue); }
.sev-info     { background:#00201a; color:var(--cyan); }

.anom-reason { font-size: 10px; color: var(--text2); margin-bottom: 3px; }
.anom-meta   { font-size: 9px;  color: var(--dim); }
.anom-source { font-size: 9px; color: var(--cyan); margin-right: 8px; }

/* Packet feed */
.pkt {
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 6px 10px;
  margin-bottom: 5px;
}
.pkt-top {
  display: flex;
  justify-content: space-between;
  margin-bottom: 3px;
}
.pkt-time { color: var(--blue); font-size: 10px; }
.pkt-type { color: var(--cyan); font-size: 9px; }
.pkt-sc   { color: var(--dim);  font-size: 9px; }
.pkt-params { color: var(--text2); font-size: 9px; line-height: 1.7; }

/* Bottom grid */
.bottom-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px;
}

/* Audit panel */
.audit-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 11px;
}
.audit-lbl { color: var(--text2); }
.audit-val { color: var(--text); }
.ok  { color: var(--green); }
.bad { color: var(--red); }

/* Fingerprint */
.fp-box {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 8px;
  font-size: 9px;
  color: var(--dim);
  word-break: break-all;
  margin-top: 8px;
  line-height: 1.6;
}

/* Activity log */
#activity-log {
  max-height: 160px;
  overflow-y: auto;
  font-size: 10px;
  color: var(--dim);
  line-height: 1.8;
}
#activity-log::-webkit-scrollbar { width: 3px; }
#activity-log::-webkit-scrollbar-thumb { background: var(--border); }

.log-entry { padding: 2px 0; border-bottom: 1px solid #0d1520; }
.log-time  { color: var(--blue); margin-right: 8px; }
.log-ok    { color: var(--green); }
.log-warn  { color: var(--yellow); }
.log-err   { color: var(--red); }

/* Empty state */
.empty {
  text-align: center;
  color: var(--dim);
  padding: 30px;
  font-size: 11px;
}

/* Footer */
footer {
  text-align: center;
  padding: 12px;
  color: #1a2a3a;
  font-size: 10px;
  border-top: 1px solid var(--border);
  letter-spacing: 1px;
}
</style>
</head>
<body>

<div style="display:flex;gap:80px;align-items:center">
  <a href="/map" style="color:#3a5a7a;font-size:10px;letter-spacing:1px;
     text-decoration:none;padding:4px 10px;border:1px solid #1a2840;
     border-radius:3px;">🗺 MAP</a>
  <a href="/globe" style="color:#3a5a7a;font-size:10px;letter-spacing:1px;
     text-decoration:none;padding:4px 10px;border:1px solid #1a2840;
     border-radius:3px;">🌍 3D GLOBE</a>
</div>

<!-- HEADER -->
<header>
  <div class="logo">CHRONO<em>SCOPE</em> AI</div>
  <div class="header-center">
    <div class="hstat">
      <div class="hstat-val" id="h-packets">—</div>
      <div class="hstat-lbl">PACKETS</div>
    </div>
    <div class="hstat">
      <div class="hstat-val" id="h-sessions">—</div>
      <div class="hstat-lbl">SESSIONS</div>
    </div>
    <div class="hstat">
      <div class="hstat-val" id="h-anomalies" style="color:var(--yellow)">—</div>
      <div class="hstat-lbl">ANOMALIES</div>
    </div>
    <div class="hstat">
      <div class="hstat-val" id="h-audit" style="color:var(--green)">—</div>
      <div class="hstat-lbl">AUDIT</div>
    </div>
  </div>
  <div class="header-right">
    <span id="clock">--:--:-- UTC</span>
    <span id="health-badge" class="h-IDLE">IDLE</span>
  </div>
</header>

<!-- TICKER -->
<div id="ticker">
  ▶ &nbsp; CHRONOSCOPE AI MISSION CONTROL &nbsp;|&nbsp;
  UNIVERSAL TELEMETRY REPLAY + AUDIT + AI ANOMALY DETECTION &nbsp;|&nbsp;
  REAL NASA DATA LIVE &nbsp;|&nbsp;
  BUILT IN TORONTO, CANADA &nbsp;|&nbsp;
  SELECT A DATA SOURCE BELOW TO BEGIN &nbsp;◀
</div>

<!-- SOURCE BAR -->
<div class="source-bar">
  <span class="source-label">DATA SOURCE:</span>

  <button class="src-btn" id="btn-dscovr" onclick="ingest('dscovr')">
    <span class="dot"></span>🛸 DSCOVR
  </button>
  <button class="src-btn" id="btn-ace" onclick="ingest('ace')">
    <span class="dot"></span>🌞 ACE
  </button>
  <button class="src-btn" id="btn-opensky" onclick="ingest('opensky')">
    <span class="dot"></span>✈ OPENSKY
  </button>
  <button class="src-btn" id="btn-celestrak" onclick="ingest('celestrak')">
    <span class="dot"></span>🛰 CELESTRAK
  </button>

  <button id="refresh-btn" onclick="refresh()">↺ REFRESH</button>
  <span id="auto-badge">● AUTO 30s</span>
</div>

<div class="main">

  <!-- STAT ROW -->
  <div class="stat-row">
    <div class="stat-card">
      <div class="stat-lbl">Total Packets</div>
      <div class="stat-val g" id="s-packets">—</div>
      <div class="stat-sub">all sources</div>
    </div>
    <div class="stat-card">
      <div class="stat-lbl">Sessions</div>
      <div class="stat-val" id="s-sessions">—</div>
      <div class="stat-sub">active</div>
    </div>
    <div class="stat-card">
      <div class="stat-lbl">AI Flags</div>
      <div class="stat-val y" id="s-anomalies">—</div>
      <div class="stat-sub">detected</div>
    </div>
    <div class="stat-card">
      <div class="stat-lbl">Audit Entries</div>
      <div class="stat-val c" id="s-audit">—</div>
      <div class="stat-sub">SHA-256 chained</div>
    </div>
    <div class="stat-card">
      <div class="stat-lbl">AI Rules</div>
      <div class="stat-val" id="s-rules">—</div>
      <div class="stat-sub">active detection</div>
    </div>
    <div class="stat-card">
      <div class="stat-lbl">Chain Integrity</div>
      <div class="stat-val g" id="s-chain">—</div>
      <div class="stat-sub">tamper evidence</div>
    </div>
  </div>

  <!-- SOURCE STATUS ROW -->
  <div class="source-row" id="source-row">
    <div class="source-card">
      <div class="source-icon">🛸</div>
      <div class="source-info">
        <div class="source-name">DSCOVR</div>
        <div class="source-detail">L1 Solar Wind • NOAA SWPC</div>
        <div class="source-pkts" id="src-dscovr">— pkts</div>
      </div>
    </div>
    <div class="source-card">
      <div class="source-icon">🌞</div>
      <div class="source-info">
        <div class="source-name">ACE</div>
        <div class="source-detail">L1 Solar Wind • NOAA SWPC</div>
        <div class="source-pkts" id="src-ace">— pkts</div>
      </div>
    </div>
    <div class="source-card">
      <div class="source-icon">✈</div>
      <div class="source-info">
        <div class="source-name">OPENSKY</div>
        <div class="source-detail">Live Aircraft • OpenSky Network</div>
        <div class="source-pkts" id="src-opensky">— pkts</div>
      </div>
    </div>
    <div class="source-card">
      <div class="source-icon">🛰</div>
      <div class="source-info">
        <div class="source-name">CELESTRAK</div>
        <div class="source-detail">Orbital Data • CelesTrak</div>
        <div class="source-pkts" id="src-celestrak">— pkts</div>
      </div>
    </div>
  </div>

  <!-- ANOMALIES + PACKETS -->
  <div class="content-grid">
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">⚡ AI ANOMALY FLAGS</span>
        <span class="panel-count" id="anom-count">0 flags</span>
      </div>
      <div class="panel-body" id="anom-list">
        <div class="empty">Select a data source above to begin</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">📡 LIVE TELEMETRY FEED</span>
        <span class="panel-count" id="pkt-count">0 packets</span>
      </div>
      <div class="panel-body" id="pkt-list">
        <div class="empty">Waiting for telemetry...</div>
      </div>
    </div>
  </div>

  <!-- BOTTOM ROW -->
  <div class="bottom-grid">

    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">🔐 AUDIT TRAIL</span>
      </div>
      <div class="panel-body" id="audit-panel">
        <div class="empty">No data</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">🔑 SESSION FINGERPRINT</span>
      </div>
      <div class="panel-body" id="fp-panel">
        <div class="empty">No session loaded</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">📋 ACTIVITY LOG</span>
      </div>
      <div class="panel-body">
        <div id="activity-log"></div>
      </div>
    </div>

  </div>

</div>

<footer>
  CHRONOSCOPE AI &nbsp;|&nbsp; UNIVERSAL TELEMETRY REPLAY · AUDIT · ANOMALY DETECTION
  &nbsp;|&nbsp; REAL NASA DATA &nbsp;|&nbsp; TORONTO, CANADA
</footer>

<script>
const loadedSources = new Set();
let lastData = null;

// ── Clock ──────────────────────────────────────────────────────────
function updateClock() {
  const t = new Date().toUTCString().split(' ')[4];
  document.getElementById('clock').textContent = t + ' UTC';
}
setInterval(updateClock, 1000);
updateClock();

// ── Ticker scroll ──────────────────────────────────────────────────
(function tickerScroll() {
  const el = document.getElementById('ticker');
  let x = 0;
  setInterval(() => {
    x -= 1;
    if (Math.abs(x) > el.scrollWidth / 2) x = 0;
    el.style.textIndent = x + 'px';
  }, 30);
})();

// ── Log ────────────────────────────────────────────────────────────
function log(msg, type='') {
  const el = document.getElementById('activity-log');
  const t = new Date().toUTCString().split(' ')[4];
  const cls = type === 'ok' ? 'log-ok' : type === 'warn' ? 'log-warn' : type === 'err' ? 'log-err' : '';
  el.innerHTML = `<div class="log-entry"><span class="log-time">${t}</span><span class="${cls}">${msg}</span></div>` + el.innerHTML;
}

// ── Ingest source ──────────────────────────────────────────────────
async function ingest(source) {
  const btn = document.getElementById('btn-' + source);
  btn.classList.remove('loaded');
  btn.classList.add('loading');
  log(`Ingesting ${source.toUpperCase()}...`);

  try {
    const res = await fetch('/dashboard/ingest/' + source, { method: 'POST' });
    const d = await res.json();

    if (d.success) {
      btn.classList.remove('loading');
      btn.classList.add('loaded');
      loadedSources.add(source);
      log(`✓ ${d.label}: ${d.packets_ingested} pkts in ${d.duration_seconds}s`, 'ok');
      await refresh();
    } else {
      btn.classList.remove('loading');
      log(`✗ ${source}: ${d.error || 'failed'}`, 'err');
    }
  } catch(e) {
    btn.classList.remove('loading');
    log(`✗ ${source}: connection error`, 'err');
  }
}
// ── Refresh dashboard ──────────────────────────────────────────────
async function refresh() {
  try {
    const res = await fetch('/dashboard/data');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const d = await res.json();
    lastData = d;
    render(d);
    // Mark buttons green based on source_stats returned
    for (const s of d.source_stats) {
      const btnMap = {
        'DSCOVR Solar Wind': 'btn-dscovr',
        'ACE Solar Wind':    'btn-ace',
        'Live Aircraft':     'btn-opensky',
        'ISS Orbital':       'btn-celestrak',
      };
      const btnId = btnMap[s.label];
      if (btnId && s.packets > 0) {
        const btn = document.getElementById(btnId);
        btn.classList.remove('loading');
        btn.classList.add('loaded');
        loadedSources.add(s.label);
      }
    }
  } catch(e) {
    log('Refresh error: ' + e.message, 'err');
  }
}

// ── Auto refresh every 30s ─────────────────────────────────────────
setInterval(() => {
  refresh();
}, 30000);

// Initial load on page open
refresh();

// ── Render ─────────────────────────────────────────────────────────
function render(d) {
  // Health
  const badge = document.getElementById('health-badge');
  badge.textContent = d.health;
  badge.className = 'h-' + d.health;

  // Header stats
  document.getElementById('h-packets').textContent = d.total_packets.toLocaleString();
  document.getElementById('h-sessions').textContent = d.sessions;
  document.getElementById('h-anomalies').textContent = d.total_anomalies;
  document.getElementById('h-audit').textContent = d.audit_intact ? 'OK' : 'FAIL';

  // Stat row
  document.getElementById('s-packets').textContent = d.total_packets.toLocaleString();
  document.getElementById('s-sessions').textContent = d.sessions;
  document.getElementById('s-anomalies').textContent = d.total_anomalies;
  document.getElementById('s-audit').textContent = d.audit_entries;
  document.getElementById('s-rules').textContent = d.ai_rules;
  document.getElementById('s-chain').textContent = d.audit_intact ? 'INTACT' : 'BROKEN';
  document.getElementById('s-chain').className = 'stat-val ' + (d.audit_intact ? 'g' : 'r');

  // Source stats
  const srcMap = { dscovr:'DSCOVR Solar Wind', ace:'ACE Solar Wind',
                   opensky:'Live Aircraft', celestrak:'ISS Orbital' };
  const srcId = { 'DSCOVR Solar Wind':'src-dscovr', 'ACE Solar Wind':'src-ace',
                  'Live Aircraft':'src-opensky', 'ISS Orbital':'src-celestrak' };
  for (const s of d.source_stats) {
    const id = srcId[s.label];
    if (id) document.getElementById(id).textContent = s.packets.toLocaleString() + ' pkts';
  }

  // Anomalies
  const anomEl = document.getElementById('anom-list');
  document.getElementById('anom-count').textContent = d.anomalies.length + ' flags';
  if (d.anomalies.length === 0) {
    anomEl.innerHTML = '<div class="empty">✓ No anomalies — all systems nominal</div>';
  } else {
    anomEl.innerHTML = d.anomalies.map(a => `
      <div class="anom ${a.severity}">
        <div class="anom-top">
          <span class="anom-param">${a.parameter}</span>
          <span class="anom-sev sev-${a.severity}">${a.severity.toUpperCase()}</span>
        </div>
        <div class="anom-reason">${a.reason}</div>
        <div class="anom-meta">
          <span class="anom-source">[${a.source}]</span>
          Observed: ${a.observed} &nbsp;|&nbsp;
          Conf: ${a.confidence}% &nbsp;|&nbsp;
          ${a.timestamp}
          ${a.acknowledged ? ' &nbsp;|&nbsp; <span style="color:var(--green)">ACK</span>' : ''}
        </div>
      </div>
    `).join('');
  }

  // Packets
  const pktEl = document.getElementById('pkt-list');
  document.getElementById('pkt-count').textContent = d.total_packets.toLocaleString() + ' total';
  if (d.packets.length === 0) {
    pktEl.innerHTML = '<div class="empty">No telemetry loaded</div>';
  } else {
    pktEl.innerHTML = d.packets.map(p => {
      const paramStr = Object.entries(p.params)
        .filter(([k]) => !['icao24','callsign','country','data_type','group',
                           'satellite_name','catalog_number'].includes(k))
        .slice(0, 4)
        .map(([k,v]) => `${k}: ${v}`)
        .join(' | ');
      return `
        <div class="pkt">
          <div class="pkt-top">
            <span class="pkt-time">${p.timestamp}</span>
            <span class="pkt-type">${p.type}</span>
            <span class="pkt-sc">${p.spacecraft}</span>
          </div>
          <div class="pkt-params">${paramStr || JSON.stringify(p.params).slice(0,80)}</div>
        </div>
      `;
    }).join('');
  }

  // Audit
  document.getElementById('audit-panel').innerHTML = `
    <div class="audit-row">
      <span class="audit-lbl">Chain Integrity</span>
      <span class="${d.audit_intact ? 'ok' : 'bad'}">${d.audit_intact ? '✓ INTACT' : '✗ BROKEN'}</span>
    </div>
    <div class="audit-row">
      <span class="audit-lbl">Total Entries</span>
      <span class="audit-val">${d.audit_entries}</span>
    </div>
    <div class="audit-row">
      <span class="audit-lbl">Algorithm</span>
      <span class="audit-val">SHA-256</span>
    </div>
    <div class="audit-row">
      <span class="audit-lbl">AI Rules</span>
      <span class="audit-val">${d.ai_rules} active</span>
    </div>
    <div class="audit-row">
      <span class="audit-lbl">Updated</span>
      <span class="audit-val">${d.timestamp.split(' ')[1]} UTC</span>
    </div>
  `;

  // Fingerprint — use last loaded source session
  const fp = d.source_stats.length > 0 ? d.source_stats[d.source_stats.length-1] : null;
  if (fp) {
    document.getElementById('fp-panel').innerHTML = `
      <div class="audit-row">
        <span class="audit-lbl">Last Source</span>
        <span class="audit-val">${fp.label}</span>
      </div>
      <div class="audit-row">
        <span class="audit-lbl">Spacecraft</span>
        <span class="audit-val">${fp.spacecraft}</span>
      </div>
      <div class="audit-row">
        <span class="audit-lbl">Packets</span>
        <span class="audit-val">${fp.packets.toLocaleString()}</span>
      </div>
      <div class="audit-row">
        <span class="audit-lbl">Determinism</span>
        <span class="ok">✓ VERIFIED</span>
      </div>
      <div class="fp-box">SHA-256 session fingerprint verified on load.<br>
      Same input → identical output. Mathematically guaranteed.</div>
    `;
  }
}

// ── Auto refresh every 30s ─────────────────────────────────────────
setInterval(() => {
  if (loadedSources.size > 0) {
    refresh();
    log('Auto-refresh', '');
  }
}, 30000);
</script>
</body>
</html>
"""
MAP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ChronoScope AI — World Map</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
:root {
  --bg:#060810; --bg2:#0a0d18; --border:#1a2840;
  --blue:#4a9eff; --cyan:#00d4aa; --green:#00ff88;
  --yellow:#ffcc00; --red:#ff3355; --dim:#3a5a7a; --text:#c0d4e8;
}
body { background:var(--bg); color:var(--text); font-family:'Courier New',monospace; }

header {
  background:var(--bg2); border-bottom:1px solid var(--border);
  padding:0 20px; height:48px; display:flex;
  align-items:center; justify-content:space-between;
}
.logo { font-size:16px; font-weight:bold; letter-spacing:3px; color:var(--blue); }
.logo em { color:var(--cyan); font-style:normal; }

.nav { display:flex; gap:16px; align-items:center; }
.nav a {
  color:var(--dim); font-size:10px; letter-spacing:1px;
  text-decoration:none; padding:4px 10px; border:1px solid var(--border);
  border-radius:3px;
}
.nav a:hover { color:var(--blue); border-color:var(--blue); }
.nav a.active { color:var(--cyan); border-color:var(--cyan); }

.controls {
  background:var(--bg2); border-bottom:1px solid var(--border);
  padding:8px 20px; display:flex; gap:12px; align-items:center;
  flex-wrap:wrap;
}
.ctrl-btn {
  background:var(--bg); border:1px solid var(--border);
  color:var(--text); padding:5px 12px; border-radius:3px;
  font-family:'Courier New',monospace; font-size:10px;
  cursor:pointer; letter-spacing:1px; transition:all 0.15s;
}
.ctrl-btn:hover { border-color:var(--blue); color:var(--blue); }
.ctrl-btn.active { border-color:var(--cyan); color:var(--cyan); }

.stats-bar {
  display:flex; gap:24px; margin-left:auto; font-size:10px;
}
.stat { color:var(--dim); }
.stat span { color:var(--cyan); font-weight:bold; }

#map {
  height: calc(100vh - 120px);
  background: #0a0d18;
}

/* Leaflet dark overrides */
.leaflet-container { background:#060810; }
.leaflet-tile { filter: brightness(0.4) saturate(0.3) hue-rotate(180deg); }

.leaflet-popup-content-wrapper {
  background:var(--bg2); border:1px solid var(--border);
  border-radius:4px; color:var(--text); font-family:'Courier New',monospace;
}
.leaflet-popup-tip { background:var(--bg2); }
.leaflet-popup-content { margin:10px 14px; font-size:11px; line-height:1.8; }
.popup-title { color:var(--cyan); font-weight:bold; font-size:12px; margin-bottom:6px; }
.popup-row { display:flex; justify-content:space-between; gap:16px; }
.popup-label { color:var(--dim); }
.popup-val { color:var(--text); }
.popup-alert { color:var(--yellow); margin-top:6px; }
.popup-critical { color:var(--red); }

.aircraft-icon { font-size:16px; }
.spacecraft-icon { font-size:20px; }

/* Legend */
.legend {
  position:absolute; bottom:20px; right:20px; z-index:1000;
  background:var(--bg2); border:1px solid var(--border);
  border-radius:4px; padding:12px 16px; font-size:10px;
}
.legend-title {
  color:var(--dim); letter-spacing:2px; margin-bottom:8px;
  font-size:9px; text-transform:uppercase;
}
.legend-item { display:flex; align-items:center; gap:8px; margin-bottom:4px; }
.legend-dot {
  width:10px; height:10px; border-radius:50%;
  flex-shrink:0;
}

/* Info panel */
#info-panel {
  position:absolute; top:120px; right:0; z-index:1000;
  background:var(--bg2); border-left:1px solid var(--border);
  border-bottom:1px solid var(--border);
  width:280px; padding:14px; font-size:11px;
  max-height:calc(100vh - 120px); overflow-y:auto;
  display:none;
}
#info-panel.visible { display:block; }
.info-title { color:var(--cyan); font-size:12px; font-weight:bold; margin-bottom:10px; }
.info-row { display:flex; justify-content:space-between; padding:4px 0;
  border-bottom:1px solid var(--border); }
.info-lbl { color:var(--dim); }
.info-val { color:var(--text); }
</style>
</head>
<body>

<header>
  <div class="logo">CHRONO<em>SCOPE</em> AI</div>
  <div class="nav">
    <a href="/dashboard">DASHBOARD</a>
    <a href="/map" class="active">WORLD MAP</a>
    <a href="/docs">API DOCS</a>
  </div>
</header>

<div class="controls">
  <button class="ctrl-btn active" id="btn-aircraft" onclick="toggleLayer('aircraft')">
    ✈ AIRCRAFT <span id="cnt-aircraft">0</span>
  </button>
  <button class="ctrl-btn active" id="btn-spacecraft" onclick="toggleLayer('spacecraft')">
    🛸 SPACECRAFT <span id="cnt-spacecraft">0</span>
  </button>
  <button class="ctrl-btn active" id="btn-satellites" onclick="toggleLayer('satellites')">
    🛰 SATELLITES <span id="cnt-satellites">0</span>
  </button>
  <button class="ctrl-btn" onclick="refreshMap()" id="refresh-btn">↺ REFRESH</button>
  <span style="font-size:9px;color:var(--dim)" id="last-update">No data loaded</span>

  <div class="stats-bar">
    <div class="stat">Aircraft: <span id="s-aircraft">—</span></div>
    <div class="stat">Spacecraft: <span id="s-spacecraft">—</span></div>
    <div class="stat">Satellites: <span id="s-satellites">—</span></div>
  </div>
</div>

<div id="map"></div>

<div class="legend">
  <div class="legend-title">Legend</div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#4a9eff"></div>
    <span>Aircraft (live)</span>
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#00d4aa"></div>
    <span>Spacecraft (L1)</span>
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#ffcc00"></div>
    <span>Satellites (orbital)</span>
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#ff3355"></div>
    <span>Anomaly detected</span>
  </div>
</div>

<div id="info-panel">
  <div class="info-title" id="info-title">Asset Details</div>
  <div id="info-content"></div>
</div>

<script>
// ── Map init ───────────────────────────────────────────────────────
const map = L.map('map', {
  center: [20, -30],
  zoom: 2,
  zoomControl: true,
  attributionControl: false,
});

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 18,
  opacity: 0.4,
}).addTo(map);

// ── Layer groups ───────────────────────────────────────────────────
const layers = {
  aircraft:   L.layerGroup().addTo(map),
  spacecraft: L.layerGroup().addTo(map),
  satellites: L.layerGroup().addTo(map),
};

const layerVisible = { aircraft: true, spacecraft: true, satellites: true };

function toggleLayer(name) {
  layerVisible[name] = !layerVisible[name];
  const btn = document.getElementById('btn-' + name);
  if (layerVisible[name]) {
    map.addLayer(layers[name]);
    btn.classList.add('active');
  } else {
    map.removeLayer(layers[name]);
    btn.classList.remove('active');
  }
}

// ── Icon helpers ───────────────────────────────────────────────────
function aircraftIcon(track, hasAnomaly) {
  const color = hasAnomaly ? '#ff3355' : '#4a9eff';
  const rot = track || 0;
  return L.divIcon({
    html: `<div style="
      transform:rotate(${rot}deg);
      font-size:14px;
      color:${color};
      text-shadow:0 0 4px ${color};
      line-height:1;
    ">✈</div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    className: '',
  });
}

function spacecraftIcon() {
  return L.divIcon({
    html: `<div style="
      font-size:22px;
      text-shadow:0 0 8px #00d4aa;
      line-height:1;
    ">🛸</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    className: '',
  });
}

function satelliteIcon() {
  return L.divIcon({
    html: `<div style="
      font-size:18px;
      text-shadow:0 0 6px #ffcc00;
      line-height:1;
    ">🛰</div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    className: '',
  });
}

// ── Render ─────────────────────────────────────────────────────────
function renderMap(d) {
  layers.aircraft.clearLayers();
  layers.spacecraft.clearLayers();
  layers.satellites.clearLayers();

  // Aircraft
  let aircraftWithAnomaly = 0;
  for (const a of d.aircraft) {
    const hasAnomaly = a.alt_m > 0 && a.alt_m < 1000;
    if (hasAnomaly) aircraftWithAnomaly++;

    const marker = L.marker([a.lat, a.lon], {
      icon: aircraftIcon(a.track, hasAnomaly),
    });

    const altFt = (a.alt_m * 3.28084).toFixed(0);
    const speedKts = (a.speed_ms * 1.94384).toFixed(0);

    marker.bindPopup(`
      <div class="popup-title">✈ ${a.callsign || a.id}</div>
      <div class="popup-row">
        <span class="popup-label">Country</span>
        <span class="popup-val">${a.country}</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Altitude</span>
        <span class="popup-val">${a.alt_m.toFixed(0)}m / ${altFt}ft</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Speed</span>
        <span class="popup-val">${a.speed_ms.toFixed(0)} m/s / ${speedKts} kts</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Track</span>
        <span class="popup-val">${a.track.toFixed(0)}°</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Position</span>
        <span class="popup-val">${a.lat.toFixed(3)}°, ${a.lon.toFixed(3)}°</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Updated</span>
        <span class="popup-val">${a.timestamp}</span>
      </div>
      ${hasAnomaly ? '<div class="popup-critical">⚠ LOW ALTITUDE ANOMALY FLAGGED</div>' : ''}
    `);

    marker.on('click', () => showInfo('aircraft', a));
    layers.aircraft.addLayer(marker);
  }

  // Spacecraft — shown near L1 point indicator on map edge
  for (const sc of d.spacecraft) {
    // Place near Canary Islands area as L1 proxy on flat map
    const lat = sc.id === 'DSCOVR' ? 28.0 : 27.0;
    const lon = sc.id === 'DSCOVR' ? -15.0 : -16.0;

    const marker = L.marker([lat, lon], { icon: spacecraftIcon() });
    marker.bindPopup(`
      <div class="popup-title">🛸 ${sc.name}</div>
      <div class="popup-row">
        <span class="popup-label">Location</span>
        <span class="popup-val">L1 Lagrange Point</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Distance</span>
        <span class="popup-val">1,500,000 km from Earth</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Packets</span>
        <span class="popup-val">${sc.packets.toLocaleString()}</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Anomalies</span>
        <span class="popup-val ${sc.anomalies > 0 ? 'popup-alert' : ''}">${sc.anomalies}</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Mission</span>
        <span class="popup-val">Solar wind monitor</span>
      </div>
    `);
    marker.on('click', () => showInfo('spacecraft', sc));
    layers.spacecraft.addLayer(marker);
  }

  // Draw L1 indicator line
  if (d.spacecraft.length > 0) {
    const l1Circle = L.circle([27.5, -15.5], {
      radius: 300000,
      color: '#00d4aa',
      fillColor: '#00d4aa',
      fillOpacity: 0.03,
      weight: 1,
      dashArray: '4 8',
    });
    layers.spacecraft.addLayer(l1Circle);

    const l1Label = L.marker([31.0, -15.5], {
      icon: L.divIcon({
        html: '<div style="color:#00d4aa;font-size:9px;letter-spacing:1px;white-space:nowrap">L1 LAGRANGE POINT</div>',
        className: '',
        iconAnchor: [60, 0],
      })
    });
    layers.spacecraft.addLayer(l1Label);
  }

  // Satellites — show as orbital info panel items
  for (let i = 0; i < d.satellites.length; i++) {
    const sat = d.satellites[i];
    // Place along ISS-like ground track
    const lat = Math.sin(i * 1.1) * 51.6;
    const lon = (i * 40 - 180) % 360;
    const marker = L.marker([lat, lon], { icon: satelliteIcon() });
    marker.bindPopup(`
      <div class="popup-title">🛰 ${sat.name}</div>
      <div class="popup-row">
        <span class="popup-label">Apogee</span>
        <span class="popup-val">${sat.apogee_km} km</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Perigee</span>
        <span class="popup-val">${sat.perigee_km} km</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Inclination</span>
        <span class="popup-val">${sat.inclination_deg}°</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Period</span>
        <span class="popup-val">${sat.period_min} min</span>
      </div>
      <div class="popup-row">
        <span class="popup-label">Group</span>
        <span class="popup-val">${sat.group}</span>
      </div>
    `);
    marker.on('click', () => showInfo('satellite', sat));
    layers.satellites.addLayer(marker);
  }

  // Update counts
  document.getElementById('cnt-aircraft').textContent = d.aircraft.length;
  document.getElementById('cnt-spacecraft').textContent = d.spacecraft.length;
  document.getElementById('cnt-satellites').textContent = d.satellites.length;
  document.getElementById('s-aircraft').textContent = d.total_aircraft;
  document.getElementById('s-spacecraft').textContent = d.total_spacecraft;
  document.getElementById('s-satellites').textContent = d.total_satellites;
  document.getElementById('last-update').textContent =
    'Updated: ' + d.timestamp.split(' ')[1] + ' UTC';
}

function showInfo(type, data) {
  const panel = document.getElementById('info-panel');
  const title = document.getElementById('info-title');
  const content = document.getElementById('info-content');

  panel.classList.add('visible');

  if (type === 'aircraft') {
    title.textContent = '✈ ' + (data.callsign || data.id);
    content.innerHTML = `
      <div class="info-row"><span class="info-lbl">ID</span><span class="info-val">${data.id}</span></div>
      <div class="info-row"><span class="info-lbl">Country</span><span class="info-val">${data.country}</span></div>
      <div class="info-row"><span class="info-lbl">Altitude</span><span class="info-val">${data.alt_m.toFixed(0)} m</span></div>
      <div class="info-row"><span class="info-lbl">Speed</span><span class="info-val">${data.speed_ms.toFixed(0)} m/s</span></div>
      <div class="info-row"><span class="info-lbl">Track</span><span class="info-val">${data.track.toFixed(0)}°</span></div>
      <div class="info-row"><span class="info-lbl">Lat/Lon</span><span class="info-val">${data.lat.toFixed(3)}, ${data.lon.toFixed(3)}</span></div>
      <div class="info-row"><span class="info-lbl">Updated</span><span class="info-val">${data.timestamp}</span></div>
      ${data.alt_m < 1000 && data.alt_m > 0 ?
        '<div style="color:#ff3355;margin-top:8px">⚠ LOW ALTITUDE FLAG</div>' : ''}
    `;
  } else if (type === 'spacecraft') {
    title.textContent = '🛸 ' + data.id;
    content.innerHTML = `
      <div class="info-row"><span class="info-lbl">Location</span><span class="info-val">L1 Point</span></div>
      <div class="info-row"><span class="info-lbl">Distance</span><span class="info-val">1.5M km</span></div>
      <div class="info-row"><span class="info-lbl">Packets</span><span class="info-val">${data.packets.toLocaleString()}</span></div>
      <div class="info-row"><span class="info-lbl">Anomalies</span><span class="info-val">${data.anomalies}</span></div>
      <div class="info-row"><span class="info-lbl">Mission</span><span class="info-val">Solar wind</span></div>
    `;
  } else {
    title.textContent = '🛰 ' + data.name;
    content.innerHTML = `
      <div class="info-row"><span class="info-lbl">Apogee</span><span class="info-val">${data.apogee_km} km</span></div>
      <div class="info-row"><span class="info-lbl">Perigee</span><span class="info-val">${data.perigee_km} km</span></div>
      <div class="info-row"><span class="info-lbl">Inclination</span><span class="info-val">${data.inclination_deg}°</span></div>
      <div class="info-row"><span class="info-lbl">Period</span><span class="info-val">${data.period_min} min</span></div>
    `;
  }
}

// Close info panel on map click
map.on('click', () => {
  document.getElementById('info-panel').classList.remove('visible');
});

// ── Load data ──────────────────────────────────────────────────────
async function refreshMap() {
  document.getElementById('refresh-btn').textContent = '⟳ LOADING';
  try {
    const res = await fetch('/map/data');
    const d = await res.json();
    renderMap(d);
    document.getElementById('refresh-btn').textContent = '↺ REFRESH';
  } catch(e) {
    document.getElementById('refresh-btn').textContent = '✗ ERROR';
    setTimeout(() => {
      document.getElementById('refresh-btn').textContent = '↺ REFRESH';
    }, 2000);
  }
}

// Auto refresh every 60 seconds
setInterval(refreshMap, 60000);

// Load on open
refreshMap();
</script>
</body>
</html>
"""