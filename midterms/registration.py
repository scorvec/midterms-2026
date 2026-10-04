"""Voter registration by party: North Carolina and Pennsylvania (2026-10-03; user: "Something else we haven't paid much
attention to - voter registration ... the canary in the coal mine in 2024", then "build that for NC and PA").

DIAGNOSTIC ONLY - nothing here feeds the forecast. Registration moves for reasons that are not vote intention: list
maintenance (both states purge inactive voters on a cycle, which removes low-turnout voters of whichever party has more of
them), automatic registration at the DMV (PA since Sept 2023, defaulting new registrants toward "no affiliation"),
party switching by people who already voted the other way (southern Democrats re-registering Republican), and the
long drift to unaffiliated. So the series are compared with THEMSELVES: the same calendar window in earlier cycles.

Sources (official, public, anonymous):
  North Carolina  NC State Board of Elections "Voter Registration Statistics" (vt.ncsbe.gov/RegStat), one snapshot per
                  week (Saturdays) back to 2004, by county and party. The results page embeds the county table as JSON;
                  one request per week, cached per date under data/raw/registration/nc/.
  Pennsylvania    PA Department of State: certified registration statistics at every primary and general election
                  (PDF, statewide party totals) and the CURRENT weekly workbook (currentvotestats.xlsx, updated Mondays,
                  no public archive of past weeks), which this module saves every run so the weekly series accrues from
                  2026-10-03 on. The workbook also carries party-to-party switch counts for the current year.

    python -m midterms.registration              # refresh both states, write data/cache/registration_*.csv and web/data/registration.json
    python -m midterms.registration --nc-since 2016-01-01
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import time
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "registration"
CACHE = ROOT / "data" / "cache"
WEB = ROOT / "web" / "data"
UA = {"User-Agent": "scorvec.com midterm model (+https://scorvec.com/midterms/about.html)"}

NC_BASE = "https://vt.ncsbe.gov/RegStat/"
PA_DAM = "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/voting-and-elections/voting-and-election-statistics/"
PA_PAGE = "https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/voting-and-election-statistics"
GENERALS = {2016: "2016-11-08", 2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05",
            2026: "2026-11-03"}


# ── North Carolina ──────────────────────────────────────────────────────────────────────────────────────────────────
def nc_dates(session, year: int) -> list[str]:
    fp = RAW / "nc" / f"dates_{year}.json"                 # past years' snapshot lists never change: asked once
    if year < dt.date.today().year and fp.exists(): return json.loads(fp.read_text())
    out = _nc_dates(session, year)
    if year < dt.date.today().year and out: fp.parent.mkdir(parents=True, exist_ok=True); fp.write_text(json.dumps(out))
    return out


def _nc_dates(session, year: int) -> list[str]:
    h = session.get(NC_BASE, params={"handler": "YearDropdownPartial", "year": year},
                    headers={"X-Requested-With": "XMLHttpRequest"}, timeout=60).text
    return sorted({pd.Timestamp(v).strftime("%Y-%m-%d") for v in re.findall(r'value="(\d{2}/\d{2}/\d{4})"', h)})


def nc_week(session, day: str) -> list[dict] | None:
    fp = RAW / "nc" / f"{day}.json"
    if fp.exists():
        return json.loads(fp.read_text())
    for k in range(3):
        try:
            r = session.get(NC_BASE + "Results", params={"date": day}, timeout=90)
            m = re.search(r'"data":\{"Data":(\[.*?\])', r.text)
            if r.status_code == 200 and m:
                rows = json.loads(m.group(1))
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(json.dumps(rows, separators=(",", ":")))
                return rows
        except requests.RequestException:
            pass
        time.sleep(5 * (k + 1))
    print(f"  !! NC {day}: no table", flush=True)
    return None


def nc_series(since: str = "2016-01-01") -> pd.DataFrame:
    s = requests.Session(); s.headers.update(UA)
    days = []
    for y in range(pd.Timestamp(since).year, dt.date.today().year + 1):
        days += [d for d in nc_dates(s, y) if d >= since]
    out, new = [], 0
    for d in days:
        cached = (RAW / "nc" / f"{d}.json").exists()
        rows = nc_week(s, d)
        if not cached:
            new += 1
            time.sleep(1.0)                                              # one request a second: a public state server
        if not rows:
            continue
        tot = [x for x in rows if x["CountyName"].strip().lower() == "totals"]
        t = tot[0] if tot else {k: sum(x[k] for x in rows if isinstance(x.get(k), (int, float))) for k in rows[0]}
        other = sum(t.get(k, 0) for k in ("Libertarians", "Green", "NoLabels", "Constitution", "JusticeForAll", "WeThePeople"))
        out.append({"date": d, "dem": t["Democrats"], "rep": t["Republicans"], "unaff": t["Unaffiliated"], "other": other,
                    "total": t["Total"]})
    print(f"  NC: {len(out)} weekly snapshots ({new} fetched now), {out[0]['date']} -> {out[-1]['date']}", flush=True)
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"])
    chk = (df[["dem", "rep", "unaff", "other"]].sum(1) - df["total"]).abs().max()
    if chk > 0:
        print(f"  !! NC party columns do not add up to the total (max gap {chk})", flush=True)
    return df


# ── Pennsylvania ────────────────────────────────────────────────────────────────────────────────────────────────────
def pa_certified() -> pd.DataFrame:
    """Statewide Dem / Rep / No affiliation / Other / Total at every certified primary and general, from the PDFs."""
    import fitz
    html = requests.get(PA_PAGE, headers=UA, timeout=60).text
    links = sorted(set(re.findall(r'href="([^"]*voter-registration-statistics/[^"]+\.pdf)"', html)))
    rows = []
    for href in links:
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        fp = RAW / "pa" / "certified" / name
        if not fp.exists():
            fp.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(urllib.parse.urljoin("https://www.pa.gov", href), headers=UA, timeout=120)
            if r.status_code != 200:
                print(f"  !! PA {name}: HTTP {r.status_code}", flush=True); continue
            fp.write_bytes(r.content); time.sleep(1.0)
        yr = re.search(r"(20\d\d)", name)
        if not yr or int(yr.group(1)) < 2015:
            continue
        kind = "primary" if re.search(r"primary", name, re.I) else "general"
        doc = fitz.open(fp)
        text = "\n".join(p.get_text() for p in doc)
        rec = pa_parse_totals(text)
        if rec is None:
            print(f"  !! PA {name}: statewide totals not found", flush=True); continue
        rec.update(year=int(yr.group(1)), kind=kind, file=name)
        rows.append(rec)
    df = pd.DataFrame(rows)
    # the certification date is not in a fixed place; the election date is: May primary / November general
    df["date"] = [pd.Timestamp(GENERALS[y]) if k == "general" and y in GENERALS else
                  pd.Timestamp(f"{y}-11-0{2 + (y % 2)}") if k == "general" else pd.Timestamp(f"{y}-05-15")
                  for y, k in zip(df.year, df.kind)]
    return df.sort_values("date").reset_index(drop=True)


def pa_parse_totals(text: str) -> dict | None:
    """The statewide total line of a certified-statistics PDF ('Total', or 'PENNSYLVANIA' in 2012): the party columns in
    the order of the header (Republican came first until 2014, Democratic after), then the all-parties total (printed
    twice in some years). Only Dem, Rep and the total are taken; the rest is 'all others' (no affiliation + minor
    parties), because the PDFs do not split no-affiliation the same way every year."""
    lines = [l.strip() for l in text.splitlines()]
    num = lambda s: int(s.replace(",", "")) if re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", s) else None
    head = " ".join(lines[:40]).lower()
    rep_first = 0 <= head.find("republican") < head.find("democrat")
    for i in range(len(lines) - 1, -1, -1):
        if re.fullmatch(r"(?i)(state\s*)?totals?:?|pennsylvania", lines[i]):
            vals = []
            for l in lines[i + 1:i + 12]:
                if not l:
                    continue
                v = num(l)
                if v is None:
                    break
                vals.append(v)
            while len(vals) >= 2 and vals[-1] == vals[-2]:
                vals.pop()                                                 # the total printed twice
            if len(vals) >= 4 and vals[-1] > 7_000_000 and sum(vals[:-1]) == vals[-1]:
                a1, a2, tot = vals[0], vals[1], vals[-1]
                d, r = (a2, a1) if rep_first else (a1, a2)
                return {"dem": d, "rep": r, "unaff": np.nan, "other": tot - d - r, "total": tot}
    return None


def pa_weekly() -> pd.DataFrame:
    """Archive this week's workbook (the state keeps no past weeks) and return every archived week."""
    wk = RAW / "pa" / "weekly"; wk.mkdir(parents=True, exist_ok=True)
    r = requests.get(PA_DAM + "currentvotestats.xlsx", headers=UA, timeout=120)
    if r.status_code == 200:
        head = pd.read_excel(io.BytesIO(r.content), sheet_name=0, header=None, nrows=1).iloc[0, 0]
        m = re.search(r"(\d{2}/\d{2}/\d{4})", str(head))
        if m:
            (wk / f"{pd.Timestamp(m.group(1)):%Y-%m-%d}.xlsx").write_bytes(r.content)
    rows = []
    for fp in sorted(wk.glob("*.xlsx")):
        t = pd.read_excel(fp, sheet_name=0, header=None)
        tot = t[t[0].astype(str).str.strip().str.lower().str.startswith("total")].iloc[0]
        v = [x for x in tot.tolist()[1:] if isinstance(x, (int, float, np.integer, np.floating)) and pd.notna(x)]
        dem, rep, noaff, oth, total = [int(x) for x in v[-5:]]
        rows.append({"date": pd.Timestamp(fp.stem), "dem": dem, "rep": rep, "unaff": noaff, "other": noaff + oth, "total": total,
                     "kind": "weekly", "year": int(fp.stem[:4])})
    return pd.DataFrame(rows)


