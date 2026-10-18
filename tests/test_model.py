"""Core simulation pieces (model.py, national_mood.py), the Senate's funded-independent gate, pollster identity
(model._same_pollster, names.canon) and the national trend's pollster share cap (generic.fit)."""

import json

import numpy as np
import pandas as pd
import pytest

from midterms import generic as G
from midterms import model as M
from midterms import names as R
from midterms import national_mood as NM
from midterms import senate2026 as SN


def test_national_z_is_unit_t5():
    z = M.national_z(400_000, seed=11)
    assert np.std(z) == pytest.approx(1.0, abs=0.01)
    assert np.array_equal(z[:5], M.national_z(5, seed=11))  # one stream per seed: House and Senate share it


def test_statewide_draw_correlation():
    zh, zs = NM.draws(200_000, seed=7, rho=0.3)
    assert np.std(zs) == pytest.approx(1.0, abs=0.01)
    assert np.corrcoef(zh, zs)[0, 1] == pytest.approx(0.3, abs=0.01)


def test_national_shock(monkeypatch):
    z_nat, z_mv = M.national_z(1000, seed=1), M.national_z(1000, seed=2)
    states = np.array(["AZ", "MS", "TX"])
    monkeypatch.setattr(M, "MOVE", None)
    assert np.array_equal(M.national_shock(1000, 3.0, states, z_nat), (z_nat * 3.0)[:, None])
    # movement split: the rest of the national error is shared, the movement part scaled per state by k
    monkeypatch.setattr(M, "MOVE", {"z": z_mv, "m": 1.5, "k": {"AZ": 0.7, "MS": 1.2}})
    got = M.national_shock(1000, 3.0, states, z_nat)
    k = np.array([0.7, 1.2, 1.0])  # a state without a factor keeps 1
    want = (z_nat * np.sqrt(3.0**2 - 1.5**2))[:, None] + z_mv[:, None] * 1.5 * k[None, :]
    assert np.allclose(got, want)
    # the movement part never exceeds 95 % of the national sd
    monkeypatch.setattr(M, "MOVE", {"z": z_mv, "m": 10.0, "k": {}})
    m = 0.95 * 3.0
    assert np.allclose(M.national_shock(1000, 3.0, states, z_nat)[:, 0], z_nat * np.sqrt(9.0 - m**2) + z_mv * m)


def test_summarize_majority():
    seats = [217, 218, 230, 200]  # Democratic seats in four simulations
    mg = np.array([[1.0] * d + [-1.0] * (435 - d) for d in seats])
    s = M.summarize(mg, None)
    assert s["dem_seats_mean"] == pytest.approx(np.mean(seats))
    assert s["p_dem_majority"] == 0.5  # 218 of 435
    assert M.summarize(mg, None, shift=-2.0)["dem_seats_mean"] == 0.0


def test_funded_independent_gate():
    assert SN.ind_funded(0.63e6, 1.42e6)  # Orman 2014: under $1M, 0.45x the Republican
    assert not SN.ind_funded(0.0, 3.0e6)  # Harrington 2020: no campaign money
    assert not SN.ind_funded(0.2e6, 1.0e6)  # under a quarter of the Republican's
    assert not SN.ind_funded(float("nan"), 2.0e6) and not SN.ind_funded(1.0e6, float("nan"))  # money not on file


