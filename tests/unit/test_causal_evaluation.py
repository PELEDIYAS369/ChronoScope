"""Unit tests for the known-physics evaluation framework."""

from src.chronoscope.causal.evaluation import (
    ExpectedLink,
    ForbiddenLink,
    known_space_weather_physics,
    score_against_known_physics,
    score_graph,
)
from src.chronoscope.causal.graph import CausalGraph


def _graph_with(*edges, variables=("bz_min", "bt_max", "kp")):
    g = CausalGraph(variables)
    for src, tgt, lag in edges:
        g.add_edge(src, tgt, lag, 0.5, 1e-6)
    return g


class TestKnownPhysicsReference:
    def test_gating_link_is_bz_to_kp(self):
        expected, _forbidden = known_space_weather_physics(["bz_min", "bt_max", "kp"])
        gating = [e for e in expected if e.gating]
        assert len(gating) == 1
        assert (gating[0].source, gating[0].target) == ("bz_min", "kp")

    def test_forbidden_includes_reverse_causation(self):
        _expected, forbidden = known_space_weather_physics(["bz_min", "bt_max", "kp"])
        pairs = {(f.source, f.target) for f in forbidden}
        assert ("kp", "bz_min") in pairs
        assert ("kp", "bt_max") in pairs

    def test_only_links_with_present_variables(self):
        # no plasma var -> no sw_speed link should appear
        expected, forbidden = known_space_weather_physics(["bz_min", "kp"])
        for link in expected:
            assert link.source in {"bz_min", "kp"} and link.target in {"bz_min", "kp"}
        for link in forbidden:
            assert link.source in {"bz_min", "kp"} and link.target in {"bz_min", "kp"}

    def test_plasma_var_enables_speed_link(self):
        expected, _ = known_space_weather_physics(["sw_speed_mean", "kp"])
        assert any(e.source == "sw_speed_mean" and e.target == "kp" for e in expected)


class TestScoring:
    def test_correct_graph_passes(self):
        g = _graph_with(("bz_min", "kp", 2))
        card = score_against_known_physics(g)
        assert card.passed is True
        assert card.gating_found == card.gating_total == 1
        assert card.violations == []

    def test_reverse_causation_fails(self):
        g = _graph_with(("bz_min", "kp", 2), ("kp", "bz_min", 1))
        card = score_against_known_physics(g)
        assert card.passed is False
        assert len(card.violations) == 1

    def test_missing_gating_link_fails(self):
        g = _graph_with(("bt_max", "kp", 1))  # soft link only, no bz_min->kp
        card = score_against_known_physics(g)
        assert card.passed is False
        assert card.gating_found == 0

    def test_soft_link_reported_not_gating(self):
        g = _graph_with(("bz_min", "kp", 2), ("bt_max", "kp", 1))
        card = score_against_known_physics(g)
        assert card.passed is True          # gating present, no violations
        assert card.soft_found == 1         # bt_max->kp counted as soft

    def test_lag_window_enforced(self):
        link = ExpectedLink("bz_min", "kp", min_lag=1, max_lag=3, gating=True)
        forbidden: list[ForbiddenLink] = []
        g_in = _graph_with(("bz_min", "kp", 2))
        g_out = _graph_with(("bz_min", "kp", 5))
        assert score_graph(g_in, [link], forbidden).passed is True
        assert score_graph(g_out, [link], forbidden).passed is False

    def test_summary_reports_verdict(self):
        g = _graph_with(("bz_min", "kp", 2))
        assert "PASS" in score_against_known_physics(g).summary()
        g.add_edge("kp", "bz_min", 1, 0.3, 1e-3)
        assert "FAIL" in score_against_known_physics(g).summary()
