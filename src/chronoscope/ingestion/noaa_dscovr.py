"""
ChronoScope AI — NOAA DSCOVR Solar Wind Ingester
Pulls real solar wind telemetry from NOAA's public API.
DSCOVR (Deep Space Climate Observatory) is a real NASA/NOAA spacecraft
at the L1 Lagrange point measuring solar wind in real time.
This is live, real, public data — no synthetic data anywhere here.
Data source: https://services.swpc.noaa.gov/products/solar-wind/
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterator
import requests
import structlog

from src.chronoscope.domain.models import (
    TelemetryPacket,
    PacketType,
)
from src.chronoscope.domain.constants import (
    NOAA_DSCOVR_URL,
    SPACECRAFT_DSCOVR,
)
from src.chronoscope.domain.exceptions import (
    DataSourceUnavailableError,
    PacketParseError,
)
from src.chronoscope.ingestion.base import BaseIngester

logger = structlog.get_logger(__name__)

# NOAA DSCOVR public endpoints — no API key required
DSCOVR_PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json"
DSCOVR_MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-7-day.json"
DSCOVR_HEALTH_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-2-hour.json"

# APID assignments for DSCOVR parameters
APID_PLASMA = 0x64        # 100 decimal — plasma measurements
APID_MAGNETIC = 0x65      # 101 decimal — magnetic field measurements


class NOAADscovrIngester(BaseIngester):
    """
    Ingests real solar wind data from NOAA's DSCOVR spacecraft.

    DSCOVR measures:
    - Solar wind plasma: density, speed, temperature
    - Magnetic field: Bx, By, Bz components, Bt total field

    This is the same data used by NOAA for space weather alerts
    and by NASA for real mission planning decisions.
    """

    def __init__(self, timeout_seconds: int = 30):
        super().__init__(source_name="noaa_dscovr")
        self.timeout = timeout_seconds

    def is_available(self) -> bool:
        """Check if NOAA DSCOVR endpoint is reachable."""
        try:
            response = requests.get(
                DSCOVR_HEALTH_URL,
                timeout=10,
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_available_spacecraft(self) -> list[str]:
        """DSCOVR is the only spacecraft on this source."""
        return [SPACECRAFT_DSCOVR]

    def fetch_packets(
        self,
        spacecraft_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """
        Fetch real DSCOVR telemetry and yield as TelemetryPackets.
        Yields plasma packets first, then magnetic field packets.
        All packets are sorted chronologically within each type.
        """
        if spacecraft_id != SPACECRAFT_DSCOVR:
            self.logger.warning(
                "unsupported_spacecraft",
                spacecraft_id=spacecraft_id,
                supported=SPACECRAFT_DSCOVR,
            )
            return

        self.logger.info(
            "fetching_dscovr_data",
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

        # Yield plasma packets
        yield from self._fetch_plasma_packets(start_time, end_time)

        # Yield magnetic field packets
        yield from self._fetch_magnetic_packets(start_time, end_time)

    def _fetch_plasma_packets(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """Fetch and parse solar wind plasma measurements."""
        try:
            response = requests.get(DSCOVR_PLASMA_URL, timeout=self.timeout)
            response.raise_for_status()
            raw_data = response.json()
        except requests.RequestException as e:
            raise DataSourceUnavailableError(DSCOVR_PLASMA_URL, str(e))

        # First row is headers: time_tag, density, speed, temperature
        if len(raw_data) < 2:
            self.logger.warning("plasma_data_empty")
            return

        headers = raw_data[0]
        records = raw_data[1:]

        sequence = 0
        for record in records:
            try:
                packet = self._parse_plasma_record(
                    record, headers, sequence, start_time, end_time
                )
                if packet is not None:
                    yield packet
                    sequence += 1
            except PacketParseError as e:
                self.logger.warning("plasma_parse_failed", error=str(e))
                continue

    def _fetch_magnetic_packets(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """Fetch and parse solar wind magnetic field measurements."""
        try:
            response = requests.get(DSCOVR_MAG_URL, timeout=self.timeout)
            response.raise_for_status()
            raw_data = response.json()
        except requests.RequestException as e:
            raise DataSourceUnavailableError(DSCOVR_MAG_URL, str(e))

        if len(raw_data) < 2:
            self.logger.warning("magnetic_data_empty")
            return

        headers = raw_data[0]
        records = raw_data[1:]

        sequence = 0
        for record in records:
            try:
                packet = self._parse_magnetic_record(
                    record, headers, sequence, start_time, end_time
                )
                if packet is not None:
                    yield packet
                    sequence += 1
            except PacketParseError as e:
                self.logger.warning("magnetic_parse_failed", error=str(e))
                continue

    def _parse_plasma_record(
        self,
        record: list,
        headers: list,
        sequence: int,
        start_time: datetime,
        end_time: datetime,
    ) -> TelemetryPacket | None:
        """Parse a single plasma data record into a TelemetryPacket."""
        try:
            time_tag = record[0]
            timestamp = self._parse_timestamp(time_tag)
        except Exception as e:
            raise PacketParseError(f"Invalid timestamp: {record[0]}", None)

        # Filter to requested time range
        if timestamp < start_time or timestamp > end_time:
            return None

        try:
            density = self._safe_float(record[1])
            speed = self._safe_float(record[2])
            temperature = self._safe_float(record[3])
        except (IndexError, ValueError) as e:
            raise PacketParseError(f"Plasma field parse error: {e}", None)

        parameters = {
            "proton_density_n_cc": density,
            "bulk_speed_km_s": speed,
            "ion_temperature_k": temperature,
            "data_type": "plasma",
        }

        raw = self._encode_plasma_raw(density, speed, temperature)

        return TelemetryPacket.create(
            spacecraft_id=SPACECRAFT_DSCOVR,
            packet_type=PacketType.TELEMETRY,
            apid=APID_PLASMA,
            sequence_count=sequence % 16384,
            raw_bytes=raw,
            parameters=parameters,
            source=self.source_name,
            timestamp=timestamp,
        )

    def _parse_magnetic_record(
        self,
        record: list,
        headers: list,
        sequence: int,
        start_time: datetime,
        end_time: datetime,
    ) -> TelemetryPacket | None:
        """Parse a single magnetic field record into a TelemetryPacket."""
        try:
            time_tag = record[0]
            timestamp = self._parse_timestamp(time_tag)
        except Exception as e:
            raise PacketParseError(f"Invalid timestamp: {record[0]}", None)

        if timestamp < start_time or timestamp > end_time:
            return None

        try:
            bx = self._safe_float(record[1])
            by = self._safe_float(record[2])
            bz = self._safe_float(record[3])
            bt = self._safe_float(record[4])
        except (IndexError, ValueError) as e:
            raise PacketParseError(f"Magnetic field parse error: {e}", None)

        parameters = {
            "bx_gsm_nt": bx,
            "by_gsm_nt": by,
            "bz_gsm_nt": bz,
            "bt_nt": bt,
            "data_type": "magnetic",
        }

        raw = self._encode_magnetic_raw(bx, by, bz, bt)

        return TelemetryPacket.create(
            spacecraft_id=SPACECRAFT_DSCOVR,
            packet_type=PacketType.TELEMETRY,
            apid=APID_MAGNETIC,
            sequence_count=sequence % 16384,
            raw_bytes=raw,
            parameters=parameters,
            source=self.source_name,
            timestamp=timestamp,
        )

    def _parse_timestamp(self, time_tag: str) -> datetime:
        """Parse NOAA time tag string to UTC datetime."""
        # NOAA format: "2024-01-15 12:00:00.000"
        ts = datetime.strptime(time_tag, "%Y-%m-%d %H:%M:%S.%f")
        return ts.replace(tzinfo=timezone.utc)

    def _safe_float(self, value: object) -> float:
        """Convert value to float, returning 0.0 for None or invalid."""
        if value is None or value == "":
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def _encode_plasma_raw(
        self,
        density: float,
        speed: float,
        temperature: float,
    ) -> bytes:
        """
        Encode plasma parameters as raw bytes.
        Simple big-endian IEEE 754 encoding — 4 bytes per float.
        """
        import struct
        return struct.pack(">fff", density, speed, temperature)

    def _encode_magnetic_raw(
        self,
        bx: float,
        by: float,
        bz: float,
        bt: float,
    ) -> bytes:
        """Encode magnetic field parameters as raw bytes."""
        import struct
        return struct.pack(">ffff", bx, by, bz, bt)