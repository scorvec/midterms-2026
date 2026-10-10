"""North Carolina mail and early in-person ballots by SELF-REPORTED race and ethnicity, 2026 against 2022 and 2024 on the
same number of days before Election Day (2026-10-10; user, after Tom Bonier's "Early notes on early votes", whose VA/NJ
race figures are modelled from surname and geography: test the Hispanic / Black pattern where race is on the voter record).

DIAGNOSTIC ONLY - nothing here feeds the forecast, and nothing is on the site. Early ballots are uncounted and their make-up
is driven by who uses which mode, so every comparison is made three ways:
  * share of ballots         - the group's share of accepted ballots cast by day d (Bonier's measure);
  * index                    - that share divided by the group's share of REGISTERED voters on the same days-out, so a
                               change in who is registered is not read as a change in who votes (1.00 = proportional);
  * rate                     - accepted ballots per 1,000 registered voters of the group;
and each group's ballots are split by what the same voters did in the previous general of the same kind (2026 -> 2022,
2022 -> 2018, 2024 -> 2020: voted by mail / early in person / on Election Day / did not vote) and by low propensity (voted in
at most one of the three previous generals). Voters who did not vote last time are new turnout; voters who voted on
Election Day last time and early now are a MODE SHIFT that adds nothing on Election Day.

Sources (NC State Board of Elections, public, anonymous):
  * absentee_<election>.zip on dl.ncsbe.gov: one row per mail / one-stop ballot with race, ethnicity, party, age, county,
    request / send / return dates and status. The final 2022 and 2024 files keep every ballot's return date, so their pace at
    any days-out is reconstructed exactly; 2026 is the current file (one conditional request per run).
  * ncvhis_Statewide.zip: every voter's participation history (2016 general onward), joined on ncid. Reduced once to the
    generals 2016-2024 (data/cache/nc_hist_generals.csv.gz); history before 2026 does not change, so it is built only when
    missing from the cache (one ~350 MB download).
  * Voter Registration Statistics (vt.ncsbe.gov/RegStat), weekly Saturday snapshots by county with race and Hispanic
    ethnicity: the denominators, taken from the latest snapshot on or before the same days-out (registration.py caches them).
Race groups are NCSBE's race codes (white, Black, other = Asian, American Indian, Native Hawaiian, two or more, other;
undesignated); the index and rates use white / Black / "other or undesignated" because RegStat folded Asian, multiracial and
undesignated into "Other" until Dec 2023. Hispanic is the ETHNICITY field, any race, reported beside the race groups (it
overlaps them). Ethnicity is
undesignated for a growing share of voters (22 % of 2022's absentee rows, 28 % in 2024, 33 % of 2026 mail so far), so the
Hispanic share is also given among ballots with a designated ethnicity.

Traps (checked 2026-10-10):
  * ballot_req_type is "ONE-STOP" in 2018-2022 but "EARLY VOTING" in 2024, so in person = any code but "MAIL" (a mail ballot
    requested in person is still mail); the codes seen are kept in qc_2026.request_types.
  * Accepted = status starting "ACCEPTED" (incl. "- EXCEPTION", "- CURED"); one ballot per ncid; return dates after the file
    date are keying errors (two 10/29/2026 dates in the 4 Oct file were corrected to 09/29 later) and are dropped.
  * The newest days fill in late: between the 4 and 10 Oct 2026 files, ballots returned by 2 Oct grew 1.1 % (cures,
    late keying with the original return date). The 2026 reading is taken through the day before the file date.
  * 2022 accepted mail ballots that arrived up to 3 days after Election Day (10,694; the grace period ended in 2024) are
    outside every pre-Election-Day count.
  * Mail ballots went out on 9 Sep 2022 and 4 Sep 2026 (60 days out) but only from ~24 Sep 2024 (court-ordered reprint), so
    2024 mail at 25-40 days out is behind for reasons that have nothing to do with the voters; one-stop opened 19 days out in
    all three years (20 Oct 2022, 17 Oct 2024, 15 Oct 2026).
  * Rules changed after 2022: mail ballots need a copy of photo ID (2024) and must arrive by Election Day (no grace period).

Output: data/early/nc_early.json and the day's 2026 aggregate table data/early/nc/2026_<through>.csv (committed by the
early-diag workflow; data/early is not copied to the site) and the printed table.
Aggregates only - no voter-level row leaves this module.

    python -m midterms.nc_early                         # daily: 2026 file + comparison (needs data/static/nc_early_baseline.csv)
    python -m midterms.nc_early baseline --src DIR      # one-time: rebuild the 2022 / 2024 baseline from NCSBE files in DIR
                                                        # (absentee_20221108.csv, absentee_20241105.csv, ncvhis_Statewide.txt
                                                        # and RegStat snapshots DIR/regstat/<date>.json)
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ncsbe"
CACHE = ROOT / "data" / "cache"
REGSTAT = ROOT / "data" / "raw" / "registration" / "nc"
BASELINE = ROOT / "data" / "static" / "nc_early_baseline.csv"
EARLY = ROOT / "data" / "early"
OUT = EARLY / "nc_early.json"
S3 = "https://s3.amazonaws.com/dl.ncsbe.gov"
HIST_ZIP_URL = f"{S3}/data/ncvhis_Statewide.zip"
HIST = CACHE / "nc_hist_generals.csv.gz"

ED = {2022: dt.date(2022, 11, 8), 2024: dt.date(2024, 11, 5), 2026: dt.date(2026, 11, 3)}
ONESTOP_OUT = 19                                     # one-stop opened 19 days out in 2022, 2024 and 2026
GENERALS = {"g2016": "11/08/2016", "g2018": "11/06/2018", "g2020": "11/03/2020", "g2022": "11/08/2022", "g2024": "11/05/2024"}
PRIOR3 = {2022: ("g2016", "g2018", "g2020"), 2024: ("g2018", "g2020", "g2022"), 2026: ("g2020", "g2022", "g2024")}
PREV_SAME = {2022: "g2018", 2024: "g2020", 2026: "g2022"}   # the previous general of the same kind
MAX_OUT = 60
# history codes: 0 did not vote, 1 by mail, 2 early in person, 3 Election Day (incl. provisional / transfer)
METHOD = {"ABSENTEE BY MAIL": 1, "ABSENTEE": 1, "ABSENTEE ONESTOP": 2, "EARLY VOTING IN-PERSON": 2, "ABSENTEE CURBSIDE": 2,
          "EARLY VOTING CURBSIDE": 2, "ELECTION DAY IN-PERSON": 3, "ELECTION DAY CURBSIDE": 3, "PROVISIONAL": 3, "TRANSFER": 3,
          "LEGACY": 3}                               # "ELIGIBLE DID NOT VOTE" (1 row) is not a vote
RACE = {"WHITE": "white", "BLACK or AFRICAN AMERICAN": "black", "ASIAN": "other", "INDIAN AMERICAN or ALASKA NATIVE": "other",
        "NATIVE HAWAIIAN or PACIFIC ISLANDER": "other", "TWO or MORE RACES": "other", "OTHER": "other"}   # else undesignated
# RegStat denominators. Until 2023-12-17 RegStat folded Asian, multiracial and undesignated into "Other" (Undesignated = 0
# in every 2022 snapshot); the sum of the four is continuous across the change, so they are one denominator group here.
REG_COLS = {"white": ["White"], "black": ["Black"], "other_undesig": ["AmericanIndian", "NativeHawaiian", "Asian", "Multiracial",
            "Undesignated", "Other"], "hispanic": ["Hispanic"], "all": ["Total"]}
GROUPS = ["all", "white", "black", "other", "undesignated", "other_undesig", "hispanic", "eth_designated"]
MODES = ["mail", "in_person", "all"]
USE = ["ncid", "race", "ethnicity", "age", "ballot_req_delivery_type", "ballot_req_type", "ballot_rtn_dt", "ballot_rtn_status"]


# ── ballots ────────────────────────────────────────────────────────────────────────────────────────────────────────
def load_absentee(src, year: int, file_date: dt.date | None = None) -> tuple[pd.DataFrame, dict]:
    """Accepted ballots, one per voter: ncid, group columns, mode, days_out. `src` is a CSV path or CSV bytes."""
    d = pd.read_csv(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src, usecols=lambda c: c in USE, dtype=str,
                    encoding="latin-1", on_bad_lines="skip")
    for c in d.columns: d[c] = d[c].fillna("").str.strip()
    qc = {"rows": len(d)}
    d = d[d["ballot_rtn_status"].str.upper().str.startswith("ACCEPTED")].copy()
    d["rtn"] = pd.to_datetime(d["ballot_rtn_dt"], format="%m/%d/%Y", errors="coerce")
    qc["accepted"] = len(d); qc["no_return_date"] = int(d["rtn"].isna().sum())
    d = d[d["rtn"].notna()]
    last = pd.Timestamp(min(ED[year], file_date - dt.timedelta(days=1)) if file_date else ED[year])
    if file_date:
        bad = d["rtn"] > pd.Timestamp(file_date)
        qc["future_return_dates_dropped"] = int(bad.sum()); d = d[~bad]
    n0 = len(d); d = d.drop_duplicates("ncid", keep="first"); qc["duplicate_voters_dropped"] = n0 - len(d)
    d["days_out"] = (pd.Timestamp(ED[year]) - d["rtn"]).dt.days
    qc["after_election_day"] = int((d["days_out"] < 0).sum())
    rt = d["ballot_req_type"].str.upper()
    qc["request_types"] = {str(k): int(v) for k, v in rt.value_counts().items()}
    d["in_person"] = rt.ne("MAIL")          # ONE-STOP (2018-22) / EARLY VOTING (2024): any non-mail code is in person
    d["race_g"] = d["race"].map(RACE).fillna("undesignated")
    eth = d["ethnicity"].str.upper()
    d["hispanic"] = eth.eq("HISPANIC OR LATINO")
    d["eth_designated"] = eth.isin(["HISPANIC OR LATINO", "NOT HISPANIC OR NOT LATINO"])
    d["age"] = pd.to_numeric(d["age"], errors="coerce")
    qc["through"] = str(min(last, d["rtn"].max()).date()) if len(d) else None
    return d[["ncid", "race_g", "hispanic", "eth_designated", "age", "in_person", "days_out"]].reset_index(drop=True), qc


# ── history ────────────────────────────────────────────────────────────────────────────────────────────────────────
def build_history(src) -> pd.DataFrame:
    """ncvhis (txt path or zip path) -> one row per voter with a vote in any general 2016-2024: method code per general."""
    lbl = {v: k for k, v in GENERALS.items()}
    parts = {g: [] for g in GENERALS}
    def take(fh):
        for ch in pd.read_csv(fh, sep="\t", usecols=["ncid", "election_lbl", "voting_method"], encoding="latin-1",
                              dtype={"ncid": str, "election_lbl": "category", "voting_method": "category"},
                              chunksize=4_000_000, on_bad_lines="skip"):
            ch = ch[ch["election_lbl"].isin(lbl)]
            m = ch["voting_method"].astype(str).str.strip().map(METHOD)
            ch = ch.assign(m=m)[m.notna()]
            for lab, sub in ch.groupby("election_lbl", observed=True):
                parts[lbl[lab]].append(sub[["ncid", "m"]].astype({"m": np.int8}))
    if str(src).endswith(".zip"):
        with zipfile.ZipFile(src) as z:
            name = [n for n in z.namelist() if n.endswith(".txt")][0]
            with z.open(name) as fh: take(fh)
    else:
        take(src)
    cols = {}
    for g, ps in parts.items():                            # 692 duplicate voter-elections: keep the ballot method (lowest code)
        h = pd.concat(ps) if ps else pd.DataFrame({"ncid": [], "m": []})
        cols[g] = h.sort_values("m").drop_duplicates("ncid").set_index("ncid")["m"]
        parts[g] = None
    w = pd.DataFrame(cols).fillna(0).astype(np.int8)
    w.index.name = "ncid"
    return w[list(GENERALS)]


def history() -> pd.DataFrame:
    if HIST.exists():
        return pd.read_csv(HIST, dtype={"ncid": str}).set_index("ncid")
    import shutil, tempfile, urllib.request
    from . import fetch as F
    print("  NC history: building the 2016-2024 generals table from ncvhis_Statewide.zip (one download)", flush=True)
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / "ncvhis_Statewide.zip"
        req = urllib.request.Request(HIST_ZIP_URL, headers={"User-Agent": F.UA})
        with urllib.request.urlopen(req, timeout=900) as r, open(zp, "wb") as fh: shutil.copyfileobj(r, fh, 1 << 22)
        F.count(HIST_ZIP_URL, zp.stat().st_size)
        w = build_history(zp)
    HIST.parent.mkdir(parents=True, exist_ok=True)
    w.reset_index().to_csv(HIST, index=False, compression="gzip")
    print(f"  NC history: {len(w):,} voters -> {HIST.relative_to(ROOT)} ({HIST.stat().st_size / 1e6:.0f} MB)", flush=True)
    return w


def add_history(b: pd.DataFrame, hist: pd.DataFrame, year: int) -> pd.DataFrame:
    h = hist.reindex(b["ncid"]).fillna(0).astype(np.int8).to_numpy()
    cols = list(hist.columns)
    prior = np.stack([h[:, cols.index(g)] for g in PRIOR3[year]], 1)
    prev = h[:, cols.index(PREV_SAME[year])]
    return b.assign(low_prop=(prior > 0).sum(1) <= 1, prev_none=prev == 0, prev_mail=prev == 1, prev_early_ip=prev == 2,
                    prev_ed=prev == 3, youth=b["age"] < 30)


METRICS = ["n", "low_prop", "prev_none", "prev_mail", "prev_early_ip", "prev_ed", "youth"]


def cumulate(b: pd.DataFrame, year: int, max_out: int = MAX_OUT, min_out: int = 0) -> pd.DataFrame:
    """Cumulative counts of ballots cast by each days-out d (days_out >= d), per mode and group."""
    b = b[b["days_out"] >= min_out].assign(n=True, d=b["days_out"].clip(upper=max_out))
    days = np.arange(max_out, min_out - 1, -1)
    rows = []
    for mode in MODES:
        bm = b if mode == "all" else b[b["in_person"] == (mode == "in_person")]
        for g in GROUPS:
            sel = (bm if g == "all" else bm[bm["race_g"].isin(["other", "undesignated"])] if g == "other_undesig"
                   else bm[bm["race_g"] == g] if g in ("white", "black", "other", "undesignated") else bm[bm[g]])
            per = sel.groupby("d")[METRICS].sum().reindex(days, fill_value=0)
            cum = per.cumsum()                       # days run from max_out down, so this is "cast by day d"
            cum = cum.assign(year=year, mode=mode, group=g, days_out=days)
            rows.append(cum.reset_index(drop=True))
    out = pd.concat(rows, ignore_index=True)
    return out[["year", "mode", "group", "days_out"] + METRICS].astype({m: int for m in METRICS})


# ── registration (denominators) ────────────────────────────────────────────────────────────────────────────────────
def regstat_table(src_dir: Path = REGSTAT) -> pd.DataFrame:
    rows = []
    for p in sorted(Path(src_dir).glob("20*.json")):
        t = [x for x in json.loads(p.read_text()) if str(x.get("CountyName", "")).strip().lower() == "totals"]
        if not t: continue
        t = t[0]
        rows.append({"date": p.stem, **{g: int(sum(t[c] for c in cs)) for g, cs in REG_COLS.items()}})
    R = pd.DataFrame(rows)
    if len(R):
        R["date"] = pd.to_datetime(R["date"])
        gap = (R[["white", "black", "other_undesig"]].sum(1) - R["all"]).abs().max()
        if gap: print(f"  !! RegStat race columns do not add up to the total (max gap {gap})")
    return R


def reg_at(R: pd.DataFrame, year: int, days_out: int) -> dict | None:
    """The latest weekly snapshot on or before Election Day - days_out (None when there is none within 8 days)."""
    tgt = pd.Timestamp(ED[year] - dt.timedelta(days=days_out))
    s = R[(R["date"] <= tgt) & (R["date"] > tgt - pd.Timedelta(days=8))]
    if not len(s): return None
    r = s.iloc[-1]
    return {"date": str(r["date"].date()), **{g: int(r[g]) for g in REG_COLS}}


def add_reg(C: pd.DataFrame, R: pd.DataFrame) -> pd.DataFrame:
    keys = C[["year", "days_out"]].drop_duplicates()
    regs = {(y, d): reg_at(R, y, d) for y, d in keys.itertuples(index=False)}
    C = C.copy()
    C["reg_date"] = [(regs[(y, d)] or {}).get("date") for y, d in zip(C["year"], C["days_out"])]
    C["reg_n"] = [(regs[(y, d)] or {}).get(g) if g in REG_COLS else None for y, d, g in zip(C["year"], C["days_out"], C["group"])]
    return C


# ── baseline (one-time, from the final 2022 / 2024 files) ──────────────────────────────────────────────────────────
def baseline(src: Path):
    src = Path(src)
    print("  NC baseline: reading history ...", flush=True)
    hist = build_history(src / "ncvhis_Statewide.txt")
    out, qcs = [], {}
    for y in (2022, 2024):
        b, qc = load_absentee(src / f"absentee_{ED[y]:%Y%m%d}.csv", y)
        qc["in_history_this_election"] = round(float(hist.reindex(b["ncid"])[f"g{y}"].fillna(0).gt(0).mean()), 4)
        qcs[y] = qc; print(f"  {y}:", qc, flush=True)
        out.append(cumulate(add_history(b, hist, y), y))
    C = add_reg(pd.concat(out, ignore_index=True), regstat_table(src / "regstat"))
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    C.to_csv(BASELINE, index=False)
    (BASELINE.with_suffix(".qc.json")).write_text(json.dumps({str(k): v for k, v in qcs.items()}, indent=1))
    print(f"  wrote {BASELINE.relative_to(ROOT)} ({len(C)} rows)")


# ── 2026 ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def fetch_2026() -> tuple[bytes, dt.date]:
    from . import fetch as F
    url = f"{S3}/ENRS/{ED[2026]:%Y_%m_%d}/absentee_{ED[2026]:%Y%m%d}.zip"
    raw, _ = F.get(url, RAW / f"absentee_{ED[2026]:%Y%m%d}.zip", timeout=300)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        info = [i for i in z.infolist() if i.filename.endswith(".csv")][0]
        return z.read(info), dt.date(*info.date_time[:3])          # the CSV's own timestamp = the file date


def regstat_2026() -> pd.DataFrame:
    """Use the snapshots registration.py caches; fetch the newest Saturdays if they are missing (one request each)."""
    R = regstat_table()
    need = [ED[2026] - dt.timedelta(days=k) for k in range(0, 70)]
    sats = sorted({d - dt.timedelta(days=(d.weekday() - 5) % 7) for d in need if d <= dt.date.today()})
    have = set(R["date"].dt.date) if len(R) else set()
    miss = [s for s in sats[-3:] if s not in have]
    if miss:
        import requests
        from . import registration as RG
        s = requests.Session(); s.headers.update(RG.UA)
        listed = set(RG.nc_dates(s, ED[2026].year))
        for d in miss:
            if d.isoformat() in listed: RG.nc_week(s, d.isoformat())
        R = regstat_table()
    return R


def pct(a, b):
    return None if not b else round(100 * a / b, 2)


def two_prop_z(a1, n1, a2, n2):
    if not (n1 and n2): return None
    p = (a1 + a2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0
    return None if not se else round((a1 / n1 - a2 / n2) / se, 1)


def compare(C: pd.DataFrame, d: int) -> dict:
    """At days-out d: per mode and group, counts, shares, index, rates and the history split, 2026 vs 2022 vs 2024."""
    out = {}
    X = C[C["days_out"] == d].set_index(["year", "mode", "group"])
    for mode in MODES:
        m = {}
        for g in GROUPS:
            row = {}
            for y in (2026, 2022, 2024):
                if (y, mode, g) not in X.index: continue
                r, tot = X.loc[(y, mode, g)], X.loc[(y, mode, "all")]
                base = X.loc[(y, mode, "eth_designated")]["n"] if g == "hispanic" else tot["n"]
                e = {"n": int(r["n"]), "share_pct": pct(r["n"], tot["n"])}
                if g == "hispanic": e["share_of_designated_pct"] = pct(r["n"], base)
                if pd.notna(r.get("reg_n")) and r.get("reg_n") and pd.notna(tot.get("reg_n")) and tot["n"]:
                    reg_share = r["reg_n"] / tot["reg_n"]
                    e.update(reg_n=int(r["reg_n"]), reg_share_pct=round(100 * reg_share, 2),
                             index=round((r["n"] / tot["n"]) / reg_share, 3),
                             per_1000_registered=round(1000 * r["n"] / r["reg_n"], 2), reg_date=r["reg_date"])
                if r["n"]:
                    e.update(low_prop_pct=pct(r["low_prop"], r["n"]), youth_pct=pct(r["youth"], r["n"]),
                             prev_general={k: pct(r[f"prev_{k}"], r["n"]) for k in ("none", "mail", "early_ip", "ed")})
                row[str(y)] = e
            for y in ("2022", "2024"):
                if "2026" in row and y in row and g != "all":
                    a, b = X.loc[(2026, mode, g)], X.loc[(int(y), mode, g)]
                    na, nb = X.loc[(2026, mode, "all")]["n"], X.loc[(int(y), mode, "all")]["n"]
                    row[f"z_share_vs_{y}"] = two_prop_z(a["n"], na, b["n"], nb)
            m[g] = row
        out[mode] = m
    return out


def fill_in(C26: pd.DataFrame, asof: dt.date) -> dict | None:
    """Against the previous day's table: how much the earlier days grew (late keying, cures) and whether any count fell."""
    prev = sorted(p for p in (EARLY / "nc").glob("2026_*.csv") if p.stem < f"2026_{asof:%Y%m%d}")
    if not prev: return None
    P = pd.read_csv(prev[-1])
    k = ["mode", "group", "days_out"]
    m = C26[C26["mode"].eq("all") & C26["group"].eq("all")].merge(P[P["mode"].eq("all") & P["group"].eq("all")], on=k,
                                                                   suffixes=("", "_prev"))
    m = m[m["n_prev"] > 0]
    if not len(m): return None
    g = 100 * (m["n"] / m["n_prev"] - 1)
    last = m.loc[m["days_out"].idxmin()]
    return {"previous": prev[-1].stem.split("_")[1], "growth_at_previous_last_day_pct": round(float(100 * (last["n"] / last["n_prev"] - 1)), 2),
            "max_drop_pct": round(float(g.min()), 2), "days_compared": int(len(m))}


