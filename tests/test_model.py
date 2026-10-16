"""Core simulation pieces (model.py, national_mood.py), the Senate's funded-independent gate and pollster identity
(model._same_pollster, names.canon)."""

import numpy as np
import pandas as pd
import pytest

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
