"""Race-poll calibration from 538's rated polls (raw_polls.csv: every poll with the actual result).

    python -m midterms.race_poll_calibration      -> data/cache/race_poll_calibration.json

Scope: House / Senate / Governor generals 1998-2022, last 21 days, D v R polls. Error = poll margin - actual,
D-R, + = too Democratic. Every lean is measured RELATIVE TO the non-partisan polls of the same race, so each
race's own polling miss (2020 everywhere) cancels and what is left is the pollster / sponsor.

Pollster leans use an explicit pattern list (a 2026 pollster string -> the 538 name), never fuzzy matching:
an automatic normaliser mapped 'Public Policy Research' to PPP and 'co/efficient' to an unrelated 'co'.
Each lean is shrunk toward the tag-based sponsor effect by n / (n + K_SHRINK).
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "race_poll_calibration.json"
K_SHRINK = 10
# 2026 pollster string (lowercase substring) -> regex on 538's pollster name
POLLSTERS = {
    "trafalgar": r"trafalgar", "insideradvantage": r"insideradvantage", "rasmussen": r"rasmussen",
    "co/efficient": r"co/efficient", "cygnal": r"cygnal", "public policy polling": r"public policy polling",
    "data for progress": r"data for progress", "change research": r"change research", "surveyusa": r"surveyusa",
    "emerson": r"emerson", "yougov": r"yougov", "siena": r"siena", "gravis": r"gravis", "ssrs": r"ssrs",
    "suffolk": r"suffolk", "quinnipiac": r"quinnipiac", "marist": r"marist", "monmouth": r"monmouth",
    "mason-dixon": r"mason-dixon", "st. pete polls": r"st\. pete", "susquehanna": r"susquehanna",
    "epic-mra": r"epic-mra|epic/mra", "mitchell": r"mitchell research", "fabrizio": r"fabrizio",
    "public opinion strategies": r"public opinion strategies", "remington": r"remington", "wick": r"^wick",
    "echelon": r"echelon", "big data poll": r"big data poll", "atlasintel": r"atlasintel|atlas intel",
}


def _load():
    d = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False)
    d = d[d["type_simple"].isin(["House-G", "Sen-G", "Gov-G"]) & (d["time_to_election"] <= 21)]
    d = d[d["cand1_party"].isin(["DEM", "REP"]) & d["cand2_party"].isin(["DEM", "REP"]) & (d["cand1_party"] != d["cand2_party"])].copy()
    sgn = np.where(d["cand1_party"] == "DEM", 1, -1)
    d["err"] = (d["margin_poll"] - d["margin_actual"]) * sgn
    d["und"] = 100 - d["cand1_pct"] - d["cand2_pct"]; d["third"] = 100 - d["cand1_actual"] - d["cand2_actual"]
    base = d[d["partisan"].isna()].groupby("race_id")["err"].mean()
    d = d[d["race_id"].isin(base.index)].copy(); d["rel"] = d["err"] - d["race_id"].map(base)
    return d


def calibration(before=None, lean_cycles=4, d=None):
    """The calibration dict main() writes, fitted only on cycles < `before` (2026-10-03, for leak-free backtests: the live file's
    pollster leans come from 2016-22 and its sponsor effects from 1998-2022, so scoring 2018-22 with it used each cycle's own
    results). Sponsor effects from every earlier cycle; pollster leans from the last `lean_cycles` earlier cycles."""
    d = _load() if d is None else d
    if before is not None: d = d[d["cycle"] < before]
    cyc = sorted(d["cycle"].unique()); recent = d[d["cycle"].isin(cyc[-lean_cycles:])] if before is not None else d[d["cycle"] >= 2016]
    out = {"source": f"538 raw_polls, cycles < {before}" if before else "538 raw_polls, House/Senate/Gov generals 1998-2022, last 21 days",
           "n": int(len(d)), "k_shrink": K_SHRINK, "sponsor": {}}
    for k, lab in (("DEM", "D"), ("REP", "R")):
        g = d[d["partisan"] == k]["rel"]
        out["sponsor"][lab] = {"lean": round(float(g.mean()), 2), "se": round(float(g.std() / np.sqrt(len(g))), 2), "sd": round(float(g.std()), 2), "n": int(len(g))}
    out["sponsor"]["none"] = {"sd": round(float(d[d["partisan"].isna()]["rel"].std()), 2)}
    pl = {}
    for key, pat in POLLSTERS.items():
        g = recent[recent["pollster"].str.contains(pat, case=False, regex=True)]
        if len(g) >= 5:
            pl[key] = {"lean": round(float(g["rel"].mean()), 2), "n": int(len(g)), "flagged_share": round(float(g["partisan"].notna().mean()), 2)}
    out["pollsters"] = pl
    return out


def main():
    d = _load()
    out = {"source": "538 raw_polls, House/Senate/Gov generals 1998-2022, last 21 days", "n": int(len(d)), "k_shrink": K_SHRINK}
    sp = {}
    for k, lab in (("DEM", "D"), ("REP", "R")):
        g = d[d["partisan"] == k]["rel"]
        sp[lab] = {"lean": round(float(g.mean()), 2), "se": round(float(g.std() / np.sqrt(len(g))), 2), "sd": round(float(g.std()), 2), "n": int(len(g))}
    sp["none"] = {"sd": round(float(d[d["partisan"].isna()]["rel"].std()), 2)}
    out["sponsor"] = sp
    nb = d[d["partisan"].isna()].copy()
    ub = pd.cut(nb["und"], [-1, 4, 8, 12, 16, 25, 100]); out["undecided_sd"] = {str(k): round(float(v), 2) for k, v in nb.groupby(ub, observed=True)["err"].std().items()}
    tb = pd.cut(nb["third"], [-1, 2, 5, 10, 100]); out["third_abs_err"] = {str(k): round(float(v), 2) for k, v in nb.groupby(tb, observed=True)["err"].apply(lambda x: x.abs().mean()).items()}
    recent = d[d["cycle"] >= 2016]; pl = {}
    for key, pat in POLLSTERS.items():
        g = recent[recent["pollster"].str.contains(pat, case=False, regex=True)]
        if len(g) >= 5:
            pl[key] = {"lean": round(float(g["rel"].mean()), 2), "n": int(len(g)), "flagged_share": round(float(g["partisan"].notna().mean()), 2)}
    out["pollsters"] = pl
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("sponsor", "undecided_sd", "third_abs_err")}, indent=1))
    for k, v in sorted(pl.items(), key=lambda kv: kv[1]["lean"]): print(f"  {k:26s} {v['lean']:+5.2f}  n {v['n']:3d}  538-flagged {100 * v['flagged_share']:.0f}%")


# Bipartisan teams that publish without both tags (2026-10-03): AARP's Fabrizio (R) / Impact Research (D), Fox's Beacon (D) / Shaw (R),
# Cygnal (R) / Beacon (D). Lower-case substrings; a poll naming two of a pair is a joint poll.
BIPARTISAN_PAIRS = [("fabrizio", "impact research"), ("beacon", "shaw"), ("cygnal", "beacon")]


def lean_for(pollster: str, tag: str, cal: dict) -> float:
    """Expected lean of this poll (+ = too Democratic, i.e. too far toward the challenger's side for tag 'D').
    Pollster record shrunk toward the tag-based sponsor effect.
    A JOINT bipartisan poll (both a (D) and an (R) firm, or a known pair) averages its firms' records, each shrunk toward 0, a firm
    without a record counting 0 (2026-10-03): the first-match rule gave the AARP Fabrizio/Impact polls Fabrizio's own lean (-1.57,
    62 % of it from its Republican internals, shrunk to -0.70) and Cygnal/Beacon Cygnal's - a lean built on one side's internals."""
    prior = cal["sponsor"][tag]["lean"] if tag in ("D", "R") else 0.0
    s = str(pollster).lower()
    tags = set(re.findall(r"\((d|r)\)", s))
    joint = len(tags) == 2 or any(a_ in s and b_ in s for a_, b_ in BIPARTISAN_PAIRS)
    if joint:
        parts = [x for x in re.split(r"/", s.replace("co/efficient", "coefficient")) if x.strip()]
        vals = []
        for part in parts:
            part = part.replace("coefficient", "co/efficient")
            hit = next((v for key, v in cal["pollsters"].items() if key in part), None)
            vals.append(hit["n"] / (hit["n"] + cal["k_shrink"]) * hit["lean"] if hit else 0.0)
        return float(np.mean(vals)) if vals else 0.0
    for key, v in cal["pollsters"].items():
        if key in s:
            w = v["n"] / (v["n"] + cal["k_shrink"])
            return w * v["lean"] + (1 - w) * prior
    return prior


def pollster_shared_sd(before=None, types=("House-G", "Sen-G", "Gov-G"), boot=0, seed=1):
    """tau (pts): sd of the pollster-race error a pollster's polls share within one race (model.POLLSTER_SHARED_ERROR), from 538
    raw_polls (generals, even years, last 61 days, D v R; cycles < `before` only when given). Error = poll margin - result, minus
    the pollster / sponsor lean the model removes (lean_for with calibration(before)); one row per poll and race. For every pair
    of polls in a race, h = (e_i - e_j)^2 / 2 - (v_i + v_j) / 2 with v the binomial sampling variance of the margin. Same pollster
    (538 pollster_rating_id): E[h] = own non-sampling variance + time drift; different pollsters: the same + tau^2. So tau^2 =
    mean h (different) - mean h (same), compared within race x gap bin (0-3, 4-7, 8-14, 15-30, 31-61 days apart: drift cancels)
    and weighted by the number of same-pollster pairs. boot > 0 also returns a race-bootstrap se of tau^2."""
    d = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False)
    d = d[d["type_simple"].isin(types) & (d["cycle"] % 2 == 0) & d["cand1_party"].isin(["DEM", "REP"]) & d["cand2_party"].isin(["DEM", "REP"])
          & (d["cand1_party"] != d["cand2_party"])].copy()
    if before is not None: d = d[d["cycle"] < before]
    dem = np.where(d["cand1_party"] == "DEM", d["cand1_pct"], d["cand2_pct"]) / 100; rep = np.where(d["cand1_party"] == "DEM", d["cand2_pct"], d["cand1_pct"]) / 100
    d["err"] = (d["margin_poll"] - d["margin_actual"]) * np.where(d["cand1_party"] == "DEM", 1, -1)
    d["v"] = 1e4 * (dem + rep - (dem - rep) ** 2) / d["samplesize"]
    d = d.dropna(subset=["err", "v"])
    d = d.groupby(["race_id", "poll_id"], as_index=False).agg(pollster=("pollster", "first"), pid=("pollster_rating_id", "first"),
                                                              partisan=("partisan", "first"), err=("err", "mean"), v=("v", "mean"), tte=("time_to_election", "first"))
    cal = calibration(before=before)
    d["e"] = d["err"] - [lean_for(p, {"DEM": "D", "REP": "R"}.get(t, ""), cal) for p, t in zip(d["pollster"], d["partisan"])]
    rows = []
    for r, g in d.groupby("race_id"):
        if len(g) < 3: continue
        e, v, p, t = g["e"].values, g["v"].values, g["pid"].values, g["tte"].values
        i, j = np.triu_indices(len(g), 1)
        rows.append(pd.DataFrame({"race": r, "same": p[i] == p[j], "gap": np.abs(t[i] - t[j]), "h": (e[i] - e[j]) ** 2 / 2 - (v[i] + v[j]) / 2}))
    P = pd.concat(rows); P["gb"] = pd.cut(P["gap"], [-1, 3, 7, 14, 30, 61])
    A = P.groupby(["race", "gb", "same"], observed=True)["h"].agg(["sum", "size"]).unstack("same").dropna()
    def est(A):
        dd = A[("sum", False)] / A[("size", False)] - A[("sum", True)] / A[("size", True)]; w = A[("size", True)]
        return float((dd * w).sum() / w.sum())
    t2 = est(A)
    if not boot: return float(np.sqrt(max(t2, 0.0)))
    rng = np.random.default_rng(seed); races = A.index.get_level_values(0).unique(); bs = []
    for _ in range(boot): bs.append(est(pd.concat([A.loc[[r]] for r in rng.choice(races, len(races))])))
    return float(np.sqrt(max(t2, 0.0))), t2, float(np.std(bs))


if __name__ == "__main__":
    main()