def run() -> dict:
    if not BASELINE.exists():
        raise SystemExit(f"missing {BASELINE.relative_to(ROOT)} - run the baseline first")
    raw, file_date = fetch_2026()
    b, qc = load_absentee(raw, 2026, file_date)
    asof = dt.date.fromisoformat(qc["through"])
    d_now = (ED[2026] - asof).days
    hist = history()
    b = add_history(b, hist, 2026)
    C26 = add_reg(cumulate(b, 2026, min_out=d_now), regstat_2026())
    (EARLY / "nc").mkdir(parents=True, exist_ok=True)       # the day's table: later files show how far each day filled in
    fill = fill_in(C26, asof)
    if fill and fill["max_drop_pct"] < -1:
        print(f"  !! NC: cumulative ballots by some day FELL by {fill['max_drop_pct']} % since {fill['previous']}", flush=True)
    C26.to_csv(EARLY / "nc" / f"2026_{asof:%Y%m%d}.csv", index=False)
    B = pd.read_csv(BASELINE)
    C = pd.concat([B, C26], ignore_index=True)
    pace_days = list(range(min(MAX_OUT, 45), d_now - 1, -1))
    pace = {mode: {g: {str(y): [int(v) for v in C[(C.year == y) & (C["mode"] == mode) & (C.group == g)].set_index("days_out")
                                .reindex(pace_days)["n"].fillna(-1)] for y in (2022, 2024, 2026)}
                   for g in ("all", "white", "black", "hispanic")} for mode in ("mail", "in_person")}
    res = {"asof": asof.isoformat(), "file_date": file_date.isoformat(), "days_out": d_now, "qc_2026": qc,
           "note": ("DIAGNOSTIC ONLY. NCSBE absentee files (self-reported race/ethnicity), accepted ballots cast by the same number "
                    "of days before Election Day; denominators = NCSBE weekly registration statistics on the same days-out. "
                    "The newest day fills in by ~1 % over the following days. 2024 mail went out ~2 weeks late; one-stop opens "
                    "19 days out (15 Oct 2026). Composition is mode-driven: see prev_general (what the same voters did in the "
                    "previous general of the same kind) and low_prop before reading a share change as new turnout."),
           "onestop_opens_days_out": ONESTOP_OUT, "fill_in": fill, "compare": compare(C, d_now), "pace_days_out": pace_days, "pace": pace}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, separators=(",", ":"), allow_nan=False))
    return res


