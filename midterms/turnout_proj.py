"""How many votes will 2026 have? A projection of each state's total from the ballots already banked, with a backtested range.

    python -m midterms.turnout_proj fetch       # GitHub Actions only (early-diag.yml): the missing fixed inputs (--force: all)
    python -m midterms.turnout_proj             # -> data/early/turnout_proj.json (one small data.pa.gov count)
    python -m midterms.turnout_proj --no-fetch  # the same, reusing the Pennsylvania count already in that file

A metric for the early-vote page, not a forecast input: nothing here moves a margin or enters model.json.

PRE-REGISTRATION (written 2026-10-20, before any projection or backtest number was computed)

  Banked      B_s,y(L) = ballots cast before Election Day and on hand by L days out, from the state trackers' official files
              (North Carolina: accepted mail and one-stop ballots by return date, nc_tracker's party_pace.csv for 2008-2024 and
              its 2026 table; Pennsylvania: returned mail ballots by return date, data.pa.gov, early_skill's pa_returns.csv
              for 2020-2024 and the 2026 dataset counted server-side). L is the days out of the newest complete day.
  Total       T_s,y = the largest of the state's general-election votes for President, U.S. Senate and U.S. House that year
              (President and Senate from MIT Election Data and Science Lab's returns, CC0; House from the FEC's Federal
              Elections tables): a floor on ballots cast, blank and undervoted ballots left out, the same definition every
              year.
  Share       F_s,y(L) = B_s,y(L) / T_s,y, the share of the final vote banked by L days out.
  Projection  T_hat = B_s,2026(L) / F_ref(L). Three references are scored, each built only from cycles before the target:
                A  the last cycle of the same kind (midterm for a midterm): F_y-4
                B  the last cycle of any kind: F_y-2
                C  A moved by the change between the two cycles of the other kind since: F_y-4 x F_y-2 / F_y-6
              The reference is the one with the smallest root-mean-square log error over every state and target year where
              all three exist, at L = 15 and 7 together; it is chosen on the backtest alone and fixed for every state and day.
  Backtest    every target year y of a state with its reference in the data: e = ln(T_hat / T) = ln(F_y / F_ref) at the
              same L as today's reading. The primary set is midterm targets (2026 is a midterm); presidential targets are
              reported beside it.
  Range       80 %: T_hat x exp(-/+ t(0.90, n) x RMS(e)) over the state's n midterm targets (RMS about zero, so a past bias
              widens the range rather than being corrected away). With n >= 10, the empirical 10th and 90th percentiles of
              -e instead. A state needs n >= 3 for a published projection; below that it shows its banked count against
              2022 at the same point only, and says why.
  Also shown  T_hat against 2022's total, and as a share of the citizen voting-age population (Census ACS 1-year,
              B29001_001E: 2024 for 2026, 2022 for 2022; CVAP counts citizens 18 and over, so it is not the eligible
              electorate: no correction for felony disenfranchisement or overseas voters). The Census API needs a key
              (CENSUS_API_KEY); without one the CVAP shares are left out.
  Not done    No national figure: no permitted, publishable source gives the banked vote of enough states (UF Election Lab's
              tables are CC BY-NC-ND 4.0 and no figure derived from them may be published), and six states are not a sample
              of fifty. Georgia, Florida, Clark County and Texas have no past cycle at matched days out in an official file
              (see their trackers), so they show their banked count against the latest comparable point only.

AMENDMENT (2026-10-20, after the first dry run on local copies of the inputs, before any published number): a pair of cycles
is compared only if their in-person early voting opened within a day of each other (North Carolina's one-stop start, read
from the daily counts: the first day, within a month of Election Day, with at least ten times the largest of the five days
before; Pennsylvania has no in-person early voting). The dry run showed why: North Carolina's 2014 one-stop period was cut
to 10 days (it opened 12 days out; every other year 19 or 20), so at 16 days out 2014 had banked almost nothing, and the
2018 target scored against it came out +2,200 % - a calendar, not a turnout, difference. Without the rule the range at that
lead ran from 0.17 to 160 million votes; with it the only comparable North Carolina midterm pair is 2022 against 2018.
North Carolina's opening day is read from the in-person ballots where the tracker's tables split them by method (2018 on):
the daily totals alone put 2020's at 29 days out, a pandemic mail surge, against 19 in the in-person ballots.
A data correction found on the same dry run: the FEC tables' Senate column adds Georgia's runoffs and both of its 2020 races
(18.8 million "votes"), so the Senate vote is MIT's general-election race total instead (the larger race where two were held).

RESULT (first run, 2026-10-20, early-diag.yml): reference A chosen (RMS log error 0.186 against 0.631 for B and 0.568 for C,
4 scored targets: North Carolina 2022 and 2024 at 15 and 7 days out). No state publishes a projection. North Carolina has one
comparable midterm (2022 against 2018: -12 % at 15 days out, -2 % at 7, +2 % at 3); its presidential targets ran +27 % (2012),
-5 % (2016), +146 % (2020, pandemic mail) and -28 % (2024) at 15 days. Pennsylvania's mail records start in 2020: no midterm
target (2024 against 2020, +0.4 %). The CVAP shares wait for a CENSUS_API_KEY secret (the API refuses keyless requests).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from . import fetch as F

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
STATIC = ROOT / "data" / "static"
EARLY = ROOT / "data" / "early"
OUT = EARLY / "turnout_proj.json"
TOTALS = STATIC / "turnout_totals.csv"
CVAP = STATIC / "cvap.csv"
NC_PACE = STATIC / "nc_tracker" / "party_pace.csv"
NC_LIVE = EARLY / "nc_tracker"
PA_RETURNS = STATIC / "early_skill" / "pa_returns.csv"
MIT_PRES = RAW / "mit" / "president_1976_2024.csv"

FEC_URL = "https://www.fec.gov/resources/cms-content/documents/federalelections{y}.{ext}"
CENSUS = "https://api.census.gov/data/{y}/acs/acs1"
PA_2026 = (
    "nbwd-pfn4",
    "2026-11-03",
)  # data.pa.gov dataset of the 2026 general and its Election Day (pa_early.DATASETS)
FIPS = {"NC": "37", "PA": "42", "GA": "13", "FL": "12", "TX": "48", "NV": "32"}
CLARK = ("32", "003")
YEARS = range(2008, 2025, 2)
ELECTION_DAY = dt.date(2026, 11, 3)
LEVEL = 0.80
MIN_N = 3  # midterm targets a state needs for a published range
EMPIRICAL_N = 10
CHOICE_LEADS = (15, 7)
REFERENCES = ("A", "B", "C")
SAME_CALENDAR_DAYS = 1  # in-person early voting must have opened within this many days of each other
LABEL = {"NC": "North Carolina", "PA": "Pennsylvania", "GA": "Georgia", "FL": "Florida", "NV": "Clark County, NV",
         "TX": "Texas"}  # fmt: skip


# --------------------------------------------------------------------------------------------------------- fetch (once)
def _fec_sheet(names: list[str]) -> str:
    """The workbook's general-election votes-cast table (President / Senate / House by state): its name changes each cycle."""
    keep = [s for s in names if re.search(r"GE ?Votes? ?(Cast|for)", s) and not re.search(r"Party|P&G|Prim", s)]
    if not keep:
        raise RuntimeError(f"no general-election votes-cast table among {names}")
    return keep[0]


