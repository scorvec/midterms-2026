"""North Carolina early-vote tracker (nc_tracker.py): ballot counting, the committed history and the forecast gate."""

import io
import json

import pandas as pd
import pytest

from midterms import nc_tracker as NT

HDR = "county_desc,ncid,race,age,voter_party_code,ballot_req_type,ballot_rtn_dt,ballot_rtn_status\n"


def test_ballots_counting():
    """Accepted only (any accepted status), once per voter (first return), up to the day before the live file's date."""
    rows = [
        "WAKE,A1,WHITE,70,DEM,EARLY VOTING,10/15/2026,ACCEPTED",
        "WAKE,A1,WHITE,70,DEM,MAIL,10/20/2026,ACCEPTED",  # the same voter again: counted once, on the first day
        "WAKE,A2,BLACK or AFRICAN AMERICAN,22,REP,MAIL,10/14/2026,ACCEPTED - CURED",
        "DARE,A3,ASIAN,45,LIB,EARLY VOTING,10/15/2026,CANCELLED",
        "DARE,A4,WHITE,30,UNA,EARLY VOTING,10/16/2026,ACCEPTED",  # the file's own date: a partial day, left out
        "DARE,A5,WHITE,50,UNA,MAIL,09/01/2026,ACCEPTED",  # earlier than DBE_MAX: counted on day 45
    ]
    b = NT.ballots(io.BytesIO((HDR + "\n".join(rows)).encode()), 2026, file_date=pd.Timestamp("2026-10-16").date())
    assert sorted(b.ncid) == ["A1", "A2", "A5"]
    r = b.set_index("ncid")
    assert r.loc["A1", "dbe"] == 19 and r.loc["A1", "method"] == "In person"
    assert r.loc["A2", "party"] == "REP" and r.loc["A2", "race"] == "Black" and r.loc["A2", "age"] == "18-25"
    assert r.loc["A5", "dbe"] == NT.DBE_MAX and r.loc["A5", "method"] == "Mail"


def test_ballots_2008_format():
    """2008's files: no NCID (county + registration number), status OK, dates YYYYMMDD."""
    hdr = "county_desc,voter_reg_num,race,age,voter_party_code,ballot_request_type,ballot_rtn_dt,ballot_rtn_status\n"
    rows = [
        "WAKE,1,WHITE,40,DEM,ONE STOP,20081030,OK",
        "DARE,1,WHITE,40,REP,MAIL,20081029,OK",
        "WAKE,2,W,40,REP,MAIL,,SPOILED",
    ]
    b = NT.ballots(io.BytesIO((hdr + "\n".join(rows)).encode()), 2008)
    assert len(b) == 2 and set(b.party) == {"DEM", "REP"}  # the same number in two counties is two voters


def test_committed_history():
    pace, reg = pd.read_csv(NT.STATIC / "party_pace.csv"), pd.read_csv(NT.STATIC / "party_reg.csv")
    assert set(pace.year) == set(reg.year) == {y for y in NT.ELECTIONS if y != NT.LIVE}
    assert pace.dbe.between(NT.DBE_MIN, NT.DBE_MAX).all()
    for y in (2018, 2020, 2022, 2024):  # the full tables agree with the party paces they were summed from
        st = pd.read_csv(NT.STATIC / f"state_{y}.csv")
        assert st.n.sum() == pace[pace.year == y].n.sum()


def test_gate_stays_shut():
    g = json.loads(NT.GATE_JSON.read_text())
    assert g["open"] is False
    assert all(v["p"] > 0.05 for v in g["leads"].values())  # no lead shows skill
    scored = [r for r in g["rows"] if r["poll_miss"] is not None]
    assert len(scored) == g["leads"]["17"]["n"] == 5
    c = NT.composition(2022, 17)
    assert next(r for r in g["rows"] if r["year"] == 2022)["comp_17"] == pytest.approx(c, abs=0.01)
