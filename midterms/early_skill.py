"""Does the early vote's party mix predict where the final polls miss? A pre-registered backtest over states and cycles.

    python -m midterms.early_skill fetch     # GitHub Actions only (early-skill.yml): the inputs that are not committed
    python -m midterms.early_skill           # the test -> data/static/early_skill.json

An early ballot is not counted, so its party mix adds to the forecast only if it says something the polls do not: where they
will miss. Readings such as "Democratic primary voters lead the first day of early voting by 8 points, against 11 behind four
years ago" compare this cycle's early ballots with the same point of the last cycle of the same kind. This module asks whether
that change has come before the polls' misses in the past.

PRE-REGISTRATION (written 2026-10-20, before any of the pooled numbers were computed; North Carolina's ballots had already been
examined by nc_tracker.gate - a different measure, net of the rolls, at 17 / 10 / 5 days out: five cycles, no lead
significant - so for North Carolina this is a re-test, not a blind one)

  Unit        one state x cycle. Its outcome is the mean, over the state's President, Senate and Governor general races that
              cycle with a Democrat against a Republican, of the final poll average's miss: the actual margin minus the poll
              average, D minus R in points of the total vote. Averaging gives every state-cycle one weight, however many races
              it had, and keeps its races (which share one polling miss) from counting as separate evidence.
  Poll average the mean D v R margin of the polls that ended within 21 days of Election Day. Through 2022: 538's rated polls
              (raw_polls: margin_poll, margin_actual), as nc_tracker reads them. 2024 is not in raw_polls, so its polls are the
              538 / ABC News archives (the Senate and governor copies bootstrap keeps, the presidential one from the same
              Internet Archive capture date): general-election questions naming both nominees, the versions of one survey
              averaged (as gov_backtest reads them). 2024 results: MIT Election Data and Science Lab, President and Senate
              1976-2024 (CC0); governors data/static/gov_results_2024.csv.
  Predictor   lean_L(y) - lean_L(c): lean = 100 x (D - R) / all early ballots banked by L days before that cycle's own
              Election Day (registered party), c = the same kind of cycle four years earlier (midterm against midterm,
              presidential against presidential). L = 15 (the primary lead: the Texas first-day reading of 2026), L = 7.
  Test        OLS  miss = a + b x predictor  at each lead; two-sided p of b from the t distribution; Holm over the two leads
              at alpha 0.05. 90 % interval of b by a bootstrap over state-cycles (and over cycles, reported beside it). Leave one
              cycle out (all of its states together): fit a, b on the other cycles and predict the held-out misses; RMSE
              against two poll-only references, the average as published (miss 0) and the average plus the training cycles'
              mean miss (intercept only).
  Verdict     skill = b significant after Holm AND a lower out-of-sample RMSE than both references. A significant b of the
              opposite sign to the usual reading (b < 0: a more Democratic early vote before polls that ran too Democratic) is
              reported as such and would need a mechanism before any use. Anything less leaves the forecast as it is.
  Beside it, not tested: the two-year comparison (c = y - 2); North Carolina's lean net of the rolls (nc_tracker's measure);
              the predictor against the change in the actual margin (the reading people make of it).

Coverage (official sources, free; every request in GitHub Actions; aggregates only, no voter row is kept):
  NC  NCSBE absentee files 2008-2024 (mail + one-stop: return date, registered party), reduced by nc_tracker into
      data/static/nc_tracker/party_pace.csv: units 2012, 2014, 2016, 2020, 2022, 2024 (2018 had no statewide race).
  PA  Department of State mail-ballot request datasets on data.pa.gov (Socrata, counted server-side by party and return date,
      2020 found by its catalogue title; PA has no in-person early voting): unit 2024 against 2020; 2022 two-year only.
  NV  Clark County mail returns by party 2022 / 2024 (nv_clark_early's baseline, about 70 % of the state's vote): no
      four-year unit; 2024 against 2022 in the two-year comparison.
  FL  the Division of Elections' statistics files exist only for the current election; a past cycle could enter only where
      the Internet Archive holds captures at exactly L days out in both years of a pair (fetch lists them).
  GA  not testable: the Secretary of State's files sit behind a bot check, the voter list with primary history is sold, and
      the 2022 / 2024 public dashboards show no composition (ga_early).
Not used: UF Election Lab tables (CC BY-NC-ND 4.0: no derived numbers may be published), modelled party scores, third-party
compilations.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from . import fetch as F

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
STATIC = ROOT / "data" / "static"
INPUTS = STATIC / "early_skill"  # committed aggregates written by fetch(): pa_returns.csv, fl_captures.json
OUT = STATIC / "early_skill.json"
RAW_POLLS = RAW / "538repo" / "raw_polls.csv"
ARCHIVE = RAW / "538"
PRES_ARCHIVE = ARCHIVE / "president_polls_historical.csv"
MIT_PRES = RAW / "mit" / "president_1976_2024.csv"
MIT_SEN = RAW / "mit" / "senate_1976_2024.csv"
GOV_2024 = STATIC / "gov_results_2024.csv"
NC_PACE = STATIC / "nc_tracker" / "party_pace.csv"
NC_REG = STATIC / "nc_tracker" / "party_reg.csv"

LEADS = (15, 7)  # days before Election Day: primary, secondary
ALPHA = 0.05
POLL_WINDOW = 21  # polls that ended within this many days of Election Day
N_BOOT = 10000
RACES = {"Pres-G": "U.S. President", "Sen-G": "U.S. Senate", "Gov-G": "Governor"}
EDAY = {
    2008: dt.date(2008, 11, 4),
    2010: dt.date(2010, 11, 2),
    2012: dt.date(2012, 11, 6),
    2014: dt.date(2014, 11, 4),
    2016: dt.date(2016, 11, 8),
    2018: dt.date(2018, 11, 6),
    2020: dt.date(2020, 11, 3),
    2022: dt.date(2022, 11, 8),
    2024: dt.date(2024, 11, 5),
}
PA_TITLE = "2020 General Election Mail Ballot Requests"  # the 2020 dataset, found by its catalogue title
PA_EXTRA = {2020: "2020-11-03"}  # Election Days of the PA datasets beyond pa_early's 2022 / 2024
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
PRES_URL = "https://projects.fivethirtyeight.com/polls/data/president_polls_historical.csv"
PRES_CAPTURE_NEAR = "20250306"  # the capture date of the archives bootstrap keeps
MIT_SEN_DOI = "doi:10.7910/DVN/PEJ5QU"  # MIT Election Data and Science Lab, U.S. Senate statewide 1976-2024
FL_PREFIX = "electionfiles.floridados.gov/countyballotreportfiles/"


def _json(url: str, timeout: int = 120):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": F.UA}), timeout=timeout) as r:
        b = r.read()
    F.count(url, len(b))
    return json.loads(b)


# ------------------------------------------------------------------------------------------------------------- fetch
def fetch_pa() -> dict:
    """Returned mail ballots by party and days out for 2020, 2022 and 2024 -> INPUTS/pa_returns.csv (counted server-side)."""
    from . import pa_early as PE

    cat = _json(
        "https://api.us.socrata.com/api/catalog/v1?"
        + urllib.parse.urlencode({"domains": "data.pa.gov", "q": PA_TITLE, "limit": 20})
    )
    hits = [r["resource"] for r in cat.get("results", []) if r["resource"]["name"].startswith(PA_TITLE)]
    if not hits:
        raise RuntimeError(f"data.pa.gov: no dataset titled '{PA_TITLE}...'")
    datasets = {2020: (hits[0]["id"], PA_EXTRA[2020])} | {y: PE.DATASETS[y] for y in (2022, 2024)}
    rows = []
    for y, (ds, ed) in datasets.items():
        cols = set(_json(f"https://data.pa.gov/resource/{ds}.json?$limit=1")[0])
        if not {"party", "ballotreturneddate"} <= cols:
            raise RuntimeError(f"data.pa.gov {ds} ({y}): columns {sorted(cols)}")
        r = PE.q(
            ds,
            "SELECT party, ballotreturneddate AS d, count(*) AS n WHERE ballotreturneddate IS NOT NULL GROUP BY party, d",
        )
        d = pd.DataFrame(r)
        d["day"] = pd.to_datetime(d["d"].astype(str).str[:10], errors="coerce", format="mixed")
        print(
            f"  PA {y} ({ds}): {int(d.n.astype(int).sum()):,} returned ballots, {int(d.day.isna().sum())} undated groups dropped"
        )
        d = d[d.day.notna()]
        rows.append(
            pd.DataFrame(
                {
                    "year": y,
                    "dataset": ds,
                    "days_out": (pd.Timestamp(ed) - d.day).dt.days,
                    "party": d.party.astype(str).str.upper().map(PE.PARTY).fillna("OTH"),
                    "n": d.n.astype(int),
                }
            )
        )
    out = pd.concat(rows).groupby(["year", "dataset", "days_out", "party"], as_index=False)["n"].sum()
    out = out[out.days_out >= 0]
    INPUTS.mkdir(parents=True, exist_ok=True)
    out.to_csv(INPUTS / "pa_returns.csv", index=False)
    return {str(y): ds for y, (ds, _) in datasets.items()}


def fetch_fl() -> dict:
    """Which past Florida statistics files the Internet Archive holds, with their capture dates -> INPUTS/fl_captures.json."""
    q = {
        "url": FL_PREFIX,
        "matchType": "prefix",
        "from": "2016",
        "to": "2023",
        "filter": "statuscode:200",
        "output": "json",
        "collapse": "digest",
    }
    rows = _json(f"{WAYBACK_CDX}?{urllib.parse.urlencode(q)}", timeout=300)
    caps = [dict(zip(rows[0], r)) for r in rows[1:]] if rows else []
    out = {}
    for c in caps:
        name = c["original"].rsplit("/", 1)[-1]
        out.setdefault(name, []).append(c["timestamp"][:8])
    res = {"query": q, "files": {k: sorted(set(v)) for k, v in sorted(out.items())}}
    INPUTS.mkdir(parents=True, exist_ok=True)
    (INPUTS / "fl_captures.json").write_text(json.dumps(res, indent=1))
    print(f"  FL: {len(caps)} captures of {len(out)} files, 2016-2023")
    return res


def fetch_pres_archive() -> str:
    """538's presidential poll archive from the Internet Archive capture nearest PRES_CAPTURE_NEAR -> data/raw (md5)."""
    q = {"url": PRES_URL, "from": "20250301", "to": "20250331", "filter": "statuscode:200", "output": "json"}
    rows = _json(f"{WAYBACK_CDX}?{urllib.parse.urlencode(q)}")
    ts = sorted((abs(int(r[1][:8]) - int(PRES_CAPTURE_NEAR)), r[1]) for r in rows[1:])
    if not ts:
        raise RuntimeError("no Internet Archive capture of the 538 presidential poll archive in March 2025")
    b, _ = F.get(f"https://web.archive.org/web/{ts[0][1]}id_/{PRES_URL}", PRES_ARCHIVE, timeout=600)
    md5 = hashlib.md5(b).hexdigest()
    print(f"  538 president archive: capture {ts[0][1]}, {len(b) / 1e6:.1f} MB, md5 {md5}")
    return f"{ts[0][1]} md5 {md5}"