def fec_house(path: Path, year: int) -> pd.DataFrame:
    """state, year, house: each state's U.S. House general-election vote in the FEC's Federal Elections tables."""
    x = pd.ExcelFile(path)
    sh = _fec_sheet(x.sheet_names)
    raw = x.parse(sh, header=None)
    hdr = int(raw.index[raw.iloc[:, 0].astype(str).str.strip().str.upper().eq("STATE")][0])
    t = x.parse(sh, header=hdr)
    t.columns = [re.sub(r"\s+", " ", str(c)).strip().upper() for c in t.columns]
    col = "STATE ABBREVIATION" if "STATE ABBREVIATION" in t.columns else "STATE"  # 2010 names the state in full
    t = t[t[col].astype(str).str.strip().str.len().eq(2)]
    house = [c for c in t.columns if "HOUSE" in c][0]
    votes = pd.to_numeric(t[house], errors="coerce")
    return pd.DataFrame({"state": t[col].astype(str).str.strip(), "year": year, "house": votes})


def mit_totals(pres: pd.DataFrame, sen: pd.DataFrame) -> pd.DataFrame:
    """state, year, president, senate from MIT's returns: each race's total votes, the Senate general only (a runoff is a
    separate election) and the larger race where a state held two that year (Georgia 2020)."""
    pv = pres.groupby(["state_po", "year"])["totalvotes"].max().rename("president")
    s = sen[sen.office.astype(str).str.upper().eq("US SENATE") & sen.stage.astype(str).str.lower().eq("gen")]
    sv = (
        s.groupby(["state_po", "year", "special"])["totalvotes"]
        .max()
        .groupby(["state_po", "year"])
        .max()
        .rename("senate")
    )
    out = pd.concat([pv, sv], axis=1).reset_index().rename(columns={"state_po": "state"})
    return out[out.state.isin(FIPS) & out.year.isin(YEARS)]


