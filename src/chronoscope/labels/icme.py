# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — ICME Interval Labels (Richardson & Cane catalog)
==================================================================

Fetches the Richardson & Cane near-Earth ICME catalog and writes it as an
INTERVAL label table joinable to the telemetry corpus. Where the Kp labels
(geomagnetic.py) describe geomagnetic *response*, ICMEs are the solar-wind
*drivers* — the coronal mass ejection passages themselves.

Source (DEC-008):
  HTML table at https://izw1.caltech.edu/ACE/ASC/DATA/level3/icmetable2.htm
  (DOI 10.7910/DVN/C2MHTH). ~500 events, May 1996 -> present. No official CSV.
  Cite Cane & Richardson 2003 (JGR) and Richardson & Cane 2010 (Solar Phys).

The table is an 18-column HTML grid with a header that REPEATS each year-block.
Since May 2016 every timestamp carries its year (Y/M/D HHMM). Parsing handles:
  - '...' = missing
  - repeated header rows (filtered)
  - speed suffixes like '100 S'
  - MC-flag forms like '2H'
  - LASCO column noise: 'dg (1997/11/19 1700)', '1996/12/19 1630 H'

Output: {root}/labels/icme/richardson_cane.parquet
  columns: source_row (int32), disturbance_time, icme_start, icme_end,
           quality (string), v_icme_km_s, v_max_km_s, b_nt, mc_flag (int8),
           dst_min_nt, v_transit_km_s, lasco_cme_time
  (icme_start/icme_end are the interval; rows without a valid interval are dropped.)

Design: fetch_icme_html + read_icme_dataframe (network/IO) are isolated; the
parsing/cleaning functions are pure and unit-tested. An --inspect mode prints
the raw parsed structure so the column layout can be verified against the live
file before trusting the mapping.

Usage:
  python -m src.chronoscope.labels.icme --root E:\\chronoscope_corpus --inspect
  python -m src.chronoscope.labels.icme --root E:\\chronoscope_corpus