def pa_switches() -> dict | None:
    """Party-to-party switches so far this year from the newest archived workbook (sheet 'Party-to-Party(<year>)')."""
    files = sorted((RAW / "pa" / "weekly").glob("*.xlsx"))
    if not files:
        return None
    x = pd.ExcelFile(files[-1])
    sh = [s for s in x.sheet_names if s.lower().startswith("party-to-party") and "past" not in s.lower()]
    if not sh:
        return None
    t = pd.read_excel(x, sh[0], header=None)
    tot = t[t[0].astype(str).str.strip().str.lower().str.startswith("total")]
    hdr = None
    for i in range(min(6, len(t))):
        if t.iloc[i].astype(str).str.contains("Dem", case=False).any():
            hdr = [str(c).strip() for c in t.iloc[i].tolist()]; break
    if tot.empty or hdr is None:
        return None
    vals = tot.iloc[0].tolist()
    return {"asof": files[-1].stem, "sheet": sh[0], "columns": hdr,
            "totals": [None if (isinstance(v, float) and np.isnan(v)) else (int(v) if isinstance(v, (int, float, np.integer)) else str(v))
                       for v in vals]}


# ── comparisons ─────────────────────────────────────────────────────────────────────────────────────────────────────
def margin(df):
    df = df.copy()
    df["d_minus_r"] = df["dem"] - df["rep"]
    df["dem_sh"] = 100 * df["dem"] / df["total"]
    df["rep_sh"] = 100 * df["rep"] / df["total"]
    df["unaff_sh"] = 100 * df["unaff"] / df["total"]
    df["margin_pts"] = df["dem_sh"] - df["rep_sh"]
    return df


