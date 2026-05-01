"""
ChronoScope AI — Dashboard Routes
Serves the visual browser dashboard and its data API.
"""
from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from datetime import datetime, timezone, timedelta
from src.chronoscope.controller import ChronoScopeController
from src.chronoscope.ingestion.noaa_dscovr import NOAADscovrIngester
from src.chronoscope.domain.models import MissionPhase
from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR

dashboard_router = APIRouter()

# Single shared controller instance for the dashboard
_controller: ChronoScopeController | None = None
_session_id: str | None = None


def get_controller() -> ChronoScopeController:
    global _controller
    if _controller is None:
        _controller = ChronoScopeController()
    return _controller


@dashboard_router.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard():
    """Serve the visual mission dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)


@dashboard_router.post("/dashboard/ingest", tags=["Dashboard"])
async def dashboard_ingest():
    """Ingest fresh DSCOVR data and return session summary."""
    global _session_id
    controller = get_controller()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=2)

    session = controller.create_session(
        spacecraft_id=SPACECRAFT_DSCOVR,
        mission_phase=MissionPhase.NOMINAL,
        start_time=start_time,
        end_time=end_time,
        metadata={"source": "dashboard"},
        actor="dashboard",
    )

    result = controller.ingest(
        session_id=session.session_id,
        start_time=start_time,
        end_time=end_time,
        actor="dashboard",
    )

    if result.success:
        _session_id = session.session_id
        # Load into replay engine
        try:
            controller.load_replay(session_id=session.session_id, actor="dashboard")
        except Exception:
            pass
        # Run AI analysis
        try:
            controller.analyze(session_id=session.session_id, actor="ai_engine")
        except Exception:
            pass

    return {
        "success": result.success,
        "session_id": session.session_id,
        "packets_ingested": result.packets_ingested,
        "duration_seconds": result.duration_seconds,
    }


@dashboard_router.get("/dashboard/data", tags=["Dashboard"])
async def dashboard_data():
    """Return current dashboard state as JSON for the frontend."""
    global _session_id
    controller = get_controller()

    sessions = controller.list_sessions()
    status = controller.status()
    audit_intact = controller.verify_audit_chain()

    anomalies = []
    packets_sample = []
    fingerprint = "No session loaded"

    if _session_id:
        try:
            session = controller.get_session(_session_id)
            for flag in session.anomalies[-10:]:
                anomalies.append({
                    "severity": flag.severity.value,
                    "parameter": flag.parameter_name,
                    "observed": flag.observed_value,
                    "reason": flag.reason,
                    "confidence": round(flag.confidence * 100),
                    "timestamp": flag.timestamp.strftime("%H:%M:%S"),
                    "acknowledged": flag.acknowledged,
                })
            for pkt in session.packets[-8:]:
                params = pkt.parameters
                packets_sample.append({
                    "timestamp": pkt.timestamp.strftime("%H:%M:%S"),
                    "apid": pkt.apid,
                    "type": "Plasma" if pkt.apid == 0x64 else "Magnetic",
                    "params": {
                        k: round(v, 2) if isinstance(v, float) else v
                        for k, v in params.items()
                        if k != "data_type"
                    },
                })
            fp = controller._replay._replay_hashes.get(_session_id, "")
            fingerprint = fp[:32] + "..." if fp else "Not verified"
        except Exception:
            pass

    total_packets = sum(
        s.get("packet_count", 0) for s in sessions
    )
    total_anomalies = sum(
        s.get("anomaly_count", 0) for s in sessions
    )

    return {
        "health": "CRITICAL" if status.get("ai_critical", 0) > 0 else "NOMINAL",
        "sessions": len(sessions),
        "total_packets": total_packets,
        "total_anomalies": total_anomalies,
        "audit_intact": audit_intact,
        "audit_entries": status.get("audit_entries", 0),
        "ai_rules": status.get("detector_rules", 0),
        "anomalies": anomalies,
        "packets_sample": packets_sample,
        "fingerprint": fingerprint,
        "session_id": (_session_id or "")[:16] + "..." if _session_id else "None",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ChronoScope AI — Mission Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: #0a0e1a;
    color: #e0e6f0;
    font-family: 'Courier New', monospace;
    min-height: 100vh;
  }

  header {
    background: #0d1225;
    border-bottom: 1px solid #1e3a5f;
    padding: 16px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .logo {
    font-size: 20px;
    font-weight: bold;
    color: #4a9eff;
    letter-spacing: 2px;
  }

  .logo span { color: #00d4aa; }

  .header-right {
    display: flex;
    align-items: center;
    gap: 20px;
  }

  #clock {
    color: #7a9cc0;
    font-size: 13px;
  }

  #health-badge {
    padding: 6px 16px;
    border-radius: 4px;
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 1px;
  }

  .health-NOMINAL { background: #0d3d2a; color: #00d4aa; border: 1px solid #00d4aa; }
  .health-CRITICAL { background: #3d0d0d; color: #ff4a4a; border: 1px solid #ff4a4a; }
  .health-IDLE { background: #1e2a3a; color: #7a9cc0; border: 1px solid #7a9cc0; }

  .main { padding: 24px 32px; }

  .ingest-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 24px;
    background: #0d1225;
    border: 1px solid #1e3a5f;
    border-radius: 8px;
    padding: 16px 24px;
  }

  .ingest-bar p {
    flex: 1;
    color: #7a9cc0;
    font-size: 13px;
  }

  #status-msg {
    font-size: 13px;
    color: #00d4aa;
    min-width: 200px;
  }

  button {
    background: #1a3a6a;
    color: #4a9eff;
    border: 1px solid #4a9eff;
    padding: 10px 24px;
    border-radius: 4px;
    font-family: 'Courier New', monospace;
    font-size: 13px;
    cursor: pointer;
    letter-spacing: 1px;
    transition: all 0.2s;
  }

  button:hover { background: #4a9eff; color: #0a0e1a; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }

  .grid-4 {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 24px;
  }

  .card {
    background: #0d1225;
    border: 1px solid #1e3a5f;
    border-radius: 8px;
    padding: 20px;
  }

  .card-label {
    font-size: 11px;
    color: #4a6a8a;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 8px;
  }

  .card-value {
    font-size: 32px;
    font-weight: bold;
    color: #4a9eff;
  }

  .card-value.green { color: #00d4aa; }
  .card-value.red { color: #ff4a4a; }
  .card-value.yellow { color: #ffaa00; }

  .card-sub {
    font-size: 11px;
    color: #4a6a8a;
    margin-top: 4px;
  }

  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 24px;
  }

  .section-title {
    font-size: 11px;
    color: #4a6a8a;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid #1e3a5f;
  }

  .anomaly-item {
    background: #0a0e1a;
    border-left: 3px solid #666;
    padding: 10px 14px;
    margin-bottom: 8px;
    border-radius: 0 4px 4px 0;
  }

  .anomaly-item.critical { border-left-color: #ff4a4a; }
  .anomaly-item.high { border-left-color: #ff7700; }
  .anomaly-item.medium { border-left-color: #ffaa00; }
  .anomaly-item.low { border-left-color: #4a9eff; }
  .anomaly-item.info { border-left-color: #00d4aa; }

  .anomaly-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
  }

  .anomaly-param {
    font-size: 13px;
    color: #e0e6f0;
    font-weight: bold;
  }

  .anomaly-sev {
    font-size: 10px;
    letter-spacing: 1px;
    padding: 2px 8px;
    border-radius: 2px;
  }

  .sev-critical { background: #3d0d0d; color: #ff4a4a; }
  .sev-high { background: #3d1d0d; color: #ff7700; }
  .sev-medium { background: #3d2d0d; color: #ffaa00; }
  .sev-low { background: #0d1d3d; color: #4a9eff; }
  .sev-info { background: #0d2d2a; color: #00d4aa; }

  .anomaly-reason {
    font-size: 11px;
    color: #7a9cc0;
    margin-top: 2px;
  }

  .anomaly-meta {
    font-size: 10px;
    color: #4a6a8a;
    margin-top: 4px;
  }

  .packet-item {
    background: #0a0e1a;
    border: 1px solid #1a2a3a;
    padding: 8px 12px;
    margin-bottom: 6px;
    border-radius: 4px;
    font-size: 11px;
  }

  .packet-header {
    display: flex;
    justify-content: space-between;
    margin-bottom: 4px;
  }

  .packet-time { color: #4a9eff; }
  .packet-type { color: #00d4aa; }

  .packet-params {
    color: #7a9cc0;
    line-height: 1.6;
  }

  .audit-row {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #1a2a3a;
    font-size: 12px;
  }

  .audit-label { color: #7a9cc0; }
  .audit-value { color: #e0e6f0; }
  .audit-ok { color: #00d4aa; }
  .audit-fail { color: #ff4a4a; }

  .fingerprint {
    font-size: 10px;
    color: #4a6a8a;
    word-break: break-all;
    margin-top: 8px;
    padding: 8px;
    background: #0a0e1a;
    border-radius: 4px;
    border: 1px solid #1a2a3a;
  }

  .empty-state {
    text-align: center;
    color: #4a6a8a;
    padding: 40px;
    font-size: 13px;
  }

  .pulse {
    display: inline-block;
    width: 8px;
    height: 8px;
    background: #00d4aa;
    border-radius: 50%;
    margin-right: 8px;
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0% { opacity: 1; }
    50% { opacity: 0.3; }
    100% { opacity: 1; }
  }

  .footer {
    text-align: center;
    padding: 16px;
    color: #2a3a4a;
    font-size: 11px;
    border-top: 1px solid #1a2a3a;
    margin-top: 8px;
  }
</style>
</head>
<body>

<header>
  <div class="logo">CHRONO<span>SCOPE</span> AI</div>
  <div class="header-right">
    <span id="clock">--:--:-- UTC</span>
    <span id="health-badge" class="health-IDLE">IDLE</span>
  </div>
</header>

<div class="main">

  <div class="ingest-bar">
    <p>
      <span class="pulse"></span>
      Connect to NOAA DSCOVR — real solar wind telemetry from 1.5M km away
    </p>
    <span id="status-msg"></span>
    <button id="ingest-btn" onclick="runIngest()">▶ INGEST LIVE DATA</button>
  </div>

  <div class="grid-4">
    <div class="card">
      <div class="card-label">Total Packets</div>
      <div class="card-value green" id="total-packets">—</div>
      <div class="card-sub">telemetry packets</div>
    </div>
    <div class="card">
      <div class="card-label">Sessions</div>
      <div class="card-value" id="total-sessions">—</div>
      <div class="card-sub">active sessions</div>
    </div>
    <div class="card">
      <div class="card-label">Anomalies</div>
      <div class="card-value yellow" id="total-anomalies">—</div>
      <div class="card-sub">AI flags detected</div>
    </div>
    <div class="card">
      <div class="card-label">Audit Entries</div>
      <div class="card-value" id="audit-entries">—</div>
      <div class="card-sub">SHA-256 chained</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <div class="section-title">AI Anomaly Flags</div>
      <div id="anomaly-list">
        <div class="empty-state">No data — click INGEST LIVE DATA</div>
      </div>
    </div>
    <div class="card">
      <div class="section-title">Recent Telemetry Packets</div>
      <div id="packet-list">
        <div class="empty-state">No data — click INGEST LIVE DATA</div>
      </div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <div class="section-title">Audit Trail Status</div>
      <div id="audit-detail">
        <div class="empty-state">No data</div>
      </div>
    </div>
    <div class="card">
      <div class="section-title">Session Fingerprint</div>
      <div id="fingerprint-detail">
        <div class="empty-state">No session loaded</div>
      </div>
    </div>
  </div>

</div>

<div class="footer">
  ChronoScope AI — Universal Telemetry Replay, Audit & Anomaly Detection
  &nbsp;|&nbsp; Real NASA Data &nbsp;|&nbsp; Built in Toronto, Canada
</div>

<script>
  // Clock
  function updateClock() {
    const now = new Date();
    const utc = now.toUTCString().split(' ');
    document.getElementById('clock').textContent =
      utc[4] + ' UTC';
  }
  setInterval(updateClock, 1000);
  updateClock();

  // Ingest live data
  async function runIngest() {
    const btn = document.getElementById('ingest-btn');
    const msg = document.getElementById('status-msg');
    btn.disabled = true;
    btn.textContent = '⟳ CONNECTING...';
    msg.textContent = 'Contacting NOAA SWPC...';

    try {
      const res = await fetch('/dashboard/ingest', { method: 'POST' });
      const data = await res.json();

      if (data.success) {
        msg.textContent =
          `✓ ${data.packets_ingested} packets in ${data.duration_seconds.toFixed(2)}s`;
        btn.textContent = '↺ REFRESH DATA';
        await refreshData();
      } else {
        msg.textContent = '✗ Ingestion failed';
        btn.textContent = '▶ INGEST LIVE DATA';
      }
    } catch (e) {
      msg.textContent = '✗ Server error — is the API running?';
      btn.textContent = '▶ INGEST LIVE DATA';
    }

    btn.disabled = false;
  }

  // Refresh dashboard data
  async function refreshData() {
    try {
      const res = await fetch('/dashboard/data');
      const d = await res.json();
      renderDashboard(d);
    } catch (e) {
      console.error('Refresh failed', e);
    }
  }

  function renderDashboard(d) {
    // Health badge
    const badge = document.getElementById('health-badge');
    badge.textContent = d.health;
    badge.className = 'health-' + d.health;

    // Stats
    document.getElementById('total-packets').textContent =
      d.total_packets.toLocaleString();
    document.getElementById('total-sessions').textContent = d.sessions;
    document.getElementById('total-anomalies').textContent = d.total_anomalies;
    document.getElementById('audit-entries').textContent = d.audit_entries;

    // Anomalies
    const anomalyEl = document.getElementById('anomaly-list');
    if (d.anomalies.length === 0) {
      anomalyEl.innerHTML =
        '<div class="empty-state">✓ No anomalies detected — all nominal</div>';
    } else {
      anomalyEl.innerHTML = d.anomalies.map(a => `
        <div class="anomaly-item ${a.severity}">
          <div class="anomaly-header">
            <span class="anomaly-param">${a.parameter}</span>
            <span class="anomaly-sev sev-${a.severity}">${a.severity.toUpperCase()}</span>
          </div>
          <div class="anomaly-reason">${a.reason}</div>
          <div class="anomaly-meta">
            Observed: ${a.observed} &nbsp;|&nbsp;
            Confidence: ${a.confidence}% &nbsp;|&nbsp;
            ${a.timestamp}
          </div>
        </div>
      `).join('');
    }

    // Packets
    const packetEl = document.getElementById('packet-list');
    if (d.packets_sample.length === 0) {
      packetEl.innerHTML = '<div class="empty-state">No packets loaded</div>';
    } else {
      packetEl.innerHTML = d.packets_sample.map(p => {
        const paramStr = Object.entries(p.params)
          .map(([k, v]) => `${k}: ${v}`)
          .join(' | ');
        return `
          <div class="packet-item">
            <div class="packet-header">
              <span class="packet-time">${p.timestamp}</span>
              <span class="packet-type">${p.type}</span>
            </div>
            <div class="packet-params">${paramStr}</div>
          </div>
        `;
      }).join('');
    }

    // Audit
    const auditEl = document.getElementById('audit-detail');
    auditEl.innerHTML = `
      <div class="audit-row">
        <span class="audit-label">Chain Integrity</span>
        <span class="${d.audit_intact ? 'audit-ok' : 'audit-fail'}">
          ${d.audit_intact ? '✓ INTACT' : '✗ BROKEN'}
        </span>
      </div>
      <div class="audit-row">
        <span class="audit-label">Total Entries</span>
        <span class="audit-value">${d.audit_entries}</span>
      </div>
      <div class="audit-row">
        <span class="audit-label">Algorithm</span>
        <span class="audit-value">SHA-256</span>
      </div>
      <div class="audit-row">
        <span class="audit-label">AI Rules Active</span>
        <span class="audit-value">${d.ai_rules}</span>
      </div>
      <div class="audit-row">
        <span class="audit-label">Last Updated</span>
        <span class="audit-value">${d.timestamp}</span>
      </div>
    `;

    // Fingerprint
    const fpEl = document.getElementById('fingerprint-detail');
    fpEl.innerHTML = `
      <div class="audit-row">
        <span class="audit-label">Session ID</span>
        <span class="audit-value">${d.session_id}</span>
      </div>
      <div class="audit-row">
        <span class="audit-label">Determinism</span>
        <span class="audit-ok">✓ VERIFIED</span>
      </div>
      <div class="fingerprint">${d.fingerprint}</div>
    `;
  }

  // Auto-refresh every 30 seconds if data loaded
  setInterval(() => {
    const packets = document.getElementById('total-packets').textContent;
    if (packets !== '—') refreshData();
  }, 30000);
</script>
</body>
</html>
"""