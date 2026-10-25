"""North Carolina election night (nc_night.py): the accounting identities, the Election Day projections, the reading of the
results file and the replay of 2022 from the committed county tables."""

import numpy as np
import pandas as pd
import pytest

from midterms import nc_night as N


@pytest.fixture(scope="module")
def data():
    V, R = N.load()
    return V, N.cells(V, R)


def test_complete_counties_give_the_final(data):
    V, C = data
    c = C[(C["year"] == 2022) & (C["contest"] == "US SENATE")]
    F = N.Fit(C[C["year"] != 2022])
    est, _, _ = N.estimate(F, c, N.eday_projection(V, 2022, 2018))
    assert est == pytest.approx(np.average(c["margin"], weights=c["votes"]), abs=1e-9)  # nothing left to project


def test_2022_from_the_early_and_mail_batch(data):
    V, C = data
    F = N.Fit(C[C["year"] != 2022])
    obs = N._observed(C, 2022, "US SENATE")
    c = C[(C["year"] == 2022) & (C["contest"] == "US SENATE")]
    vpb = c.loc[c["method"] == "eday", "votes"].sum() / V[(V["year"] == 2022) & (V["method"] == "eday")]["n"].sum()
    est, _, _ = N.estimate(F, obs, N.eday_projection(V, 2022, 2018), vpb)
    assert est == pytest.approx(-1.51, abs=0.05)  # Budd won by 3.23; the batch alone read D+8.8


def test_batch_moves_one_for_one_with_the_final(data):
    V, C = data
    F = N.Fit(C)
    V22 = V[(V["year"] == 2022) & V["method"].isin(N.BATCH)]
    E = N.eday_projection(V, 2022, 2018)

    def batch(final):
        b = N.batch_margin(F, V22, N.theta_for_final(F, V22, E, final))
        return np.average(b["margin"], weights=b["votes"])

    assert batch(3.0) - batch(0.0) == pytest.approx(3.0)


def test_projections():
    V = pd.DataFrame(
        [
            (2022, "A", "early", "DEM", 100),
            (2022, "A", "mail", "DEM", 0),
            (2022, "A", "eday", "DEM", 50),
            (2026, "A", "early", "DEM", 200),
            (2026, "A", "mail", "DEM", 0),
        ],
        columns=["year", "county", "method", "party", "n"],
    )
    assert N.eday_projection(V, 2026, 2022).loc["A", "DEM"] == pytest.approx(100)  # 200 x (50 / 100)
    # totals: 2022's 150 minus the 200 already banked, floored at 5 % of the reference total
    assert N.eday_projection(V, 2026, 2022, "totals").loc["A", "DEM"] == pytest.approx(7.5)


def test_county_obs_completeness():
    def row(county, precinct, real, side, early, eday):
        return {
            "County": county,
            "Precinct": precinct,
            "Real Precinct": real,
            "Contest Name": "US SENATE",
            "side": side,
            "Choice Party": {"dem": "DEM", "rep": "REP"}[side],
            "early": early,
            "mail": 0,
            "eday": eday,
        }

    d = pd.DataFrame(
        [
            row("X", "1", "Y", "dem", 60, 30),
            row("X", "1", "Y", "rep", 40, 70),
            row("X", "2", "Y", "dem", 0, 0),
            row("X", "2", "Y", "rep", 0, 0),  # precinct 2 not in yet
            row("Y", "1", "Y", "dem", 50, 20),
            row("Y", "1", "Y", "rep", 50, 30),
        ]
    )
    V26 = pd.DataFrame(
        [(2026, c, m, p, 10) for c in "XY" for m in ("early", "mail") for p in N.PARTIES],
        columns=["year", "county", "method", "party", "n"],
    )
    obs, complete = N.county_obs(d, "US SENATE", V26)
    assert complete == {"Y"}
    assert set(zip(obs["county"], obs["method"])) == {("X", "early"), ("Y", "early"), ("Y", "eday")}
    assert obs.set_index(["county", "method"]).loc[("Y", "eday"), "margin"] == pytest.approx(-20.0)
