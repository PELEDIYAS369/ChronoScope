# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — NOAA DSCOVR Historical Archive Ingester
Pulls definitive (science-quality) DSCOVR telemetry from the NASA SPDF/CDAWeb
HAPI server. This is the historical-corpus counterpart to noaa_dscovr.py, which
only handles the live 7-day rolling SWPC feed.

Datasets accessed:
  - DSCOVR_H0_MAG  — 1-second fluxgate magnetometer (Bx/By/Bz GSE, magnitude)
  - DSCOVR_H1_FC   — 1-minute Faraday Cup solar wind plasma
                     (proton density, V_GSE, thermal speed)

DSCOVR became operational on 2016-07-27. Data before that date is ground-test
data and should not be used for space-environment analysis.

Access protocol: HAPI v2 (Heliophysics Application Programmer's Interface)
HAPI base:  https://cdaweb.gsfc.nasa.gov/hapi/
Spec:       https://github.com/hapi-server/data-specification

This ingester deliberately uses HAPI CSV (default format) rather than CDF binary
files. See DEC-004 for the reasoning.

Coordinate-frame note: archive MAG data is published in GSE coordinates, while
the live SWPC ingester (noaa_dscovr.py) publishes in GSM. To keep the data
honest, this ingester writes archive packets with `bx_gse_nt` / `by_gse_nt` /
`bz_gse_nt` parameter keys (distinct from the live ingester's `bx_gsm_nt` etc.).
A coordinate-conversion layer can be added later if cross-source comparison
becomes necessary.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from typing import Iterator

import requests
import structlog

from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR
from src.chronoscope.domain.exceptions import (
    DataSourceUnavailableError,
    PacketParseError,
)
from src.chronoscope.domain.models import PacketType, TelemetryPacket
from src.chronoscope.ingestion.base import BaseIngester

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# HAPI endpoint configuration
# ---------------------------------------------------------------------------

HAPI_BASE_URL = "https://cdaweb.gsfc.nasa.gov/hapi"
HAPI_DATA_ENDPOINT = f"{HAPI_BASE_URL}/data"
HAPI_INFO_ENDPOINT = f"{HAPI_BASE_URL}/info"
HAPI_CAPABILITIES_ENDPOINT = f"{HAPI_BASE_URL}/capabilities"

# Dataset identifiers in the CDAWeb HAPI catalog
DATASET_MAG = "DSCOVR_H0_MAG"
DATASET_FC = "DSCOVR_H1_FC"

# APIDs match the live noaa_dscovr ingester — same physical data, different
# source pipeline. Downstream consumers distinguish via the `source` field.
APID_PLASMA = 0x64        # 100 decimal — plasma measurements
APID_MAGNETIC = 0x65      # 101 decimal — magnetic field measurements

# Earliest date with valid space-environment data (post-commissioning)
DSCOVR_OPERATIONAL_DATE = datetime(2016, 7, 27, tzinfo=timezone.utc)

# HAPI time format — restricted ISO 8601 with millisecond precision and Z
_HAPI_TIME_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _to_hapi_time(t: datetime) -> str:
    """Serialize a datetime to HAPI's restricted ISO 8601 form."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    else:
        t = t.astimezone(timezone.utc)
    # %f gives microseconds (6 digits); HAPI accepts that
    return t.strftime(_HAPI_TIME_FMT)


def _parse_hapi_time(s: str) -> datetime:
    """Parse a HAPI ISO 8601 time string to a UTC datetime."""
    s = s.strip()
    # Accept both with and without fractional seconds
    # HAPI canonical form is "YYYY-MM-DDTHH:MM:SS.sssZ"
    try:
        return datetime.strptime(s, _HAPI_TIME_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise PacketParseError(f"Unrecognized HAPI timestamp: {s!r}", None) from e


# ---------------------------------------------------------------------------
# Ingester
# ---------------------------------------------------------------------------


class NOAADscovrArchiveIngester(BaseIngester):
    """
    Historical DSCOVR ingester using the NASA SPDF/CDAWeb HAPI server.

    Yields TelemetryPacket objects with the same shape as the live
    NOAADscovrIngester, so downstream consumers (replay, audit, anomaly
    detection, future causal engine) work unchanged.

    Determinism: identical (start_time, end_time, spacecraft_id) inputs yield
    the same packet sequence, modulo upstream NOAA reprocessing — which is rare
    for definitive (H0/H1) data but can occur. The packet_id is a per-call UUID
    so byte-equality of packets across runs is NOT guaranteed; deterministic
    replay relies on (timestamp, parameters), not packet_id.
    """

    def __init__(self, timeout_seconds: int = 60):
        super().__init__(source_name="noaa_dscovr_archive")
        self.timeout = timeout_seconds

    # ------------------------------------------------------------------
    # BaseIngester contract
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Probe the HAPI capabilities endpoint."""
        try:
            response = requests.get(HAPI_CAPABILITIES_ENDPOINT, timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    def get_available_spacecraft(self) -> list[str]:
        """Only DSCOVR is supported on this source."""
        return [SPACECRAFT_DSCOVR]

    def fetch_packets(
        self,
        spacecraft_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """
        Yield all available DSCOVR packets in the requested time range.

        Order: plasma packets first (1-min cadence), then magnetic field
        packets (1-sec cadence). Within each type, chronological.
        """
        if spacecraft_id != SPACECRAFT_DSCOVR:
            self.logger.warning(
                "unsupported_spacecraft",
                spacecraft_id=spacecraft_id,
                supported=SPACECRAFT_DSCOVR,
            )
            return

        # Clamp the requested window to the operational period. Returning
        # pre-operational ground-test data would silently poison any training
        # corpus built on top.
        clamped_start = max(start_time, DSCOVR_OPERATIONAL_DATE)
        if clamped_start >= end_time:
            self.logger.warning(
                "request_outside_operational_window",
                requested_start=start_time.isoformat(),
                operational_start=DSCOVR_OPERATIONAL_DATE.isoformat(),
            )
            return

        self.logger.info(
            "fetching_dscovr_archive",
            start_time=clamped_start.isoformat(),
            end_time=end_time.isoformat(),
        )

        yield from self._fetch_plasma_packets(clamped_start, end_time)
        yield from self._fetch_magnetic_packets(clamped_start, end_time)

    # ------------------------------------------------------------------
    # Plasma (Faraday Cup) — DSCOVR_H1_FC
    # ------------------------------------------------------------------

    def _fetch_plasma_packets(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """Fetch and parse Faraday Cup plasma records."""
        rows = self._fetch_hapi_csv(DATASET_FC, start_time, end_time)
        sequence = 0
        for row in rows:
            try:
                packet = self._parse_plasma_row(row, sequence)
            except PacketParseError as e:
                self.logger.warning("plasma_parse_failed", error=str(e), row=row)
                continue
            if packet is not None:
                yield packet
                sequence += 1

    def _parse_plasma_row(
        self,
        row: list[str],
        sequence: int,
    ) -> TelemetryPacket | None:
        """
        Parse one HAPI CSV row from DSCOVR_H1_FC into a TelemetryPacket.

        Expected column order (HAPI info confirms; we follow the documented
        DSCOVR_H1_FC schema):
          [0] Epoch (ISO 8601)
          [1] Np                 — proton density (cm^-3)
          [2] V_GSE_X            — bulk velocity X in GSE (km/s)
          [3] V_GSE_Y            — bulk velocity Y in GSE (km/s)
          [4] V_GSE_Z            — bulk velocity Z in GSE (km/s)
          [5] THERMAL_SPD        — thermal speed (km/s)

        If thermal speed is published instead of temperature, we convert
        T_ion = m_p * v_th^2 / (2 * k_B) so we can store the same
        `ion_temperature_k` parameter the live ingester uses.
        """
        if len(row) < 6:
            raise PacketParseError(
                f"Plasma row has {len(row)} columns, expected >= 6", None
            )

        timestamp = _parse_hapi_time(row[0])
        density = self._safe_float(row[1])
        vx_gse = self._safe_float(row[2])
        vy_gse = self._safe_float(row[3])
        vz_gse = self._safe_float(row[4])
        thermal_speed = self._safe_float(row[5])

        # Magnitude of velocity (matches live ingester's `bulk_speed_km_s`)
        bulk_speed = math.sqrt(vx_gse * vx_gse + vy_gse * vy_gse + vz_gse * vz_gse)

        # Thermal speed -> ion temperature (K).
        # T = m_p * v_th^2 / (2 * k_B); m_p / (2 * k_B) ≈ 60.5 K / (km/s)^2
        ion_temperature_k = (thermal_speed * thermal_speed) * 60.5

        parameters = {
            "proton_density_n_cc": density,
            "bulk_speed_km_s": bulk_speed,
            "ion_temperature_k": ion_temperature_k,
            "vx_gse_km_s": vx_gse,
            "vy_gse_km_s": vy_gse,
            "vz_gse_km_s": vz_gse,
            "thermal_speed_km_s": thermal_speed,
            "data_type": "plasma",
            "data_level": "definitive",
            "archive_dataset": DATASET_FC,
        }

        raw = self._encode_plasma_raw(density, bulk_speed, ion_temperature_k)

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

    # ------------------------------------------------------------------
    # Magnetic field — DSCOVR_H0_MAG
    # ------------------------------------------------------------------

    def _fetch_magnetic_packets(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """Fetch and parse fluxgate magnetometer records."""
        rows = self._fetch_hapi_csv(DATASET_MAG, start_time, end_time)
        sequence = 0
        for row in rows:
            try:
                packet = self._parse_magnetic_row(row, sequence)
            except PacketParseError as e:
                self.logger.warning("magnetic_parse_failed", error=str(e), row=row)
                continue
            if packet is not None:
                yield packet
                sequence += 1

    def _parse_magnetic_row(
        self,
        row: list[str],
        sequence: int,
    ) -> TelemetryPacket | None:
        """
        Parse one HAPI CSV row from DSCOVR_H0_MAG.

        Expected column order:
          [0] Epoch (ISO 8601)
          [1] B1F1        — magnitude |B| (nT)
          [2] B1GSE_X     — Bx in GSE (nT)
          [3] B1GSE_Y     — By in GSE (nT)
          [4] B1GSE_Z     — Bz in GSE (nT)

        Note: archive uses GSE, distinct from live SWPC feed which is GSM.
        We store under explicitly-named GSE keys.
        """
        if len(row) < 5:
            raise PacketParseError(
                f"Magnetic row has {len(row)} columns, expected >= 5", None
            )

        timestamp = _parse_hapi_time(row[0])
        bt = self._safe_float(row[1])
        bx_gse = self._safe_float(row[2])
        by_gse = self._safe_float(row[3])
        bz_gse = self._safe_float(row[4])

        parameters = {
            "bx_gse_nt": bx_gse,
            "by_gse_nt": by_gse,
            "bz_gse_nt": bz_gse,
            "bt_nt": bt,
            "data_type": "magnetic",
            "data_level": "definitive",
            "archive_dataset": DATASET_MAG,
        }

        raw = self._encode_magnetic_raw(bx_gse, by_gse, bz_gse, bt)

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

    # ------------------------------------------------------------------
    # HAPI transport
    # ------------------------------------------------------------------

    def _fetch_hapi_csv(
        self,
        dataset_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[list[str]]:
        """
        Issue a HAPI /data request and return parsed CSV rows.

        HAPI CSV default format: no header row. First column is always Epoch.
        Subsequent columns are the dataset's parameters in the order returned
        by the /info endpoint. We trust that order here; tests use the same
        documented column order. A future hardening pass can fetch /info,
        validate the schema, and remap if needed.
        """
        params = {
            "id": dataset_id,
            "time.min": _to_hapi_time(start_time),
            "time.max": _to_hapi_time(end_time),
            "format": "csv",
        }

        try:
            response = requests.get(
                HAPI_DATA_ENDPOINT,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise DataSourceUnavailableError(HAPI_DATA_ENDPOINT, str(e))

        body = response.text
        if not body.strip():
            self.logger.info("hapi_empty_response", dataset=dataset_id)
            return []

        # HAPI CSV per spec: no comment lines for default (no header) responses,
        # but be defensive — drop blank/comment lines if any server includes them.
        reader = csv.reader(io.StringIO(body))
        rows: list[list[str]] = []
        for raw_row in reader:
            if not raw_row:
                continue
            if raw_row[0].startswith("#"):
                continue
            rows.append(raw_row)
        return rows

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_float(self, value: object) -> float:
        """
        Convert value to float, returning 0.0 for empty / None / unparseable.

        HAPI fill values are dataset-specific (often -1e31 for missing); for
        now we accept them as-is. A future filter pass can drop fill-value
        rows before they hit downstream models.
        """
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
        """Big-endian IEEE 754 — matches noaa_dscovr.py byte layout."""
        import struct
        return struct.pack(">fff", density, speed, temperature)

    def _encode_magnetic_raw(
        self,
        bx: float,
        by: float,
        bz: float,
        bt: float,
    ) -> bytes:
        """Big-endian IEEE 754 — matches noaa_dscovr.py byte layout."""
        import struct
        return struct.pack(">ffff", bx, by, bz, bt)