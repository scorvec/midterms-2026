"""Results of the 2026 races from Wikipedia into data/results/results_2026.csv, which the scorecard reads (scorecard.py).

    python -m midterms.results_wiki        # check every results page for edits, update the results file, print what moved

Calls: the winner ticked on the three overview pages (House, Senate, governors: one row per race, its result cell reads
"Democratic gain", "Republican hold", ...). Counts: the general-election box on each race's own page (the House: one page
per state, one box per district) once editors fill it in; an empty or half-filled box leaves the counts blank. Ranked-choice
races (Alaska, Maine) get no count here: their boxes carry rounds, and the scorer needs the final round from official returns.
Calls made elsewhere first (data/results/outside_calls.csv: a race a news organisation has called, NBC News's included, entered
by hand with its source; NBC's pages are never read by a script) fill in a race Wikipedia has not ticked yet; a winner
ticked on Wikipedia takes precedence, and a disagreement is printed.
The national House vote: the overview page's national table (Democratic, Republican and every vote), once editors fill it in.
The House popular vote by party (data/results/popular_vote.json, the pages' headline): every row of that national table once
it is filled in; until then the sum of the state House pages' infoboxes (each state's Democratic and Republican vote, its
total being the largest party's vote over its percentage), with the states whose infobox is still empty named.
Rows read here are status "called" (a winner is ticked) or "counting" (a count, no call); "final" and "certified" come only
from official returns and are never overwritten. A count entered from another tally (source not Wikipedia: a news count with
its reported share counted) keeps its numbers; only a call is added to it. The two sides are the results
file's challenger and republican, the forecast's names unless corrected there (Louisiana's incumbents, missed by the forecast).
Louisiana's House races were one ballot of every candidate in 2026 (a December runoff below 50 %): a side's count there is its
party's total, the model's party margin.
"""

from __future__ import annotations

import json
import re
import time

import lxml.html
import pandas as pd

from . import scorecard as SC, wiki_polls as W
from .data_prep import _ST
from .names import clean_text
from .wiki_inputs import HOUSE_PAGE, SENATE_PAGE, _seat

GOV_PAGE = "2026 United States gubernatorial elections"
SOURCE = "Wikipedia"
NAME = {v: k for k, v in _ST.items()}
AT_LARGE = {"AK", "DE", "ND", "SD", "VT", "WY"}
ALL_CANDIDATE = {"LA"}  # House races with every candidate on one November ballot
TICK = "img[contains(@alt, 'tick') or contains(@alt, 'check')]"
COUNT_COLS = ["chal_votes", "rep_votes", "total_votes"]
OUTSIDE = SC.ROOT / "data" / "results" / "outside_calls.csv"  # office, seat, winner, source, called_at
POPULAR = SC.ROOT / "data" / "results" / "popular_vote.json"  # the House popular vote by party (results_page.py)
NAT_ROWS = {  # the national table's rows: the start of the row's name -> the party's name here
    "Democratic": "Democratic",
    "Republican": "Republican",
    "Independent": "Independent",
    "Libertarian": "Libertarian",
    "Green": "Green",
    "Constitution": "Constitution",
    "Other": "Other parties",
    "Write-in": "Write-ins",
    "Total": "Total",
}


def _text(el) -> str:
    for hidden in el.xpath(".//*[contains(translate(@style, ' ', ''), 'display:none')]"):
        hidden.drop_tree()  # the tick's hidden "Y" sort key
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", "", clean_text(el.text_content()))).strip()


def _int(s):
    s = re.sub(r"[,\s]", "", str(s))
    return int(s) if s.isdigit() else None


def party_code(party: str) -> str:
    p = party.strip()
    if p.startswith(("Democratic", "DFL")):
        return "D"
    if p.startswith("Republican"):
        return "R"
    return "I" if p.startswith(("Independent", "Nonpartisan")) else "O"


def overview_rows(html: str):
    """(first cell, result cell, [(name, party, ticked)]) for every table row whose last cell lists candidates."""
    for tr in lxml.html.fromstring(html).xpath("//table[contains(@class, 'wikitable')]//tr"):
        cells = tr.xpath("./th|./td")
        lis = cells[-1].xpath(".//li") if len(cells) >= 3 else []
        cands = []
        for li in lis:
            ticked = bool(li.xpath(".//" + TICK))
            m = re.match(r"^(.*?)\s*\(([^()]*)\)$", _text(li).lstrip("▌ "))
            if m:
                cands.append((m.group(1).strip(), m.group(2).strip(), ticked))
        if cands:
            yield _text(cells[0]), _text(cells[-2]), cands


