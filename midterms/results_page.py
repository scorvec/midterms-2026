"""The results page's data: every race's frozen forecast next to its result, the chamber counts and the scorecard.

    python -m midterms.results_page        # -> web/data/results.json (web/results.html), results_summary.json (homepage)

Per race: the forecast (p = the Republican's opponent wins, the median margin and its 80 % range) and the result (the call,
the status and the margin of the latest count). A margin is "final" only once the count is final or certified (scorecard.py
scores those); before that it is the latest count's, shown as provisional and never scored. The national House vote: the
count so far (the results file's national row), the forecast's and every House election's since 1946, all as the model's
two-party margin (data_prep.national_house_vote). The House popular vote by party, the pages' headline, is
data/results/popular_vote.json as civic.py wrote it (civicAPI's national tally), which also stands in for the national
count until the results file's national row has one.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from . import scorecard as SC
from .data_prep import national_house_vote

OUT = SC.ROOT / "web" / "data" / "results.json"
LIVE = SC.ROOT / "web" / "data" / "live.json"  # the races still out (live/remaining.js): the homepage's median outcome
SUMMARY = SC.ROOT / "web" / "data" / "results_summary.json"  # the scorvec.com homepage panel, under a kilobyte
POPULAR = SC.ROOT / "data" / "results" / "popular_vote.json"  # the House popular vote by party (civic.py)


def _num(x):
    v = pd.to_numeric(x, errors="coerce")
    return None if v != v else float(v)


def _str(x) -> str:
    return "" if x is None or x != x else str(x).strip()  # an empty CSV cell reads as NaN


def margin(row) -> float | None:
    c, r, t = (_num(row.get(k)) for k in ("chal_votes", "rep_votes", "total_votes"))
    return round(100 * (c - r) / t, 2) if c is not None and r is not None and t else None


def spread(hist: dict) -> dict:
    """mean, median and 80 % range of a simulated seat count {seats: draws}."""
    k = np.array([int(s) for s in hist], float)
    w = np.array(list(hist.values()), float)
    o = np.argsort(k)
    k, c = k[o], np.cumsum(w[o]) / w.sum()
    q = [float(k[np.searchsorted(c, p)]) for p in (0.1, 0.5, 0.9)]
    return {"mean": round(float((k * w[o]).sum() / w.sum()), 1), "p10": q[0], "p50": q[1], "p90": q[2]}


def national(doc: dict, res: pd.DataFrame, pop: dict | None) -> dict:
    """The national House vote: the count so far (votes, shares of every vote, two-party margin, its source: the results
    file's national row once it is official or holds at least the popular vote's total, else the popular vote's tally),
    the forecast's margin with its 80 % range, and [year, margin] for every House election since 1946."""
    r = next((x for x in res.to_dict("records") if x["office"] == "national"), {})
    d, rv, t = (_num(r.get(k)) for k in ("chal_votes", "rep_votes", "total_votes"))
    source, final = _str(r.get("source")), _str(r.get("status")) in SC.COMPLETE
    # Wikipedia's national table fills in state by state: a partial row gives way to the fuller tally
    if pop and not final and not (d and rv and t and t >= pop["total"]):
        by = dict(pop["parties"])
        d, rv, t, source = by.get("Democratic"), by.get("Republican"), pop["total"], pop["source"]
    count = None
    if d and rv and t:
        count = {
            "dem": int(d),
            "rep": int(rv),
            "total": int(t),
            "dem_pct": round(100 * d / t, 2),
            "rep_pct": round(100 * rv / t, 2),
            "margin": round(100 * (d - rv) / (d + rv), 2),
            "final": final,
            "source": source,
        }
    nv = doc["chambers"]["national_vote"]
    return {
        "count": count,
        "forecast": {"center": nv["center"], "p10": nv["q"]["10"], "p90": nv["q"]["90"]},
        "history": [[int(y), round(float(m), 2)] for y, m in national_house_vote().items()],
    }


def build(doc: dict, res: pd.DataFrame) -> dict:
    by = {(r["office"], str(r["seat"])): r for r in res.to_dict("records")}
    pop = json.loads(POPULAR.read_text()) if POPULAR.exists() else None
    sc = SC.score(doc, res)
    races = []
    for f in doc["races"]:
        r = by.get((f["office"], str(f["seat"])), {})
        st = _str(r.get("status"))
        races.append(
            {
                "office": f["office"],
                "seat": f["seat"],
                "challenger": _str(r.get("challenger")) or f["challenger"],  # the results file corrects a name the
                "party": f["challenger_party"],  # forecast got wrong (Louisiana's incumbents)
                "republican": _str(r.get("republican")) or f["republican"],
                "opp": f.get("opponent_party", "R"),  # the side p is against: CA-6's incumbent is an independent
                "p": f["p"],
                "med": SC.quantile(f["q"], 0.5),
                "lo": SC.quantile(f["q"], 0.10),
                "hi": SC.quantile(f["q"], 0.90),
                "winner": _str(r.get("winner")) or None,
                "status": st or None,
                "margin": None if f.get("rcv") and st not in SC.COMPLETE else margin(r),
                "final": st in SC.COMPLETE,
            }
        )
    C = doc["chambers"]
    senate_hist = {}
    for k, v in C["senate"]["hist2"].items():
        senate_hist[k.split("|")[0]] = senate_hist.get(k.split("|")[0], 0) + v
    forecast = {
        "house": {**spread(C["house"]["hist"]), "p_control": C["house"]["p_maj"], "fixed_d": doc["house_fixed"]["D"]},
        "senate": {**spread(senate_hist), "p_control": C["senate"]["p_ctrl"], "holdover_d": C["senate"]["holdover_d"]},
        "governor": {**spread(C["governor"]["hist"]), "holdover_d": C["governor"]["holdover_d"]},
    }
    forecast["house"]["fixed_r"] = doc["house_fixed"]["R"]
    forecast["senate"]["holdover_r"] = C["senate"]["holdover_r"]
    n_gov = sum(f["office"] == "governor" for f in doc["races"])
    forecast["governor"]["holdover_r"] = 50 - n_gov - C["governor"]["holdover_d"]
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "frozen": doc["frozen"],
        "forecast": forecast,
        "scores": sc["by_office"],
        "chambers": sc["chambers"],
        "national": national(doc, res, pop),
        "popular_vote": pop,
        "races": races,
    }


