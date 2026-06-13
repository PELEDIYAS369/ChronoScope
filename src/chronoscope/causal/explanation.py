"""
Causal explanation: attribute geomagnetic events to their causal drivers.

Causal discovery (discovery.py) tells us the STRUCTURE -- which variables, at
which lags, causally drive Kp. This module estimates the MAGNITUDES by fitting a
linear structural equation on exactly those discovered parents (and no spurious
extras), then decomposes a specific event into per-driver contributions.

For an event at time T:
    kp(T) ~ intercept + sum_d  coef_d * driver_d(T - lag_d)
the exogenous-driver terms (e.g. bz_min at lag 1) are the "cause" of the
excursion, while the autoregressive terms (kp's own past) are persistence -- "it
was already elevated". The explanation separates the two.

This is causal-effect estimation UNDER the discovered DAG assuming roughly
linear effects -- an honest attribution, not a counterfactual guarantee. The
output and docs frame it that way.

CLI:
    python -m src.chronoscope.causal.explanation --root <corpus> [--top 5]
    python -m src.chronoscope.causal.explanation --root <corpus> --vars bz_min,bt_max,kp
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import structlog

from src.chronoscope.causal.discovery import DEFAULT_VARIABLES, discover_from_corpus
from src.chronoscope.causal.graph import CausalGraph
from src.chronoscope.corpus.training import HOURLY_FEATURES_RELPATH

logger = structlog.get_logger(__name__)

CAUSAL_GRAPH_RELPATH = Path("derived") / "causal_graph.json"


def fit_structural_model(df, graph: CausalGraph, target: str = "kp", *,
                         include_autoregressive: bool = True,
                         exogenous_lags: int | None = None) -> dict:
    """
    Fit kp(t) ~ intercept + sum coef * parent(t-lag) by OLS over the causal
    parents of `target` in `graph`, on rows where all terms exist.

    Two attribution views:
      * Full structural model (include_autoregressive=True): uses the discovered
        parents including target autocorrelation. High R^2, but the persistence
        term absorbs most of the driver's effect (it already showed up in the
        previous hour's value), so it understates the exogenous cause.
      * Driver attribution (include_autoregressive=False, exogenous_lags=W):
        drops the autoregressive terms and spreads each exogenous driver over
        lags 1..W. This estimates the CUMULATIVE causal effect of the sustained
        solar-wind forcing -- physically motivated (the magnetosphere integrates
        southward Bz over hours), and the honest view for attributing a storm to
        its driver rather than to its own past.

    Returns a model dict with parents (var, lag, coef), intercept, r2, n.
    """
    parents = [(e.source, e.lag) for e in graph.edges_into(target)]
    if not include_autoregressive:
        parents = [(v, l) for (v, l) in parents if v != target]
    if exogenous_lags is not None:
        auto = [(v, l) for (v, l) in parents if v == target]
        exo_vars = sorted({v for (v, l) in parents if v != target})
        parents = auto + [(v, lag) for v in exo_vars for lag in range(1, exogenous_lags + 1)]
    if not parents:
        raise ValueError(
            f"graph has no usable causal parents of {target!r} "
            f"(include_autoregressive={include_autoregressive}); nothing to fit")

    y = df[target].to_numpy(dtype=float)
    cols, names = [], []
    for var, lag in parents:
        if var not in df.columns:
            raise ValueError(f"parent {var!r} not in feature matrix columns")
        cols.append(df[var].shift(lag).to_numpy(dtype=float))  # value at t-lag aligned to t
        names.append((var, lag))
    X = np.column_stack(cols)

    mask = ~np.isnan(y)
    for c in range(X.shape[1]):
        mask &= ~np.isnan(X[:, c])
    Xv, yv = X[mask], y[mask]
    if len(yv) < X.shape[1] + 2:
        raise ValueError("not enough complete rows to fit the structural model")

    A = np.column_stack([np.ones(len(Xv)), Xv])
    coef, *_ = np.linalg.lstsq(A, yv, rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((yv - pred) ** 2))
    ss_tot = float(np.sum((yv - yv.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {
        "target": target,
        "intercept": float(coef[0]),
        "parents": [{"var": v, "lag": l, "coef": float(b)}
                    for (v, l), b in zip(names, coef[1:])],
        "r2": float(r2),
        "n": int(mask.sum()),
    }


def explain_event(df, model: dict, idx: int) -> dict:
    """Decompose the target at row `idx` into per-parent contributions."""
    target = model["target"]
    contribs = []
    for p in model["parents"]:
        src_idx = idx - p["lag"]
        val = float(df[p["var"]].iloc[src_idx]) if src_idx >= 0 else float("nan")
        contribs.append({"var": p["var"], "lag": p["lag"], "coef": p["coef"],
                         "value": val, "contribution": p["coef"] * val})
    observed = float(df[target].iloc[idx])
    predicted = model["intercept"] + sum(
        c["contribution"] for c in contribs if not np.isnan(c["contribution"]))
    return {
        "time": df["timestamp"].iloc[idx],
        "observed": observed,
        "predicted": float(predicted),
        "intercept": model["intercept"],
        "contributions": contribs,
    }


def format_explanation(exp: dict, target: str = "kp") -> str:
    exo = [c for c in exp["contributions"] if c["var"] != target and not np.isnan(c["contribution"])]
    auto = [c for c in exp["contributions"] if c["var"] == target and not np.isnan(c["contribution"])]
    exo.sort(key=lambda c: -abs(c["contribution"]))

    when = exp["time"]
    lines = [f"{when}  {target} = {exp['observed']:.2f}  (model {exp['predicted']:.2f})"]
    if exo:
        top = exo[0]
        lines.append(f"    primary cause : {top['var']} = {top['value']:+.1f} at lag {top['lag']}h "
                     f"-> {top['contribution']:+.2f} to {target}")
        for c in exo[1:]:
            lines.append(f"    also          : {c['var']} = {c['value']:+.1f} (lag {c['lag']}h) "
                         f"-> {c['contribution']:+.2f}")
    if auto:
        auto_sum = sum(c["contribution"] for c in auto)
        lines.append(f"    persistence   : prior {target} -> {auto_sum:+.2f}")
    lines.append(f"    baseline      : {exp['intercept']:+.2f}")
    return "\n".join(lines)


def _load_graph(root: str | Path, var_names: list[str] | None) -> tuple[CausalGraph, list[str]]:
    """Load the saved causal graph if present, else run discovery to produce one."""
    gpath = Path(root) / CAUSAL_GRAPH_RELPATH
    if var_names is None and gpath.exists():
        payload = json.loads(gpath.read_text())
        graph = CausalGraph.from_dict(payload["graph"])
        logger.info("explanation_graph_loaded", path=str(gpath), vars=graph.variables)
        return graph, list(graph.variables)
    names = list(var_names) if var_names else list(DEFAULT_VARIABLES)
    graph, _meta = discover_from_corpus(root, var_names=names)
    logger.info("explanation_graph_discovered", vars=names)
    return graph, names


def explain_top_events(root: str | Path, *, target: str = "kp",
                       var_names: list[str] | None = None, top: int = 5,
                       include_autoregressive: bool = True,
                       exogenous_lags: int | None = None) -> dict:
    """Fit the structural model and explain the strongest `target` events."""
    import pandas as pd

    path = Path(root) / HOURLY_FEATURES_RELPATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Build it: python -m src.chronoscope.corpus.training --root {root}")
    graph, names = _load_graph(root, var_names)
    df = pd.read_parquet(path).sort_values("timestamp").reset_index(drop=True)
    model = fit_structural_model(df, graph, target=target,
                                 include_autoregressive=include_autoregressive,
                                 exogenous_lags=exogenous_lags)

    order = df[target].to_numpy()
    top_positions = np.argsort(np.nan_to_num(order, nan=-np.inf))[::-1][:top]
    explanations = [explain_event(df, model, int(i)) for i in sorted(top_positions, key=lambda i: -order[i])]
    return {"model": model, "explanations": explanations}


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain geomagnetic events via the discovered causal model.")
    parser.add_argument("--root", required=True, help="Corpus root.")
    parser.add_argument("--target", default="kp", help="Variable to explain (default kp).")
    parser.add_argument("--vars", default=None, help="Override variables (else use saved graph or defaults).")
    parser.add_argument("--top", type=int, default=5, help="Number of strongest events to explain.")
    parser.add_argument("--exogenous", action="store_true",
                        help="Driver attribution: exclude Kp persistence and spread drivers over a "
                             "lag window (the cumulative-forcing view; honest for storm attribution).")
    parser.add_argument("--exo-window", type=int, default=6,
                        help="Lag window (hours) for --exogenous attribution (default 6).")
    args = parser.parse_args()

    var_names = [s.strip() for s in args.vars.split(",")] if args.vars else None
    out = explain_top_events(
        args.root, target=args.target, var_names=var_names, top=args.top,
        include_autoregressive=not args.exogenous,
        exogenous_lags=args.exo_window if args.exogenous else None,
    )
    m = out["model"]

    print("=" * 70)
    print("Causal attribution of geomagnetic events")
    print("=" * 70)
    mode = "DRIVER attribution (cumulative forcing, persistence excluded)" if args.exogenous \
        else "FULL structural model (includes Kp persistence)"
    print(f"mode: {mode}")
    drivers = ", ".join(f"{p['var']}(lag {p['lag']}) coef {p['coef']:+.3f}" for p in m["parents"])
    print(f"structural model for {m['target']}: {drivers}")
    print(f"  intercept {m['intercept']:+.3f}   R^2 {m['r2']:.3f}   fit on {m['n']:,} hours")
    print("-" * 70)
    for exp in out["explanations"]:
        print(format_explanation(exp, target=m["target"]))
        print()
    print("=" * 70)
    print("NOTE: attribution is under the discovered causal structure assuming")
    print("      linear effects -- a causal estimate, not a counterfactual proof.")


if __name__ == "__main__":
    main()
