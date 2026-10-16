"""Georgia early vote: who has voted, against 2022 at the same point. Diagnostic only - nothing here feeds the forecast.

The composition read would enter the forecast only if a backtest showed skill; no such test is possible yet (below).

Source: georgiavotes.com (an independent site compiled from the Georgia Secretary of State's free absentee voter files and voter
history - the SOS site itself sits behind a browser check). Its pages are open to crawlers; /api/ is disallowed in its
robots.txt and is never used here. One page a day (the home page carries everything), identified User-Agent. Aggregates only.

What it gives, per snapshot: ballots so far (mail + in person) and the 2022 general's count at the same point, statewide and
for each of the 159 counties; race / gender / age of 2026 voters; their 2022 voting (early, election day, none).
2022 at the same days out (race / age / gender / 2018 behaviour) comes from the Wayback Machine's daily captures of the same
site in October-November 2022 (data/static/ga_wayback_2022.json, built once, gaps filled on request).

Measures (2026 against 2022; Election Day 2026-11-03, 2022-11-08):
  * turnout so far vs 2022, statewide and metro Atlanta (10 core counties) vs the rest;
  * county mix: sum_c (share_2026_c - share_2022_c) x margin_c, with each county's 2022 governor and 2024 presidential
    margin (data/static/ga_county_baselines.csv). Read: how far the margin would move if every county voted as it did then
    and only the geography of who has voted changed. It is not a forecast;
  * race, age and gender shares against 2022 at the same days out; share who skipped the previous midterm general.

Why it does not feed the forecast: a skill test needs past cycles with both a same-day baseline and an outcome to
score. The 2022 and 2024 versions of the site (Wayback) show each cycle's own composition and only the previous
cycle's total at the same point, and county comparisons exist only on the 2026 site - so no past cycle can be scored, and
SB 202 (2021) changed how Georgians vote early. Revisit after 2026 with 2022 -> 2026 as the first scoreable pair.

    python -m midterms.ga_early              # snapshot + compare -> data/early/ga/<date>.json, data/early/ga_early.json
    python -m midterms.ga_early wayback      # the 2022 baseline from the Wayback Machine (incremental)
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import fetch as F

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "early"
SNAP = OUT / "ga"
BASE = ROOT / "data" / "static" / "ga_county_baselines.csv"
WAY = ROOT / "data" / "static" / "ga_wayback_2022.json"
SITE = "https://georgiavotes.com/"
ELECTION = {2026: date(2026, 11, 3), 2022: date(2022, 11, 8)}
# in-person early voting began 21 days out in 2026 (Tue 13 Oct, after Columbus Day) but 22 days out in 2022 (Mon 17 Oct), so the
# 2022 composition baseline is matched on the day of in-person voting, not days out. (The site's own "at this point in 2022"
# totals - cur / cmp - match on days out: in the first days they set 2026 against one more 2022 voting day.)
EV_START = {2026: date(2026, 10, 13), 2022: date(2022, 10, 17)}
METRO = {
    "FULTON",
    "DEKALB",
    "COBB",
    "GWINNETT",
    "CLAYTON",
    "HENRY",
    "DOUGLAS",
    "ROCKDALE",
    "CHEROKEE",
    "FORSYTH",
}  # ARC core 10
NUM = r"([\d,]+)"


def _text(html: str) -> str:
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#x27;|&#39;", " ", t)
    return re.sub(r"\s+", " ", t)


def _n(s):
    return int(s.replace(",", ""))


def _table(txt, title, labels):
    """{label: votes} from a 'Breakdown by <title>' block: 'Race Votes Vote % White 3,328 60.1% ...'."""
    i = txt.find(f"Breakdown by {title}")
    if i < 0:
        return {}
    seg = txt[i : i + 900]
    out = {}
    for lab in labels:
        m = re.search(re.escape(lab) + r" " + NUM + r" [\d.]+%", seg)
        if m:
            out[lab] = _n(m.group(1))
    return out


def parse(html: str, year: int) -> dict:
    txt = _text(html)
    m = re.search(r"Last updated (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})", txt)
    upd = datetime.strptime(m.group(1), "%Y-%m-%d") if m else None
    hour = int(m.group(2)) if m else 12
    m2 = re.search(
        r"So far in the \d{4} general election\s*,\s*"
        + NUM
        + r" people have voted\s*\.\s*At this point in the (\d{4}) general "
        r"election\s*,\s*that number was\s*" + NUM,
        txt,
    )
    rec = {
        "year": year,
        "last_updated": m.group(1) + " " + m.group(2) + ":" + m.group(3) if m else None,
        # morning updates hold the counties' uploads through the previous day; an evening update holds that day
        "through": str((upd.date() - timedelta(days=1)) if hour < 15 else upd.date()) if upd else None,
        "total": _n(m2.group(1)) if m2 else None,
        "cmp_year": int(m2.group(2)) if m2 else None,
        "cmp_total": _n(m2.group(3)) if m2 else None,
    }
    m3 = re.search(r"breakdown is\s*" + NUM + r"\s*mail ballots and\s*" + NUM + r"\s*early votes", txt)
    if m3:
        rec["mail"], rec["in_person"] = _n(m3.group(1)), _n(m3.group(2))
    rec["race"] = _table(txt, "Race", ("White", "Black", "Hispanic", "Asian", "Other"))
    rec["gender"] = _table(txt, "Gender", ("Female", "Male", "Unknown"))
    rec["age"] = _table(txt, "Age", ("18-29", "30-39", "40-49", "50-64", "65+", "Unknown"))
    # previous-midterm behaviour: "<n> (<p>%) of <yr> general early voters voted early / on election day / did not vote in the <prev>"
    prev = {}
    for key, pat in (
        ("early", r"voted early in the (\d{4}) general"),
        ("election_day", r"voted on election day in the (\d{4}) general"),
        ("none", r"did not vote in the (\d{4}) general"),
    ):
        mm = re.search(
            NUM + r" \( ?[\d.]+% ?\) of \d{4} (?:general )?early voters " + pat, txt
        )  # 2022: "general early voters"; 2026: "early voters"
        if mm:
            prev[key] = _n(mm.group(1))
            prev["year"] = int(mm.group(2))
    rec["previous_general"] = prev
    # counties (2026 site only): the page's embedded data [{"change","cmp","cur","label","name"}, ...]
    blob = "".join(re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, flags=re.S))
    try:
        blob = blob.encode().decode("unicode_escape", errors="ignore")
        mc = re.search(r'\[\{"change":[^\]]*\]', blob)
        rec["counties"] = (
            {x["name"].upper(): {"cur": x["cur"], "cmp": x["cmp"]} for x in json.loads(mc.group(0))} if mc else {}
        )
    except Exception:
        rec["counties"] = {}
    return rec


def days_out(rec):
    return (ELECTION[rec["year"]] - date.fromisoformat(rec["through"])).days if rec.get("through") else None


def ev_day(rec):
    """Days of in-person early voting included (0 = before it began)."""
    return max(0, (date.fromisoformat(rec["through"]) - EV_START[rec["year"]]).days + 1) if rec.get("through") else None


def wayback():
    """The 2022 baseline: Wayback captures of the 2022 site (Oct 17 - Nov 8, 2022) parsed into data/static/ga_wayback_2022.json.
    Incremental: keeps what is there and tries the first and the last capture of each day not yet parsed (the site updated
    through the day, so the two can be different days of voting); a capture that times out is tried again on the next run."""
    out = json.loads(WAY.read_text()) if WAY.exists() else []
    done = {r["wayback"] for r in out}
    cdx = (
        "https://web.archive.org/cdx/search/cdx?url=georgiavotes.com&from=20221015&to=20221108&output=json"
        "&fl=timestamp&filter=statuscode:200&collapse=timestamp:10"
    )  # one per hour (all of them: 504)
    for k in range(4):
        try:
            stamps = sorted(r[0] for r in json.loads(F.open_url(cdx, timeout=180))[1:])
            break
        except Exception as ex:
            print(f"  capture index try {k + 1} failed: {ex!r}"[:120])
            if k == 3:
                raise
            time.sleep(60)
    by_day = {}
    for ts in stamps:
        by_day.setdefault(ts[:8], []).append(ts)
    todo = [t for v in by_day.values() for t in dict.fromkeys((v[0], v[-1])) if t not in done]
    print(f"  {len(stamps)} captures on {len(by_day)} days; {len(done)} parsed, {len(todo)} to try")
    for ts in todo:
        try:
            html = F.open_url(f"https://web.archive.org/web/{ts}id_/https://www.georgiavotes.com/", timeout=90).decode(
                "utf-8", "ignore"
            )
            rec = parse(html, 2022)
            rec.pop("counties", None)
            rec["wayback"] = ts
            if rec.get("total"):
                rec["days_out"] = days_out(rec)
                rec["ev_day"] = ev_day(rec)
                out.append(rec)
                out.sort(key=lambda r: r["wayback"])
                WAY.write_text(json.dumps(out, indent=1))
                print(
                    f"  2022 capture {ts}: through {rec['through']} ({rec['days_out']} days out), {rec['total']:,} voters"
                )
        except Exception as ex:
            print(f"  2022 capture {ts} failed: {ex!r}"[:120])
        time.sleep(2)  # the archive, politely
    days = sorted({r["through"] for r in out})
    print(f"{WAY.name}: {len(out)} captures, {len(days)} distinct days through: {', '.join(d[5:] for d in days)}")


def share(d, keys=None):
    tot = sum(d.values()) if d else 0
    return {k: v / tot for k, v in d.items() if keys is None or k in keys} if tot else {}


def compare(rec):
    """The measures for one 2026 snapshot (see the module docstring)."""
    res = {
        "through": rec["through"],
        "days_out": days_out(rec),
        "ev_day": ev_day(rec),
        "total": rec["total"],
        "total_2022_same_point": rec["cmp_total"],
        "vs_2022_pct": round(100 * (rec["total"] / rec["cmp_total"] - 1), 1) if rec.get("cmp_total") else None,
        "mail": rec.get("mail"),
        "in_person": rec.get("in_person"),
    }
    C = rec.get("counties") or {}
    if C and BASE.exists():
        b = pd.read_csv(BASE).set_index("county")
        cur = pd.Series({k: v["cur"] for k, v in C.items()})
        cmp_ = pd.Series({k: v["cmp"] for k, v in C.items()})
        s26, s22 = cur / cur.sum(), cmp_ / cmp_.sum()
        res["county_mix_shift"] = {
            f: round(float(((s26 - s22) * b[f].reindex(s26.index)).sum()), 2) for f in ("gov22_margin", "pres24_margin")
        }
        met = cur.index.isin(METRO)
        res["metro_atlanta_share"] = {"2026": round(float(s26[met].sum()), 4), "2022": round(float(s22[met].sum()), 4)}
        res["metro_vs_2022_pct"] = round(100 * (cur[met].sum() / cmp_[met].sum() - 1), 1) if cmp_[met].sum() else None
        res["rest_vs_2022_pct"] = round(100 * (cur[~met].sum() / cmp_[~met].sum() - 1), 1) if cmp_[~met].sum() else None
    # composition against 2022 at the same days out (Wayback baseline, nearest day within 1)
    base = json.loads(WAY.read_text()) if WAY.exists() else []
    base = [r for r in base if r.get("ev_day")]
    near = min(base, key=lambda r: abs(r["ev_day"] - res["ev_day"]), default=None) if base and res["ev_day"] else None
    if near and near["ev_day"] == res["ev_day"]:
        comp = {}
        for grp in ("race", "age", "gender"):
            a, z = share(rec.get(grp, {})), share(near.get(grp, {}))
            comp[grp] = {k: {"2026": round(a[k], 4), "2022": round(z.get(k, float("nan")), 4)} for k in a}
        pa, pz = rec.get("previous_general", {}), near.get("previous_general", {})
        if pa and pz:
            comp["skipped_previous_midterm"] = {
                "2026": round(pa["none"] / rec["total"], 4),
                "2022": round(pz["none"] / near["total"], 4),
            }
        res["composition_vs_2022"] = comp
        res["baseline_2022_through"] = near["through"]
        res["baseline_2022_ev_day"] = near["ev_day"]
        # turnout on the same day of in-person voting (the like-for-like comparison; the site's "same point" is days out)
        res["total_2022_same_ev_day"] = near["total"]
        res["vs_2022_same_ev_day_pct"] = round(100 * (rec["total"] / near["total"] - 1), 1)
    else:
        res["composition_vs_2022"] = None
    return res


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "wayback":
        return wayback()
    html = F.open_url(SITE, timeout=120).decode("utf-8", "ignore")
    rec = parse(html, 2026)
    if not rec.get("total") or not rec.get("through"):
        print("!! georgiavotes.com: page did not parse (layout change?) - nothing written")
        return 1
    SNAP.mkdir(parents=True, exist_ok=True)
    (SNAP / f"{rec['through']}.json").write_text(json.dumps(rec, indent=1))
    hist = []
    for f in sorted(SNAP.glob("*.json")):
        try:
            hist.append(compare(json.loads(f.read_text())))
        except Exception as ex:
            print(f"  {f.name}: {ex!r}"[:120])
    out = {
        "generated": datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "source": "georgiavotes.com (from GA SOS absentee files)",
        "diagnostic_only": True,
        "feeds_forecast": False,
        "why_not": "no past cycle has both a same-day baseline and an outcome to score (see midterms/ga_early.py)",
        "latest": hist[-1] if hist else None,
        "history": hist,
    }
    (OUT / "ga_early.json").write_text(json.dumps(out, indent=1))
    L = out["latest"]
    print(
        f"Georgia through {L['through']} ({L['days_out']} days out, in-person day {L['ev_day']}): {L['total']:,} voted, "
        f"{L['vs_2022_pct']:+.1f}% vs 2022 at the same days out, {L.get('vs_2022_same_ev_day_pct')}% on the same voting day; "
        f"county-mix shift {L.get('county_mix_shift')}; metro share {L.get('metro_atlanta_share')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