def summary(out: dict) -> dict:
    """Each chamber's seats as called, one character per seat in the homepage arcs' order (most Democratic first): 0 = a
    Democratic win, a = an independent's, 3 = not called, 6 = a Republican win, D / R = a Senate seat not up this year;
    the median outcome of the races still out (Democratic, Republican seats) and of the national House vote (the
    two-party margin) when live.json has them; and the check of the forecast: how many called races its favourite won."""
    F, res = out["forecast"], {}
    live = json.loads(LIVE.read_text()) if LIVE.exists() else {}
    for office, keep_d, keep_r in (("house", "fixed_d", "fixed_r"), ("senate", "holdover_d", "holdover_r")):
        g = [r for r in out["races"] if r["office"] == office]
        d, r = (sum(x["winner"] == w for x in g) for w in "DR")
        i = sum(x["winner"] not in (None, "D", "R") for x in g)
        n = len(g) - d - r - i
        held = "D" if office == "senate" else "0"
        held_r = "R" if office == "senate" else "6"
        res[office] = {
            "d": F[office][keep_d] + d,
            "r": F[office][keep_r] + r,
            "i": i,
            "open": n,
            "forecast_d": F[office]["p50"],
            "median": [live[office]["p50"], live[office]["r50"]] if "r50" in live.get(office, {}) else None,
            "seats": held * F[office][keep_d] + "0" * d + "a" * i + "3" * n + "6" * r + held_r * F[office][keep_r],
        }
    a = out["scores"].get("all", {})
    check = {"called": a["called"], "won": round(a["favourite_won"] * a["called"])} if a.get("called") else None
    national = live.get("national", {}).get("p50")
    return {"generated": out["generated"], **res, "national": national, "check": check}


def main():
    doc, _frozen = SC.load_forecast()
    out = SC._clean(build(doc, SC.load_results()))
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    n = sum(r["winner"] is not None for r in out["races"])
    if {**old, "generated": None} == {**out, "generated": None}:  # nothing moved: keep the file (and its time) as it is
        out = old
        print(f"{OUT.relative_to(SC.ROOT)}: unchanged ({n} of {len(out['races'])} races called)")
    else:
        OUT.write_text(json.dumps(out, separators=(",", ":")))
        print(f"{OUT.relative_to(SC.ROOT)}: {n} of {len(out['races'])} races called")
    s = json.dumps(summary(out), separators=(",", ":"))
    if not SUMMARY.exists() or SUMMARY.read_text() != s:
        SUMMARY.write_text(s)


if __name__ == "__main__":
    main()
