# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Geomagnetic Index Labels (Kp / ap / derived G-scale)
======================================================================

Fetches the IAGA-endorsed Kp and ap geomagnetic indices from GFZ Potsdam and
writes them as a label time series joinable to the telemetry corpus by
timestamp. Each Kp/ap value covers a 3-hour UT interval; the timestamp stored
is the START of that interval (GFZ convention).

Source (DEC-008):
  GFZ JSON webservice — https://kp.gfz.de/app/json/?start=..&end=..&index=Kp&status=def
  Returns parallel arrays: {<index>: [...], "datetime": [...], "status": [...], "meta": {...}}
  Kp is decimal (thirds, e.g. 4.667 = "5-"); ap is integer; missing = -1.
  License: CC BY 4.0, GFZ Potsdam. Cite Matzka et al. 2021, DOI 10.5880/Kp.0001.

G-scale (NOAA) is DERIVED from Kp, not a separate source:
  G1=Kp5 ... G5=Kp9. Because GFZ reports Kp in thirds (5- = 4.667 still
  classifies as Kp level 5 = G1), we round to the nearest Kp level rather than
  floor. Raw kp is preserved as source-of-truth so g_scale can be re-derived.

Output: {root}/labels/geomagnetic/kp_ap.parquet
  columns: timestamp (us, UTC), kp (float64), ap (int32),
           g_scale (int8, 0-5), status (string: "def"/"nowcast")

Design: the HTTP fetch (fetch_kp_ap) is isolated; parse_gfz_json,
derive_g_scale, rows_to_table, and write_kp_labels are pure and unit-tested
without network.

Usage:
  python -m src.chronoscope.labels.geomagnetic --root E:\\chronoscope_corpus \\
      --start 2016-07-26 --end 2026-06-06
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import requests
import structlog

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = structlog.get_logger(__name__)

GFZ_JSON_URL = "https://kp.gfz.de/app/json/"
PARQUET_COMPRESSION = "zstd"

# GFZ missing-data sentinel for Kp and ap.
GFZ_MISSING = -1

# Relative location of the geomagnetic label file under the corpus root.
KP_AP_RELATIVE_PATH = Path("labels") / "geomagnetic" / "kp_ap.parquet"

_SCHEMA = pa.schema(
    [
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("kp", pa.float64()),
        pa.field("ap", pa.int32()),
        pa.field("g_scale", pa.int8()),
        pa.field("status", pa.string()),
    ]
)


# ---------------------------------------------------------------------------
# Pure logic (unit-tested without network)
# ---------------------------------------------------------------------------


def derive_g_scale(kp: float) -> int:
    """
    Map a Kp value to the NOAA G-scale (0 = no storm, 1-5 = G1-G5).

    GFZ reports Kp in thirds as decimals; the storm LEVEL is the nearest
    integer Kp (5- = 4.667 -> level 5 -> G1), so we round rather than floor.
    """
    level = round(kp)
    if level < 5:
        return 0
    return min(5, level - 4)


def parse_gfz_json(payload: dict[str, Any], index_name: str) -> dict[datetime, Any]:
    """
    Turn a GFZ JSON response into {interval_start_utc: value}.

    The response has parallel arrays: payload[index_name][i] corresponds to
    payload["datetime"][i]. Datetimes are ISO-8601 with a trailing 'Z'.
    """
    if index_name not in payload:
        raise ValueError(
            f"GFZ response missing '{index_name}' key; got keys: "
            f"{sorted(payload.keys())}"
        )
    values = payload[index_name]
    times = payload.get("datetime")
    if times is None:
        raise ValueError("GFZ response missing 'datetime' key")
    if len(values) != len(times):
        raise ValueError(
            f"GFZ arrays length mismatch: {index_name}={len(values)} "
            f"datetime={len(times)}"
        )
    out: dict[datetime, Any] = {}
    for ts_str, v in zip(times, values):
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        out[ts] = v
    return out


def join_kp_ap(
    kp_by_time: dict[datetime, Any],
    ap_by_time: dict[datetime, Any],
    status_by_time: dict[datetime, Any] | None = None,
) -> list[dict]:
    """
    Join Kp and ap (both keyed by interval-start) into row dicts, dropping
    intervals where Kp is missing (the -1 sentinel). Rows are time-sorted.
    """
    rows: list[dict] = []
    for ts in sorted(kp_by_time.keys()):
        kp = kp_by_time[ts]
        ap = ap_by_time.get(ts)
        if kp is None or kp == GFZ_MISSING:
            continue
        kp = float(kp)
        # ap may be missing even when Kp present; store -1 -> treat as null-ish 0?
        # Keep ap honest: if missing, we still keep the row (Kp drives g_scale).
        ap_val = int(ap) if (ap is not None and ap != GFZ_MISSING) else GFZ_MISSING
        status = None
        if status_by_time is not None:
            status = status_by_time.get(ts)
        rows.append(
            {
                "timestamp": ts,
                "kp": kp,
                "ap": ap_val,
                "g_scale": derive_g_scale(kp),
                "status": status if status is not None else "",
            }
        )
    return rows


