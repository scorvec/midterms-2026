"""Texas early-vote turnout tracker (2026-10-05; companion to fl_early / pa_early). DIAGNOSTIC ONLY, nothing feeds the forecast.

Texas has no party registration, so this counts TURNOUT only: early votes (in person + by mail, cumulative) as a share of
registered voters, statewide and for fixed county groups, against the 2022 general on the same early-voting day. No party
split is inferred (modelled party scores such as TargetSmart / L2 are proprietary and out of scope).

Sources (Texas Secretary of State; public records, data supplied by the county election officials):
  * 2026 general (Nov 3, 2026; early voting Mon Oct 19 - Fri Oct 30): the SOS "Early Voting Turnout" portal on Civix,
      https://goelect.txelections.civixapps.com/ivis-evr-ui/evr
    backed by plain GETs (JSON, either raw or wrapped as {"upload": <base64>}):
      .../api-ivis-system/api/v1/getFile?type=EVR_ELECTION                                   election index + EV dates
      .../api-ivis-system/api/v1/getFile?type=EVR_EARLYVOTING&electionId=<id>&electionDate=MM/DD/YYYY
                                                     one row per county: registered_voters, in_person_votes_on_date,
                                                     total_in_person_votes_for_election, total_mail_votes_for_election
    Only the county summary is read; the per-voter rosters behind the same portal are never requested.
  * 2022 general (Nov 8, 2022; early voting Mon Oct 24 - Fri Nov 4): the SOS legacy portal (elections 2020-2025),
      https://earlyvoting.texas-election.com/Elections/getElectionDetails.do
    a Struts form (GET getElectionDetails.do -> POST getElectionEVDates.do -> POST getEVDetails.do per date) whose county
    table is parsed once (`baseline`) into data/static/tx_early_2022.csv; provenance in data/static/tx_early_sources.json.
  * county groups are checked against the 2024 presidential result by county: MIT Election Data and Science Lab, County
    Presidential Election Returns 2000-2024, doi:10.7910/DVN/VOQCHQ (CC0) -> data/static/tx_county_pres2024.csv.

Calendar: Election Code 85.001 starts early voting on the 17th day before Election Day (moved to the next business day
when that is a weekend) and ends on the 4th day before. Nov 3, 2026 - 17 = Sat Oct 17 -> Mon Oct 19; Nov 8, 2022 - 17 = Sat
Oct 22 -> Mon Oct 24. Both periods are 12 days (Mon to the second Fri, weekends included), so early-voting day k falls on
the same weekday and the same number of days before Election Day in both years. SB 2753 (2025) would move early voting to
12 days before through the day before Election Day, but only after the SOS publishes its implementation report (due by
Aug 1, 2027); the SOS calendar for Nov 3, 2026 keeps Oct 19-30.

    python -m midterms.tx_early               # daily: snapshot (from Oct 19) + summary -> web/data/tx_early.json
    python -m midterms.tx_early baseline      # ONE-TIME, GitHub Actions only (.github/workflows/tx-early-baseline.yml)
"""
from __future__ import annotations

import base64
import datetime as dt
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "tx_stats"
STATIC = ROOT / "data" / "static"
BASE_2022 = STATIC / "tx_early_2022.csv"
PRES_2024 = STATIC / "tx_county_pres2024.csv"
SOURCES = STATIC / "tx_early_sources.json"
OUT = ROOT / "web" / "data" / "tx_early.json"

CIVIX = "https://goelect.txelections.civixapps.com/api-ivis-system/api/v1/getFile"
CIVIX_UI = "https://goelect.txelections.civixapps.com/ivis-evr-ui/evr"
LEGACY = "https://earlyvoting.texas-election.com/Elections"
MEDSL_COUNTY = "https://dataverse.harvard.edu/api/access/datafile/13573089"   # countypres_2000-2024.tab (tab-separated)