def test_robust_blend_moves_toward_the_polls():
    asof = pd.Timestamp("2026-10-16")
    assert M.robust_blend(1.0, 6.7, None, asof, 4.3, 2.0, 5) == (1.0, 6.7)
    one = pd.DataFrame(
        {"end_date": [asof - pd.Timedelta(days=3)], "grade": [1.5], "margin": [9.0], "pollster": ["Siena"]}
    )
    mu1, sd1 = M.robust_blend(1.0, 6.7, one, asof, 4.3, 2.0, 5)
    assert 1.0 < mu1 < 9.0 and sd1 < 6.7
    two = pd.concat([one, one.assign(pollster="Emerson")], ignore_index=True)
    mu2, sd2 = M.robust_blend(1.0, 6.7, two, asof, 4.3, 2.0, 5)
    assert mu1 < mu2 < 9.0 and sd2 < sd1  # a second poll pulls further and narrows the estimate
    stale = one.assign(end_date=asof - pd.Timedelta(days=M.POLL_WINDOW + 1))
    assert M.robust_blend(1.0, 6.7, stale, asof, 4.3, 2.0, 5) == (1.0, 6.7)  # outside the window: the prior


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("AtlasIntel", "Atlas Intel", True),
        ("Research & Polling Inc.", "Research & Polling", True),
        ("UNH", "University of New Hampshire", True),
        ("Data for Progress", "DFP", True),
        ("New York Times/Siena", "Siena College", True),
        ("Data Orbital", "Data for Progress", False),  # a shared generic first word is not an identity
        ("Siena", "Emerson", False),
    ],
)
def test_same_pollster(a, b, same):
    assert M._same_pollster(a, b) is same
    assert M._same_pollster(b, a) is same


@pytest.mark.parametrize("name", ["Siena College", "Quinnipiac University", "YouGov/Economist", "cygnal"])
def test_canon_is_idempotent(name):
    assert R.canon(R.canon(name)) == R.canon(name)


def test_unpriced_change():
    b = M.HEAT_UNPRICED
    # the change since the newest poll in full, the change before it at the base share
    assert M.unpriced_change(now=5.0, at_polls=5.0, yr_ago=3.0) == pytest.approx(
        2.0 * b
    )  # the polls saw the whole rise
    assert M.unpriced_change(now=5.0, at_polls=3.0, yr_ago=3.0) == pytest.approx(2.0)  # the whole rise came after them
    assert M.unpriced_change(now=5.0, at_polls=4.0, yr_ago=3.0) == pytest.approx(1.0 + b)
    # a price that rose before the polls and fell back since: the fall is unseen, the rise half seen
    assert M.unpriced_change(now=3.0, at_polls=5.0, yr_ago=3.0) == pytest.approx(-2.0 + 2.0 * b)
    assert M.unpriced_change(now=3.0, at_polls=3.0, yr_ago=3.0) == 0.0
    # symmetric: a fall is the same arithmetic with the opposite sign
    assert M.unpriced_change(now=3.0, at_polls=4.0, yr_ago=5.0) == pytest.approx(-(1.0 + b))


def test_newest_poll_dates():
    p = pd.DataFrame(
        {"seat": ["GA", "GA", "NC"], "end_date": pd.to_datetime(["2026-10-01", "2026-10-09", "2026-10-20"])}
    )
    got = M.newest_poll_dates(p, pd.Timestamp("2026-10-15"))
    assert got == {"GA": pd.Timestamp("2026-10-09")}  # the newest on or before the as-of date; NC's poll is later
    assert M.newest_poll_dates(None, pd.Timestamp("2026-10-15")) == {}


def test_national_house_vote_is_the_one_definition():
    from midterms import data_prep as D

    v = D.national_house_vote()
    assert v[2018] > 8 and v[2022] < 0 and v[2024] < 0
    assert set(v.index) >= {1996, 2024}


def test_generic_share_cap():
    """A weekly tracker with large samples carries more than SHARE_CAP of today's trend uncapped; capped, its polls'
    positive influence sits at the cap and the influences still add to today's value."""
    rng = np.random.default_rng(3)
    start = pd.Timestamp("2026-03-01")
    rows = [("tracker", start + pd.Timedelta(days=7 * k), 15000.0) for k in range(30)]
    rows += [(f"p{j}", start + pd.Timedelta(days=30 * k + 2 * j), 1000.0) for j in range(12) for k in range(7)]
    df = pd.DataFrame(rows, columns=["pollster", "end_date", "n"])
    df["pop"], df["internal"], df["partisan"] = "lv", False, None
    df["y"] = (
        5 + 0.01 * (df["end_date"] - start).dt.days - 2.0 * (df["pollster"] == "tracker") + rng.normal(0, 2, len(df))
    )
    t1 = df["end_date"].max()

    def shares(F):
        pw = F["pw"]
        return pw["influence"].clip(lower=0).groupby(pw["pollster"]).sum() / pw["influence"].sum()

    off = shares(G.fit(df, t1=t1, share_cap=None))
    assert off.idxmax() == "tracker" and off["tracker"] > 0.2
    F = G.fit(df, t1=t1, share_cap=0.15)
    on = shares(F)
    assert on["tracker"] == pytest.approx(0.15, abs=1e-3) and on.max() <= 0.15 + 1e-3
    assert F["pw"].groupby("pollster")["share_w"].first()["tracker"] < 1
    assert F["pw"]["influence"].sum() == pytest.approx(1.0, abs=1e-6)


