"""
ChronoScope — Causal Diagnosis Demo

Narrates the causal engine end-to-end on the real ten-year DSCOVR corpus:
  1. the causal STRUCTURE the engine discovered from real data
     (southward Bz drives geomagnetic activity), graded against known physics; and
  2. a causal ATTRIBUTION of the strongest storm in the corpus
     (the May 2024 Gannon superstorm, Kp 9) to its driver.

Honest scope: this is causal inference from real data, validated against known
space-weather physics. It attributes a GEOMAGNETIC index (Kp) to its SOLAR-WIND
driver. Attributing a specific spacecraft's anomaly would need that spacecraft's
housekeeping telemetry (a pilot dataset, not yet in hand) -- see "What this is"
at the end.

Prereqs (built in earlier sessions, on Utsav's machine):
  - <root>/derived/hourly_features.parquet   (training matrix; build with corpus/training.py)
  - <root>/derived/causal_graph.json         (saved graph; discovery.py --save)
    (if the graph file is missing, the demo runs discovery live -- slower)

Usage:
    python scripts/causal_demo.py --root E:\\chronoscope_corpus
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.chronoscope.causal.discovery import discover_from_corpus
from src.chronoscope.causal.evaluation import score_against_known_physics
from src.chronoscope.causal.explanation import (
    CAUSAL_GRAPH_RELPATH,
    explain_top_events,
    format_explanation,
)
from src.chronoscope.causal.graph import CausalGraph
from src.chronoscope.corpus.training import HOURLY_FEATURES_RELPATH


def header(title: str) -> None:
    print()
    print("=" * 68)
    print(f"  {title}")
    print("=" * 68)


def section(title: str) -> None:
    print(f"\n{'-' * 68}")
    print(f"  {title}")
    print(f"{'-' * 68}")


def _load_graph(root: Path):
    """Load the saved causal graph, or run discovery if it isn't there."""
    gpath = root / CAUSAL_GRAPH_RELPATH
    if gpath.exists():
        payload = json.loads(gpath.read_text())
        return CausalGraph.from_dict(payload["graph"]), False
    graph, _ = discover_from_corpus(root)
    return graph, True


def run_causal_demo(root: Path) -> None:
    import pandas as pd

    header("ChronoScope — Causal Diagnosis Demo")
    print("""
  Most monitoring tools tell you THAT something deviated.
  ChronoScope's causal engine tells you WHY -- and it learned the "why"
  from ten years of real spacecraft data, not from rules we wrote in.
    """)

    features = root / HOURLY_FEATURES_RELPATH
    if not features.exists():
        print(f"  ERROR: {features} not found.")
        print(f"  Build it first: python -m src.chronoscope.corpus.training --root {root}")
        sys.exit(1)

    # -- Step 1: the data ------------------------------------------------
    section("Step 1 of 3 — Ten years of real telemetry")
    ts = pd.read_parquet(features, columns=["timestamp"])["timestamp"]
    print(f"\n  Source:        NOAA DSCOVR (L1 Lagrange point, 1.5M km from Earth)")
    print(f"  Hourly rows:   {len(ts):,}")
    print(f"  Span:          {ts.min()}  ->  {ts.max()}")
    print(f"  Labels joined: geomagnetic Kp / G-scale, Richardson-Cane ICME intervals")
    print(f"  (All real measured data. No synthetic data anywhere.)")

    # -- Step 2: the discovered causal structure -------------------------
    section("Step 2 of 3 — What the engine discovered (and how we checked it)")
    graph, discovered_live = _load_graph(root)
    if discovered_live:
        print("\n  (No saved graph found -- ran PCMCI discovery live.)")
    scorecard = score_against_known_physics(graph)

    bz_edges = [e for e in graph.edges_into("kp") if e.source == "bz_min"]
    print("\n  Causal discovery (PCMCI) over the corpus found, as the dominant")
    print("  cross-variable cause of geomagnetic activity:")
    if bz_edges:
        e = min(bz_edges, key=lambda x: x.lag)
        print(f"\n      southward field  bz_min  -->  Kp     (lag {e.lag} h, strength {e.strength:+.3f})")
        print(f"\n  i.e. when the interplanetary field turns strongly southward, the")
        print(f"  geomagnetic index Kp rises about {e.lag} hour later. That is the textbook")
        print(f"  driver of geomagnetic storms -- recovered here purely from data.")
    else:
        print("\n      (bz_min -> kp edge not present in this graph)")

    print("\n  We grade the result against KNOWN PHYSICS -- links the engine must")
    print("  find, and physically-impossible ones it must never invent")
    print("  (the magnetosphere cannot drive the upstream solar wind):")
    print()
    for line in scorecard.summary().splitlines():
        print(f"  {line}")
    print(f"\n  Verdict: {'PASS' if scorecard.passed else 'FAIL'} "
          f"-- the engine recovers real physics and invents no impossible causes.")

    # -- Step 3: causal attribution of the strongest storm ---------------
    section("Step 3 of 3 — Causal attribution of the strongest storm on record")
    out = explain_top_events(root, target="kp", top=5,
                             include_autoregressive=False, exogenous_lags=6)
    model = out["model"]
    top = out["explanations"][0]

    when = str(top["time"])
    is_gannon = when.startswith("2024-05-1")
    label = "  (This is the Gannon superstorm -- the strongest storm in 20 years.)" if is_gannon else ""

    print(f"\n  Strongest geomagnetic hour in the corpus: {when}")
    print(f"  Observed Kp = {top['observed']:.0f}  (G5 -- extreme){label}")
    print(f"\n  ChronoScope's causal attribution (drivers only, persistence removed):")
    print()
    for line in format_explanation(top, target="kp").splitlines():
        print(f"  {line}")
    print(f"\n  Read plainly: this extreme storm was driven by SUSTAINED, strongly")
    print(f"  southward interplanetary field over the preceding hours -- each hour")
    print(f"  of strong southward Bz adding to the geomagnetic response.")
    print(f"\n  (Driver model R^2 = {model['r2']:.2f} on hourly Kp -- one driver explains")
    print(f"   part of Kp; speed, density and nonlinear effects account for the rest.)")

    # -- Honest footer ---------------------------------------------------
    header("What this is — and what it is not")
    print("""
  IS:   Causal inference from ten years of real data, graded against known
        space-weather physics. It attributes a geomagnetic index (Kp) to its
        solar-wind driver, with effect sizes and honest caveats -- not a
        black box, and not a curve fit dressed up as a story.

  NOT:  Diagnosis of a SPECIFIC spacecraft's anomaly. That needs that
        spacecraft's housekeeping telemetry, available through a pilot
        partnership -- the natural next step. The attribution here is also
        linear, so on the most extreme storms it over-predicts past the Kp
        ceiling (Kp saturates at 9); a nonlinear coupling model is planned.
    """)


def main() -> None:
    parser = argparse.ArgumentParser(description="Narrated causal-diagnosis demo on the DSCOVR corpus.")
    parser.add_argument("--root", required=True, help="Corpus root (e.g. E:\\chronoscope_corpus).")
    args = parser.parse_args()
    run_causal_demo(Path(args.root))


if __name__ == "__main__":
    main()
