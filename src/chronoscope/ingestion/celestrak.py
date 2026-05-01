"""
ChronoScope AI — CelesTrak Satellite Ingester
Live satellite orbital data from CelesTrak public API.
Tracks GPS, Starlink, ISS, weather satellites, and more.
No API key required.
https://celestrak.org
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterator
import requests
import structlog

from src.chronoscope.domain.models import TelemetryPacket, PacketType
from src.chronoscope.domain.exceptions import DataSourceUnavailableError
from src.chronoscope.ingestion.base import BaseIngester

logger = structlog.get_logger(__name__)

CELESTRAK_BASE = "https://celestrak.org/SOCRATES/query.php"
CELESTRAK_TLE_URL = "https://celestrak.org/SOCRATES/query.php"

# Catalog groups available from CelesTrak
SATELLITE_GROUPS = {
    "ISS": "https://celestrak.org/SOCRATES/query.php?CATNR=25544&DAYS=7&MAX=10&SORT=1&FORMAT=json",
    "STARLINK": "https://celestrak.org/SOCRATES/query.php?GROUP=starlink&FORMAT=json",
    "GPS": "https://celestrak.org/SOCRATES/query.php?GROUP=gps-ops&FORMAT=json",
    "WEATHER": "https://celestrak.org/SOCRATES/query.php?GROUP=weather&FORMAT=json",
}

# Simpler direct TLE endpoints
CELESTRAK_TLE_ENDPOINTS = {
    "ISS": "https://celestrak.org/satcat/records.php?CATNR=25544&FORMAT=json",
    "STARLINK": "https://celestrak.org/satcat/records.php?INTDES=2019-029&FORMAT=json",
    "WEATHER_NOAA": "https://celestrak.org/satcat/records.php?CATNR=43226&FORMAT=json",
}

APID_SATELLITE_TLE = 0x90


class CelesTrakIngester(BaseIngester):
    """
    Ingests satellite catalog data from CelesTrak.
    Provides orbital parameters for tracked satellites.
    Demonstrates ChronoScope's universal architecture for
    any flying object — spacecraft, aircraft, satellites.
    """

    def __init__(
        self,
        group: str = "ISS",
        timeout_seconds: int = 30,
    ):
        super().__init__(source_name=f"celestrak_{group.lower()}")
        self.group = group
        self.timeout = timeout_seconds

    def is_available(self) -> bool:
        try:
            r = requests.get(
                "https://celestrak.org/satcat/records.php?CATNR=25544&FORMAT=json",
                timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    def get_available_spacecraft(self) -> list[str]:
        return list(CELESTRAK_TLE_ENDPOINTS.keys())

    def fetch_packets(
        self,
        spacecraft_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """
        Fetch satellite catalog records and yield as TelemetryPackets.
        Each satellite record becomes a packet with orbital parameters.
        """
        url = CELESTRAK_TLE_ENDPOINTS.get(
            self.group,
            "https://celestrak.org/satcat/records.php?CATNR=25544&FORMAT=json"
        )

        try:
            r = requests.get(url, timeout=self.timeout)
            r.raise_for_status()
            records = r.json()
        except requests.RequestException as e:
            raise DataSourceUnavailableError(url, str(e))
        except Exception:
            records = []

        if not isinstance(records, list):
            records = [records] if records else []

        now = datetime.now(timezone.utc)

        for i, record in enumerate(records[:20]):
            try:
                sat_name = record.get("OBJECT_NAME", "UNKNOWN")
                catnr = record.get("CCSDS_OMID", record.get("NORAD_CAT_ID", str(i)))
                period = float(record.get("PERIOD", 0) or 0)
                inclination = float(record.get("INCLINATION", 0) or 0)
                apogee = float(record.get("APOGEE", 0) or 0)
                perigee = float(record.get("PERIGEE", 0) or 0)
                eccentricity = float(record.get("ECCENTRICITY", 0) or 0)
                raan = float(record.get("RA_OF_ASC_NODE", 0) or 0)

                import struct
                raw = struct.pack(
                    ">ffffff",
                    period, inclination, apogee,
                    perigee, eccentricity, raan,
                )

                yield TelemetryPacket.create(
                    spacecraft_id=f"SAT_{sat_name.replace(' ', '_').upper()[:20]}",
                    packet_type=PacketType.TELEMETRY,
                    apid=APID_SATELLITE_TLE,
                    sequence_count=i % 16384,
                    raw_bytes=raw,
                    parameters={
                        "satellite_name": sat_name,
                        "catalog_number": str(catnr),
                        "period_min": period,
                        "inclination_deg": inclination,
                        "apogee_km": apogee,
                        "perigee_km": perigee,
                        "eccentricity": eccentricity,
                        "raan_deg": raan,
                        "data_type": "orbital_elements",
                        "group": self.group,
                    },
                    source=self.source_name,
                    timestamp=now,
                )
            except Exception:
                continue