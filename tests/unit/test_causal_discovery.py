"""
Unit tests for PCMCI causal discovery.

These require tigramite; the module is skipped if it isn't installed, so the
suite stays green in environments without the (heavy) Phase-2 dependency.
"""

import numpy as np
import pytest

pytest.importorskip("tigramite")

from src.chronoscope.causal.discovery import run_pcmci  # noqa: E402
from src.chronoscope.causal.evaluation import score_against_known_physics  # noqa: E402


def _synthetic(seed=7, T=3000, missing_frac=0.09):
    """bz_min drives kp at lag 2 (negative); bt_max is an unrelated AR(1)."""
    rng = np.random.default_rng(seed)
    x = np.zeros(T)
    z = np.zeros(T)
    y = np.zeros(T)
    for t in range(1, T):
        x[t] = 0.6 * x[t - 1] + rng.normal(0, 1)
        z[t] = 0.5 * z[t - 1] + rng.normal(0, 1)
    for t in range(2, T):
        y[t] = -0.7 * x[t - 2] + 0.3 * y[t - 1] + rng.normal(0, 0.5)
    if missing_frac:
        idx = rng.choice(T, size=int(missing_frac * T), replace=False)
        x = x.copy()
        x[idx] = np.nan
    return np.column_stack([x, z, y]), ["bz_min", "bt_max", "kp"]


class TestRunPcmci:
    def test_recovers_true_lagged_driver(self):
        data, names = _synthetic()
        g = run_pcmci(data, names, tau_max=4, pc_alpha=0.01)
        assert g.has_edge("bz_min", "kp")
        assert 2 in g.lags_between("bz_min", "kp")

    def test_recovered_link_has_negative_sign(self):
        data, names = _synthetic()
        g = run_pcmci(data, names, tau_max=4, pc_alpha=0.01)
        assert g.edge("bz_min", "kp", 2).strength < 0  # southward Bz -> higher Kp

    def test_does_not_invent_reverse_causation(self):
        data, names = _synthetic()
        g = run_pcmci(data, names, tau_max=4, pc_alpha=0.01)
        assert not g.has_edge("kp", "bz_min")

    def test_scorecard_passes_on_recovered_structure(self):
        data, names = _synthetic()
        g = run_pcmci(data, names, tau_max=4, pc_alpha=0.01)
        assert score_against_known_physics(g).passed is True

    def test_missing_values_tolerated(self):
        # heavier missingness still recovers the driver
        data, names = _synthetic(missing_frac=0.2)
        g = run_pcmci(data, names, tau_max=4, pc_alpha=0.01)
        assert g.has_edge("bz_min", "kp")

    def test_min_strength_filters_weak_edges(self):
        data, names = _synthetic()
        strong = run_pcmci(data, names, tau_max=4, pc_alpha=0.01, min_strength=0.9)
        # the true link (|val|~0.8) is below 0.9, so nothing survives the floor
        assert len(strong) == 0

    def test_bad_shape_rejected(self):
        with pytest.raises(ValueError, match="2D"):
            run_pcmci(np.zeros(10), ["a"], tau_max=2)

    def test_var_name_count_mismatch_rejected(self):
        with pytest.raises(ValueError, match="var_names"):
            run_pcmci(np.zeros((100, 3)), ["a", "b"], tau_max=2)
