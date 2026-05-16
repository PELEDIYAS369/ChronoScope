# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — 3D Globe Routes
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from src.chronoscope.api.dashboard_routes import get_controller, _sessions
from datetime import datetime, timezone

globe_router = APIRouter()

@globe_router.get("/globe", response_class=HTMLResponse, tags=["Globe"])
async def globe():
    return HTMLResponse(content=GLOBE_HTML)

@globe_router.get("/globe/data", tags=["Globe"])
async def globe_data():
    controller = get_controller()
    aircraft = []
    spacecraft = []
    satellites = []
    anomaly_positions = []

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
                            has_anomaly = 0 < p.get("baro_altitude_m", 9999) < 1000
                            aircraft.append({
                                "callsign": p.get("callsign", "").strip() or pkt.spacecraft_id,
                                "lat": lat, "lon": lon,
                                "alt_m": p.get("baro_altitude_m", 0),
                                "speed": p.get("velocity_ms", 0),
                                "track": p.get("true_track_deg", 0),
                                "country": p.get("country", ""),
                                "anomaly": has_anomaly,
                            })
                            if has_anomaly:
                                anomaly_positions.append({"lat": lat, "lon": lon, "type": "aircraft"})
            elif source in ("dscovr", "ace"):
                spacecraft.append({
                    "id": source.upper(),
                    "name": f"{source.upper()} Solar Wind Monitor",
                    "location": "L1 Lagrange Point",
                    "distance_km": 1_500_000,
                    "packets": session.packet_count,
                    "anomalies": session.anomaly_count,
                })
                if session.anomaly_count > 0:
                    anomaly_positions.append({"lat": 0, "lon": 0, "type": "spacecraft", "id": source})
            elif source == "celestrak":
                for pkt in session.packets:
                    p = pkt.parameters
                    if p.get("data_type") == "orbital_elements":
                        satellites.append({
                            "name": p.get("satellite_name", ""),
                            "apogee_km": p.get("apogee_km", 400),
                            "perigee_km": p.get("perigee_km", 400),
                            "inclination_deg": p.get("inclination_deg", 51.6),
                            "period_min": p.get("period_min", 92),
                        })
        except Exception:
            continue

    return {
        "aircraft": aircraft[:100],
        "spacecraft": spacecraft,
        "satellites": satellites,
        "anomaly_positions": anomaly_positions,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "total_aircraft": len(aircraft),
        "total_anomalies": len(anomaly_positions),
    }


GLOBE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>ChronoScope AI — 3D Globe</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
:root {
  --bg:#060810; --bg2:#0a0d18; --border:#1a2840;
  --blue:#4a9eff; --cyan:#00d4aa; --green:#00ff88;
  --yellow:#ffcc00; --red:#ff3355; --dim:#3a5a7a; --text:#c0d4e8;
}
body { background:var(--bg); color:var(--text); font-family:'Courier New',monospace; overflow:hidden; }

header {
  background:var(--bg2); border-bottom:1px solid var(--border);
  padding:0 20px; height:44px; display:flex;
  align-items:center; justify-content:space-between;
  position:fixed; top:0; left:0; right:0; z-index:100;
}
.logo { font-size:15px; font-weight:bold; letter-spacing:3px; color:var(--blue); }
.logo em { color:var(--cyan); font-style:normal; }
.nav { display:flex; gap:10px; align-items:center; }
.nav a {
  color:var(--dim); font-size:10px; letter-spacing:1px;
  text-decoration:none; padding:3px 10px;
  border:1px solid var(--border); border-radius:3px;
}
.nav a:hover { color:var(--blue); border-color:var(--blue); }
.nav a.active { color:var(--cyan); border-color:var(--cyan); }

#canvas { position:fixed; top:44px; left:0; width:100vw; height:calc(100vh - 44px); display:block; }

/* HUD overlays */
.hud {
  position:fixed; z-index:50; pointer-events:none;
  font-size:10px; letter-spacing:1px;
}