CAL = {2026: {"election_day": dt.date(2026, 11, 3), "first": dt.date(2026, 10, 19), "last": dt.date(2026, 10, 30)},
       2022: {"election_day": dt.date(2022, 11, 8), "first": dt.date(2022, 10, 24), "last": dt.date(2022, 11, 4)}}

# County groups (SOS spelling, upper case). The two suburban groups are fixed here, not chosen from the turnout.
GROUPS = {
    "big_dem": ("Big Democratic counties", ["HARRIS", "DALLAS", "TRAVIS", "BEXAR", "EL PASO"]),
    # Republican-leaning suburbs / exurbs: Collin, Denton (the large, fast-growing DFW suburbs, Trump single to low double
    # digits in 2024) and Montgomery, plus every collar county of the four big metros (DFW, Houston, San Antonio) with more
    # than ~75,000 registered voters that Trump carried by 20+ points in 2024 (tx_county_pres2024.csv; the margins are in
    # the JSON). Austin's collar (Williamson close, Hays carried by Harris) is left out; Tarrant and Fort Bend are the
    # swing group below.
    "gop_suburbs": ("Republican-leaning suburbs and exurbs",
                    ["COLLIN", "DENTON", "MONTGOMERY", "ROCKWALL", "PARKER", "KAUFMAN", "ELLIS", "JOHNSON",
                     "BRAZORIA", "GALVESTON", "COMAL", "GUADALUPE"]),
    "rgv_border": ("Rio Grande Valley and border", ["HIDALGO", "CAMERON", "WEBB", "STARR", "WILLACY", "MAVERICK"]),
    "swing_suburbs": ("Swing suburbs", ["TARRANT", "FORT BEND"]),
}

NOTES = [
    "Diagnostic only: nothing here feeds the forecast.",
    "Texas has no party registration and the SOS publishes no party data: these are turnout counts, not vote choice. No "
    "party split is estimated (modelled party scores are proprietary and not used).",
    "Early votes = cumulative in-person early votes + cumulative mail ballots received, as reported by each county to the "
    "SOS; shares are of the registered voters in the same file. Mail ballots received are not mail ballots accepted.",
    "Mail voting in Texas is limited to voters 65 or older, voters with a disability, voters out of the county during the "
    "election, voters confined for childbirth and eligible jailed voters (Election Code ch. 82), so mail is a small and "
    "old-skewed slice of the early vote; mail ballots keep arriving after early voting ends and are not followed here.",
    "Same-day comparison: day k of early voting in 2026 (Oct 19-30) against day k in 2022 (Oct 24 - Nov 4). Both periods "
    "start on the 17th day before Election Day moved to the following Monday, run 12 days and end on the 4th day before "
    "Election Day, so day k is the same weekday and the same number of days out in both years.",
    "Rules since 2022: SB 1 (2021: 6 a.m.-10 p.m. window, no 24-hour or drive-through voting, ID numbers on mail ballots) "
    "already applied in 2022. HB 1217 (2023) extends the 12-hour last-week weekdays and the last-weekend hours to every "
    "county regardless of population (in 2022 only larger counties had to offer them), which adds hours mostly in small "
    "rural counties. SB 2753 (2025) is not in effect for November 2026 (SOS calendar: Oct 19-30).",
    "County roster updates lag: a county that has not sent the day's file shows the previous total. Each day lists how "
    "many counties did not move from the day before; the SOS revises past days as counties catch up, and the latest "
    "version of every day is used.",
    "Registered voters grew between 2022 and 2026, so turnout is compared as a share of registered voters, and the group "
    "composition (its share of the statewide early vote) is shown against its 2022 share.",
]


# ------------------------------------------------------------------------------------------------------------------------
# 2026: daily snapshot of the Civix county files