def calls(office: str, html: str) -> dict:
    """seat -> {"winner": party code or None, "name": the ticked candidate, "result": the result cell, "cands": [...]}."""
    out = {}
    for first, result, cands in overview_rows(html):
        seat = _seat(first) if office == "house" else _ST.get(re.sub(r"\s*\(.*?\)", "", first).strip())
        if not seat:
            continue
        won = [c for c in cands if c[2]]
        row = {
            "winner": party_code(won[0][1]) if len(won) == 1 else None,
            "name": won[0][0] if len(won) == 1 else None,
            "result": result,
            "cands": cands,
        }
        if seat not in out or (row["winner"] and not out[seat]["winner"]):  # a race listed twice: keep the called row
            out[seat] = row
    return out


def boxes(html: str):
    """General-election result boxes: (caption, [(party, candidate, votes or None)], total votes or None)."""
    for t in lxml.html.fromstring(html).xpath("//table[contains(@class, 'wikitable')][caption]"):
        cap = _text(t.xpath("./caption")[0])
        if re.search(r"primary|runoff|convention|caucus|first round|final round", cap, re.I):
            continue
        rows, total = [], None
        for tr in t.xpath(".//tr"):
            cells = [_text(c) for c in tr.xpath("./th|./td")]
            if cells and cells[0].startswith("Total votes"):
                total = _int(cells[1]) if len(cells) > 1 else None
            elif len(cells) >= 4 and cells[0] == "" and cells[1] and cells[2]:  # colour | party | candidate | votes | %
                rows.append((cells[1], re.sub(r"\s*\(.*?\)", "", cells[2]).strip(), _int(cells[3])))
        if rows:
            yield cap, rows, total


def count(bx: list, challenger: str, republican: str, parties: tuple | None = None):
    """(challenger votes, Republican votes, total) from the last of a page's boxes naming both, or None while it is not
    filled in. With `parties` (the challenger's party code, "R"), every line of a side's party counts for that side."""
    sc, sr = W._surname(challenger), W._surname(republican)
    found = None
    for _cap, rows, total in bx:
        names = {W._surname(n) for _p, n, _v in rows}
        if sc in names and sr in names:
            found = (rows, total)
    if not found:
        return None
    rows, total = found
    if not total or any(v is None for _p, _n, v in rows):
        return None

    def side(surname, party):  # a candidate on several lines (fusion) is summed
        return sum(v for p, n, v in rows if W._surname(n) == surname or (parties and party_code(p) == party))

    c, r = side(sc, parties and parties[0]), side(sr, parties and parties[1])
    return (c, r, total) if 0 < c + r <= total else None


def national(html: str):
    """{party: votes} with "Total" from the overview page's national table (Parties | Popular vote | Seats), a row whose vote
    cell is empty left out, or None while the Democratic, Republican or total cell is empty or the parties exceed the total."""
    for t in lxml.html.fromstring(html).xpath("//table[contains(@class, 'wikitable')]"):
        trs = t.xpath(".//tr")
        head = _text(trs[0]) if trs else ""
        if "Parties" not in head or "Popular vote" not in head:
            continue
        got = {}
        for tr in trs:
            cells = [_text(c) for c in tr.xpath("./th|./td")]
            for i, c in enumerate(cells[:-1]):
                name = next((v for k, v in NAT_ROWS.items() if c.startswith(k)), None)
                if name:
                    if (v := _int(cells[i + 1])) is not None:  # the vote is the cell after the party's name
                        got[name] = v
                    break
        d, r, total = got.get("Democratic"), got.get("Republican"), got.get("Total")
        if d and r and total and sum(v for k, v in got.items() if k != "Total") <= total:
            return got
    return None


