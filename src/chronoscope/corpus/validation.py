# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Corpus Data-Quality Validation
=================================================

Whole-corpus sanity checks over the partitioned DSCOVR Parquet corpus. Where
EXP-001 validated ONE storm by hand, this validates the ENTIRE corpus
programmatically and produces a committable report.

Checks (each returns a CheckResult with pass/fail + the numbers behind it):

  1. coverage          — per-day row counts vs the theoretical max
                         (86,400/day MAG @ 1 s, 1,440/day plasma @ 60 s).
                         Surfaces data gaps. Low coverage is EXPECTED on some
                         days (real instrument/telemetry gaps), so this check
                         reports the distribution rather than hard-failing on
                         individual sparse days; it only FAILS if the corpus is
                         empty or wholesale implausible.

  2. b_field_consistency — for MAG, stored |B| (bt_nt) vs the magnitude
                         recomputed from the GSE components
                         sqrt(bx² + by² + bz²). These are independent
                         derivations in the source data and should agree
                         closely. This is the whole-corpus version of the
                         hand-check in EXP-001 (which agreed to 4 dp).
                         FAILS if too large a fraction exceeds tolerance —
                         that would mean a column-mapping or unit error.

  3. physical_ranges   — min/max of each physical quantity against generous
                         physical bounds (no negative densities, solar-wind
                         speed in a sane band, |B| not absurd, temperature
                         positive). Catches fill-value leakage or unit bugs.
                         FAILS on values outside the hard bounds.

  4. cadence (optional, --cadence-day) — precise inter-sample spacing on ONE
                         day, confirming the 1 s (MAG) / 60 s (plasma) cadence
                         and quantifying gaps/duplicates. Off by default
                         because the window scan is expensive over 271M rows;
                         run it on a representative day when you want detail.

Design notes:
  - Checks 1-3 are single-pass aggregate queries — cheap even over a
    multi-hundred-million-row corpus. No full sort, no window function.
  - Nothing is mutated. This is read-only against the corpus.
  - "PASS/FAIL" encodes whether the data is trustworthy, not whether it is
    gap-free. Gaps are real and reported, not treated as corpus defects.

Usage:
  python src/chronoscope/corpus/validation.py --root E:\\chronoscope_corpus
  python src/chronoscope/corpus/validation.py --root E:\\chronoscope_corpus --report out.json
  python src/chronoscope/corpus/validation.py --root E:\\chronoscope_corpus --cadence-day 2017-09-07
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make `from src.chronoscope...` importable regardless of where this is run.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.chronoscope.corpus.storage import (  # noqa: E402
    INSTRUMENT_MAG,
    INSTRUMENT_PLASMA,
    CorpusReader,
)

# Expected cadence -> rows per full UTC day.
ROWS_PER_DAY_MAG = 86_400      # 1-second cadence
ROWS_PER_DAY_PLASMA = 1_440    # 60-second cadence

# |B| consistency tolerance. bt_nt (published B1F1) and the GSE-component
# magnitude are independent derivations; they should agree to well under this.
B_ABS_TOL_NT = 0.5             # absolute tolerance in nT
B_REL_TOL = 0.02               # 2% relative tolerance
# Fail the check only if more than this fraction of rows exceed BOTH tolerances.
B_FAIL_FRACTION = 0.01         # 1%

