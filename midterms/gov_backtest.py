"""Leak-free governor backtest (2026-10-05), the governor counterpart of the Senate's live harness.

For every even-year cycle 2006-2024 and several dates in the campaign, the LIVE governor path (gov2026: Prior, prepare, blend,
simulate) is run with only what was known on that date:
  - prior: gov2026.Prior fitted WALK-FORWARD on the governor races of earlier cycles only (1998 onward; never a later cycle)
  - polls: 538's governor poll archive for 2018-2024 (every general-election poll; the nominees' question only, versions of one
    survey averaged, first round where ranked choice), 538's raw_polls for 2006-2016 (rated polls of the last ~60 days, so those
    cycles are scored from Oct 1 on); a poll is seen RELEASE_LAG days after its last field day
  - race-poll calibration (sponsor effects, pollster leans) fitted on earlier cycles only (race_poll_calibration.calibration(before))
  - E: the generic-ballot average known at that lead (data/raw/national_mood/table.csv: 538's average to 2016, our estimator from
    2018; the newest lead on or before the date), no directional correction
  - simulation: gov2026.simulate (the shared national draw at the governors' slope, white non-college factor, t5 race errors);
    the 2026-only Hispanic / Asian loadings and the heating-oil term are left out
Scored on every D-v-R race of the cycle (polled or not): Brier and log loss of P(D wins), error of the race mean, the PIT of the
actual margin in the simulated distribution, 80 % / 50 % coverage and the CRPS of the margin.

Significance of a variant against the baseline (paired by race and date): the cycle-mean differences under an exact sign-flip
permutation over cycles (the honest unit when a cycle shares one national miss) and a race-cluster bootstrap stratified by cycle
(all dates of a race resampled together).

    python -m midterms.gov_backtest run NAME          # one variant (VARIANTS) -> data/cache/gov_bt/rows_NAME.csv
    python -m midterms.gov_backtest all               # every variant
    python -m midterms.gov_backtest report            # table of every variant against "base"
"""
import contextlib, itertools, sys
from pathlib import Path
import numpy as np, pandas as pd
from . import model as M, gov2026 as GV, data_prep as D

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "gov_bt"
RELEASE_LAG = 3
EDAY = {2006: "2006-11-07", 2008: "2008-11-04", 2010: "2010-11-02", 2012: "2012-11-06", 2014: "2014-11-04", 2016: "2016-11-08",
        2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
YEARS = tuple(EDAY)
ARCHIVE_FROM = 2018
DATES_ARCHIVE = ("09-01", "09-15", "10-01", "10-15", "11-01", "final")
DATES_RAW = ("10-01", "10-15", "11-01", "final")


def dates(year):
    out = []
    for d in (DATES_ARCHIVE if year >= ARCHIVE_FROM else DATES_RAW):
        out.append((pd.Timestamp(EDAY[year]) - pd.Timedelta(days=1)) if d == "final" else pd.Timestamp(f"{year}-{d}"))
    return sorted(set(out))         # 2010: the day before the election IS Nov 1 (counted twice until 2026-10-05 evening)


_E = None


def E_at(year, asof):
    """Generic-ballot average (D-R) at the newest tabulated lead on or before `asof` (leads 120/90/75/60/45/30/21/14/7/0 days)."""
    global _E
    if _E is None: _E = pd.read_csv(ROOT / "data" / "raw" / "national_mood" / "table.csv")
    lead = (pd.Timestamp(EDAY[year]) - pd.Timestamp(asof)).days
    t = _E[(_E.year == year) & (_E.lead >= lead)].sort_values("lead")
    return float(t.G.iloc[0])


def _sur(n): return GV._surname(n)


_POLLS = {}


def polls(year, H):
    """Poll frame for one cycle: seat, pollster (with (D)/(R) sponsor tag), end_date, dem, rep, margin, und, other, grade."""
    if year in _POLLS: return _POLLS[year]
    res = H[H.year == year].set_index("state")
    if year >= ARCHIVE_FROM:
        g = pd.read_csv(ROOT / "data" / "raw" / "538" / "governor_polls_historical.csv", low_memory=False)
        g = g[(g.cycle == year) & (g.stage.astype(str).str.lower() == "general") & (g.hypothetical.fillna(False) != True)].copy()
        if "ranked_choice_reallocated" in g: g = g[g.ranked_choice_reallocated.fillna(False) != True]
        g["seat"] = g.state.map(D._abbr); g = g[g.seat.isin(res.index)]
        g["p"] = g.party.map(D._party); g["sur"] = g.candidate_name.map(_sur)
        g["end_date"] = pd.to_datetime(g.end_date, format="%m/%d/%y", errors="coerce")
        rows = []
        for (pid, qid, seat), q in g.groupby(["poll_id", "question_id", "seat"]):
            dn, rn = _sur(res.dn[seat]), _sur(res.rn[seat])
            dq = q[(q.p == "D") & (q.sur == dn)]; rq = q[(q.p == "R") & (q.sur == rn)]
            if not len(dq) or not len(rq): continue
            tot = q.pct.sum(); d_, r_ = float(dq.pct.max()), float(rq.pct.max())
            rows.append({"poll_id": pid, "seat": seat, "pollster": str(q.pollster.iloc[0]) + {"DEM": " (D)", "REP": " (R)"}.get(q.partisan.iloc[0], ""),
                         "end_date": q.end_date.iloc[0], "dem": d_, "rep": r_, "other": max(tot - d_ - r_, 0.0), "und": max(100 - tot, 0.0)})
        P = pd.DataFrame(rows)
    else:
        r = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False)
        r = r[(r.cycle == year) & (r.type_simple == "Gov-G") & r.location.isin(res.index)].copy()
        r = r[r.cand1_party.isin(["DEM", "REP"]) & r.cand2_party.isin(["DEM", "REP"]) & (r.cand1_party != r.cand2_party)]
        dem = np.where(r.cand1_party == "DEM", r.cand1_pct, r.cand2_pct); rep = np.where(r.cand1_party == "REP", r.cand1_pct, r.cand2_pct)
        dnm = np.where(r.cand1_party == "DEM", r.cand1_name, r.cand2_name); rnm = np.where(r.cand1_party == "REP", r.cand1_name, r.cand2_name)
        P = pd.DataFrame({"poll_id": r.poll_id.values, "seat": r.location.values,
                          "pollster": (r.pollster.astype(str) + r.partisan.map({"DEM": " (D)", "REP": " (R)"}).fillna("")).values,
                          "end_date": pd.to_datetime(r.polldate).values, "dem": dem, "rep": rep, "dnm": dnm, "rnm": rnm})
        ok = [(_sur(a) == _sur(res.dn[s])) and (_sur(b) == _sur(res.rn[s])) for a, b, s in zip(P.dnm, P.rnm, P.seat)]
        P = P[ok].drop(columns=["dnm", "rnm"]); P["und"] = (100 - P.dem - P.rep).clip(lower=0); P["other"] = np.nan
    # versions of one survey (LV/RV, with and without minor candidates) -> one row, as the live race_polls does
    P = P.groupby(["poll_id", "seat"], as_index=False).agg(pollster=("pollster", "first"), end_date=("end_date", "max"), dem=("dem", "mean"),
                                                           rep=("rep", "mean"), und=("und", "mean"), other=("other", "mean"))
    P["margin"] = P.dem - P.rep; P["grade"] = 1.5
    P = M.collapse_versions(P.drop(columns="poll_id"), "seat")
    _POLLS[year] = P
    return P


