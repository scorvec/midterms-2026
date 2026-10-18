"""North Carolina early vote on the site (web/early-vote.html): 2026 against 2018-2024 at the same number of days out, by
party, age, race, county and vote history - and the backtest that keeps the early-vote mix out of the forecast.

Companion to nc_early.py (the race / ethnicity diagnostic in Tom Bonier's three measures); this module is the published
tracker. Both read the same NC State Board of Elections files (dl.ncsbe.gov, public, anonymous) and keep aggregates only:
no voter's row, name or address is written anywhere.

  * absentee_<election>.zip: one row per mail / one-stop ballot with the voter's county, party, race, age, the ballot type,
    the date it came back and its status. 2026 is the morning's file (nc_early.fetch_2026, one conditional request);
    2008-2024 come from their final files, which keep every ballot's return date, so each year's pace at any days out is
    reconstructed (a ballot accepted then and spoiled later is missing from them: the history is very slightly short).
  * voter_stats_<election>.zip: registered voters on each earlier election day by county, party, race and age; for 2026
    this week's statewide registration file (ncvoter_Statewide.zip, ACTIVE + INACTIVE), reduced when the board replaces it
    (one HEAD request a run) into data/early/nc_tracker/reg_2026.csv.
  * voter history: nc_early.history() (every voter's method in the 2016-2024 generals, kept in the Actions cache).
  * 2024 presidential result by county (results_pct_20241105.zip): each county's lean.

Counting: a ballot counts once its status is accepted (any accepted status; 2008: "OK"), once per voter (NCID; before 2016
county + registration number), on the day it came back; the 2026 reading is through the day before the file date. Days are
counted before each year's own Election Day, so the years line up on their own calendars: one-stop opened 19 days out in
2020-2026 and 20 in 2018. Rates are per registered voter of the same group on that year's rolls, so a group that has grown
or shrunk on the rolls is not mistaken for one that turned out more or less. Party = registration (DEM, REP, UNA; the small
parties together as OTH); race in three groups (Hispanic is not separable in 2018's file); age at the file date.

The forecast gate (gate()). Should the early-vote mix move the margins? Only if it has predicted the polls' misses. The
measure: the Democratic-minus-Republican share of the ballots banked by L days out, minus the same on the rolls, and its
change from the previous cycle of the same kind; the outcome: North Carolina's statewide poll miss (result minus the mean of
the D v R polls of the last 21 days, 538 raw_polls; President, Senate, Governor). Five cycles can be scored (2012, 2014,
2016, 2020, 2022; 2018 had no statewide race and 2024 is not in raw_polls). At 17 days out r = -0.72 (p 0.17), at 10 days
-0.27, at 5 days -0.22: no detectable skill, and its sign is the opposite of the usual reading (a more Democratic early vote
than last time came before polls that ran too Democratic, 2016 and 2020). The gate stays shut; data/static/early_gate.json
keeps the table and the page shows today's reading beside it. Rerun after 2026 adds a sixth cycle.

Where it runs: nightly in early-diag.yml, after nc_early.py (which has fetched the morning's absentee file), then
early_page.py; the workflow commits data/early/nc_tracker/ and web/data/early_vote.json - aggregates only.

    python -m midterms.nc_tracker                      # daily: 2026 tables (data/early/nc_tracker/)
    python -m midterms.nc_tracker history --src DIR    # one-time: 2008-2024 into data/static/nc_tracker/ (NCSBE zips in DIR)
    python -m midterms.nc_tracker gate                 # the backtest -> data/static/early_gate.json (needs raw_polls)
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import io
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import nc_early as NE

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "data" / "static" / "nc_tracker"
LIVE_DIR = ROOT / "data" / "early" / "nc_tracker"
GATE_JSON = ROOT / "data" / "static" / "early_gate.json"
RAW_POLLS = ROOT / "data" / "raw" / "538repo" / "raw_polls.csv"
S3 = NE.S3
ELECTIONS = {
    2008: dt.date(2008, 11, 4),
    2010: dt.date(2010, 11, 2),
    2012: dt.date(2012, 11, 6),
    2014: dt.date(2014, 11, 4),
    2016: dt.date(2016, 11, 8),
    2018: dt.date(2018, 11, 6),
    2020: dt.date(2020, 11, 3),
    2022: dt.date(2022, 11, 8),
    2024: dt.date(2024, 11, 5),
    2026: dt.date(2026, 11, 3),
}
LIVE = 2026
YEARS = (2018, 2020, 2022, 2024, 2026)  # the page's comparison years (full tables)
MIDTERMS = (2018, 2022, 2026)
PREV = {2012: 2008, 2014: 2010, 2016: 2012, 2018: 2014, 2020: 2016, 2022: 2018, 2024: 2020, 2026: 2022}
HIST_PRIOR = {2026: ("g2022", "g2024"), 2022: ("g2018", "g2020")}  # previous midterm, previous presidential general
ONESTOP_OUT = {2018: 20, 2020: 19, 2022: 19, 2024: 19, 2026: 19}
DBE_MAX, DBE_MIN = 45, -15  # earlier returns are counted on day 45; mail accepted after Election Day down to -15
PARTIES = ("DEM", "REP", "UNA")
AGES = ((18, 25, "18-25"), (26, 40, "26-40"), (41, 65, "41-65"), (66, 200, "66+"))
GATE_LEADS = (17, 10, 5)


def edate(y: int) -> str:
    return ELECTIONS[y].strftime("%Y%m%d")


def _party(s: pd.Series) -> pd.Series:
    s = s.fillna("").astype(str).str.strip().str.upper()
    return s.where(s.isin(PARTIES), "OTH")


def _race(s: pd.Series) -> np.ndarray:
    s = s.fillna("").astype(str).str.strip().str.upper()
    white = s.str.startswith("WHITE") | (s == "W")
    black = s.str.startswith("BLACK") | (s == "B")
    return np.where(white, "White", np.where(black, "Black", "Other"))


def _age(a: pd.Series) -> pd.Series:
    a = pd.to_numeric(a, errors="coerce")
    out = pd.Series("Unknown", index=a.index)
    for lo, hi, lab in AGES:
        out[(a >= lo) & (a <= hi)] = lab
    return out


# ----------------------------------------------------------------------------------------------------- ballots
def ballots(fh, year: int, file_date: dt.date | None = None) -> pd.DataFrame:
    """Accepted ballots of `year`, one per voter: ncid, county, party, race, age, method, dbe. fh: a CSV file object.
    With file_date (the live file) only the days before it count."""
    hdr = pd.read_csv(fh, nrows=0, encoding="latin-1").columns
    fh.seek(0)
    want = ["county_desc", "ncid", "voter_reg_num", "race", "age", "voter_party_code", "ballot_req_type",
            "ballot_request_type", "ballot_rtn_dt", "ballot_rtn_status"]  # fmt: skip
    cols = [c for c in want if c in hdr]
    chunks = pd.read_csv(fh, usecols=cols, dtype=str, encoding="latin-1", chunksize=1_000_000)
    ok = r"ACCEPTED|OK$"  # 2008's files say OK
    df = pd.concat(
        [ch[ch["ballot_rtn_status"].fillna("").str.upper().str.match(ok)] for ch in chunks], ignore_index=True
    )
    rtn = pd.to_datetime(df["ballot_rtn_dt"], format="%m/%d/%Y", errors="coerce")
    rtn = rtn.fillna(pd.to_datetime(df["ballot_rtn_dt"], format="%Y%m%d", errors="coerce"))  # 2008
    vid = df["ncid"] if "ncid" in df else df["county_desc"].str.strip() + ":" + df["voter_reg_num"].str.strip()
    df = df.assign(rtn=rtn, vid=vid.str.strip()).dropna(subset=["rtn"]).sort_values("rtn").drop_duplicates("vid")
    if file_date is not None:
        df = df[df["rtn"].dt.date < file_date]
    rt = (
        df["ballot_req_type"]
        if "ballot_req_type" in df
        else df.get("ballot_request_type", pd.Series("", index=df.index))
    )
    return pd.DataFrame(
        {
            "ncid": df["vid"].values,
            "county": df["county_desc"].str.strip().str.upper().values,
            "party": _party(df["voter_party_code"]).values,
            "race": _race(df["race"]) if "race" in df else "Other",
            "age": _age(df["age"]).values if "age" in df else "Unknown",
            "method": np.where(rt.fillna("").str.upper().str.contains("MAIL").values, "Mail", "In person"),
            "dbe": (pd.Timestamp(ELECTIONS[year]) - df["rtn"]).dt.days.clip(DBE_MIN, DBE_MAX).values,
        }
    )


def tables(b: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "state": b.groupby(["dbe", "method", "party", "race", "age"]).size().rename("n").reset_index(),
        "county": b.groupby(["dbe", "county", "party"]).size().rename("n").reset_index(),
    }


def history_table(b: pd.DataFrame, year: int, hist: pd.DataFrame) -> pd.DataFrame:
    """dbe, method, party, prior: whether `year`'s early voters voted in the previous midterm and presidential generals
    (both / midterm only / presidential only / neither)."""
    gm, gp = HIST_PRIOR[year]
    h = hist.reindex(b["ncid"].values)
    vm, vp = h[gm].fillna(0).values > 0, h[gp].fillna(0).values > 0
    prior = np.select([vm & vp, vm & ~vp, ~vm & vp], ["both", "midterm only", "presidential only"], "neither")
    return b.assign(prior=prior).groupby(["dbe", "method", "party", "prior"]).size().rename("n").reset_index()


# ------------------------------------------------------------------------------------------------ registration
def registration_stats(zp: Path) -> pd.DataFrame:
    """Registered voters on an election day (voter_stats): county, party, race, age -> n."""
    with zipfile.ZipFile(zp) as z, z.open(z.infolist()[0]) as f:
        v = pd.read_csv(f, sep="\t", dtype=str, encoding="latin-1")
    v = v[v["stats_type"].str.strip() == "voter"]
    age = v["age"].str.extract(r"(\d+)")[0].astype(float)  # "Age 18 - 25" ... "Age Over 66": the lower bound
    out = pd.DataFrame(
        {
            "county": v["county_desc"].str.strip().str.upper(),
            "party": _party(v["party_cd"]),
            "race": _race(v["race_code"]),
            "age": _age(age),
            "n": v["total_voters"].astype(int),
        }
    )
    return out.groupby(["county", "party", "race", "age"])["n"].sum().reset_index()


def registration_2026() -> tuple[pd.DataFrame, str]:
    """This week's registration (ACTIVE + INACTIVE) by county, party, race, age, and the file's date; reduced again only
    when the board has replaced ncvoter_Statewide.zip since the committed table."""
    from . import fetch as F

    url = f"{S3}/data/ncvoter_Statewide.zip"
    p, meta_p = LIVE_DIR / "reg_2026.csv", LIVE_DIR / "reg_2026.json"
    with urllib.request.urlopen(
        urllib.request.Request(url, method="HEAD", headers={"User-Agent": F.UA}), timeout=60
    ) as r:
        lm = r.headers["Last-Modified"]
    if p.exists() and meta_p.exists() and json.loads(meta_p.read_text()).get("last_modified") == lm:
        return pd.read_csv(p), json.loads(meta_p.read_text())["date"]
    cols = ["county_desc", "voter_status_desc", "party_cd", "race_code", "age_at_year_end"]
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / "ncvoter.zip"
        req = urllib.request.Request(url, headers={"User-Agent": F.UA})
        with urllib.request.urlopen(req, timeout=900) as r, open(zp, "wb") as fh:
            while chunk := r.read(1 << 22):
                fh.write(chunk)
        F.count(url, zp.stat().st_size)
        with zipfile.ZipFile(zp) as z, z.open(z.infolist()[0]) as f:
            chunks = pd.read_csv(f, sep="\t", usecols=cols, dtype=str, encoding="latin-1", chunksize=1_000_000)
            active = ["ACTIVE", "INACTIVE"]
            v = pd.concat(
                [ch[ch["voter_status_desc"].str.strip().str.upper().isin(active)] for ch in chunks], ignore_index=True
            )
    reg = (
        pd.DataFrame(
            {
                "county": v["county_desc"].str.strip().str.upper(),
                "party": _party(v["party_cd"]),
                "race": _race(v["race_code"]),
                "age": _age(v["age_at_year_end"]),
            }
        )
        .groupby(["county", "party", "race", "age"])
        .size()
        .rename("n")
        .reset_index()
    )
    date = email.utils.parsedate_to_datetime(lm).date().isoformat()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    reg.to_csv(p, index=False)
    meta_p.write_text(json.dumps({"last_modified": lm, "date": date}, indent=1))
    return reg, date


def county_pres_2024(zp: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zp) as z, z.open(z.infolist()[0]) as f:
        r = pd.read_csv(f, sep="\t", dtype=str, encoding="latin-1")
    r = r[r["Contest Name"].str.strip().str.upper() == "US PRESIDENT"]
    r = r.assign(v=pd.to_numeric(r["Total Votes"], errors="coerce").fillna(0), cp=r["Choice Party"].str.strip())
    t = pd.DataFrame(
        {
            "total": r.groupby("County")["v"].sum(),
            "dem": r[r.cp == "DEM"].groupby("County")["v"].sum(),
            "rep": r[r.cp == "REP"].groupby("County")["v"].sum(),
        }
    ).fillna(0)
    t.index = t.index.str.strip().str.upper()
    return t.astype(int).rename_axis("county").reset_index()


# ---------------------------------------------------------------------------------------------------- builders
def build_history(src: Path) -> None:
    """One-time: data/static/nc_tracker/ from the NCSBE zips in src (absentee_*, voter_stats_*, results_pct_20241105)."""
    STATIC.mkdir(parents=True, exist_ok=True)
    hist = NE.history()
    pace, regp = [], []
    for y in ELECTIONS:
        if y == LIVE:
            continue
        with zipfile.ZipFile(src / f"absentee_{edate(y)}.zip") as z, z.open(z.infolist()[0]) as f:
            b = ballots(f, y)  # streamed: 2020's and 2024's files are 2 GB
        reg = registration_stats(src / f"voter_stats_{edate(y)}.zip")
        pace.append(b.groupby(["dbe", "party"]).size().rename("n").reset_index().assign(year=y))
        regp.append(reg.groupby("party")["n"].sum().reset_index().assign(year=y))
        if y in YEARS:
            for k, t in tables(b).items():
                t.to_csv(STATIC / f"{k}_{y}.csv", index=False)
            reg.to_csv(STATIC / f"reg_{y}.csv", index=False)
        if y in HIST_PRIOR:
            history_table(b, y, hist).to_csv(STATIC / f"hist_{y}.csv", index=False)
        print(f"  {y}: {len(b):,} accepted ballots", flush=True)
    pd.concat(pace).to_csv(STATIC / "party_pace.csv", index=False)
    pd.concat(regp).to_csv(STATIC / "party_reg.csv", index=False)
    county_pres_2024(src / "results_pct_20241105.zip").to_csv(STATIC / "pres2024_county.csv", index=False)


def run_live() -> dict:
    raw, file_date = NE.fetch_2026()
    b = ballots(io.BytesIO(raw), LIVE, file_date)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    for k, t in tables(b).items():
        t.to_csv(LIVE_DIR / f"{k}_{LIVE}.csv", index=False)
    _, reg_date = registration_2026()
    history_table(b, LIVE, NE.history()).to_csv(LIVE_DIR / f"hist_{LIVE}.csv", index=False)
    asof = file_date - dt.timedelta(days=1)
    meta = {
        "asof": asof.isoformat(),
        "dbe": (ELECTIONS[LIVE] - asof).days,
        "ballots": len(b),
        "registration_file": reg_date,
        "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"),
    }
    (LIVE_DIR / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"NC tracker: {len(b):,} ballots through {asof} ({meta['dbe']} days out)", flush=True)
    return meta


# -------------------------------------------------------------------------------------------- comparisons / page
def _load(y: int, kind: str) -> pd.DataFrame:
    return pd.read_csv((LIVE_DIR if y == LIVE else STATIC) / f"{kind}_{y}.csv")


def _upto(df: pd.DataFrame, dbe: int) -> pd.DataFrame:
    return df[df["dbe"] >= dbe]


def composition(y: int, L: int, pace: pd.DataFrame | None = None, reg: pd.DataFrame | None = None) -> float:
    """D - R share of the ballots banked by L days out minus D - R share of the rolls, points."""
    if y == LIVE:
        b = _upto(_load(y, "state"), L).groupby("party")["n"].sum()
        r = _load(y, "reg").groupby("party")["n"].sum()
    else:
        pace = pd.read_csv(STATIC / "party_pace.csv") if pace is None else pace
        reg = pd.read_csv(STATIC / "party_reg.csv") if reg is None else reg
        b = _upto(pace[pace.year == y], L).groupby("party")["n"].sum()
        r = reg[reg.year == y].set_index("party")["n"]
    return 100 * ((b.get("DEM", 0) - b.get("REP", 0)) / b.sum() - (r["DEM"] - r["REP"]) / r.sum())


def nc_poll_misses() -> dict:
    """{cycle: mean over NC's statewide races of result minus the mean D v R poll of the last 21 days} (538 raw_polls)."""
    d = pd.read_csv(RAW_POLLS)
    d = d[
        (d.location == "NC")
        & d.type_simple.isin(["Pres-G", "Sen-G", "Gov-G"])
        & d.cand1_party.eq("DEM")
        & d.cand2_party.eq("REP")
        & (d.time_to_election <= 21)
    ]
    g = d.groupby(["cycle", "type_simple"]).agg(poll=("margin_poll", "mean"), actual=("margin_actual", "first"))
    return (g.actual - g.poll).groupby(level=0).mean().round(2).to_dict()


def gate(write: bool = True) -> dict:
    from scipy import stats

    miss = nc_poll_misses()
    pace, reg = pd.read_csv(STATIC / "party_pace.csv"), pd.read_csv(STATIC / "party_reg.csv")
    rows, leads = [], {}
    for y in sorted(PREV):
        if y == LIVE:
            continue
        rec = {"year": y, "previous": PREV[y], "poll_miss": miss.get(y)}
        for L in GATE_LEADS:
            c, c0 = composition(y, L, pace, reg), composition(PREV[y], L, pace, reg)
            rec[f"comp_{L}"], rec[f"change_{L}"] = round(c, 2), round(c - c0, 2)
        rows.append(rec)
    for L in GATE_LEADS:
        u = [(r[f"change_{L}"], r["poll_miss"]) for r in rows if r["poll_miss"] is not None]
        r_, p_ = stats.pearsonr(*zip(*u, strict=True))
        leads[str(L)] = {"n": len(u), "r": round(float(r_), 3), "p": round(float(p_), 3)}
    out = {
        "open": False,
        "reason": "no detectable skill: 5 scoreable cycles, no lead significant, and the sign opposite to the usual reading",
        "measure": "change from the previous cycle of the same kind in (D - R share of ballots banked by L days out) minus "
        "(D - R share of the rolls), points",
        "outcome": "NC statewide poll miss, result minus the mean D v R poll of the last 21 days (538 raw_polls), D - R",
        "leads": leads,
        "rows": rows,
    }
    if write:
        GATE_JSON.write_text(json.dumps(out, indent=1))
    return out


def payload() -> dict:
    """The North Carolina block of web/data/early_vote.json: same-day comparisons, the series the page draws, the gate."""
    meta = json.loads((LIVE_DIR / "meta.json").read_text())
    now = int(meta["dbe"])
    st = {y: _load(y, "state") for y in YEARS}
    co = {y: _load(y, "county") for y in YEARS}
    reg = {y: _load(y, "reg") for y in YEARS}
    hist = {y: _load(y, "hist") for y in HIST_PRIOR}
    years = {}
    for y in YEARS:
        u, r = _upto(st[y], now), reg[y]
        rec = {
            "ballots": int(u.n.sum()),
            "in_person": int(u[u.method == "In person"].n.sum()),
            "mail": int(u[u.method == "Mail"].n.sum()),
            "registered": int(r.n.sum()),
            "pct_registered": round(100 * u.n.sum() / r.n.sum(), 2),
        }
        if y != LIVE:
            rec["final_early"] = int(st[y].n.sum())
            rec["share_of_final_by_now"] = round(100 * u.n.sum() / st[y].n.sum(), 1)
        for col in ("party", "age", "race"):
            n, rr = u.groupby(col)["n"].sum(), r.groupby(col)["n"].sum()
            rec[col] = {
                k: {
                    "ballots": int(n[k]),
                    "share": round(100 * n[k] / n.sum(), 1),
                    "pct_registered": round(100 * n[k] / rr[k], 2),
                    "reg_share": round(100 * rr[k] / rr.sum(), 1),
                }
                for k in n.index
                if k != "Unknown" and k in rr
            }
        if y in hist:
            h = _upto(hist[y], now)
            rec["history"] = {}
            for p in ("ALL", *PARTIES):
                s = (h if p == "ALL" else h[h.party == p]).groupby("prior")["n"].sum()
                rec["history"][p] = {k: round(100 * v / s.sum(), 1) for k, v in s.items()}
        years[str(y)] = rec
    # cumulative pace, days 30 .. 0 (2026 up to today)
    days = list(range(30, -1, -1))
    pace = {}
    for y in YEARS:
        d = st[y].groupby("dbe")["n"].sum().reindex(range(DBE_MIN, DBE_MAX + 1), fill_value=0)
        cum = d[::-1].cumsum()[::-1]
        xs = [x for x in days if y != LIVE or x >= now]
        pace[str(y)] = {"n": [int(cum[x]) for x in xs], "pct": [round(100 * cum[x] / reg[y].n.sum(), 2) for x in xs]}
    # counties: 2026 against 2022 at the same day, by 2024 presidential margin
    pres = pd.read_csv(STATIC / "pres2024_county.csv").set_index("county")
    n26, n22 = _upto(co[LIVE], now).groupby("county")["n"].sum(), _upto(co[2022], now).groupby("county")["n"].sum()
    r26, r22 = reg[LIVE].groupby("county")["n"].sum(), reg[2022].groupby("county")["n"].sum()
    ct = pd.DataFrame({"n26": n26, "n22": n22, "r26": r26, "r22": r22}).fillna(0)
    ct = ct[(ct.n22 > 0) & (ct.r26 > 0)]
    ct["ratio"] = (ct.n26 / ct.r26) / (ct.n22 / ct.r22)
    ct["margin"] = 100 * (pres["rep"] - pres["dem"]) / pres["total"]
    ct = ct.dropna()
    bins = [-100, -20, -5, 5, 20, 40, 100]
    band = ct.groupby(pd.cut(ct.margin, bins), observed=True).apply(
        lambda d: (d.n26.sum() / d.r26.sum()) / (d.n22.sum() / d.r22.sum()), include_groups=False
    )
    gate_rec = json.loads(GATE_JSON.read_text()) if GATE_JSON.exists() else None
    c26 = composition(LIVE, now)
    c22 = composition(2022, now)
    return {
        "asof": meta["asof"],
        "dbe": now,
        "onestop_out": ONESTOP_OUT[LIVE],
        "generated": meta["generated"],
        "registration_file": meta["registration_file"],
        "years": years,
        "pace": {"days": days, **pace},
        "counties": [
            {"c": c.title(), "m": round(r.margin, 1), "x": round(r.ratio, 3), "reg": int(r.r26)}
            for c, r in ct.iterrows()
        ],
        "county_bands": [{"lo": int(b.left), "hi": int(b.right), "x": round(float(v), 3)} for b, v in band.items()],
        "composition": {"lead": now, "2026": round(c26, 1), "2022": round(c22, 1), "change": round(c26 - c22, 1)},
        "gate": gate_rec,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="live", choices=["live", "history", "gate"])
    ap.add_argument("--src", type=Path, help="history: directory with the NCSBE zips")
    a = ap.parse_args(argv)
    if a.what == "history":
        build_history(a.src)
    elif a.what == "gate":
        print(json.dumps(gate(), indent=1))
    else:
        run_live()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
