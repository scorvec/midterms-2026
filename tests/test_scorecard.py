"""The scorecard (scorecard.py), the frozen forecast (final.py), the no-majority chance (majority.py), the Senate's late-count
pivot (senate2026.decides_control) and the House seat count with an independent challenger (model.house_seat_counts)."""

import json

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from midterms import final as FN
from midterms import majority as MJ
from midterms import model as M
from midterms import scorecard as SC
from midterms import senate2026 as SN

Q = [float(norm.ppf(u / 100, loc=2.0, scale=4.0)) for u in FN.PCTS]  # a N(2, 4) margin forecast


def _doc():
    races = [
        {
            "office": "house",
            "seat": "AK-1",
            "challenger": "Bill Hill",
            "challenger_party": "I",
            "republican": "Nick Begich III",
            "p": 0.16,
            "q": Q,
            "rcv": True,
            "raters": 0.2,
        },
        {
            "office": "house",
            "seat": "PA-7",
            "challenger": "D",
            "challenger_party": "D",
            "republican": "R",
            "p": 0.7,
            "q": Q,
            "rcv": False,
            "raters": 0.6,
        },
        {
            "office": "senate",
            "seat": "GA",
            "challenger": "Ossoff",
            "challenger_party": "D",
            "republican": "Collins",
            "p": 0.96,
            "q": Q,
            "rcv": False,
        },
        {
            "office": "governor",
            "seat": "VT",
            "challenger": "Janoo",
            "challenger_party": "D",
            "republican": "Scott",
            "p": 0.28,
            "q": Q,
            "rcv": False,
            "majority": {"rule": "legislature", "p_none": 0.5},
        },
    ]
    grid = np.arange(-10, 20.25, 0.25)
    chambers = {
        "house": {"hist": {"0": 25, "1": 50, "2": 25}, "p_maj": 0.5},
        "senate": {"hist2": {"47|0": 50, "48|0": 50}, "p_ctrl": 0.3, "holdover_d": 47, "holdover_r": 52},
        "governor": {"hist": {"20": 1, "21": 1}, "holdover_d": 20},
        "national_vote": {"center": 5.0, "grid": grid.tolist(), "dens": norm.pdf(grid, 5, 3).tolist()},
    }
    return {
        "asof": "2026-11-03",
        "races": races,
        "chambers": chambers,
        "house_fixed": {"D": 0, "R": 0},
        "house_seats_total": 2,
        "house_majority": 2,
    }


def test_outcome_is_the_republican_losing():
    assert SC.outcome("I") == 1 and SC.outcome("D") == 1 and SC.outcome("r") == 0
    assert SC.outcome(float("nan")) is None and SC.outcome("") is None
    # CA-6: an independent incumbent against a Democrat, p = the Democrat's chance
    assert SC.outcome("I", "I") == 0 and SC.outcome("D", "I") == 1


def test_freeze_refuses_an_incomplete_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(FN, "DETAIL", tmp_path / "detail.json")
    monkeypatch.setattr(FN, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(FN, "FINAL", tmp_path / "final" / "forecast_2026.json")
    monkeypatch.setattr(FN, "FINAL_MODEL", tmp_path / "final" / "model_2026.json")
    monkeypatch.setattr(FN, "PUBLISHED", tmp_path / "model.json")
    doc = {**_doc(), "E_now": 9.0}
    assert FN.shortfall(doc) == []
    doc["races"] = [r for r in doc["races"] if r["office"] != "governor"]
    del doc["chambers"]["governor"]
    assert FN.shortfall(doc) == ["governor: 0 races"]
    FN.DETAIL.write_text(json.dumps(doc))
    with pytest.raises(SystemExit):
        FN.freeze()
    FN.freeze(force=True)
    assert FN.FINAL.exists()


def test_brier_and_log_loss():
    assert SC.brier([0.8, 0.3], [1, 0]) == pytest.approx((0.04 + 0.09) / 2)
    assert SC.log_loss([0.5], [1]) == pytest.approx(np.log(2))
    assert np.isfinite(SC.log_loss([1.0], [0]))  # clipped


def test_pit_and_crps_on_a_normal_forecast():
    assert SC.pit(Q, 2.0) == pytest.approx(0.5, abs=0.005)
    assert SC.pit(Q, 2.0 + 4.0 * norm.ppf(0.9)) == pytest.approx(0.9, abs=0.005)
    assert SC.pit(Q, -100) == pytest.approx(0.005) and SC.pit(Q, 100) == pytest.approx(0.995)
    # closed form for a normal: sigma * (z (2 Phi(z) - 1) + 2 phi(z) - 1 / sqrt(pi)); the percentile grid is close to it
    z = (5.0 - 2.0) / 4.0
    exact = 4.0 * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi))
    assert SC.crps(Q, 5.0) == pytest.approx(exact, rel=0.05)


def test_margin_units():
    plain = {"status": "final", "chal_votes": 450, "rep_votes": 500, "total_votes": 1000}
    assert SC.actual_margin(plain, rcv=False) == pytest.approx(-5.0)
    assert SC.actual_margin({**plain, "status": "called"}, rcv=False) is None  # not complete yet
    # ranked choice: the final round over the FIRST round's ballots (exhausted ballots stay in the denominator)
    rounds = {**plain, "rcv_chal": 480, "rcv_rep": 500, "rcv_first_round": 1000}
    assert SC.actual_margin(rounds, rcv=True) == pytest.approx(-2.0)
    assert SC.actual_margin(plain, rcv=True) is None  # nobody over 50 % and no rounds yet
    assert SC.actual_margin({**plain, "rep_votes": 520}, rcv=True) == pytest.approx(-7.0)  # first-round majority