_CAL = {}


def _cal(year):
    from .race_poll_calibration import calibration
    if year not in _CAL: _CAL[year] = calibration(before=year)
    return _CAL[year]


def run_date(year, asof, H, n=10000, seed=13):
    """One cycle at one date: per-race rows with the forecast and the outcome."""
    from . import senate2026 as SN
    M.CAL_OVERRIDE = _cal(year)
    PR = GV.Prior(H[H.year < year]); b3 = PR.slope
    te = H[H.year == year].copy(); E = E_at(year, asof); te["E"] = E
    mu_p, sd_p = PR.predict(te)
    pol = polls(year, H); pol = pol[pol.end_date <= pd.Timestamp(asof) - pd.Timedelta(days=RELEASE_LAG)]
    wnc = SN.state_loadings()["wnc_z"]
    rows = []
    for (i, r), mp, sp in zip(te.iterrows(), mu_p, sd_p):
        q = pol[pol.seat == r.state]
        qq = GV.prepare(q.assign(margin=q.margin + GV.PULL_K * (mp - b3 * E))) if len(q) else q
        mu, sd, pm, ne = GV.blend(mp, sp, qq, asof, b3)
        npol = int(((qq.end_date > pd.Timestamp(asof) - pd.Timedelta(days=M.POLL_WINDOW)).sum()) if len(qq) else 0)
        rows.append({"year": year, "asof": pd.Timestamp(asof).date().isoformat(), "state": r.state, "race": f"{year}{r.state}", "E": E, "mu_prior": mp,
                     "prior_sd": sp, "pm": pm, "ne": ne, "npolls": npol, "mu": mu, "sd": sd, "wnc_z": wnc.get(r.state, 0.0), "result": r.margin,
                     "inc": r.inc, "oth_max": r.oth_max})
    S = pd.DataFrame(rows)
    mg = GV.simulate(S, b3, n=n, seed=seed)
    y = (S.result > 0).values
    S["p"] = (mg > 0).mean(0); S["y"] = y.astype(float)
    S["pit"] = (mg <= S.result.values[None, :]).mean(0)
    q10, q25, q75, q90 = np.percentile(mg, [10, 25, 75, 90], axis=0)
    S["in80"] = ((S.result >= q10) & (S.result <= q90)).astype(float); S["in50"] = ((S.result >= q25) & (S.result <= q75)).astype(float)
    S["sim_mean"] = mg.mean(0)
    # CRPS from the simulated sample: E|X - y| - 0.5 E|X - X'| (sorted-sample identity)
    xs = np.sort(mg, axis=0); k = np.arange(1, len(xs) + 1)[:, None]
    S["crps"] = np.abs(mg - S.result.values[None, :]).mean(0) - (((2 * k - len(xs) - 1) * xs).sum(0) / len(xs) ** 2)
    pc = S.p.clip(1e-3, 1 - 1e-3)
    S["ll"] = -np.where(y, np.log(pc), np.log(1 - pc)); S["brier"] = (S.p - S.y) ** 2
    S["err"] = S.mu - S.result
    S["d_dates"] = 1.0
    return S