# Generous physical bounds for L1 solar wind. Outside these = almost certainly
# a fill-value leak or a unit/mapping bug, not real space weather.
RANGE_BOUNDS = {
    # column            (hard_min, hard_max, unit)
    "bt_nt":            (0.0, 200.0, "nT"),       # |B|; quiet ~5, extreme storms <100
    "bx_gse_nt":        (-200.0, 200.0, "nT"),
    "by_gse_nt":        (-200.0, 200.0, "nT"),
    "bz_gse_nt":        (-200.0, 200.0, "nT"),
    "proton_density_n_cc": (0.0, 500.0, "cm^-3"), # quiet ~5, extreme <100
    "bulk_speed_km_s":  (150.0, 1500.0, "km/s"),  # slow ~300, fast streams <1000
    "ion_temperature_k": (0.0, 1.0e8, "K"),
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_coverage(reader: CorpusReader) -> CheckResult:
    """Per-day row counts vs theoretical max, for both instruments."""
    details: dict[str, Any] = {}
    any_rows = False

    for instrument, per_day in (
        (INSTRUMENT_MAG, ROWS_PER_DAY_MAG),
        (INSTRUMENT_PLASMA, ROWS_PER_DAY_PLASMA),
    ):
        df = reader.query_df(
            f"""
            WITH daily AS (
                SELECT CAST(timestamp AS DATE) AS day, COUNT(*) AS n
                FROM {instrument}
                GROUP BY 1
            )
            SELECT
                COUNT(*)                              AS days_present,
                COALESCE(SUM(n), 0)                   AS total_rows,
                COALESCE(MIN(n), 0)                   AS min_rows_in_a_day,
                COALESCE(MAX(n), 0)                   AS max_rows_in_a_day,
                COALESCE(ROUND(AVG(n), 1), 0)         AS avg_rows_per_day,
                COALESCE(SUM(CASE WHEN n >= {int(per_day * 0.95)} THEN 1 ELSE 0 END), 0) AS days_full,
                COALESCE(SUM(CASE WHEN n <  {int(per_day * 0.50)} THEN 1 ELSE 0 END), 0) AS days_under_half
            FROM daily
            """
        )
        row = df.iloc[0].to_dict()
        days_present = int(row["days_present"])
        if days_present > 0:
            any_rows = True
        avg_cov = (
            float(row["avg_rows_per_day"]) / per_day if days_present else 0.0
        )
        details[instrument] = {
            "expected_rows_per_full_day": per_day,
            "days_present": days_present,
            "total_rows": int(row["total_rows"]),
            "min_rows_in_a_day": int(row["min_rows_in_a_day"]),
            "max_rows_in_a_day": int(row["max_rows_in_a_day"]),
            "avg_rows_per_day": float(row["avg_rows_per_day"]),
            "avg_coverage_fraction": round(avg_cov, 4),
            "days_at_95pct_plus": int(row["days_full"]),
            "days_under_50pct": int(row["days_under_half"]),
        }

    # The check FAILS only if the corpus is empty. Sparse days are reported,
    # not failed — real gaps are part of honest space-weather data.
    passed = any_rows
    if passed:
        m = details[INSTRUMENT_MAG]
        p = details[INSTRUMENT_PLASMA]
        summary = (
            f"MAG {m['days_present']} days, avg coverage "
            f"{m['avg_coverage_fraction']:.1%} ({m['days_at_95pct_plus']} days >=95%, "
            f"{m['days_under_50pct']} days <50%); "
            f"plasma {p['days_present']} days, avg coverage "
            f"{p['avg_coverage_fraction']:.1%}."
        )
    else:
        summary = "Corpus is empty — no rows in either instrument."
    return CheckResult("coverage", passed, summary, details)


def check_b_field_consistency(reader: CorpusReader) -> CheckResult:
    """Stored |B| vs magnitude recomputed from GSE components (MAG)."""
    df = reader.query_df(
        f"""
        WITH d AS (
            SELECT
                bt_nt,
                sqrt(bx_gse_nt*bx_gse_nt
                   + by_gse_nt*by_gse_nt
                   + bz_gse_nt*bz_gse_nt)            AS b_from_components
            FROM {INSTRUMENT_MAG}
        ),
        e AS (
            SELECT
                abs(bt_nt - b_from_components)        AS abs_diff,
                CASE WHEN bt_nt <> 0
                     THEN abs(bt_nt - b_from_components) / abs(bt_nt)
                     ELSE 0 END                        AS rel_diff
            FROM d
        )
        SELECT
            COUNT(*)                                  AS n_rows,
            COALESCE(MAX(abs_diff), 0)                AS max_abs_diff,
            COALESCE(AVG(abs_diff), 0)                AS avg_abs_diff,
            COALESCE(MAX(rel_diff), 0)                AS max_rel_diff,
            COALESCE(SUM(CASE WHEN abs_diff > {B_ABS_TOL_NT}
                              AND rel_diff > {B_REL_TOL}
                         THEN 1 ELSE 0 END), 0)        AS n_exceeding
        FROM e
        """
    )
    row = df.iloc[0].to_dict()
    n_rows = int(row["n_rows"])
    n_exceeding = int(row["n_exceeding"])
    frac_exceeding = (n_exceeding / n_rows) if n_rows else 0.0
    passed = (n_rows > 0) and (frac_exceeding <= B_FAIL_FRACTION)
    details = {
        "n_rows": n_rows,
        "max_abs_diff_nt": round(float(row["max_abs_diff"]), 6),
        "avg_abs_diff_nt": round(float(row["avg_abs_diff"]), 6),
        "max_rel_diff": round(float(row["max_rel_diff"]), 6),
        "abs_tol_nt": B_ABS_TOL_NT,
        "rel_tol": B_REL_TOL,
        "rows_exceeding_both_tol": n_exceeding,
        "fraction_exceeding": round(frac_exceeding, 6),
        "fail_threshold_fraction": B_FAIL_FRACTION,
    }
    if n_rows == 0:
        summary = "No MAG rows to check."
    else:
        summary = (
            f"{n_rows:,} MAG rows: max |B| discrepancy "
            f"{details['max_abs_diff_nt']} nT, "
            f"{n_exceeding:,} rows ({frac_exceeding:.4%}) exceed tolerance "
            f"(threshold {B_FAIL_FRACTION:.0%})."
        )
    return CheckResult("b_field_consistency", passed, summary, details)


def check_physical_ranges(reader: CorpusReader) -> CheckResult:
    """min/max of each physical quantity vs generous physical bounds."""
    details: dict[str, Any] = {}
    violations: list[str] = []

    col_instrument = {
        "bt_nt": INSTRUMENT_MAG,
        "bx_gse_nt": INSTRUMENT_MAG,
        "by_gse_nt": INSTRUMENT_MAG,
        "bz_gse_nt": INSTRUMENT_MAG,
        "proton_density_n_cc": INSTRUMENT_PLASMA,
        "bulk_speed_km_s": INSTRUMENT_PLASMA,
        "ion_temperature_k": INSTRUMENT_PLASMA,
    }

    for col, (lo, hi, unit) in RANGE_BOUNDS.items():
        instrument = col_instrument[col]
        df = reader.query_df(
            f"""
            SELECT
                COUNT(*)                              AS n,
                MIN({col})                            AS min_v,
                MAX({col})                            AS max_v,
                SUM(CASE WHEN {col} < {lo} OR {col} > {hi} THEN 1 ELSE 0 END) AS n_out
            FROM {instrument}
            """
        )
        row = df.iloc[0].to_dict()
        n = int(row["n"]) if row["n"] is not None else 0
        n_out = int(row["n_out"]) if row["n_out"] is not None else 0
        min_v = None if row["min_v"] is None else float(row["min_v"])
        max_v = None if row["max_v"] is None else float(row["max_v"])
        details[col] = {
            "unit": unit,
            "bound_min": lo,
            "bound_max": hi,
            "observed_min": min_v,
            "observed_max": max_v,
            "rows_out_of_bounds": n_out,
            "n_rows": n,
        }
        if n_out > 0:
            violations.append(f"{col}: {n_out:,} rows out of [{lo},{hi}] {unit}")

    passed = len(violations) == 0
    if passed:
        summary = "All physical quantities within bounds."
    else:
        summary = "Out-of-bounds values found: " + "; ".join(violations)
    return CheckResult("physical_ranges", passed, summary, details)


def check_cadence(reader: CorpusReader, day: str) -> CheckResult:
    """
    Precise inter-sample spacing on a single UTC day for both instruments.
    Expensive (window function over the day), so opt-in via --cadence-day.
    """
    details: dict[str, Any] = {}
    notes: list[str] = []

    for instrument, expected_s in (
        (INSTRUMENT_MAG, 1),
        (INSTRUMENT_PLASMA, 60),
    ):
        df = reader.query_df(
            f"""
            WITH ordered AS (
                SELECT timestamp,
                       epoch(timestamp) - lag(epoch(timestamp))
                           OVER (ORDER BY timestamp) AS dt_s
                FROM {instrument}
                WHERE timestamp >= TIMESTAMP '{day} 00:00:00'
                  AND timestamp <  TIMESTAMP '{day} 00:00:00' + INTERVAL 1 DAY
            )
            SELECT
                COUNT(*)                                       AS n_rows,
                COUNT(dt_s)                                    AS n_gaps_measured,
                COALESCE(SUM(CASE WHEN dt_s = {expected_s} THEN 1 ELSE 0 END), 0) AS n_at_cadence,
                COALESCE(SUM(CASE WHEN dt_s > {expected_s} THEN 1 ELSE 0 END), 0) AS n_gaps,
                COALESCE(SUM(CASE WHEN dt_s <= 0 THEN 1 ELSE 0 END), 0)           AS n_dupe_or_back,
                COALESCE(MAX(dt_s), 0)                         AS max_gap_s,
                COALESCE(MEDIAN(dt_s), 0)                      AS median_dt_s
            FROM ordered
            """
        )
        row = df.iloc[0].to_dict()
        n_meas = int(row["n_gaps_measured"])
        n_at = int(row["n_at_cadence"])
        frac_at = (n_at / n_meas) if n_meas else 0.0
        details[instrument] = {
            "day": day,
            "expected_cadence_s": expected_s,
            "n_rows": int(row["n_rows"]),
            "median_dt_s": float(row["median_dt_s"]),
            "fraction_at_expected_cadence": round(frac_at, 4),
            "n_gaps": int(row["n_gaps"]),
            "max_gap_s": float(row["max_gap_s"]),
            "n_duplicate_or_backwards": int(row["n_dupe_or_back"]),
        }
        notes.append(
            f"{instrument} median {details[instrument]['median_dt_s']:.0f}s, "
            f"{frac_at:.1%} at cadence, {details[instrument]['n_gaps']} gaps "
            f"(max {details[instrument]['max_gap_s']:.0f}s)"
        )

    # Pass if the median spacing matches expectation for both (the dominant
    # cadence is correct); gaps/dupes are reported, not auto-failed.
    mag_ok = details[INSTRUMENT_MAG]["median_dt_s"] == 1
    plasma_med = details[INSTRUMENT_PLASMA]["median_dt_s"]
    plasma_ok = (plasma_med == 60) or (details[INSTRUMENT_PLASMA]["n_rows"] == 0)
    passed = mag_ok and plasma_ok
    return CheckResult("cadence", passed, "; ".join(notes), details)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_validation(root: Path, cadence_day: str | None = None) -> dict[str, Any]:
    results: list[CheckResult] = []
    with CorpusReader(root) as reader:
        mag_total = reader.count(INSTRUMENT_MAG)
        plasma_total = reader.count(INSTRUMENT_PLASMA)
        mag_range = reader.time_range(INSTRUMENT_MAG)
        plasma_range = reader.time_range(INSTRUMENT_PLASMA)

        results.append(check_coverage(reader))
        results.append(check_b_field_consistency(reader))
        results.append(check_physical_ranges(reader))
        if cadence_day:
            results.append(check_cadence(reader, cadence_day))

    def _fmt_range(r):
        return None if r is None else [r[0].isoformat(), r[1].isoformat()]

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_root": str(root),
        "totals": {
            "mag_rows": mag_total,
            "plasma_rows": plasma_total,
            "mag_time_range": _fmt_range(mag_range),
            "plasma_time_range": _fmt_range(plasma_range),
        },
        "all_passed": all(r.passed for r in results),
        "checks": [asdict(r) for r in results],
    }
    return report


