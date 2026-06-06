# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Corpus Storage Layer

Per DEC-004 the historical DSCOVR corpus lives as partitioned Parquet files on
local disk, queried via embedded DuckDB. This module encapsulates that choice
so the rest of the codebase never imports pyarrow/duckdb directly.

Layout on disk:

    {root}/
        dscovr/
            mag/
                year=2016/
                    month=07/
                        part-20160727-20160801.parquet
                        part-20160801-20160901.parquet
                ...
            plasma/
                year=2016/
                    month=07/
                        part-20160727-20160801.parquet
                ...

Filtering policy applied at write time (NOT in the ingester — the ingester
stays faithful to source; storage applies corpus policy):

  * HAPI fill values: any row where ANY observed numeric is exactly -1.0E31
    (the documented HAPI fill sentinel for DSCOVR_H0_MAG and DSCOVR_H1_FC) is
    dropped. Recorded in the write report.
  * Plasma DQF gate: plasma rows where `data_quality_flag != 0` are dropped.
    MAG has no DQF column. Recorded in the write report.

Both filters can be disabled at the call site if needed (e.g. to inspect raw
fill rates during data exploration), but the bulk-backfill default is ON.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from src.chronoscope.domain.models import TelemetryPacket

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# HAPI documented fill value for DSCOVR_H0_MAG and DSCOVR_H1_FC doubles.
# See DEC-005. Equality compare is exact — the sentinel is itself an exact
# float, not an approximation.
HAPI_FILL_SENTINEL = -1.0e31

# Physical-plausibility bounds (DEC-007). A third filter tier alongside the
# fill-value and DQF gates: rows whose physical quantities fall outside these
# generous bounds are dropped at the storage boundary so they never enter the
# corpus. These catch source-level corruption that is NOT a fill value and is
# flagged "good" (DQF=0) — e.g. negative proton densities and absurd spikes
# (observed up to 3.5e10 cm^-3) that whole-corpus validation surfaced.
#
# Bounds are deliberately generous: wide enough that no physically real L1
# solar-wind / IMF measurement is ever clipped, tight enough to reject
# garbage. See validation.py for the same bounds used in read-side checks.
# Mapping: column -> (min_inclusive, max_inclusive).
PHYSICAL_BOUNDS: dict[str, tuple[float, float]] = {
    # MAG (nT). Real |B| at L1 is single digits when quiet, <100 in extreme
    # storms; components rarely exceed a few tens. 500 is far past any real value.
    "bt_nt": (0.0, 500.0),
    "bx_gse_nt": (-500.0, 500.0),
    "by_gse_nt": (-500.0, 500.0),
    "bz_gse_nt": (-500.0, 500.0),
    # Plasma. Density never realistically exceeds ~100 cm^-3 even in the
    # densest CME sheaths; 200 leaves generous headroom. Negative = impossible.
    "proton_density_n_cc": (0.0, 200.0),
    # Solar-wind bulk speed: slow ~300, fast streams <1000 km/s. Sane band.
    "bulk_speed_km_s": (150.0, 1500.0),
    # Proton temperature is positive; 1e8 K is orders of magnitude above real.
    "ion_temperature_k": (0.0, 1.0e8),
}

# Parquet compression. zstd is the modern default — better ratio than snappy
# at comparable speed, and pyarrow ships with it.
PARQUET_COMPRESSION = "zstd"

# Instrument tags used as the first partition level.
INSTRUMENT_MAG = "mag"
INSTRUMENT_PLASMA = "plasma"

