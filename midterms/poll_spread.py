"""Poll spread (a herding check): are a race's polls further apart, or closer together, than sampling error alone allows?

Each pollster's newest poll of a race in the last WINDOW days is compared with the race's precision-weighted mean, on the
published D-R margin (before any of the model's adjustments). Sampling alone gives a poll's margin the variance

    v_i = 1e4 (d + r - (d - r)^2) / n        (d, r the two candidates' shares as fractions, n the sample size)

and   ratio = sum_i (m_i - mean)^2 / v_i / (k - 1)   averages 1 when k polls differ by sampling error only. Real polls also
differ by house effect, mode, likely-voter screen and timing, so a race's ratio is normally above 1. A ratio well below 1
(polls closer together than chance allows) is the mark of herding: pollsters weighting toward the average, or holding
back results that look like outliers, as in the 2024 presidential swing states. p_low = P(chi2_{k-1} <= sum) is how
unlikely that tightness is under sampling error alone.

The benchmark is the same statistic on 538's rated polls (raw_polls.csv: Senate and governor generals 1998-2022, last
21 days, one poll per pollster, races with >= K_MIN pollsters), built once into data/static/poll_spread_hist.json:

    python -m midterms.poll_spread --hist     (needs data/raw/538repo/raw_polls.csv)

The daily run (build_web.polls_page) writes web/data/poll_spread.json from the poll list it has just published:
per race the ratio and p_low, per office the pooled ratio (sum of chi2 / sum of k-1) and how often K_MIN-race draws from
the benchmark races of the same office pool to a ratio at least as low ('p_hist'). A poll without a sample size takes its
office's median and is counted in 'n_known'.

Diagnostic only: nothing here enters the forecast. In the benchmark a race's tightness says nothing about its miss (Spearman
0.02 between the ratio and |weighted mean - result| over 342 races; the tightest third missed by 4.2 points on average,
the widest third by 4.2), and a cycle's pooled ratio nothing about that cycle's average miss (Senate 0.01, governor
-0.27, 12-13 cycles). The forecast's race-level systematic error already treats a race's polls as sharing one miss.

    python -m midterms.poll_spread            (from the committed web/data/polls.json)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "538repo" / "raw_polls.csv"
HIST_JSON = ROOT / "data" / "static" / "poll_spread_hist.json"
POLLS_JSON = ROOT / "web" / "data" / "polls.json"
OUT = ROOT / "web" / "data" / "poll_spread.json"
WINDOW = 21  # days: the benchmark's span (538 rates the polls of the last 21 days)
K_MIN = 4  # pollsters in a race
N_DRAWS = 20000
OFFICES = {"senate": "Sen-G", "governor": "Gov-G"}
_STOP = {"the", "inc", "llc", "co", "reports", "research", "polling", "poll", "polls", "group", "strategies"}


def pollster_key(s) -> str:
    """One key per polling firm within a race: lower case, sponsor tags and filler words dropped, first two words
    ('Rasmussen Reports (R)' and 'Rasmussen (R)' -> 'rasmussen'; 'Public Policy Polling' -> 'public policy')."""
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", str(s).lower().replace("&amp;", "&"))
    w = [x for x in re.sub(r"[^a-z0-9/]+", " ", s).replace("/", " ").split() if x not in _STOP]
    return " ".join(w[:2])


def samp_var(dem, rep, n):
    """Sampling variance of the D-R margin in points^2 (shares in points)."""
    d, r = np.asarray(dem, float) / 100, np.asarray(rep, float) / 100
    return 1e4 * (d + r - (d - r) ** 2) / np.asarray(n, float)


def spread(m, v) -> dict:
    """Dispersion of margins m about their precision-weighted mean against the sampling variances v."""
    m, v = np.asarray(m, float), np.asarray(v, float)
    w = 1 / v
    mean = float((w * m).sum() / w.sum())
    chi, df = float(((m - mean) ** 2 / v).sum()), len(m) - 1
    return {
        "k": len(m),
        "mean": round(mean, 2),
        "sd": round(float(np.std(m, ddof=1)), 2),
        "samp_sd": round(float(np.sqrt(v.mean())), 2),
        "chi2": round(chi, 3),
        "ratio": round(chi / df, 3),
        "p_low": round(float(stats.chi2.cdf(chi, df)), 4),
    }


def history(write=True) -> dict:
    """The benchmark from 538's raw_polls.csv: per office the per-race (chi2, k-1) and summaries."""
    d = pd.read_csv(RAW, low_memory=False)
    d = d[d.type_simple.isin(OFFICES.values()) & (d.cycle % 2 == 0) & d.time_to_election.between(0, WINDOW)]
    d = d[d.cand1_party.isin(["DEM", "REP"]) & d.cand2_party.isin(["DEM", "REP"]) & (d.cand1_party != d.cand2_party)]
    d = d[d.samplesize.between(100, 20000)]
    first = d.cand1_party == "DEM"
    d = d.assign(
        dem=np.where(first, d.cand1_pct, d.cand2_pct),
        rep=np.where(first, d.cand2_pct, d.cand1_pct),
        date=pd.to_datetime(d.polldate),
        pk=d.pollster.map(pollster_key),
    )
    d = d.sort_values(["race_id", "pk", "date", "samplesize"]).groupby(["race_id", "pk"]).tail(1)
    res = {"source": "538 raw_polls.csv", "window_days": WINDOW, "k_min": K_MIN, "offices": {}}
    for off, typ in OFFICES.items():
        rows = []
        for (cy, _), g in d[d.type_simple == typ].groupby(["cycle", "race_id"]):
            if len(g) >= K_MIN:
                s = spread(g.dem - g.rep, samp_var(g.dem, g.rep, g.samplesize))
                rows.append((int(cy), s["chi2"], s["k"] - 1, s["ratio"], s["p_low"]))
        r = pd.DataFrame(rows, columns=["cycle", "chi2", "df", "ratio", "p_low"])
        res["offices"][off] = {
            "races": len(r),
            "pooled": round(float(r.chi2.sum() / r.df.sum()), 3),
            "median": round(float(r.ratio.median()), 3),
            "below_1": round(float((r.ratio < 1).mean()), 3),
            "p_low_05": round(float((r.p_low < 0.05).mean()), 3),
            "by_cycle": {str(c): round(float(g.chi2.sum() / g.df.sum()), 3) for c, g in r.groupby("cycle")},
            "chi2_df": r[["chi2", "df"]].values.round(3).tolist(),
        }
    if write:
        HIST_JSON.write_text(json.dumps(res, separators=(",", ":")) + "\n")
    return res


