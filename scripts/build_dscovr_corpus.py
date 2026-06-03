# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Bulk DSCOVR Corpus Backfill
=============================================

Walks the DSCOVR operational window one DAY at a time, fetches each day from
the NASA SPDF/CDAWeb HAPI server via NOAADscovrArchiveIngester, and writes it
into the partitioned Parquet corpus via write_partitioned_parquet.

WHY DAILY CHUNKS (this is the v2 fix):
  The first version walked month-by-month. A full month of 1-second MAG data
  is ~2.7 million rows of CSV, which CDAWeb either can't serve inside a sane
  timeout or throttles. A single DAY (~86,400 MAG + ~1,440 plasma rows) is the
  unit we proved works in the storage smoke test — it fetches in seconds when
  the server is healthy. Walking by day keeps every HTTP request small.

  The v1 month walker also had a real bug: it floored --start to the first of
  the month, so `--start 2018-03-15` silently became 2018-03-01. This version
  honors the exact dates you pass and never widens the window.

Design goals (see docs/DECISIONS.md DEC-004):
  * RESUMABLE. A JSON checkpoint is written after every day. Re-running picks
    up exactly where it left off — completed days are skipped, so an
    interrupted multi-year run costs nothing. Granularity is one day.
  * RESILIENT. A day that fails to fetch (network blip, HAPI 5xx, timeout) is
    retried a few times with backoff, then logged to a failed list and skipped
    so the overall run completes. Failed days are re-runnable with
    --retry-failed.
  * HONEST. Per-day and end-of-run logging reports rows seen, written, and
    dropped (fill / dqf / other), plus a tally of succeeded / skipped / failed
    days. A day that writes zero rows is logged as a warning, not hidden — many
    recent days are legitimately empty (definitive data lags real time), but
    you should always be able to see it.

Coverage windows (the ingester's own constants are the single source of truth):
  * MAG  (DSCOVR_H0_MAG): operational date 2016-07-27 -> present
  * Plasma (DSCOVR_H1_FC): operational date 2016-07-27 -> 2019-06-27 (frozen)
The ingester clamps to the operational date and short-circuits plasma past the
H1_FC end date, so this script just walks days and lets the ingester decide
what is actually fetchable.

Usage:
  python scripts/build_dscovr_corpus.py --dry-run --start 2018-03 --end 2018-04
  python scripts/build_dscovr_corpus.py --root data/corpus --start 2018-03-15 --end 2018-03-18
  python scripts/build_dscovr_corpus.py            # whole window into data/corpus
  python scripts/build_dscovr_corpus.py --retry-failed

NOTE: the full operational window is ~3,600+ days. That is a long, sequential,
unattended run. The per-day checkpoint is exactly what makes it safe to leave
running and resume after a sleep/network drop. Consider capping --end or doing
it in yearly slices if you want to babysit smaller batches.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import structlog

# The script lives in scripts/; make sure the repo root is importable so
# `from src.chronoscope...` works regardless of where it's invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.chronoscope.corpus.storage import (  # noqa: E402
    CorpusReader,
    WriteReport,
    write_partitioned_parquet,
)
from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR  # noqa: E402
from src.chronoscope.domain.exceptions import (  # noqa: E402
    DataSourceUnavailableError,
    PacketParseError,
)
from src.chronoscope.ingestion.noaa_dscovr_archive import (  # noqa: E402
    DSCOVR_H1_FC_END_DATE,
    DSCOVR_OPERATIONAL_DATE,
    NOAADscovrArchiveIngester,
)

logger = structlog.get_logger("build_dscovr_corpus")

DEFAULT_ROOT = Path("data/corpus")
CHECKPOINT_NAME = ".checkpoint.json"

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 5.0  # multiplied by attempt number
# Default per-request timeout. A healthy CDAWeb serves a day in seconds, but
# a single day's MAG CSV is ~5-6 MB, so give a slow-but-up server headroom.
DEFAULT_TIMEOUT_SECONDS = 120

# Cap how many individual days we enumerate in --dry-run output before
# summarizing, so a 10-year dry run doesn't print 3,600 lines.
_DRY_RUN_SAMPLE = 14


# ---------------------------------------------------------------------------
# Day arithmetic (stdlib only)
# ---------------------------------------------------------------------------


def _day_floor(dt: datetime) -> datetime:
    """First instant of dt's calendar day, UTC."""
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