#hud-tl {
  top:54px; left:12px;
  background:rgba(6,8,16,0.85);
  border:1px solid var(--border); border-radius:4px;
  padding:10px 14px; pointer-events:all;
  min-width:180px;
}
.hud-title { color:var(--dim); font-size:9px; letter-spacing:2px; margin-bottom:8px; }
.hud-row { display:flex; justify-content:space-between; gap:20px; margin-bottom:4px; }
.hud-lbl { color:var(--dim); }
.hud-val { color:var(--cyan); font-weight:bold; }
.hud-val.r { color:var(--red); }
.hud-val.g { color:var(--green); }

#hud-tr {
  top:54px; right:12px;
  background:rgba(6,8,16,0.85);
  border:1px solid var(--border); border-radius:4px;
  padding:10px 14px; pointer-events:all;
  min-width:160px;
}

#hud-br {
  bottom:20px; right:12px;
  background:rgba(6,8,16,0.85);
  border:1px solid var(--border); border-radius:4px;
  padding:10px 14px; pointer-events:all;
}
.legend-item { display:flex; align-items:center; gap:8px; margin-bottom:4px; }
.ldot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }

#hud-bl {
  bottom:20px; left:12px;
  background:rgba(6,8,16,0.85);
  border:1px solid var(--border); border-radius:4px;
  padding:8px 12px; pointer-events:all;
  display:flex; gap:8px; flex-wrap:wrap; max-width:400px;
}
.ctrl-btn {
  background:transparent; border:1px solid var(--border);
  color:var(--dim); padding:4px 10px; border-radius:3px;
  font-family:'Courier New',monospace; font-size:9px;
  cursor:pointer; letter-spacing:1px; pointer-events:all;
}
.ctrl-btn:hover { border-color:var(--blue); color:var(--blue); }
.ctrl-btn.on { border-color:var(--cyan); color:var(--cyan); }

#info-box {
  position:fixed; top:50%; left:50%;
  transform:translate(-50%,-50%);
  background:rgba(10,13,24,0.95);
  border:1px solid var(--cyan); border-radius:6px;
  padding:16px 20px; min-width:220px;
  z-index:200; display:none;
  font-size:11px;
}
.ib-title { color:var(--cyan); font-size:13px; font-weight:bold; margin-bottom:10px; }
.ib-row { display:flex; justify-content:space-between; gap:16px;
  padding:4px 0; border-bottom:1px solid var(--border); }
.ib-lbl { color:var(--dim); }
.ib-val { color:var(--text); }
.ib-close {
  position:absolute; top:8px; right:10px;
  color:var(--dim); cursor:pointer; font-size:14px;
}
.ib-close:hover { color:var(--red); }

#clock {
  position:fixed; top:54px; left:50%; transform:translateX(-50%);
  background:rgba(6,8,16,0.7); border:1px solid var(--border);
  border-radius:3px; padding:3px 12px; font-size:10px;
  color:var(--dim); z-index:50; pointer-events:none;
  letter-spacing:2px;
}
</style>
</head>
<body>

<header>
  <div class="logo">CHRONO<em>SCOPE</em> AI — 3D GLOBE</div>
  <div class="nav">
    <a href="/dashboard">DASHBOARD</a>
    <a href="/map">FLAT MAP</a>
    <a href="/globe" class="active">3D GLOBE</a>
    <a href="/docs">API DOCS</a>
  </div>
</header>

<canvas id="canvas"></canvas>

<div id="clock">--:--:-- UTC</div>

<!-- Top Left HUD -->
<div class="hud" id="hud-tl">
  <div class="hud-title">LIVE ASSETS</div>
  <div class="hud-row"><span class="hud-lbl">✈ Aircraft</span><span class="hud-val" id="h-ac">—</span></div>
  <div class="hud-row"><span class="hud-lbl">🛸 Spacecraft</span><span class="hud-val g" id="h-sc">—</span></div>
  <div class="hud-row"><span class="hud-lbl">🛰 Satellites</span><span class="hud-val" id="h-sat">—</span></div>
  <div class="hud-row"><span class="hud-lbl">⚡ Anomalies</span><span class="hud-val r" id="h-anom">—</span></div>
  <div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
    <div class="hud-row"><span class="hud-lbl">Updated</span><span class="hud-val" id="h-time" style="color:var(--dim)">—</span></div>
  </div>