def _decode(b: bytes):
    """Civix payloads come raw or as {"upload": base64}; an empty body means no data yet."""
    if not b or not b.strip(): return None
    j = json.loads(b)
    if isinstance(j, dict) and "upload" in j:
        s = base64.b64decode(j["upload"] or "")
        return json.loads(s) if s.strip() else None
    return j


def _civix_url(**q) -> str:
    return CIVIX + "?" + urllib.parse.urlencode(q)


def _mdy(d: dt.date) -> str:
    return d.strftime("%m/%d/%Y")


def find_2026(index: dict) -> dict | None:
    for e in (index or {}).get("elections") or []:
        name = str(e.get("election_name", "")).upper()
        if e.get("election_date") == _mdy(CAL[2026]["election_day"]) and "GENERAL" in name and "SPECIAL" not in name:
            return e
    return None


def snapshot(today: dt.date | None = None) -> list[Path]:
    """One conditional request per file per day (fetch.get, min_age 20 h): the election index and the county file of every
    early-voting date up to today. Quiet no-op before early voting starts or while a file is not up."""
    from . import fetch as F
    today = today or dt.date.today()
    if not CAL[2026]["first"] <= today <= CAL[2026]["election_day"] + dt.timedelta(days=14): return []
    RAW.mkdir(parents=True, exist_ok=True); got = []
    try:
        idx = _decode(F.get(_civix_url(type="EVR_ELECTION"), RAW / "election_index.download", min_age_h=20, timeout=60)[0])
    except Exception as e:
        print("  TX election index unavailable:", str(e)[:120]); return []
    el = find_2026(idx)
    if not el: return []
    (RAW / "election.json").write_text(json.dumps({k: v for k, v in el.items() if k != "counties"}))
    dates = sorted({dt.datetime.strptime(x["date"], "%m/%d/%Y").date() for x in el.get("early_voting_dates") or []}) \
        or list(pd.date_range(CAL[2026]["first"], CAL[2026]["last"]).date)
    for d in [d for d in dates if d <= today]:
        try:
            raw, changed = F.get(_civix_url(type="EVR_EARLYVOTING", electionId=el["id"], electionDate=_mdy(d)),
                                 RAW / f"latest_{d:%Y%m%d}.download", min_age_h=20, timeout=60)
            j = _decode(raw)
        except Exception:
            continue                                  # not posted yet (empty body / S3 key error): next run
        if not j or not j.get("turnout_by_county"): continue
        p = RAW / f"ev{d:%Y%m%d}_got{today:%Y%m%d}.json"
        if changed or not list(RAW.glob(f"ev{d:%Y%m%d}_got*.json")):
            p.write_text(json.dumps(j, separators=(",", ":"))); got.append(p)
    return got


def _num(x):
    try: return float(str(x).replace(",", "")) if x not in (None, "", " ") else float("nan")
    except ValueError: return float("nan")


def series_2026() -> pd.DataFrame:
    """Latest version of every early-voting day held in data/raw/tx_stats."""
    latest = {}
    for p in sorted(RAW.glob("ev*_got*.json")):
        latest[p.stem.split("_")[0][2:]] = p                     # sorted: the newest retrieval wins
    rows = []
    for day, p in sorted(latest.items()):
        d = dt.datetime.strptime(day, "%Y%m%d").date()
        for c in json.loads(p.read_text()).get("turnout_by_county", []):
            rows.append({"date": d, "county": str(c.get("name", "")).strip().upper(),
                         "registered": _num(c.get("registered_voters")),
                         "in_person_on_date": _num(c.get("in_person_votes_on_date")),
                         "in_person_cum": _num(c.get("total_in_person_votes_for_election")),
                         "mail_cum": _num(c.get("total_mail_votes_for_election")), "retrieved": p.stem.split("got")[1]})
    D = pd.DataFrame(rows)
    return _with_days(D, 2026) if len(D) else D