def _next_day(dt: datetime) -> datetime:
    """First instant of the day after dt's calendar day, UTC."""
    return _day_floor(dt) + timedelta(days=1)


def _day_key(dt: datetime) -> str:
    """Stable 'YYYY-MM-DD' key for checkpointing and logging."""
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"


def iter_days(start: datetime, end: datetime) -> Iterable[tuple[datetime, datetime]]:
    """
    Yield (day_start, day_end) UTC pairs covering [start, end), one per
    calendar day. Edges are honored exactly: the first pair starts at `start`
    (NOT floored to the month or even the day), and the last pair ends at
    `end`. In normal use start/end are midnight, so this yields clean
    midnight-to-midnight days.

    This is the core fix over v1: no flooring to month boundaries, so the
    window you ask for is the window you get.
    """
    cursor = start
    while cursor < end:
        day_end = min(_next_day(cursor), end)
        yield cursor, day_end
        cursor = _next_day(cursor)


def _parse_date_arg(value: str) -> datetime:
    """Parse a 'YYYY-MM-DD' or 'YYYY-MM' CLI arg into a UTC datetime.

    'YYYY-MM' is interpreted as the first day of that month.
    """
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid date {value!r}; expected YYYY-MM-DD or YYYY-MM"
    )


def _expects_plasma(day_start: datetime) -> bool:
    """Whether a given day is inside the DSCOVR_H1_FC plasma coverage window."""
    return day_start < DSCOVR_H1_FC_END_DATE


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