</div>

<!-- Top Right HUD -->
<div class="hud" id="hud-tr">
  <div class="hud-title">SOLAR SYSTEM</div>
  <div class="hud-row"><span class="hud-lbl">☀ Sun</span><span class="hud-val" style="color:#ffaa00">CENTRAL</span></div>
  <div class="hud-row"><span class="hud-lbl">🌍 Earth</span><span class="hud-val g">1 AU</span></div>
  <div class="hud-row"><span class="hud-lbl">L1 Point</span><span class="hud-val" style="color:#00d4aa">1.5M km</span></div>
  <div class="hud-row"><span class="hud-lbl">🔴 Mars</span><span class="hud-val" style="color:#ff6644">1.52 AU</span></div>
  <div class="hud-row"><span class="hud-lbl">🪐 Jupiter</span><span class="hud-val" style="color:#ffcc88">5.2 AU</span></div>
  <div class="hud-row"><span class="hud-lbl">🪐 Saturn</span><span class="hud-val" style="color:#ffeeaa">9.5 AU</span></div>
</div>

<!-- Bottom Right Legend -->
<div class="hud" id="hud-br">
  <div class="hud-title">LEGEND</div>
  <div class="legend-item"><div class="ldot" style="background:#4a9eff"></div><span>Aircraft (live)</span></div>
  <div class="legend-item"><div class="ldot" style="background:#ff3355"></div><span>Aircraft anomaly</span></div>
  <div class="legend-item"><div class="ldot" style="background:#00d4aa"></div><span>Spacecraft (L1)</span></div>
  <div class="legend-item"><div class="ldot" style="background:#ffcc00"></div><span>Satellite (orbital)</span></div>
  <div class="legend-item"><div class="ldot" style="background:#ffaa00"></div><span>Sun</span></div>
  <div class="legend-item"><div class="ldot" style="background:#ffffff"></div><span>Stars</span></div>
</div>

<!-- Bottom Left Controls -->
<div class="hud" id="hud-bl">
  <button class="ctrl-btn on" id="btn-rotate" onclick="toggleRotate()">⟳ AUTO ROTATE</button>
  <button class="ctrl-btn on" id="btn-aircraft" onclick="toggleLayer('aircraft')">✈ AIRCRAFT</button>
  <button class="ctrl-btn on" id="btn-spacecraft" onclick="toggleLayer('spacecraft')">🛸 SPACECRAFT</button>
  <button class="ctrl-btn on" id="btn-orbits" onclick="toggleLayer('orbits')">⭕ ORBITS</button>
  <button class="ctrl-btn on" id="btn-solar" onclick="toggleLayer('solar')">☀ SOLAR SYSTEM</button>
  <button class="ctrl-btn on" id="btn-atm" onclick="toggleLayer('atmosphere')">🌐 ATMOSPHERE</button>
  <button class="ctrl-btn" onclick="loadData()">↺ REFRESH DATA</button>
  <button class="ctrl-btn" onclick="resetView()">⌂ RESET VIEW</button>
</div>

<!-- Info Box -->
<div id="info-box">
  <span class="ib-close" onclick="closeInfo()">✕</span>
  <div class="ib-title" id="ib-title">Asset</div>
  <div id="ib-content"></div>
</div>

<script>
// ── Three.js from CDN ──────────────────────────────────────────────
const script = document.createElement('script');
script.src = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js';
script.onload = init;
document.head.appendChild(script);

let scene, camera, renderer, earthMesh, atmosphereMesh;
let starField, sunMesh, sunGlow;
let aircraftGroup, spacecraftGroup, orbitGroup, solarGroup;
let autoRotate = true;
let isDragging = false, prevMouse = {x:0, y:0};
let targetRotX = 0.3, targetRotY = 0;
let currentRotX = 0.3, currentRotY = 0;
let globeData = null;

const LAYERS = {
  aircraft: true, spacecraft: true,
  orbits: true, solar: true, atmosphere: true
};