def fetch_mit_senate() -> str:
    """MIT's Senate 1976-2024 results (Harvard Dataverse, CC0) -> data/raw/mit/senate_1976_2024.csv."""
    meta = _json(f"https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId={MIT_SEN_DOI}")
    files = [f["dataFile"] for f in meta["data"]["latestVersion"]["files"]]
    pick = [f for f in files if "senate" in (f.get("originalFileName") or f["filename"]).lower()]
    if not pick:
        raise RuntimeError(f"{MIT_SEN_DOI}: no Senate file among {[f['filename'] for f in files]}")
    f = pick[0]
    b, _ = F.get(f"https://dataverse.harvard.edu/api/access/datafile/{f['id']}?format=original", MIT_SEN, timeout=300)
    print(f"  MIT Senate: {f.get('originalFileName') or f['filename']} (file {f['id']}), {len(b) / 1e6:.1f} MB")
    return f"{MIT_SEN_DOI} file {f['id']}"


def fetch() -> None:
    src = {"pa_datasets": fetch_pa(), "pres_archive_capture": fetch_pres_archive(), "mit_senate": fetch_mit_senate()}
    fetch_fl()
    INPUTS.mkdir(parents=True, exist_ok=True)
    (INPUTS / "sources.json").write_text(json.dumps(src, indent=1))
    F.report()


