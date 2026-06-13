"""Unit tests for the causal explanation / attribution layer."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.chronoscope.causal.explanation import (
    explain_event,
    fit_structural_model,
    format_explanation,
)
from src.chronoscope.causal.graph import CausalGraph

UTC = timezone.utc


def _synthetic_df(seed=3, T=2000):
    """bz_min drives kp negatively at lag 1; kp persists at lag 1."""
    rng = np.random.default_rng(seed)
    bz = np.zeros(T)
    bt = np.zeros(T)
    kp = np.zeros(T)
    for t in range(1, T):
        bz[t] = 0.6 * bz[t - 1] + rng.normal(0, 3)
        bt[t] = 0.5 * bt[t - 1] + rng.normal(0, 1) + 5
    for t in range(1, T):
        kp[t] = max(0.0, -0.7 * bz[t - 1] + 0.3 * kp[t - 1] + rng.normal(0, 0.5) + 2)
    t0 = datetime(2017, 1, 1, tzinfo=UTC)
    df = pd.DataFrame({
        "timestamp": [t0 + timedelta(hours=i) for i in range(T)],
        "bz_min": bz, "bt_max": bt, "kp": kp,
    })
    return df


def _graph():
    g = CausalGraph(["bz_min", "bt_max", "kp"])
    g.add_edge("bz_min", "kp", 1, -0.5, 1e-9)   # exogenous driver
    g.add_edge("kp", "kp", 1, 0.3, 1e-9)         # persistence
    return g


class TestFitStructuralModel:
    def test_recovers_driver_coefficient_sign_and_magnitude(self):
        model = fit_structural_model(_synthetic_df(), _graph(), target="kp")
        bz = [p for p in model["parents"] if p["var"] == "bz_min"][0]
        assert bz["lag"] == 1
        assert bz["coef"] < 0                 # southward Bz -> higher Kp
        assert abs(bz["coef"] - (-0.7)) < 0.2  # near the true value

    def test_good_fit_quality(self):
        model = fit_structural_model(_synthetic_df(), _graph(), target="kp")
        assert model["r2"] > 0.8
        assert model["n"] > 1900

    def test_raises_without_parents(self):
        g = CausalGraph(["bz_min", "kp"])  # no edges into kp
        with pytest.raises(ValueError, match="no usable causal parents"):
            fit_structural_model(_synthetic_df(), g, target="kp")

    def test_missing_values_handled(self):
        df = _synthetic_df()
        df.loc[5:50, "bz_min"] = np.nan  # gaps
        model = fit_structural_model(df, _graph(), target="kp")
        assert model["n"] < 2000           # gapped rows dropped
        assert model["r2"] > 0.7


class TestExplainEvent:
    def test_attributes_largest_event_to_driver(self):
        df = _synthetic_df()
        model = fit_structural_model(df, _graph(), target="kp")
        top = int(np.argmax(df["kp"].to_numpy()))
        exp = explain_event(df, model, top)
        exo = [c for c in exp["contributions"] if c["var"] == "bz_min"][0]
        # the spike was preceded by strongly negative bz_min, contributing positively
        assert exo["value"] < 0
        assert exo["contribution"] > 0
        # exogenous driver dominates the autoregressive term for an extreme event
        auto = [c for c in exp["contributions"] if c["var"] == "kp"][0]
        assert abs(exo["contribution"]) > abs(auto["contribution"])

    def test_contributions_sum_to_prediction(self):
        df = _synthetic_df()
        model = fit_structural_model(df, _graph(), target="kp")
        exp = explain_event(df, model, 500)
        total = exp["intercept"] + sum(c["contribution"] for c in exp["contributions"])
        assert abs(total - exp["predicted"]) < 1e-6

    def test_format_mentions_primary_cause(self):
        df = _synthetic_df()
        model = fit_structural_model(df, _graph(), target="kp")
        top = int(np.argmax(df["kp"].to_numpy()))
        text = format_explanation(explain_event(df, model, top), target="kp")
        assert "primary cause" in text
        assert "bz_min" in text


class TestDriverAttributionMode:
    def test_exogenous_mode_excludes_persistence(self):
        df = _synthetic_df()
        model = fit_structural_model(df, _graph(), target="kp",
                                     include_autoregressive=False, exogenous_lags=6)
        # no autoregressive (kp->kp) terms remain
        assert all(p["var"] != "kp" for p in model["parents"])
        # exogenous driver spread across the lag window
        bz_lags = sorted(p["lag"] for p in model["parents"] if p["var"] == "bz_min")
        assert bz_lags == [1, 2, 3, 4, 5, 6]

    def test_exogenous_mode_attributes_more_to_driver(self):
        df = _synthetic_df()
        top = int(np.argmax(df["kp"].to_numpy()))
        full = fit_structural_model(df, _graph(), target="kp")
        drv = fit_structural_model(df, _graph(), target="kp",
                                   include_autoregressive=False, exogenous_lags=6)
        bz_full = sum(c["contribution"] for c in explain_event(df, full, top)["contributions"]
                      if c["var"] == "bz_min")
        bz_drv = sum(c["contribution"] for c in explain_event(df, drv, top)["contributions"]
                     if c["var"] == "bz_min")
        assert bz_drv > bz_full

    def test_exogenous_mode_raises_if_no_exogenous_parents(self):
        g = CausalGraph(["bz_min", "kp"])
        g.add_edge("kp", "kp", 1, 0.3, 1e-9)  # only autoregressive
        with pytest.raises(ValueError, match="no usable causal parents"):
            fit_structural_model(_synthetic_df(), g, target="kp", include_autoregressive=False)