# ---- variants: name -> settings of gov2026's switches (and model globals) for that run. "base" = the live model before the review;
# "lean_t+succ+qual" = the prior adopted on 2026-10-05 (gov2026.PRIOR_SPEC).
def _set(**kw):
    kw.setdefault("PRIOR_SPEC", {})          # every variant is stated against the PRE-review prior unless it sets its own
    @contextlib.contextmanager
    def ctx():
        old = {}
        for k, v in kw.items():
            mod, name = (M, k[2:]) if k.startswith("M_") else (GV, k)
            old[(mod, name)] = getattr(mod, name); setattr(mod, name, v)
        try: yield
        finally:
            for (mod, name), v in old.items(): setattr(mod, name, v)
    return ctx


VARIANTS = {
    "base": _set(),
    # data fix
    "inc_fix": _set(INC_FIX=True),
    # (a) prior structure
    "lean_t": _set(PRIOR_SPEC={"lean_t": True}),
    "half12": _set(PRIOR_SPEC={"half": 12}),
    "half8": _set(PRIOR_SPEC={"half": 8}),
    "excl_third": _set(PRIOR_SPEC={"excl_third": 15}),
    # (b) candidate strength: the same state's previous governor race (the incumbent's own, or the party's)
    "prev_all": _set(PRIOR_SPEC={"prev": "all"}),
    "prev_split": _set(PRIOR_SPEC={"prev": "split"}),
    "prev_inc": _set(PRIOR_SPEC={"prev": "inc"}),
    # (c) heteroscedastic prior sd, governor poll errors
    "het_inc": _set(PRIOR_SPEC={"het": "inc"}),
    "het_lean": _set(PRIOR_SPEC={"het": "lean"}),
    "prior_sd_x0.8": _set(PRIOR_SD_K=0.8),
    "prior_sd_x1.25": _set(PRIOR_SD_K=1.25),
    # (d) shared shock / race spread
    "shared_x0.5": _set(SHARED_K=0.5),
    "shared_x1.5": _set(SHARED_K=1.5),
    "race_sd_x1.15": _set(RACE_SD_K=1.15),
    "race_sd_x0.9": _set(RACE_SD_K=0.9),
    # (e) polls
    "und_x2": _set(UND_PARAMS=(M.UND_FREE, 2 * M.UND_SLOPE, M.UND_CAP)),
    "und_free8": _set(UND_PARAMS=(8.0, M.UND_SLOPE, M.UND_CAP)),
    "und_off": _set(UND_PARAMS=(M.UND_FREE, 0.0, M.UND_CAP)),
    "third_x1.5": _set(THIRD_MULT=1.5),
    "age42": _set(AGE_HALF=42.0),
    "age90": _set(AGE_HALF=90.0),
    "poll_gov": _set(GOV_POLL_FIT=True),
    "succ": _set(PRIOR_SPEC={"succ": True}),
    "lean_t+inc_fix": _set(PRIOR_SPEC={"lean_t": True}, INC_FIX=True),
    "lean_t+succ": _set(PRIOR_SPEC={"lean_t": True, "succ": True}),
    "pull_wf": None,           # walk-forward PULL_K (run() sets it per cycle)
    "qual": _set(PRIOR_SPEC={"qual": "all"}),
    "qual_open": _set(PRIOR_SPEC={"qual": "open"}),
    "lean_t+succ+qual": _set(PRIOR_SPEC={"lean_t": True, "succ": True, "qual": "all"}),
    "adopted+und_trail": _set(PRIOR_SPEC={"lean_t": True, "succ": True, "qual": "all"}),     # undecided allocation (2026-10-05 follow-up)
    "lean_t+succ+qual_open": _set(PRIOR_SPEC={"lean_t": True, "succ": True, "qual": "open"}),
    "lean_t+succ+race_sd_x1.15": _set(PRIOR_SPEC={"lean_t": True, "succ": True}, RACE_SD_K=1.15),
    "lean_t+succ+race_sd_x0.9": _set(PRIOR_SPEC={"lean_t": True, "succ": True}, RACE_SD_K=0.9),
    "lean_t+succ+shared_x1.5": _set(PRIOR_SPEC={"lean_t": True, "succ": True}, SHARED_K=1.5),
    "lean_t+succ+prior_sd_x1.25": _set(PRIOR_SPEC={"lean_t": True, "succ": True}, PRIOR_SD_K=1.25),
}