def nc_matched(nc: pd.DataFrame) -> pd.DataFrame:
    """Each cycle measured from the first snapshot of December after the previous general (the post-election roll,
    before the odd-year list maintenance) to the same days-to-the-election point as the newest 2026 snapshot."""
    today = nc["date"].max()
    days_out = (pd.Timestamp(GENERALS[2026]) - today).days
    rows = []
    for y in (2018, 2020, 2022, 2024, 2026):
        e = pd.Timestamp(GENERALS[y])
        base = nc[nc.date >= pd.Timestamp(f"{y - 2}-12-01")].iloc[0]
        mid = nc[nc.date >= pd.Timestamp(f"{y}-01-01")].iloc[0]          # start of the election year
        end = nc[nc.date <= e - pd.Timedelta(days=days_out)].iloc[-1]
        rows.append({"cycle": y, "base": base.date, "jan": mid.date, "end": end.date,
                     "dem_chg": int(end.dem - base.dem), "rep_chg": int(end.rep - base.rep),
                     "unaff_chg": int(end.unaff - base.unaff), "total_chg": int(end.total - base.total),
                     "net_d_minus_r": int((end.dem - end.rep) - (base.dem - base.rep)),
                     "net_d_minus_r_since_jan": int((end.dem - end.rep) - (mid.dem - mid.rep)),
                     "margin_pts_chg": round(float(end.margin_pts - base.margin_pts), 2),
                     "margin_pts_chg_since_jan": round(float(end.margin_pts - mid.margin_pts), 2),
                     "unaff_sh_chg": round(float(end.unaff_sh - base.unaff_sh), 2)})
    return pd.DataFrame(rows)