def _with_days(D: pd.DataFrame, year: int) -> pd.DataFrame:
    c = CAL[year]
    D = D[~D["county"].isin(["STATEWIDE", "TOTAL", "TOTALS", "STATE TOTAL", ""])].copy()
    D["ev_day"] = [(pd.Timestamp(x) - pd.Timestamp(c["first"])).days + 1 for x in D["date"]]
    D["days_out"] = [(pd.Timestamp(c["election_day"]) - pd.Timestamp(x)).days for x in D["date"]]
    D["total_cum"] = D["in_person_cum"].fillna(0) + D["mail_cum"].fillna(0)
    return D[(D.ev_day >= 1) & (D.date <= c["last"])]


# ------------------------------------------------------------------------------------------------------------------------
# summary -> web/data/tx_early.json

def _agg(D: pd.DataFrame, counties: list[str] | None, state_ev: float | None):
    g = D if counties is None else D[D.county.isin(counties)]
    if not len(g): return None
    ev, rv, mail = g.total_cum.sum(), g.registered.sum(), g.mail_cum.fillna(0).sum()
    return {"ev": int(ev), "rv": int(rv), "pct_rv": round(100 * ev / rv, 2) if rv else None,
            "share": round(100 * ev / state_ev, 2) if state_ev else None,
            "mail_pct": round(100 * mail / ev, 1) if ev else None,
            "n_counties": int(g.county.nunique())}


def _pres_margins() -> dict:
    if not PRES_2024.exists(): return {}
    P = pd.read_csv(PRES_2024)
    return {r.county: r for r in P.itertuples()}


def _group_margin(pres: dict, counties: list[str] | None):
    rs = list(pres.values()) if counties is None else [pres[c] for c in counties if c in pres]
    tot = sum(r.total for r in rs)
    return round(100 * (sum(r.rep for r in rs) - sum(r.dem for r in rs)) / tot, 1) if tot else None