PULL_WF = False
UND_WF = False          # variants named *und_trail*: model.UND_TRAIL_K = und_trail_fit(before=cycle), walk-forward


def pull_fit(before):
    """Walk-forward PULL_K: regress (result - poll average) on the prior's non-national part (prior - b3 E) with a fixed effect per
    cycle and date, on the BASE run's polled rows of cycles before `before` (0 when there are none)."""
    f = OUT / "rows_base.csv"
    if not f.exists(): return 0.0
    R = pd.read_csv(f); R = R[(R.year < before) & (R.npolls > 0) & R.pm.notna()]
    if R.year.nunique() < 1: return 0.0
    b3 = {y: GV.Prior(GV.history()[lambda h: h.year < y]).slope for y in R.year.unique()}
    x = R.mu_prior - R.year.map(b3) * R.E; fe = pd.get_dummies(R.year.astype(str) + R["asof"].astype(str), dtype=float).values
    b = np.linalg.lstsq(np.column_stack([fe, x]), (R.result - R.pm).values, rcond=None)[0]
    return float(b[-1])


def run(name, ctx=None, years=YEARS, n=10000, history=None):
    OUT.mkdir(parents=True, exist_ok=True)
    global PULL_WF, UND_WF
    PULL_WF = name.startswith("pull_wf"); UND_WF = "und_trail" in name
    ctx = ctx or VARIANTS[name] or _set()
    out = []
    with ctx():
        old24 = GV.USE_2024; GV.USE_2024 = True               # 2024 is a test cycle (and, for 2026, a training one)
        H = history if history is not None else GV.history()
        GV.USE_2024 = old24
        _POLLS.clear()
        for y in years:
            GV.POLL_FIT_BEFORE = y
            if PULL_WF: GV.PULL_K = pull_fit(y)
            M.UND_TRAIL_K = M.und_trail_fit(before=y) if UND_WF else None
            for d in dates(y):
                out.append(run_date(y, d, H, n=n))
    M.CAL_OVERRIDE = None; GV.POLL_FIT_BEFORE = None; GV.PULL_K = 0.0; PULL_WF = False; UND_WF = False; M.UND_TRAIL_K = None
    R = pd.concat(out, ignore_index=True); R["variant"] = name
    R.to_csv(OUT / f"rows_{name}.csv", index=False)
    return R