def fetch_totals() -> pd.DataFrame:
    """The House vote from the FEC's tables (their Senate column adds runoffs and second races, so it is not used), the
    President and Senate votes from MIT's returns; the year's total is the largest of the three."""
    from .bootstrap import FILES
    from .early_skill import MIT_SEN, fetch_mit_senate

    F.get(next(u for d, u, _ in FILES if d == "mit/president_1976_2024.csv"), MIT_PRES, min_age_h=24 * 365, timeout=300)
    fetch_mit_senate()
    house = []
    for y in YEARS:
        for ext in ("xlsx", "xls"):
            dest = RAW / "fec" / f"federalelections{y}.{ext}"
            try:
                F.get(FEC_URL.format(y=y, ext=ext), dest, min_age_h=24 * 365, timeout=300)
                house.append(fec_house(dest, y).assign(house_source=FEC_URL.format(y=y, ext=ext)))
                break
            except Exception as e:  # the other extension; a year the FEC has not published keeps President / Senate
                print(f"  FEC {y} .{ext}: {type(e).__name__}: {str(e)[:120]}")
    mit = mit_totals(pd.read_csv(MIT_PRES, low_memory=False), pd.read_csv(MIT_SEN, low_memory=False))
    d = mit.merge(pd.concat(house, ignore_index=True), on=["state", "year"], how="outer")
    d = d[d.state.isin(FIPS) & d.year.isin(YEARS)]
    d["total"] = d[["president", "senate", "house"]].max(axis=1)
    d.sort_values(["state", "year"]).to_csv(TOTALS, index=False)
    print(f"  totals: {len(d)} state-years -> {TOTALS.relative_to(ROOT)}")
    return d


def _census(y: int, q: dict, key: str) -> list[list[str]]:
    """One ACS 1-year query, its header row dropped; anything but the API's JSON table is an error that shows the reply."""
    url = f"{CENSUS.format(y=y)}?{urllib.parse.urlencode(q, safe=':,')}"
    b = F.open_url(f"{url}&key={key}", timeout=120)
    try:
        return json.loads(b)[1:]
    except ValueError:
        raise RuntimeError(f"Census API {url}: {b[:300]!r}") from None


def fetch_cvap() -> pd.DataFrame | None:
    """The Census Bureau's API answers only with a (free) key: CENSUS_API_KEY, a repository secret. Without one, nothing is
    requested and the table goes without its CVAP shares."""
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        print("  CVAP: no CENSUS_API_KEY, not fetched (the table leaves out the shares of citizens 18+)")
        return None
    rows = []
    for y in (2022, 2024):
        body = _census(y, {"get": "NAME,B29001_001E", "for": "state:" + ",".join(FIPS.values())}, key)
        rows.extend({"geo": next(k for k, v in FIPS.items() if v == r[2]), "year": y, "cvap": int(r[1])} for r in body)
        body = _census(y, {"get": "NAME,B29001_001E", "for": f"county:{CLARK[1]}", "in": f"state:{CLARK[0]}"}, key)
        rows.append({"geo": "NV-Clark", "year": y, "cvap": int(body[0][1])})
    d = pd.DataFrame(rows)
    d.to_csv(CVAP, index=False)
    print(f"  CVAP: {len(d)} rows -> {CVAP.relative_to(ROOT)}")
    return d


