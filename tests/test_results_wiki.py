import pandas as pd

from midterms import results_wiki as RW, scorecard as SC

TICK = '<span><span><img alt="Green tick" src="x.png"></span></span><span style="display:none">Y</span>'
OVERVIEW = f"""<table class="wikitable"><tr><th>Location</th><th>Member</th><th>Results</th><th>Candidates</th></tr>
<tr><td>Kentucky 6</td><td>Andy Barr</td><td>Incumbent retired. Democratic gain.</td><td><div class="plainlist"><ul>
<li><span>▌</span>{TICK}<b><a>Zach Dembo</a></b> (Democratic)</li>
<li><span>▌</span><a>Ralph Alvarado</a> (Republican)</li><li><span>▌</span>Jay Bowman (Independent)</li></ul></div></td></tr>
<tr><td>California 22</td><td>David Valadao</td><td>Incumbent advanced to general.</td><td><div><ul>
<li><span>▌</span>David Valadao (Republican)</li><li><span>▌</span>Randy Villegas (Democratic)</li></ul></div></td></tr>
<tr><td>Alaska at-large</td><td>Nick Begich</td><td>Incumbent re-elected.</td><td><div><ul>
<li><span>▌</span>{TICK}<b>Nick Begich III</b> (Republican)</li><li><span>▌</span>Bill Hill (Independent)</li></ul></div></td></tr>
</table>"""


def _box(caption, rows, total):
    body = "".join(f"<tr><td></td><td>{p}</td><td>{n}</td><td>{v}</td><td></td></tr>" for p, n, v in rows)
    return (
        f'<table class="wikitable plainrowheaders"><caption>{caption}[12]</caption>'
        f"<tr><th>Party</th><th>Candidate</th><th>Votes</th><th>%</th></tr>{body}"
        f"<tr><td>Total votes</td><td>{total}</td><td>100.0</td></tr></table>"
    )


def test_calls_read_the_tick_not_the_sort_key():
    c = RW.calls("house", OVERVIEW)
    assert c["KY-6"]["winner"] == "D" and c["KY-6"]["name"] == "Zach Dembo"
    assert c["CA-22"]["winner"] is None
    assert c["AK-1"]["winner"] == "R" and c["AK-1"]["name"] == "Nick Begich III"


def test_counts_take_the_general_box_and_sum_fusion_lines():
    page = _box("Democratic primary results", [("Democratic", "Ann Smith", "9,000")], "9,000") + _box(
        "New York's 4th congressional district, 2026",
        [
            ("Democratic", "Ann Smith", "120,000"),
            ("Working Families", "Ann Smith", "6,000"),
            ("Republican", "Bob Jones (incumbent)", "110,500"),
        ],
        "236,500",
    )
    assert RW.count(list(RW.boxes(page)), "Ann Smith", "Bob Jones") == (126000, 110500, 236500)
    empty = _box(
        "2026 United States Senate election in Mississippi",
        [("Republican", "Bob Jones (incumbent)", ""), ("Democratic", "Ann Smith", "")],
        "",
    )
    assert RW.count(list(RW.boxes(empty)), "Ann Smith", "Bob Jones") is None
    one_ballot = _box(
        "Louisiana's 3rd congressional district, 2026",
        [
            ("Republican", "Clay Higgins (incumbent)", "150,000"),
            ("Democratic", "John Day", "60,000"),
            ("Republican", "Kate Cotten", "20,000"),
            ("Democratic", "Caleb Walker", "15,000"),
            ("Independent", "Tia LeBrun", "5,000"),
        ],
        "250,000",
    )
    bx = list(RW.boxes(one_ballot))
    assert RW.count(bx, "John Day", "Clay Higgins") == (60000, 150000, 250000)
    assert RW.count(bx, "John Day", "Clay Higgins", ("D", "R")) == (75000, 170000, 250000)


def test_update_keeps_official_rows_and_flags_a_stray_winner():
    forecast = {
        "races": [
            {"office": "house", "seat": s, "challenger": c, "republican": r}
            for s, c, r in [
                ("KY-6", "Zach Dembo", "Ralph Alvarado"),
                ("CA-22", "Randy Villegas", "David Valadao"),
                ("NY-4", "Ann Smith", "Bob Jones"),
                ("OR-5", "Janelle Bynum", "Jen Bass"),
            ]
        ]
    }
    res = pd.DataFrame(
        [
            dict.fromkeys(SC.COLS, "")
            | {"office": "house", "seat": f["seat"], "challenger": f["challenger"]}
            | {"republican": f["republican"]}
            for f in forecast["races"]
        ]
    )
    res.loc[3, ["winner", "status", "source"]] = ["D", "certified", "Oregon Secretary of State"]
    called = {
        ("house", "KY-6"): {"winner": "D", "name": "Zach Dembo"},
        ("house", "OR-5"): {"winner": "R", "name": "Jen Bass"},
    }
    called[("house", "NY-4")] = {"winner": "O", "name": "Carl Third"}
    counts = {("house", "CA-22"): (90, 100, 200)}
    moved = RW.update(res, forecast, called, counts, "2026-11-04T12:00Z")
    assert len(moved) == 3
    r = res.set_index("seat")
    assert r.loc["KY-6", ["winner", "status", "source"]].tolist() == ["D", "called", "Wikipedia"]
    assert r.loc["CA-22", ["winner", "status", "chal_votes", "rep_votes", "total_votes"]].tolist() == [
        "",
        "counting",
        "90",
        "100",
        "200",
    ]
    assert r.loc["NY-4", "note"] == "winner Carl Third is neither forecast side"
    assert r.loc["OR-5", ["winner", "status", "source"]].tolist() == ["D", "certified", "Oregon Secretary of State"]
    assert RW.update(res, forecast, called, counts, "2026-11-04T13:00Z") == []


