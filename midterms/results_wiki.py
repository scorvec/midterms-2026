"""Results of the 2026 races from Wikipedia into data/results/results_2026.csv, which the scorecard reads (scorecard.py).

    python -m midterms.results_wiki        # check every results page for edits, update the results file, print what moved

Calls: the winner ticked on the three overview pages (House, Senate, governors: one row per race, its result cell reads
"Democratic gain", "Republican hold", ...). Counts: the general-election box on each race's own page (the House: one page
per state, one box per district) once editors fill it in; an empty or half-filled box leaves the counts blank. Ranked-choice
races (Alaska, Maine) get no count here: their boxes carry rounds, and the scorer needs the final round from official returns.
Rows read here are status "called" (a winner is ticked) or "counting" (a count, no call); "final" and "certified" come only
from official returns, and a row whose source is anything but Wikipedia is never overwritten. The two sides are the results
file's challenger and republican, the forecast's names unless corrected there (Louisiana's incumbents, missed by the forecast).
Louisiana's House races were one ballot of every candidate in 2026 (a December runoff below 50 %): a side's count there is its
party's total, the model's party margin.
"""

from __future__ import annotations

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


def race_page(office: str, seat: str, special: bool) -> str:
    st = seat.split("-")[0]
    if office == "senate":
        return f"2026 United States Senate {'special ' if special else ''}election in {NAME[st]}"
    if office == "governor":
        return f"2026 {NAME[st]} gubernatorial election"
    return f"2026 United States House of Representatives election{'' if st in AT_LARGE else 's'} in {NAME[st]}"


def update(res: pd.DataFrame, forecast: dict, called: dict, counts: dict, now: str) -> list[str]:
    """Write the calls and counts into the results rows (in place); returns one line per row that changed."""
    races = {(f["office"], f["seat"]) for f in forecast["races"]}
    moved = []
    for i, r in res.iterrows():
        key = (r["office"], r["seat"])
        if key not in races or r["source"] not in ("", SOURCE):  # official returns and hand entries stay as they are
            continue
        c, n = called.get(key, {}), counts.get(key)
        new = {"winner": c.get("winner") or "", "status": "called" if c.get("winner") else ("counting" if n else "")}
        new.update(zip(COUNT_COLS, [str(v) for v in n] if n else ["", "", ""]))
        note = ""
        sides = {W._surname(r["challenger"]), W._surname(r["republican"])}
        if c.get("name") and W._surname(c["name"]) not in sides:
            note = f"winner {c['name']} is neither forecast side"
        new["note"] = note
        if all(r[k] == v for k, v in new.items()):
            continue
        was, now_ = f"{r['winner'] or '-'}/{r['status'] or '-'}", f"{new['winner'] or '-'}/{new['status'] or '-'}"
        moved.append(f"{key[0]:8s} {key[1]:6s} {was} -> {now_}")
        for k, v in new.items():
            res.at[i, k] = v
        res.at[i, "source"] = SOURCE if (new["winner"] or n) else ""
        res.at[i, "updated"] = now if (new["winner"] or n) else ""
    return moved


def main():
    forecast, _frozen = SC.load_forecast()
    res = pd.read_csv(SC.RESULTS, dtype=str, keep_default_na=False)[SC.COLS]
    called = {}
    for office, page in (("house", HOUSE_PAGE), ("senate", SENATE_PAGE), ("governor", GOV_PAGE)):
        for seat, c in calls(office, W.fetch(page, max_age_h=0)).items():
            called[(office, seat)] = c
    counts, pages = {}, {}
    names = {(r.office, r.seat): (r.challenger, r.republican) for r in res.itertuples()}
    for f in forecast["races"]:
        if f.get("rcv"):
            continue
        title = race_page(f["office"], f["seat"], f.get("special", False))
        if title not in pages:  # a state's House page serves all its districts
            try:
                pages[title] = list(boxes(W.fetch(title, max_age_h=0)))
            except Exception as e:  # a page that does not exist (yet) leaves its counts blank
                print(f"  {title}: {str(e)[:80]}")
                pages[title] = []
        key = (f["office"], f["seat"])
        jungle = f["office"] == "house" and f["seat"].split("-")[0] in ALL_CANDIDATE
        n = count(
            pages[title],
            *names.get(key, (f["challenger"], f["republican"])),
            (f["challenger_party"], "R") if jungle else None,
        )
        if n:
            counts[key] = n
    moved = update(res, forecast, called, counts, time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()))
    res.to_csv(SC.RESULTS, index=False)
    print(
        f"{sum(1 for c in called.values() if c['winner'])} races called, {len(counts)} with a count; {len(moved)} rows changed"
    )
    for line in moved:
        print("  " + line)


if __name__ == "__main__":
    main()