def fetch(force: bool = False) -> None:
    """The fixed inputs that are missing (all of them with force)."""
    if force or not TOTALS.exists():
        fetch_totals()
    if force or not CVAP.exists():
        fetch_cvap()
    F.report()


# --------------------------------------------------------------------------------------------------------------- banked
def nc_banked() -> pd.DataFrame:
    """year, dbe, n: accepted North Carolina mail and one-stop ballots by days before Election Day, 2008-2024 and 2026."""
    p = pd.read_csv(NC_PACE).groupby(["year", "dbe"], as_index=False)["n"].sum()
    live = NC_LIVE / "state_2026.csv"
    if live.exists():
        s = pd.read_csv(live).groupby("dbe", as_index=False)["n"].sum()
        p = pd.concat([p, s.assign(year=2026)], ignore_index=True)
    return p


def nc_now() -> tuple[int, str] | None:
    m = NC_LIVE / "meta.json"
    if not m.exists():
        return None
    j = json.loads(m.read_text())
    return int(j["dbe"]), j["asof"]


def pa_live(previous: dict | None, refresh: bool) -> dict | None:
    """{days_out: n} of the 2026 returned mail ballots, with the newest (possibly partial) return day left out."""
    if not refresh:
        return (previous or {}).get("pa_2026")
    from . import pa_early as PE

    ds, ed = PA_2026
    r = PE.q(ds, "SELECT ballotreturneddate AS d, count(*) AS n WHERE ballotreturneddate IS NOT NULL GROUP BY d")
    d = pd.DataFrame(r)
    if d.empty:
        return None
    d["day"] = pd.to_datetime(d["d"].astype(str).str[:10], errors="coerce", format="mixed")
    d = d[d.day.notna()]
    d["days_out"] = (pd.Timestamp(ed) - d.day).dt.days
    g = d.assign(n=d.n.astype(int)).groupby("days_out")["n"].sum()
    g = g[g.index > g.index.min()]  # the newest return day may still be filling
    if g.empty:
        return None
    L = int(g.index.min())
    return {"through": str((pd.Timestamp(ed) - pd.Timedelta(days=L)).date()), "lead": L,
            "days_out": {int(k): int(v) for k, v in g.items()}}  # fmt: skip


def pa_banked(live: dict | None) -> pd.DataFrame:
    p = (
        pd.read_csv(PA_RETURNS)
        .groupby(["year", "days_out"], as_index=False)["n"]
        .sum()
        .rename(columns={"days_out": "dbe"})
    )
    if live:
        s = pd.DataFrame({"dbe": list(map(int, live["days_out"])), "n": list(live["days_out"].values())})
        p = pd.concat([p, s.assign(year=2026)], ignore_index=True)
    return p


def onestop_open(b: pd.DataFrame) -> dict[int, int]:
    """{year: days out on which North Carolina's one-stop (in-person) voting opened}. Where the tracker's table splits the
    ballots by method (2018 on), the first day with 10,000 in-person ballots; before that, from the daily totals: the
    first day within 30 days of Election Day with at least 10,000 ballots and ten times the largest of the five days
    before (no year before 2018 had a mail surge to mistake for it; 2020's pandemic mail would have been)."""
    out = {}
    for y, g in b[b.dbe.between(0, 30)].groupby("year"):
        f = (STATIC / "nc_tracker" / f"state_{y}.csv") if y != 2026 else NC_LIVE / "state_2026.csv"
        if f.exists():
            ip = pd.read_csv(f).query("method == 'In person'").groupby("dbe")["n"].sum()
            if (ip >= 10000).any():
                out[int(y)] = int(ip[ip >= 10000].index.max())
            continue
        d = g.groupby("dbe")["n"].sum().sort_index(ascending=False)
        for k, n in d.items():
            prior = d[(d.index > k) & (d.index <= k + 5)]
            if n >= 10000 and (prior.empty or n >= 10 * prior.max()):
                out[int(y)] = int(k)
                break
    return out


def banked_at(b: pd.DataFrame, year: int, L: int) -> int | None:
    """Ballots on hand by L days out: everything that came back L or more days before Election Day."""
    g = b[b.year == year]
    if g.empty or g.dbe.max() < L:
        return None
    return int(g[g.dbe >= L].n.sum())


