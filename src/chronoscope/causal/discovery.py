"""
Causal discovery via PCMCI (Tigramite) over the labeled feature matrix.

Runs the PCMCI algorithm with a partial-correlation conditional-independence
test on the hourly feature matrix (DEC-009) and returns a CausalGraph. PCMCI
is well suited here: it tests *lagged* conditional independence, so it both
orients links by time (Bz at t-tau -> Kp at t, never the reverse for tau>0) and
conditions out confounders -- exactly what's needed to go beyond the -0.549
contemporaneous correlation toward a directed causal edge.

Tigramite is an optional dependency; it is imported lazily so the rest of the
causal package (graph, evaluation) works without it.

CLI:
    python -m src.chronoscope.causal.discovery --root <corpus> [--vars bz_min,bt_max,kp]
                                                [--tau-max 6] [--alpha 0.01] [--save]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import structlog

from src.chronoscope.causal.evaluation import score_against_known_physics
from src.chronoscope.causal.graph import CausalGraph
from src.chronoscope.corpus.training import HOURLY_FEATURES_RELPATH

logger = structlog.get_logger(__name__)

# Variables present over the full corpus span (MAG-derived + Kp). Plasma vars
# (sw_speed_mean, density_mean) exist only pre-2019; pass them via --vars to run
# the richer, shorter-span discovery.
DEFAULT_VARIABLES = ["bz_min", "bt_max", "kp"]

# Effect-size floor for the default run. With ~85k highly autocorrelated hourly
# samples, naive p-values flag trivially-small partial correlations as wildly
# significant, so significance is a poor filter and effect size is the right
# lens. |partial correlation| < 0.1 explains <1% of variance -- negligible by
# Cohen's small-effect convention. Filtering at this floor by default keeps the
# verdict robust (weak reverse-causation artifacts from autocorrelation and
# unconditioned solar-cycle/rotation common-mode drop out, the dominant causal
# link survives). Use --min-strength 0 to see the full raw graph.
NEGLIGIBLE_EFFECT = 0.1

MISSING_FLAG = 999.0


def run_pcmci(
    data: np.ndarray,
    var_names: list[str],
    *,
    tau_max: int = 6,
    pc_alpha: float = 0.01,
    alpha_level: float | None = None,
    min_strength: float = 0.0,
    missing_flag: float = MISSING_FLAG,
) -> CausalGraph:
    """
    Run PCMCI on a (T, N) array (NaN allowed) and return a CausalGraph.

    Edges use the time-series convention: source at t-lag -> target at t.

    A link is recorded when its MCI p-value < ``alpha_level`` (defaults to
    ``pc_alpha``) AND |MCI statistic| >= ``min_strength``. For lagged links
    (tau > 0) the direction is fixed by time. For contemporaneous links
    (tau = 0) only tigramite-oriented links ('-->') are kept, since same-step
    direction is otherwise ambiguous. Applying one explicit significance
    threshold uniformly (rather than tigramite's internal default) keeps weak
    spurious reverse edges -- a known artifact between autocorrelated coupled
    series -- from being recorded.
    """
    from tigramite import data_processing as pp
    from tigramite.independence_tests.parcorr import ParCorr
    from tigramite.pcmci import PCMCI

    if alpha_level is None:
        alpha_level = pc_alpha
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"data must be 2D (T, N); got shape {arr.shape}")
    if arr.shape[1] != len(var_names):
        raise ValueError(f"{arr.shape[1]} columns but {len(var_names)} var_names")
    # tigramite uses a numeric sentinel for missing values, not NaN
    arr = np.where(np.isnan(arr), missing_flag, arr)

    dataframe = pp.DataFrame(arr, var_names=list(var_names), missing_flag=missing_flag)
    pcmci = PCMCI(dataframe=dataframe, cond_ind_test=ParCorr(), verbosity=0)
    results = pcmci.run_pcmci(tau_max=tau_max, pc_alpha=pc_alpha)

    graph = CausalGraph(var_names)
    link_arr = results.get("graph")
    val = results["val_matrix"]
    pmat = results["p_matrix"]
    n = len(var_names)
    for i in range(n):
        for j in range(n):
            for tau in range(0, tau_max + 1):
                if tau == 0 and i == j:
                    continue
                p = float(pmat[i, j, tau])
                strength = float(val[i, j, tau])
                significant = p < alpha_level and abs(strength) >= min_strength
                if tau == 0:
                    # contemporaneous direction is ambiguous; trust only oriented links
                    oriented = link_arr is not None and link_arr[i, j, 0] == "-->"
                    keep = significant and oriented
                else:
                    keep = significant
                if keep:
                    graph.add_edge(var_names[i], var_names[j], tau, strength, p)
    return graph


def _load_matrix(root: str | Path, var_names: list[str]) -> tuple[np.ndarray, dict]:
    """Load the requested columns from the hourly feature matrix as a (T, N) array."""
    import pandas as pd

    path = Path(root) / HOURLY_FEATURES_RELPATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build it first: "
            f"python -m src.chronoscope.corpus.training --root {root}"
        )
    df = pd.read_parquet(path).sort_values("timestamp").reset_index(drop=True)
    missing_cols = [c for c in var_names if c not in df.columns]
    if missing_cols:
        raise ValueError(f"columns not in feature matrix: {missing_cols}; "
                         f"available: {list(df.columns)}")
    sub = df[var_names].astype(float)
    meta = {
        "n_rows": len(df),
        "time_start": df["timestamp"].iloc[0],
        "time_end": df["timestamp"].iloc[-1],
        "missing_per_var": {c: int(sub[c].isna().sum()) for c in var_names},
    }
    return sub.to_numpy(), meta


def discover_from_corpus(
    root: str | Path,
    *,
    var_names: list[str] | None = None,
    tau_max: int = 6,
    pc_alpha: float = 0.01,
    min_strength: float = 0.0,
) -> tuple[CausalGraph, dict]:
    """Load the feature matrix and run PCMCI; returns (graph, metadata)."""
    var_names = list(var_names) if var_names else list(DEFAULT_VARIABLES)
    data, meta = _load_matrix(root, var_names)
    logger.info("pcmci_start", vars=var_names, tau_max=tau_max, alpha=pc_alpha,
                rows=meta["n_rows"])
    graph = run_pcmci(data, var_names, tau_max=tau_max, pc_alpha=pc_alpha,
                      min_strength=min_strength)
    logger.info("pcmci_done", edges=len(graph))
    meta["var_names"] = var_names
    meta["tau_max"] = tau_max
    meta["pc_alpha"] = pc_alpha
    meta["min_strength"] = min_strength
    return graph, meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PCMCI causal discovery on the feature matrix.")
    parser.add_argument("--root", required=True, help="Corpus root (contains derived/hourly_features.parquet).")
    parser.add_argument("--vars", default=None,
                        help=f"Comma-separated variables (default: {','.join(DEFAULT_VARIABLES)}).")
    parser.add_argument("--tau-max", type=int, default=6, help="Max lag in hours.")
    parser.add_argument("--alpha", type=float, default=0.01, help="PC significance level (also the edge-recording MCI threshold).")
    parser.add_argument("--min-strength", type=float, default=NEGLIGIBLE_EFFECT,
                        help=(f"Effect-size floor: drop edges with |MCI statistic| below this "
                              f"(default {NEGLIGIBLE_EFFECT} = negligible-effect boundary). "
                              f"Use 0 to see the full raw graph including weak artifacts."))
    parser.add_argument("--save", action="store_true",
                        help="Write the graph to <root>/derived/causal_graph.json.")
    args = parser.parse_args()

    var_names = [s.strip() for s in args.vars.split(",")] if args.vars else None
    graph, meta = discover_from_corpus(args.root, var_names=var_names,
                                       tau_max=args.tau_max, pc_alpha=args.alpha,
                                       min_strength=args.min_strength)

    print("=" * 64)
    print("PCMCI causal discovery")
    print("=" * 64)
    print(f"variables : {meta['var_names']}")
    print(f"rows      : {meta['n_rows']:,}  ({meta['time_start']} -> {meta['time_end']})")
    print(f"tau_max   : {meta['tau_max']}   alpha: {meta['pc_alpha']}   "
          f"min_strength: {meta['min_strength']}")
    print(f"missing   : {meta['missing_per_var']}")
    if meta["min_strength"] > 0:
        print(f"(edges with |MCI| < {meta['min_strength']} are filtered as negligible; "
              f"--min-strength 0 shows all)")
    print("-" * 64)
    print(graph.summary())
    print("-" * 64)
    card = score_against_known_physics(graph)
    print(card.summary())
    print("=" * 64)
    if not card.passed:
        print("NOTE: a FAIL here is informative, not a crash -- it means PCMCI did")
        print("      not (yet) recover the expected directed physics with these")
        print("      variables/lags/alpha. Tune --vars, --tau-max, --alpha and re-run.")

    if args.save:
        import json
        out = Path(args.root) / "derived" / "causal_graph.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"graph": graph.to_dict(), "metadata": {
            **meta,
            "time_start": str(meta["time_start"]),
            "time_end": str(meta["time_end"]),
        }, "passed_known_physics": card.passed}
        out.write_text(json.dumps(payload, indent=2))
        print(f"saved: {out}")


if __name__ == "__main__":
    main()