def summary(R):
    comp = R.mu_prior.abs() < 1e9
    return {"race_dates": len(R), "races": R.race.nunique(), "cycles": R.year.nunique(), "polled": int((R.npolls > 0).sum()),
            "brier": R.brier.mean(), "logloss": R.ll.mean(), "mae": R.err.abs().mean(), "rmse": float(np.sqrt((R.err ** 2).mean())),
            "crps": R.crps.mean(), "cov80": R.in80.mean(), "cov50": R.in50.mean(), "pit_ks": _ks(R.pit.values)}


def _ks(u):
    u = np.sort(u); n = len(u); i = np.arange(1, n + 1)
    return float(max((i / n - u).max(), (u - (i - 1) / n).max()))


def paired(A, B, col, n_boot=4000, seed=7):
    """B - A on `col`, paired by race and date. Returns mean diff, cycles better, exact sign-flip p over cycle means (two-sided),
    race-cluster bootstrap 95 % interval and two-sided p (stratified by cycle)."""
    X = A[["race", "asof", "year", col]].merge(B[["race", "asof", col]], on=["race", "asof"], suffixes=("_a", "_b"))
    X["d"] = X[col + "_b"] - X[col + "_a"]
    cyc = X.groupby("year").d.mean(); w = X.groupby("year").size()
    obs = float(np.average(cyc, weights=w))
    flips = np.array(list(itertools.product([1, -1], repeat=len(cyc))))
    perm = (flips * cyc.values[None, :] * w.values[None, :]).sum(1) / w.sum()
    p_perm = float((np.abs(perm) >= abs(obs) - 1e-12).mean())
    rng = np.random.default_rng(seed)
    per = X.groupby(["year", "race"]).d.agg(["sum", "size"]).reset_index()
    groups = [g[["sum", "size"]].values for _, g in per.groupby("year")]
    bs = np.empty(n_boot)
    for i in range(n_boot):
        tot = np.zeros(2)
        for g in groups: tot += g[rng.integers(0, len(g), len(g))].sum(0)
        bs[i] = tot[0] / tot[1]
    lo, hi = np.percentile(bs, [2.5, 97.5]); p_boot = float(min(1.0, 2 * min((bs <= 0).mean(), (bs >= 0).mean())))
    return {"diff": obs, "cyc_better": int((cyc < 0).sum()), "cycles": len(cyc), "p_perm": p_perm, "lo": lo, "hi": hi, "p_boot": p_boot}


def report(names=None, base="base"):
    A = pd.read_csv(OUT / f"rows_{base}.csv")
    names = names or [p.stem[5:] for p in sorted(OUT.glob("rows_*.csv"))]
    rows = []
    for nm in names:
        f = OUT / f"rows_{nm}.csv"
        if not f.exists(): continue
        B = pd.read_csv(f); s = summary(B); r = {"variant": nm, **{k: s[k] for k in ("race_dates", "races", "logloss", "brier", "mae", "rmse", "crps", "cov80", "cov50")}}
        if nm != base:
            for col, lab in (("ll", "LL"), ("brier", "Br"), ("crps", "CRPS")):
                p = paired(A, B, col); r[f"d{lab}"] = p["diff"]; r[f"{lab}_cyc"] = f"{p['cyc_better']}/{p['cycles']}"; r[f"{lab}_p_perm"] = p["p_perm"]; r[f"{lab}_p_boot"] = p["p_boot"]
            B2 = B.assign(ae=B.err.abs()); A2 = A.assign(ae=A.err.abs()); p = paired(A2, B2, "ae")
            r["dMAE"] = p["diff"]; r["MAE_p_boot"] = p["p_boot"]; r["MAE_p_perm"] = p["p_perm"]
        rows.append(r)
    T = pd.DataFrame(rows)
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print(T.round(4).to_string(index=False))
    return T


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "run":
        for nm in sys.argv[2:]: R = run(nm); print(nm, {k: round(v, 4) if isinstance(v, float) else v for k, v in summary(R).items()})
    elif cmd == "all":
        for nm in VARIANTS: R = run(nm); print(nm, {k: round(v, 4) if isinstance(v, float) else v for k, v in summary(R).items()}, flush=True)
        report()
    else: report(sys.argv[2:] or None)
