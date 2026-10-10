"""Florida mail / early vote tracker (2026-09-25; 2024 baseline and registration normalisation 2026-10-10). DIAGNOSTIC ONLY.

Florida is the one state whose early-vote party totals tracked the 2024 result (user). What is public:
  * the Division of Elections' statistics files, statewide and by county, by PARTY only, refreshed through the day:
      https://electionfiles.floridados.gov/countyballotreportfiles/Stats_<election>_{VbmProvided,VbmVoted,EarlyVoted}.txt
    (the statistics PAGE needs a browser session; these files do not). Current election only: no day-by-day history is
    published, so the 2026 pace is built by snapshotting them every day into data/early/fl_stats/ (committed by the
    early-diag workflow; the daily forecast run keeps its own copy in data/raw/fl_stats, read here too).
  * 2024 at matched days out: the Wayback Machine's captures of the same files for the 2024 general (election 43888),
    fetched once into data/static/fl_wayback/ (VbmVoted compiled 10/04, 10/09, 10/28; VbmProvided 09/16, 10/10, 10/28;
    EarlyVoted 10/28; plus the final files). 2022 (26906) was captured only after the election (final totals).
  * registration by party (active voters, month end): DOS monthly reports, data/static/fl_registration.csv. dos.fl.gov is
    behind a browser check, so new months are added by hand from the DOS workbook (no automated fetch).
  * race is NOT in any of this. It is self-reported on the Florida voter record, but only the monthly voter extract carries
    it (free, on request to the Division, not downloadable), and per-voter VBM request files are restricted by statute
    (s. 101.62) to parties, candidates and committees.

Measures, each at the same number of days before Election Day (days out = Election Day - CompileDate; the 8 a.m. compile
holds the counties' uploads through the previous day):
  * returns R-D       : (REP - DEM) / all returned ballots, points;
  * vs registration   : returns R-D minus the active-registration R-D of the latest month end (FL's Republican lead grew
                        from +2.0 in Sep 2022 to +7.6 in Sep 2024 and +11.4 in Aug 2026, so a flat returns margin is a
                        Democratic gain relative to who is registered);
  * vs requests       : returns R-D minus the R-D of all ballots sent (returned + outstanding), and each party's return
                        rate. This is the cleanest party-intensity read: it does not depend on who requested a mail ballot.
Traps: Hurricane Helene (26 Sep 2024) and Milton (landfall 9 Oct 2024) hit the 2024 mail period exactly at the baseline
dates; VbmProvided and VbmVoted compile at different times, so "requests" pairs files at most one day apart (flagged).

    python -m midterms.fl_early              # snapshot (data/early/fl_stats), compare, write data/early/fl_early.json
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "fl_stats"                    # the daily forecast run's copy (actions cache)
EARLY = ROOT / "data" / "early"
SNAP = EARLY / "fl_stats"                                   # committed day-by-day snapshots
OUT = EARLY / "fl_early.json"
STATIC = ROOT / "data" / "static"
WAYBACK = STATIC / "fl_wayback"
REG = STATIC / "fl_registration.csv"
ELECTION = ("49894", "2026-11-03")
ELECTIONS = {2026: ("49894", "2026-11-03"), 2024: ("43888", "2024-11-05"), 2022: ("26906", "2022-11-08")}
BASE = "https://electionfiles.floridados.gov/countyballotreportfiles/Stats_{}_{}.txt"
KINDS = ("VbmProvided", "VbmVoted", "EarlyVoted")
STAT_KIND = {"Vote-by-Mail Provided (Not Yet Returned)": "VbmProvided", "Voted Vote-by-Mail": "VbmVoted", "Voted Early": "EarlyVoted"}
# final pre-Election-Day totals (DOS archive PDFs): voted by mail + voted early, and the statewide result (D minus R)
FINALS = {2022: {"REP": 1001229 + 1167529, "DEM": 1194857 + 675629, "total": 2773948 + 2285465, "result": ("Governor (DeSantis)", -19.4)},
          2024: {"REP": 1046566 + 2552609, "DEM": 1257851 + 1514619, "total": 3029152 + 5363013, "result": ("President (Trump)", -13.1)}}


def snapshot(dest: Path = RAW) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True); RAW.mkdir(parents=True, exist_ok=True); got = []
    for k in KINDS:
        try:
            from . import fetch as F                    # one conditional request per file per run
            raw = F.get(BASE.format(ELECTION[0], k), RAW / f"latest_{k}.download", timeout=60)[0]   # conditional copy: cache
        except Exception:
            continue                                     # EarlyVoted appears only once early voting starts
        d = pd.read_csv(io.BytesIO(raw), sep="\t", dtype=str)
        comp = pd.to_datetime(d["CompileDate"].str.split().str[0], format="%m/%d/%Y", errors="coerce").max()
        p = dest / f"{comp:%Y%m%d}_{k}.txt"
        p.write_bytes(raw); got.append(p)
    return got


def read_stats(path: Path) -> dict | None:
    """The State Totals row of one statistics file, checked against the sum of the counties."""
    d = pd.read_csv(path, sep="\t", dtype=str)
    num = lambda s: pd.to_numeric(s.str.replace(",", ""), errors="coerce")
    for c in ("TotalRep", "TotalDem", "TotalOth", "TotalNpa", "GrandTotal"): d[c] = num(d[c])
    st = d[d["CountyName"].str.strip() == "State Totals"]
    if len(st) != 1: return None
    s = st.iloc[0]; cty = d[d["CountyName"].str.strip() != "State Totals"]
    comp = pd.to_datetime(" ".join(s["CompileDate"].split()), format="%m/%d/%Y %I:%M%p", errors="coerce")
    if pd.isna(comp): comp = pd.to_datetime(s["CompileDate"].split()[0], format="%m/%d/%Y")
    r = {"election": s["ElectionNumber"].strip(), "kind": STAT_KIND.get(s["StatType"].strip(), s["StatType"].strip()),
         "compiled": comp, "REP": int(s["TotalRep"]), "DEM": int(s["TotalDem"]), "OTH": int(s["TotalOth"]),
         "NPA": int(s["TotalNpa"]), "total": int(s["GrandTotal"]), "counties": int(len(cty)),
         "county_sum_gap": int(cty["GrandTotal"].sum() - s["GrandTotal"]), "file": path.name}
    r["parts_gap"] = r["REP"] + r["DEM"] + r["OTH"] + r["NPA"] - r["total"]
    return r


def snapshots() -> pd.DataFrame:
    rows = []
    for p in sorted(SNAP.glob("2*_*.txt")) + sorted(RAW.glob("2*_*.txt")) + sorted(WAYBACK.glob("Stats_*.txt")):
        r = read_stats(p)
        if r: rows.append(r)
    S = pd.DataFrame(rows)
    yr = {v[0]: k for k, v in ELECTIONS.items()}
    S["year"] = S["election"].map(yr)
    S = S[S["year"].notna()].astype({"year": int})
    S["day"] = S["compiled"].dt.normalize()
    S = S.sort_values("compiled").drop_duplicates(["year", "kind", "day"], keep="last")   # the day's latest compile
    ed = S["year"].map(lambda y: pd.Timestamp(ELECTIONS[y][1]))
    S["days_out"] = (ed - S["day"]).dt.days
    S = S[S["days_out"] >= 0].copy()                        # pre-Election-Day compiles only (finals kept separately)
    S["R_minus_D_pct"] = (100 * (S.REP - S.DEM) / S.total).round(2)
    return S.reset_index(drop=True)


def registration(day: pd.Timestamp) -> dict | None:
    R = pd.read_csv(REG, parse_dates=["date"])
    R = R[R["date"] <= day]
    if not len(R): return None
    r = R.iloc[-1]
    return {"date": str(r["date"].date()), "R_minus_D_pct": round(100 * (r.REP - r.DEM) / r.total, 2),
            "REP_pct": round(100 * r.REP / r.total, 2), "DEM_pct": round(100 * r.DEM / r.total, 2),
            "age_days": int((day - r["date"]).days)}


def enrich(S: pd.DataFrame) -> list[dict]:
    """One record per pre-Election-Day VbmVoted / EarlyVoted compile: margins vs registration and vs requests."""
    out = []
    prov = S[S.kind == "VbmProvided"]
    for _, v in S[S.kind.isin(["VbmVoted", "EarlyVoted"])].iterrows():
        e = {"year": int(v.year), "kind": v.kind, "compiled": v.compiled.strftime("%Y-%m-%d %H:%M"), "days_out": int(v.days_out),
             "REP": int(v.REP), "DEM": int(v.DEM), "NPA": int(v.NPA), "OTH": int(v.OTH), "total": int(v.total),
             "R_minus_D_pct": float(v.R_minus_D_pct)}
        rg = registration(v.day)
        if rg:
            e["registration"] = rg
            e["vs_registration_pts"] = round(e["R_minus_D_pct"] - rg["R_minus_D_pct"], 2)
        if v.kind == "VbmVoted":
            p = prov[(prov.year == v.year) & ((prov.day - v.day).abs() <= pd.Timedelta(days=1))]
            if len(p):
                p = p.iloc[(p.day - v.day).abs().argsort().iloc[0]]
                base = {k: int(v[k] + p[k]) for k in ("REP", "DEM", "NPA", "OTH", "total")}
                bm = 100 * (base["REP"] - base["DEM"]) / base["total"]
                e["requests"] = {"provided_compiled": p.compiled.strftime("%Y-%m-%d %H:%M"),
                                 "offset_days": int((p.day - v.day).days), "sent_total": base["total"],
                                 "sent_R_minus_D_pct": round(bm, 2),
                                 "return_rate_pct": {k: round(100 * v[k] / base[k], 2) for k in ("REP", "DEM", "NPA")}}
                e["vs_requests_pts"] = round(e["R_minus_D_pct"] - bm, 2)
        out.append(e)
    return out


def matched(rec: list[dict]) -> list[dict]:
    """2026 against 2024 at the same days out (exact day only; the nearest 2026 day within one is shown, flagged)."""
    out = []
    for b in [r for r in rec if r["year"] == 2024]:
        c = [r for r in rec if r["year"] == 2026 and r["kind"] == b["kind"]]
        if not c: continue
        best = min(c, key=lambda r: abs(r["days_out"] - b["days_out"]))
        if abs(best["days_out"] - b["days_out"]) > 1: continue
        m = {"kind": b["kind"], "days_out": b["days_out"], "exact": best["days_out"] == b["days_out"], "2024": b, "2026": best,
             "returned_change_pct": round(100 * (best["total"] / b["total"] - 1), 1),
             "R_minus_D_change_pts": round(best["R_minus_D_pct"] - b["R_minus_D_pct"], 2)}
        if "vs_registration_pts" in b and "vs_registration_pts" in best:
            m["vs_registration_change_pts"] = round(best["vs_registration_pts"] - b["vs_registration_pts"], 2)
        if "vs_requests_pts" in b and "vs_requests_pts" in best:
            m["vs_requests_change_pts"] = round(best["vs_requests_pts"] - b["vs_requests_pts"], 2)
        out.append(m)
    return out


def run() -> dict:
    try:
        got = snapshot(SNAP); print("FL snapshots:", [p.name for p in got])
    except Exception as e:
        print("FL snapshot failed:", str(e)[:120])
    S = snapshots()
    bad = S[(S.parts_gap != 0) | (S.county_sum_gap != 0) | (S.counties != 67)]
    if len(bad): print("  !! FL files that do not add up:", bad[["file", "parts_gap", "county_sum_gap", "counties"]].to_dict("records"))
    rec = enrich(S[~S.index.isin(bad.index)])
    latest = [r for r in rec if r["year"] == 2026]
    res = {"note": ("DIAGNOSTIC ONLY. Florida DOS statistics files (party only) snapshotted daily; 2024 from Wayback Machine "
                    "captures of the same files. Registration = DOS month-end active voters (latest month on or before the "
                    "compile). Hurricanes Helene (26 Sep 2024) and Milton (9 Oct 2024) disrupted 2024 mail at the baseline dates."),
           "latest": {k: min((r for r in latest if r["kind"] == k), key=lambda r: r["days_out"], default=None)
                      for k in ("VbmVoted", "EarlyVoted")},
           "matched_2024": matched(rec), "series": rec,
           "finals": {str(y): {**f, "R_minus_D_pct": round(100 * (f["REP"] - f["DEM"]) / f["total"], 2)} for y, f in FINALS.items()}}
    EARLY.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, separators=(",", ":"), allow_nan=False, default=str))
    return res


def show(res: dict):
    nan = float("nan")
    print("\nFlorida returns, R minus D (points of all returned), vs registration and vs ballots sent:")
    for r in sorted(res["series"], key=lambda r: (r["kind"], r["year"], -r["days_out"])):
        rq, rg = r.get("requests"), r.get("registration", {})
        print(f"  {r['year']} {r['kind']:<10} {r['compiled']} {r['days_out']:>3} d  R {r['REP']:>9,} D {r['DEM']:>9,} "
              f"all {r['total']:>9,}  R-D {r['R_minus_D_pct']:+6.2f}  reg {rg.get('R_minus_D_pct', nan):+6.2f} ({rg.get('date')})"
              f"  vs reg {r.get('vs_registration_pts', nan):+6.2f}"
              + (f"  sent R-D {rq['sent_R_minus_D_pct']:+6.2f} (off {rq['offset_days']} d) vs sent {r['vs_requests_pts']:+5.2f}"
                 f"  return rate R {rq['return_rate_pct']['REP']:.1f}% D {rq['return_rate_pct']['DEM']:.1f}%" if rq else ""))
    print("\n2026 vs 2024 at the same days out:")
    for m in res["matched_2024"]:
        print(f"  {m['kind']} {m['days_out']} d ({'exact' if m['exact'] else 'nearest, 2026 at ' + str(m['2026']['days_out'])}): "
              f"returned {m['2024']['total']:,} -> {m['2026']['total']:,} ({m['returned_change_pct']:+.1f} %), R-D "
              f"{m['2024']['R_minus_D_pct']:+.2f} -> {m['2026']['R_minus_D_pct']:+.2f} ({m['R_minus_D_change_pts']:+.2f}); "
              f"vs registration {m.get('vs_registration_change_pts', nan):+.2f}; vs sent {m.get('vs_requests_change_pts', nan):+.2f}")
    print("\nFinal pre-Election-Day totals (voted by mail + early), R minus D share vs the result:")
    for y, f in FINALS.items():
        print(f"  {y}: R {f['REP']:,}  D {f['DEM']:,}  R-D {100 * (f['REP'] - f['DEM']) / f['total']:+.1f} of all  |  result {f['result'][0]} D-R {f['result'][1]:+.1f}")


if __name__ == "__main__":
    show(run())
    from . import fetch as _F; _F.report()
    sys.exit(0)
