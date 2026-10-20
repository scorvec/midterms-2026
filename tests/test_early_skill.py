"""The early-vote skill test's statistics (early_skill.py): slope and p, Holm, leave-one-cycle-out and the bootstrap."""

import numpy as np
import pytest

from midterms import early_skill as ES


def test_ols_recovers_slope_and_p():
    rng = np.random.default_rng(0)
    x = np.linspace(-5, 5, 40)
    y = 1.0 + 0.5 * x + rng.normal(0, 0.1, x.size)
    o = ES.ols(x, y)
    assert o["b"] == pytest.approx(0.5, abs=0.02)
    assert o["a"] == pytest.approx(1.0, abs=0.05)
    assert o["p"] < 1e-10
    noise = ES.ols(x, rng.normal(0, 1, x.size))
    assert noise["p"] > 0.01


def test_holm_step_down_and_monotone():
    adj = ES.holm({"15": 0.01, "7": 0.04})
    assert adj["15"] == pytest.approx(0.02)
    assert adj["7"] == pytest.approx(0.04)
    adj = ES.holm({"15": 0.03, "7": 0.02})
    assert adj["7"] == pytest.approx(0.04)
    assert adj["15"] == pytest.approx(0.04)  # never below the smaller p's adjusted value


def test_loco_holds_out_whole_cycles():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    y = 2.0 * x  # a perfect predictor: out of sample it beats both references
    cv = ES.loco(x, y, np.array([2012, 2012, 2016, 2016, 2020, 2020]))
    assert cv["n_scored"] == 6
    assert cv["rmse_with_predictor"] == pytest.approx(0.0, abs=1e-9)
    assert cv["rmse_with_predictor"] < cv["rmse_intercept"] < cv["rmse_poll_only"]


def test_bootstrap_interval_brackets_the_slope():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 3, 30)
    y = 0.4 * x + rng.normal(0, 0.5, 30)
    lo, hi = ES.boot_b(x, y, np.arange(30), n_boot=2000)
    assert lo < 0.4 < hi
