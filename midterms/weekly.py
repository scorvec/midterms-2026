"""Daily refresh + weekly change report (run by .github/workflows/daily.yml).

    python -m midterms.weekly --daily    # the daily run: refresh, snapshot, history, outputs; the change report on Mondays
    python -m midterms.weekly            # the same, always writing reports/weekly_<date>.md against the previous snapshot
    python -m midterms.weekly --report   # only re-write the report from the two newest snapshots

Steps: VoteHub national polls (generic ballot, approval) -> generic-ballot refit (VoteHub + Polling USA + pollresults.org)
-> UF early-vote counts -> EIA heating-oil price -> Wikipedia seats / ratings / House + Senate + governor race polls, VoteHub
race polls, Bluesky feeds, pollresults.org race pages -> build_web -> web/data/history/model_<date>.json -> reconstructed
history (backfill, rebuilt only when the methodology changed) -> web/data/race_history.json -> web/data/summary.json.
The report compares that snapshot with an earlier one at each one's own national environment, and splits every race's move
into the national part (environment change x the chamber's slope) and the race part (new polls, poll average, prior).
Exit code 3 = publish gate held (see publish_gate); the workflow then does not commit the outputs.
"""
import json, sys, glob, subprocess, time, datetime as dt
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; HIST = ROOT / "web" / "data" / "history"; REP = ROOT / "reports"


def _download_votehub():
    """VoteHub's open API returns each poll type whole (no date filter, no ETag): five requests, ~2.5 MB a day."""
    from . import fetch as F
    (ROOT / "data" / "raw" / "votehub").mkdir(parents=True, exist_ok=True)
    # national polls + race-poll catalogues (the race files also date each pollster's experience, model.pollster_experience)
    for k in ("generic-ballot", "approval", "us-senator", "governor", "us-representative"):
        f = ROOT / "data" / "raw" / "votehub" / f"{k}.json"
        try:
            b = F.open_url(f"https://api.votehub.com/polls?poll_type={k}", timeout=180); json.loads(b); f.write_bytes(b)
        except Exception as e: print(f"  votehub {k}: kept the previous file ({str(e)[:60]})")
        time.sleep(1)


def refresh():
    _download_votehub()
    from . import generic; generic.main()
    try:                                        # UF early-vote counts for the movement split (early_vote.py) - before build_web
        from . import early_vote as _ev; print("early-vote snapshot:", _ev.fetch().name)
    except Exception as e: print("early-vote snapshot failed (yesterday's is used):", str(e)[:120])
    try:                                        # EIA weekly residential heating-oil price (heating_oil.py) - before build_web
        from . import heating_oil as _ho; _s = _ho.fetch(); print("heating oil: EIA", _s.date.max().date(), _s.price.iloc[-1])
    except Exception as e: print("heating-oil fetch failed (the cached series is used):", str(e)[:120])
    from . import refresh as _r                 # Wikipedia inputs, race polls, VoteHub races, build_web (module runs on import)


def snapshot():
    src = ROOT / "web" / "data" / "model.json"; d = json.loads(src.read_text())
    out = HIST / f"model_{d['asof']}.json"; out.write_text(src.read_text()); return out


def _p(r, i): return r["p"][i]


