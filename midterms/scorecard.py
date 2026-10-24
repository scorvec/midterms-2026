"""Score the frozen forecast (final.py) against the results as they come in.

    python -m midterms.scorecard template   # add a row for every forecast race to data/results/results_2026.csv (keeps filled rows)
    python -m midterms.scorecard            # score -> data/results/scorecard_2026.json and a summary on stdout
    python -m midterms.scorecard --latest   # dry run against today's unfrozen forecast (data/cache/forecast_detail.json)

data/results/results_2026.csv, one row per race (office house / senate / governor, seat "AK-1" / "GA"; plus office national,
seat US for the national House vote):
  challenger, republican   the forecast's two sides (filled by `template`; the challenger is the Republican's opponent the
                           model scored - Hill (I) in AK-1, the independents in NE / ID / SD / MT, otherwise the Democrat)
  winner                   D / R / I / O once the race is called (the eventual winner: Georgia's runoff, Vermont's legislature)
  status                   counting / called / final / certified / runoff / legislature / rcv_pending
  chal_votes, rep_votes, total_votes   the count for the two sides and every ballot cast in the race (a candidate on several
                           lines, New York fusion, is summed); national row: Democratic and Republican House votes
  pct_counted              share of the expected vote counted
  rcv_chal, rcv_rep, rcv_first_round   ranked-choice races: the final round for the two sides and the first round's total
  source, updated, note

Rules:
  * a race's outcome is "the Republican loses" (winner not R), the event the forecast's p is about; seats with no Democrat or no
    Republican on the ballot are decided and not scored;
  * the margin is scored once the count is final or certified: (challenger - Republican) / total votes; in a ranked-choice
    race the final round over the first round's ballots (the model's unit, rcv.py), or the first round itself when a candidate
    cleared 50 % in it;
  * Vermont's governor (majority.py): whether anyone cleared 50 %, scored once the count is final; the winner when the
    legislature has chosen; Georgia on the runoff's winner;
  * chambers once every scored race in them has a winner: the seat count's place in the simulated distribution (mid-PIT) and,
    for the Senate, control by the pro tem rule (senate2026.d_controls, independents abstain).
Scores: Brier and log loss of p (House against the race raters' average on the same seats), calibration by probability band,
margin error (actual minus the forecast median; + = the Republican's opponent did better), MAE, PIT, CRPS and the coverage of
the 80 % and 95 % ranges."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import final as FN

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "results" / "results_2026.csv"
OUT = ROOT / "data" / "results" / "scorecard_2026.json"
COLS = [
    "office",
    "seat",
    "challenger",
    "republican",
    "winner",
    "status",
    "chal_votes",
    "rep_votes",
    "total_votes",
    "pct_counted",
    "rcv_chal",
    "rcv_rep",
    "rcv_first_round",
    "source",
    "updated",
    "note",
]
COMPLETE = ("final", "certified")
BANDS = [0.0, 0.05, 0.15, 0.35, 0.5, 0.65, 0.85, 0.95, 1.0]
P_CLIP = 0.001


def load_forecast(latest=False):
    if latest:
        return json.loads(FN.DETAIL.read_text()), False
    if not FN.FINAL.exists():
        sys.exit("no frozen forecast (python -m midterms.final freeze); --latest scores today's unfrozen one")
    if not FN.verify():
        sys.exit(f"{FN.FINAL.name} does not match its SHA-256: the frozen forecast was edited")
    return json.loads(FN.FINAL.read_text()), True


def load_results():
    if not RESULTS.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(RESULTS, dtype={"seat": str, "winner": str, "status": str}, keep_default_na=True)


def template(doc):
    """Rows for every forecast race (and the national vote) not yet in the results file; filled rows are kept as they are."""
    old = load_results()
    have = set(zip(old["office"], old["seat"]))
    new = [
        {"office": r["office"], "seat": r["seat"], "challenger": r["challenger"], "republican": r["republican"]}
        for r in doc["races"]
        if (r["office"], r["seat"]) not in have
    ]
    if ("national", "US") not in have:
        new.append({"office": "national", "seat": "US", "challenger": "Democrats", "republican": "Republicans"})
    out = pd.concat([old, pd.DataFrame(new, columns=COLS)], ignore_index=True)[COLS]
    order = {"national": 0, "senate": 1, "governor": 2, "house": 3}
    out = out.sort_values(["office", "seat"], key=lambda c: c.map(order) if c.name == "office" else c)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(RESULTS, index=False)
    print(f"{RESULTS.relative_to(ROOT)}: {len(out)} rows ({len(new)} added)")


# --- scores ---------------------------------------------------------------------------------------------------------------
def outcome(winner):
    """1 = the Republican lost (the event p is about), 0 = the Republican won, None = not called."""
    w = str(winner).strip().upper() if isinstance(winner, str) else ""
    return None if w not in ("D", "R", "I", "O") else int(w != "R")


def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def log_loss(p, y):
    p = np.clip(np.asarray(p, float), P_CLIP, 1 - P_CLIP)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def actual_margin(row, rcv):
    """(challenger - Republican) in the model's unit, or None while the count is not complete."""
    if str(row.get("status")) not in COMPLETE:
        return None
    c, r, t = (pd.to_numeric(row.get(k), errors="coerce") for k in ("chal_votes", "rep_votes", "total_votes"))
    if not (t > 0) or c != c or r != r:
        return None
    if rcv:
        rc, rr, r1 = (pd.to_numeric(row.get(k), errors="coerce") for k in ("rcv_chal", "rcv_rep", "rcv_first_round"))
        if r1 > 0 and rc == rc and rr == rr:
            return float(100 * (rc - rr) / r1)
        if max(c, r) / t <= 0.5:
            return None  # the rounds are still to be run: the first round is not the final margin
    return float(100 * (c - r) / t)


