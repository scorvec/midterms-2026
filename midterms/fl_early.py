"""Florida mail / early vote tracker (2026-09-25; companion to pa_early). DIAGNOSTIC ONLY.

Florida is the one state whose early-vote party totals tracked the 2024 result (user). What is public:
  * the Division of Elections' statistics files, statewide and by county, by party, refreshed daily by 8 a.m.:
      https://electionfiles.floridados.gov/countyballotreportfiles/Stats_<election>_{VbmProvided,VbmVoted,EarlyVoted}.txt
    (the statistics PAGE needs a browser session; these files do not). Current election only: no day-by-day history is
    published, so the 2026 pace is built by snapshotting them every day into data/raw/fl_stats/.
  * final totals of past generals (PDF archive, dos.fl.gov): 2022 and 2024 below.
  * the per-voter vote-by-mail REQUEST files are restricted by statute (s. 101.62) to parties and campaigns; the early-vote
    files are public but only once early voting starts (mid-October).

    python -m midterms.fl_early
"""
from __future__ import annotations

import datetime as dt
import io
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "fl_stats"
ELECTION = ("49894", "2026-11-03")
BASE = "https://electionfiles.floridados.gov/countyballotreportfiles/Stats_{}_{}.txt"
KINDS = ("VbmProvided", "VbmVoted", "EarlyVoted")
# final pre-Election-Day totals (DOS archive PDFs): voted by mail + voted early, and the statewide result (D minus R)
FINALS = {2022: {"REP": 1001229 + 1167529, "DEM": 1194857 + 675629, "total": 2773948 + 2285465, "result": ("Governor (DeSantis)", -19.4)},
          2024: {"REP": 1046566 + 2552609, "DEM": 1257851 + 1514619, "total": 3029152 + 5363013, "result": ("President (Trump)", -13.1)}}


def snapshot() -> list[Path]:
    RAW.mkdir(parents=True, exist_ok=True); got = []
    for k in KINDS:
        try:
            from . import fetch as F                    # one conditional request per file per day
            raw = F.get(BASE.format(ELECTION[0], k), RAW / f"latest_{k}.download", timeout=60)[0]
        except Exception:
            continue                                     # EarlyVoted appears only once early voting starts
        d = pd.read_csv(io.BytesIO(raw), sep="\t", dtype=str)
        comp = pd.to_datetime(d["CompileDate"].str.split().str[0], format="%m/%d/%Y", errors="coerce").max()
        p = RAW / f"{comp:%Y%m%d}_{k}.txt"
        p.write_bytes(raw); got.append(p)
    return got


def pace() -> pd.DataFrame:
    rows = []
    for p in sorted(RAW.glob("2*_*.txt")):
        day, kind = p.stem.split("_", 1)
        d = pd.read_csv(p, sep="\t", dtype=str); s = d[d["CountyName"] == "State Totals"].iloc[0]
        num = lambda c: int(str(s[c]).replace(",", ""))
        rows.append({"date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "kind": kind, "REP": num("TotalRep"), "DEM": num("TotalDem"),
                     "OTH": num("TotalOth"), "NPA": num("TotalNpa"), "total": num("GrandTotal")})
    P = pd.DataFrame(rows)
    if len(P):
        P["days_out"] = (pd.Timestamp(ELECTION[1]) - pd.to_datetime(P["date"])).dt.days
        P["R_minus_D_pct"] = (100 * (P.REP - P.DEM) / P.total).round(1)
    return P


if __name__ == "__main__":
    print("snapshots:", [p.name for p in snapshot()])
    P = pace()
    print(P.to_string(index=False) if len(P) else "no snapshots yet")
    print("\nFinal pre-Election-Day totals (voted by mail + early), R minus D share vs the result:")
    for y, f in FINALS.items():
        print(f"  {y}: R {f['REP']:,}  D {f['DEM']:,}  R-D {100 * (f['REP'] - f['DEM']) / f['total']:+.1f} of all  |  result {f['result'][0]} D-R {f['result'][1]:+.1f}")