def report(new=None, old=None):
    snaps = sorted(HIST.glob("model_*.json"))
    if len(snaps) < 2 and old is None: print("need two snapshots for a report"); return None
    new = Path(new or snaps[-1]); old = Path(old or snaps[-2])
    a, b = json.loads(old.read_text()), json.loads(new.read_text())
    ia = a["house"]["grid"].index(a["house"]["E0"]); ib = b["house"]["grid"].index(b["house"]["E0"])
    Ea, Eb = a["house"].get("E_now", a["house"]["E0"]), b["house"].get("E_now", b["house"]["E0"]); dE = Eb - Ea
    L = [f"# Weekly model changes: {a['asof']} -> {b['asof']}", ""]
    ga, gb = (a.get("polls") or {}), (b.get("polls") or {})
    L += [f"**National environment:** D{Ea:+.2f} -> D{Eb:+.2f} ({dE:+.2f}); generic trend {ga.get('generic_now')} -> {gb.get('generic_now')}, "
          f"approval {ga.get('approval_now')} -> {gb.get('approval_now')}; newest generic poll {gb.get('last_generic')}.", ""]
    def hnow(d): return d["house"].get("now") or {k: v for k, v in d["house"]["dist"][str(d["house"]["E0"])].items() if k != "hist"}
    def snow(d): return d["senate"].get("now") or {k: v for k, v in d["senate"]["dist"][str(d["house"]["E0"])].items() if k != "hist"}
    hn, hon = hnow(b), hnow(a); sn, son = snow(b), snow(a)
    L += ["| | Last week | Now |", "|---|---|---|",
          f"| House Dem seats | {hon['mean']} | {hn['mean']} |", f"| P(House majority) | {hon['p_maj']:.2f} | {hn['p_maj']:.2f} |",
          f"| P(D >= 51 Senate) | {son['p51plus']:.2f} | {sn['p51plus']:.2f} |", f"| P(R loses Senate) | {son['p_r_lose']:.2f} | {sn['p_r_lose']:.2f} |",
          f"| P(D controls Senate, Kalshi rule) | {son.get('p_ctrl', float('nan')):.2f} | {sn.get('p_ctrl', float('nan')):.2f} |"]
    if "governor" in a and "governor" in b:
        L += [f"| Dem governors | {a['governor']['now']['mean']} | {b['governor']['now']['mean']} |"]
    ja, jb = a["senate"].get("joint", {}).get(str(a["house"]["E0"])), b["senate"].get("joint", {}).get(str(b["house"]["E0"]))
    if ja and jb: L += [f"| Democratic sweep | {ja['dd']:.2f} | {jb['dd']:.2f} |"]
    L += [""]

    def movers(sec, key, slope, thresh, label):
        A = {r[key] + ("-sp" if r.get("special") else ""): r for r in a[sec]["races" if sec != "house" else "seats"]}
        B = {r[key] + ("-sp" if r.get("special") else ""): r for r in b[sec]["races" if sec != "house" else "seats"]}
        rows = []
        for k, r in B.items():
            if k not in A: continue
            p0, p1 = _p(A[k], ia), _p(r, ib)
            if abs(p1 - p0) < thresh: continue
            m0, m1 = A[k].get("mu"), r.get("mu")
            nat = dE * slope; race = (m1 - m0 - nat) if (m0 is not None and m1 is not None) else None
            why = []
            if (r.get("n_polls") or 0) != (A[k].get("n_polls") or 0): why.append(f"polls {A[k].get('n_polls', 0)}->{r.get('n_polls', 0)}")
            if r.get("poll_margin") is not None and A[k].get("poll_margin") is not None and abs(r["poll_margin"] - A[k]["poll_margin"]) >= 0.5:
                why.append(f"poll avg {A[k]['poll_margin']:+.1f}->{r['poll_margin']:+.1f}")
            if abs(nat) >= 0.3: why.append(f"national {nat:+.1f}")
            if not why: why.append("prior / model inputs")
            rows.append((abs(p1 - p0), f"| {k} | {r.get('dem') or r.get('member') or ''} | {100*p0:.0f}% -> **{100*p1:.0f}%** | "
                         f"{'' if m1 is None else f'{m0:+.1f} -> {m1:+.1f}'} | {'' if race is None else f'{race:+.1f}'} | {'; '.join(why)} |"))
        if rows:
            L.extend([f"### {label}", "", "| Race | Candidate / member | P(D) | Margin | Race-specific move | Why |", "|---|---|---|---|---|---|"])
            L.extend(x for _, x in sorted(rows, reverse=True)); L.append("")
    movers("senate", "state", b["senate"].get("nat_slope", 0.8), 0.03, "Senate races that moved 3+ points")
    if "governor" in b and "governor" in a: movers("governor", "state", b["governor"].get("slope", 0.55), 0.05, "Governor races that moved 5+ points")
    movers("house", "seat", 1.0, 0.10, "House seats that moved 10+ points")
    L += ["_National polls: VoteHub, Polling USA and pollresults.org; VoteHub's generic-ballot feed lags a few weeks._"]
    REP.mkdir(exist_ok=True); out = REP / f"weekly_{b['asof']}.md"; out.write_text("\n".join(L)); print(out); return out


def publish_gate(new: Path | None):
    """Problems that should stop today's publish (2026-10-03). A race page that silently fails to parse drops its race to the
    fundamentals prior with nothing to show for it (the FL special ran on its prior for days, 2026-09-19); compare today's
    snapshot with the previous day's and hold the publish when a polled race lost all its polls, the race-poll count fell more
    than 10 %, or the generic-ballot poll count fell. MIDTERMS_FORCE_PUBLISH=1 overrides."""
    if new is None: return []
    snaps = sorted(p for p in HIST.glob("model_*.json") if p != new)
    if not snaps: return []
    A, B = json.loads(snaps[-1].read_text()), json.loads(Path(new).read_text())
    def counts(d):
        c = {("house", r["seat"]): r.get("n_polls") or 0 for r in d["house"]["seats"]}
        c.update({("senate", r["state"] + ("|sp" if r.get("special") else "")): r.get("n_polls") or 0 for r in d["senate"]["races"]})
        c.update({("governor", r["state"]): r.get("n_polls") or 0 for r in (d.get("governor") or {}).get("races", [])})
        return c
    ca, cb = counts(A), counts(B); out = []
    lost = [f"{o} {k}" for (o, k), v in ca.items() if v >= 3 and cb.get((o, k), 0) == 0]
    if lost: out.append(f"{len(lost)} polled race(s) now have no polls: {', '.join(lost[:8])}")
    ta, tb = sum(ca.values()), sum(cb.values())
    if ta and tb < 0.9 * ta: out.append(f"race polls fell {ta} -> {tb}")
    ga, gb = (A.get("polls") or {}).get("n_generic"), (B.get("polls") or {}).get("n_generic")
    # RealClearPolling rows (a source dropped on 2026-10-04) are not counted against today's total
    rcp = (((A.get("polls") or {}).get("sources") or {}).get("generic") or {}).get("rcp", 0)
    if ga: ga -= rcp
    if ga and gb is not None and gb < ga: out.append(f"generic polls fell {ga} -> {gb}")
    return out