function init() {
  const canvas = document.getElementById('canvas');
  const W = window.innerWidth;
  const H = window.innerHeight - 44;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';

  // Scene
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x010208);

  // Camera
  camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 10000);
  camera.position.set(0, 0, 3.5);

  // Renderer
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setSize(W, H);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  buildScene();
  loadData();
  animate();
  setupEvents(canvas);

  window.addEventListener('resize', () => {
    const W2 = window.innerWidth;
    const H2 = window.innerHeight - 44;
    canvas.style.width = W2 + 'px';
    canvas.style.height = H2 + 'px';
    camera.aspect = W2 / H2;
    camera.updateProjectionMatrix();
    renderer.setSize(W2, H2);
  });
}

function buildScene() {
  // ── Stars ──────────────────────────────────────────────────────
  const starGeo = new THREE.BufferGeometry();
  const starCount = 8000;
  const starPos = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount * 3; i++) {
    starPos[i] = (Math.random() - 0.5) * 2000;
  }
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  starField = new THREE.Points(starGeo, new THREE.PointsMaterial({
    color: 0xffffff, size: 0.5, sizeAttenuation: true,
  }));
  scene.add(starField);

  // ── Sun ───────────────────────────────────────────────────────
  solarGroup = new THREE.Group();
  scene.add(solarGroup);

  const sunGeo = new THREE.SphereGeometry(1.2, 32, 32);
  const sunMat = new THREE.MeshBasicMaterial({ color: 0xffaa00 });
  sunMesh = new THREE.Mesh(sunGeo, sunMat);
  sunMesh.position.set(-80, 0, -120);
  solarGroup.add(sunMesh);

  // Sun glow
  const sunGlowGeo = new THREE.SphereGeometry(1.8, 32, 32);
  const sunGlowMat = new THREE.MeshBasicMaterial({
    color: 0xff6600, transparent: true, opacity: 0.15, side: THREE.BackSide
  });
  const sunGlowMesh = new THREE.Mesh(sunGlowGeo, sunGlowMat);
  sunMesh.add(sunGlowMesh);

  // Sun light
  const sunLight = new THREE.PointLight(0xffffff, 1.5, 300);
  sunLight.position.copy(sunMesh.position);
  solarGroup.add(sunLight);

  // Ambient
  scene.add(new THREE.AmbientLight(0x223355, 0.8));

  // ── Planets ───────────────────────────────────────────────────
  const planets = [
    { color: 0xff6644, dist: 110, size: 0.18, name: 'Mars' },
    { color: 0xffcc88, dist: 200, size: 0.5,  name: 'Jupiter' },
    { color: 0xffeeaa, dist: 320, size: 0.4,  name: 'Saturn' },
  ];
  for (const p of planets) {
    const geo = new THREE.SphereGeometry(p.size, 16, 16);
    const mat = new THREE.MeshBasicMaterial({ color: p.color });
    const mesh = new THREE.Mesh(geo, mat);
    const angle = Math.random() * Math.PI * 2;
    mesh.position.set(
      Math.cos(angle) * p.dist - 80,
      (Math.random() - 0.5) * 20,
      Math.sin(angle) * p.dist - 80,
    );
    solarGroup.add(mesh);

    // Orbit ring
    const ringGeo = new THREE.RingGeometry(p.dist - 0.5, p.dist + 0.5, 128);
    const ringMat = new THREE.MeshBasicMaterial({
      color: p.color, side: THREE.DoubleSide,
      transparent: true, opacity: 0.06,
    });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = Math.PI / 2;
    ring.position.set(-80, 0, -80);
    solarGroup.add(ring);
  }

 // ── Earth ─────────────────────────────────────────────────────
  const earthGeo = new THREE.SphereGeometry(1, 64, 64);

  // Load NASA Blue Marble texture
  const textureLoader = new THREE.TextureLoader();
  const earthTex = textureLoader.load(
    'https://eoimages.gsfc.nasa.gov/images/imagerecords/74000/74117/world.200408.3x5400x2700.jpg',
    () => { renderer.render(scene, camera); },
    undefined,
    () => {
      // Fallback if NASA texture fails — use canvas texture
      const fc = document.createElement('canvas');
      fc.width = 512; fc.height = 256;
      const fctx = fc.getContext('2d');
      const grad = fctx.createLinearGradient(0,0,0,256);
      grad.addColorStop(0,'#0a1628');
      grad.addColorStop(0.5,'#0d2040');
      grad.addColorStop(1,'#0a1628');
      fctx.fillStyle = grad;
      fctx.fillRect(0,0,512,256);
      fctx.fillStyle = '#1a3a1a';
      fctx.fillRect(60,40,90,100);
      fctx.fillRect(100,150,50,80);
      fctx.fillRect(220,30,50,60);
      fctx.fillRect(220,90,60,110);
      fctx.fillRect(270,20,150,100);
      fctx.fillRect(360,150,70,50);
      earthMesh.material.map = new THREE.CanvasTexture(fc);
      earthMesh.material.needsUpdate = true;
    }
  );

  // Night lights texture (city lights on dark side)
  const nightTex = textureLoader.load(
    'https://eoimages.gsfc.nasa.gov/images/imagerecords/79000/79765/dnb_land_ocean_ice.2012.3600x1800.jpg'
  );

  // Cloud texture
  const cloudTex = textureLoader.load(
    'https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57747/cloud_combined_2048.jpg'
  );

  const earthMat = new THREE.MeshPhongMaterial({
    map: earthTex,
    specularMap: nightTex,
    specular: new THREE.Color(0x111133),
    shininess: 15,
  });
  earthMesh = new THREE.Mesh(earthGeo, earthMat);
  scene.add(earthMesh);

  // Cloud layer
  const cloudGeo = new THREE.SphereGeometry(1.005, 64, 64);
  const cloudMat = new THREE.MeshPhongMaterial({
    map: cloudTex,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
  });
  const cloudMesh = new THREE.Mesh(cloudGeo, cloudMat);
  scene.add(cloudMesh);

  // Slowly rotate clouds independently
  function animateClouds() {
    requestAnimationFrame(animateClouds);
    cloudMesh.rotation.y += 0.00018;
  }
  animateClouds();

  // Grid lines on Earth
  const gridMat = new THREE.MeshBasicMaterial({
    color: 0x1a3a6a, wireframe: true,
    transparent: true, opacity: 0.04,
  });
  const gridMesh = new THREE.Mesh(
    new THREE.SphereGeometry(1.002, 24, 12), gridMat
  );
  scene.add(gridMesh);
  
  // ── Atmosphere ─────────────────────────────────────────────────
  const atmGeo = new THREE.SphereGeometry(1.12, 64, 64);
  const atmMat = new THREE.MeshBasicMaterial({
    color: 0x4488ff,
    transparent: true, opacity: 0.07,
    side: THREE.BackSide,
  });
  atmosphereMesh = new THREE.Mesh(atmGeo, atmMat);
  scene.add(atmosphereMesh);

  // Atmosphere glow ring
  const glowGeo = new THREE.SphereGeometry(1.15, 64, 64);
  const glowMat = new THREE.MeshBasicMaterial({
    color: 0x2255cc,
    transparent: true, opacity: 0.04,
    side: THREE.BackSide,
  });
  scene.add(new THREE.Mesh(glowGeo, glowMat));

  // ── L1 Point indicator ─────────────────────────────────────────
  const l1Geo = new THREE.SphereGeometry(0.04, 8, 8);
  const l1Mat = new THREE.MeshBasicMaterial({ color: 0x00d4aa });
  const l1Mesh = new THREE.Mesh(l1Geo, l1Mat);
  l1Mesh.position.set(0.35, 0, 0);  // Slightly outside Earth toward sun
  scene.add(l1Mesh);

  const l1GlowGeo = new THREE.SphereGeometry(0.08, 8, 8);
  const l1GlowMat = new THREE.MeshBasicMaterial({
    color: 0x00d4aa, transparent: true, opacity: 0.2,
  });
  scene.add(new THREE.Mesh(l1GlowGeo, l1GlowMat)).position.set(0.35, 0, 0);

  // ── Groups ─────────────────────────────────────────────────────
  aircraftGroup = new THREE.Group();
  scene.add(aircraftGroup);

  spacecraftGroup = new THREE.Group();
  scene.add(spacecraftGroup);

  orbitGroup = new THREE.Group();
  scene.add(orbitGroup);

  // ISS orbit ring
  const issRingGeo = new THREE.RingGeometry(1.065, 1.075, 128);
  const issRingMat = new THREE.MeshBasicMaterial({
    color: 0xffcc00, side: THREE.DoubleSide,
    transparent: true, opacity: 0.4,
  });
  const issRing = new THREE.Mesh(issRingGeo, issRingMat);
  issRing.rotation.x = Math.PI / 2 - (51.6 * Math.PI / 180);
  orbitGroup.add(issRing);

  // LEO zone indicator
  const leoGeo = new THREE.SphereGeometry(1.07, 64, 64);
  const leoMat = new THREE.MeshBasicMaterial({
    color: 0x2244aa, transparent: true, opacity: 0.03,
    side: THREE.BackSide,
  });
  orbitGroup.add(new THREE.Mesh(leoGeo, leoMat));
}