# ------------------------------------------------------------------------------------------------------------- leans
def nc_lean(year: int, L: int, net: bool = False) -> float | None:
    """North Carolina: 100 x (D - R) / all mail + one-stop ballots banked by L days out; net=True subtracts the rolls'."""
    from . import nc_tracker as NT

    pace = pd.read_csv(NC_PACE)
    if year not in set(pace.year):
        return None
    if net:
        return float(NT.composition(year, L, pace, pd.read_csv(NC_REG)))
    b = pace[(pace.year == year) & (pace.dbe >= L)].groupby("party")["n"].sum()
    return float(100 * (b.get("DEM", 0) - b.get("REP", 0)) / b.sum()) if b.sum() else None


def pa_lean(year: int, L: int) -> float | None:
    f = INPUTS / "pa_returns.csv"
    if not f.exists():
        return None
    d = pd.read_csv(f)
    b = d[(d.year == year) & (d.days_out >= L)].groupby("party")["n"].sum()
    return float(100 * (b.get("DEM", 0) - b.get("REP", 0)) / b.sum()) if b.sum() else None


def nv_lean(year: int, L: int) -> float | None:
    """Clark County mail returns; None before the county's first bulk-logging day of that year (nv_clark_early)."""
    from . import nv_clark_early as NV

    if year not in NV.ELECTION_DAY:
        return None
    B = NV.baseline()
    first = NV.logged_from(B, year)
    if first is None or first < L:
        return None
    c = NV.at_days_out(B, year, "mail", L)
    return float(100 * (c["DEM"] - c["REP"]) / c["total"]) if c and c["total"] else None