# ------------------------------------------------------------------------------------------------------------- backtest
def share(b: pd.DataFrame, totals: pd.Series, year: int, L: int) -> float | None:
    n = banked_at(b, year, L)
    t = totals.get(year)
    return n / t if n and t and np.isfinite(t) else None


def comparable(cal: dict[int, int] | None, *years: int) -> bool:
    """Same early-voting calendar (AMENDMENT): in-person voting opened within SAME_CALENDAR_DAYS of each other, or the
    state has none (cal is None). A year whose opening the counts do not show is not comparable."""
    if cal is None:
        return True
    if any(y not in cal for y in years):
        return False
    return max(cal[y] for y in years) - min(cal[y] for y in years) <= SAME_CALENDAR_DAYS


def reference(b: pd.DataFrame, totals: pd.Series, year: int, L: int, ref: str, cal: dict | None = None) -> float | None:
    back = {"A": (4,), "B": (2,), "C": (4, 2, 6)}[ref]
    if not comparable(cal, year, *(year - k for k in back)):
        return None
    f = [share(b, totals, year - k, L) for k in back]
    if not all(f):
        return None
    return f[0] if ref != "C" else f[0] * f[1] / f[2]


def errors(b: pd.DataFrame, totals: pd.Series, L: int, ref: str, cal: dict | None = None) -> list[dict]:
    """Every past target year with a comparable reference: e = ln(T_hat / T) = ln(F_y / F_ref)."""
    out = []
    for y in sorted(set(b.year) - {2026}):
        f, fr = share(b, totals, y, L), reference(b, totals, y, L, ref, cal)
        if f and fr:
            out.append({"year": int(y), "midterm": y % 4 == 2, "e": float(np.log(f / fr))})
    return out


def choose_reference(inputs: dict) -> dict:
    """The reference with the smallest RMS log error over the targets where all three exist, at the choice leads."""
    pool = {r: [] for r in REFERENCES}
    for b, totals, cal in inputs.values():
        for L in CHOICE_LEADS:
            per = {r: {x["year"]: x["e"] for x in errors(b, totals, L, r, cal)} for r in REFERENCES}
            common = set.intersection(*(set(v) for v in per.values()))  # targets every reference can score
            for r in REFERENCES:
                pool[r].extend(per[r][y] for y in sorted(common))
    rms = {r: round(float(np.sqrt(np.mean(np.square(v)))), 4) if v else None for r, v in pool.items()}
    scored = [r for r in REFERENCES if rms[r] is not None]
    best = min(scored, key=lambda r: rms[r]) if scored else REFERENCES[0]  # nothing to score: the simplest
    return {"chosen": best, "rms_log": rms, "n": len(pool[best]), "leads": list(CHOICE_LEADS)}


def interval(e: list[float], level: float = LEVEL) -> tuple[float, float] | None:
    """Multiplicative factors (lo, hi) on T_hat from past log errors e = ln(T_hat / T)."""
    n = len(e)
    if n < MIN_N:
        return None
    a = (1 - level) / 2
    if n >= EMPIRICAL_N:
        q = np.quantile(-np.asarray(e), [a, 1 - a])
        return float(np.exp(q[0])), float(np.exp(q[1]))
    w = stats.t.ppf(1 - a, n) * float(np.sqrt(np.mean(np.square(e))))
    return float(np.exp(-w)), float(np.exp(w))


