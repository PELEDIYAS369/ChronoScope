"""Unit tests for the CausalGraph domain object."""

import pytest

from src.chronoscope.causal.graph import CausalEdge, CausalGraph


class TestCausalEdge:
    def test_negative_lag_rejected(self):
        with pytest.raises(ValueError, match="lag must be >= 0"):
            CausalEdge("a", "b", -1, 0.0, 0.0)


class TestCausalGraph:
    def test_duplicate_variables_rejected(self):
        with pytest.raises(ValueError, match="unique"):
            CausalGraph(["a", "b", "a"])

    def test_add_and_query_edge(self):
        g = CausalGraph(["bz", "kp"])
        g.add_edge("bz", "kp", 2, -0.4, 1e-6)
        assert g.has_edge("bz", "kp") is True
        assert g.has_edge("bz", "kp", lag=2) is True
        assert g.has_edge("bz", "kp", lag=1) is False
        assert g.has_edge("kp", "bz") is False
        assert len(g) == 1

    def test_has_edge_any_lag(self):
        g = CausalGraph(["bz", "kp"])
        g.add_edge("bz", "kp", 3)
        assert g.has_edge("bz", "kp", lag=None) is True
        assert g.has_edge("bz", "kp", lag=3) is True

    def test_lags_between_and_edges_into_from(self):
        g = CausalGraph(["bz", "bt", "kp"])
        g.add_edge("bz", "kp", 1)
        g.add_edge("bz", "kp", 3)
        g.add_edge("bt", "kp", 2)
        assert g.lags_between("bz", "kp") == [1, 3]
        assert {e.source for e in g.edges_into("kp")} == {"bz", "bt"}
        assert [e.target for e in g.edges_from("bz")] == ["kp", "kp"]

    def test_unknown_variable_rejected(self):
        g = CausalGraph(["bz", "kp"])
        with pytest.raises(ValueError, match="unknown variable"):
            g.add_edge("bz", "speed", 1)

    def test_contemporaneous_self_loop_rejected(self):
        g = CausalGraph(["bz", "kp"])
        with pytest.raises(ValueError, match="self-loop"):
            g.add_edge("kp", "kp", 0)

    def test_lagged_self_loop_allowed(self):
        g = CausalGraph(["bz", "kp"])
        g.add_edge("kp", "kp", 1)  # autocorrelation is fine
        assert g.has_edge("kp", "kp", lag=1)

    def test_re_add_updates_in_place(self):
        g = CausalGraph(["bz", "kp"])
        g.add_edge("bz", "kp", 2, -0.3, 1e-3)
        g.add_edge("bz", "kp", 2, -0.5, 1e-9)
        assert len(g) == 1
        assert g.edge("bz", "kp", 2).strength == -0.5

    def test_to_from_dict_roundtrip(self):
        g = CausalGraph(["bz", "bt", "kp"])
        g.add_edge("bz", "kp", 2, -0.4, 1e-6)
        g.add_edge("kp", "kp", 1, 0.3, 1e-4)
        g2 = CausalGraph.from_dict(g.to_dict())
        assert g2.variables == g.variables
        assert g2.has_edge("bz", "kp", lag=2)
        assert g2.edge("bz", "kp", 2).strength == -0.4

    def test_summary_lists_edges(self):
        g = CausalGraph(["bz", "kp"])
        g.add_edge("bz", "kp", 2, -0.4, 1e-6)
        s = g.summary()
        assert "bz -> kp" in s
        assert "lag 2" in s