def _national(d, r, total):
    party = (
        '<tr><td style="background:#00f">&nbsp;</td><td><a>{}</a></td><td>{}</td><td></td><td></td><td>215</td></tr>'
    )
    return (
        '<table class="wikitable"><tr><th colspan="2">Parties</th><th colspan="3">Popular vote</th><th>Seats</th></tr>'
        "<tr><th>Vote</th><th>%</th><th>Change</th><th>2024</th></tr>"
        + party.format("Republican Party", r)
        + party.format("Democratic Party", d)
        + f"<tr><th colspan=2>Totals</th><th>{total}</th><th>100.00%</th><th>—</th><th>435</th></tr></table>"
    )


def test_national_vote_waits_for_the_table():
    assert RW.national(OVERVIEW + _national("", "", "")) is None
    assert RW.national(_national("60,500,000[a]", "48,000,000", "110,000,000")) == (60500000, 48000000, 110000000)
    assert RW.national(_national("60,500,000", "48,000,000", "100,000,000")) is None  # sides beyond the total


def test_a_hand_count_keeps_its_numbers_and_takes_the_call():
    forecast = {
        "races": [{"office": "senate", "seat": "MS", "challenger": "Scott Colom", "republican": "Cindy Hyde-Smith"}]
    }
    row = dict.fromkeys(SC.COLS, "") | {"office": "senate", "seat": "MS", "status": "counting", "source": "news count"}
    res = pd.DataFrame([row | {"chal_votes": "462821", "rep_votes": "462778", "total_votes": "945534"}])
    res = pd.concat([res, pd.DataFrame([dict.fromkeys(SC.COLS, "") | {"office": "national", "seat": "US"}])])
    res = res.reset_index(drop=True)
    counts = {("senate", "MS"): (1, 2, 3), ("national", "US"): (60500000, 48000000, 110000000)}
    assert len(RW.update(res, forecast, {}, counts, "2026-11-04T13:00Z")) == 1  # the national row only
    assert res.loc[0, ["status", "chal_votes", "source"]].tolist() == ["counting", "462821", "news count"]
    assert res.loc[1, ["status", "chal_votes", "total_votes", "source"]].tolist() == [
        "counting",
        "60500000",
        "110000000",
        "Wikipedia",
    ]
    RW.update(res, forecast, {("senate", "MS"): {"winner": "D", "name": "Scott Colom"}}, counts, "2026-11-04T14:00Z")
    assert res.loc[0, ["winner", "status", "chal_votes", "updated"]].tolist() == [
        "D",
        "called",
        "462821",
        "2026-11-04T14:00Z",
    ]


def test_outside_calls_fill_gaps_and_wikipedia_wins_a_clash():
    called = {("house", "CA-22"): {"winner": "R", "name": "David Valadao"}}
    rows = [
        {"office": "senate", "seat": "MS", "winner": "D", "source": "NBC News", "called_at": ""},
        {"office": "house", "seat": "CA-22", "winner": "D", "source": "NBC News", "called_at": ""},
    ]
    assert RW.outside_calls(called, rows) == ["house CA-22: Wikipedia R, NBC News D"]
    assert called[("senate", "MS")] == {"winner": "D", "name": None, "by": "NBC News"}
    assert called[("house", "CA-22")]["winner"] == "R"
    forecast = {
        "races": [{"office": "house", "seat": "KY-6", "challenger": "Zach Dembo", "republican": "Ralph Alvarado"}]
    }
    res = pd.DataFrame([dict.fromkeys(SC.COLS, "") | {"office": "house", "seat": "KY-6", "challenger": "Zach Dembo"}])
    RW.update(
        res, forecast, {("house", "KY-6"): {"winner": "D", "name": None, "by": "NBC News"}}, {}, "2026-11-04T14:00Z"
    )
    assert res.loc[0, ["winner", "status", "note"]].tolist() == ["D", "called", "called by NBC News"]


def test_page_titles():
    assert RW.race_page("senate", "OH", True) == "2026 United States Senate special election in Ohio"
    assert RW.race_page("governor", "NV", False) == "2026 Nevada gubernatorial election"
    assert RW.race_page("house", "KY-6", False) == "2026 United States House of Representatives elections in Kentucky"
    assert RW.race_page("house", "AK-1", False) == "2026 United States House of Representatives election in Alaska"
