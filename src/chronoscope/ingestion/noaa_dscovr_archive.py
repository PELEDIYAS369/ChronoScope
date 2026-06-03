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

# Last date for which the definitive Faraday Cup plasma dataset (DSCOVR_H1_FC)
# is published on CDAWeb. The dataset stopped being updated after the DSCOVR
# safe-mode incident in June 2019; later definitive plasma data is archived
# elsewhere (NOAA NCEI). The MAG dataset (DSCOVR_H0_MAG) is unaffected and
# continues to be updated.
DSCOVR_H1_FC_END_DATE = datetime(2019, 6, 28, tzinfo=timezone.utc)

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
    """
    Parse a HAPI ISO 8601 time string to a UTC datetime.

    HAPI's restricted ISO 8601 allows fractional seconds of arbitrary
    precision. CDAWeb has been observed to emit nanosecond precision
    (e.g. ``2018-03-15T00:00:00.000000000Z``, 9 fractional digits) for
    DSCOVR_H1_FC plasma records, while Python's ``strptime`` ``%f``
    directive only accepts up to 6. The previous parser rejected every
    nanosecond-precision row, silently zeroing out plasma ingest.

    This implementation normalises the string and delegates to
    ``datetime.fromisoformat``, which is permissive enough to handle
    every form HAPI is allowed to emit.

    Tolerated forms:
      * ``YYYY-MM-DDTHH:MM:SSZ``                       (no fraction)
      * ``YYYY-MM-DDTHH:MM:SS.sssZ``                   (milliseconds)
      * ``YYYY-MM-DDTHH:MM:SS.ssssssZ``                (microseconds)
      * ``YYYY-MM-DDTHH:MM:SS.sssssssssZ``             (nanoseconds)
      * Any other fractional-second precision the HAPI spec permits.

    Sub-microsecond precision is truncated (not rounded) because Python's
    ``datetime`` does not carry it. That is acceptable for DSCOVR: MAG
    cadence is 1 s and plasma cadence is 1 min, so nanoseconds are noise.
    """
    s = s.strip()

    # Strip trailing ``Z`` so ``fromisoformat`` is happy on Python < 3.11.
    body = s[:-1] if s.endswith("Z") else s

    # If there are fractional seconds, truncate to microsecond precision.
    if "." in body:
        head, frac = body.split(".", 1)
        frac_digits = frac[:6].ljust(6, "0")
        body = f"{head}.{frac_digits}"

    try:
        dt = datetime.fromisoformat(body)
    except ValueError as e:
        raise PacketParseError(f"Unrecognized HAPI timestamp: {s!r}", None) from e

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

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
        # DSCOVR_H1_FC ends 2019-06-27. If the entire requested window is past
        # that date, save the HTTP round-trip and surface a clear warning so
        # nobody thinks they got an empty corpus due to a bug.
        if start_time >= DSCOVR_H1_FC_END_DATE:
            self.logger.warning(
                "plasma_request_after_h1_fc_end_date",
                requested_start=start_time.isoformat(),
                h1_fc_end_date=DSCOVR_H1_FC_END_DATE.isoformat(),
                note=(
                    "DSCOVR_H1_FC was not updated past 2019-06-27. "
                    "Post-2019 definitive plasma data is archived elsewhere."
                ),
            )
            return

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

        Column order (VERIFIED against live CDAWeb HAPI /info on 2026-05-26;
        see DECISIONS.md DEC-005):
          [0]  Time          — ISO 8601
          [1]  DQF           — data quality flag (0 = good)
          [2]  V_GSE[0]      — Vx in GSE (km/s)
          [3]  V_GSE[1]      — Vy in GSE (km/s)
          [4]  V_GSE[2]      — Vz in GSE (km/s)
          [5]  V_GSE_ErrorBars[0]     — (skipped — uncertainty)
          [6]  V_GSE_ErrorBars[1]     — (skipped)
          [7]  V_GSE_ErrorBars[2]     — (skipped)
          [8]  THERMAL_SPD            — proton thermal speed (km/s)
          [9]  THERMAL_SPD_ErrorBars  — (skipped)
          [10] Np                     — proton density (cm^-3)
          [11] Np_ErrorBars           — (skipped)
          [12] THERMAL_TEMP           — proton temperature (K) — published directly
          [13] THERMAL_TEMP_ErrorBars — (skipped)

        HAPI vector parameters (e.g. V_GSE with size=[3]) are flattened into
        three consecutive CSV columns, which is why what looks like 6 named
        parameters in the /info response becomes 14 CSV columns.

        HAPI fill value for plasma doubles is -1.0E31. Fill values are
        preserved in the packet parameters so downstream code can decide how
        to handle them; a separate filter pass should drop fill rows before
        training. The DQF flag is also preserved for the same reason.
        """
        if len(row) < 14:
            raise PacketParseError(
                f"Plasma row has {len(row)} columns, expected >= 14", None
            )

        timestamp = _parse_hapi_time(row[0])
        dqf = self._safe_float(row[1])
        vx_gse = self._safe_float(row[2])
        vy_gse = self._safe_float(row[3])
        vz_gse = self._safe_float(row[4])
        # row[5..7] are V error bars — skipped
        thermal_speed = self._safe_float(row[8])
        # row[9] is thermal speed error bar — skipped
        density = self._safe_float(row[10])
        # row[11] is density error bar — skipped
        ion_temperature_k = self._safe_float(row[12])
        # row[13] is temperature error bar — skipped

        # Magnitude of velocity (matches live ingester's `bulk_speed_km_s`)
        bulk_speed = math.sqrt(vx_gse * vx_gse + vy_gse * vy_gse + vz_gse * vz_gse)

        parameters = {
            "proton_density_n_cc": density,
            "bulk_speed_km_s": bulk_speed,
            "ion_temperature_k": ion_temperature_k,
            "vx_gse_km_s": vx_gse,
            "vy_gse_km_s": vy_gse,
            "vz_gse_km_s": vz_gse,
            "thermal_speed_km_s": thermal_speed,
            "data_quality_flag": dqf,
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
        Parse one HAPI CSV row from DSCOVR_H0_MAG into a TelemetryPacket.

        Column order (VERIFIED against live CDAWeb HAPI /info on 2026-05-26;
        see DECISIONS.md DEC-005):
          [0]  Time            — ISO 8601
          [1]  B1F1            — |B| magnitude (nT)
          [2]  B1SDF1          — stddev of |B|  (skipped)
          [3]  B1GSE[0]        — Bx in GSE (nT)
          [4]  B1GSE[1]        — By in GSE (nT)
          [5]  B1GSE[2]        — Bz in GSE (nT)
          [6]  B1SDGSE[0]      — stddev (skipped)
          [7]  B1SDGSE[1]      — stddev (skipped)
          [8]  B1SDGSE[2]      — stddev (skipped)
          [9]  B1RTN[0]        — Br RTN  (skipped — we use GSE)
          [10] B1RTN[1]        — Bt RTN  (skipped)
          [11] B1RTN[2]        — Bn RTN  (skipped)
          [12] B1SDRTN[0]      — stddev (skipped)
          [13] B1SDRTN[1]      — stddev (skipped)
          [14] B1SDRTN[2]      — stddev (skipped)

        Like the plasma dataset, vector parameters with size=[3] flatten into
        three CSV columns, so 7 named parameters in /info become 15 CSV columns.

        Note: archive uses GSE, distinct from live SWPC feed which is GSM. We
        store under explicitly-named GSE keys to keep the coordinate frame
        unambiguous in downstream code. Fill value is -1.0E31.
        """
        if len(row) < 6:
            # Bare minimum to extract |B| + GSE vector
            raise PacketParseError(
                f"Magnetic row has {len(row)} columns, expected >= 6", None
            )

        timestamp = _parse_hapi_time(row[0])
        bt = self._safe_float(row[1])
        # row[2] is B1SDF1 stddev of |B| — skipped
        bx_gse = self._safe_float(row[3])
        by_gse = self._safe_float(row[4])
        bz_gse = self._safe_float(row[5])

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