// ── Lat/Lon to 3D ─────────────────────────────────────────────────
function latLonToVec3(lat, lon, radius) {
  const phi   = (90 - lat)  * Math.PI / 180;
  const theta = (lon + 180) * Math.PI / 180;
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
     radius * Math.cos(phi),
     radius * Math.sin(phi) * Math.sin(theta),
  );
}

// ── Place assets on globe ──────────────────────────────────────────
function placeAssets(data) {
  aircraftGroup.clear();
  spacecraftGroup.clear();

  // Aircraft
  for (const a of data.aircraft) {
    const altRadius = 1.0 + (a.alt_m / 6_371_000);
    const pos = latLonToVec3(a.lat, a.lon, altRadius);

    const color = a.anomaly ? 0xff3355 : 0x4a9eff;
    const size = a.anomaly ? 0.018 : 0.010;

    const geo = new THREE.SphereGeometry(size, 6, 6);
    const mat = new THREE.MeshBasicMaterial({ color });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.copy(pos);
    mesh.userData = { type: 'aircraft', data: a };
    aircraftGroup.add(mesh);

    if (a.anomaly) {
      // Pulsing ring for anomaly
      const ringGeo = new THREE.RingGeometry(0.025, 0.035, 16);
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0xff3355, side: THREE.DoubleSide,
        transparent: true, opacity: 0.6,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.copy(pos);
      ring.lookAt(0, 0, 0);
      ring.userData = { pulse: true };
      aircraftGroup.add(ring);
    }
  }

  // Spacecraft at L1
  for (const sc of data.spacecraft) {
    const offset = sc.id === 'DSCOVR' ? 0 : 0.02;
    const geo = new THREE.SphereGeometry(0.025, 12, 12);
    const mat = new THREE.MeshBasicMaterial({ color: 0x00d4aa });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(0.35 + offset, offset, 0);
    mesh.userData = { type: 'spacecraft', data: sc };
    spacecraftGroup.add(mesh);

    // Glow
    const glowGeo = new THREE.SphereGeometry(0.045, 8, 8);
    const glowMat = new THREE.MeshBasicMaterial({
      color: 0x00d4aa, transparent: true, opacity: 0.25,
    });
    const glow = new THREE.Mesh(glowGeo, glowMat);
    glow.position.copy(mesh.position);
    spacecraftGroup.add(glow);
  }

  // Satellite on ISS ring
  if (data.satellites.length > 0) {
    const sat = data.satellites[0];
    const avgAlt = ((sat.apogee_km + sat.perigee_km) / 2 / 6371) + 1;
    const geo = new THREE.SphereGeometry(0.018, 8, 8);
    const mat = new THREE.MeshBasicMaterial({ color: 0xffcc00 });
    const mesh = new THREE.Mesh(geo, mat);
    const inclRad = sat.inclination_deg * Math.PI / 180;
    mesh.position.set(avgAlt * Math.cos(inclRad), avgAlt * Math.sin(inclRad), 0);
    mesh.userData = { type: 'satellite', data: sat };
    spacecraftGroup.add(mesh);
  }

  // Update HUD
  document.getElementById('h-ac').textContent = data.total_aircraft;
  document.getElementById('h-sc').textContent = data.spacecraft.length;
  document.getElementById('h-sat').textContent = data.satellites.length;
  document.getElementById('h-anom').textContent = data.total_anomalies;
  document.getElementById('h-time').textContent = data.timestamp;
}

