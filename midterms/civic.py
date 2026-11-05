"""The House popular vote by party, the pages' headline, from civicAPI's national House tally.

    python -m midterms.civic        # -> data/results/popular_vote.json (results_page.py carries it into results.json)

civicAPI (https://civicapi.org, free for any use with attribution) keeps a national roll-up of the House vote by party
(race RACE). Every party with at least MIN_SHARE of the vote is kept by name; the rest are summed as "Others". The count is
the votes counted so far: its "percent reporting" is not used (it reads 100 in states still counting their mail ballots).
A failed read keeps the last file.
"""

from __future__ import annotations

import json

from . import fetch as F, scorecard as SC

RACE = 87524  # civicAPI's national House of Representatives roll-up
URL = f"https://civicapi.org/api/v2/race/{RACE}"
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


if __name__ == "__main__":
    main()