def test_mid_pit():
    assert SC.mid_pit({"0": 25, "1": 50, "2": 25}, 1) == pytest.approx(0.5)
    assert SC.mid_pit({"0": 25, "1": 50, "2": 25}, 2) == pytest.approx(0.875)


def test_score_end_to_end():
    res = pd.DataFrame(
        [
            {
                "office": "house",
                "seat": "AK-1",
                "winner": "R",
                "status": "final",
                "chal_votes": 450,
                "rep_votes": 500,
                "total_votes": 1000,
                "rcv_chal": 470,
                "rcv_rep": 510,
                "rcv_first_round": 1000,
            },
            {"office": "house", "seat": "PA-7", "winner": "D", "status": "called"},
            {
                "office": "senate",
                "seat": "GA",
                "winner": "D",
                "status": "certified",
                "chal_votes": 55,
                "rep_votes": 45,
                "total_votes": 100,
            },
            {
                "office": "governor",
                "seat": "VT",
                "status": "final",
                "chal_votes": 470,
                "rep_votes": 490,
                "total_votes": 1000,
            },
            {
                "office": "national",
                "seat": "US",
                "status": "final",
                "chal_votes": 52,
                "rep_votes": 47,
                "total_votes": 100,
            },
        ]
    )
    sc = SC.score(_doc(), res)
    a = sc["by_office"]["all"]
    assert a["called"] == 3 and a["of"] == 4
    assert a["brier"] == pytest.approx(SC.brier([0.16, 0.7, 0.96], [0, 1, 1]), abs=1e-4)
    assert sc["by_office"]["house"]["raters"]["brier_raters"] == pytest.approx(SC.brier([0.2, 0.6], [0, 1]), abs=1e-4)
    ak = next(r for r in sc["races"] if r["seat"] == "AK-1")
    assert ak["margin"] == pytest.approx(-4.0)  # the rounds, not the first round
    vt = next(r for r in sc["races"] if r["seat"] == "VT")
    assert (
        vt["majority"] == {"p_none": 0.5, "none": True} and vt["y"] is None
    )  # 49 % leader, legislature still to choose
    ch = sc["chambers"]
    assert ch["house"] == {
        "d_called": 1,
        "undecided": 0,
        "d_seats": 1,
        "independents": 0,
        "pit": 0.5,
        "p_maj": 0.5,
        "d_majority": False,
    }
    assert ch["senate"]["d_seats"] == 48 and ch["senate"]["d_controls"] is False
    assert ch["governor"] == {"d_called": 20, "undecided": 1}
    assert ch["national_vote"]["pit"] == pytest.approx(norm.cdf(5.0, 5, 3), abs=0.01)


def test_freeze_refuses_overwrite_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(FN, "DETAIL", tmp_path / "detail.json")
    monkeypatch.setattr(FN, "FINAL_DIR", tmp_path / "final")
    monkeypatch.setattr(FN, "FINAL", tmp_path / "final" / "forecast_2026.json")
    monkeypatch.setattr(FN, "FINAL_MODEL", tmp_path / "final" / "model_2026.json")
    monkeypatch.setattr(FN, "PUBLISHED", tmp_path / "model.json")
    FN.DETAIL.write_text(json.dumps({**_doc(), "E_now": 9.0}))
    FN.PUBLISHED.write_text(json.dumps({"asof": "2026-11-03"}))
    FN.freeze()
    assert FN.verify()
    # the archived copy is the page's data as published; the published file is then marked final
    assert "final" not in json.loads(FN.FINAL_MODEL.read_text())
    assert (
        json.loads(FN.PUBLISHED.read_text())["final"]["sha256"] == json.loads(FN.FINAL.read_text())["frozen"]["sha256"]
    )
    with pytest.raises(SystemExit):
        FN.freeze()
    d = json.loads(FN.FINAL.read_text())
    d["races"][0]["p"] = 0.99  # an edit after the freeze
    FN.FINAL.write_text(json.dumps(d))
    assert not FN.verify()


def test_no_majority_chance():
    m = np.array([-6.0, -2.0, 0.5, 3.0, 10.0])
    assert MJ.p_no_majority(m, np.full(5, 4.0)) == pytest.approx(0.6)
    d = MJ.minor_draws(np.array([2.9, 4.6, 8.5, 2.9, 4.6, 5.2]), 200_000)
    assert np.exp(np.log(d).mean()) == pytest.approx(np.exp(np.log([2.9, 4.6, 8.5, 2.9, 4.6, 5.2]).mean()), rel=0.01)


def test_independent_house_win_is_not_a_democratic_seat():
    seats = pd.DataFrame({"seat": ["AK-1", "PA-7"], "challenger_party": ["I", "D"]})
    d = np.array([[True, True], [True, False], [False, True]])
    ds, di = M.house_seat_counts(d, seats)
    assert ds.tolist() == [1, 0, 1] and di.tolist() == [1, 1, 0]


def test_late_count_pivot():
    S = pd.DataFrame({"state": ["AK", "GA"], "challenger_party": ["D", "D"]})
    # 49 holdover Democrats + both races up: AK decides control only when GA goes D (50 D v 50 R -> R; 51 D -> D)
    old = SN.NOW_D
    try:
        SN.NOW_D = 49
        d = np.array([[True, True], [False, True], [True, False], [False, False]])
        assert SN.decides_control(S, d, 0, "AK") == pytest.approx(0.5)
    finally:
        SN.NOW_D = old
