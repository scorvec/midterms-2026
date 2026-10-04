"""Leak-free pollster track record (2026-10-03), used by the "What if?" page's "trust the best pollsters" tool. The 2026-09-29 test weighted race polls by 538's Jan-2024 ratings, which were built partly from the 2018-22
results the backtest scored, so it was not a fair test. Here a pollster is rated ONLY on general-election polls from cycles
before the year being forecast (538 raw_polls, 1998-2023, every rated poll with its result):
    excess_i = |poll margin - result| - mean |miss| of the other polls in the same race (races with >= 3 polls)
    score_p  = sum(excess) / (n_p + K)           (shrunk to 0 for thin records; negative = better than the field)
    mult_p   = exp(-gamma * score_p), clipped to [0.5, 2]  - a multiplier on the poll's weight (grade)
Keyed on 538's pollster_rating_id (stable across names); names map to ids through raw_polls, the 538 poll archives and the
ratings file, with model._xnorm and the joint-poll rule (a joint poll takes its best-rated partner).
"""
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
from . import model as M

GEN = ("Pres-G", "Sen-G", "Gov-G", "House-G")


def _raw():
    r = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False)
    r = r[r.type_simple.isin(GEN) & r.margin_poll.notna() & r.margin_actual.notna() & r.pollster_rating_id.notna()].copy()
    r["ae"] = (r.margin_poll - r.margin_actual).abs()
    g = r.groupby("race_id").ae; n = g.transform("size"); s = g.transform("sum")
    r = r[n >= 3].copy(); r["excess"] = r.ae - (s[n >= 3] - r.ae) / (n[n >= 3] - 1)      # vs the OTHER polls in the race
    return r


_R = None


def scores(before, K=25.0):
    """{rating_id: (score, n)} from cycles < before."""
    global _R
    if _R is None: _R = _raw()
    d = _R[_R.cycle < before]
    g = d.groupby("pollster_rating_id").excess
    return {int(k): (float(v.sum() / (len(v) + K)), int(len(v))) for k, v in g}


_NAMES = None


def name_ids():
    """{xnorm name: rating_id} from raw_polls, the 538 archives and the ratings file."""
    global _NAMES
    if _NAMES is not None: return _NAMES
    pairs = []
    raw = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False, usecols=["pollster", "pollster_rating_id"])
    pairs += list(raw.dropna().itertuples(index=False, name=None))
    for f in ("senate_polls_historical", "governor_polls_historical", "house_polls_historical", "generic_ballot_polls_historical"):
        fp = ROOT / "data" / "raw" / "538" / f"{f}.csv"
        if fp.exists():
            a = pd.read_csv(fp, low_memory=False, usecols=lambda c: c in ("pollster", "pollster_rating_id", "display_name"))
            for col in ("pollster", "display_name"):
                if col in a: pairs += list(a[[col, "pollster_rating_id"]].dropna().itertuples(index=False, name=None))
    try:
        rt = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "pollster-ratings-combined.csv")
        idc = next((c for c in rt.columns if "rating_id" in c or c == "pollster_rating_id"), None)
        if idc: pairs += list(rt[["pollster", idc]].dropna().itertuples(index=False, name=None))
    except Exception: pass
    out = {}
    for n, i in pairs:
        out.setdefault(M._xnorm(n), int(i))
    _NAMES = out; return out


def quality_fn(before, gamma, K=25.0, lo=0.5, hi=2.0):
    """name -> weight multiplier, rated on cycles < before."""
    S, ids = scores(before, K), name_ids()
    def one(key):
        i = ids.get(key)
        return None if i is None or i not in S else float(np.clip(np.exp(-gamma * S[i][0]), lo, hi))
    cache = {}
    def f(name):
        if name in cache: return cache[name]
        k = M._xnorm(name); m = one(k)
        if m is None and "/" in k:                                    # joint poll: its best-rated partner
            ms = [one(x.strip()) for x in k.split("/") if x.strip()]; ms = [x for x in ms if x is not None]
            m = max(ms) if ms else None
        cache[name] = 1.0 if m is None else m; return cache[name]
    return f


if __name__ == "__main__":
    S = scores(2027); ids = name_ids(); inv = {}
    for k, i in ids.items(): inv.setdefault(i, k)
    best = sorted(((v[0], v[1], inv.get(i, i)) for i, v in S.items() if v[1] >= 40))
    print("best 15 (score = mean excess miss, pts; n polls):"); [print(f"  {s:+.2f}  n {n:4d}  {nm}") for s, n, nm in best[:15]]
    print("worst 8:"); [print(f"  {s:+.2f}  n {n:4d}  {nm}") for s, n, nm in best[-8:]]
    for nm in ("The New York Times/Siena University", "Siena College", "Marist University", "Emerson College", "Trafalgar Group (R)",
               "Rasmussen Reports", "Selzer & Co.", "Quinnipiac University", "SurveyUSA", "YouGov", "Big Data Poll", "InsiderAdvantage"):
        print(f"  mult at gamma 0.3: {quality_fn(2027, 0.3)(nm):.2f}  {nm}")