def summary() -> dict | None:
    if not BASE_2022.exists():
        print("  TX: no 2022 baseline yet (data/static/tx_early_2022.csv; run the tx-early-baseline workflow)"); return None
    B = pd.read_csv(BASE_2022, parse_dates=["date"]); B["date"] = B["date"].dt.date
    C = series_2026()
    pres = _pres_margins()
    groups = {"statewide": ("Statewide", None), **GROUPS}
    days, prev26 = [], None
    for k in range(1, 13):
        b = B[B.ev_day == k]; c = C[C.ev_day == k] if len(C) else C
        s22 = b.total_cum.sum() if len(b) else None
        s26 = c.total_cum.sum() if len(c) else None
        day = {"ev_day": k, "date": str(CAL[2026]["first"] + dt.timedelta(days=k - 1)),
               "date_2022": str(CAL[2022]["first"] + dt.timedelta(days=k - 1)),
               "days_out": (CAL[2026]["election_day"] - CAL[2026]["first"]).days - (k - 1), "groups": {}}
        for key, (_, counties) in groups.items():
            a22, a26 = _agg(b, counties, s22) if len(b) else None, _agg(c, counties, s26) if len(c) else None
            day["groups"][key] = {
                "ev": a26 and a26["ev"], "pct_rv": a26 and a26["pct_rv"], "share": a26 and a26["share"],
                "mail_pct": a26 and a26["mail_pct"],
                "ev_2022": a22 and a22["ev"], "pct_rv_2022": a22 and a22["pct_rv"], "share_2022": a22 and a22["share"],
                "mail_pct_2022": a22 and a22["mail_pct"],
                "diff_pts": round(a26["pct_rv"] - a22["pct_rv"], 2) if a26 and a22 and None not in (a26["pct_rv"], a22["pct_rv"]) else None,
                "ratio": round(a26["pct_rv"] / a22["pct_rv"], 3) if a26 and a22 and a22["pct_rv"] else None,
                "share_shift": round(a26["share"] - a22["share"], 2) if a26 and a22 and None not in (a26["share"], a22["share"]) else None}
        if len(c):
            day["counties_2026"] = int(c.county.nunique())
            day["retrieved"] = max(c.retrieved)
            if prev26 is not None:                  # counties whose total did not move from the day before (roster lag)
                m = c.merge(prev26[["county", "total_cum"]], on="county", suffixes=("", "_prev"))
                stale = m[m.total_cum <= m.total_cum_prev]
                named = sorted(set(stale.county) & {x for _, cs in GROUPS.values() for x in cs})
                day["stale_counties"] = int(len(stale)); day["stale_named"] = named
            prev26 = c
        else:
            prev26 = None                           # a missing day: no day-before comparison for the next one
        days.append(day)
    last = max(C.ev_day) if len(C) else None
    counties = {}
    for _, cs in GROUPS.values():
        for name in cs:
            r = {"pres2024_R_margin": round(float(pres[name].margin_r), 1) if name in pres else None}
            if last:
                x = C[(C.ev_day == last) & (C.county == name)]; y = B[(B.ev_day == last) & (B.county == name)]
                if len(x): r.update(pct_rv=round(100 * x.total_cum.iloc[0] / x.registered.iloc[0], 2) if x.registered.iloc[0] else None,
                                    mail_pct=round(100 * x.mail_cum.fillna(0).iloc[0] / x.total_cum.iloc[0], 1) if x.total_cum.iloc[0] else None)
                if len(y): r.update(pct_rv_2022=round(100 * y.total_cum.iloc[0] / y.registered.iloc[0], 2) if y.registered.iloc[0] else None)
            counties[name] = r
    src = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    out = {"asof": str(dt.date.today()), "status": "early voting" if last else "before early voting (2022 baseline only)",
           "latest_ev_day": int(last) if last else None,
           "election": {"2026": {k: str(v) for k, v in CAL[2026].items()}, "2022": {k: str(v) for k, v in CAL[2022].items()}},
           "groups": {k: {"label": lab, "counties": cs, "pres2024_R_margin": _group_margin(pres, cs) if pres else None}
                      for k, (lab, cs) in groups.items()},
           "days": days, "counties": counties,
           "sources": {"2026": CIVIX_UI, "2022": src.get("tx_early_2022", {}).get("source", LEGACY + "/getElectionDetails.do"),
                       "2022_retrieved": src.get("tx_early_2022", {}).get("retrieved_utc"),
                       "pres2024": "MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024, doi:10.7910/DVN/VOQCHQ (CC0)",
                       "credit": "Texas Secretary of State, Elections Division (early voting turnout by county, as reported by the counties)"},
           "notes": NOTES}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    return out


# ------------------------------------------------------------------------------------------------------------------------
# one-time baseline (GitHub Actions only)

