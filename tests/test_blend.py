"""The race-poll path on synthetic polls: sponsor tags, poll preparation, version collapsing, the Student-t blend's scale,
the House prior + poll blend, Senate seat bookkeeping and the cross-race common trend. Committed files only, no network."""

import numpy as np
import pandas as pd
import pytest

from midterms import model as M
from midterms import senate2026 as SN

ASOF = pd.Timestamp("2026-10-16")


def _polls(rows):
    """rows: (seat, pollster, days before ASOF, dem, rep[, n])"""
    out = []
    for r in rows:
        seat, pollster, days, dem, rep = r[:5]
        out.append(
            {
                "seat": seat,
                "pollster": pollster,
                "end_date": ASOF - pd.Timedelta(days=days),
                "dem": float(dem),
                "rep": float(rep),
                "margin": float(dem - rep),
                "n": r[5] if len(r) > 5 else np.nan,
            }
        )
    return pd.DataFrame(out)


@pytest.mark.parametrize(
    ("pollster", "challenger", "tag"),
    [
        ("Public Policy Polling (D)", "D", "D"),
        ("Trafalgar Group (R)", "D", "R"),
        ("Siena College", "D", ""),
        ("Fabrizio (R)/Impact Research (D)", "D", ""),  # bipartisan
        ("Lake Research (I)", "I", "D"),  # the independent's own poll is challenger-sponsored
        ("Impact Research (D)", "I", "D"),
        ("Cygnal (R)", "I", "R"),
    ],
)
def test_sponsor_of(pollster, challenger, tag):
    assert M.sponsor_of(pollster, challenger) == tag


def test_prepare_race_polls_shifts_and_weights(monkeypatch):
    cal = {"sponsor": {"D": {"lean": 4.0}, "R": {"lean": -4.0}}, "pollsters": {}, "k_shrink": 10}
    monkeypatch.setattr(M, "CAL_OVERRIDE", cal)
    monkeypatch.setattr(M, "pollster_experience", lambda name: 100)  # every pollster seasoned: no experience penalty
    p = M.prepare_race_polls(
        _polls([("NC", "GBAO (D)", 3, 50, 44), ("NC", "Siena", 3, 48, 46), ("NC", "Quantus Insights (R)", 3, 46, 48)])
    )
    d, s, q = (p[p.pollster == n].iloc[0] for n in ("GBAO (D)", "Siena", "Quantus Insights (R)"))
    assert d["margin"] == pytest.approx(6 - 4.0) and d["grade"] == pytest.approx(
        1.5 * M.SPONSOR_WEIGHT
    )  # D-sponsored: shifted, down-weighted
    assert s["margin"] == pytest.approx(2.0) and s["grade"] == pytest.approx(1.5)
    # a penalised pollster: no sponsor tag, its own measured lean, half weight
    pen = M.POLLSTER_PENALTY["quantus"]
    assert (
        q["sponsor"] == ""
        and q["margin"] == pytest.approx(-2 - pen["lean"])
        and q["grade"] == pytest.approx(1.5 * pen["weight"])
    )


def test_prepare_race_polls_undecided_variance(monkeypatch):
    monkeypatch.setattr(
        M, "CAL_OVERRIDE", {"sponsor": {"D": {"lean": 4.0}, "R": {"lean": -4.0}}, "pollsters": {}, "k_shrink": 10}
    )
    monkeypatch.setattr(M, "pollster_experience", lambda name: 100)
    p = M.prepare_race_polls(_polls([("AZ", "A", 1, 48, 46), ("AZ", "B", 1, 40, 35)]))  # 6 % v 25 % undecided
    lo, hi = p.vmult.tolist()
    assert lo == pytest.approx(1.0)
    assert hi == pytest.approx((1 + M.UND_SLOPE * (M.UND_CAP - M.UND_FREE)) ** 2)


def test_collapse_versions():
    p = _polls(
        [
            ("TX", "Marist", 2, 50, 44, 1200),  # LV and RV of one survey, two days apart: one poll
            ("TX", "Marist (D)", 1, 49, 45, 1200),
            ("TX", "Suffolk University", 5, 49, 47, 500),  # identical numbers, different pollster and n: two polls
            ("TX", "Rasmussen Reports", 6, 49, 47, 804),
            (
                "TX",
                "Grassroots Targeting (R)",
                20,
                40,
                52,
                600,
            ),  # the same pollster's identical numbers 10 days apart: listed twice
            ("TX", "Grassroots Targeting (R)", 30, 40, 52, 600),
            ("TX", "Emerson College", 40, 47, 46, 900),  # a different survey
            ("TX", "Emerson College", 60, 46, 47, 950),
            (
                "TX",
                "Morning Consult",
                70,
                45,
                44,
                1500,
            ),  # a tracker repeating its toplines 10 days later with another n: two polls
            ("TX", "Morning Consult", 80, 45, 44, 1700),
        ]
    )
    out = M.collapse_versions(p, "seat")
    assert len(out) == 8
    marist = out[out.pollster.str.startswith("Marist")]
    assert len(marist) == 1 and marist.iloc[0]["pollster"] == "Marist (D)"  # the tagged spelling and the latest date
    assert marist.iloc[0]["dem"] == pytest.approx(49.5) and marist.iloc[0]["end_date"] == ASOF - pd.Timedelta(days=1)
    assert (out.pollster == "Suffolk University").sum() == 1 and (out.pollster == "Rasmussen Reports").sum() == 1
    assert (out.pollster == "Grassroots Targeting (R)").sum() == 1
    assert (out.pollster == "Emerson College").sum() == 2
    assert (out.pollster == "Morning Consult").sum() == 2


