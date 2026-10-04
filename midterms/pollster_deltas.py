"""Same-pollster poll-to-poll changes vs our trend (2026-09-30, user: "when comparing polling trends, does it make sense to
compare the most recent poll with the previous poll from the same pollster to calculate the delta?" -> "Sure do that").

For each pollster whose newest poll ended in the last WINDOW days: its newest poll minus its previous poll of the SAME
population (LV vs LV, RV vs RV; canonical names via names.canon, already applied by generic.merged), previous poll at most
MAX_GAP days earlier. House effect and population offset cancel in the difference, so the pooled change is a mix-free read
of movement. Compared with our trend's own change over each pollster's matching window (field midpoints).

    python -m midterms.pollster_deltas        # standalone (reads the cached poll files, refits)
Daily: called from generic.main -> printed line + web/data/pollster_deltas.json (PRIVATE: publish_site does not copy it).

Output per series:
  pollsters        contributing pollsters (one row each; a pollster with both an LV and an RV pair is averaged, n-weighted)
  median, wmean    median and effective-n-weighted mean of the changes; boot 95 % CIs (pollsters resampled)
  trend_wmean      the trend's change over the same windows, same weights
  gap = wmean - trend_wmean with a paired bootstrap CI; `disagree` when the trend's change lies outside the CI of wmean
  outliers         pollsters whose change minus the trend's change is > Z_FLAG sd (sampling + non-sampling noise of two
                   polls): a possible methodology change (population, weighting, mode, question)
  watch            the same at 2 < |z| <= Z_FLAG
  pop_switch       pollsters whose newest poll is a population they did not use in their previous poll (e.g. RV -> LV)
"""
from __future__ import annotations
import json, datetime as dt, numpy as np, pandas as pd
from pathlib import Path
from . import generic as G

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / "web" / "data" / "pollster_deltas.json"
WINDOW, MAX_GAP, Z_FLAG, Z_WATCH, NBOOT = 30, 120, 2.5, 2.0, 4000


def _nclip(n): return np.clip(pd.Series(n).fillna(600).astype(float).values, 100, 3000)