def pa_windows(pa: pd.DataFrame) -> pd.DataFrame:
    """Certified-to-certified windows: previous November -> May primary of the election year, and the election-year
    November where it exists; 2026 adds the newest weekly snapshot."""
    rows = []
    for y in (2018, 2020, 2022, 2024, 2026):
        prev = pa[(pa.kind == "general") & (pa.year == y - 1)]
        prim = pa[(pa.kind == "primary") & (pa.year == y)]
        gen = pa[(pa.kind == "general") & (pa.year == y)]
        wk = pa[(pa.kind == "weekly") & (pa.year == y)]
        if prev.empty or prim.empty:
            continue
        a, b = prev.iloc[0], prim.iloc[0]
        r = {"cycle": y, "from": a.date, "to_primary": b.date,
             "net_d_minus_r_to_primary": int((b.dem - b.rep) - (a.dem - a.rep)),
             "margin_pts_chg_to_primary": round(float(b.margin_pts - a.margin_pts), 2)}
        end = gen.iloc[0] if not gen.empty else (wk.iloc[-1] if not wk.empty else None)
        if end is not None:
            r.update(to_late=end.date, late_is=("certified general" if not gen.empty else "weekly"),
                     net_d_minus_r_to_late=int((end.dem - end.rep) - (a.dem - a.rep)),
                     margin_pts_chg_to_late=round(float(end.margin_pts - a.margin_pts), 2),
                     unaff_sh_chg_to_late=round(float(end.unaff_sh - a.unaff_sh), 2) if pd.notna(end.unaff_sh) and pd.notna(a.unaff_sh) else None)
        rows.append(r)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nc-since", default="2016-01-01")
    a = ap.parse_args()
    nc = margin(nc_series(a.nc_since))
    nc.to_csv(CACHE / "registration_nc.csv", index=False)
    pac = pa_certified()
    paw = pa_weekly()
    pa = margin(pd.concat([pac, paw], ignore_index=True).sort_values("date").reset_index(drop=True))
    pa.to_csv(CACHE / "registration_pa.csv", index=False)
    ncm, paw_ = nc_matched(nc), pa_windows(pa)
    print("\nNorth Carolina, same days before the election, from the December after the previous general:")
    print(ncm.to_string(index=False))
    print("\nPennsylvania, certified previous November -> May primary -> (November or newest week):")
    print(paw_.to_string(index=False))
    sw = pa_switches()
    j = {"asof": dt.date.today().isoformat(),
         "nc": {"series": nc.assign(date=nc.date.dt.strftime("%Y-%m-%d"))[["date", "dem", "rep", "unaff", "other", "total"]].to_dict("list"),
                "matched": json.loads(ncm.to_json(orient="records", date_format="iso"))},
         "pa": {"series": pa.assign(date=pa.date.dt.strftime("%Y-%m-%d"))[["date", "kind", "dem", "rep", "unaff", "other", "total"]]
                .astype(object).where(pd.notna(pa[["date", "kind", "dem", "rep", "unaff", "other", "total"]]), None).to_dict("list"),
                "windows": json.loads(paw_.to_json(orient="records", date_format="iso")), "switches": sw},
         "generals": GENERALS}
    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "registration.json").write_text(json.dumps(j, separators=(",", ":"), default=str))
    print(f"\nwrote {WEB / 'registration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
