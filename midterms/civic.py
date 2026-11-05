"""The House popular vote by party, the pages' headline, and the newest counts, from civicAPI.

    python -m midterms.civic        # -> data/results/popular_vote.json; newer counts into data/results/results_2026.csv

civicAPI (https://civicapi.org, free for any use with attribution) keeps national roll-ups of the House and Senate votes,
with each district's and state's count (region results). The popular vote: every party in the House roll-up with at least
MIN_SHARE of the vote by name, the rest summed as "Others". The counts: a race's civicAPI count replaces the results file's
when it has more votes (the newer count; results_wiki.py takes Wikipedia's back once that is newer still). The two sides are
the results file's candidates by surname (Louisiana's one-ballot House races: their parties' totals), the total every
candidate's vote. civicAPI's "percent reporting" is never used as the share counted (it reads 100 in states still counting
their mail ballots): a count with a reported share keeps that share, scaled by the votes added since (its base is kept in
the note); any other count's share is estimated by live_obs.py. Ranked-choice races and official returns are left alone.
Governors have no roll-up here. A failed read keeps the last file and counts.
"""

from __future__ import annotations

import json
import re
import time

import pandas as pd

from . import fetch as F, scorecard as SC, wiki_polls as W
from .results_wiki import ALL_CANDIDATE, CIVIC as SOURCE, COUNT_COLS, party_code

HOUSE, SENATE = 87524, 87528  # civicAPI's national House of Representatives and Senate roll-ups
API = "https://civicapi.org/api/v2/race/{}"
OUT = SC.ROOT / "data" / "results" / "popular_vote.json"
MIN_SHARE = 0.0005  # parties below a twentieth of a percent of the vote are summed as "Others"
BASE = re.compile(r"share counted: ([\d.]+) % at ([\d,]+) votes")  # a reported share's base, kept in the note


def tally(race: dict) -> dict:
    """{source, url, parties: [[party, votes]] largest first with "Others" last, total, as_of} from a civicAPI race."""
    votes = {}
    for c in race["candidates"]:
        votes[c["party"]] = votes.get(c["party"], 0) + int(c["votes"] or 0)
    total = sum(votes.values())
    named = sorted(((p, v) for p, v in votes.items() if v >= MIN_SHARE * total), key=lambda x: -x[1])
    parties = [[p, v] for p, v in named]
    if rest := total - sum(v for _p, v in named):
        parties.append(["Others", rest])
    return {
        "source": SOURCE,
        "url": "https://civicapi.org",
        "parties": parties,
        "total": total,
        "as_of": race["last_updated"][:16] + "Z",
    }


def seat(office: str, code: str) -> str:
    """Our seat from a region code: "al-01" -> "AL-1", "ak-al" (at large) -> "AK-1"; a Senate region is its state."""
    st, _, d = code.partition("-")
    if office == "senate":
        return st.upper()
    return f"{st.upper()}-{1 if d == 'al' else int(d)}"


def sides(region: dict, challenger: str, republican: str, party: str | None = None):
    """(challenger, Republican, total) votes from a civicAPI region, or None unless both sides are on it. With `party`
    (the challenger's party code), each side is its party's total."""
    cands = [(c["name"], party_code(c["party"] or ""), int(c["votes"] or 0)) for c in region["candidates"]]
    total = sum(v for _n, _p, v in cands)
    if party:
        c, r = (sum(v for _n, p, v in cands if p == code) for code in (party, "R"))
    else:
        c, r = (sum(v for n, _p, v in cands if W._surname(n) == W._surname(who)) for who in (challenger, republican))
    return (c, r, total) if c and r and c + r <= total else None


def newer_counts(res: pd.DataFrame, regions: dict, doc: dict, now: str) -> list[str]:
    """Put civicAPI's count into every results row it is newer for (in place); returns one line per row changed."""
    fc = {(f["office"], f["seat"]): f for f in doc["races"]}
    moved = []
    for i, r in res.iterrows():
        f, reg = fc.get((r["office"], r["seat"])), regions.get((r["office"], r["seat"]))
        if f is None or reg is None or f.get("rcv") or r["status"] in SC.COMPLETE:
            continue
        jungle = r["office"] == "house" and r["seat"].split("-")[0] in ALL_CANDIDATE
        n = sides(reg, r["challenger"], r["republican"], f["challenger_party"] if jungle else None)
        old = int(r["total_votes"]) if r["total_votes"] else 0
        if not n or n[2] <= old:
            continue
        note, m = "", BASE.search(r["note"])
        if m or r["pct_counted"]:  # a reported share: scaled by the votes added since the count it came with
            pct, base = (float(m[1]), int(m[2].replace(",", ""))) if m else (float(r["pct_counted"]), old)
            res.at[i, "pct_counted"] = f"{min(100.0, pct * n[2] / base):.1f}"
            note = (
                r["note"]
                if m
                else f"share counted: {pct:g} % at {base:,} votes ({r['source']}), scaled by the votes since"
            )
        for k, v in zip(COUNT_COLS, n):
            res.at[i, k] = str(v)
        res.at[i, "source"], res.at[i, "updated"], res.at[i, "note"] = SOURCE, now, note
        res.at[i, "status"] = r["status"] or "counting"
        moved.append(
            f"{r['office']:8s} {r['seat']:6s} {old:,} -> {n[2]:,} votes (margin {100 * (n[0] - n[1]) / n[2]:+.2f})"
        )
    return moved


def main():
    house = json.loads(F.open_url(API.format(HOUSE), timeout=60))
    if house.get("election_type") != "House of Representatives" or house.get("province") != "US":
        raise SystemExit(f"civicAPI race {HOUSE} is not the national House tally: {house.get('election_name')}")
    pop = tally(house)
    d, r = (dict(pop["parties"]).get(k, 0) for k in ("Democratic", "Republican"))
    if not (d and r and d + r <= pop["total"]):
        raise SystemExit(f"civicAPI national House tally lacks a Democratic or Republican vote: {pop['parties'][:3]}")
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if old != pop:
        OUT.write_text(json.dumps(pop, indent=1) + "\n")
    print("popular vote (civicAPI, {as_of}): ".format(**pop) + ", ".join(f"{k} {v:,}" for k, v in pop["parties"]))

    senate = json.loads(F.open_url(API.format(SENATE), timeout=60))
    regions = {("house", seat("house", k)): v for k, v in house.get("region_results", {}).items()}
    regions |= {("senate", seat("senate", k)): v for k, v in senate.get("region_results", {}).items()}
    doc, _frozen = SC.load_forecast()
    res = pd.read_csv(SC.RESULTS, dtype=str, keep_default_na=False)[SC.COLS]
    moved = newer_counts(res, regions, doc, time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()))
    if moved:
        res.to_csv(SC.RESULTS, index=False, lineterminator="\n")
    print(f"civicAPI counts: {len(moved)} newer than the results file's")
    for line in moved:
        print("  " + line)


if __name__ == "__main__":
    main()
