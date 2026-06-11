"""
Causal graph as a first-class domain object.

A CausalGraph is a directed graph over named variables where each edge carries
a time lag and the statistics that justified it. The lag convention follows
time-series causal discovery: an edge ``source -> target @ lag`` means the value
of ``source`` at time ``t - lag`` influences ``target`` at time ``t``. Lag 0 is
a contemporaneous (same-time-step) link.

This object is independent of how the graph was discovered (PCMCI, a hand-built
reference, etc.), so it can represent both a discovered graph and a known-physics
reference, and the two can be compared by the evaluation module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CausalEdge:
    """A single directed, lagged causal link with its supporting statistics."""

    source: str
    target: str
    lag: int          # >= 0; source at t-lag influences target at t
    strength: float   # test statistic (e.g. MCI partial correlation)
    p_value: float

    def __post_init__(self) -> None:
        if self.lag < 0:
            raise ValueError(f"lag must be >= 0, got {self.lag}")


class CausalGraph:
    """Directed, lagged causal graph over a fixed set of variables."""

    def __init__(self, variables) -> None:
        self.variables: list[str] = list(variables)
        if len(set(self.variables)) != len(self.variables):
            raise ValueError("variables must be unique")
        # keyed by (source, target, lag) so a re-added link updates in place
        self._edges: dict[tuple[str, str, int], CausalEdge] = {}

    # -- construction -----------------------------------------------------
    def add_edge(self, source: str, target: str, lag: int,
                 strength: float = 0.0, p_value: float = 0.0) -> CausalEdge:
        for v in (source, target):
            if v not in self.variables:
                raise ValueError(f"unknown variable {v!r}; not in {self.variables}")
        if lag == 0 and source == target:
            raise ValueError("a contemporaneous self-loop (lag 0, source == target) is invalid")
        edge = CausalEdge(source, target, lag, float(strength), float(p_value))
        self._edges[(source, target, lag)] = edge
        return edge

    # -- queries ----------------------------------------------------------
    def has_edge(self, source: str, target: str, lag: int | None = None) -> bool:
        """
        True if a ``source -> target`` edge exists. With ``lag=None`` (default)
        any lag matches; with an int, only that exact lag matches.
        """
        if lag is not None:
            return (source, target, lag) in self._edges
        return any(s == source and t == target for (s, t, _l) in self._edges)

    def edge(self, source: str, target: str, lag: int) -> CausalEdge | None:
        return self._edges.get((source, target, lag))

    def lags_between(self, source: str, target: str) -> list[int]:
        """All lags at which a ``source -> target`` edge exists, ascending."""
        return sorted(l for (s, t, l) in self._edges if s == source and t == target)

    def edges(self) -> list[CausalEdge]:
        return sorted(self._edges.values(), key=lambda e: (e.source, e.target, e.lag))

    def edges_into(self, target: str) -> list[CausalEdge]:
        return [e for e in self.edges() if e.target == target]

    def edges_from(self, source: str) -> list[CausalEdge]:
        return [e for e in self.edges() if e.source == source]

    def __len__(self) -> int:
        return len(self._edges)

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "variables": list(self.variables),
            "edges": [
                {"source": e.source, "target": e.target, "lag": e.lag,
                 "strength": e.strength, "p_value": e.p_value}
                for e in self.edges()
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CausalGraph":
        g = cls(payload["variables"])
        for e in payload.get("edges", []):
            g.add_edge(e["source"], e["target"], int(e["lag"]),
                       float(e.get("strength", 0.0)), float(e.get("p_value", 0.0)))
        return g

    # -- display ----------------------------------------------------------
    def summary(self) -> str:
        lines = [f"CausalGraph: {len(self.variables)} variables, {len(self)} edges"]
        if not self._edges:
            lines.append("  (no edges)")
        for e in self.edges():
            arrow = f"{e.source} -> {e.target}"
            lag = "contemporaneous" if e.lag == 0 else f"lag {e.lag}"
            lines.append(f"  {arrow:<28} {lag:<16} val={e.strength:+.3f} p={e.p_value:.2e}")
        return "\n".join(lines)
