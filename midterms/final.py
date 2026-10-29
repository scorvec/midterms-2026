"""The forecast the results are scored against: every race's predictive distribution, frozen once before the polls close.

    python -m midterms.final freeze [--force]   # data/cache/forecast_detail.json -> data/final/forecast_2026.json

Each daily run (build_web) writes data/cache/forecast_detail.json from its simulations at today's national environment:
per race the chance that the Republican's opponent wins (p), the simulated margin's mean and percentiles 1-99 (opponent minus
Republican, points of the total vote; for a ranked-choice race the final round in points of first-round ballots, rcv.py), the
benchmark the page shows (House race raters, Cook / Sabato for the Senate), and for a race that needs a majority the chance
that nobody gets one (majority.py). Chambers: the simulated seat counts and control; the national House vote distribution.

`freeze` copies the newest detail and the published model.json into data/final/ with the code commit, the time and a SHA-256 of
the forecast, and refuses to overwrite a frozen forecast (--force replaces it, for a freeze made too early) or to freeze an
incomplete one (an office whose block failed in build_web; --force freezes it anyway). It then marks the
published model.json final (time + SHA-256), so the forecast page says so; a later run (after_freeze) writes it unmarked. The
scorecard (scorecard.py) reads only the frozen file."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "data" / "cache" / "forecast_detail.json"
FINAL_DIR = ROOT / "data" / "final"
FINAL = FINAL_DIR / "forecast_2026.json"
FINAL_MODEL = FINAL_DIR / "model_2026.json"
PUBLISHED = ROOT / "web" / "data" / "model.json"
PCTS = list(range(1, 100))


def _race(office, seat, name, rep, party, margins, extra=None):
    q = np.percentile(margins, PCTS)
    return {
        "office": office,
        "seat": seat,
        "challenger": name,
        "challenger_party": party,
        "republican": rep,
        "p": round(float((margins > 0).mean()), 4),
        "mean": round(float(margins.mean()), 2),
        "q": [round(float(x), 2) for x in q],
        **(extra or {}),
    }


def detail(out, s, mg, SS, smg, GS=None, gmg=None):
    """The scorable forecast from one build_web run (its simulations at E_now). out: the model.json dict."""
    from . import rcv as RC, senate2026 as SN, wiki_polls as W

    races = []
    for i, r in s.reset_index(drop=True).iterrows():
        if r.get("uncontested"):  # no Democrat or no Republican on the ballot: decided, nothing to score
            continue
        ballot = W.ballot_of(r.get("cands", ""))
        ind = str(r.get("challenger_party") or "D") != "D"
        dem = r["challenger"] if ind else next((n for n, pt in ballot if pt.startswith(("Democratic", "DFL"))), None)
        rep = next((n for n, pt in ballot if pt == "Republican"), None)
        rating = r.get("rating_mean")
        extra = {"rcv": RC.applies("house", r["seat"])}
        if r.get("opponent_party") == "I":  # an independent incumbent against a Democrat (CA-6): he is the other side
            rep = next((n for n, pt in ballot if not pt.startswith(("Democratic", "DFL"))), None)
            extra["opponent_party"] = "I"
        if rating == rating and rating is not None:
            extra["raters"] = round(float(rating), 3)
        races.append(_race("house", r["seat"], dem, rep, "I" if ind else "D", mg[:, i], extra))
    for i, r in SS.reset_index(drop=True).iterrows():
        extra = {"special": bool(r["special"]), "rcv": RC.applies("senate", r["state"])}
        for k in ("rat_Cook", "rat_Sabato"):
            if isinstance(r.get(k), str):
                extra[k[4:].lower()] = r[k]
        races.append(
            _race(
                "senate",
                r["state"],
                r["challenger"] or r["dem_nom"],
                r.get("republican") or r["rep_nom"],
                r["challenger_party"] or "D",
                smg[:, i],
                extra,
            )
        )
    if GS is not None:
        maj = GS.attrs.get("majority", {})
        from . import majority as MJ

        for i, r in GS.reset_index(drop=True).iterrows():
            extra = {"rcv": RC.applies("governor", r["state"])}
            if r["state"] in maj:
                extra["majority"] = {
                    "rule": maj[r["state"]][0],
                    "p_none": round(MJ.p_no_majority(gmg[:, i], maj[r["state"]][1]), 4),
                }
            races.append(_race("governor", r["state"], r["dem"], r["rep"], "D", gmg[:, i], extra))
    H = out["house"]
    k = H["keys"][H["grid"].index(H["E_now"])]
    sen = out["senate"]["dist"][k]
    chambers = {
        "house": {k2: H["dist"][k][k2] for k2 in ("mean", "p_maj", "hist")},
        "senate": {
            "p_ctrl": sen["p_ctrl"],
            "hist2": sen["hist2"],
            "holdover_d": SN.NOW_D - out["senate"]["d_up"],
            "holdover_r": SN.NOW_R - out["senate"]["r_up"],
        },
        "national_vote": out["national_vote"],
    }
    if GS is not None and "governor" in out:
        hold = out["governor"]["now"]["now_d"] - out["governor"]["now"]["up_d"]
        dg = hold + (gmg > 0).sum(1)
        chambers["governor"] = {
            "mean": round(float(dg.mean()), 2),
            "hist": {str(a): int(b) for a, b in zip(*np.unique(dg, return_counts=True))},
            "holdover_d": hold,
        }
    return {
        "asof": out["asof"],
        "generated": out["generated"],
        "E_now": H["E_now"],
        "percentiles": PCTS,
        "margin": "Republican's opponent minus the Republican, points of the total vote; ranked-choice races: the final "
        "round in points of first-round ballots",
        "races": races,
        "chambers": chambers,
        "house_seats_total": len(s),
        "house_majority": 218,
        # seats with no Democrat or no Republican on the ballot (run2026.ballot_structure), not in `races`
        "house_fixed": {
            w: int((s["uncontested"].fillna(False).astype(bool) & (s["winner"] == w)).sum()) for w in ("D", "R")
        },
    }


def write_detail(doc):
    DETAIL.parent.mkdir(parents=True, exist_ok=True)
    DETAIL.write_text(json.dumps(doc, separators=(",", ":")))


def _commit():
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=False, cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return None


def shortfall(doc):
    """What a detail lacks against its own run's totals (an office whose block failed in build_web); [] when complete."""
    n, C, out = Counter(r["office"] for r in doc["races"]), doc.get("chambers", {}), []
    fixed = sum(doc.get("house_fixed", {}).values())
    if n["house"] + fixed != doc.get("house_seats_total"):
        out.append(f"house: {n['house']} races + {fixed} decided of {doc.get('house_seats_total')} seats")
    S = C.get("senate")
    if not S or n["senate"] != 100 - S["holdover_d"] - S["holdover_r"]:
        out.append(f"senate: {n['senate']} races")
    if "governor" not in C or not n["governor"]:
        out.append(f"governor: {n['governor']} races")
    if "national_vote" not in C:
        out.append("no national vote")
    return out


