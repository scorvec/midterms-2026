"""Texas early-vote turnout tracker (companion to fl_early / pa_early); diagnostic only, nothing feeds the forecast.

Texas has no party registration, so this counts turnout only: early votes (in person + by mail, cumulative) as a share of
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
    a Struts form whose county table would give the day-by-day series data/static/tx_early_2022.csv (summary() reads it if
    it ever exists; not built: the portal blocks automated access, below).
  * county groups are checked against the 2024 presidential result by county: MIT Election Data and Science Lab 2024
    precinct returns, doi:10.7910/DVN/NYTPDU (CC0), summed by county -> data/static/tx_county_pres2024.csv.

Calendar: Election Code 85.001 starts early voting on the 17th day before Election Day (moved to the next business day
when that is a weekend) and ends on the 4th day before. Nov 3, 2026 - 17 = Sat Oct 17 -> Mon Oct 19; Nov 8, 2022 - 17 = Sat
Oct 22 -> Mon Oct 24. Both periods are 12 days (Mon to the second Fri, weekends included), so early-voting day k falls on
the same weekday and the same number of days before Election Day in both years. SB 2753 (2025) would move early voting to
12 days before through the day before Election Day, but only after the SOS publishes its implementation report (due by
Aug 1, 2027); the SOS calendar for Nov 3, 2026 keeps Oct 19-30.

Off by default: nothing runs or fetches unless TX_EARLY=on (repository variable TX_EARLY in daily.yml). The 2022
comparison is planned as the final 2022 early vote by county (see the TODO in _final_2022); the day-by-day 2022 portal
blocks automated access.

    TX_EARLY=on python -m midterms.tx_early            # daily: snapshot (from Oct 19) + summary -> web/data/tx_early.json
    TX_EARLY=on python -m midterms.tx_early baseline   # one-time, GitHub Actions only (needs a workflow; none is installed)
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sys
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

CAL = {
    2026: {"election_day": dt.date(2026, 11, 3), "first": dt.date(2026, 10, 19), "last": dt.date(2026, 10, 30)},
    2022: {"election_day": dt.date(2022, 11, 8), "first": dt.date(2022, 10, 24), "last": dt.date(2022, 11, 4)},
}

# County groups (SOS spelling, upper case). The two suburban groups are fixed here, not chosen from the turnout.
GROUPS = {
    "big_dem": ("Big Democratic counties", ["HARRIS", "DALLAS", "TRAVIS", "BEXAR", "EL PASO"]),
    # Republican-leaning suburbs / exurbs: Collin and Denton (the two large DFW suburbs; Trump +11.1 and +13.1 in 2024) and
    # Montgomery (+45.5), plus every other county of the Dallas-Fort Worth, Houston and San Antonio metro areas outside the
    # core counties with more than 60,000 presidential votes in 2024 that Trump carried by 15 points or more
    # (tx_county_pres2024.csv): Rockwall +40.7, Parker +66.4, Kaufman +27.8, Ellis +31.0, Johnson +51.3 (DFW), Brazoria +19.7,
    # Galveston +27.4 (Houston), Comal +45.5, Guadalupe +29.4 (San Antonio). Austin's collar has none (Williamson +2.4, Hays
    # -5.6). Free-standing metros (Lubbock, Smith, McLennan, Brazos, Randall, Grayson ...) are not suburbs and stay out;
    # Tarrant (+5.1) and Fort Bend (-1.6) are the swing group below.
    "gop_suburbs": (
        "Republican-leaning suburbs and exurbs",
        [
            "COLLIN",
            "DENTON",
            "MONTGOMERY",
            "ROCKWALL",
            "PARKER",
            "KAUFMAN",
            "ELLIS",
            "JOHNSON",
            "BRAZORIA",
            "GALVESTON",
            "COMAL",
            "GUADALUPE",
        ],
    ),
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
    if not b or not b.strip():
        return None
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

    if os.environ.get("TX_EARLY", "off") != "on":
        return []  # off unless TX_EARLY=on
    today = today or dt.date.today()
    if not CAL[2026]["first"] <= today <= CAL[2026]["election_day"] + dt.timedelta(days=14):
        return []
    RAW.mkdir(parents=True, exist_ok=True)
    got = []
    try:
        idx = _decode(
            F.get(_civix_url(type="EVR_ELECTION"), RAW / "election_index.download", min_age_h=20, timeout=60)[0]
        )
    except Exception as e:
        print("  TX election index unavailable:", str(e)[:120])
        return []
    el = find_2026(idx)
    if not el:
        return []
    (RAW / "election.json").write_text(json.dumps({k: v for k, v in el.items() if k != "counties"}))
    dates = sorted(
        {dt.datetime.strptime(x["date"], "%m/%d/%Y").date() for x in el.get("early_voting_dates") or []}
    ) or list(pd.date_range(CAL[2026]["first"], CAL[2026]["last"]).date)
    for d in [d for d in dates if d <= today]:
        try:
            raw, changed = F.get(
                _civix_url(type="EVR_EARLYVOTING", electionId=el["id"], electionDate=_mdy(d)),
                RAW / f"latest_{d:%Y%m%d}.download",
                min_age_h=20,
                timeout=60,
            )
            j = _decode(raw)
        except Exception:
            continue  # not posted yet (empty body / S3 key error): next run
        if not j or not j.get("turnout_by_county"):
            continue
        p = RAW / f"ev{d:%Y%m%d}_got{today:%Y%m%d}.json"
        if changed or not list(RAW.glob(f"ev{d:%Y%m%d}_got*.json")):
            p.write_text(json.dumps(j, separators=(",", ":")))
            got.append(p)
    return got


def _num(x):
    try:
        return float(str(x).replace(",", "")) if x not in (None, "", " ") else float("nan")
    except ValueError:
        return float("nan")


def series_2026() -> pd.DataFrame:
    """Latest version of every early-voting day held in data/raw/tx_stats."""
    latest = {}
    for p in sorted(RAW.glob("ev*_got*.json")):
        latest[p.stem.split("_")[0][2:]] = p  # sorted: the newest retrieval wins
    rows = []
    for day, p in sorted(latest.items()):
        d = dt.datetime.strptime(day, "%Y%m%d").date()
        rows.extend(
            {
                "date": d,
                "county": str(c.get("name", "")).strip().upper(),
                "registered": _num(c.get("registered_voters")),
                "in_person_on_date": _num(c.get("in_person_votes_on_date")),
                "in_person_cum": _num(c.get("total_in_person_votes_for_election")),
                "mail_cum": _num(c.get("total_mail_votes_for_election")),
                "retrieved": p.stem.split("got")[1],
            }
            for c in json.loads(p.read_text()).get("turnout_by_county", [])
        )
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
    if not len(g):
        return None
    ev, rv, mail = g.total_cum.sum(), g.registered.sum(), g.mail_cum.fillna(0).sum()
    return {
        "ev": int(ev),
        "rv": int(rv),
        "pct_rv": round(100 * ev / rv, 2) if rv else None,
        "share": round(100 * ev / state_ev, 2) if state_ev else None,
        "mail_pct": round(100 * mail / ev, 1) if ev else None,
        "n_counties": int(g.county.nunique()),
    }


def _pres_margins() -> dict:
    if not PRES_2024.exists():
        return {}
    P = pd.read_csv(PRES_2024)
    return {r.county: r for r in P.itertuples()}


def _group_margin(pres: dict, counties: list[str] | None):
    rs = list(pres.values()) if counties is None else [pres[c] for c in counties if c in pres]
    tot = sum(r.total for r in rs)
    return round(100 * (sum(r.rep for r in rs) - sum(r.dem for r in rs)) / tot, 1) if tot else None


def summary() -> dict | None:
    C = series_2026()
    if BASE_2022.exists():
        B = pd.read_csv(BASE_2022, parse_dates=["date"])
        B["date"] = B["date"].dt.date
    else:  # the 2026 side still runs; the 2022 fields stay null
        B = pd.DataFrame(columns=["ev_day", "county", "registered", "in_person_cum", "mail_cum", "total_cum"])
    if not len(C) and not len(B):
        print("  TX: nothing to summarise yet (no 2026 file, no 2022 baseline)")
        return None
    pres = _pres_margins()
    groups = {"statewide": ("Statewide", None), **GROUPS}
    days, prev26 = [], None
    for k in range(1, 13):
        b = B[B.ev_day == k]
        c = C[C.ev_day == k] if len(C) else C
        s22 = b.total_cum.sum() if len(b) else None
        s26 = c.total_cum.sum() if len(c) else None
        day = {
            "ev_day": k,
            "date": str(CAL[2026]["first"] + dt.timedelta(days=k - 1)),
            "date_2022": str(CAL[2022]["first"] + dt.timedelta(days=k - 1)),
            "days_out": (CAL[2026]["election_day"] - CAL[2026]["first"]).days - (k - 1),
            "groups": {},
        }
        for key, (_, counties) in groups.items():
            a22, a26 = _agg(b, counties, s22) if len(b) else None, _agg(c, counties, s26) if len(c) else None
            day["groups"][key] = {
                "ev": a26 and a26["ev"],
                "pct_rv": a26 and a26["pct_rv"],
                "share": a26 and a26["share"],
                "mail_pct": a26 and a26["mail_pct"],
                "ev_2022": a22 and a22["ev"],
                "pct_rv_2022": a22 and a22["pct_rv"],
                "share_2022": a22 and a22["share"],
                "mail_pct_2022": a22 and a22["mail_pct"],
                "diff_pts": round(a26["pct_rv"] - a22["pct_rv"], 2)
                if a26 and a22 and None not in (a26["pct_rv"], a22["pct_rv"])
                else None,
                "ratio": round(a26["pct_rv"] / a22["pct_rv"], 3) if a26 and a22 and a22["pct_rv"] else None,
                "share_shift": round(a26["share"] - a22["share"], 2)
                if a26 and a22 and None not in (a26["share"], a22["share"])
                else None,
            }
        if len(c):
            day["counties_2026"] = int(c.county.nunique())
            day["retrieved"] = max(c.retrieved)
            if prev26 is not None:  # counties whose total did not move from the day before (roster lag)
                m = c.merge(prev26[["county", "total_cum"]], on="county", suffixes=("", "_prev"))
                stale = m[m.total_cum <= m.total_cum_prev]
                named = sorted(set(stale.county) & {x for _, cs in GROUPS.values() for x in cs})
                day["stale_counties"] = len(stale)
                day["stale_named"] = named
            prev26 = c
        else:
            prev26 = None  # a missing day: no day-before comparison for the next one
        days.append(day)
    last = max(C.ev_day) if len(C) else None
    counties = {}
    for _, cs in GROUPS.values():
        for name in cs:
            r = {"pres2024_R_margin": round(float(pres[name].margin_r), 1) if name in pres else None}
            if last:
                x = C[(C.ev_day == last) & (C.county == name)]
                y = B[(B.ev_day == last) & (B.county == name)]
                if len(x):
                    r.update(
                        pct_rv=round(100 * x.total_cum.iloc[0] / x.registered.iloc[0], 2)
                        if x.registered.iloc[0]
                        else None,
                        mail_pct=round(100 * x.mail_cum.fillna(0).iloc[0] / x.total_cum.iloc[0], 1)
                        if x.total_cum.iloc[0]
                        else None,
                    )
                if len(y):
                    r.update(
                        pct_rv_2022=round(100 * y.total_cum.iloc[0] / y.registered.iloc[0], 2)
                        if y.registered.iloc[0]
                        else None
                    )
            counties[name] = r
    src = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    out = {
        "asof": str(dt.date.today()),
        "status": "early voting" if last else "before early voting (2022 baseline only)",
        "baseline_2022": bool(len(B)),
        "latest_ev_day": int(last) if last else None,
        "election": {
            "2026": {k: str(v) for k, v in CAL[2026].items()},
            "2022": {k: str(v) for k, v in CAL[2022].items()},
        },
        "groups": {
            k: {"label": lab, "counties": cs, "pres2024_R_margin": _group_margin(pres, cs) if pres else None}
            for k, (lab, cs) in groups.items()
        },
        "days": days,
        "counties": counties,
        "sources": {
            "2026": CIVIX_UI,
            "2022": src.get("tx_early_2022", {}).get("source", LEGACY + "/getElectionDetails.do"),
            "2022_retrieved": src.get("tx_early_2022", {}).get("retrieved_utc"),
            "pres2024": "MIT Election Data and Science Lab, 2024 precinct returns summed by county, doi:10.7910/DVN/NYTPDU (CC0)",
            "credit": "Texas Secretary of State, Elections Division (early voting turnout by county, as reported by the counties)",
        },
        "notes": NOTES,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    return out


# ------------------------------------------------------------------------------------------------------------------------
# one-time baseline (GitHub Actions only)


def _pres_2024(force: bool = False) -> dict:
    """2024 president by county from MEDSL's 2024 precinct returns (doi:10.7910/DVN/NYTPDU, CC0), the same file the
    state-legislature build uses (stateleg_build.dv_get / medsl_load; the workflow restores that build's raw cache, so
    normally nothing is downloaded). The county file (doi:10.7910/DVN/VOQCHQ) sits behind a Dataverse guestbook."""
    if PRES_2024.exists() and not force:
        print("  2024 county results already committed; skipped")
        return json.loads(SOURCES.read_text()).get("tx_county_pres2024", {}) if SOURCES.exists() else {}
    from . import stateleg_build as B

    p = B.dv_get("m2024", "2024-tx-precinct-general\\.(tab|csv)")
    if p is None:
        raise RuntimeError("MEDSL 2024 Texas precinct file not found in doi:10.7910/DVN/NYTPDU")
    pr = B.pres_rows(B.medsl_load(p, "TX"))
    pr = pr.assign(county=pr["county_name"].str.upper().str.strip())
    v = pr.groupby(["county", "party"])["votes"].sum().unstack(fill_value=0.0)
    for c in "DRO":
        if c not in v:
            v[c] = 0.0
    P = (
        pd.DataFrame({"rep": v["R"], "dem": v["D"], "total": v["D"] + v["R"] + v["O"]})
        .round()
        .astype(int)
        .reset_index()
    )
    P["margin_r"] = (100 * (P.rep - P.dem) / P.total).round(2)
    if len(P) != 254:
        raise RuntimeError(f"MEDSL 2024 TX: {len(P)} counties (expected 254)")
    missing = sorted({c for _, cs in GROUPS.values() for c in cs} - set(P.county))
    if missing:
        raise RuntimeError(f"group counties missing from MEDSL: {missing}")
    P.to_csv(PRES_2024, index=False)
    m = P.set_index("county").margin_r
    for lab, cs in GROUPS.values():
        print(f"  {lab}: " + ", ".join(f"{c.title()} R{m[c]:+.1f}" for c in cs))
    sub = P[(P.total > 60000) & (P.margin_r >= 20)].sort_values("total", ascending=False)
    print(
        "  counties with > 60,000 presidential votes and Trump +20 or more:",
        ", ".join(f"{r.county.title()} {r.margin_r:+.1f}" for r in sub.itertuples()),
    )
    return {
        "file": "data/static/tx_county_pres2024.csv",
        "source": "doi:10.7910/DVN/NYTPDU, file 2024-tx-precinct-general (precincts summed by county)",
        "publisher": "MIT Election Data and Science Lab, 2024 precinct-level returns (CC0)",
        "retrieved_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"),
        "statewide_R_margin": round(100 * (P.rep.sum() - P.dem.sum()) / P.total.sum(), 2),
        "total_votes": int(P.total.sum()),
    }


def _final_2022(force: bool = False) -> dict:
    """2022 general, final early vote by county. Not built yet: the tracker is off (TX_EARLY).

    TODO (when switched on): build data/static/tx_early_2022_final.csv (county, registered, ev_in_person, ev_mail, ev_total)
      * Blocked from GitHub Actions on 2026-10-05 (Cloudflare "Just a moment..." challenge, HTTP 403, honest User-Agent; not
        to be evaded): earlyvoting.texas-election.com (2020-2025 early-voting portal, idElection 47009) and
        results.texas-election.com (static/data/election/47009/242/County.json, static/data/Reports/47009/VoterTurnoutReport.pdf).
      * Candidate on an open SOS host (seen with a web reader, not yet fetched from Actions): "Voter Registration and
        Unofficial Early Voting Figures by County", https://www.sos.state.tx.us/elections/historical/counties.shtml ->
        one page per county (<name>.shtml), columns YEAR | Reg Voters | Voted | Voted % | Early Vote | EV % (Harris 2022:
        2,568,463 | 1,102,418 | 42.92% | 752,491 | 29.30%). Early Vote comes from unofficial election-night returns and has
        no in-person / mail split (ev_in_person, ev_mail would stay empty, or come from the EAC 2022 EAVS F1f / F1d with its
        own definitions). 255 sequential requests (index + counties), paced.
      * Checks: summed Reg Voters = 17,672,143 (SOS 70-92.shtml, Nov 2022; the same total as the early-voting portal's
        registered column); summed early vote against the ~5.4 million the SOS reported at the close of early voting.
      * summary(): on each 2026 day pct_rv against the 2022 final pct_rv and ev_2026 / ev_2022_final; after Oct 30 the
        like-for-like final comparison (turnout, group shares of the statewide early vote, composition shift); the per-day
        *_2022 fields dropped (null) with a note that the 2022 daily series is unavailable (portal blocks automated access).
    """
    raise RuntimeError("2022 final early vote not built yet (see the TODO in tx_early._final_2022)")


def baseline(force: bool = False) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("the baseline downloads run in GitHub Actions only (no workflow is installed yet)")
    from . import fetch as F

    src = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    errors = []
    for key, fn in (
        ("tx_early_2022_final", lambda: _final_2022(force)),
        ("tx_county_pres2024", lambda: _pres_2024(force)),
    ):
        try:
            src[key] = fn()
        except (Exception, SystemExit) as e:
            errors.append(f"{key}: {str(e)[:300]}")
            print(f"!! {key} failed: {str(e)[:300]}")
    src = {k: v for k, v in src.items() if v}
    if src:
        SOURCES.write_text(json.dumps(src, indent=1) + "\n")  # only parts that built (nothing written otherwise)
    F.report()
    if errors:
        raise SystemExit("baseline incomplete: " + " | ".join(errors))


if __name__ == "__main__":
    if os.environ.get("TX_EARLY", "off") != "on":
        print("Texas early-vote tracker is off; TX_EARLY=on switches it on")
        sys.exit(0)
    if sys.argv[1:2] == ["baseline"]:
        baseline(force="--force" in sys.argv)
    else:
        print("TX snapshots:", [p.name for p in snapshot()])
        s = summary()
        if s:
            print(f"TX early vote ({s['status']}):")
            for d in s["days"]:
                if d["groups"]["statewide"]["ev"] is None and s["latest_ev_day"]:
                    continue
                print(
                    f"  day {d['ev_day']:2d} ({d['days_out']} out): "
                    + "  ".join(
                        f"{k} {v['pct_rv'] if v['pct_rv'] is not None else '-'}% v {v['pct_rv_2022']}%"
                        for k, v in d["groups"].items()
                    )
                )
