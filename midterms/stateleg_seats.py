"""Per-seat export for the state-legislature district maps (web/legislatures.html) -> web/data/stateleg_seats.json.

Written by the daily run right after stateleg.run_live (build_web.py). Compact, rounded; one list per chamber in model order:
  d    district key (joins to the TopoJSON ids in web/data/stateleg_geo/{ST}.topo.json)
  k    seats in the district (multi-member: AZ House, NH House)
  p    P(a Democrat wins) - for k > 1 the expected Democratic share of the k seats
  ed   expected Democratic seats (k > 1 only)
  m    expected Democratic two-party margin, points
  l    district lean: 2024 presidential margin minus the national margin, points
  fx   "D" / "R" when only that party has nominees (the seat is fixed), else absent
  inc  sitting members, "Name (P); ..." ; run = number of them on the 2026 ballot (absent when none)
  dem / rep   2026 nominees when known
Seats not up in 2026 (odd-year-only senate seats in PA and WI): held = {"d", "party", "member"}.
"""
from __future__ import annotations

import json
import numpy as np
from .paths import ROOT

OUT = ROOT / "web" / "data" / "stateleg_seats.json"


def export(sl, S, asof=None):
    from . import stateleg as SLG
    try: _, H = SLG.seats_2026()
    except Exception as ex: print("  stateleg seats: held-over seats unavailable:", str(ex)[:120]); H = None
    out = {"asof": asof, "chambers": []}
    for c in sl["chambers"]:
        st, ch = c["state"], c["chamber"]
        q = S[(S["state"] == st) & (S["chamber"] == ch)]
        seats = []
        for r in q.itertuples():
            k = int(r.k); rec = {"d": r.district, "k": k, "p": round(float(r.p_d), 3), "m": round(float(r.mu), 1), "l": round(float(r.lean), 1)}
            if k > 1: rec["ed"] = round(float(r.p_d) * k, 2)
            if r.n_d == 0 or r.n_r == 0: rec["fx"] = "R" if r.n_d == 0 else "D"
            if r.members: rec["inc"] = r.members
            run = int(r.inc_d) + int(r.inc_r)
            if run: rec["run"] = run
            for side in ("dem", "rep"):
                v = getattr(r, side)
                if isinstance(v, str) and v.strip(): rec[side] = v.strip()
            seats.append(rec)
        held = []
        if H is not None and len(H):
            for r in H[(H["state"] == st) & (H["chamber"] == ch)].itertuples():
                held.append({"d": r.district, "party": r.party if isinstance(r.party, str) else None, "member": r.member if isinstance(r.member, str) else None})
        out["chambers"].append({"state": st, "chamber": ch, "name": c["name"], "seats": seats, **({"held": held} if held else {})})
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)))
    print(f"  stateleg seats: {sum(len(c['seats']) for c in out['chambers'])} districts -> {OUT.name} ({OUT.stat().st_size / 1e3:.0f} kB)")