def freeze(force=False):
    if not DETAIL.exists():
        sys.exit("no forecast detail: run the daily build first (python -m midterms.build_web)")
    if FINAL.exists() and not force:
        sys.exit(f"{FINAL.name} exists (frozen {json.loads(FINAL.read_text())['frozen']['at']}); --force replaces it")
    doc = json.loads(DETAIL.read_text())
    if (short := shortfall(doc)) and not force:
        sys.exit("incomplete forecast detail (" + "; ".join(short) + "); --force freezes it anyway")
    body = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    doc["frozen"] = {
        "at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"),
        "commit": _commit(),
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    FINAL.write_text(json.dumps(doc, separators=(",", ":")))
    if PUBLISHED.exists():
        FINAL_MODEL.write_bytes(PUBLISHED.read_bytes())
        pub = json.loads(PUBLISHED.read_text())
        pub["final"] = {k: doc["frozen"][k] for k in ("at", "sha256")}
        PUBLISHED.write_text(json.dumps(pub, separators=(",", ":")))
    print(
        f"frozen: {len(doc['races'])} races, forecast of {doc['asof']} (E {doc['E_now']:+.2f}), sha256 {doc['frozen']['sha256'][:12]}"
    )


def verify():
    """Recompute the frozen forecast's SHA-256 (the scorecard checks it before scoring)."""
    doc = json.loads(FINAL.read_text())
    fz = doc.pop("frozen")
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == fz["sha256"]


if __name__ == "__main__":
    if sys.argv[1:2] == ["freeze"]:
        freeze(force="--force" in sys.argv)
    elif sys.argv[1:2] == ["verify"]:
        print("ok" if verify() else "MISMATCH")
    else:
        sys.exit(__doc__)