@dataclass
class Checkpoint:
    """
    Persistent backfill progress, stored as JSON next to the corpus.

    completed: day keys (YYYY-MM-DD) fully written (skipped on resume).
    failed:    day keys that exhausted retries (re-runnable via --retry-failed).
    totals:    cumulative row tallies across the whole backfill, for honesty.
    """

    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    totals: dict[str, int] = field(
        default_factory=lambda: {
            "mag_written": 0,
            "mag_dropped_fill": 0,
            "plasma_written": 0,
            "plasma_dropped_fill": 0,
            "plasma_dropped_dqf": 0,
            "dropped_other": 0,
        }
    )
    last_updated: str = ""

    @classmethod
    def load(cls, path: Path) -> "Checkpoint":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("checkpoint_unreadable_starting_fresh", error=str(e))
            return cls()
        cp = cls(
            completed=list(data.get("completed", [])),
            failed=list(data.get("failed", [])),
            last_updated=data.get("last_updated", ""),
        )
        for k, v in data.get("totals", {}).items():
            cp.totals[k] = v
        return cp

    def save(self, path: Path) -> None:
        self.last_updated = datetime.now(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Temp-then-replace so an interrupted write can't corrupt a good file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")
        tmp.replace(path)

    def add_totals(self, reports: dict[str, WriteReport]) -> None:
        mag = reports.get("mag")
        plasma = reports.get("plasma")
        if mag is not None:
            self.totals["mag_written"] += mag.rows_written
            self.totals["mag_dropped_fill"] += mag.rows_dropped_fill
            self.totals["dropped_other"] += mag.rows_dropped_other
        if plasma is not None:
            self.totals["plasma_written"] += plasma.rows_written
            self.totals["plasma_dropped_fill"] += plasma.rows_dropped_fill
            self.totals["plasma_dropped_dqf"] += plasma.rows_dropped_dqf
            self.totals["dropped_other"] += plasma.rows_dropped_other


# ---------------------------------------------------------------------------
# Backfill core
# ---------------------------------------------------------------------------


def _fetch_and_write_chunk(
    ingester: NOAADscovrArchiveIngester,
    chunk_start: datetime,
    chunk_end: datetime,
    root: Path,
) -> dict[str, WriteReport]:
    """
    Fetch one day and write it to the corpus. Raises on failure so the retry
    wrapper can decide whether to try again.

    fetch_packets is a generator; we materialize it into a list so a mid-stream
    network error surfaces here (inside the retry boundary) rather than lazily
    during the write. One day is ~88k small packets — fine in memory.
    """
    packets = list(
        ingester.fetch_packets(SPACECRAFT_DSCOVR, chunk_start, chunk_end)
    )
    return write_partitioned_parquet(packets, root)


def _process_day_with_retries(
    ingester: NOAADscovrArchiveIngester,
    day_start: datetime,
    day_end: datetime,
    root: Path,
    max_retries: int,
    backoff: float,
) -> dict[str, WriteReport] | None:
    """
    Try a day up to max_retries times. Returns write reports on success, or
    None if every attempt failed (caller logs it as a failed day).
    """
    key = _day_key(day_start)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return _fetch_and_write_chunk(ingester, day_start, day_end, root)
        except (DataSourceUnavailableError, PacketParseError, OSError) as e:
            last_error = e
            if attempt < max_retries:
                wait = backoff * attempt
                logger.warning(
                    "day_fetch_failed_retrying",
                    day=key,
                    attempt=attempt,
                    max_retries=max_retries,
                    wait_seconds=wait,
                    error=str(e),
                )
                time.sleep(wait)
            else:
                logger.error(
                    "day_fetch_failed_giving_up",
                    day=key,
                    attempts=max_retries,
                    error=str(e),
                )
        except Exception as e:  # noqa: BLE001 — last-resort guard for one day
            last_error = e
            logger.error(
                "day_unexpected_error_giving_up",
                day=key,
                error=str(e),
                error_type=type(e).__name__,
            )
            break

    logger.error("day_marked_failed", day=key, final_error=str(last_error))
    return None


def _log_day_result(day_key: str, reports: dict[str, WriteReport]) -> None:
    """Honest per-day summary — including when a day writes nothing."""
    mag = reports.get("mag", WriteReport(instrument="mag"))
    plasma = reports.get("plasma", WriteReport(instrument="plasma"))
    total_written = mag.rows_written + plasma.rows_written

    log = logger.info if total_written > 0 else logger.warning
    log(
        "day_complete",
        day=day_key,
        mag_seen=mag.rows_seen,
        mag_written=mag.rows_written,
        mag_dropped_fill=mag.rows_dropped_fill,
        plasma_seen=plasma.rows_seen,
        plasma_written=plasma.rows_written,
        plasma_dropped_fill=plasma.rows_dropped_fill,
        plasma_dropped_dqf=plasma.rows_dropped_dqf,
        total_written=total_written,
        note=None if total_written > 0 else "day wrote zero rows (may be a real data gap)",
    )


def _emit_dry_run(days: list[tuple[datetime, datetime]]) -> None:
    """Summarize what a real run would do, without fetching anything."""
    plasma_days = sum(1 for s, _ in days if _expects_plasma(s))
    logger.info(
        "dry_run_summary",
        total_days=len(days),
        first_day=_day_key(days[0][0]),
        last_day=_day_key(days[-1][0]),
        days_with_plasma=plasma_days,
        days_mag_only=len(days) - plasma_days,
        plasma_cutoff=_day_key(DSCOVR_H1_FC_END_DATE),
    )
    if len(days) <= _DRY_RUN_SAMPLE:
        sample = days
        for s, e in sample:
            logger.info(
                "dry_run_day",
                day=_day_key(s),
                window=f"{s.isoformat()} -> {e.isoformat()}",
                expect_plasma=_expects_plasma(s),
            )
    else:
        half = _DRY_RUN_SAMPLE // 2
        for s, e in days[:half]:
            logger.info("dry_run_day", day=_day_key(s), expect_plasma=_expects_plasma(s))
        logger.info("dry_run_elided", omitted_days=len(days) - 2 * half)
        for s, e in days[-half:]:
            logger.info("dry_run_day", day=_day_key(s), expect_plasma=_expects_plasma(s))
    logger.info("dry_run_complete", would_process=len(days))


def run_backfill(
    root: Path,
    start: datetime,
    end: datetime,
    *,
    max_retries: int,
    backoff: float,
    retry_failed: bool,
    dry_run: bool,
    timeout_seconds: int,
) -> int:
    """
    Execute the backfill. Returns a process exit code: 0 if every attempted
    day succeeded, 1 if any day ended up in the failed list.
    """
    checkpoint_path = root / CHECKPOINT_NAME
    cp = Checkpoint.load(checkpoint_path)

    days = list(iter_days(start, end))
    if not days:
        logger.error(
            "no_days_in_range", start=start.isoformat(), end=end.isoformat()
        )
        return 1

    completed = set(cp.completed)
    if retry_failed:
        wanted = set(cp.failed)
        todo = [(s, e) for (s, e) in days if _day_key(s) in wanted]
        cp.failed = []  # days that fail again get re-added below
        logger.info("retry_failed_mode", days_to_retry=len(todo))
    else:
        todo = [(s, e) for (s, e) in days if _day_key(s) not in completed]

    logger.info(
        "backfill_starting",
        root=str(root),
        range_start=_day_key(start),
        range_end=_day_key(end),
        total_days_in_range=len(days),
        already_completed=len(completed),
        days_to_process=len(todo),
        dry_run=dry_run,
        plasma_cutoff=_day_key(DSCOVR_H1_FC_END_DATE),
    )

    if dry_run:
        _emit_dry_run(todo if todo else days)
        return 0

    ingester = NOAADscovrArchiveIngester(timeout_seconds=timeout_seconds)

    succeeded = 0
    failed = 0
    for s, e in todo:
        key = _day_key(s)
        reports = _process_day_with_retries(
            ingester, s, e, root, max_retries, backoff
        )
        if reports is None:
            failed += 1
            if key not in cp.failed:
                cp.failed.append(key)
                cp.failed.sort()
        else:
            succeeded += 1
            _log_day_result(key, reports)
            cp.add_totals(reports)
            if key not in cp.completed:
                cp.completed.append(key)
                cp.completed.sort()
        # Persist after every day — this is what makes the run resumable.
        cp.save(checkpoint_path)

    logger.info(
        "backfill_complete",
        succeeded_this_run=succeeded,
        failed_this_run=failed,
        total_completed=len(cp.completed),
        total_failed=len(cp.failed),
        failed_days=cp.failed if cp.failed else None,
        cumulative_mag_written=cp.totals["mag_written"],
        cumulative_plasma_written=cp.totals["plasma_written"],
        cumulative_mag_dropped_fill=cp.totals["mag_dropped_fill"],
        cumulative_plasma_dropped_fill=cp.totals["plasma_dropped_fill"],
        cumulative_plasma_dropped_dqf=cp.totals["plasma_dropped_dqf"],
    )

    try:
        with CorpusReader(root) as reader:
            logger.info(
                "corpus_extent",
                mag_rows=reader.count("mag"),
                plasma_rows=reader.count("plasma"),
            )
    except Exception as e:  # noqa: BLE001 — extent read is best-effort
        logger.warning("corpus_extent_unavailable", error=str(e))

    return 0 if not cp.failed else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Backfill the DSCOVR Parquet corpus from CDAWeb HAPI, one "
        "day at a time, with resumable per-day checkpoints.",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"Corpus root directory (default: {DEFAULT_ROOT}). Checkpoint is "
        f"stored at <root>/{CHECKPOINT_NAME}.",
    )
    p.add_argument(
        "--start",
        type=_parse_date_arg,
        default=None,
        help="Start date YYYY-MM-DD (or YYYY-MM = first of month). Default: "
        f"DSCOVR operational date {_day_key(DSCOVR_OPERATIONAL_DATE)}. Honored "
        "exactly — not floored.",
    )
    p.add_argument(
        "--end",
        type=_parse_date_arg,
        default=None,
        help="End date YYYY-MM-DD (exclusive). Default: today (UTC), so the "
        "last fetched day is yesterday. MAG covers to present; plasma stops at "
        f"{_day_key(DSCOVR_H1_FC_END_DATE)} regardless.",
    )
    p.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-attempt only the days currently in the checkpoint's failed "
        "list, instead of walking the whole range.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="List the days that would be processed without fetching anything. "
        "Use this to sanity-check the range and resume state.",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Attempts per day before marking it failed (default: "
        f"{DEFAULT_MAX_RETRIES}).",
    )
    p.add_argument(
        "--retry-backoff",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help="Base seconds between retries, multiplied by attempt number "
        f"(default: {DEFAULT_RETRY_BACKOFF_SECONDS}).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-request HAPI timeout in seconds (default: "
        f"{DEFAULT_TIMEOUT_SECONDS}).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    start = args.start or _day_floor(DSCOVR_OPERATIONAL_DATE)
    end = args.end or _day_floor(datetime.now(timezone.utc))

    if start >= end:
        logger.error(
            "start_not_before_end", start=start.isoformat(), end=end.isoformat()
        )
        return 1

    return run_backfill(
        root=args.root,
        start=start,
        end=end,
        max_retries=args.max_retries,
        backoff=args.retry_backoff,
        retry_failed=args.retry_failed,
        dry_run=args.dry_run,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