// ── Load data ──────────────────────────────────────────────────────
async function loadData() {
  try {
    const res = await fetch('/globe/data');
    globeData = await res.json();
    placeAssets(globeData);
  } catch(e) {
    console.error('Data load failed', e);
  }
}

// ── Animation loop ─────────────────────────────────────────────────
let t = 0;
function animate() {
  requestAnimationFrame(animate);
  t += 0.005;

  // Auto rotate earth
  if (autoRotate) {
    earthMesh.rotation.y += 0.0015;
    aircraftGroup.rotation.y += 0.0015;
  }

  // Smooth camera rotation
  currentRotX += (targetRotX - currentRotX) * 0.08;
  currentRotY += (targetRotY - currentRotY) * 0.08;
  camera.position.x = Math.sin(currentRotY) * Math.cos(currentRotX) * 3.5;
  camera.position.y = Math.sin(currentRotX) * 3.5;
  camera.position.z = Math.cos(currentRotY) * Math.cos(currentRotX) * 3.5;
  camera.lookAt(0, 0, 0);

  // Pulse anomaly rings
  aircraftGroup.children.forEach(m => {
    if (m.userData.pulse) {
      m.material.opacity = 0.3 + 0.4 * Math.sin(t * 4);
      const s = 1 + 0.3 * Math.sin(t * 4);
      m.scale.set(s, s, s);
    }
  });

  // Slow solar system rotation
  solarGroup.rotation.y += 0.0002;

  // Starfield slow drift
  starField.rotation.y += 0.00005;

  renderer.render(scene, camera);
}