LEAN = {"NC": nc_lean, "PA": pa_lean, "NV": nv_lean}


# ----------------------------------------------------------------------------------------------------------- outcomes
def races_raw_polls() -> pd.DataFrame:
    """Race-level poll averages and results 1998-2022 from raw_polls (D v R; cand1 is the Democrat in every such row)."""
    r = pd.read_csv(RAW_POLLS, low_memory=False)
    r = r[
        r.type_simple.isin(RACES)
        & r.cand1_party.eq("DEM")
        & r.cand2_party.eq("REP")
        & (r.time_to_election <= POLL_WINDOW)
        & r.location.isin(LEAN)
    ]
    g = r.groupby(["cycle", "location", "type_simple"]).agg(
        poll=("margin_poll", "mean"), actual=("margin_actual", "first"), n_polls=("poll_id", "nunique")
    )
    return g.reset_index().rename(columns={"cycle": "year", "location": "state", "type_simple": "race"})


def _results_2024() -> pd.DataFrame:
    """State, race, D - R margin of the total vote, D and R nominees' names for 2024."""
    rows = []
    for race, path, office in (("Pres-G", MIT_PRES, None), ("Sen-G", MIT_SEN, "US SENATE")):
        m = pd.read_csv(path, low_memory=False)
        m = m[(m.year == 2024) & m.state_po.isin(LEAN)]
        if office:
            m = m[m.office.eq(office) & m.stage.str.lower().eq("gen") & ~m.special.astype(str).str.upper().eq("TRUE")]
        if "mode" in m:  # a state reporting by vote mode also has a TOTAL row: counting both doubles it
            m = m[m["mode"].astype(str).str.upper().eq("TOTAL")]
        for st, g in m.groupby("state_po"):
            v = g.groupby("party_simplified")["candidatevotes"].sum()
            if "DEMOCRAT" not in v or "REPUBLICAN" not in v:
                continue
            nm = g.sort_values("candidatevotes").groupby("party_simplified")["candidate"].last()
            rows.append(
                {
                    "state": st,
                    "race": race,
                    "actual": 100 * (v["DEMOCRAT"] - v["REPUBLICAN"]) / g.totalvotes.max(),
                    "dn": nm["DEMOCRAT"],
                    "rn": nm["REPUBLICAN"],
                }
            )
    gv = pd.read_csv(GOV_2024)
    rows.extend(
        {"state": r.state, "race": "Gov-G", "actual": float(r.margin), "dn": r.dn, "rn": r.rn}
        for r in gv[gv.state.isin(LEAN)].itertuples()
    )
    return pd.DataFrame(rows)