def week_ago_snapshot(new: Path) -> Path | None:
    """The newest snapshot at least 7 days older than `new` (daily snapshots since 2026-09-26), else the oldest."""
    d_new = dt.date.fromisoformat(new.stem.split("_")[-1])
    snaps = sorted(HIST.glob("model_*.json"))
    old = [p for p in snaps if dt.date.fromisoformat(p.stem.split("_")[-1]) <= d_new - dt.timedelta(days=7)]
    return old[-1] if old else (snaps[0] if snaps and snaps[0] != new else None)


def summary():
    """web/data/summary.json: the four headline numbers for the scorvec.com homepage panel (a few hundred bytes, so the
    homepage never fetches the full model file). Same quantities and wording as the forecast page's outcome boxes."""
    d = json.loads((ROOT / "web" / "data" / "model.json").read_text())
    h, s, g = d["house"]["now"], d["senate"]["now"], d["governor"]["now"]
    joint = d["senate"].get("joint") or {}
    jt = joint[min(joint, key=lambda k: abs(float(k) - d["senate"]["E_now"]))] if joint else None
    out = {"asof": d["asof"],
           "house": {"p": h["p_maj"], "mean": h["mean"], "p10": h["p10"], "p90": h["p90"]},
           "senate": {"p": s.get("p_ctrl", s["p51plus"]), "mean": s["mean"]},
           "both": jt["dd"] if jt else None,
           "governors": {"mean": g["mean"], "p10": g["p10"], "p90": g["p90"]}}
    (ROOT / "web" / "data" / "summary.json").write_text(json.dumps(out, separators=(",", ":")))
    return out


def main(argv):
    """The daily run. Polls are released mostly Wed-Fri, 10:00-17:00 ET (VoteHub + Bluesky timestamps, Aug-Sep 2026; 94 % out by
    21:00 ET), so the workflow runs at 22:30 ET. Diagnostics (FL/PA early vote, registration) never stop the run."""
    import os, time
    os.chdir(ROOT)                                # several modules use repository-relative paths
    for d in ("data/raw/wiki", "data/raw/votehub", "data/raw/early_vote", "data/raw/fl_stats", "data/raw/pollresults_site",
              "data/raw/registration", "data/cache", "web/data/history", "reports"): (ROOT / d).mkdir(parents=True, exist_ok=True)
    daily = "--daily" in argv
    monday = dt.date.today().weekday() == 0
    snap = None
    if "--report" not in argv:
        from . import wiki_polls as _W
        _W.FRESH_SINCE = time.time()              # every race page re-fetched once per run (the 24 h cache would skip a daily run)
        refresh(); snap = snapshot()
        # reconstructed history (backfill.py): rebuilt only when the methodology changed (~6 min); a subprocess, because the
        # backfill wraps model functions for its as-of runs
        r = subprocess.run([sys.executable, "-m", "midterms.backfill", "--ensure"], cwd=str(ROOT), check=False, timeout=3600)
        if r.returncode: print("backfill failed, exit", r.returncode)
        try:
            from . import ledger as _lg; _lg.update()   # first-seen dates of race polls (release dates for the backfill)
        except Exception as e: print("poll ledger failed:", str(e)[:120])
        try:                                      # per-race chance over time for the race panels (~2 s)
            from . import race_history as _rh; _rh.build()
        except Exception as e: print("race history failed:", str(e)[:120])
        summary()
        try:                                      # early / mail vote snapshots, DIAGNOSTIC ONLY (nothing feeds the forecast). Daily
            # because Florida publishes only the current day's statistics files - a missed day is gone
            from . import fl_early as _fl; print("FL early-vote snapshot:", [p.name for p in _fl.snapshot()])
        except Exception as e: print("FL early-vote snapshot failed:", str(e)[:120])
        try:
            from . import pa_early as _pa; _pa.pace(); print("PA mail-ballot pace written")
        except Exception as e: print("PA mail-ballot pace failed:", str(e)[:120])
        if not daily or monday:                   # voter registration, NC + PA (registration.py), diagnostic only, weekly
            r = subprocess.run([sys.executable, "-m", "midterms.registration"], cwd=str(ROOT), check=False, timeout=3600)
            if r.returncode: print("registration failed, exit", r.returncode)
    rp = None
    if not daily or monday:
        if daily and snap is not None:
            old = week_ago_snapshot(snap)
            rp = report(new=snap, old=old) if old else None
        else:
            rp = report()
    from . import fetch as F; F.report()
    held = publish_gate(snap) if snap is not None else []
    if held and os.environ.get("MIDTERMS_FORCE_PUBLISH") != "1":
        print("!! publish HELD (MIDTERMS_FORCE_PUBLISH=1 to override):", "; ".join(held)); return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