// ── Controls ───────────────────────────────────────────────────────
function toggleRotate() {
  autoRotate = !autoRotate;
  const btn = document.getElementById('btn-rotate');
  btn.className = 'ctrl-btn' + (autoRotate ? ' on' : '');
}

function toggleLayer(name) {
  LAYERS[name] = !LAYERS[name];
  const btn = document.getElementById('btn-' + name);
  btn.className = 'ctrl-btn' + (LAYERS[name] ? ' on' : '');

  const targets = {
    aircraft: aircraftGroup,
    spacecraft: spacecraftGroup,
    orbits: orbitGroup,
    solar: solarGroup,
    atmosphere: atmosphereMesh,
  };
  if (targets[name]) targets[name].visible = LAYERS[name];
}

function resetView() {
  targetRotX = 0.3;
  targetRotY = 0;
}

function closeInfo() {
  document.getElementById('info-box').style.display = 'none';
}

// ── Mouse drag to rotate ───────────────────────────────────────────
function setupEvents(canvas) {
  canvas.addEventListener('mousedown', e => {
    isDragging = true;
    prevMouse = { x: e.clientX, y: e.clientY };
    autoRotate = false;
    document.getElementById('btn-rotate').className = 'ctrl-btn';
  });

  canvas.addEventListener('mousemove', e => {
    if (!isDragging) return;
    const dx = e.clientX - prevMouse.x;
    const dy = e.clientY - prevMouse.y;
    targetRotY += dx * 0.005;
    targetRotX += dy * 0.005;
    targetRotX = Math.max(-1.4, Math.min(1.4, targetRotX));
    prevMouse = { x: e.clientX, y: e.clientY };
  });

  canvas.addEventListener('mouseup', () => { isDragging = false; });
  canvas.addEventListener('mouseleave', () => { isDragging = false; });

  // Zoom
  canvas.addEventListener('wheel', e => {
    const zoom = camera.position.length() + e.deltaY * 0.005;
    const clamped = Math.max(1.5, Math.min(8, zoom));
    camera.position.normalize().multiplyScalar(clamped);
  }, { passive: true });

  // Touch support
  let lastTouch = null;
  canvas.addEventListener('touchstart', e => {
    lastTouch = e.touches[0];
    autoRotate = false;
  }, { passive: true });

  canvas.addEventListener('touchmove', e => {
    if (!lastTouch) return;
    const dx = e.touches[0].clientX - lastTouch.clientX;
    const dy = e.touches[0].clientY - lastTouch.clientY;
    targetRotY += dx * 0.005;
    targetRotX += dy * 0.005;
    lastTouch = e.touches[0];
  }, { passive: true });

  // Click on assets
  canvas.addEventListener('click', e => {
    if (isDragging) return;
    const rect = canvas.getBoundingClientRect();
    const mouse = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(mouse, camera);

    const all = [
      ...aircraftGroup.children,
      ...spacecraftGroup.children,
    ].filter(m => m.userData.type);

    const hits = raycaster.intersectObjects(all);
    if (hits.length > 0) {
      showInfo(hits[0].object.userData);
    }
  });
}

