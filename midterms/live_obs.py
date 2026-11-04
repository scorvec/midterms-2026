"""The calls and counts the remaining-races model (live/remaining.js) reweights the forecast's simulations by.

    python -m midterms.live_obs        # data/results/results_2026.csv -> data/cache/live_obs.json

Calls: a race with a winner is called for the side that won (the Republican's opponent, or the Republican - CA-6's
independent incumbent in its place); a winner of neither side is left out. Counts: every race with a count, called or not,
in the model's unit (each side's share of every ballot, as scorecard.py scores it); ranked-choice races carry no count until
their final round is official. The share counted is the results file's own when it has one (official returns, or a news count
with its reported share of the expected vote); otherwise it is estimated (est) from the race's 2024 presidential vote (D + R,
on the 2026 map), scaled by TURNOUT, and capped at F_CAP, since a Wikipedia box does not say how much of the vote is in. States that count most ballots after Election Day get extra count
noise (LATE_SD): their late ballots can run well apart from the early count either way.
"""

from __future__ import annotations

import json

import pandas as pd

from . import scorecard as SC
from .paths import STATIC

OUT = SC.ROOT / "data" / "cache" / "live_obs.json"
TURNOUT = 0.80  # 2026 votes per 2024 presidential vote: 2018 ran 0.90 and 2022 0.71 of the House vote two years before
F_CAP = 0.95  # never treat an estimated count as complete
LATE = {"AK", "AZ", "CA", "CO", "NV", "OR", "UT", "WA"}  # most ballots by mail, counted for days after Election Day
LATE_SD = 3.0  # points, added in quadrature to the count error in those states
KEY = {"house": "H", "senate": "S", "governor": "G"}


def _num(x):
    v = pd.to_numeric(x, errors="coerce")
    return None if pd.isna(v) else float(v)


def pres24() -> dict:
    """seat or state -> 2024 presidential votes (D + R) on the 2026 map."""
    cd = pd.read_csv(STATIC / "cd_lean_2026.csv")
    votes = cd.pres24_d + cd.pres24_r
    out = dict(zip(cd.seat, votes))
    out.update(votes.groupby(cd.seat.str.split("-").str[0]).sum().to_dict())
    return out


def observations(doc: dict, res: pd.DataFrame, base: dict) -> list[dict]:
    by = {(r["office"], str(r["seat"])): r for r in res.to_dict("records")}
    obs = []
    for f in doc["races"]:
        r = by.get((f["office"], str(f["seat"])))
        if r is None:
            continue
        o = {"race": f"{KEY[f['office']]}:{f['seat']}"}
        win = r["winner"].strip()
        if win == f["challenger_party"]:
            o["call"] = "D"
        elif win and win == (f.get("opponent_party") or "R"):
            o["call"] = "R"
        c, rv, t = (_num(r.get(k)) for k in ("chal_votes", "rep_votes", "total_votes"))
        if not f.get("rcv") and c is not None and rv is not None and t:
            pct = _num(r.get("pct_counted"))
            st = f["seat"].split("-")[0]
            share = pct / 100 if pct is not None else min(F_CAP, t / (TURNOUT * base[f["seat"]]))
            o |= {"d": round(100 * c / t, 2), "r": round(100 * rv / t, 2), "pct": round(100 * share, 1)}
            if pct is None:
                o["est"] = True
            if st in LATE:
                o["skew_sd"] = LATE_SD
        if len(o) > 1:
            obs.append(o)
    return obs


def main():
    doc, _frozen = SC.load_forecast()
    res = pd.read_csv(SC.RESULTS, dtype=str, keep_default_na=False)  # blank cells stay blank
    obs = observations(doc, res, pres24())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(obs, separators=(",", ":")))
    n_call = sum("call" in o for o in obs)
    print(f"{OUT.relative_to(SC.ROOT)}: {n_call} calls, {sum('pct' in o for o in obs)} counts")


if __name__ == "__main__":
    main()
