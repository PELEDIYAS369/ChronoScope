"""
ChronoScope AI — OpenSky Network Aviation Ingester
Live aircraft positions and telemetry from OpenSky Network.
Public API. No API key required for basic access.
https://opensky-network.org/api/states/all
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterator
import requests
import struct
import structlog

from src.chronoscope.domain.models import TelemetryPacket, PacketType
from src.chronoscope.domain.exceptions import DataSourceUnavailableError
from src.chronoscope.ingestion.base import BaseIngester

logger = structlog.get_logger(__name__)

OPENSKY_URL = "https://opensky-network.org/api/states/all"
APID_AIRCRAFT_STATE = 0x80


class OpenSkyIngester(BaseIngester):
    """
    Ingests live aircraft state vectors from OpenSky Network.
    Each aircraft is treated as a tracked asset.
    State vectors include: position, altitude, speed, heading.

    This demonstrates ChronoScope's universal architecture —
    same platform, different data adapter, aviation domain.
    """

    def __init__(
        self,
        timeout_seconds: int = 30,
        max_aircraft: int = 50,
        bbox: tuple[float, float, float, float] | None = None,
    ):
        super().__init__(source_name="opensky_network")
        self.timeout = timeout_seconds
        self.max_aircraft = max_aircraft
        # bbox = (min_lat, max_lat, min_lon, max_lon)
        # Default: North America
        self.bbox = bbox or (24.0, 72.0, -140.0, -52.0)

    def is_available(self) -> bool:
        try:
            r = requests.get(OPENSKY_URL, timeout=10, params={
                "lamin": self.bbox[0], "lamax": self.bbox[1],
                "lomin": self.bbox[2], "lomax": self.bbox[3],
            })
            return r.status_code == 200
        except Exception:
            return False

    def get_available_spacecraft(self) -> list[str]:
        return ["OPENSKY_LIVE"]

    def fetch_packets(
        self,
        spacecraft_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """
        Fetch live aircraft state vectors.
        Each aircraft becomes a TelemetryPacket with its
        position, altitude, speed, and heading as parameters.
        """
        try:
            r = requests.get(OPENSKY_URL, timeout=self.timeout, params={
                "lamin": self.bbox[0], "lamax": self.bbox[1],
                "lomin": self.bbox[2], "lomax": self.bbox[3],
            })
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise DataSourceUnavailableError(OPENSKY_URL, str(e))

        states = data.get("states", []) or []
        now = datetime.now(timezone.utc)

        for i, state in enumerate(states[:self.max_aircraft]):
            try:
                icao24 = state[0] or "unknown"
                callsign = (state[1] or "").strip() or icao24
                origin_country = state[2] or "unknown"
                last_contact = state[4] or 0
                longitude = state[5]
                latitude = state[6]
                baro_altitude = state[7] or 0.0
                on_ground = state[8] or False
                velocity = state[9] or 0.0
                true_track = state[10] or 0.0
                vertical_rate = state[11] or 0.0
                geo_altitude = state[13] or 0.0
                squawk = state[14] or ""

                if longitude is None or latitude is None:
                    continue
                if on_ground:
                    continue

                ts = datetime.fromtimestamp(last_contact, tz=timezone.utc) if last_contact else now

                if ts < start_time or ts > end_time:
                    ts = now

                raw = struct.pack(
                    ">ffffff",
                    float(latitude), float(longitude),
                    float(baro_altitude), float(velocity),
                    float(true_track), float(vertical_rate),
                )

                yield TelemetryPacket.create(
                    spacecraft_id=f"AIRCRAFT_{icao24.upper()}",
                    packet_type=PacketType.TELEMETRY,
                    apid=APID_AIRCRAFT_STATE,
                    sequence_count=i % 16384,
                    raw_bytes=raw,
                    parameters={
                        "icao24": icao24,
                        "callsign": callsign,
                        "country": origin_country,
                        "latitude_deg": round(float(latitude), 4),
                        "longitude_deg": round(float(longitude), 4),
                        "baro_altitude_m": round(float(baro_altitude), 1),
                        "geo_altitude_m": round(float(geo_altitude), 1),
                        "velocity_ms": round(float(velocity), 1),
                        "true_track_deg": round(float(true_track), 1),
                        "vertical_rate_ms": round(float(vertical_rate), 2),
                        "squawk": squawk,
                        "data_type": "aircraft_state",
                    },
                    source=self.source_name,
                    timestamp=ts,
                )
            except Exception:
                continue