def races_2024() -> pd.DataFrame:
    """2024 race-level poll averages from the 538 / ABC archives (both nominees named; versions of a survey averaged)."""
    from .data_prep import _abbr
    from .gov2026 import _surname

    def sur(name):  # MIT writes "TRUMP, DONALD J."; the archives and the governor table "Donald Trump"
        return _surname(str(name).split(",")[0])

    res = _results_2024()
    files = {"Pres-G": PRES_ARCHIVE, "Sen-G": ARCHIVE / "senate_polls_historical.csv"}
    files["Gov-G"] = ARCHIVE / "governor_polls_historical.csv"
    ed = pd.Timestamp(EDAY[2024])
    out = []
    for race, path in files.items():
        a = pd.read_csv(path, low_memory=False)
        a = a[(a.cycle == 2024) & a.stage.astype(str).str.lower().eq("general") & a.hypothetical.fillna(False).ne(True)]
        if "ranked_choice_reallocated" in a:
            a = a[a.ranked_choice_reallocated.fillna(False).ne(True)]
        a = a.assign(state=a.state.map(_abbr), sur=a.candidate_name.map(_surname))
        a = a[a.state.isin(LEAN)]
        a["end"] = pd.to_datetime(a.end_date, format="%m/%d/%y", errors="coerce")
        a = a[(a.end <= ed) & ((ed - a.end).dt.days <= POLL_WINDOW)]
        for r in res[res.race == race].itertuples():
            q = a[a.state == r.state]
            dn, rn = sur(r.dn), sur(r.rn)
            by_q = []
            for (pid, _qid), g in q.groupby(["poll_id", "question_id"]):
                d_, r_ = g[(g.party == "DEM") & (g.sur == dn)].pct, g[(g.party == "REP") & (g.sur == rn)].pct
                if len(d_) and len(r_):
                    by_q.append((pid, float(d_.max() - r_.max())))
            if not by_q:
                continue
            per_poll = pd.DataFrame(by_q, columns=["poll_id", "m"]).groupby("poll_id")["m"].mean()
            out.append(
                {
                    "year": 2024,
                    "state": r.state,
                    "race": race,
                    "poll": float(per_poll.mean()),
                    "actual": r.actual,
                    "n_polls": len(per_poll),
                }
            )
    return pd.DataFrame(out)


def races() -> pd.DataFrame:
    parts = [races_raw_polls()]
    if PRES_ARCHIVE.exists() and MIT_SEN.exists():
        parts.append(races_2024())
    R = pd.concat(parts, ignore_index=True)
    R["miss"] = R.actual - R.poll
    return R


