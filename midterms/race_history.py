"""Per-race forecast history for the board's race panels and the national sparklines (2026-09-30, user: "trends in the
chance of each candidate winning over time when I click on the map"; then "The history lines for the national probabilities
are misleading since we've changed the model methodology many times. Let's backfill the model with what it would have
suggested before.")

    python -m midterms.race_history          # -> web/data/race_history.json

PRIMARY series = the RECONSTRUCTION (midterms/backfill.py: the current model run as of each past date with only the polls
released by then; data/cache/backfill/<date>.json), weekly Mondays from 2026-01-05 and daily from 2026-06-01 through the day
the backfill was built, then one AS-ISSUED point per later day (the daily run's own snapshot, made with the methodology
current that day). When the methodology changes, backfill.ensure_current() (daily run) rebuilds every date, so the
appended days are reconstructed again under the new code.

SECONDARY block "issued" = what the board actually showed each day since 2026-09-16 (under whatever methodology it had
then): the daily snapshots web/data/history/model_<date>.json (09-16 to 09-21 are the last published version of each of
those days; 09-17 and 09-21 have none). Read where the page read it that day: the grid index nearest
E0 (a fixed half point until 2026-09-28, = E_now since).

Layout (compact; p in per-mille integers):
  {"start", "method": {...}, "dates": [...], "src": ["r" | "i", ...], "nat": {"E": [...], "h": [...], "s": [...]},
   "house": {"AL-1": [[p...], [mu...]]}, "senate": {"FL|sp": [[p...], [mu...](, [cp...])]}, "gov": {"AL": ...},
   "issued": {"start", "dates", "nat", "house", "senate", "gov"}}
`h` = House P(D majority), `s` = Senate P(D controls) (issued: P(51+) before 2026-09-26, when control was first counted),
`E` = E_now. src 'r' = reconstructed, 'i' = as issued (appended by the daily run). Senate/governor races carry "cp" (the
challenger party per day) when it ever differs from 'D'.
"""
import json, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "web" / "data" / "model.json"; HIST = ROOT / "web" / "data" / "history"
OUT = ROOT / "web" / "data" / "race_history.json"
BACKFILL = ROOT / "data" / "cache" / "backfill"
ISSUED_START = "2026-09-16"
NOTE = "reconstructed with the current model, using only polls released by each date"


def versions():
    """{date: model dict}: the daily snapshots web/data/history/model_<date>.json, plus today's model.json."""
    out = {}
    snaps = {p.stem.split("_")[-1]: p for p in sorted(HIST.glob("model_*.json"))}
    for day, p in snaps.items():
        if day >= ISSUED_START: out[day] = json.loads(p.read_text())
    if MODEL.exists():
        m = json.loads(MODEL.read_text())
        if m.get("asof") and (not snaps or m["asof"] >= max(snaps)): out[m["asof"]] = m
    return dict(sorted(out.items()))


def _gi(grid, e0):
    return min(range(len(grid)), key=lambda i: abs(grid[i] - e0))


def _key(grid, gi, H):
    keys = H.get("keys")
    return keys[gi] if keys else f"{grid[gi]:.1f}"


pm = lambda p: None if p is None else int(round(1000 * float(p)))
r1 = lambda v: None if v is None else round(float(v), 1)


def _issued_point(d):
    """One model.json version -> (E, h, s, races) with race values (p, mu, challenger party)."""
    H = d["house"]; grid = H["grid"]; e0 = H["E0"]; gi = _gi(grid, e0); k = _key(grid, gi, H)
    hd = (H.get("dist") or {}).get(k); sd = (d.get("senate", {}).get("dist") or {}).get(k)
    races = {"house": {s["seat"]: (s["p"][gi], s.get("mu"), "D") for s in H["seats"]}, "senate": {}, "gov": {}}
    for kind, blk in (("senate", d.get("senate")), ("gov", d.get("governor"))):
        if not blk: continue
        g2 = blk.get("grid", grid); gj = gi if g2 == grid else _gi(g2, e0)
        for r in blk["races"]:
            races[kind][r["state"] + ("|sp" if r.get("special") else "")] = (r["p"][gj], r.get("mu"), r.get("dem_party") or "D")
    return (H.get("E_now", e0), hd and hd.get("p_maj"), sd and sd.get("p_ctrl", sd.get("p51plus")), races)