def compute(df: pd.DataFrame, F: dict, window=WINDOW, max_gap=MAX_GAP, asof=None, seed=0) -> dict:
    d = G.field_prepare(df) if G.FIELD_PREP else df.copy()
    tcol = "mid" if "mid" in d else "end_date"
    t1 = pd.Timestamp(asof) if asof is not None else d["end_date"].max()
    d = d[d["end_date"] <= t1]
    # one row per (pollster, population, survey): same-population copies of one survey keep the largest n
    d = d.sort_values("n", ascending=False, na_position="last").drop_duplicates(["pollster", "pop", "end_date"]).sort_values([tcol, "end_date"])
    tau2 = max(F["resid_sd"] ** 2 - 1e4 * 0.95 / 1000.0, 1.0)      # non-sampling variance per poll (resid sd net of a 1,000 sample)
    pairs, switches, hist = [], [], []
    for p, g in d.groupby("pollster"):
        last_all = g.iloc[-1]
        for pop, gp in g.groupby("pop"):
            for j in range(1, len(gp)):
                cur, prv = gp.iloc[j], gp.iloc[j - 1]; gap = (cur[tcol] - prv[tcol]).days
                if gap > max_gap or gap <= 0: continue
                n1, n2 = _nclip([cur["n"]])[0], _nclip([prv["n"]])[0]
                tr = G.trend_at(F, cur[tcol]) - G.trend_at(F, prv[tcol])
                sd = float(np.sqrt(1e4 * 0.95 / n1 + 1e4 * 0.95 / n2 + 2 * tau2))
                r = {"pollster": p, "pop": pop, "prev_mid": str(prv[tcol].date()), "last_mid": str(cur[tcol].date()), "gap_days": int(gap),
                     "prev": round(float(prv["y"]), 2), "last": round(float(cur["y"]), 2), "delta": round(float(cur["y"] - prv["y"]), 2),
                     "trend_delta": round(float(tr), 2), "neff": float(1 / (1 / n1 + 1 / n2)), "sd": sd}
                hist.append(r)
                # the newest poll of each population, if it ended in the window (a last survey may carry LV + RV)
                if j == len(gp) - 1 and cur["end_date"] >= t1 - pd.Timedelta(days=window): pairs.append(r)
        if last_all["end_date"] < t1 - pd.Timedelta(days=window): continue
        # population switch: newest poll's population never in the previous survey (e.g. RV -> LV after Labor Day)
        prev_surveys = g[g["end_date"] < last_all["end_date"] - pd.Timedelta(days=1)]
        cur_pops = set(g[g["end_date"] >= last_all["end_date"] - pd.Timedelta(days=1)]["pop"])
        if len(prev_surveys):
            ps = prev_surveys.iloc[-1]; prev_pops = set(prev_surveys[prev_surveys["end_date"] >= ps["end_date"] - pd.Timedelta(days=1)]["pop"])
            new = cur_pops - prev_pops
            if new and not (cur_pops & prev_pops):
                cp = sorted(new, key=lambda q: G.POP_RANK.get(q, 9))[0]; cur = g[g["pop"] == cp].iloc[-1]
                adj = (cur["y"] - F["pop"].get(cp, 0.0)) - (ps["y"] - F["pop"].get(ps["pop"], 0.0))
                switches.append({"pollster": p, "from": ps["pop"], "to": cp, "prev_mid": str(ps[tcol].date()), "last_mid": str(cur[tcol].date()),
                                 "raw_delta": round(float(cur["y"] - ps["y"]), 2), "pop_adjusted_delta": round(float(adj), 2),
                                 "trend_delta": round(float(G.trend_at(F, cur[tcol]) - G.trend_at(F, ps[tcol])), 2)})
    # the SRS formula overstates poll-to-poll noise (weighted panels, trackers): rescale so that the excess over the trend's
    # change has a robust sd of 1 across EVERY consecutive same-population pair in the series (in-sample calibration)
    Hh = pd.DataFrame(hist); zr = (Hh["delta"] - Hh["trend_delta"]) / Hh["sd"]; scale = float(1.4826 * np.median(np.abs(zr - np.median(zr)))) if len(Hh) >= 20 else 1.0
    for r in pairs: r["sd"] = round(r["sd"] * scale, 2)
    P = pd.DataFrame(pairs)
    res = {"asof": str(t1.date()), "window_days": window, "noise_scale": round(scale, 3), "history_pairs": len(Hh), "max_gap_days": max_gap, "pairs": pairs, "pop_switch": switches}
    if P.empty:
        res.update(pollsters=0); return res
    # one vote per pollster: its populations averaged by effective n
    agg = P.assign(wd=P["delta"] * P["neff"], wt=P["trend_delta"] * P["neff"], wv=P["sd"] ** 2 * P["neff"] ** 2).groupby("pollster").agg(
        wd=("wd", "sum"), wt=("wt", "sum"), wv=("wv", "sum"), neff=("neff", "sum"), pops=("pop", lambda s: "+".join(sorted(s))))
    agg["delta"] = agg["wd"] / agg["neff"]; agg["trend_delta"] = agg["wt"] / agg["neff"]; agg["sd"] = np.sqrt(agg["wv"]) / agg["neff"]
    x, tr, w = agg["delta"].values, agg["trend_delta"].values, agg["neff"].values
    wm = lambda v, ww: float(np.sum(v * ww) / np.sum(ww))
    rng = np.random.default_rng(seed); k = len(x); B = rng.integers(0, k, size=(NBOOT, k))
    bm = np.array([wm(x[b], w[b]) for b in B]); bmed = np.median(x[B], axis=1); bgap = np.array([wm(x[b] - tr[b], w[b]) for b in B])
    ci = lambda a: [round(float(np.percentile(a, 2.5)), 2), round(float(np.percentile(a, 97.5)), 2)]
    exc = agg["delta"] - agg["trend_delta"]; z = exc / agg["sd"]
    outl = [{"pollster": p, "pops": agg.loc[p, "pops"], "delta": round(float(agg.loc[p, "delta"]), 2), "trend_delta": round(float(agg.loc[p, "trend_delta"]), 2),
             "z": round(float(z[p]), 2)} for p in agg.index[np.abs(z.values) > Z_FLAG]]
    watch = [{"pollster": p, "delta": round(float(agg.loc[p, "delta"]), 2), "trend_delta": round(float(agg.loc[p, "trend_delta"]), 2), "z": round(float(z[p]), 2)}
             for p in agg.index[(np.abs(z.values) > Z_WATCH) & (np.abs(z.values) <= Z_FLAG)]]
    t_wm = wm(tr, w); ci_wm = ci(bm)
    res.update({"pollsters": int(k), "pairs_n": len(P), "median": round(float(np.median(x)), 2), "median_ci": ci(bmed),
                "wmean": round(wm(x, w), 2), "wmean_ci": ci_wm, "trend_wmean": round(t_wm, 2), "trend_median": round(float(np.median(tr)), 2),
                "gap": round(wm(x - tr, w), 2), "gap_ci": ci(bgap), "disagree": bool(t_wm < ci_wm[0] or t_wm > ci_wm[1]),
                "mean_gap_days": round(float(P["gap_days"].mean()), 1), "median_gap_days": float(P["gap_days"].median()), "outliers": outl, "watch": watch,
                "by_pollster": [{"pollster": p, "pops": r.pops, "delta": round(float(r.delta), 2), "trend_delta": round(float(r.trend_delta), 2),
                                 "z": round(float(z[p]), 2), "neff": int(round(r.neff))} for p, r in agg.sort_values("delta").iterrows()]})
    for r in res["pairs"]: r["neff"] = int(round(r["neff"]))
    return res