function showInfo(ud) {
  const box = document.getElementById('info-box');
  const title = document.getElementById('ib-title');
  const content = document.getElementById('ib-content');
  box.style.display = 'block';

  if (ud.type === 'aircraft') {
    const a = ud.data;
    title.textContent = '✈ ' + (a.callsign || 'Unknown');
    content.innerHTML = `
      <div class="ib-row"><span class="ib-lbl">Country</span><span class="ib-val">${a.country}</span></div>
      <div class="ib-row"><span class="ib-lbl">Altitude</span><span class="ib-val">${a.alt_m.toFixed(0)} m</span></div>
      <div class="ib-row"><span class="ib-lbl">Speed</span><span class="ib-val">${a.speed.toFixed(0)} m/s</span></div>
      <div class="ib-row"><span class="ib-lbl">Track</span><span class="ib-val">${a.track.toFixed(0)}°</span></div>
      <div class="ib-row"><span class="ib-lbl">Position</span><span class="ib-val">${a.lat.toFixed(3)}°, ${a.lon.toFixed(3)}°</span></div>
      ${a.anomaly ? '<div style="color:#ff3355;margin-top:8px">⚠ LOW ALTITUDE ANOMALY</div>' : ''}
    `;
  } else if (ud.type === 'spacecraft') {
    const sc = ud.data;
    title.textContent = '🛸 ' + sc.name;
    content.innerHTML = `
      <div class="ib-row"><span class="ib-lbl">Location</span><span class="ib-val">L1 Point</span></div>
      <div class="ib-row"><span class="ib-lbl">Distance</span><span class="ib-val">1,500,000 km</span></div>
      <div class="ib-row"><span class="ib-lbl">Packets</span><span class="ib-val">${sc.packets.toLocaleString()}</span></div>
      <div class="ib-row"><span class="ib-lbl">Anomalies</span><span class="ib-val" style="${sc.anomalies>0?'color:#ff3355':''}">${sc.anomalies}</span></div>
    `;
  } else {
    const sat = ud.data;
    title.textContent = '🛰 ' + sat.name;
    content.innerHTML = `
      <div class="ib-row"><span class="ib-lbl">Apogee</span><span class="ib-val">${sat.apogee_km} km</span></div>
      <div class="ib-row"><span class="ib-lbl">Perigee</span><span class="ib-val">${sat.perigee_km} km</span></div>
      <div class="ib-row"><span class="ib-lbl">Inclination</span><span class="ib-val">${sat.inclination_deg}°</span></div>
      <div class="ib-row"><span class="ib-lbl">Period</span><span class="ib-val">${sat.period_min} min</span></div>
    `;
  }
}

// ── Clock ──────────────────────────────────────────────────────────
setInterval(() => {
  document.getElementById('clock').textContent =
    new Date().toUTCString().split(' ')[4] + ' UTC';
}, 1000);

// Auto refresh data every 60s
setInterval(loadData, 60000);
</script>
</body>
</html>
"""