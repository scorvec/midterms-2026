"""Candidate fundraising through June 30 from the FEC (OpenFEC API; public domain), the input of the House money term
(money.py) and the Senate funded-independent gate (senate2026.ind_money). One-time: the June 30 reports are long filed, so
the derived tables are committed under data/static/ and the daily run never calls the API. Rebuild (also available as the
workflow's manual `fec_refresh` input):

    OPENFEC_API_KEY=... python -m midterms.fec_money 2026        # data/static/fec_june30_2026.csv + fec_reports_2026.csv
    OPENFEC_API_KEY=... python -m midterms.fec_money 2018 2022   # the fitting years (nominees from the FEC results workbooks)

Key: an api.data.gov key in the environment variable OPENFEC_API_KEY (never printed or logged; errors report the HTTP status
only). Raw report lists are cached in data/raw/fec_api/{cycle}/{candidate_id}.json (git-ignored), so a rerun resumes.
Rule: reports flagged most_recent (amendments superseded), coverage_end_date <= {year}-06-30, summed per committee;
total_individual_contributions_period, plus candidate contributions and candidate loans as self-funding.
2026 nominees: data/static/fec_nominees_2026.csv (Wikipedia names matched to FEC ids by state, party and surname). An
UNMATCHED nominee is missing (NaN), not $0; a nominee with no filing at all this cycle is a late entrant (missing); a
filer with no report by June 30 is a genuine $0.
fec_reports_2026.csv keeps, per report, what the reconstructed history (backfill.money_asof) needs to know what had been
RECEIVED by each past date: committee, first file number of the amendment chain, receipt and coverage dates, amounts.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "data" / "static"
RAWDIR = ROOT / "data" / "raw" / "fec_api"
UA = {"User-Agent": "scorvec.com midterm model (+https://scorvec.com/midterms/about.html)"}
PAUSE = 1.1                       # api.data.gov keys allow 1,000 calls an hour; stay under a call per second
REPORT_COLS = ["committee_id", "chain0", "file_number", "receipt_date", "coverage_end_date", "most_recent",
               "total_individual_contributions_period", "candidate_contribution_period", "loans_made_by_candidate_period"]
socket.setdefaulttimeout(90)


def _key():
    k = os.environ.get("OPENFEC_API_KEY", "").strip()
    if not k: raise SystemExit("OPENFEC_API_KEY is not set")
    return k


def get(path, **q):
    q["api_key"] = _key()
    url = f"https://api.open.fec.gov/v1/{path}?{urllib.parse.urlencode(q)}"
    for k in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(60 * (k + 1)); continue
            if e.code >= 500: time.sleep(10 * (k + 1)); continue
            if e.code in (400, 404, 422): return None
            raise SystemExit(f"FEC API HTTP {e.code} on {path}")
        except (urllib.error.URLError, TimeoutError):
            time.sleep(10 * (k + 1))
    raise SystemExit(f"FEC API: gave up on {path}")


def reports(cand_id: str, year: int) -> list | None:
    """Every report of the candidate's committees in the cycle (cached); None if the API rejects the id."""
    cache = RAWDIR / str(year) / f"{cand_id}.json"
    if cache.exists(): return json.loads(cache.read_text())
    reps, page = [], 1
    while True:
        d = get("reports/house-senate/", candidate_id=cand_id, cycle=year, per_page=100, page=page, sort="coverage_end_date")
        time.sleep(PAUSE)
        if d is None: return None
        reps += d.get("results", [])
        if page >= d.get("pagination", {}).get("pages", 1): break
        page += 1
    cache.parent.mkdir(parents=True, exist_ok=True); cache.write_text(json.dumps(reps))
    return reps


def june30(cand_id: str, year: int):
    if not re.fullmatch(r"[HS]\d[A-Z]{2}\d{5}", cand_id): return float("nan"), 0, 0, float("nan")   # malformed id
    reps = reports(cand_id, year)
    if reps is None: return float("nan"), 0, 0, float("nan")
    cut = f"{year}-06-30"
    keep = [r for r in reps if r.get("most_recent") and str(r.get("coverage_end_date", ""))[:10] <= cut]
    f = lambda k: sum(float(r.get(k) or 0) for r in keep)
    return f("total_individual_contributions_period"), len(keep), len({r["committee_id"] for r in keep}), \
        f("candidate_contribution_period") + f("loans_made_by_candidate_period")


