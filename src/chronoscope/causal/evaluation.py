"""
Evaluation framework: score a discovered causal graph against known physics.

A causal-discovery result is only trustworthy if we can grade it against ground
truth we already believe. For near-Earth space weather, the directed couplings
are textbook:

  * Southward interplanetary field (negative Bz / large |B|) DRIVES geomagnetic
    activity (Kp). This must appear, at a short positive lag.
  * The reverse cannot happen: the magnetosphere/ionosphere (Kp) cannot drive
    the upstream solar wind measured at L1 (Bz, |B|, speed). Any such edge is a
    physically impossible artifact and counts as a violation.

So the scorecard has three kinds of expectation:

  * gating expected links  - MUST be found for the run to pass (the core gate,
    e.g. bz_min -> kp).
  * soft expected links     - we hope to find them; reported as recall but not
    pass/fail (e.g. bt_max -> kp, sw_speed_mean -> kp, in_icme -> kp).
  * forbidden links         - MUST be absent; any present is a violation and
    fails the run (the reverse-causation set).

``passed`` = all gating links found AND zero forbidden violations. This turns
"did PCMCI recover known physics?" into an objective check instead of a vibe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.chronoscope.causal.graph import CausalEdge, CausalGraph


@dataclass(frozen=True)
class ExpectedLink:
    """A link we expect a correct method to find (within a lag window)."""

    source: str
    target: str
    min_lag: int = 0
    max_lag: int | None = None   # None -> any lag >= min_lag
    gating: bool = False         # True -> must be found for the run to pass
    note: str = ""

    def matched_by(self, graph: CausalGraph) -> list[int]:
        """Lags at which `graph` satisfies this expectation."""
        out = []
        for lag in graph.lags_between(self.source, self.target):
            if lag < self.min_lag:
                continue
            if self.max_lag is not None and lag > self.max_lag:
                continue
            out.append(lag)
        return out


@dataclass(frozen=True)
class ForbiddenLink:
    """A link that must NOT appear (reverse / physically impossible causation)."""

    source: str
    target: str
    note: str = ""

    def violations(self, graph: CausalGraph) -> list[CausalEdge]:
        return [e for e in graph.edges() if e.source == self.source and e.target == self.target]


@dataclass
class PhysicsScorecard:
    required_results: list[tuple[ExpectedLink, list[int]]] = field(default_factory=list)
    soft_results: list[tuple[ExpectedLink, list[int]]] = field(default_factory=list)
    forbidden_results: list[tuple[ForbiddenLink, list[CausalEdge]]] = field(default_factory=list)

    @property
    def gating_found(self) -> int:
        return sum(1 for _link, lags in self.required_results if lags)

    @property
    def gating_total(self) -> int:
        return len(self.required_results)

    @property
    def soft_found(self) -> int:
        return sum(1 for _link, lags in self.soft_results if lags)

    @property
    def violations(self) -> list[CausalEdge]:
        out: list[CausalEdge] = []
        for _link, edges in self.forbidden_results:
            out.extend(edges)
        return out

    @property
    def gating_recall(self) -> float:
        return self.gating_found / self.gating_total if self.gating_total else 1.0

    @property
    def passed(self) -> bool:
        all_gating = self.gating_found == self.gating_total
        return all_gating and not self.violations

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        lines = [f"Known-physics scorecard: {verdict}"]
        lines.append(f"  gating links found : {self.gating_found}/{self.gating_total}")
        for link, lags in self.required_results:
            mark = "OK " if lags else "MISS"
            at = f"@lag {lags}" if lags else "(not found)"
            lines.append(f"    [{mark}] {link.source} -> {link.target} {at}  {link.note}")
        if self.soft_results:
            lines.append(f"  soft links found   : {self.soft_found}/{len(self.soft_results)}")
            for link, lags in self.soft_results:
                mark = "OK " if lags else "-- "
                at = f"@lag {lags}" if lags else "(not found)"
                lines.append(f"    [{mark}] {link.source} -> {link.target} {at}  {link.note}")
        lines.append(f"  forbidden violations: {len(self.violations)}")
        for link, edges in self.forbidden_results:
            if edges:
                lags = [e.lag for e in edges]
                lines.append(f"    [BAD] {link.source} -> {link.target} present @lag {lags}  {link.note}")
        return "\n".join(lines)


def known_space_weather_physics(
    variables,
) -> tuple[list[ExpectedLink], list[ForbiddenLink]]:
    """
    Build the expected + forbidden link sets for whichever variables are present.

    Only links whose endpoints are both in `variables` are included, so the same
    reference adapts to a MAG-only run or a plasma-rich run.
    """
    v = set(variables)
    expected: list[ExpectedLink] = []
    forbidden: list[ForbiddenLink] = []

    # --- expected drivers of geomagnetic activity (Kp) ---
    if {"bz_min", "kp"} <= v:
        expected.append(ExpectedLink(
            "bz_min", "kp", min_lag=0, max_lag=None, gating=True,
            note="southward IMF drives geomagnetic activity (core gate)"))
    if {"bt_max", "kp"} <= v:
        expected.append(ExpectedLink(
            "bt_max", "kp", min_lag=0, gating=False,
            note="stronger field magnitude associated with activity"))
    if {"sw_speed_mean", "kp"} <= v:
        expected.append(ExpectedLink(
            "sw_speed_mean", "kp", min_lag=0, gating=False,
            note="faster solar wind drives activity (plasma era only)"))
    if {"in_icme", "kp"} <= v:
        expected.append(ExpectedLink(
            "in_icme", "kp", min_lag=0, gating=False,
            note="ICME passage coincides with elevated activity"))

    # --- forbidden reverse causation: Kp cannot drive the upstream solar wind ---
    for upstream in ("bz_min", "bz_mean", "bt_max", "bt_mean", "sw_speed_mean", "density_mean"):
        if {"kp", upstream} <= v:
            forbidden.append(ForbiddenLink(
                "kp", upstream,
                note="magnetosphere cannot drive upstream solar wind (reverse causation)"))
        if {"g_scale", upstream} <= v:
            forbidden.append(ForbiddenLink(
                "g_scale", upstream,
                note="g_scale (from Kp) cannot drive upstream solar wind"))

    return expected, forbidden


def score_graph(
    graph: CausalGraph,
    expected: list[ExpectedLink],
    forbidden: list[ForbiddenLink],
) -> PhysicsScorecard:
    card = PhysicsScorecard()
    for link in expected:
        lags = link.matched_by(graph)
        if link.gating:
            card.required_results.append((link, lags))
        else:
            card.soft_results.append((link, lags))
    for link in forbidden:
        card.forbidden_results.append((link, link.violations(graph)))
    return card


def score_against_known_physics(graph: CausalGraph) -> PhysicsScorecard:
    """Convenience: build the reference from the graph's own variables and score."""
    expected, forbidden = known_space_weather_physics(graph.variables)
    return score_graph(graph, expected, forbidden)
