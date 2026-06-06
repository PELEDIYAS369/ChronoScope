# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — In-Place Corpus Cleanup (physical plausibility)
=================================================================

One-off remediation tool. The storage layer now drops physically implausible
rows at write time (DEC-007, PHYSICAL_BOUNDS in storage.py), but the existing
corpus was built before that gate existed and contains a small number of
impossible values (notably negative and absurdly-high proton densities that
whole-corpus validation surfaced).

Rather than re-fetch the entire corpus from CDAWeb (hours), this rewrites the
already-on-disk Parquet files in place, dropping only rows that fall outside
the SAME bounds the storage gate now enforces. It imports PHYSICAL_BOUNDS from
storage.py so the cleanup and the gate can never drift apart.

Safety:
  * --dry-run (default OFF, but recommended first) reports what WOULD be
    removed per file without touching anything.
  * A file is only rewritten if it actually contains out-of-bounds rows;
    clean files are left byte-for-byte untouched.
  * Rewrite is atomic per file: write to a temp file, then replace, so an
    interruption can't leave a half-written Parquet.
  * Schema and zstd compression are preserved exactly.
  * MAG is checked too (it was clean in validation, but the bounds apply).

Usage:
  python scripts/clean_corpus_plausibility.py --root E:\\chronoscope_corpus --dry-run
  python scripts/clean_corpus_plausibility.py --root E:\\chronoscope_corpus
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.chronoscope.corpus.storage import (  # noqa: E402
    INSTRUMENT_MAG,
    INSTRUMENT_PLASMA,
    PARQUET_COMPRESSION,
    PHYSICAL_BOUNDS,
)


def _bounds_for_columns(columns: list[str]) -> dict[str, tuple[float, float]]:
    """Subset of PHYSICAL_BOUNDS whose columns exist in this table."""
    return {c: PHYSICAL_BOUNDS[c] for c in columns if c in PHYSICAL_BOUNDS}


def _in_bounds_mask(table: pa.Table) -> pa.Array:
    """
    Boolean mask: True for rows where EVERY bounded column is within range
    (and not null). Rows with a null in a bounded column are treated as
    out-of-bounds here too (clean data shouldn't have them; the write-time
    fill gate would have removed real fill rows).
    """
    bounds = _bounds_for_columns(table.column_names)
    mask = pa.array([True] * table.num_rows)
    for col, (lo, hi) in bounds.items():
        values = table.column(col)
        col_ok = pc.and_(
            pc.greater_equal(values, pa.scalar(lo)),
            pc.less_equal(values, pa.scalar(hi)),
        )
        # null -> treat as not-ok
        col_ok = pc.fill_null(col_ok, False)
        mask = pc.and_(mask, col_ok)
    return mask


def _process_file(path: Path, *, dry_run: bool) -> tuple[int, int]:
    """
    Returns (rows_total, rows_dropped) for one Parquet file. Rewrites the file
    (dropping out-of-bounds rows) unless dry_run or nothing needs dropping.
    """
    table = pq.read_table(path)
    total = table.num_rows
    if total == 0:
        return 0, 0

    mask = _in_bounds_mask(table)
    keep = pc.sum(pc.cast(mask, pa.int64())).as_py() or 0
    dropped = total - keep
    if dropped == 0:
        return total, 0

    if not dry_run:
        cleaned = table.filter(mask)
        tmp = path.with_suffix(path.suffix + ".tmp")
        pq.write_table(cleaned, tmp, compression=PARQUET_COMPRESSION)
        tmp.replace(path)
    return total, dropped


def run_cleanup(root: Path, *, dry_run: bool) -> int:
    grand_total = 0
    grand_dropped = 0
    files_changed = 0

    for instrument in (INSTRUMENT_MAG, INSTRUMENT_PLASMA):
        inst_dir = root / "dscovr" / instrument
        if not inst_dir.exists():
            print(f"[skip] {instrument}: no directory at {inst_dir}")
            continue
        files = sorted(inst_dir.rglob("*.parquet"))
        inst_total = 0
        inst_dropped = 0
        inst_files_changed = 0
        for f in files:
            total, dropped = _process_file(f, dry_run=dry_run)
            inst_total += total
            inst_dropped += dropped
            if dropped > 0:
                inst_files_changed += 1
                rel = f.relative_to(root)
                action = "would drop" if dry_run else "dropped"
                print(f"  [{instrument}] {action} {dropped} row(s) in {rel}")
        print(
            f"[{instrument}] files={len(files)} rows={inst_total:,} "
            f"out_of_bounds={inst_dropped:,} files_affected={inst_files_changed}"
        )
        grand_total += inst_total
        grand_dropped += inst_dropped
        files_changed += inst_files_changed

    print("-" * 64)
    verb = "WOULD REMOVE" if dry_run else "REMOVED"
    print(
        f"{verb} {grand_dropped:,} implausible row(s) across {files_changed} "
        f"file(s); corpus scanned {grand_total:,} rows total."
    )
    if dry_run:
        print("DRY RUN — nothing was modified. Re-run without --dry-run to apply.")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Drop physically-implausible rows from an existing corpus "
        "in place (DEC-007 bounds). Run with --dry-run first."
    )
    p.add_argument("--root", type=Path, required=True, help="Corpus root.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed without modifying any files.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.root.exists():
        print(f"ERROR: corpus root not found: {args.root}")
        return 1
    return run_cleanup(args.root, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