def _recon_point(r):
    races = {"house": {k: (v[0], v[1], "D") for k, v in r["house"].items()},
             "senate": {k: (v[0], v[1], v[3] if len(v) > 3 else "D") for k, v in r["senate"].items()},
             "gov": {k: (v[0], v[1], "D") for k, v in r["gov"].items()}}
    return (r["E"], r["nat"]["h"], r["nat"]["s"], races)


def _assemble(points, start):
    """{date: (E, h, s, races)} -> the compact block."""
    dates = sorted(points); n = len(dates)
    nat = {"E": [None] * n, "h": [None] * n, "s": [None] * n}; sec = {"house": {}, "senate": {}, "gov": {}}; party = {"senate": {}, "gov": {}}
    for i, day in enumerate(dates):
        E, h, s, races = points[day]
        nat["E"][i], nat["h"][i], nat["s"][i] = r1(E), pm(h), pm(s)
        for kind, rs in races.items():
            for key, (p, mu, cp) in rs.items():
                v = sec[kind].setdefault(key, [[None] * n, [None] * n]); v[0][i] = pm(p); v[1][i] = r1(mu)
                if kind in party: party[kind].setdefault(key, [None] * n)[i] = cp
    out = {"start": start, "dates": dates, "nat": nat, "house": sec["house"], "senate": {}, "gov": {}}
    for kind in ("senate", "gov"):
        for key, v in sec[kind].items():
            cp = party[kind].get(key)
            out[kind][key] = v + ([cp] if cp and any(x not in (None, "D") for x in cp) else [])
    return out


def reconstructed():
    """({date: point}, meta) from data/cache/backfill (backfill.py), or ({}, {}) if it has not been built."""
    mf = BACKFILL / "meta.json"
    if not mf.exists(): return {}, {}
    meta = json.loads(mf.read_text()); pts = {}
    for d in meta.get("dates", []):
        f = BACKFILL / f"{d}.json"
        if f.exists(): pts[d] = _recon_point(json.loads(f.read_text()))
    return pts, meta


def build(write=True):
    V = versions()
    issued = {day: _issued_point(d) for day, d in V.items()}
    R, meta = reconstructed()
    last_r = max(R) if R else ""
    main = dict(R); src = {d: "r" for d in R}
    for day, pt in issued.items():                     # the daily run's own points after the backfill's last day
        if day > last_r: main[day] = pt; src[day] = "i"
    try:
        from . import backfill as BF
        stale = bool(meta) and meta.get("code_sha1") != BF.method_stamp()["code_sha1"]
    except Exception: stale = None
    out = _assemble(main, min(main) if main else ISSUED_START)
    out["src"] = [src[d] for d in out["dates"]]
    appended = [d for d in out["dates"] if src[d] == "i"]
    out["method"] = {"note": NOTE if R else "as issued each day", "reconstructed_from": min(R) if R else None, "reconstructed_to": last_r or None,
                     "weekly_until": "2026-06-01", "appended_as_issued_from": appended[0] if appended else None,
                     "code_sha1": meta.get("code_sha1"), "git_head": meta.get("git_head"), "built": meta.get("built"),
                     "release_lag_days": meta.get("lag_days"), "stale": stale}
    out["issued"] = _assemble(issued, ISSUED_START)
    if write:
        OUT.write_text(json.dumps(out, separators=(",", ":")))
        print(f"race_history: {len(out['dates'])} days {out['dates'][0]}..{out['dates'][-1]} ({len(R)} reconstructed, {len(appended)} as issued), "
              f"issued block {len(issued)} days; {OUT.stat().st_size / 1024:.0f} KB{' - BACKFILL STALE: run python -m midterms.backfill --rebuild' if stale else ''}")
    return out


if __name__ == "__main__":
    build()
