"""House race polls from the VoteHub open API (poll_type=us-representative) as a second source next to the Wikipedia
state pages (2026-09-22). Each answer is given a party by matching the candidate's surname against the seat's 2026
nominee list (house2026_seats.csv 'cands'); a poll is kept only if it names BOTH the Democratic and the Republican
nominee (the same rule as wiki_polls.screen_house_polls). Polls already in the Wikipedia set (same seat, end date within
5 days, D and R shares within 1 point) are dropped as duplicates.
    python -m midterms.votehub_races      -> data/cache/house2026_polls_votehub.csv
"""
import json, re, urllib.request
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
URL = "https://api.votehub.com/polls?poll_type=us-representative"


def _surname(n): return re.sub(r"[^a-z]", "", str(n).split("(")[0].strip().split()[-1].lower()) if str(n).strip() else ""


def fetch(refresh=True):
    f = ROOT / "data" / "raw" / "votehub" / "us-representative.json"
    if refresh:                                    # at most once in 6 h (the daily run downloads it first, weekly._download_votehub)
        from . import fetch as F; F.get(URL, f, min_age_h=6)
    return json.loads(f.read_text())


def build(refresh=True):
    seats = pd.read_csv(ROOT / "data" / "cache" / "house2026_seats.csv").set_index("seat")
    rows, rej = [], []
    for p in fetch(refresh):
        sn = str(p.get("seat_name") or "")
        m = re.match(r"([A-Z]{2})-(\d+)", sn)
        if not m: rej.append((sn, "no seat")); continue
        seat = f"{m.group(1)}-{int(m.group(2))}"
        if seat not in seats.index: rej.append((seat, "unknown seat")); continue
        cands = re.findall(r"▌([^▌\[]+?)\s*\(([^)]+)\)", str(seats.at[seat, "cands"]))
        party = {_surname(n): ("D" if pt.startswith(("Democratic", "DFL")) else "R" if pt == "Republican" else "O") for n, pt in cands}
        d = r_ = None; other = 0.0; tot = 0.0; dn = rn = None
        for a in p.get("answers") or []:
            pct = float(a.get("pct") or 0); tot += pct; pty = party.get(_surname(a.get("choice")))
            if pty == "D" and d is None: d, dn = pct, a["choice"]
            elif pty == "R" and r_ is None: r_, rn = pct, a["choice"]
            else: other += pct
        if d is None or r_ is None: rej.append((seat, "not both nominees")); continue
        if other > min(d, r_): rej.append((seat, "a third candidate outpolls a major nominee")); continue   # AK-1: Begich v independent Hill
        tag = {"DEM": " (D)", "REP": " (R)"}.get(p.get("partisan"), "")
        rows.append({"seat": seat, "pollster": str(p.get("pollster")) + tag, "end_date": p["end_date"], "n": p.get("sample_size"), "dem": d, "rep": r_,
                     "margin": d - r_, "dem_name": dn, "rep_name": rn, "und": max(0.0, 100 - tot), "other": other, "src": "votehub"})
    v = pd.DataFrame(rows); v["end_date"] = pd.to_datetime(v["end_date"])
    w = pd.read_csv(ROOT / "data" / "cache" / "house2026_polls.csv", parse_dates=["end_date"])
    from .model import _same_poll
    def dup(r): return _same_poll(r.pollster, r.end_date, r.margin, w[w.seat == r.seat])
    v["dup"] = v.apply(dup, axis=1)
    new = v[~v.dup].drop(columns="dup")
    new.to_csv(ROOT / "data" / "cache" / "house2026_polls_votehub.csv", index=False)
    print(f"VoteHub House polls: {len(v)} usable ({len(rej)} rejected: {pd.Series([x[1] for x in rej]).value_counts().to_dict()}), "
          f"{int(v.dup.sum())} already in the Wikipedia set, {len(new)} new in {new.seat.nunique()} seats "
          f"({len(set(new.seat) - set(w.seat))} seats with no Wikipedia poll)")
    return new


if __name__ == "__main__":
    n = build(refresh=False); print(n.sort_values("end_date").tail(12).to_string(index=False))