def line(kind: str, r: dict) -> str:
    if not r.get("pollsters"): return f"same-pollster deltas ({kind}): no pairs in the last {r['window_days']} d"
    s = (f"same-pollster deltas ({kind}, last {r['window_days']} d, {r['pollsters']} pollsters, median gap {r['median_gap_days']:.0f} d): "
         f"median {r['median']:+.2f} {r['median_ci']}, n-wtd {r['wmean']:+.2f} {r['wmean_ci']} | trend over the same windows {r['trend_wmean']:+.2f}"
         f" -> {'DISAGREE' if r['disagree'] else 'agree'} (gap {r['gap']:+.2f} {r['gap_ci']})")
    if r["outliers"]: s += " | outliers: " + ", ".join(f"{o['pollster']} {o['delta']:+.1f} (z {o['z']:+.1f})" for o in r["outliers"])
    if r.get("watch"): s += " | watch (z>2): " + ", ".join(f"{o['pollster']} {o['delta']:+.1f} vs trend {o['trend_delta']:+.1f}" for o in r["watch"])
    if r["pop_switch"]: s += " | pop switch: " + ", ".join(f"{o['pollster']} {o['from']}->{o['to']} {o['raw_delta']:+.1f}" for o in r["pop_switch"])
    return s


def run(series: dict) -> dict:
    """series: {kind: (poll frame, fit)} -> writes OUT and prints one line per series."""
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "private": True,
           "method": __doc__.split("Output per series:")[0].strip()}
    for kind, (df, F) in series.items():
        out[kind] = compute(df, F); print(line(kind, out[kind]))
    OUT.parent.mkdir(parents=True, exist_ok=True); json.dump(out, open(OUT, "w"), indent=1)
    return out


def main():
    C = G.CACHE; ser = {}
    for kind, f in (("generic", "generic_polls.csv"), ("approval", "approval_polls_votehub.csv")):
        df = pd.read_csv(C / f, parse_dates=["end_date", "start_date"]); df["internal"] = df["internal"].fillna(False).astype(bool)
        df["partisan"] = df["partisan"].where(df["partisan"].notna(), None); ser[kind] = (df, G.fit(df))
    run(ser)


if __name__ == "__main__":
    main()