# ------------------------------------------------------------------------------------------------------------- units
def units(gap: int = 4) -> pd.DataFrame:
    """One row per state x cycle with an outcome and an early-vote lean in both cycles at some lead."""
    R = races()
    rows = []
    for (st, y), g in R.groupby(["state", "year"]):
        c = y - gap
        if y not in EDAY or c not in EDAY:
            continue
        rec = {
            "state": st,
            "year": int(y),
            "compare": c,
            "races": ",".join(sorted(g.race)),
            "n_polls": int(g.n_polls.sum()),
            "poll": round(float(g.poll.mean()), 2),
            "actual": round(float(g.actual.mean()), 2),
            "miss": round(float(g.miss.mean()), 2),
        }
        prev = R[(R.state == st) & (R.year == c)]
        rec["actual_change"] = round(rec["actual"] - float(prev.actual.mean()), 2) if len(prev) else None
        for L in LEADS:
            a, b = LEAN[st](y, L), LEAN[st](c, L)
            rec[f"lean_{L}"], rec[f"lean_{L}_compare"] = _r(a), _r(b)
            rec[f"pred_{L}"] = _r(a - b) if a is not None and b is not None else None
            if st == "NC":
                a, b = nc_lean(y, L, net=True), nc_lean(c, L, net=True)
                rec[f"pred_net_{L}"] = _r(a - b) if a is not None and b is not None else None
        if any(rec[f"pred_{L}"] is not None for L in LEADS):
            rows.append(rec)
    return pd.DataFrame(rows)


def _r(x, k=2):
    return None if x is None else round(float(x), k)


# -------------------------------------------------------------------------------------------------------------- stats
def ols(x, y) -> dict:
    """y = a + b x: a, b, se(b), two-sided p of b (t, n - 2 df)."""
    from scipy import stats

    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    X = np.c_[np.ones(n), x]
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    s2 = res @ res / (n - 2)
    se = float(np.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1]))
    t = beta[1] / se if se > 0 else np.inf
    return {
        "n": n,
        "a": float(beta[0]),
        "b": float(beta[1]),
        "se": se,
        "p": float(2 * stats.t.sf(abs(t), n - 2)),
        "r": float(np.corrcoef(x, y)[0, 1]),
    }


def boot_b(x, y, groups, n_boot: int = N_BOOT, seed: int = 7) -> list[float] | None:
    """90 % percentile interval of the slope, resampling whole groups (units or cycles) with replacement."""
    x, y, groups = np.asarray(x, float), np.asarray(y, float), np.asarray(groups)
    ids = np.unique(groups)
    idx = {g: np.flatnonzero(groups == g) for g in ids}
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(n_boot):
        k = np.concatenate([idx[g] for g in rng.choice(ids, len(ids))])
        if np.ptp(x[k]) > 0:
            bs.append(np.polyfit(x[k], y[k], 1)[0])
    return [round(float(v), 3) for v in np.percentile(bs, [5, 95])] if len(bs) > n_boot / 2 else None


def loco(x, y, cycles) -> dict:
    """Leave one cycle out: RMSE of the held-out misses for the published average (0), the training mean and a + b x."""
    x, y, cycles = np.asarray(x, float), np.asarray(y, float), np.asarray(cycles)
    e0, e1, e2 = [], [], []
    for c in np.unique(cycles):
        tr, te = cycles != c, cycles == c
        if tr.sum() < 3 or np.ptp(x[tr]) == 0:
            continue
        b, a = np.polyfit(x[tr], y[tr], 1)
        e0.extend(y[te])
        e1.extend(y[te] - y[tr].mean())
        e2.extend(y[te] - (a + b * x[te]))

    def rmse(e):
        return round(float(np.sqrt(np.mean(np.square(e)))), 3) if e else None

    return {
        "n_scored": len(e0),
        "rmse_poll_only": rmse(e0),
        "rmse_intercept": rmse(e1),
        "rmse_with_predictor": rmse(e2),
    }


def holm(ps: dict) -> dict:
    """Holm-adjusted p for each key (step-down, monotone)."""
    order = sorted(ps, key=lambda k: ps[k])
    m, run, out = len(order), 0.0, {}
    for i, k in enumerate(order):
        run = max(run, min(1.0, (m - i) * ps[k]))
        out[k] = run
    return out