def rows_to_table(rows: list[dict]) -> pa.Table:
    """Build an Arrow table with the canonical schema from row dicts."""
    if not rows:
        return _SCHEMA.empty_table()
    cols = {name: [r[name] for r in rows] for name in _SCHEMA.names}
    return pa.table(cols, schema=_SCHEMA)


def write_kp_labels(rows: list[dict], root: Path | str) -> Path:
    """Write the Kp/ap label table to {root}/labels/geomagnetic/kp_ap.parquet."""
    out_path = Path(root) / KP_AP_RELATIVE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = rows_to_table(rows)
    pq.write_table(table, out_path, compression=PARQUET_COMPRESSION)
    logger.info(
        "kp_labels_written",
        path=str(out_path),
        rows=len(rows),
        storm_intervals=sum(1 for r in rows if r["g_scale"] > 0),
    )
    return out_path


# ---------------------------------------------------------------------------
# Network fetch (runs on the operator's machine; not unit-tested)
# ---------------------------------------------------------------------------


def _fetch_index(
    index_name: str,
    start: datetime,
    end: datetime,
    *,
    status: str | None,
    timeout: float,
) -> dict[str, Any]:
    """Fetch one index (Kp or ap) from the GFZ JSON webservice."""
    params = {
        "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "index": index_name,
    }
    if status:
        params["status"] = status
    url = GFZ_JSON_URL + "?" + urllib.parse.urlencode(params)
    logger.info("gfz_fetch", index=index_name, url=url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_kp_ap(
    start: datetime,
    end: datetime,
    *,
    status: str | None = None,
    timeout: float = 60.0,
) -> list[dict]:
    """
    Fetch Kp and ap over [start, end] and return joined, g-scale-derived rows.

    status=None returns the hybrid nowcast+definitive series (full coverage to
    present); status="def" returns definitive-only (may stop short of recent
    weeks). For a historical corpus we usually want full coverage, so default
    is None; pass "def" if you want definitive-only.
    """
    kp_raw = _fetch_index("Kp", start, end, status=status, timeout=timeout)
    ap_raw = _fetch_index("ap", start, end, status=status, timeout=timeout)
    kp_by_time = parse_gfz_json(kp_raw, "Kp")
    ap_by_time = parse_gfz_json(ap_raw, "ap")
    status_by_time = None
    if "status" in kp_raw and "datetime" in kp_raw:
        status_by_time = {
            datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(
                timezone.utc
            ): s
            for t, s in zip(kp_raw["datetime"], kp_raw["status"])
        }
    rows = join_kp_ap(kp_by_time, ap_by_time, status_by_time)
    logger.info(
        "kp_ap_fetched",
        rows=len(rows),
        first=rows[0]["timestamp"].isoformat() if rows else None,
        last=rows[-1]["timestamp"].isoformat() if rows else None,
    )
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch GFZ Kp/ap geomagnetic indices and write corpus labels."
    )
    p.add_argument("--root", type=Path, required=True, help="Corpus root.")
    p.add_argument(
        "--start", type=_parse_date, required=True, help="UTC start date YYYY-MM-DD."
    )
    p.add_argument(
        "--end", type=_parse_date, required=True, help="UTC end date YYYY-MM-DD."
    )
    p.add_argument(
        "--status",
        choices=["def", "all"],
        default="all",
        help="'def' = definitive only; 'all' = hybrid nowcast+definitive (default).",
    )
    p.add_argument("--timeout", type=float, default=60.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    status = None if args.status == "all" else "def"
    rows = fetch_kp_ap(args.start, args.end, status=status, timeout=args.timeout)
    if not rows:
        print("No Kp/ap rows fetched — nothing written.")
        return 1
    out_path = write_kp_labels(rows, args.root)

    # Honest summary.
    g_counts = {g: 0 for g in range(6)}
    max_kp = 0.0
    max_kp_ts = None
    for r in rows:
        g_counts[r["g_scale"]] += 1
        if r["kp"] > max_kp:
            max_kp = r["kp"]
            max_kp_ts = r["timestamp"]
    print("=" * 60)
    print("Kp/ap labels written")
    print("=" * 60)
    print(f"path        : {out_path}")
    print(f"intervals   : {len(rows):,} (3-hourly)")
    print(f"range       : {rows[0]['timestamp'].isoformat()} -> "
          f"{rows[-1]['timestamp'].isoformat()}")
    print(f"max Kp      : {max_kp:.3f} at {max_kp_ts.isoformat() if max_kp_ts else '-'}")
    print("G-scale interval counts:")
    for g in range(1, 6):
        print(f"  G{g}: {g_counts[g]:,}")
    print(f"  (quiet/none: {g_counts[0]:,})")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
