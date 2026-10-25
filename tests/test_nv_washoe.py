"""Washoe County early vote (nv_washoe_early.py): the newest file on the page, the rolls and returns from the mail ballot
file, one count per voter across mail and in person."""

import pytest

from midterms import nv_washoe_early as W


def test_latest_file_is_the_highest_number():
    html = (
        '<a href="resultsfiles/Mail-Ballot-Detail---Public-Consumption9.csv">x</a>'
        '<a href="resultsfiles/Mail-Ballot-Detail---Public-Consumption10.csv">x</a>'
        '<a href="resultsfiles/In-Person-Turnout---Public-Consumption3.csv">x</a>'
    )
    assert W.latest(html, "mail").endswith("Consumption10.csv")
    assert W.latest(html, "in_person").endswith("Consumption3.csv")
    assert W.latest("", "mail") is None


def test_counts():
    mail = (
        b"VOTER_ID,FULL_NAME,VOTER_REG_PARTY,BALLOT_STATUS,BALLOT_RETURNED_DATE\n"
        b"1,A,DEM,Accepted,10/20/2026 12:00:00 AM\n"
        b"2,B,REP,Mailed,\n"
        b"3,C,NP ,Signature Cure Needed,10/21/2026 12:00:00 AM\n"
        b"4,D,REP,Canceled,\n"  # a cancelled ballot: not on the rolls, not a return
        b"5,E,REP,Surrendered and Voted in Person,\n"
        b"6,F,IAP,Accepted,10/22/2026 12:00:00 AM\n"
    )
    ip = (
        b"COUNTY,VOTER_ID,FULL_NAME,VOTER_REG_PARTY,PRECINCT,DATE_VOTED,VOTE_LOCATION\n"
        b"WASHOE,5,E,REP,1,10/21/2026 12:00:00 AM,X\n"
        b"WASHOE,1,A,DEM,1,10/21/2026 12:00:00 AM,X\n"  # already counted by mail
    )
    rolls, m = W.mail(mail)
    assert len(rolls) == 5 and dict(rolls.value_counts()) == {"REP": 2, "DEM": 1, "NP": 1, "OTH": 1}
    assert sorted(m["id"]) == ["1", "3", "6"] and int(m["cure"].sum()) == 1
    res = W.summary(rolls, m, W.in_person(ip), "2026-10-24")
    assert res["mail"]["returned"] == {"DEM": 1, "REP": 0, "NP": 1, "OTH": 1, "total": 3}
    assert res["in_person"]["voted"]["total"] == 1  # voter 1 is not counted twice
    assert res["combined"]["R_minus_D_pct"] == pytest.approx(0.0)
    assert res["registration"]["R_minus_D_pct"] == pytest.approx(20.0)
    assert res["through"] == "2026-10-22"
