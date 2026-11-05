"""The House popular vote by party, the pages' headline, from civicAPI's national House tally.

    python -m midterms.civic        # -> data/results/popular_vote.json (results_page.py carries it into results.json)

civicAPI (https://civicapi.org, free for any use with attribution) keeps a national roll-up of the House vote by party
(race RACE). Every party with at least MIN_SHARE of the vote is kept by name; the rest are summed as "Others". The count is
the votes counted so far: its "percent reporting" is not used (it reads 100 in states still counting their mail ballots).
A failed read keeps the last file. Each run also sets civicAPI's district and state counts (the roll-ups' region results)
beside every count in the results file, to check the two agree.
"""

from __future__ import annotations

import json

import pandas as pd

from . import fetch as F, scorecard as SC, wiki_polls as W

RACE = 87524  # civicAPI's national House of Representatives roll-up
SENATE = 87528  # and its Senate roll-up
API = "https://civicapi.org/api/v2/race/{}"
URL = API.format(RACE)
OUT = SC.ROOT / "data" / "results" / "popular_vote.json"
MIN_SHARE = 0.0005  # parties below a twentieth of a percent of the vote are summed as "Others"


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
        "source": "civicAPI",
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


def compare(regions: dict, res: pd.DataFrame) -> list[str]:
    """One line per race with a count in the results file: ours against civicAPI's (the two sides by surname, the total
    every candidate's vote), with the difference in margin (points of every ballot) and civicAPI's total over ours."""
    out = []
    for r in res[res.total_votes != ""].itertuples():
        reg = regions.get((r.office, r.seat))
        if reg is None:
            continue
        cands = reg["candidates"]
        side = [
            sum(c["votes"] or 0 for c in cands if W._surname(c["name"]) == W._surname(n))
            for n in (r.challenger, r.republican)
        ]
        tot = sum(c["votes"] or 0 for c in cands)
        c, rv, t = int(r.chal_votes), int(r.rep_votes), int(r.total_votes)
        if not (tot and all(side)):
            out.append(f"{r.office:8s} {r.seat:6s} civicAPI has no count naming {r.challenger} and {r.republican}")
            continue
        dm = 100 * (side[0] - side[1]) / tot - 100 * (c - rv) / t
        out.append(
            f"{r.office:8s} {r.seat:6s} ours {c:>9,} {rv:>9,} of {t:>10,} ({r.source[:20]}); civicAPI {side[0]:>9,} {side[1]:>9,}"
            f" of {tot:>10,} at {reg.get('percent_reporting')} % reporting; margin {dm:+.2f}, total x{tot / t:.3f}"
        )
    return out


def main():
    race = json.loads(F.open_url(URL, timeout=60))
    if race.get("election_type") != "House of Representatives" or race.get("province") != "US":
        raise SystemExit(f"civicAPI race {RACE} is not the national House tally: {race.get('election_name')}")
    pop = tally(race)
    d, r = (dict(pop["parties"]).get(k, 0) for k in ("Democratic", "Republican"))
    if not (d and r and d + r <= pop["total"]):
        raise SystemExit(f"civicAPI national House tally lacks a Democratic or Republican vote: {pop['parties'][:3]}")
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if old != pop:
        OUT.write_text(json.dumps(pop, indent=1) + "\n")
    print("popular vote (civicAPI, {as_of}): ".format(**pop) + ", ".join(f"{k} {v:,}" for k, v in pop["parties"]))
    senate = json.loads(F.open_url(API.format(SENATE), timeout=60))
    print("civicAPI Senate regions:", " ".join(sorted(senate.get("region_results", {}))))
    regions = {("house", seat("house", k)): v for k, v in race.get("region_results", {}).items()}
    regions |= {("senate", seat("senate", k)): v for k, v in senate.get("region_results", {}).items()}
    for line in compare(regions, pd.read_csv(SC.RESULTS, dtype=str, keep_default_na=False)):
        print("  " + line)


if __name__ == "__main__":
    main()
