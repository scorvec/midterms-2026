import pandas as pd

from midterms import results_page as RP, scorecard as SC

DOC = {"chambers": {"national_vote": {"center": 9.2, "q": {"10": 5.3, "90": 13.1}}}}
POP = {"parties": [["Democratic", 62_000_000], ["Republican", 48_000_000]], "total": 112_000_000, "source": "civicAPI"}


def _row(d, r, t, status="counting"):
    row = dict.fromkeys(SC.COLS, "")
    row.update(
        office="national", seat="US", chal_votes=d, rep_votes=r, total_votes=t, status=status, source="Wikipedia"
    )
    return pd.DataFrame([row])


def test_a_partial_national_row_gives_way_to_the_fuller_tally():
    partial = RP.national(DOC, _row("6085414", "5585824", "11892146"), POP)["count"]
    assert partial["source"] == "civicAPI" and partial["total"] == 112_000_000
    fuller = RP.national(DOC, _row("63000000", "49000000", "113000000"), POP)["count"]
    assert fuller["source"] == "Wikipedia" and not fuller["final"]
    official = RP.national(DOC, _row("6085414", "5585824", "11892146", "certified"), POP)["count"]
    assert official["source"] == "Wikipedia" and official["final"]