# Parameter keys carried per instrument. These mirror what the archive
# ingester writes (DEC-005). Listed explicitly here so the Parquet schema
# is stable and discoverable; adding a new parameter requires updating
# both the ingester and this list, which is the desired discipline.
MAG_PARAMETER_KEYS: tuple[str, ...] = (
    "bx_gse_nt",
    "by_gse_nt",
    "bz_gse_nt",
    "bt_nt",
)
PLASMA_PARAMETER_KEYS: tuple[str, ...] = (
    "proton_density_n_cc",
    "bulk_speed_km_s",
    "ion_temperature_k",
    "vx_gse_km_s",
    "vy_gse_km_s",
    "vz_gse_km_s",
    "thermal_speed_km_s",
    "data_quality_flag",
)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class WriteReport:
    """
    Honest summary of what a single write_partitioned_parquet call did.

    Always returned (even if zero rows). The bulk-backfill script aggregates
    these so the operator gets a real picture of corpus quality, not just
    a "looks fine" log line.
    """

    instrument: str
    rows_seen: int = 0
    rows_written: int = 0
    rows_dropped_fill: int = 0
    rows_dropped_dqf: int = 0
    rows_dropped_implausible: int = 0
    rows_dropped_other: int = 0
    files_written: list[Path] = field(default_factory=list)

    @property
    def drop_rate(self) -> float:
        if self.rows_seen == 0:
            return 0.0
        dropped = self.rows_seen - self.rows_written
        return dropped / self.rows_seen


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def _schema_for(instrument: str) -> pa.Schema:
    """
    Build the pyarrow schema for a given instrument.

    Same base columns for every packet (timestamp, packet_id, spacecraft_id,
    apid, sequence_count, source, archive_dataset, data_level) plus the
    instrument-specific parameter columns.

    Timestamp is stored as microsecond-precision UTC timestamp. That gives
    enough resolution for both 1-sec MAG and 1-min plasma cadences without
    forcing nanosecond storage.
    """
    base_fields = [
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("packet_id", pa.string()),
        pa.field("spacecraft_id", pa.string()),
        pa.field("apid", pa.int32()),
        pa.field("sequence_count", pa.int32()),
        pa.field("source", pa.string()),
        pa.field("archive_dataset", pa.string()),
        pa.field("data_level", pa.string()),
    ]
    if instrument == INSTRUMENT_MAG:
        param_keys = MAG_PARAMETER_KEYS
    elif instrument == INSTRUMENT_PLASMA:
        param_keys = PLASMA_PARAMETER_KEYS
    else:
        raise ValueError(f"Unknown instrument: {instrument!r}")
    param_fields = [pa.field(k, pa.float64()) for k in param_keys]
    return pa.schema(base_fields + param_fields)


# ---------------------------------------------------------------------------
# Classification: which instrument does this packet belong to?
# ---------------------------------------------------------------------------


def _classify(packet: TelemetryPacket) -> str | None:
    """
    Return INSTRUMENT_MAG, INSTRUMENT_PLASMA, or None (skip).

    Uses the parameters' `data_type` tag set by the archive ingester. If the
    tag is missing or unrecognized, the packet is skipped (with a count) —
    we don't want to guess.
    """
    data_type = packet.parameters.get("data_type")
    if data_type == "magnetic":
        return INSTRUMENT_MAG
    if data_type == "plasma":
        return INSTRUMENT_PLASMA
    return None


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def _has_fill_value(packet: TelemetryPacket, param_keys: Sequence[str]) -> bool:
    """
    Return True if any of the listed parameter values equals the HAPI fill
    sentinel. Uses exact equality (the sentinel is itself an exact float).
    NaN values are also treated as fill — they shouldn't appear in clean
    data, but if they do we want them gone.
    """
    for key in param_keys:
        v = packet.parameters.get(key)
        if v is None:
            continue
        if not isinstance(v, (int, float)):
            continue
        if v == HAPI_FILL_SENTINEL:
            return True
        # math.isnan only takes float, so guard the cast
        try:
            if math.isnan(float(v)):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _has_bad_dqf(packet: TelemetryPacket) -> bool:
    """Plasma rows: drop anything where DQF is not exactly 0."""
    dqf = packet.parameters.get("data_quality_flag")
    if dqf is None:
        # Should not happen for archive plasma packets (DEC-005), but if it
        # does, treat as bad — fail loud rather than poison the corpus.
        return True
    return dqf != 0


