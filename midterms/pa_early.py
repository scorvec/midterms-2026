"""Pennsylvania mail-ballot tracker (2026-09-25). DIAGNOSTIC ONLY, nothing feeds the forecast.

Source: PA Department of State mail-ballot request datasets on data.pa.gov (Socrata, public, aggregated server-side, so
nothing large is downloaded): one row per application with party, birth month, application date and ballot-returned date.
  2022 general  uhfm-zhus   (party codes D / R / NF / ...)
  2024 general  3q5t-ddp8   (DEM / REP / OTH / ...)
  2026 general  nbwd-pfn4   (updated daily)
Pennsylvania has no in-person early voting, so mail is the whole pre-Election-Day vote. Two things are counted at matched
days out: APPLICATIONS (by application date) and RETURNED ballots (by return date), by party, and the share under 30.
Caveat: Democrats have voted by mail far more than Republicans since 2020, and Republicans moved
toward mail in 2024 ("Swamp the Vote"), so the party split is a mode signal first. Compare with the same point in 2022.

    python -m midterms.pa_early
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
DATASETS = {2022: ("uhfm-zhus", "2022-11-08"), 2024: ("3q5t-ddp8", "2024-11-05"), 2026: ("nbwd-pfn4", "2026-11-03")}
PARTY = {"D": "DEM", "DEM": "DEM", "R": "REP", "REP": "REP"}
RESULT = {2022: ("US Senate (Fetterman v Oz)", 4.9), 2024: ("President (Harris v Trump)", -1.7)}


def q(ds: str, soql: str) -> list[dict]:
    out, off = [], 0
    while True:
        u = f"https://data.pa.gov/resource/{ds}.json?" + urllib.parse.urlencode({"$query": f"{soql} LIMIT 50000 OFFSET {off}"})
        rows = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "scorvec.com midterm model (+https://scorvec.com/midterms/about.html)"}), timeout=300).read())
        out += rows
        if len(rows) < 50000:
            return out
        off += 50000


def series(year: int) -> pd.DataFrame:
    """Past cycles never change: they are queried once and kept in data/cache; only 2026 is queried each run."""
    keep = CACHE / f"pa_early_series_{year}.csv"
    if year != 2026 and keep.exists(): return pd.read_csv(keep)
    d = _series(year)
    if year != 2026: d.to_csv(keep, index=False)
    return d


def _series(year: int) -> pd.DataFrame:
    ds, ed = DATASETS[year]
    cut = pd.Timestamp(ed) - pd.DateOffset(years=30)                                 # under 30 on Election Day
    # 2022 stores birth dates as timestamps, 2024/2026 as 'YYYY-MM' text (month only, so the cut is to the month)
    youth_from = cut.strftime("%Y-%m-%dT00:00:00") if year == 2022 else (cut - pd.DateOffset(months=1)).strftime("%Y-%m")
    rows = []
    for kind, col in (("applications", "appreturndate"), ("returned", "ballotreturneddate")):
        for grp, where in (("all", ""), ("youth", f" AND dateofbirth > '{youth_from}'")):
            # ballotreturneddate is TEXT in every year (appreturndate a date): group on the raw value, parse here
            sel = f"date_trunc_ymd({col})" if kind == "applications" else col
            r = q(ds, f"SELECT party, {sel} AS d, count(*) AS n WHERE {col} IS NOT NULL{where} GROUP BY party, d")
            for x in r:
                rows.append({"year": year, "kind": kind, "grp": grp, "party": PARTY.get(str(x.get("party")).upper(), "OTH"),
                             "date": str(x.get("d", ""))[:10], "n": int(x["n"])})
    df = pd.DataFrame(rows)
    df["dt"] = pd.to_datetime(df["date"], errors="coerce", format="mixed")
    bad = df["dt"].isna().sum()
    if bad: print(f"  {year}: {bad} date groups unparsed, dropped")
    df = df[df["dt"].notna()]
    df["days_out"] = (pd.Timestamp(ed) - df["dt"]).dt.days
    return df.groupby(["year", "kind", "grp", "party", "days_out"], as_index=False)["n"].sum()


def pace(max_out=70) -> pd.DataFrame:
    D = pd.concat([series(y) for y in DATASETS], ignore_index=True)
    out = []
    for (y, kind, grp, party), g in D.groupby(["year", "kind", "grp", "party"]):
        for d in range(max_out, -1, -1):
            out.append({"year": y, "kind": kind, "grp": grp, "party": party, "days_out": d, "n": int(g[g.days_out >= d]["n"].sum())})
    P = pd.DataFrame(out)
    P.to_csv(CACHE / "pa_early_pace.csv", index=False)
    return P


def summary(P: pd.DataFrame, days_out: int) -> pd.DataFrame:
    rows = []
    for y in sorted(P.year.unique()):
        g = P[(P.year == y) & (P.days_out == days_out)]
        v = lambda kind, grp, party=None: int(g[(g.kind == kind) & (g.grp == grp) & ((g.party == party) if party else True)]["n"].sum())
        a, r = v("applications", "all"), v("returned", "all")
        rows.append({"year": y, "days_out": days_out,
                     "applications": a, "app_D_minus_R_pct": round(100 * (v("applications", "all", "DEM") - v("applications", "all", "REP")) / a, 1) if a else None,
                     "app_youth_pct": round(100 * v("applications", "youth") / a, 1) if a else None,
                     "returned": r, "ret_D_minus_R_pct": round(100 * (v("returned", "all", "DEM") - v("returned", "all", "REP")) / r, 1) if r else None,
                     "ret_youth_pct": round(100 * v("returned", "youth") / r, 1) if r else None,
                     "ret_rate_D": round(100 * v("returned", "all", "DEM") / max(1, v("applications", "all", "DEM")), 1),
                     "ret_rate_R": round(100 * v("returned", "all", "REP") / max(1, v("applications", "all", "REP")), 1),
                     "result_D_minus_R": RESULT.get(y, (None, None))[1]})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    P = pace()
    d = max(0, (pd.Timestamp(DATASETS[2026][1]) - pd.Timestamp(dt.date.today())).days)
    print(f"\nPennsylvania mail ballots at {d} days out (same point in each cycle):")
    print(summary(P, d).to_string(index=False))
    print("\nFinal (everything before Election Day):")
    print(summary(P, 0).to_string(index=False))
