"""The turnout projection's arithmetic (turnout_proj.py): banked counts, the backtest split, the calendar rule, the range."""

import numpy as np
import pandas as pd
import pytest

from midterms import turnout_proj as TP


def _series(shares: dict, total: float = 1_000_000.0, lead: int = 10) -> tuple[pd.DataFrame, pd.Series]:
    """Each year banks `share` of its total exactly `lead` days out, nothing else before Election Day."""
    b = pd.DataFrame([{"year": y, "dbe": lead, "n": round(f * total)} for y, f in shares.items()])
    return b, pd.Series(total, index=list(shares))


def test_banked_counts_everything_returned_by_the_lead():
    b = pd.DataFrame({"year": [2022] * 4, "dbe": [20, 12, 10, 3], "n": [5, 7, 11, 13]})
    assert TP.banked_at(b, 2022, 10) == 23  # 20, 12 and 10 days out; not 3
    assert TP.banked_at(b, 2022, 25) is None  # before the year's first ballot: no reading
    assert TP.banked_at(b, 2018, 10) is None


def test_projection_divides_by_the_reference_share():
    b, totals = _series({2018: 0.20, 2022: 0.25, 2026: 0.30})
    f22 = TP.reference(b, totals, 2026, 10, "A")
    assert f22 == pytest.approx(0.25)  # the last midterm's share
    banked = TP.banked_at(b, 2026, 10)
    assert banked / f22 == pytest.approx(1_200_000)  # 300k banked / 25 % = 1.2 million


def test_backtest_uses_only_earlier_cycles_and_skips_2026():
    b, totals = _series({2014: 0.10, 2018: 0.20, 2022: 0.25, 2026: 0.99})
    e = {x["year"]: x["e"] for x in TP.errors(b, totals, 10, "A")}
    assert set(e) == {2018, 2022}  # 2014 has no 2010 to compare with; 2026 is never a target
    assert e[2018] == pytest.approx(np.log(0.20 / 0.10))
    assert e[2022] == pytest.approx(np.log(0.25 / 0.20))


def test_calendar_rule_drops_pairs_that_opened_on_different_days():
    b, totals = _series({2010: 0.30, 2014: 0.05, 2018: 0.30, 2022: 0.31})
    cal = {2010: 19, 2014: 12, 2018: 20, 2022: 19}
    years = {x["year"] for x in TP.errors(b, totals, 10, "A", cal)}
    assert years == {2022}  # 2014 (opened 12 days out) is compared with nothing; 2018 vs 2022 is one day apart
    assert TP.comparable(None, 2014, 2018)  # a state with no in-person early voting has one calendar


def test_range_needs_three_and_widens_with_bias():
    assert TP.interval([0.1, -0.1]) is None
    lo, hi = TP.interval([0.1, -0.1, 0.1])
    w = TP.stats.t.ppf(0.9, 3) * 0.1
    assert lo == pytest.approx(np.exp(-w)) and hi == pytest.approx(np.exp(w))
    lo_b, hi_b = TP.interval([0.2, 0.2, 0.2])  # a steady past miss widens the range instead of shifting it
    assert lo_b < lo and hi_b > hi and lo_b * hi_b == pytest.approx(1.0)


def test_no_range_means_no_published_total():
    b, totals = _series({2018: 0.20, 2022: 0.25, 2026: 0.30})
    p = TP.project(b, totals, 10, "A", {}, None)
    assert "total" not in p and "reason" in p and p["n_midterm"] == 1


def test_fec_sheet_matches_every_cycle_name():
    names = {
        2008: ["Table 1. 2008 Pres Popular Vote", "Table 3.GEVotes for Pres, H & S", "Table 4. GE Votes Cast by Party"],
        2010: ["2010 US House & Senate Results", "Table 1. GE Votes Cast for Cong", "Table 3. Prim & Gen Votes Cast"],
        2020: ["2. Table 1 Pres Popular Vote", "4. Table 3 GEVotefor Pres, H, S", "5. Table 4 GE VotesCastbyParty"],
        2022: ["2. Table 1 GE Vote for S & H", "3. Table 2 GE VotesCastbyParty", "4. Table 3 P&GVotesCastforCong"],
    }
    want = {2008: 1, 2010: 1, 2020: 1, 2022: 0}
    for y, n in names.items():
        assert TP._fec_sheet(n) == n[want[y]]


def test_senate_total_is_the_general_and_the_larger_race():
    pres = pd.DataFrame({"state_po": ["GA"] * 2, "year": [2020] * 2, "totalvotes": [5_000_000] * 2})
    sen = pd.DataFrame(
        {
            "state_po": ["GA"] * 4,
            "year": [2020] * 4,
            "office": ["US SENATE"] * 4,
            "stage": ["gen", "gen", "runoff", "runoff"],
            "special": [False, True, False, True],
            "totalvotes": [4_950_000, 4_990_000, 4_480_000, 4_480_000],
        }
    )
    t = TP.mit_totals(pres, sen).set_index("state").loc["GA"]
    assert t["president"] == 5_000_000
    assert t["senate"] == 4_990_000  # the larger general race; neither runoff, never the sum