def project(b: pd.DataFrame, totals: pd.Series, L: int, ref: str, cvap: dict, cal: dict | None) -> dict:
    """2026's projected total at lead L, its range from the midterm targets, and the presidential ones beside it."""
    n26 = banked_at(b, 2026, L)
    fr = reference(b, totals, 2026, L, ref, cal)
    if not n26 or not fr:
        return {"reason": "no comparable reference cycle at this point"}
    errs = errors(b, totals, L, ref, cal)
    mid = [x for x in errs if x["midterm"]]
    t_hat = n26 / fr
    t22 = float(totals.get(2022, np.nan))
    out = {
        "total": round(t_hat),
        "reference_share_pct": round(100 * fr, 2),
        "n_midterm": len(mid),
        "midterm_errors_pct": {x["year"]: round(100 * (np.exp(x["e"]) - 1), 1) for x in mid},
        "presidential_errors_pct": {x["year"]: round(100 * (np.exp(x["e"]) - 1), 1) for x in errs if not x["midterm"]},
        "vs_2022_pct": round(100 * (t_hat / t22 - 1), 1) if np.isfinite(t22) else None,
    }
    if c := cvap.get(2024):
        out["pct_cvap"] = round(100 * t_hat / c, 1)
    if (c := cvap.get(2022)) and np.isfinite(t22):
        out["pct_cvap_2022"] = round(100 * t22 / c, 1)
    iv = interval([x["e"] for x in mid])
    if iv is None:  # no range, so no published total either: the point alone would read as more certain than it is
        n = len(mid)
        return {k: out[k] for k in ("n_midterm", "midterm_errors_pct", "presidential_errors_pct")} | {
            "reason": f"{n} comparable past midterm{'' if n == 1 else 's'} at this point: too few to test a projection"
        }
    out |= {"lo": round(t_hat * iv[0]), "hi": round(t_hat * iv[1]), "level": LEVEL}
    if np.isfinite(t22):
        out |= {"vs_2022_lo_pct": round(100 * (t_hat * iv[0] / t22 - 1), 1),
                "vs_2022_hi_pct": round(100 * (t_hat * iv[1] / t22 - 1), 1)}  # fmt: skip
    if allv := interval([x["e"] for x in errs]):
        out["all_targets"] = {"n": len(errs), "lo": round(t_hat * allv[0]), "hi": round(t_hat * allv[1])}
    return out