"""

from __future__ import annotations

import argparse
import re
import sys
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

ICME_HTML_URL = "https://izw1.caltech.edu/ACE/ASC/DATA/level3/icmetable2.htm"
PARQUET_COMPRESSION = "zstd"
ICME_RELPATH = Path("labels") / "icme" / "richardson_cane.parquet"

# The live table renders with a trailing always-empty column: read_html yields
# 19 columns (18 meaningful + 1 nan). We map positionally by the first 18
# (indices 0..17, LASCO last) and ignore any extra trailing columns. The parser
# requires at least enough cells to reach the last mapped column.
MIN_NCOLS = 18  # indices 0..17 must be present (LASCO at 17)

# Positional column map (0-indexed) into the parsed table. Mapping by position
# is more robust than by name because the header cells are merged/repeated.
COL = {
    "disturbance_time": 0,
    "icme_start": 1,
    "icme_end": 2,
    # 3,4 = Comp. start/end (hrs); 5,6 = MC start/end (hrs); 7 = BDE; 8 = BIF
    "quality": 9,
    # 10 = dV
    "v_icme_km_s": 11,
    "v_max_km_s": 12,
    "b_nt": 13,
    "mc_flag": 14,
    "dst_min_nt": 15,
    "v_transit_km_s": 16,
    "lasco_cme_time": 17,
}

_SCHEMA = pa.schema(
    [
        pa.field("source_row", pa.int32()),
        pa.field("disturbance_time", pa.timestamp("us", tz="UTC")),
        pa.field("icme_start", pa.timestamp("us", tz="UTC")),
        pa.field("icme_end", pa.timestamp("us", tz="UTC")),
        pa.field("quality", pa.string()),
        pa.field("v_icme_km_s", pa.float64()),
        pa.field("v_max_km_s", pa.float64()),
        pa.field("b_nt", pa.float64()),
        pa.field("mc_flag", pa.int8()),
        pa.field("dst_min_nt", pa.float64()),
        pa.field("v_transit_km_s", pa.float64()),
        pa.field("lasco_cme_time", pa.timestamp("us", tz="UTC")),
    ]
)

# ---------------------------------------------------------------------------
# Pure parsing helpers (unit-tested without network)
# ---------------------------------------------------------------------------

# Matches an embedded "YYYY/MM/DD HHMM" anywhere in a cell, ignoring
# surrounding noise (letters, parentheses, 'dg', halo flags, etc.).
_DT_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,4})")
# Leading signed number, for speeds / Dst / B (handles '100 S', '-33', '2H').
_NUM_RE = re.compile(r"-?\d+\.?\d*")

_MISSING_TOKENS = {"", "...", "....", ".....", "nan", "none"}


def _is_missing(cell: Any) -> bool:
    if cell is None:
        return True
    s = str(cell).strip().lower()
    return s in _MISSING_TOKENS or s.startswith("...")


def parse_rc_datetime(cell: Any) -> datetime | None:
    """Parse an embedded Y/M/D HHMM into a UTC datetime, or None if absent."""
    if _is_missing(cell):
        return None
    m = _DT_RE.search(str(cell))
    if not m:
        return None
    y, mo, d, hm = m.groups()
    hm = hm.zfill(4)
    try:
        return datetime(
            int(y), int(mo), int(d), int(hm[:2]), int(hm[2:]), tzinfo=timezone.utc
        )
    except ValueError:
        return None


def clean_float(cell: Any) -> float | None:
    """Extract the leading (signed) number, ignoring suffixes like ' S'/'H'."""
    if _is_missing(cell):
        return None
    m = _NUM_RE.search(str(cell))
    return float(m.group()) if m else None


def clean_int(cell: Any) -> int | None:
    f = clean_float(cell)
    return int(f) if f is not None else None


def clean_str(cell: Any) -> str:
    if _is_missing(cell):
        return ""
    return str(cell).strip()


def is_header_row(first_cell: Any) -> bool:
    """Repeated header rows lead with 'Disturbance' / contain 'Y/M/D'."""
    s = str(first_cell)
    return ("Disturbance" in s) or ("Y/M/D" in s)


def parse_icme_rows(table_rows: list[list[Any]]) -> list[dict]:
    """
    Turn raw table rows (list of cell-lists) into canonical label dicts.

    Drops repeated header rows and any row lacking a valid ICME start+end
    interval (those can't anchor a time-interval label).
    """
    rows: list[dict] = []
    source_row = 0
    for cells in table_rows:
        if not cells:
            continue
        if is_header_row(cells[0]):
            continue
        if len(cells) < MIN_NCOLS:
            # Malformed/short row — skip rather than misalign columns.
            continue
        icme_start = parse_rc_datetime(cells[COL["icme_start"]])
        icme_end = parse_rc_datetime(cells[COL["icme_end"]])
        if icme_start is None or icme_end is None:
            continue
        source_row += 1
        rows.append(
            {
                "source_row": source_row,
                "disturbance_time": parse_rc_datetime(cells[COL["disturbance_time"]]),
                "icme_start": icme_start,
                "icme_end": icme_end,
                "quality": clean_str(cells[COL["quality"]]),
                "v_icme_km_s": clean_float(cells[COL["v_icme_km_s"]]),
                "v_max_km_s": clean_float(cells[COL["v_max_km_s"]]),
                "b_nt": clean_float(cells[COL["b_nt"]]),
                "mc_flag": clean_int(cells[COL["mc_flag"]]),
                "dst_min_nt": clean_float(cells[COL["dst_min_nt"]]),
                "v_transit_km_s": clean_float(cells[COL["v_transit_km_s"]]),
                "lasco_cme_time": parse_rc_datetime(cells[COL["lasco_cme_time"]]),
            }
        )
    return rows


def rows_to_table(rows: list[dict]) -> pa.Table:
    if not rows:
        return _SCHEMA.empty_table()
    cols = {name: [r[name] for r in rows] for name in _SCHEMA.names}
    return pa.table(cols, schema=_SCHEMA)


def write_icme_labels(rows: list[dict], root: Path | str) -> Path:
    out_path = Path(root) / ICME_RELPATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(rows_to_table(rows), out_path, compression=PARQUET_COMPRESSION)
    logger.info(
        "icme_labels_written",
        path=str(out_path),
        intervals=len(rows),
        magnetic_clouds=sum(1 for r in rows if r["mc_flag"] == 2),
    )
    return out_path


# ---------------------------------------------------------------------------
# Network / IO (runs on the operator's machine; not unit-tested)
# ---------------------------------------------------------------------------


def fetch_icme_html(url: str = ICME_HTML_URL, *, timeout: float = 60.0) -> str:
    logger.info("icme_fetch", url=url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def html_to_rows(html: str) -> list[list[Any]]:
    """
    Parse the ICME HTML into a list of raw cell-rows using pandas.read_html,
    selecting the widest table (the catalog) and returning its values as
    plain Python lists for the pure parser to consume.
    """
    import pandas as pd
    from io import StringIO

    tables = pd.read_html(StringIO(html))  # requires lxml; StringIO for pandas 3.x
    if not tables:
        raise ValueError("pandas.read_html found no tables in the ICME page")
    # The catalog is the widest table on the page.
    table = max(tables, key=lambda t: t.shape[1])
    logger.info(
        "icme_table_selected",
        n_tables=len(tables),
        shape=list(table.shape),
    )
    return table.values.tolist()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _inspect(html: str) -> None:
    import pandas as pd
    from io import StringIO

    tables = pd.read_html(StringIO(html))
    print(f"read_html found {len(tables)} table(s).")
    for i, t in enumerate(tables):
        print(f"  table[{i}] shape={t.shape}")
    widest = max(tables, key=lambda t: t.shape[1])
    print(f"\nWidest table shape: {widest.shape} (min required={MIN_NCOLS}, live table has 19 incl. trailing empty)")
    print("First 3 data rows (positional):")
    for row in widest.values.tolist()[:5]:
        print("  ", row)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch the Richardson-Cane ICME catalog and write corpus labels."
    )
    p.add_argument("--root", type=Path, required=True, help="Corpus root.")
    p.add_argument("--url", type=str, default=ICME_HTML_URL)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument(
        "--inspect",
        action="store_true",
        help="Print the raw parsed table structure and exit (no write). "
        "Use this first to verify the column layout against the live file.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    html = fetch_icme_html(args.url, timeout=args.timeout)

    if args.inspect:
        _inspect(html)
        return 0

    raw_rows = html_to_rows(html)
    rows = parse_icme_rows(raw_rows)
    if not rows:
        print("No ICME intervals parsed — check --inspect output; nothing written.")
        return 1
    out_path = write_icme_labels(rows, args.root)

    # Honest summary + a ground-truth pointer.
    mc = sum(1 for r in rows if r["mc_flag"] == 2)
    partial = sum(1 for r in rows if r["mc_flag"] == 1)
    ejecta = sum(1 for r in rows if r["mc_flag"] == 0)
    with_shock = sum(1 for r in rows if r["disturbance_time"] is not None)
    min_dst = min((r["dst_min_nt"] for r in rows if r["dst_min_nt"] is not None),
                  default=None)
    print("=" * 60)
    print("Richardson-Cane ICME labels written")
    print("=" * 60)
    print(f"path        : {out_path}")
    print(f"intervals   : {len(rows)}")
    print(f"range       : {rows[0]['icme_start'].isoformat()} -> "
          f"{rows[-1]['icme_end'].isoformat()}")
    print(f"with shock  : {with_shock}")
    print(f"structure   : {mc} magnetic clouds, {partial} partial, {ejecta} ejecta")
    print(f"min Dst     : {min_dst} nT (most geoeffective event in catalog)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