def _has_implausible_value(
    packet: TelemetryPacket, param_keys: Sequence[str]
) -> bool:
    """
    Return True if any listed parameter falls outside its physical bounds
    (DEC-007). Only checks keys that appear in PHYSICAL_BOUNDS; other keys
    (e.g. velocity components, thermal speed) are left to the fill/DQF gates.

    Fill sentinels and NaN are intentionally NOT this gate's job — they're
    handled by _has_fill_value, which runs first. This gate exists for
    finite, DQF-good, non-fill values that are nonetheless physically
    impossible (negative density, 3.5e10 cm^-3 spikes, etc.).
    """
    for key in param_keys:
        bounds = PHYSICAL_BOUNDS.get(key)
        if bounds is None:
            continue
        v = packet.parameters.get(key)
        if v is None:
            continue
        if not isinstance(v, (int, float)):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(fv):
            # Leave NaN to the fill gate; don't double-count here.
            continue
        lo, hi = bounds
        if fv < lo or fv > hi:
            return True
    return False


# ---------------------------------------------------------------------------
# Per-instrument batching
# ---------------------------------------------------------------------------


def _packet_to_row(packet: TelemetryPacket, param_keys: Sequence[str]) -> dict:
    """Flatten a packet into a row dict matching the instrument schema."""
    # Normalize the timestamp to a UTC-aware datetime; pyarrow rejects naive.
    ts = packet.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    row: dict = {
        "timestamp": ts,
        "packet_id": packet.packet_id,
        "spacecraft_id": packet.spacecraft_id,
        "apid": packet.apid,
        "sequence_count": packet.sequence_count,
        "source": packet.source,
        "archive_dataset": packet.parameters.get("archive_dataset", ""),
        "data_level": packet.parameters.get("data_level", ""),
    }
    for k in param_keys:
        v = packet.parameters.get(k)
        # All parameter columns are float64; missing becomes NaN to keep
        # the schema strict.
        row[k] = float(v) if v is not None else float("nan")
    return row


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def _partition_dir(root: Path, instrument: str, ts: datetime) -> Path:
    """
    Return the directory for a given timestamp's partition.

    Layout: {root}/dscovr/{instrument}/year={YYYY}/month={MM}/
    """
    return (
        Path(root)
        / "dscovr"
        / instrument
        / f"year={ts.year:04d}"
        / f"month={ts.month:02d}"
    )


def _bucket_packets_by_partition(
    rows: Iterable[dict],
) -> dict[tuple[int, int], list[dict]]:
    """Group rows by (year, month) of the timestamp."""
    buckets: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        ts: datetime = row["timestamp"]
        key = (ts.year, ts.month)
        buckets.setdefault(key, []).append(row)
    return buckets


# ---------------------------------------------------------------------------
# Public writer
# ---------------------------------------------------------------------------