def state_vote(html: str):
    """(Democratic, Republican, total) House vote from a state House page's first infobox (its Party, Popular vote and
    Percentage rows; an at-large race's candidates count as their parties, a ranked-choice race's first round as its vote),
    the total being the largest party's vote over its percentage; None while the vote rows are empty or a party's percentage disagrees with its vote over that total."""
    box = lxml.html.fromstring(html).xpath("(//table[contains(@class, 'infobox')])[1]")
    rows = {"Party": [], "Popular vote": [], "Percentage": []}
    for tr in box[0].xpath(".//tr") if box else []:
        tds = tr.xpath("./th|./td")
        cells = [_text(c) for c in tds]
        if cells and cells[0] in rows:
            rows[cells[0]] += cells[1:]
        elif (
            cells and cells[0].replace(" ", "") == "Firstround"
        ):  # ranked choice: each cell is a vote over a percentage
            for td in tds[1:]:
                m = re.fullmatch(r"([\d,]+) (\S+)", re.sub(r"\s+", " ", " ".join(td.itertext())).strip())
                rows["Popular vote"].append(m[1] if m else "")
                rows["Percentage"].append(m[2] if m else "")
    party, votes = rows["Party"], [_int(v) for v in rows["Popular vote"]]
    pct = [re.fullmatch(r"(\d+(?:\.(\d+))?) ?%", p) for p in rows["Percentage"]]
    if not party or not len(party) == len(votes) == len(pct) or None in votes or None in pct:
        return None
    share = [float(m[1]) for m in pct]
    tol = 10.0 ** -min(len(m[2] or "") for m in pct)  # the percentages' rounding: one unit of their last digit
    top = max(range(len(votes)), key=votes.__getitem__)
    total = max(round(100 * votes[top] / share[top]), sum(votes)) if share[top] else 0
    if not total or any(abs(100 * v / total - s) > tol for v, s in zip(votes, share)):
        return None
    side = {"D": 0, "R": 0}
    for p, v in zip(party, votes):
        if party_code(p) in side:
            side[party_code(p)] += v
    return side["D"], side["R"], total


def popular_vote(nat: dict | None, states: dict) -> dict:
    """The House popular vote by party: every row of the national table once it is filled in (the total less the rows
    still empty as "Not listed"), else the states' sum (Democratic, Republican, everyone else) with the states missing."""
    if nat:
        parties = [[k, v] for k, v in nat.items() if k != "Total"]
        rest = nat["Total"] - sum(v for _k, v in parties)  # rows whose vote cell is still empty
        return {
            "source": "national",
            "parties": [*parties, *([["Not listed", rest]] if rest else [])],
            "total": nat["Total"],
        }
    have = {st: v for st, v in states.items() if v}
    d, r, total = (sum(v[i] for v in have.values()) for i in range(3))
    return {
        "source": "states",
        "parties": [["Democratic", d], ["Republican", r], ["Others", total - d - r]],
        "total": total,
        "states": sorted(have),
        "missing": sorted(st for st, v in states.items() if not v),
    }


def outside_calls(called: dict, rows: list[dict]) -> list[str]:
    """Add the hand-entered calls (in place) to races Wikipedia has not called; returns the disagreements."""
    clash = []
    for r in rows:
        key, win = (r["office"], r["seat"]), r["winner"].strip()
        have = (called.get(key) or {}).get("winner")
        if have and have != win:
            clash.append(f"{key[0]} {key[1]}: Wikipedia {have}, {r['source']} {win}")
        elif not have:
            called[key] = {"winner": win, "name": None, "by": r["source"]}
    return clash


def race_page(office: str, seat: str, special: bool) -> str:
    st = seat.split("-")[0]
    if office == "senate":
        return f"2026 United States Senate {'special ' if special else ''}election in {NAME[st]}"
    if office == "governor":
        return f"2026 {NAME[st]} gubernatorial election"
    return f"2026 United States House of Representatives election{'' if st in AT_LARGE else 's'} in {NAME[st]}"