def test_race_shock(monkeypatch):
    """Race-level movement split: off -> u x sd; on -> the movement part of each race scaled by its state's k, capped at
    0.9 sd, the total kept where k = 1."""
    n = 200_000
    u = M.national_z(n, seed=3)[:, None] * np.ones((1, 3))  # the off case needs no independent columns
    sd = np.array([5.0, 5.0, 1.0])
    states = np.array(["AZ", "MS", "TX"])
    monkeypatch.setattr(M, "MOVE", None)
    assert np.allclose(M.race_shock(u, sd, states, seed=1), u * sd[None, :])
    rng = np.random.default_rng(4)
    u = rng.standard_t(M.T_DF, (n, 3)) * np.sqrt((M.T_DF - 2) / M.T_DF)
    monkeypatch.setattr(M, "MOVE", {"z": np.zeros(n), "m": 1.0, "m_race": 2.0, "k": {"AZ": 0.5, "TX": 1.0}})
    got = M.race_shock(u, sd, states, seed=1)
    want_var = [5.0**2 - 4.0 + 4.0 * 0.25, 5.0**2, 1.0]  # AZ damped; MS without a factor keeps 1; TX capped at 0.9 sd
    assert np.allclose(got.var(0), want_var, rtol=0.02)
    # same unit draws -> the rest of the error is the old draw rescaled: other draws of a run are unchanged
    assert np.corrcoef(got[:, 1], u[:, 1])[0, 1] > 0.9


def test_senate_race_shock_keeps_joint_component(monkeypatch):
    """With the split on, the same-state component shared with the governor's race is still c x JOINT z."""
    n = 50_000
    S = pd.DataFrame({"state": ["AZ", "GA"], "mu": [0.0, 0.0], "sd": [4.0, 4.0], "wnc_z": [0.0, 0.0]})
    monkeypatch.setattr(SN, "JOINT", {"c": 2.0, "z": {"AZ": M.national_z(n, seed=8), "GA": M.national_z(n, seed=9)}})
    monkeypatch.setattr(M, "MOVE", {"z": np.zeros(n), "m": 0.0, "m_race": 1.5, "k": {"AZ": 0.6, "GA": 1.0}})
    mg = SN.simulate_senate(S, n=n, extra=0.0, nat_z=np.zeros(n))
    assert np.corrcoef(mg[:, 0], SN.JOINT["z"]["AZ"])[0, 1] == pytest.approx(2.0 / mg[:, 0].std(), abs=0.02)
    assert mg[:, 0].var() == pytest.approx(16.0 - 2.25 + 2.25 * 0.36, rel=0.03)  # AZ banks faster: tighter
    assert mg[:, 1].var() == pytest.approx(16.0, rel=0.03)


def test_race_move_fit_file():
    from midterms import early_vote as EV

    rec = json.loads(EV.RACE_FIT_JSON.read_text())
    assert 0.0 < rec["q_race"] <= rec["q_total"] and rec["q_national"] > 0
    assert EV.race_move_var(17) == pytest.approx(rec["q_race"] * 17)
    assert EV.race_move_var(90) == pytest.approx(rec["q_race"] * EV.MOVE_LEAD_MAX)  # the fitted range