def write_partitioned_parquet(
    packets: Iterable[TelemetryPacket],
    root: Path | str,
    *,
    apply_fill_filter: bool = True,
    apply_dqf_filter: bool = True,
    apply_plausibility_filter: bool = True,
) -> dict[str, WriteReport]:
    """
    Persist packets as partitioned Parquet under `root`.

    Behavior:
      * Each packet is classified as mag/plasma via its `data_type` parameter.
        Unrecognized packets are counted under `rows_dropped_other` and skipped.
      * Fill-value rows are dropped if `apply_fill_filter` (default True).
      * Plasma rows with bad DQF are dropped if `apply_dqf_filter` (default True).
      * Rows with physically implausible values are dropped if
        `apply_plausibility_filter` (default True) — see PHYSICAL_BOUNDS / DEC-007.
      * Surviving rows are bucketed by (year, month) of their timestamp and
        each bucket is written as a single Parquet file. Filename encodes the
        timestamp range: `part-{first_ts}-{last_ts}.parquet`.
      * Writes are idempotent at the (instrument, year, month, time-range)
        level: the same input always produces the same filename and overwrites
        any existing file with that name.

    Returns: a dict keyed by instrument tag, each value a WriteReport. Both
    'mag' and 'plasma' keys are always present (with zero counts if no rows
    of that instrument were seen) — makes downstream aggregation simpler.
    """
    root_path = Path(root)
    mag_report = WriteReport(instrument=INSTRUMENT_MAG)
    plasma_report = WriteReport(instrument=INSTRUMENT_PLASMA)

    mag_rows: list[dict] = []
    plasma_rows: list[dict] = []
    unclassified_count = 0

    for packet in packets:
        instrument = _classify(packet)
        if instrument is None:
            # Unclassified — count separately, don't attribute to either
            # instrument's stats (it wouldn't have been a row of either).
            unclassified_count += 1
            continue

        if instrument == INSTRUMENT_MAG:
            mag_report.rows_seen += 1
            if apply_fill_filter and _has_fill_value(packet, MAG_PARAMETER_KEYS):
                mag_report.rows_dropped_fill += 1
                continue
            if apply_plausibility_filter and _has_implausible_value(
                packet, MAG_PARAMETER_KEYS
            ):
                mag_report.rows_dropped_implausible += 1
                continue
            mag_rows.append(_packet_to_row(packet, MAG_PARAMETER_KEYS))
        else:  # plasma
            plasma_report.rows_seen += 1
            if apply_fill_filter and _has_fill_value(
                packet, PLASMA_PARAMETER_KEYS
            ):
                plasma_report.rows_dropped_fill += 1
                continue
            if apply_dqf_filter and _has_bad_dqf(packet):
                plasma_report.rows_dropped_dqf += 1
                continue
            if apply_plausibility_filter and _has_implausible_value(
                packet, PLASMA_PARAMETER_KEYS
            ):
                plasma_report.rows_dropped_implausible += 1
                continue
            plasma_rows.append(_packet_to_row(packet, PLASMA_PARAMETER_KEYS))

    _flush_instrument(
        rows=mag_rows,
        instrument=INSTRUMENT_MAG,
        schema=_schema_for(INSTRUMENT_MAG),
        root=root_path,
        report=mag_report,
    )
    _flush_instrument(
        rows=plasma_rows,
        instrument=INSTRUMENT_PLASMA,
        schema=_schema_for(INSTRUMENT_PLASMA),
        root=root_path,
        report=plasma_report,
    )

    logger.info(
        "corpus_write_complete",
        mag_written=mag_report.rows_written,
        mag_dropped_fill=mag_report.rows_dropped_fill,
        mag_dropped_implausible=mag_report.rows_dropped_implausible,
        plasma_written=plasma_report.rows_written,
        plasma_dropped_fill=plasma_report.rows_dropped_fill,
        plasma_dropped_dqf=plasma_report.rows_dropped_dqf,
        plasma_dropped_implausible=plasma_report.rows_dropped_implausible,
        unclassified_packets=unclassified_count,
        files=len(mag_report.files_written) + len(plasma_report.files_written),
    )

    return {INSTRUMENT_MAG: mag_report, INSTRUMENT_PLASMA: plasma_report}


def _flush_instrument(
    *,
    rows: list[dict],
    instrument: str,
    schema: pa.Schema,
    root: Path,
    report: WriteReport,
) -> None:
    """Write one instrument's accumulated rows to disk, partitioned."""
    if not rows:
        return

    buckets = _bucket_packets_by_partition(rows)
    for (year, month), bucket_rows in sorted(buckets.items()):
        # Sort rows within the bucket by timestamp so the filename's encoded
        # range is meaningful and reads are well-ordered.
        bucket_rows.sort(key=lambda r: r["timestamp"])
        first_ts = bucket_rows[0]["timestamp"]
        last_ts = bucket_rows[-1]["timestamp"]

        partition_dir = _partition_dir(
            root, instrument, datetime(year, month, 1, tzinfo=timezone.utc)
        )
        partition_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"part-{first_ts.strftime('%Y%m%dT%H%M%S')}"
            f"-{last_ts.strftime('%Y%m%dT%H%M%S')}.parquet"
        )
        out_path = partition_dir / filename

        # Build a pyarrow Table from the column-oriented data so we can
        # specify the schema explicitly.
        column_data = {field.name: [] for field in schema}
        for row in bucket_rows:
            for field in schema:
                column_data[field.name].append(row[field.name])
        table = pa.Table.from_pydict(column_data, schema=schema)

        pq.write_table(table, out_path, compression=PARQUET_COMPRESSION)

        report.rows_written += len(bucket_rows)
        report.files_written.append(out_path)


