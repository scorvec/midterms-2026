"""Exact figures for a scraped poll (2026-10-02, user: "But in the unrounded kansas poll").

The feeds round a release to whole points (Trafalgar KS Senate 10/1: 44.4-43.5 published, 44-44 scraped). A row in
data/manual/race_polls.csv with exact=1 REPLACES the toplines of the scraped copy of that poll (model._same_poll: same
pollster, end dates within 3 days) instead of being dropped as a duplicate. With no scraped copy yet it is an ordinary
hand-entered row (model.manual_race_polls keeps it until a copy appears - and this module then corrects that copy).
"""
from pathlib import Path

import pandas as pd

from . import model as M

F = Path(__file__).resolve().parents[1] / "data" / "manual" / "race_polls.csv"


def exact_rows(state, office):
    if not F.exists():
        return pd.DataFrame()
    d = pd.read_csv(F, parse_dates=["start_date", "end_date"])
    if "exact" not in d:
        return pd.DataFrame()
    return d[(d.state == state) & (d.office == office) & (d.exact.fillna(0).astype(int) == 1)]


def apply(polls: pd.DataFrame, state, office) -> pd.DataFrame:
    """Overwrite dem/rep/und/other/margin of scraped rows that are copies of an exact=1 manual row."""
    ex = exact_rows(state, office)
    if polls is None or not len(polls) or not len(ex):
        return polls
    p = polls.copy()
    for r in ex.itertuples():
        one = pd.DataFrame({"pollster": [r.pollster], "end_date": [r.end_date]})
        hit = [M._same_poll(q.pollster, q.end_date, None, one) for q in p.itertuples()]
        if not any(hit):
            continue
        # 2026-10-03: only the scraped VERSION this release is - the one closest to the exact figures (a rounded copy sits within
        # half a point); an LV/RV or with/without-leaners twin of the same survey keeps its own numbers
        cand = p.index[hit]
        dist = (p.loc[cand, "dem"].astype(float) - float(r.dem)).abs() + (p.loc[cand, "rep"].astype(float) - float(r.rep)).abs()
        idx = [dist.idxmin()]
        hit = [i in idx for i in p.index]
        for c, v in (("dem", r.dem), ("rep", r.rep), ("und", r.und), ("other", r.other)):
            if c in p and pd.notna(v):
                p.loc[idx, c] = float(v)
        if "margin" in p:
            p.loc[idx, "margin"] = float(r.dem) - float(r.rep)
        print(f"  exact figures for {state} {office} {r.pollster} {r.end_date:%Y-%m-%d}: {r.dem}-{r.rep} ({int(sum(hit))} scraped row)")
    return p