def test(U: pd.DataFrame, col: str = "pred") -> dict:
    res, ps = {}, {}
    for L in LEADS:
        u = U[U[f"{col}_{L}"].notna()]
        if len(u) < 4:
            res[str(L)] = {"n": len(u), "note": "too few units"}
            continue
        x, y = u[f"{col}_{L}"].to_numpy(float), u.miss.to_numpy(float)
        o = ols(x, y)
        res[str(L)] = {
            **{k: round(v, 4) if isinstance(v, float) else v for k, v in o.items()},
            "b_ci90_units": boot_b(x, y, (u.state + u.year.astype(str)).to_numpy()),
            "b_ci90_cycles": boot_b(x, y, u.year.to_numpy()),
            "cv": loco(x, y, u.year.to_numpy()),
            "states": sorted(set(u.state)),
            "cycles": sorted(int(v) for v in set(u.year)),
        }
        ac = u[u.actual_change.notna()]
        if len(ac) >= 4:
            c = ols(ac[f"{col}_{L}"], ac.actual_change)
            res[str(L)]["vs_actual_change"] = {
                "n": c["n"],
                "b": round(c["b"], 3),
                "r": round(c["r"], 3),
                "p": round(c["p"], 4),
            }
        ps[str(L)] = o["p"]
    for k, p in holm(ps).items():
        res[k]["p_holm"] = round(p, 4)
        res[k]["sign"] = "usual" if res[k]["b"] > 0 else "opposite"
        cv = res[k]["cv"]
        res[k]["skill"] = bool(
            p < ALPHA
            and cv["rmse_with_predictor"] is not None
            and cv["rmse_with_predictor"] < min(cv["rmse_poll_only"], cv["rmse_intercept"])
        )
    return res


def run() -> dict:
    U4, U2 = units(4), units(2)
    primary = test(U4)
    out = {
        "question": "does the change in the early vote's party mix from the last cycle of the same kind predict the final poll "
        "average's miss? (pre-registered: midterms/early_skill.py)",
        "built": dt.date.today().isoformat(),
        "outcome": f"mean over the state's President / Senate / Governor D v R races of actual minus the mean poll of the last "
        f"{POLL_WINDOW} days, D - R points of the total vote",
        "predictor": "change from the comparison cycle in 100 x (D - R) / early ballots banked by L days out",
        "skill": any(v.get("skill") for v in primary.values()),
        "primary": primary,
        "two_year_comparison": test(U2),
        "nc_net_of_rolls": test(U4[U4.state == "NC"], "pred_net"),
        "sources": json.loads((INPUTS / "sources.json").read_text()) if (INPUTS / "sources.json").exists() else None,
        "units_four_year": U4.replace({np.nan: None}).to_dict("records"),
        "units_two_year": U2.replace({np.nan: None}).to_dict("records"),
        "not_covered": {
            "GA": "SOS files behind a bot check; the voter list with primary history is sold; the 2022 / 2024 dashboards show "
            "no composition",
            "FL": "statistics files exist for the current election only; see data/static/early_skill/fl_captures.json",
            "NV": "Clark County mail only, 2022 and 2024: no four-year pair",
        },
    }
    OUT.write_text(json.dumps(out, indent=1, allow_nan=False))
    return out


def show(out: dict) -> None:
    for name in ("primary", "two_year_comparison", "nc_net_of_rolls"):
        print(f"\n{name}")
        for L, r in out[name].items():
            if "b" not in r:
                print(f"  L {L}: n {r['n']} ({r.get('note', '')})")
                continue
            cv = r["cv"]
            print(
                f"  L {L}: n {r['n']}  b {r['b']:+.3f} (90 % units {r['b_ci90_units']}, cycles {r['b_ci90_cycles']})  r {r['r']:+.2f}  "
                f"p {r['p']:.3f}  Holm {r.get('p_holm', float('nan')):.3f}  CV RMSE poll {cv['rmse_poll_only']} / mean "
                f"{cv['rmse_intercept']} / +pred {cv['rmse_with_predictor']}  skill {r.get('skill')}"
            )
    print(f"\nskill: {out['skill']}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["fetch"]:
        fetch()
        return 0
    show(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