class _Legacy:
    """Cookie session for the legacy Struts portal: identifying User-Agent, one request at a time, 2 s apart."""
    def __init__(self, save: Path):
        from . import fetch as F
        self.F, self.save = F, save
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        save.mkdir(parents=True, exist_ok=True)

    def req(self, action: str, data: dict | None = None, name: str | None = None) -> str:
        time.sleep(2)
        url = f"{LEGACY}/{action}"
        h = {"User-Agent": self.F.UA, "Referer": f"{LEGACY}/getElectionDetails.do"}
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        if body: h["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            with self.op.open(urllib.request.Request(url, data=body, headers=h), timeout=120) as r:
                b = r.read()
        except urllib.error.HTTPError as e:
            snippet = re.sub(r"\s+", " ", e.read(600).decode("utf-8", "replace"))
            raise RuntimeError(f"{action}: HTTP {e.code} (server {e.headers.get('Server')}, cf-ray {e.headers.get('CF-RAY')}): {snippet[:300]}")
        self.F.count(url, len(b))
        if name: (self.save / name).write_bytes(b)    # county tables only (no voter data); kept for the run's artifact
        return b.decode("utf-8", "replace")


def _norm(h) -> str:
    h = " ".join(str(x) for x in h) if isinstance(h, tuple) else str(h)
    return re.sub(r"\s+", " ", h.upper().replace("-", " ").replace("_", " ")).strip()


def parse_legacy_table(html: str) -> pd.DataFrame:
    """County turnout table of getEVDetails.do: columns located by their headers (County, Registered Voters, # In Person
    On <date>, Cumulative In-Person Voters, Cumulative % In-Person, Cumulative By Mail Voters, Cumulative In-Person And
    Mail Voters, Cumulative Percent Early Voting)."""
    best = None
    for t in pd.read_html(io.StringIO(html), thousands=",", flavor="lxml"):
        if all(isinstance(c, int) for c in t.columns) and len(t):      # header in the first row instead of <th>
            t = t.iloc[1:].set_axis([str(x) for x in t.iloc[0]], axis=1)
        cols = [_norm(c) for c in t.columns]
        if any("COUNTY" in c for c in cols) and any("MAIL" in c for c in cols) and (best is None or len(t) > len(best[0])):
            best = (t, cols)
    if best is None: raise ValueError("no county turnout table in the page")
    t, cols = best; m = {}
    for i, h in enumerate(cols):
        if "%" in h or "PERCENT" in h: continue
        if "COUNTY" in h: m.setdefault("county", i)
        elif "REGISTERED" in h or h == "VOTERS": m.setdefault("registered", i)
        elif "MAIL" in h and ("IN PERSON" in h or "TOTAL" in h or " AND " in h): m.setdefault("total_pub", i)
        elif "MAIL" in h: m.setdefault("mail_cum", i)
        elif "IN PERSON" in h and "CUMULATIVE" in h: m.setdefault("in_person_cum", i)
        elif "IN PERSON" in h: m.setdefault("in_person_on_date", i)
    need = {"county", "registered", "in_person_cum", "mail_cum"}
    if not need <= set(m): raise ValueError(f"columns not found: {sorted(need - set(m))} in {cols}")
    D = pd.DataFrame({k: t.iloc[:, i] for k, i in m.items()})
    D["county"] = D["county"].astype(str).str.strip().str.upper()
    for k in m:
        if k != "county": D[k] = D[k].map(_num)
    return D


def _baseline_2022(force: bool) -> dict:
    if BASE_2022.exists() and not force:
        print("  2022 baseline already committed; skipped (--force to rebuild)")
        return json.loads(SOURCES.read_text()).get("tx_early_2022", {}) if SOURCES.exists() else {}
    S = _Legacy(RAW / "legacy2022")
    page = S.req("getElectionDetails.do", name="election_index.html")
    opts = re.findall(r'<option[^>]*value="?(\d+)"?[^>]*>\s*([^<]+?)\s*<', page)
    el = [(i, n) for i, n in opts if "2022" in n and "GENERAL" in n.upper() and "NOV" in n.upper() and "SPECIAL" not in n.upper()]
    if len(el) != 1: raise SystemExit(f"2022 general not found uniquely among {len(opts)} elections: {opts[:60]}")
    eid, ename = el[0]; print(f"  legacy portal: {ename} (idElection {eid})")
    page = S.req("getElectionEVDates.do", {"idElection": eid}, name=f"{eid}_dates.html")
    listed = sorted({dt.date.fromisoformat(x) for x in re.findall(r'value=["\']?(\d{4}-\d{2}-\d{2}) 00:00:00\.0', page)})
    evd = [d for d in listed if CAL[2022]["first"] <= d <= CAL[2022]["last"]]
    print(f"  {len(listed)} dates listed, {len(evd)} in the early-voting period: {evd[:1]} .. {evd[-1:]}")
    if not evd: raise SystemExit(f"no early-voting dates in getElectionEVDates.do (listed: {listed})")
    parts, checks = [], []
    for d in evd:
        html = S.req("getEVDetails.do", {"idElection": eid, "selectedDate": f"{d} 00:00:00.0", "electionDate": "",
                                         "earlyVoteFlag": "true", "downloadElectionFileCSVFlag": "false", "idTown": ""},
                     name=f"{eid}_{d}.html")
        T = parse_legacy_table(html)
        sw = T[T.county.isin(["STATEWIDE", "TOTAL", "TOTALS"])]
        T = T[~T.county.isin(["STATEWIDE", "TOTAL", "TOTALS", "", "NAN"])]
        if T.county.nunique() < 254: raise SystemExit(f"{d}: only {T.county.nunique()} counties parsed")
        T["date"] = d; parts.append(T)
        tot = T.in_person_cum.fillna(0) + T.mail_cum.fillna(0)
        chk = {"date": str(d), "counties": int(T.county.nunique()), "ev_sum": int(tot.sum())}
        if len(sw):
            chk["statewide_row"] = int(sw.in_person_cum.fillna(0).iloc[0] + sw.mail_cum.fillna(0).iloc[0])
        if "total_pub" in T: chk["rows_total_mismatch"] = int(((T.total_pub - tot).abs() > 0.5).sum())
        checks.append(chk); print("   ", chk)
    D = _with_days(pd.concat(parts, ignore_index=True), 2022)
    cols = ["ev_day", "date", "days_out", "county", "registered", "in_person_on_date", "in_person_cum", "mail_cum", "total_cum"]
    for c in cols[4:]:
        if c not in D: D[c] = float("nan")
        D[c] = D[c].round().astype("Int64")
    D = D.sort_values(["ev_day", "county"])[cols]
    D.to_csv(BASE_2022, index=False)
    print(f"  wrote {BASE_2022.relative_to(ROOT)}: {len(D)} rows, {D.county.nunique()} counties, {D.ev_day.nunique()} days")
    return {"file": "data/static/tx_early_2022.csv", "source": f"{LEGACY}/getElectionDetails.do",
            "publisher": "Texas Secretary of State, Elections Division (Early Voting Turnout 2020-2025 portal)",
            "election": ename, "idElection": eid, "early_voting_dates": [str(d) for d in evd],
            "retrieved_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "rows": int(len(D)), "counties": int(D.county.nunique()), "days": int(D.ev_day.nunique()),
            "dates_listed": [str(d) for d in listed], "checks": checks,
            "terms": "public records supplied by the county election officials (SOS: data are posted as reported by the "
                     "counties); information on Texas state websites may be copied with attribution and without implying "
                     "endorsement (SOS link policy, sos.state.tx.us/linkpolicy.shtml). Derived county totals only; no voter "
                     "rosters were requested."}


def _pres_2024(force: bool = False) -> dict:
    from . import fetch as F
    if PRES_2024.exists() and not force:
        print("  2024 county results already committed; skipped")
        return json.loads(SOURCES.read_text()).get("tx_county_pres2024", {}) if SOURCES.exists() else {}
    try:
        raw = F.get(MEDSL_COUNTY, ROOT / "data" / "raw" / "mit" / "countypres_2000_2024.tab", timeout=300)[0]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"MEDSL HTTP {e.code}: {e.read(400).decode('utf-8', 'replace')}")
    sep = "\t" if b"\t" in raw[:2000] else ","
    d = pd.read_csv(io.BytesIO(raw), sep=sep, dtype={"county_fips": str}, keep_default_na=False, low_memory=False)
    d["year"] = pd.to_numeric(d["year"], errors="coerce")
    d = d[(d.year == 2024) & (d.state_po == "TX")].copy()
    d["candidatevotes"] = pd.to_numeric(d.candidatevotes, errors="coerce").fillna(0)
    d["county"] = d.county_name.str.upper().str.strip()
    g = d.groupby("county")
    P = pd.DataFrame({"rep": d[d.party == "REPUBLICAN"].groupby("county").candidatevotes.sum(),
                      "dem": d[d.party == "DEMOCRAT"].groupby("county").candidatevotes.sum(),
                      "total": g.candidatevotes.sum()}).fillna(0).astype(int).reset_index()
    P["margin_r"] = (100 * (P.rep - P.dem) / P.total).round(2)
    if len(P) != 254: raise SystemExit(f"MEDSL 2024 TX: {len(P)} counties (expected 254)")
    missing = sorted({c for _, cs in GROUPS.values() for c in cs} - set(P.county))
    if missing: raise SystemExit(f"group counties missing from MEDSL: {missing}")
    P.to_csv(PRES_2024, index=False)
    for key, (lab, cs) in GROUPS.items():
        print(f"  {lab}: " + ", ".join(f"{c.title()} {P.set_index('county').margin_r[c]:+.1f}" for c in cs))
    return {"file": "data/static/tx_county_pres2024.csv", "source": "doi:10.7910/DVN/VOQCHQ, file countypres_2000-2024 (id 13573089)",
            "publisher": "MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024 (CC0)",
            "retrieved_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "statewide_R_margin": round(100 * (P.rep.sum() - P.dem.sum()) / P.total.sum(), 2)}


def _probe_2026() -> dict:
    """Records what the SOS portal lists for the 2026 general (id, early-voting dates); one request."""
    from . import fetch as F
    try:
        idx = _decode(F.open_url(_civix_url(type="EVR_ELECTION"), timeout=60))
    except Exception as e:
        return {"error": str(e)[:200]}
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "election_index_probe.json").write_text(json.dumps(idx, indent=1)[:2_000_000])
    el = find_2026(idx)
    names = [f'{e.get("election_name")} ({e.get("election_date")})' for e in (idx or {}).get("elections") or []]
    if not el: return {"found": False, "elections_listed": names}
    ev = [x["date"] for x in el.get("early_voting_dates") or []]
    print(f"  2026 general in the SOS index: {el.get('election_name')} id {el.get('id')}, EV dates {ev[:1]}..{ev[-1:]} ({len(ev)})")
    return {"found": True, "id": el.get("id"), "election_name": el.get("election_name"), "early_voting_dates": ev,
            "counties": len(el.get("counties") or []), "index_date_updated": idx.get("date_updated"),
            "retrieved_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}


def baseline(force: bool = False) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("the baseline downloads run in GitHub Actions only (.github/workflows/tx-early-baseline.yml)")
    from . import fetch as F
    src = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    errors = []
    for key, fn in (("civix_2026_probe", _probe_2026), ("tx_early_2022", lambda: _baseline_2022(force)),
                    ("tx_county_pres2024", lambda: _pres_2024(force))):
        try: src[key] = fn()
        except (Exception, SystemExit) as e:
            errors.append(f"{key}: {str(e)[:300]}"); print(f"!! {key} failed: {str(e)[:300]}")
    src = {k: v for k, v in src.items() if v}
    if src: SOURCES.write_text(json.dumps(src, indent=1) + "\n")   # only parts that built (nothing written otherwise)
    F.report()
    if errors: raise SystemExit("baseline incomplete: " + " | ".join(errors))


if __name__ == "__main__":
    if sys.argv[1:2] == ["baseline"]:
        baseline(force="--force" in sys.argv)
    else:
        print("TX snapshots:", [p.name for p in snapshot()])
        s = summary()
        if s:
            print(f"TX early vote ({s['status']}):")
            for d in s["days"]:
                if d["groups"]["statewide"]["ev"] is None and s["latest_ev_day"]: continue
                print(f"  day {d['ev_day']:2d} ({d['days_out']} out): " + "  ".join(
                    f"{k} {v['pct_rv'] if v['pct_rv'] is not None else '-'}% v {v['pct_rv_2022']}%" for k, v in d["groups"].items()))
