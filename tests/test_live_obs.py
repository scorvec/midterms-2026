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
        ]
    }
    res = pd.DataFrame(
        [
            _row("KY-6", winner="D"),
            _row("AK-1", winner="I", chal_votes="100", rep_votes="90", total_votes="200"),
            _row("CA-6", winner="I", chal_votes="40", rep_votes="50", total_votes="100"),
            _row("OK-5", chal_votes="48", rep_votes="50", total_votes="100"),
            _row("TX-9", winner="O"),
        ]
    )
    base = {"CA-6": 1000, "OK-5": 100}
    obs = {o["race"]: o for o in LO.observations(doc, res, base)}
    assert obs["H:KY-6"] == {"race": "H:KY-6", "call": "D"}
    assert obs["H:AK-1"] == {"race": "H:AK-1", "call": "D"}  # ranked choice: the call, no first-round count
    assert obs["H:CA-6"] == {"race": "H:CA-6", "call": "R", "d": 40.0, "r": 50.0, "pct": 12.5, "skew_sd": LO.LATE_SD}
    assert obs["H:OK-5"]["pct"] == 100 * LO.F_CAP  # more votes than the turnout estimate: capped, never complete
    assert "H:TX-9" not in obs  # a winner of neither side says nothing about either