def show(res: dict):
    d = res["days_out"]
    print(f"\nNC accepted ballots cast by {d} days out (2026 through {res['asof']}; file {res['file_date']}) - "
          "share %, index = share / registered share, per 1,000 registered, low-propensity %, previous same-kind general")
    for mode in MODES:
        cmp = res["compare"][mode]
        if not cmp["all"].get("2026", {}).get("n"):
            print(f"  {mode}: no 2026 ballots yet"); continue
        print(f"  -- {mode}: total " + "  ".join(f"{y} {cmp['all'][y]['n']:,}" for y in ("2026", "2022", "2024") if y in cmp["all"]))
        for g in GROUPS[1:]:
            parts = []
            for y in ("2026", "2022", "2024"):
                e = cmp[g].get(y)
                if not e: continue
                s = f"{y} {e['n']:>7,} {e['share_pct']:5.2f}%"
                if "share_of_designated_pct" in e: s += f" ({e['share_of_designated_pct']:.2f}% of desig.)"
                if e.get("index") is not None: s += f" idx {e['index']:.2f} {e['per_1000_registered']:6.2f}/k"
                if e.get("low_prop_pct") is not None:
                    pg = e["prev_general"]; s += f" lowp {e['low_prop_pct']:.0f}% prev none/ED {pg['none']:.0f}/{pg['ed']:.0f}"
                parts.append(s)
            z = "  z vs 22/24 " + "/".join(str(cmp[g].get(f"z_share_vs_{y}")) for y in ("2022", "2024"))
            print(f"    {g:<14}" + " | ".join(parts) + z)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "baseline"])
    ap.add_argument("--src", help="baseline: directory with the final NCSBE files and regstat/<date>.json")
    a = ap.parse_args(argv)
    if a.cmd == "baseline":
        if not a.src: raise SystemExit("baseline needs --src")
        baseline(Path(a.src)); return 0
    show(run())
    from . import fetch as F; F.report(); return 0


if __name__ == "__main__":
    sys.exit(main())