def pit(q, x, pcts=FN.PCTS):
    """Forecast CDF at x from percentiles (linear between them, the outer 1 % split evenly beyond the extremes)."""
    q = np.asarray(q, float)
    u = np.asarray(pcts, float) / 100
    if x <= q[0]:
        return float(u[0] / 2)
    if x >= q[-1]:
        return float(1 - (1 - u[-1]) / 2)
    return float(np.interp(x, q, u))


def crps(q, x, pcts=FN.PCTS):
    """CRPS from the percentiles: twice the mean pinball loss over the levels."""
    q = np.asarray(q, float)
    u = np.asarray(pcts, float) / 100
    return float(2 * np.mean(np.where(x >= q, u * (x - q), (1 - u) * (q - x))))


def quantile(q, level, pcts=FN.PCTS):
    return float(np.interp(level * 100, pcts, q))


def mid_pit(hist, x):
    """P(X < x) + P(X = x) / 2 for a simulated count {value: count}."""
    v = np.array([int(k) for k in hist])
    c = np.array(list(hist.values()), float)
    c = c / c.sum()
    return float(c[v < x].sum() + c[v == x].sum() / 2)


def calibration(p, y):
    rows = []
    p, y = np.asarray(p, float), np.asarray(y, float)
    for lo, hi in itertools.pairwise(BANDS):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            rows.append(
                {
                    "band": f"{lo:.2f}-{hi:.2f}",
                    "n": int(m.sum()),
                    "forecast": round(float(p[m].mean()), 3),
                    "won": round(float(y[m].mean()), 3),
                }
            )
    return rows


def score(doc, res):
    by = {(r["office"], str(r["seat"])): r for r in res.to_dict("records")}
    rows = []
    for f in doc["races"]:
        r = by.get((f["office"], str(f["seat"])), {})
        y = outcome(r.get("winner"))
        m = actual_margin(r, f.get("rcv", False))
        row = {"office": f["office"], "seat": f["seat"], "p": f["p"], "y": y, "status": r.get("status")}
        if f.get("raters") is not None:
            row["raters"] = f["raters"]
        if m is not None:
            med = quantile(f["q"], 0.5)
            row.update(
                margin=round(m, 2),
                error=round(m - med, 2),
                pit=round(pit(f["q"], m), 3),
                crps=round(crps(f["q"], m), 3),
                in80=bool(quantile(f["q"], 0.10) <= m <= quantile(f["q"], 0.90)),
                in95=bool(quantile(f["q"], 0.025) <= m <= quantile(f["q"], 0.975)),
            )
        if "majority" in f and (m is not None or str(r.get("status")) == "legislature"):
            t = pd.to_numeric(r.get("total_votes"), errors="coerce")
            lead = max(
                pd.to_numeric(r.get("chal_votes"), errors="coerce"), pd.to_numeric(r.get("rep_votes"), errors="coerce")
            )
            none = str(r.get("status")) == "legislature" or (t > 0 and lead / t <= 0.5)
            row["majority"] = {"p_none": f["majority"]["p_none"], "none": bool(none)}
        rows.append(row)
    R = pd.DataFrame(rows)
    out = {"asof": doc["asof"], "races_forecast": len(R), "by_office": {}, "races": rows}
    for office, g in [("all", R), *R.groupby("office")]:
        w = g[g["y"].notna()]
        blk = {"called": len(w), "of": len(g)}
        if len(w):
            p, y = w["p"].to_numpy(float), w["y"].to_numpy(float)
            blk.update(
                brier=round(brier(p, y), 4),
                log_loss=round(log_loss(p, y), 4),
                favourite_won=round(float(((p > 0.5) == (y == 1)).mean()), 3),
                calibration=calibration(p, y),
            )
            if "raters" in w and w["raters"].notna().any():
                v = w[w["raters"].notna()]
                blk["raters"] = {
                    "n": len(v),
                    "brier_model": round(brier(v["p"], v["y"]), 4),
                    "brier_raters": round(brier(v["raters"], v["y"]), 4),
                }
        mm = g[g["margin"].notna()] if "margin" in g else g.iloc[0:0]
        if len(mm):
            blk["margins"] = {
                "n": len(mm),
                "mean_error": round(float(mm["error"].mean()), 2),
                "mae": round(float(mm["error"].abs().mean()), 2),
                "rmse": round(float(np.sqrt((mm["error"] ** 2).mean())), 2),
                "mean_pit": round(float(mm["pit"].mean()), 3),
                "crps": round(float(mm["crps"].mean()), 3),
                "in80": round(float(mm["in80"].mean()), 3),
                "in95": round(float(mm["in95"].mean()), 3),
            }
        out["by_office"][office] = blk
    out["chambers"] = chambers(doc, R, by)
    return out