# ------------------------------------------------------------------------------------------------- the other states
def _read(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def others(totals: pd.DataFrame) -> list[dict]:
    """Georgia, Florida, Clark County and Texas: banked so far against the latest comparable point, no projection."""
    t22 = totals[totals.year == 2022].set_index("state")["total"]
    rows = []
    if g := (_read(EARLY / "ga_early.json") or {}).get("latest"):
        rows.append({"state": "GA", "through": g["through"], "days_out": g["days_out"], "banked": g["total"],
                     "same_point_2022": g.get("total_2022_same_point"), "vs_2022_same_point_pct": g.get("vs_2022_pct"),
                     "total_2022": int(t22["GA"]) if "GA" in t22 else None})  # fmt: skip
    if f := (_read(EARLY / "fl_early.json") or {}).get("latest"):
        v, e = f.get("VbmVoted") or {}, f.get("EarlyVoted") or {}
        n = int(v.get("total", 0)) + int(e.get("total", 0))
        rows.append({"state": "FL", "through": (v or e).get("compiled", "")[:10], "days_out": min(
            x["days_out"] for x in (v, e) if x), "banked": n, "same_point_2022": None, "vs_2022_same_point_pct": None,
                     "total_2022": int(t22["FL"]) if "FL" in t22 else None,
                     "note": "mail and in-person early ballots on their latest compiles; no 2022 file at this point"})  # fmt: skip
    if nv := _read(EARLY / "nv_clark_early.json"):
        m, ip = nv.get("mail") or {}, nv.get("in_person") or {}
        v22 = m.get("vs_2022") or {}
        rows.append({"state": "NV", "through": nv.get("through"), "days_out": nv.get("days_out"),
                     "banked": (nv.get("combined") or {}).get("total"), "mail": (m.get("returned") or {}).get("total"),
                     "in_person": (ip.get("voted") or {}).get("total"),
                     "mail_2022_same_point": (v22.get("returned") or {}).get("total"),
                     "mail_vs_2022_same_point_pct": v22.get("returned_change_pct"), "total_2022": None,
                     "note": "Clark County only; mail is the only part with a 2022 file at the same point"})  # fmt: skip
    if tx := _read(EARLY / "tx_early.json"):
        done = [x for x in tx.get("days") or [] if (x["groups"].get("statewide") or {}).get("ev")]  # days with a file
        d = done[-1:] or [None]
        if d[0]:
            s = d[0]["groups"]["statewide"]
            rows.append({"state": "TX", "through": d[0]["date"], "days_out": d[0]["days_out"], "banked": s["ev"],
                         "pct_registered": s["pct_rv"], "same_point_2022": s.get("ev_2022"),
                         "total_2022": int(t22["TX"]) if "TX" in t22 else None,
                         "note": "counties as reported to the Secretary of State; no 2022 file at this point yet"})  # fmt: skip
    for r in rows:
        if r.get("banked") and r.get("total_2022"):
            r["share_of_2022_total_pct"] = round(100 * r["banked"] / r["total_2022"], 1)
        r["label"] = LABEL[r["state"]]
    return rows


def with_cvap(row: dict, cv: pd.DataFrame) -> dict:
    """Ballots so far and 2022's total as shares of the citizen voting-age population (Clark County: the county's)."""
    c = cv[cv.geo == ("NV-Clark" if row["state"] == "NV" else row["state"])].set_index("year")["cvap"]
    if row.get("banked") and 2024 in c:
        row["banked_pct_cvap"] = round(100 * row["banked"] / c[2024], 1)
    if row.get("total_2022") and 2022 in c:
        row["total_2022_pct_cvap"] = round(100 * row["total_2022"] / c[2022], 1)
    return row


# ---------------------------------------------------------------------------------------------------------------- run
def build(refresh_pa: bool = True) -> dict:
    if not TOTALS.exists():
        raise FileNotFoundError(
            f"{TOTALS.relative_to(ROOT)} missing: run `python -m midterms.turnout_proj fetch` in Actions"
        )
    tot = pd.read_csv(TOTALS)
    cv = pd.read_csv(CVAP) if CVAP.exists() else pd.DataFrame(columns=["geo", "year", "cvap"])
    prev = _read(OUT)
    pa = pa_live(prev, refresh_pa)
    series = {"NC": nc_banked(), "PA": pa_banked(pa)}
    cals = {"NC": onestop_open(series["NC"]), "PA": None}  # Pennsylvania: mail only, no in-person calendar
    inputs = {s: (series[s], tot[tot.state == s].set_index("year")["total"], cals[s]) for s in series}
    choice = choose_reference(inputs)
    now = {"NC": nc_now(), "PA": (int(pa["lead"]), pa["through"]) if pa else None}
    rows = []
    for s, (b, totals, cal) in inputs.items():
        if not now[s]:
            continue
        L, through = now[s]
        n26, n22 = banked_at(b, 2026, L), banked_at(b, 2022, L)
        t22 = float(totals.get(2022, np.nan))
        cvap = cv[cv.geo == s].set_index("year")["cvap"].to_dict()
        row = {
            "state": s,
            "label": LABEL[s],
            "through": through,
            "days_out": L,
            "banked": n26,
            "same_point_2022": n22,
            "vs_2022_same_point_pct": round(100 * (n26 / n22 - 1), 1) if n26 and n22 else None,
            "total_2022": round(t22) if np.isfinite(t22) else None,
            "share_of_2022_total_pct": round(100 * n26 / t22, 1) if n26 and np.isfinite(t22) else None,
            "projection": project(b, totals, L, choice["chosen"], cvap, cal),
        }
        if cal:
            row["onestop_opened_days_out"] = {str(y): d for y, d in sorted(cal.items())}
        rows.append(row)
    res = {
        "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"),
        "election_day": str(ELECTION_DAY),
        "reference": choice,
        "level": LEVEL,
        "min_midterms": MIN_N,
        "states": [with_cvap(r, cv) for r in rows + others(tot)],
        "national": None,
        "pa_2026": pa,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    for r in res["states"]:
        p = r.get("projection") or {}
        rng = f" ({p['lo']:,}-{p['hi']:,})" if "lo" in p else ""
        tail = f"projected {p['total']:,}{rng}" if p.get("total") else (p.get("reason") or r.get("note") or "")
        print(f"  {r['state']}: banked {r.get('banked')} at {r.get('days_out')} days out; {tail}")
    print(f"  reference {choice['chosen']} (RMS log {choice['rms_log']}, n {choice['n']}) -> {OUT.relative_to(ROOT)}")
    return res


if __name__ == "__main__":
    if sys.argv[1:2] == ["fetch"]:
        fetch(force="--force" in sys.argv)
    else:
        build(refresh_pa="--no-fetch" not in sys.argv)