def nominees(year):
    """(seat, party, FEC id) of the top D and top R vote-getter per contested district, from the FEC results workbook."""
    from . import data_prep as D
    x = pd.ExcelFile(ROOT / "data" / "raw" / "fec" / f"federalelections{year}.xlsx")
    df = x.parse([s for s in x.sheet_names if "House Results" in s][0]); df.columns = [str(c).strip() for c in df.columns]
    idc = [c for c in df.columns if c.startswith("FEC ID")][0]; gv = [c for c in df.columns if c.startswith("GENERAL VOTES")][0]
    df["st"] = df["STATE ABBREVIATION"].ffill(); df["cd"] = pd.to_numeric(df["DISTRICT"].astype(str).str.extract(r"^(\d+)", expand=False), errors="coerce")
    df["p"] = df["PARTY"].map(D._party); df["v"] = pd.to_numeric(df[gv], errors="coerce")
    df = df[df["p"].isin(["D", "R"]) & (df["v"] > 0) & df["cd"].notna() & df[idc].notna()].copy(); df.loc[df["cd"] == 0, "cd"] = 1
    nom = df.sort_values("v").groupby(["st", "cd", "p"]).tail(1)
    nom["seat"] = nom["st"] + "-" + nom["cd"].astype(int).astype(str)
    nom = nom[["seat", "p", idc]].rename(columns={idc: "cand_id"})
    nom["cand_id"] = nom["cand_id"].astype(str).str.strip().str.upper()
    return nom


def main_past(years):
    for y in years:
        nom = nominees(y); rows = []
        for i, r in enumerate(nom.itertuples()):
            indiv, nrep, ncom, selff = june30(r.cand_id, y)
            rows.append({"seat": r.seat, "party": r.p, "cand_id": r.cand_id, "indiv_jun30": indiv, "self_jun30": selff, "n_reports": nrep, "n_committees": ncom})
            if i % 100 == 0: print(f"{y}: {i}/{len(nom)}", flush=True)
        pd.DataFrame(rows).to_csv(STATIC / f"fec_june30_{y}.csv", index=False); print(y, "done", flush=True)


def main_2026():
    nom = pd.read_csv(STATIC / "fec_nominees_2026.csv"); rows = []
    for i, r in enumerate(nom.itertuples()):
        cid = str(r.cand_id).strip() if isinstance(r.cand_id, str) else None
        indiv, nrep, ncom, selff = june30(cid, 2026) if cid else (float("nan"), 0, 0, float("nan"))
        cache = RAWDIR / "2026" / f"{cid}.json"
        if cid and cache.exists() and not json.loads(cache.read_text()): indiv = selff = float("nan")   # late entrant
        rows.append({"seat": r.seat, "party": r.party, "cand_id": r.cand_id, "indiv_jun30": indiv, "self_jun30": selff, "n_reports": nrep})
        if i % 100 == 0: print(f"2026: {i}/{len(nom)}", flush=True)
    pd.DataFrame(rows).to_csv(STATIC / "fec_june30_2026.csv", index=False)
    sen = pd.read_csv(STATIC / "fec_senate_june30_2026.csv")            # Senate nominees: report lists only (backfill)
    for cid in sen["cand_id"].dropna().astype(str).str.strip().unique():
        june30(cid, 2026)
    compact(2026); print("2026 done", flush=True)


def compact(year=2026, cut=None):
    """data/static/fec_reports_{year}.csv from the cached report lists: one row per report with coverage through June 30."""
    cut = cut or f"{year}-06-30"; rows = []
    for f in sorted((RAWDIR / str(year)).glob("*.json")):
        for r in json.loads(f.read_text()):
            if str(r.get("coverage_end_date", ""))[:10] > cut: continue
            ch = r.get("amendment_chain") or [r.get("file_number")]
            rows.append({"cand_id": f.stem, "committee_id": r.get("committee_id"), "chain0": ch[0], "file_number": r.get("file_number"),
                         "receipt_date": str(r.get("receipt_date") or "")[:10], "coverage_end_date": str(r.get("coverage_end_date") or "")[:10],
                         "most_recent": bool(r.get("most_recent")),
                         **{k: r.get(k) for k in REPORT_COLS[6:]}})
    out = pd.DataFrame(rows, columns=["cand_id"] + REPORT_COLS)
    out.to_csv(STATIC / f"fec_reports_{year}.csv", index=False); print(f"fec_reports_{year}.csv: {len(out)} reports, {out.cand_id.nunique()} candidates")
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["2026"]: main_2026()
    elif args == ["compact"]: compact(2026)
    else: main_past([int(a) for a in args] or [2018, 2022])
