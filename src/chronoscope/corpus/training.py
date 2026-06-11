"""
Build a labeled, analysis-ready feature matrix from the corpus + labels.

Resamples telemetry to a regular cadence (default hourly), aligns the
geomagnetic (Kp / g_scale) and Richardson-Cane ICME interval labels onto the
same grid, and materializes the result as a single Parquet. This is the direct
input to Phase 2 causal discovery, and it does the expensive interval-join
ONCE at build time (after downsampling) so the causal pipeline never recomputes
the full-resolution BETWEEN join over 271M telemetry rows.

The grid is a COMPLETE regular spine from the first to the last telemetry hour;
hours with no telemetry appear as rows with NULL features (so the series keeps
a regular cadence, which PCMCI assumes) rather than being silently absent.

Output columns (one row per cadence step):
  timestamp        - start of the cadence bucket (UTC)
  bz_mean, bz_min  - GSE Bz bucket mean / most-southward (peak) value (nT)
  bt_mean, bt_max  - field magnitude bucket mean / max (nT)
  mag_n            - MAG samples in the bucket (0 = gap)
  sw_speed_mean    - bulk speed mean (km/s); plasma, NULL after 2019-06-27
  sw_speed_max     - bulk speed max (km/s)
  density_mean     - proton density mean (cm^-3)
  temp_mean        - ion temperature mean (K)
  plasma_n         - plasma samples in the bucket (0 = gap / post-2019)
  kp, g_scale      - prevailing 3-hourly Kp / derived G-scale (ASOF join)
  in_icme          - bucket falls inside a Richardson-Cane ICME passage
  icme_mc_flag     - structure of the covering ICME (2=MC, 1=partial, 0=ejecta)
  icme_dst_min     - min Dst of the covering ICME (nT)

Both label sets (kp and icme) must exist under the corpus before building;
run the geomagnetic and icme label builders first.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import structlog

from src.chronoscope.corpus.storage import CorpusReader

logger = structlog.get_logger(__name__)

# cadence name -> (date_trunc unit, generate_series interval literal, default output)
_CADENCE = {
    "hour": ("hour", "INTERVAL '1 hour'", Path("derived") / "hourly_features.parquet"),
    "minute": ("minute", "INTERVAL '1 minute'", Path("derived") / "minute_features.parquet"),
}


def _features_sql(trunc: str, step: str) -> str:
    """
    Build the resample + label-alignment query for a given cadence.

    `trunc` and `step` come from the fixed _CADENCE map (never user input),
    so the f-string interpolation is safe.
    """
    return f"""
    WITH bounds AS (
        SELECT date_trunc('{trunc}', min(timestamp)) AS t0,
               date_trunc('{trunc}', max(timestamp)) AS t1
        FROM mag
    ),
    spine AS (
        SELECT unnest(
            generate_series((SELECT t0 FROM bounds),
                            (SELECT t1 FROM bounds),
                            {step})
        ) AS ts
    ),
    mag_b AS (
        SELECT date_trunc('{trunc}', timestamp) AS ts,
               avg(bz_gse_nt) AS bz_mean,
               min(bz_gse_nt) AS bz_min,
               avg(bt_nt)     AS bt_mean,
               max(bt_nt)     AS bt_max,
               count(*)       AS mag_n
        FROM mag
        GROUP BY 1
    ),
    plasma_b AS (
        SELECT date_trunc('{trunc}', timestamp) AS ts,
               avg(bulk_speed_km_s)    AS sw_speed_mean,
               max(bulk_speed_km_s)    AS sw_speed_max,
               avg(proton_density_n_cc) AS density_mean,
               avg(ion_temperature_k)   AS temp_mean,
               count(*)                 AS plasma_n
        FROM plasma
        GROUP BY 1
    ),
    base AS (
        SELECT s.ts,
               m.bz_mean, m.bz_min, m.bt_mean, m.bt_max,
               coalesce(m.mag_n, 0) AS mag_n,
               p.sw_speed_mean, p.sw_speed_max, p.density_mean, p.temp_mean,
               coalesce(p.plasma_n, 0) AS plasma_n
        FROM spine s
        LEFT JOIN mag_b m ON s.ts = m.ts
        LEFT JOIN plasma_b p ON s.ts = p.ts
    ),
    with_kp AS (
        SELECT b.*, k.kp, k.g_scale
        FROM base b
        ASOF LEFT JOIN kp k ON b.ts >= k.timestamp
    )
    SELECT w.ts AS timestamp,
           w.bz_mean, w.bz_min, w.bt_mean, w.bt_max, w.mag_n,
           w.sw_speed_mean, w.sw_speed_max, w.density_mean, w.temp_mean, w.plasma_n,
           w.kp, w.g_scale,
           (i.source_row IS NOT NULL) AS in_icme,
           i.mc_flag    AS icme_mc_flag,
           i.dst_min_nt AS icme_dst_min
    FROM with_kp w
    LEFT JOIN icme i ON w.ts BETWEEN i.icme_start AND i.icme_end
    QUALIFY row_number() OVER (
        PARTITION BY w.ts ORDER BY i.dst_min_nt ASC NULLS LAST
    ) = 1
    ORDER BY w.ts
    """


def build_hourly_features(
    root: str | Path,
    *,
    cadence: str = "hour",
    out_relpath: str | Path | None = None,
) -> Path:
    """
    Materialize the labeled feature matrix under <root>/<out_relpath>.

    Requires both the `kp` and `icme` label views (raises if missing). Writes
    zstd Parquet via a single DuckDB COPY (streams; safe at minute cadence).
    Returns the output path.
    """
    if cadence not in _CADENCE:
        raise ValueError(f"cadence must be one of {sorted(_CADENCE)}; got {cadence!r}")
    trunc, step, default_out = _CADENCE[cadence]
    out = Path(root) / (Path(out_relpath) if out_relpath else default_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_str = str(out).replace("\\", "/")

    with CorpusReader(root) as reader:
        if not reader.has_labels():
            raise RuntimeError(
                "Geomagnetic `kp` labels not found. Run the geomagnetic label "
                "builder before building the training dataset."
            )
        if not reader.has_icme_labels():
            raise RuntimeError(
                "`icme` labels not found. Run the icme label builder before "
                "building the training dataset."
            )
        logger.info("training_build_start", cadence=cadence, out=str(out))
        sql = _features_sql(trunc, step)
        reader.query(f"COPY ({sql}) TO '{out_str}' (FORMAT parquet, COMPRESSION zstd)")
        logger.info("training_build_written", path=str(out))

    return out


def _summary(root: str | Path, out: Path) -> dict:
    """Read the written file back and compute headline stats for the CLI."""
    out_str = str(out).replace("\\", "/")
    with CorpusReader(root) as reader:
        row = reader.query(
            f"""
            SELECT count(*) AS rows,
                   min(timestamp) AS t0,
                   max(timestamp) AS t1,
                   sum(CASE WHEN mag_n > 0 THEN 1 ELSE 0 END)        AS with_mag,
                   sum(CASE WHEN kp IS NOT NULL THEN 1 ELSE 0 END)   AS with_kp,
                   sum(CASE WHEN plasma_n > 0 THEN 1 ELSE 0 END)     AS with_plasma,
                   sum(CASE WHEN in_icme THEN 1 ELSE 0 END)          AS in_icme,
                   corr(bz_min, kp)                                  AS corr_bzmin_kp
            FROM read_parquet('{out_str}')
            """
        )[0]
    keys = ["rows", "t0", "t1", "with_mag", "with_kp", "with_plasma", "in_icme", "corr_bzmin_kp"]
    return dict(zip(keys, row))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the labeled training feature matrix.")
    parser.add_argument("--root", required=True, help="Corpus root (contains telemetry + labels/).")
    parser.add_argument("--cadence", default="hour", choices=sorted(_CADENCE), help="Resample cadence.")
    parser.add_argument("--out", default=None, help="Output relpath under root (optional).")
    args = parser.parse_args()

    out = build_hourly_features(args.root, cadence=args.cadence, out_relpath=args.out)
    s = _summary(args.root, out)

    pct = (100.0 * s["with_mag"] / s["rows"]) if s["rows"] else 0.0
    corr = s["corr_bzmin_kp"]
    corr_str = f"{corr:+.3f}" if corr is not None else "n/a"
    print("=" * 60)
    print("Labeled training feature matrix written")
    print("=" * 60)
    print(f"path         : {out}")
    print(f"cadence      : {args.cadence}")
    print(f"rows (grid)  : {s['rows']:,}")
    print(f"range        : {s['t0']} -> {s['t1']}")
    print(f"with MAG     : {s['with_mag']:,} ({pct:.1f}% of grid)")
    print(f"with Kp      : {s['with_kp']:,}")
    print(f"with plasma  : {s['with_plasma']:,} (pre-2019 era only)")
    print(f"in ICME      : {s['in_icme']:,} buckets inside ICME passages")
    print(f"corr(bz_min, kp): {corr_str}  (expect NEGATIVE: southward field <-> stronger activity)")
    print("=" * 60)
    print("NOTE: corr is a sanity check, NOT a causal result. Phase 2 (PCMCI)")
    print("      produces the actual causal graph from this matrix.")


if __name__ == "__main__":
    main()
