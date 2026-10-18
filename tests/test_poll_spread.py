"""Poll spread (poll_spread.py): the dispersion statistic, the committed 538 benchmark and the daily build."""

import json

import numpy as np
import pytest

from midterms import poll_spread as S


def test_spread_statistic():
    assert S.samp_var(50, 50, 1000) == pytest.approx(10.0)  # 1e4 * (1 - 0) / 1000
    assert S.pollster_key("Rasmussen Reports (R)") == S.pollster_key("Rasmussen (R)") == "rasmussen"
    assert S.pollster_key("Public Policy Polling (D)") != S.pollster_key("Public Opinion Strategies")
    s = S.spread([4, 4, 4, 4], [10, 10, 10, 10])
    assert s["ratio"] == 0 and s["p_low"] < 1e-3
    rng = np.random.default_rng(0)  # sampling noise only: the ratio averages 1
    r = [S.spread(rng.normal(3, np.sqrt(10), 8), np.full(8, 10.0))["ratio"] for _ in range(2000)]
    assert np.mean(r) == pytest.approx(1.0, abs=0.05)


def test_benchmark_file():
    h = json.loads(S.HIST_JSON.read_text())
    assert h["window_days"] == S.WINDOW and h["k_min"] == S.K_MIN
    for off in S.OFFICES:
        o = h["offices"][off]
        assert o["races"] == len(o["chi2_df"]) > 100
        assert all(df >= S.K_MIN - 1 for _, df in o["chi2_df"])
        assert 1.2 < o["pooled"] < 2.2  # real polls spread wider than sampling alone
        assert o["pooled"] == pytest.approx(sum(c for c, _ in o["chi2_df"]) / sum(d for _, d in o["chi2_df"]), abs=0.01)


def _race(office, seat, margins, n=1000, end="2026-10-10"):
    polls = [
        {"pollster": f"Pollster{i}", "end": end, "dem": 48 + m / 2, "rep": 48 - m / 2, "n": n}
        for i, m in enumerate(margins)
    ]
    return {"office": office, "seat": seat, "label": seat, "polls": polls}


def test_build_flags_herding():
    doc = {
        "asof": "2026-10-18",
        "races": [
            _race("senate", "AA", [5, 5, 5, 5, 5]),  # herded: identical polls
            _race("senate", "BB", [5, 5, 5], n=600),  # too few pollsters
            _race("governor", "CC", [-6, 2, 9, -3, 12]),  # wide
            _race("governor", "DD", [0, 20, 1, 2], end="2026-09-01"),  # outside the window
            _race("house", "EE-1", [1, 2, 3, 4]),  # House races are left out
        ],
    }
    r = S.build(doc, write=False)
    assert [x["seat"] for x in r["races"]] == ["AA", "CC"]
    assert r["offices"]["senate"]["pooled"] == 0 and r["offices"]["senate"]["p_hist"] < 0.05
    assert r["offices"]["governor"]["pooled"] > 2 and r["offices"]["governor"]["p_hist"] > 0.5
    assert r["n_known"] == 1.0


def test_build_on_published_polls():
    r = S.build(write=False)  # the committed polls.json (sample sizes or not)
    assert set(r["offices"]) <= set(S.OFFICES)
    assert all(x["k"] >= S.K_MIN for x in r["races"])
