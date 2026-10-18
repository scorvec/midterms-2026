"""web/data/early_vote.json: everything web/early-vote.html shows, from the early-vote trackers.

  nc  nc_tracker.payload(): North Carolina against 2018-2024 at the same days out (party, age, race, county, vote
      history) and the forecast gate (data/static/early_gate.json);
  ga  ga_early's latest reading (data/early/ga_early.json; compiled by georgiavotes.com from the Secretary of State's
      absentee files): turnout against 2022, metro Atlanta, the county mix, race / age / gender, new voters;
  fl  fl_early's latest reading (data/early/fl_early.json; Division of Elections statistics files): mail ballots returned by
      party against registration, against the ballots sent and against 2024 at the same days out; once in-person early
      voting opens, its ballots by party the same way (and mail and early together on the same compile);
  nv  nv_clark_early's latest reading (data/early/nv_clark_early.json; Clark County Election Department files): mail
      ballots returned by party against registration and against 2022 / 2024 at the same days out, in-person early voters
      by party (joined to the voter list) and against 2024's turnout on the same day; both by congressional district.

How banked ballots enter the forecast (early_vote.py's per-state factors) is read by the page from model.json itself.
A state whose input is missing is left out; nothing here is needed by the forecast. It reads committed files only (no
download), so it runs both in the cloud session after nc_tracker and in early-diag.yml after Georgia, Florida and Nevada.

    python -m midterms.early_page
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EARLY = ROOT / "data" / "early"
OUT = ROOT / "web" / "data" / "early_vote.json"


def _read(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def georgia() -> dict | None:
    g = _read(EARLY / "ga_early.json")
    L = (g or {}).get("latest")
    if not L:
        return None
    keep = ("through", "days_out", "ev_day", "total", "mail", "in_person", "total_2022_same_point", "vs_2022_pct",
            "total_2022_same_ev_day", "vs_2022_same_ev_day_pct", "county_mix_shift", "metro_atlanta_share",
            "metro_vs_2022_pct", "rest_vs_2022_pct", "composition_vs_2022")  # fmt: skip
    return {k: L[k] for k in keep if k in L} | {"source": g.get("source")}


def florida() -> dict | None:
    f = _read(EARLY / "fl_early.json")
    v = ((f or {}).get("latest") or {}).get("VbmVoted")
    if not v:
        return None
    out = {
        "compiled": v["compiled"],
        "days_out": v["days_out"],
        "returned": {k: v[k] for k in ("DEM", "REP", "NPA", "OTH", "total")},
        "R_minus_D_pct": v["R_minus_D_pct"],
        "registration": v.get("registration"),
        "vs_registration_pts": v.get("vs_registration_pts"),
        "requests": v.get("requests"),
        "vs_requests_pts": v.get("vs_requests_pts"),
        "finals": f.get("finals"),
    }
    if b := _last_matched(f, "VbmVoted"):
        out["vs_2024"] = b
    e = f["latest"].get("EarlyVoted")
    if e:
        out["early"] = {
            "compiled": e["compiled"],
            "days_out": e["days_out"],
            "voted": {k: e[k] for k in ("DEM", "REP", "NPA", "OTH", "total")},
            "R_minus_D_pct": e["R_minus_D_pct"],
            "vs_registration_pts": e.get("vs_registration_pts"),
        }
        if e["days_out"] == v["days_out"]:  # the same compile day: mail and early together
            both = {k: e[k] + v[k] for k in ("REP", "DEM", "total")}
            out["early"]["with_mail"] = {
                "total": both["total"],
                "R_minus_D_pct": round(100 * (both["REP"] - both["DEM"]) / both["total"], 2),
            }
        if b := _last_matched(f, "EarlyVoted"):
            out["early"]["vs_2024"] = b
    return out


def nevada() -> dict | None:
    v = _read(EARLY / "nv_clark_early.json")
    if not v:
        return None
    keep = ("through", "days_out", "registration", "mail", "in_person", "combined", "districts", "source")
    return {k: v[k] for k in keep if k in v}


def _last_matched(f: dict, kind: str) -> dict | None:
    """The matched-days-out comparison with 2024 closest to Election Day for one kind of ballot."""
    m = [x for x in f.get("matched_2024") or [] if x.get("kind") == kind]
    if not m:
        return None
    b = min(m, key=lambda x: x["days_out"])
    return {
        "days_out": b["days_out"],
        "R_minus_D_2024": b["2024"]["R_minus_D_pct"],
        "R_minus_D_2026": b["2026"]["R_minus_D_pct"],
        "vs_registration_change_pts": b.get("vs_registration_change_pts"),
        "returned_change_pct": b.get("returned_change_pct"),
    }


def build() -> dict:
    from . import nc_tracker as NT

    try:
        nc = NT.payload()
    except FileNotFoundError as e:  # no 2026 run yet
        print(f"  early page: North Carolina left out ({e})")
        nc = None
    res = {
        "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"),
        "nc": nc,
        "ga": georgia(),
        "fl": florida(),
        "nv": nevada(),
    }
    OUT.write_text(json.dumps(res, separators=(",", ":")))
    print(f"early page: {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e3:.0f} kB; " + ", ".join(
        k for k in ("nc", "ga", "fl", "nv") if res[k]) + ")", flush=True)  # fmt: skip
    return res


if __name__ == "__main__":
    build()
