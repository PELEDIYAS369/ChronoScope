# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — ACE Spacecraft Ingester
Advanced Composition Explorer — solar wind backup to DSCOVR.
Public data from NOAA SWPC. No API key required.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterator
import requests
import structlog

from src.chronoscope.domain.models import TelemetryPacket, PacketType
from src.chronoscope.domain.exceptions import DataSourceUnavailableError, PacketParseError
from src.chronoscope.ingestion.base import BaseIngester

logger = structlog.get_logger(__name__)

ACE_SWEPAM_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json"
ACE_MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-7-day.json"
SPACECRAFT_ACE = "ACE"
APID_ACE_PLASMA = 0x70
APID_ACE_MAG = 0x71


class ACEIngester(BaseIngester):
    """
    Ingests solar wind data attributed to ACE spacecraft.
    ACE is at L1 alongside DSCOVR — complementary measurements.
    """

    def __init__(self, timeout_seconds: int = 30):
        super().__init__(source_name="ace_spacecraft")
        self.timeout = timeout_seconds

    def is_available(self) -> bool:
        try:
            r = requests.get(ACE_SWEPAM_URL, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def get_available_spacecraft(self) -> list[str]:
        return [SPACECRAFT_ACE]

    def fetch_packets(
        self,
        spacecraft_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        if spacecraft_id != SPACECRAFT_ACE:
            return
        yield from self._fetch_plasma(start_time, end_time)
        yield from self._fetch_mag(start_time, end_time)

    def _fetch_plasma(self, start_time, end_time) -> Iterator[TelemetryPacket]:
        try:
            r = requests.get(ACE_SWEPAM_URL, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise DataSourceUnavailableError(ACE_SWEPAM_URL, str(e))

        if len(data) < 2:
            return

        seq = 0
        for record in data[1:]:
            try:
                ts = datetime.strptime(record[0], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                if ts < start_time or ts > end_time:
                    continue
                density = self._safe_float(record[1])
                speed = self._safe_float(record[2])
                temp = self._safe_float(record[3])
                import struct
                raw = struct.pack(">fff", density, speed, temp)
                yield TelemetryPacket.create(
                    spacecraft_id=SPACECRAFT_ACE,
                    packet_type=PacketType.TELEMETRY,
                    apid=APID_ACE_PLASMA,
                    sequence_count=seq % 16384,
                    raw_bytes=raw,
                    parameters={
                        "proton_density_n_cc": density,
                        "bulk_speed_km_s": speed,
                        "ion_temperature_k": temp,
                        "data_type": "plasma",
                    },
                    source=self.source_name,
                    timestamp=ts,
                )
                seq += 1
            except Exception:
                continue

    def _fetch_mag(self, start_time, end_time) -> Iterator[TelemetryPacket]:
        try:
            r = requests.get(ACE_MAG_URL, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise DataSourceUnavailableError(ACE_MAG_URL, str(e))

        if len(data) < 2:
            return

        seq = 0
        for record in data[1:]:
            try:
                ts = datetime.strptime(record[0], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                if ts < start_time or ts > end_time:
                    continue
                bx = self._safe_float(record[1])
                by = self._safe_float(record[2])
                bz = self._safe_float(record[3])
                bt = self._safe_float(record[4])
                import struct
                raw = struct.pack(">ffff", bx, by, bz, bt)
                yield TelemetryPacket.create(
                    spacecraft_id=SPACECRAFT_ACE,
                    packet_type=PacketType.TELEMETRY,
                    apid=APID_ACE_MAG,
                    sequence_count=seq % 16384,
                    raw_bytes=raw,
                    parameters={
                        "bx_gsm_nt": bx, "by_gsm_nt": by,
                        "bz_gsm_nt": bz, "bt_nt": bt,
                        "data_type": "magnetic",
                    },
                    source=self.source_name,
                    timestamp=ts,
                )
                seq += 1
            except Exception:
                continue

    def _safe_float(self, v) -> float:
        try:
            return float(v) if v not in (None, "") else 0.0
        except (ValueError, TypeError):
            return 0.0