def chambers(doc, R, by):
    C, out = doc["chambers"], {}
    for office in ("house", "senate", "governor"):
        g = R[R["office"] == office]
        if g.empty or office not in C:
            continue
        won = {
            w: int(
                sum(
                    str(by[(office, str(s))].get("winner", "")).strip().upper() == w
                    for s in g["seat"]
                    if (office, str(s)) in by
                )
            )
            for w in "DRI"
        }
        open_ = int(g["y"].isna().sum())
        if office == "house":
            d = doc["house_fixed"]["D"] + won["D"]
            blk = {"d_called": d, "undecided": open_}
            if not open_:
                blk.update(
                    d_seats=d,
                    independents=won["I"],
                    pit=round(mid_pit(C["house"]["hist"], d), 3),
                    p_maj=C["house"]["p_maj"],
                    d_majority=d >= doc["house_majority"],
                )
        elif office == "senate":
            d = C["senate"]["holdover_d"] + won["D"]
            blk = {"d_called": d, "undecided": open_}
            if not open_:
                hd = {}
                for k, v in C["senate"]["hist2"].items():
                    hd[k.split("|")[0]] = hd.get(k.split("|")[0], 0) + v
                r_seats = 100 - d - won["I"]
                ctrl = d > r_seats  # pro tem: the Republican vice president breaks a tie; independents abstain
                blk.update(
                    d_seats=d,
                    independents=won["I"],
                    pit=round(mid_pit(hd, d), 3),
                    p_ctrl=C["senate"]["p_ctrl"],
                    d_controls=bool(ctrl),
                    brier_ctrl=round((C["senate"]["p_ctrl"] - ctrl) ** 2, 4),
                )
        else:
            d = C["governor"]["holdover_d"] + won["D"]
            blk = {"d_called": d, "undecided": open_}
            if not open_:
                blk.update(d_seats=d, pit=round(mid_pit(C["governor"]["hist"], d), 3))
        out[office] = blk
    nat = by.get(("national", "US"))
    if nat and str(nat.get("status")) in COMPLETE:
        dv, rv, t = (pd.to_numeric(nat.get(k), errors="coerce") for k in ("chal_votes", "rep_votes", "total_votes"))
        if t > 0:
            m = float(100 * (dv - rv) / t)
            nv = C["national_vote"]
            g, dens = np.asarray(nv["grid"], float), np.asarray(nv["dens"], float)
            cdf = np.concatenate([[0.0], np.cumsum((dens[1:] + dens[:-1]) / 2 * np.diff(g))])
            out["national_vote"] = {
                "margin": round(m, 2),
                "forecast": nv["center"],
                "error": round(m - nv["center"], 2),
                "pit": round(float(np.interp(m, g, cdf / cdf[-1])), 3),
            }
    return out


def summary(sc, frozen):
    print(f"scorecard for the forecast of {sc['asof']}{'' if frozen else ' (NOT FROZEN: dry run)'}")
    for office, b in sc["by_office"].items():
        line = f"  {office:8s} called {b['called']}/{b['of']}"
        if b.get("brier") is not None:
            line += f"  Brier {b['brier']:.4f}  log loss {b['log_loss']:.4f}  favourite won {b['favourite_won']:.0%}"
        if "raters" in b:
            line += f"  (raters' Brier {b['raters']['brier_raters']:.4f} v ours {b['raters']['brier_model']:.4f} on {b['raters']['n']})"
        print(line)
        if "margins" in b:
            m = b["margins"]
            print(
                f"           margins {m['n']}: mean error {m['mean_error']:+.2f}  MAE {m['mae']:.2f}  PIT {m['mean_pit']:.2f}  80 % range {m['in80']:.0%}  95 % {m['in95']:.0%}"
            )
    for k, v in sc["chambers"].items():
        print(f"  {k}: {v}")


def main(argv):
    latest = "--latest" in argv
    doc, frozen = load_forecast(latest)
    if argv[:1] == ["template"]:
        template(doc)
        return
    sc = score(doc, load_results())
    sc["frozen"] = doc.get("frozen") if frozen else None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if frozen:  # a dry run never overwrites the real scorecard
        OUT.write_text(json.dumps(_clean(sc), indent=1))
    summary(sc, frozen)


def _clean(x):
    """NaN (an empty CSV cell) -> null: NaN is not JSON."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return None if isinstance(x, float) and x != x else x


if __name__ == "__main__":
    main(sys.argv[1:])
