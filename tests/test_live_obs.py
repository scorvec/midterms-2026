import pandas as pd

from midterms import live_obs as LO, scorecard as SC


def _row(seat, **kw):
    return dict.fromkeys(SC.COLS, "") | {"office": "house", "seat": seat} | kw


def test_calls_follow_the_sides_and_counts_estimate_the_share_in():
    doc = {
        "races": [
            {"office": "house", "seat": "KY-6", "challenger_party": "D"},
            {"office": "house", "seat": "AK-1", "challenger_party": "I", "rcv": True},
            {"office": "house", "seat": "CA-6", "challenger_party": "D", "opponent_party": "I"},
            {"office": "house", "seat": "OK-5", "challenger_party": "D"},
            {"office": "house", "seat": "TX-9", "challenger_party": "D"},
            {"office": "senate", "seat": "MS", "challenger_party": "D"},
        ]
    }
    res = pd.DataFrame(
        [
            _row("KY-6", winner="D"),
            _row("AK-1", winner="I", chal_votes="100", rep_votes="90", total_votes="200"),
            _row("CA-6", winner="I", chal_votes="40", rep_votes="50", total_votes="100"),
            _row("OK-5", chal_votes="48", rep_votes="50", total_votes="100"),
            _row("TX-9", winner="O"),
            _row("MS", office="senate", chal_votes="49", rep_votes="48", total_votes="100", pct_counted="95.6"),
        ]
    )
    base = {"CA-6": 1000, "OK-5": 100}
    obs = {o["race"]: o for o in LO.observations(doc, res, base)}
    assert obs["H:KY-6"] == {"race": "H:KY-6", "call": "D"}
    assert obs["H:AK-1"] == {"race": "H:AK-1", "call": "D"}  # ranked choice: the call, no first-round count
    ca6 = {"race": "H:CA-6", "call": "R", "d": 40.0, "r": 50.0, "pct": 12.5, "est": True, "skew_sd": LO.LATE_SD}
    assert obs["H:CA-6"] == ca6
    assert obs["H:OK-5"]["pct"] == 100 * LO.F_CAP  # more votes than the turnout estimate: capped, never complete
    assert "H:TX-9" not in obs  # a winner of neither side says nothing about either
    assert obs["S:MS"] == {"race": "S:MS", "d": 49.0, "r": 48.0, "pct": 95.6}  # a reported share counted: not estimated


def test_the_count_history_records_each_new_count_and_gives_the_late_ballots():
    first = pd.DataFrame(
        [
            _row(
                "MS",
                office="senate",
                chal_votes="490",
                rep_votes="480",
                total_votes="1000",
                pct_counted="95",
                updated="t0",
            )
        ]
    )
    hist = LO.record(first, pd.DataFrame(columns=LO.HIST_COLS))
    assert len(LO.record(first, hist)) == 1  # the same votes again: nothing appended
    later = pd.DataFrame(
        [
            _row(
                "MS",
                office="senate",
                chal_votes="560",
                rep_votes="510",
                total_votes="1100",
                pct_counted="96",
                updated="t1",
            ),
            _row("OK-5", chal_votes="48", rep_votes="50", total_votes="100"),
        ]
    )
    hist = LO.record(later, hist)
    assert hist.updated.tolist() == ["t0", "t1"]  # a count with an estimated share is not recorded
    doc = {"races": [{"office": "senate", "seat": "MS", "challenger_party": "D"}]}
    (ms,) = LO.observations(doc, later, {"OK-5": 100}, hist)
    assert (ms["late_n"], ms["late"]) == (100, 40.0)  # 70 more for the challenger, 30 for the Republican, of 100
    (first_only,) = LO.observations(doc, first, {}, hist.iloc[:1])
    assert "late" not in first_only  # nothing counted since the first entry