def test_robust_blend_scale_is_a_standard_deviation():
    """poll_sd is a standard deviation: one fresh poll at full grade must tighten the prior by the Gaussian amount
    1 / (1/prior^2 + 1/(poll_sd^2 + poll_sys^2)) within the Student-t's tolerance, not by a scale 12 % wider."""
    prior_sd, poll_sd, poll_sys = 7.0, 5.0, 0.5
    one = pd.DataFrame({"end_date": [ASOF], "grade": [1.5], "margin": [3.0], "pollster": ["Siena"]})
    _mu, sd = M.robust_blend(2.0, prior_sd, one, ASOF, poll_sd, poll_sys, 10)
    gauss = np.sqrt(1 / (1 / prior_sd**2 + 1 / (poll_sd**2 + poll_sys**2)))
    assert sd == pytest.approx(gauss, rel=0.04)


def test_house_blend_moves_toward_polls_and_records_prior_sd(monkeypatch):
    monkeypatch.setattr(
        M, "CAL_OVERRIDE", {"sponsor": {"D": {"lean": 4.0}, "R": {"lean": -4.0}}, "pollsters": {}, "k_shrink": 10}
    )
    monkeypatch.setattr(M, "pollster_experience", lambda name: 100)
    fit = M.PriorFit(b_lean=0.9, c_inc=4.0, k0=0.5, sd=7.0, intercepts={})
    seats = pd.DataFrame({"seat": ["AZ-1", "AZ-2"], "lean": [-2.0, 10.0], "inc": [-1, 1]})
    polls = _polls([("AZ-1", "Siena", 3, 50, 44), ("AZ-1", "Emerson", 5, 49, 45)])
    s = M.house_blend(seats, 6.0, M.Params(), fit, polls=polls, asof=ASOF)
    prior = 0.5 + 0.9 * (-2.0) + 4.0 * (-1) + 6.0
    assert s.loc[0, "mu_prior"] == pytest.approx(prior)
    assert (
        prior < s.loc[0, "mu"] < 6.0 and s.loc[0, "mu_local_sd"] < 7.0
    )  # pulled toward D+5 to D+6, tighter than the prior
    assert s.loc[1, "mu"] == pytest.approx(s.loc[1, "mu_prior"]) and s.loc[1, "mu_local_sd"] == pytest.approx(7.0)
    assert (s["prior_sd"] == 7.0).all() and s.loc[0, "n"] == 2


def test_senate_control_bookkeeping():
    S = pd.DataFrame({"challenger_party": ["D", "D", "I", "D"], "inc_party": ["D", "R", "R", "R"]})
    d_up = int((S.inc_party == "D").sum())  # one Democratic seat up
    hold = SN.NOW_D - d_up
    # wins: (D keeps, D flips, independent flips, R holds) in two simulations
    d = np.array([[True, True, True, False], [True, False, False, False]])
    seats, ind = SN.seat_counts(S, d, d_up)
    assert seats.tolist() == [hold + 2, hold + 1] and ind.tolist() == [1, 0]
    # 50 D - 49 R - 1 I: pro tem by a majority of senators voting - an abstaining independent leaves 50 v 49, no tie, so
    # the Democrats organise; an independent who sides with the Republicans makes it 50-50 and the Republican VP decides
    assert SN.d_controls(np.array([50]), np.array([1]))[0]
    assert not SN.d_controls(np.array([50]), np.array([1]), "R")[0]
    assert SN.d_controls(np.array([50]), np.array([1]), "D")[0]
    assert SN.d_controls(np.array([51]), np.array([0]))[0] and not SN.d_controls(np.array([50]), np.array([0]))[0]


def test_common_trend_recovers_a_shared_move():
    rng = np.random.default_rng(3)
    rows = []
    for race, level in (("A", 0.0), ("B", 8.0), ("C", -5.0)):
        for days in range(2, 120, 4):
            drift = -3.0 * days / 120  # the field moved 3 points toward the Democrats over the window
            rows.append(
                {
                    "race": race,
                    "pollster": f"P{days % 5}",
                    "end_date": ASOF - pd.Timedelta(days=days),
                    "margin": level + drift + rng.normal(0, 0.5),
                }
            )
    g = SN.common_trend(pd.DataFrame(rows), ASOF)
    assert g is not None
    assert g(ASOF) - g(ASOF - pd.Timedelta(days=100)) == pytest.approx(2.5, abs=0.7)