def p_hist(chi2_df, k, pooled, seed=1) -> float:
    """Share of k-race draws (with replacement) from the benchmark races whose pooled ratio is <= pooled."""
    h = np.asarray(chi2_df, float)
    ix = np.random.default_rng(seed).integers(0, len(h), (N_DRAWS, k))
    sims = h[ix, 0].sum(1) / h[ix, 1].sum(1)
    return round(float((sims <= pooled).mean()), 4)


def race_polls(doc: dict) -> pd.DataFrame:
    """Each pollster's newest poll of each Senate / governor race in the last WINDOW days of polls.json."""
    asof = pd.Timestamp(doc["asof"])
    rows = []
    for r in doc["races"]:
        if r["office"] not in OFFICES:
            continue
        for p in r["polls"]:
            if p.get("dem") is None or p.get("rep") is None or "[" in p["pollster"]:  # a projected sub-state poll
                continue
            if (asof - pd.Timestamp(p["end"])).days > WINDOW:
                continue
            rows.append({
                "office": r["office"], "seat": r["seat"], "label": r.get("label", ""), "pollster": p["pollster"],
                "end": p["end"], "dem": p["dem"], "rep": p["rep"], "n": p.get("n"),
            })  # fmt: skip
    t = pd.DataFrame(rows, columns=["office", "seat", "label", "pollster", "end", "dem", "rep", "n"])
    t["n"] = pd.to_numeric(t["n"], errors="coerce").where(lambda x: x.between(100, 20000))
    t["n_known"] = t["n"].notna()
    if t["n_known"].any():  # a poll without a sample size: its office's median, else the overall median
        t["n"] = t["n"].fillna(t.groupby("office")["n"].transform("median")).fillna(t["n"].median())
    t["n"] = t["n"].fillna(800.0)  # none known at all (a polls.json from before sample sizes were published)
    t["pk"] = t.pollster.map(pollster_key)
    return t.sort_values(["office", "seat", "pk", "end", "n"]).groupby(["office", "seat", "pk"]).tail(1)


def build(doc: dict | None = None, write=True) -> dict:
    if doc is None:
        doc = json.loads(POLLS_JSON.read_text())
    hist = json.loads(HIST_JSON.read_text())
    t = race_polls(doc)
    races = []
    for (off, seat), g in t.groupby(["office", "seat"]):
        if len(g) < K_MIN:
            continue
        s = spread(g.dem - g.rep, samp_var(g.dem, g.rep, g.n))
        races.append({
            "office": off, "seat": seat, "label": g.label.iloc[0], **s, "n_known": int(g.n_known.sum()),
            "range": [float((g.dem - g.rep).min()), float((g.dem - g.rep).max())],
            "pollsters": [f"{x.pollster} {x.dem - x.rep:+g}" for x in g.sort_values("end").itertuples()],
        })  # fmt: skip
    offices = {}
    for off in OFFICES:
        r = [x for x in races if x["office"] == off]
        if not r:
            continue
        chi, df = sum(x["chi2"] for x in r), sum(x["k"] - 1 for x in r)
        h = hist["offices"][off]
        offices[off] = {
            "races": len(r),
            "pooled": round(chi / df, 3),
            "median": round(float(np.median([x["ratio"] for x in r])), 3),
            "below_1": round(float(np.mean([x["ratio"] < 1 for x in r])), 3),
            "p_hist": p_hist(h["chi2_df"], len(r), chi / df),
            "hist_pooled": h["pooled"],
            "hist_median": h["median"],
            "hist_last": {c: h["by_cycle"][c] for c in sorted(h["by_cycle"])[-3:]},
        }
    res = {
        "asof": doc["asof"],
        "window_days": WINDOW,
        "k_min": K_MIN,
        "polls": len(t),
        "n_known": round(float(t.n_known.mean()), 3) if len(t) else None,
        "offices": offices,
        "races": sorted(races, key=lambda x: x["ratio"]),
    }
    if write:
        OUT.write_text(json.dumps(res, separators=(",", ":")) + "\n")
        print("poll spread: " + "; ".join(
            f"{o} {v['races']} races pooled {v['pooled']:.2f} (1998-2022 {v['hist_pooled']:.2f}, p {v['p_hist']:.3f})"
            for o, v in offices.items()) + f"; sample sizes known for {res['n_known']:.0%} of polls", flush=True)  # fmt: skip
    return res


if __name__ == "__main__":
    import sys

    if "--hist" in sys.argv:
        h = history()
        for o, v in h["offices"].items():
            print(f"{o}: {v['races']} races, pooled {v['pooled']}, median {v['median']}, by cycle {v['by_cycle']}")
    else:
        build()