# ---------------------------------------------------------------------------
# Public reader
# ---------------------------------------------------------------------------


class CorpusReader:
    """
    Thin DuckDB-backed query helper for the partitioned corpus.

    DuckDB reads partitioned Parquet natively via glob patterns and exposes
    partition columns automatically when you use `hive_partitioning=True`.
    We give callers two views (`mag` and `plasma`) and otherwise let them
    write raw SQL.

    Each CorpusReader owns a fresh in-memory DuckDB connection. Cheap to
    construct, no global state, safe to throw away.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._conn = duckdb.connect(":memory:")
        self._register_views()

    def _register_views(self) -> None:
        """
        Register `mag` and `plasma` views over the partitioned files.

        DuckDB rejects `read_parquet` globs that match zero files, so for
        instruments with no files yet we register an empty placeholder view
        with the correct schema. That keeps `SELECT * FROM mag` working even
        on a fresh empty corpus — important for the bulk-backfill script
        which checks the existing corpus state before writing.
        """
        for instrument in (INSTRUMENT_MAG, INSTRUMENT_PLASMA):
            files = list(iter_partition_files(self.root, instrument))
            if files:
                # DuckDB needs forward slashes for globs on Windows too.
                glob = (
                    self.root
                    / "dscovr"
                    / instrument
                    / "year=*"
                    / "month=*"
                    / "*.parquet"
                )
                glob_str = str(glob).replace("\\", "/")
                self._conn.execute(
                    f"""
                    CREATE OR REPLACE VIEW {instrument} AS
                    SELECT * FROM read_parquet(
                        '{glob_str}',
                        hive_partitioning = true,
                        union_by_name = true
                    )
                    """
                )
            else:
                # Empty placeholder with correct schema. Build a 0-row table
                # from the pyarrow schema so column types are correct.
                schema = _schema_for(instrument)
                empty_table = pa.Table.from_pydict(
                    {f.name: [] for f in schema}, schema=schema
                )
                self._conn.register(f"_empty_{instrument}", empty_table)
                self._conn.execute(
                    f"CREATE OR REPLACE VIEW {instrument} AS "
                    f"SELECT * FROM _empty_{instrument}"
                )

    def query(self, sql: str) -> list[tuple]:
        """Run a raw SQL query against the corpus. Returns row tuples."""
        return self._conn.execute(sql).fetchall()

    def query_df(self, sql: str):
        """Run a raw SQL query and return a pandas DataFrame."""
        return self._conn.execute(sql).fetchdf()

    def count(self, instrument: str) -> int:
        """Total row count for an instrument view."""
        result = self._conn.execute(
            f"SELECT COUNT(*) FROM {instrument}"
        ).fetchone()
        return int(result[0]) if result else 0

    def time_range(self, instrument: str) -> tuple[datetime, datetime] | None:
        """Return (min_ts, max_ts) for an instrument, or None if empty."""
        row = self._conn.execute(
            f"SELECT MIN(timestamp), MAX(timestamp) FROM {instrument}"
        ).fetchone()
        if not row or row[0] is None:
            return None
        return row[0], row[1]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CorpusReader:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Convenience: iterate partition files (handy for the future backfill script)
# ---------------------------------------------------------------------------


def iter_partition_files(
    root: Path | str,
    instrument: str,
) -> Iterator[Path]:
    """Yield every .parquet file for `instrument` under `root` in sorted order."""
    base = Path(root) / "dscovr" / instrument
    if not base.exists():
        return
    yield from sorted(base.rglob("*.parquet"))