def _print_console(report: dict[str, Any]) -> None:
    t = report["totals"]
    print("=" * 68)
    print("ChronoScope Corpus Validation")
    print("=" * 68)
    print(f"corpus     : {report['corpus_root']}")
    print(f"generated  : {report['generated_utc']}")
    print(f"mag rows   : {t['mag_rows']:,}")
    print(f"plasma rows: {t['plasma_rows']:,}")
    if t["mag_time_range"]:
        print(f"mag range  : {t['mag_time_range'][0]} -> {t['mag_time_range'][1]}")
    if t["plasma_time_range"]:
        print(f"plasma rng : {t['plasma_time_range'][0]} -> {t['plasma_time_range'][1]}")
    print("-" * 68)
    for c in report["checks"]:
        mark = "PASS" if c["passed"] else "FAIL"
        print(f"[{mark}] {c['name']}")
        print(f"       {c['summary']}")
    print("-" * 68)
    overall = "ALL CHECKS PASSED" if report["all_passed"] else "SOME CHECKS FAILED"
    print(overall)
    print("=" * 68)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Validate the data quality of a ChronoScope DSCOVR corpus."
    )
    p.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Corpus root directory (e.g. E:\\chronoscope_corpus).",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write the JSON report (e.g. corpus_validation.json).",
    )
    p.add_argument(
        "--cadence-day",
        type=str,
        default=None,
        help="Optional YYYY-MM-DD to run the (expensive) precise cadence check "
        "on a single day, e.g. 2017-09-07.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = run_validation(args.root, cadence_day=args.cadence_day)
    _print_console(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"JSON report written to {args.report}")
    # Exit non-zero if any check failed, so this is CI/automation-friendly.
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