def update(res: pd.DataFrame, forecast: dict, called: dict, counts: dict, now: str) -> list[str]:
    """Write the calls and counts into the results rows (in place; counts may hold ("national", "US")); returns one line per
    row that changed."""
    races = {(f["office"], f["seat"]) for f in forecast["races"]} | {("national", "US")}
    moved = []
    for i, r in res.iterrows():
        key = (r["office"], r["seat"])
        if key not in races or r["status"] in SC.COMPLETE:  # official returns stay as they are
            continue
        hand = r["source"] not in ("", SOURCE)  # a count from another tally: its numbers and note stay, a call is added
        c, n = called.get(key, {}), counts.get(key)
        if hand:
            win = c.get("winner") or r["winner"]
            new = {"winner": win, "status": "called" if win else r["status"]}
        else:
            new = {
                "winner": c.get("winner") or "",
                "status": "called" if c.get("winner") else ("counting" if n else ""),
            }
            new.update(zip(COUNT_COLS, [str(v) for v in n] if n else ["", "", ""]))
            sides = {W._surname(r["challenger"]), W._surname(r["republican"])}
            stray = c.get("name") and W._surname(c["name"]) not in sides
            new["note"] = f"winner {c['name']} is neither forecast side" if stray else ""
            if c.get("by"):
                new["note"] = f"called by {c['by']}"
        if all(r[k] == v for k, v in new.items()):
            continue
        was, now_ = f"{r['winner'] or '-'}/{r['status'] or '-'}", f"{new['winner'] or '-'}/{new['status'] or '-'}"
        moved.append(f"{key[0]:8s} {key[1]:6s} {was} -> {now_}")
        for k, v in new.items():
            res.at[i, k] = v
        if not hand:
            res.at[i, "source"] = SOURCE if (new["winner"] or n) else ""
        res.at[i, "updated"] = now if (new["winner"] or n or hand) else ""
    return moved


def main():
    forecast, _frozen = SC.load_forecast()
    res = pd.read_csv(SC.RESULTS, dtype=str, keep_default_na=False)[SC.COLS]
    called, overview = {}, {}
    for office, page in (("house", HOUSE_PAGE), ("senate", SENATE_PAGE), ("governor", GOV_PAGE)):
        overview[office] = W.fetch(page, max_age_h=0)
        for seat, c in calls(office, overview[office]).items():
            called[(office, seat)] = c
    if OUTSIDE.exists():
        for line in outside_calls(called, pd.read_csv(OUTSIDE, dtype=str, keep_default_na=False).to_dict("records")):
            print("  calls disagree: " + line)
    nat = national(overview["house"])
    counts = {("national", "US"): (nat["Democratic"], nat["Republican"], nat["Total"])} if nat else {}
    html, parsed = {}, {}

    # every page once (a state's House page serves all its districts); one that does not exist reads as blank
    def read(title):
        if title not in html:
            try:
                html[title] = W.fetch(title, max_age_h=0)
            except Exception as e:
                print(f"  {title}: {str(e)[:80]}")
                html[title] = ""
        return html[title]

    house = sorted({f["seat"].split("-")[0] for f in forecast["races"] if f["office"] == "house"})
    states = {} if nat else {st: state_vote(read(race_page("house", st, False))) for st in house}
    names = {(r.office, r.seat): (r.challenger, r.republican) for r in res.itertuples()}
    for f in forecast["races"]:
        if f.get("rcv"):
            continue
        title = race_page(f["office"], f["seat"], f.get("special", False))
        if title not in parsed:
            parsed[title] = list(boxes(read(title))) if read(title) else []
        key = (f["office"], f["seat"])
        jungle = f["office"] == "house" and f["seat"].split("-")[0] in ALL_CANDIDATE
        n = count(
            parsed[title],
            *names.get(key, (f["challenger"], f["republican"])),
            (f["challenger_party"], "R") if jungle else None,
        )
        if n:
            counts[key] = n
    now = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    moved = update(res, forecast, called, counts, now)
    res.to_csv(SC.RESULTS, index=False)
    print(
        f"{sum(1 for c in called.values() if c['winner'])} races called, {len(counts) - bool(nat)} with a count; {len(moved)} rows changed"
    )
    print(
        "national House vote: "
        + ("D {Democratic}, R {Republican} of {Total}".format(**nat) if nat else "not filled in yet")
    )
    pop = popular_vote(nat, states)
    old = json.loads(POPULAR.read_text()) if POPULAR.exists() else {}
    if {**old, "as_of": None} != {**pop, "as_of": None}:  # a run that changes nothing keeps the file and its time
        POPULAR.write_text(json.dumps({**pop, "as_of": now}, indent=1) + "\n")
    print(
        "popular vote ({}): ".format(pop["source"] if nat else f"{len(pop['states'])} of {len(states)} states")
        + ", ".join(f"{k} {v:,}" for k, v in pop["parties"])
        + f" of {pop['total']:,}"
        + (f"; no infobox count yet: {' '.join(pop['missing'])}" if pop.get("missing") else "")
    )
    for line in moved:
        print("  " + line)


if __name__ == "__main__":
    main()
