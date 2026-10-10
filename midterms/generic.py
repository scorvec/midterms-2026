"""Our own generic-ballot and approval read, from the polls rather than the aggregators.

Model (per series): y_i = trend(t_i) + house[pollster_i] + pop[population_i] + partisan_i * s + e_i
  trend  : piecewise-linear in time with knots every 14 days, second-difference penalty (Whittaker)
  house  : one effect per pollster, ridge-shrunk toward zero (sum-to-zero enforced through the penalty
           and by centring on the weighted pollster mean), so a change in WHO is polling does not move
           the trend
  pop    : likely / registered / adult offsets (likely voters are the reference)
  weights: sqrt(sample size), capped; internal and partisan-sponsored polls down-weighted x0.5
Everything is in Democratic-minus-Republican points (generic) or approve-minus-disapprove (approval).
Calibration on the 538 archives: the same estimator run on 2018 and 2022 generic polls, evaluated at
election day against the actual national House two-party margin (+8.46, -3.30 from the FEC totals),
gives the bias and error to carry into the seat model.
    python -m midterms.generic
"""
from __future__ import annotations
import json, re, numpy as np, pandas as pd, datetime as dt
from pathlib import Path
from . import data_prep as D

ROOT = Path(__file__).resolve().parents[1]; RAW = ROOT / "data" / "raw" / "votehub"; CACHE = ROOT / "data" / "cache"
# LAMBDA_HOUSE 4 -> 1 (user decision 2026-10-09, deep review): less ridge shrinkage of the pollster house effects. 538's generic archive
# 2018-24 x 7 dates, live fit, 3-day release lag: weighted RMSE predicting the NEXT 24 days' polls (free of the polling-miss level)
# 2.623 -> 2.551, better in 23/28 cases and 4/4 years (year-block t 1.95, 3 df - not significant on its own; most of the gain is 2018);
# margin vs the House vote 3.61 -> 3.49. 16 was worse in every year. Live trend 9.07 -> 9.29 on the Oct 9 polls.
KNOT_DAYS, LAMBDA_TREND, LAMBDA_HOUSE = 14, 30.0, 1.0


# Checked corrections to VoteHub records: (pollster, start, end, population) -> fields. The raw file is re-downloaded daily, so
# fixes live here. 2026-09-30: The Argument/Verasight's LV generic ballot was listed ending 9/22 at 55-45; the article
# (theargumentmag.com "The least popular war since Vietnam") gives field dates Sept 16-21 and LV D+9 (54.7-45.3 on
# pollresults.org). With the wrong end date it was not grouped with the RV version (same survey) and counted as a second poll.
VOTEHUB_FIX = {("The Argument/Verasight", "2026-09-16", "2026-09-22", "lv"): {"end_date": "2026-09-21", "Dem": 54.7, "Rep": 45.3}}


def load_votehub(kind: str) -> pd.DataFrame:
    d = json.load(open(RAW / f"{kind}.json")); rows = []
    for p in d:
        fx = VOTEHUB_FIX.get((p.get("pollster"), p.get("start_date"), p.get("end_date"), (p.get("population") or "").lower()))
        if fx:
            p = dict(p, end_date=fx.get("end_date", p["end_date"]),
                     answers=[dict(x, pct=fx.get(x["choice"], x["pct"])) for x in p.get("answers", [])])
        a = {x["choice"].lower(): x["pct"] for x in p.get("answers", [])}
        if kind == "generic-ballot":
            dem, rep = a.get("dem"), a.get("rep")
            if dem is None or rep is None: continue
            y, dv, rv = dem - rep, dem, rep
        else:
            ap, dis = a.get("approve"), a.get("disapprove")
            if ap is None or dis is None: continue
            y, dv, rv = ap - dis, ap, dis
        rows.append({"id": p["id"], "pollster": p["pollster"], "end_date": pd.Timestamp(p["end_date"]), "start_date": pd.Timestamp(p["start_date"]), "n": p.get("sample_size"), "pop": (p.get("population") or "rv").lower(),
                     "internal": bool(p.get("internal")), "partisan": p.get("partisan"), "sponsors": ",".join(s if isinstance(s, str) else str(s) for s in (p.get("sponsors") or [])), "subject": p.get("subject"), "y": y, "dem": dv, "rep": rv, "und": 100 - dv - rv})
    return pd.DataFrame(rows).sort_values("end_date").reset_index(drop=True)


# Field-period handling (2026-09-23, user: "how do we take into account the dates the polls were surveyed over?"):
#  1. one survey reported for several populations (adults + RV + LV; 65 surveys were 140 rows in 2026) is ONE poll: keep the
#     LV version, else RV, else V, else A (the fit's population offsets are anchored on LV); same-population variants keep
#     the largest n. Matched on pollster + start + end date where the start is known, else pollster + end date (538 archive);
#  2. each poll is placed at the MIDPOINT of its field period (end date when the start is unknown);
#  3. a tracker release that overlaps the same pollster's previous release is down-weighted by the shared share of its field
#     days (floor 0.25) - its respondents are partly the same people.
FIELD_PREP = True
VARIANT_MODE = "split"      # 2018-24 at 6 leads: old RMSE 4.10 (midterms 4.00), keep-LV-only 4.15 (4.12), split 3.97 (3.65)
POP_RANK = {"lv": 0, "rv": 1, "v": 2, "a": 3}
# Pollster QUALITY vs partisan tags (2026-09-29, user: "Big Data is a very low rated pollster" ... "We need to make sure our
# quality screen is good"). Ratings: midterms/pollster_quality.py (538 archived ratings by vintage, pollresults.org grades).
# Backtest (538 archive 2018/20/22/24 x the 6 DATES, the POLLSTER_POP_LAMBDA harness; ratings as known BEFORE each election):
#   RMSE vs the House margin    off 3.924 | Rasmussen+Big Data tagged REP 4.049 (3/24 cases better, year-block p 0.32)
#   exclude D/F (score <= 1.2) 4.059 (2/24, p 0.41) | exclude bottom decile 3.964 | Big Data excluded 3.938 (0/5 changed better)
#   weight (score/3)**g: g 1 3.922, 2 3.838, 4 3.687 (LOYO picks 4; year-block p 0.41; 2024 WORSE 4.21 -> 4.62; the gain is a
#   shift toward D in 2018) and the bias-free next-3-weeks poll prediction gets WORSE at every g (2.506 -> 2.59 / 2.65 / 2.72)
#   + low-rated house effects shrunk less (ridge x weight): 3.78-3.94, same story.
# Nothing significant -> no quality weighting, no exclusion, and the partisan tags removed (the fitted house effect already
# carries a pollster's lean: Big Data +1.2, Rasmussen -0.25 today). Switches kept for the user:
KNOWN_LEAN = {}                 # pollster key -> partisan tag forced in merged(); was {"rasmussen": "REP", "big data poll": "REP"}
QUALITY_GAMMA = None            # poll weight x (score/3)**gamma (unrated = 1.5); None = off
QUALITY_EXCLUDE_BELOW = None    # drop polls whose pollster scores <= this (1.2 = D+ or worse; Big Data = F 0.5); None = off


# Sponsored custom polls by a firm whose main product is its own tracker get their OWN pollster name (2026-10-01, user: "give
# it its own name. This is misleading"): Morning Consult's Deseret News/Hinckley national sample (9/10-14, 2,102 RV, D+3) was
# filed under "morning consult" and the overlap/variant logic treated it as a repeat of MC's 9/11-13 tracker reading (Deseret
# w 1.45, tracker LV D+6 w 0.19). Rule: a SERIES_FIRMS poll with a named sponsor -> "<firm>/<sponsor short>". A recurring
# sponsor series keeps one name across its polls (Deseret: 4 approval + 1 generic), so it carries its own house effect.
SERIES_FIRMS = {"morning consult"}
_SPONSOR_SHORT = {"deseret": "deseret", "harvard": "harvard caps", "strength in numbers": "strength in numbers", "npr": "npr/pbs"}


def sponsor_class(sp) -> str:
    """Normalised first sponsor ('' = none/unknown): 'Deseret News,University of Utah Hinckley ...' -> 'deseret'."""
    if not isinstance(sp, str) or not sp.strip() or sp.strip().lower() == "nan": return ""
    first = re.sub(r"\s+", " ", sp.split(",")[0].strip().lower())
    for k, v in _SPONSOR_SHORT.items():
        if k in first: return v
    return re.sub(r"[^a-z0-9 &]", "", first).strip()


def series_key(key: str, sponsors) -> str:
    c = sponsor_class(sponsors)
    return f"{key}/{c}" if key in SERIES_FIRMS and c else key


# Overlap / variant logic keyed on the SERIES, not just the pollster name (2026-10-01): rows are versions of one survey only
# when pollster, field dates AND sponsor class agree (a known, different sponsor = a different sample); the tracker-overlap
# discount applies between DIFFERENT surveys of the same pollster with compatible sponsors (equal, or either unknown) - never
# between versions of one survey (the second-sorted version used to take max(0.25, ...) on top of its 1/k variant share:
# Harvard-Harris 9/28 RV 0.185 vs LV 0.387) and never between two sponsors' samples (Forbes/HarrisX vs Harvard CAPS 5/29-31).
FIELD_SERIES = True


def field_prepare(df):
    if not FIELD_SERIES or "start_date" not in df or not df["start_date"].notna().any(): return _field_prepare_old(df)
    d = df.copy()
    d["_r"] = d["pop"].map(POP_RANK).fillna(4); d["_n"] = -d["n"].fillna(0).astype(float)
    d["start_date"] = d["start_date"].fillna(d["end_date"])
    d["_s"] = d["sponsors"].map(sponsor_class) if "sponsors" in d else ""
    # survey id: pollster + dates, split by sponsor class only when two KNOWN classes differ (unknown joins the majority known)
    keys = ["pollster", "start_date", "end_date"]; d["sgrp"] = ""
    for _, idx in d.groupby(keys).groups.items():
        cl = d.loc[idx, "_s"]; known = cl[cl != ""]
        if known.nunique() > 1: d.loc[idx, "sgrp"] = cl.where(cl != "", known.mode().iat[0])
    skey = keys + ["sgrp"]
    if VARIANT_MODE == "keep_lv":
        d = d.sort_values(["_r", "_n"]).drop_duplicates(skey, keep="first"); d["vw"] = 1.0
    else:
        d["vw"] = 1.0 / d.groupby(skey)["pollster"].transform("size")
    d["mid"] = (d["start_date"] + (d["end_date"] - d["start_date"]) / 2).dt.normalize()
    surv = d.groupby(skey, as_index=False).agg(cls=("_s", lambda x: next((c for c in x if c), "")))
    surv = surv.sort_values(["pollster", "start_date", "end_date"]).reset_index(drop=True); ov = {}
    for p, grp in surv.groupby("pollster", sort=False):
        seen = []
        for r in grp.itertuples():
            pe = max((e for c, e in seen if (c == r.cls or not c or not r.cls)), default=None)
            w = 1.0
            if pe is not None and r.start_date <= pe:
                shared = (min(pe, r.end_date) - r.start_date).days + 1; field = (r.end_date - r.start_date).days + 1
                w = max(0.25, 1 - shared / max(field, 1))
            ov[(p, r.start_date, r.end_date, r.sgrp)] = w; seen.append((r.cls, r.end_date))
    d["ovw"] = [ov[k] for k in zip(d["pollster"], d["start_date"], d["end_date"], d["sgrp"])]
    return d.drop(columns=["_r", "_n", "_s", "sgrp"]).sort_values("end_date").reset_index(drop=True)


def _field_prepare_old(df):
    d = df.copy()
    d["_r"] = d["pop"].map(POP_RANK).fillna(4); d["_n"] = -d["n"].fillna(0).astype(float)
    keys = ["pollster", "start_date", "end_date"] if "start_date" in d and d["start_date"].notna().any() else ["pollster", "end_date"]
    if "start_date" in d: d["start_date"] = d["start_date"].fillna(d["end_date"])
    if VARIANT_MODE == "keep_lv":
        d = d.sort_values(["_r", "_n"]).drop_duplicates(keys, keep="first"); d["vw"] = 1.0
    else:                                   # "split": keep every version (the within-survey LV/RV gap informs the population
        d["vw"] = 1.0 / d.groupby(keys)["pollster"].transform("size")   # offsets) but the survey's weight sums to one poll
    if "start_date" in d:
        d["mid"] = d["start_date"] + (d["end_date"] - d["start_date"]) / 2
        d = d.sort_values(["pollster", "start_date"]); ovw = np.ones(len(d)); prev_end = {}
        for i, (p, s, e) in enumerate(zip(d["pollster"], d["start_date"], d["end_date"])):
            pe = prev_end.get(p)
            if pe is not None and s <= pe:
                shared = (min(pe, e) - s).days + 1; field = (e - s).days + 1
                ovw[i] = max(0.25, 1 - shared / max(field, 1))
            prev_end[p] = max(e, pe) if pe is not None else e
        d["ovw"] = ovw
        d["mid"] = d["mid"].dt.normalize()
    return d.drop(columns=["_r", "_n"]).sort_values("end_date").reset_index(drop=True)


ROBUST_NU = None   # Student-t iteratively reweighted fit (2026-09-22); None = Gaussian
# Pollster-specific population offsets (2026-09-29): each pollster that publishes several populations of the SAME survey
# (the "split" variants) gets its own RV-LV / A-LV / V-LV offset on top of the global one, ridge-shrunk toward the global
# offset with this penalty (None = off: one global offset per population). Only those (pollster, population) cells get a
# term - for a pollster that only ever publishes one population the interaction is not identified (its house effect
# already absorbs it).
# TESTED 2026-09-29 and left OFF (not significant). Backtest = backtest_all's env path (538 archive, 2018/20/22/24 x the 6
# DATES, 3-day release lag), trend minus the actual House margin. RMSE off 3.924 (midterms 3.489); lambda 100 3.901, 30 3.869,
# 10 3.830, 3 3.800 (3.309), 1 3.792, 0.3 3.794. But the gain is a small shift toward R (20/24 cases at lambda 3, mean -0.12)
# in years where the polls ran D, the year-block paired t is 1.8 on 3 df (p ~0.17), 2018 gets WORSE at lambda <= 1, and the
# bias-free test - predicting the next 3 weeks' polls from the fit - is a wash (weighted RMSE 2.506 off vs 2.501-2.524,
# better in only 7-9 of 24 cases). Live effect is nil: trend 8.83 off vs 8.80-8.85 at lambda 10-1; YouGov's own RV-LV
# comes out -0.3 (lambda 10) to -1.4 (lambda 1) - its many RV-only weeks swamp the two paired Sep surveys (gaps -5, -7).
POLLSTER_POP_LAMBDA = None
# (A) WITHIN-SURVEY population gap (2026-09-29): each pollster's RV-LV / A-LV gap estimated ONLY from its surveys published
# for several populations (same pollster + field dates), as the within-survey difference net of the global offset, ridge-
# shrunk toward the global offset (penalty in units of 0.5 per LV/RV pair); RV-only surveys do not enter it. The gap is then
# subtracted from ALL that pollster's rows of that population and the model refitted. None = off.
# TESTED 2026-09-29 and left OFF (worse): same harness as above, RMSE off 3.924 (midterms 3.489) vs lambda 30 3.920, 10 3.929,
# 3 3.953, 1 3.979, 0.3 3.998, 0.1 4.007 (midterms 3.48-3.76); year-block paired t +0.06 to -0.79 (p >= 0.49), 2018 worse at
# every lambda <= 10 (09-01: -4.01 -> -6.38 at 0.1); next-3-weeks poll RMSE 2.506 off vs 2.505-2.536. YouGov's within-survey
# gap: RV-LV total -0.1 (lambda 10), -1.7 (1), -4.6 (0.1). Live trend 8.83 -> 8.82 / 8.88 / 9.05. Re-run of keep-LV-only
# (VARIANT_MODE "keep_lv") on today's inputs: RMSE 4.064 (midterms 3.912), next-3-weeks 2.723, live trend 9.58 - worse too.
WITHIN_GAP_LAMBDA = None
# Slowly DRIFTING house effects (2026-09-30, user: same-pollster deltas -> "Sure do that"): each pollster's house effect is
# h_p + d_p(t), d_p piecewise-linear on knots every HOUSE_DRIFT_KNOT_DAYS from the pollster's first poll to today, penalised
# lambda x (first differences)^2 = a random walk (ORDER 1), or second differences + 0.05 x first (ORDER 2, a smooth spline),
# and pinned to a poll-weighted mean of zero so h_p keeps its meaning (the pollster's average effect, same ridge as before).
# lambda -> infinity recovers the constant house effect exactly; None = off. The house effect used for "now" (centring, the
# next poll) is h_p + d_p(today): the random walk's forecast holds the last level.
# TESTED 2026-09-30 and left OFF (research/house_drift/, harness of research/pollster_quality/gen_bt.py): margin RMSE off 3.924
# -> RW lambda 1000 3.919, 100 3.900, 30 3.879, 10 3.851, 3 3.809, 1 3.785 (year-block p 0.02, 4/4 years), 0.3 3.817; but the
# bias-free next-3-weeks poll RMSE is 2.506 off vs 2.492-2.504 at lambda 30-1000 (10-12/24 cases, p >= 0.47) and WORSE where the
# margin gains (2.513 at 3, 2.577 at 1, 2.734 at 0.3); LOYO lambda by next polls -> margin gain +0.04 only. Smooth (ORDER 2)
# is worse still at low lambda (next polls 2.62-2.90). Pollsters with a new population / a drifting recent residual: better
# next polls (e.g. |dev| >= 2: 4.08 -> 3.94 at lambda 10) but a few dozen polls. Live trend 8.76 off -> 8.58-8.67.
HOUSE_DRIFT_LAMBDA = None
HOUSE_DRIFT_KNOT_DAYS = 28
HOUSE_DRIFT_ORDER = 1


def _within_gaps(df, gpop, lam, ref_pop="lv"):
    keys = ["pollster", "start_date", "end_date"] if "start_date" in df and df["start_date"].notna().any() else ["pollster", "end_date"]
    m = df[df.groupby(keys)["pop"].transform("nunique") > 1]; out = {}
    for p, mp in m.groupby("pollster"):
        cells = sorted(set(mp["pop"]) - {ref_pop}); ix = {c: i for i, c in enumerate(cells)}; Xs, ts = [], []
        for _, grp in mp.groupby(keys):
            r = grp["y"].values - np.array([gpop.get(q, 0.0) for q in grp["pop"]])
            E = np.zeros((len(grp), len(cells)))
            for i, q in enumerate(grp["pop"]):
                if q in ix: E[i, ix[q]] = 1.0
            Xs.append(E - E.mean(0)); ts.append(r - r.mean())
        X, t = np.vstack(Xs), np.concatenate(ts)
        d = np.linalg.solve(X.T @ X + lam * np.eye(len(cells)), X.T @ t)
        out.update({(p, c): float(v) for c, v in zip(cells, d)})
    return out


def _pop_cells(df, pops):
    """(pollster, population) cells identified by within-survey variants: pop is non-reference and the pollster has at
    least one survey (same pollster + field dates) published for two or more populations including it."""
    keys = ["pollster", "start_date", "end_date"] if "start_date" in df and df["start_date"].notna().any() else ["pollster", "end_date"]
    g = df.groupby(keys)["pop"].transform("nunique"); m = df[(g > 1) & df["pop"].isin(pops)]
    return sorted(set(zip(m["pollster"], m["pop"])))


# Lead-dependent population weight (2026-10-01, user: "It might be time to start discounting non-LV polls more now as we get closer
# to the election"). POP_LEAD = None (off) or dict(kmin, form "linear"|"logistic", start days, a_pow, lv_only_days, lv_min):
# RV (and V) rows x k(d), adults x k(d)**a_pow (a_pow >= 1: adults discounted at least as much), d = days from the fit date to
# ELECTION_DATE; k = 1 at d >= start, kmin on election day. lv_only_days: within that many days, if >= lv_min LV polls ended in
# the last 30 days, RV/A rows get weight 1e-3 (LV-only; the fitted offsets survive through the ridge). Backtest:
# research/pop_lead/pl_bt.py. The fitted population offsets are unchanged in form.
POP_LEAD = None
ELECTION_DATE = pd.Timestamp("2026-11-03")


def pop_lead_k(d, pl):
    st, km = float(pl.get("start", 90)), float(pl["kmin"])
    if d >= st: return 1.0
    x = max(d, 0.0) / st
    if pl.get("form", "linear") == "logistic":
        s = lambda z: 1 / (1 + np.exp(-10 * (z - 0.5)))
        return km + (1 - km) * (s(x) - s(0)) / (s(1) - s(0))
    return km + (1 - km) * x


def pop_lead_mult(df, t1):
    pl = POP_LEAD; d = (ELECTION_DATE - pd.Timestamp(t1)).days; pop = df["pop"].astype(str).values
    m = np.ones(len(df))
    lvd = pl.get("lv_only_days")
    if lvd is not None and d <= lvd:
        nlv = int(((df["pop"] == "lv") & (df["end_date"] >= pd.Timestamp(t1) - pd.Timedelta(days=30))).sum())
        if nlv >= pl.get("lv_min", 10):
            m[pop != "lv"] = 1e-3; return m
    k = pop_lead_k(d, pl)
    m[(pop == "rv") | (pop == "v")] = k; m[pop == "a"] = k ** float(pl.get("a_pow", 1.0))
    return m


def fit(df: pd.DataFrame, t0=None, t1=None, knot_days=KNOT_DAYS, lam_t=LAMBDA_TREND, lam_h=LAMBDA_HOUSE, ref_pop="lv", robust_nu="default", lam_pp="default", lam_wg="default", lam_hd="default", hd_order="default", hd_knot="default"):
    """Joint fit → dict(trend: DataFrame(date, value), house: Series, pop: dict, resid_sd, n)."""
    wl = WITHIN_GAP_LAMBDA if lam_wg == "default" else lam_wg; gaps = {}
    if wl is not None:                        # (A) two-stage: global offsets from the plain fit, within-survey gaps, refit
        F0 = fit(df, t0, t1, knot_days, lam_t, lam_h, ref_pop, robust_nu, lam_pp, lam_wg=None, lam_hd=lam_hd, hd_order=hd_order, hd_knot=hd_knot)
        dp = field_prepare(df) if FIELD_PREP else df.copy(); gaps = _within_gaps(dp, F0["pop"], wl, ref_pop)
        df = df.copy(); df["y"] = df["y"] - np.array([gaps.get((a, b), 0.0) for a, b in zip(df["pollster"], df["pop"])])
    df = field_prepare(df) if FIELD_PREP else df.copy()
    tcol = "mid" if "mid" in df else "end_date"
    t0 = pd.Timestamp(t0 or df[tcol].min()); t1 = pd.Timestamp(t1 or df["end_date"].max())
    t = (df[tcol] - t0).dt.days.values.astype(float); T = (t1 - t0).days
    knots = np.arange(0, T + knot_days, knot_days); K = len(knots)
    # hat-function basis
    B = np.zeros((len(df), K))
    for k in range(K):
        lo, mid, hi = (knots[k - 1] if k > 0 else knots[0] - knot_days), knots[k], (knots[k + 1] if k + 1 < K else knots[-1] + knot_days)
        B[:, k] = np.clip(np.where(t <= mid, (t - lo) / (mid - lo), (hi - t) / (hi - mid)), 0, 1)
    pollsters = sorted(df["pollster"].unique()); pi = {p: i for i, p in enumerate(pollsters)}; P = len(pollsters)
    H = np.zeros((len(df), P)); H[np.arange(len(df)), df["pollster"].map(pi).values] = 1.0
    pops = [p for p in ("rv", "a", "v") if p != ref_pop]; Q = np.column_stack([(df["pop"] == p).astype(float) for p in pops]) if pops else np.zeros((len(df), 0))
    lpp = POLLSTER_POP_LAMBDA if lam_pp == "default" else lam_pp
    cells = _pop_cells(df, pops) if lpp is not None else []
    IQ = np.column_stack([((df["pollster"] == c[0]) & (df["pop"] == c[1])).astype(float).values for c in cells]) if cells else np.zeros((len(df), 0)); NI = len(cells)
    part = ((df["internal"]) | df["partisan"].notna()).astype(float).values[:, None]
    lhd = HOUSE_DRIFT_LAMBDA if lam_hd == "default" else lam_hd
    kd = HOUSE_DRIFT_KNOT_DAYS if hd_knot == "default" else hd_knot; hord = HOUSE_DRIFT_ORDER if hd_order == "default" else hd_order
    dk = np.arange(0, T + kd, kd); hatd = lambda tt: np.clip(1 - np.abs(np.asarray(tt, float)[..., None] - dk) / kd, 0, 1)
    dcols, dblocks = [], []                  # (pollster index, drift knot index) per column; column ranges per pollster
    if lhd is not None:
        Bd = hatd(t); pidx = df["pollster"].map(pi).values
        for p_ in range(P):
            m = pidx == p_; k0 = int(np.floor(t[m].min() / kd)) if m.any() else len(dk)
            if len(dk) - k0 < 2 or m.sum() < 2: continue
            dblocks.append((p_, len(dcols), len(dcols) + len(dk) - k0)); dcols += [(p_, k) for k in range(k0, len(dk))]
        DR = np.zeros((len(df), len(dcols)))
        for c, (p_, k) in enumerate(dcols): DR[:, c] = Bd[:, k] * (pidx == p_)
    else: DR = np.zeros((len(df), 0))
    ND = DR.shape[1]
    X = np.hstack([B, H, Q, IQ, DR, part]); y = df["y"].values
    w = (np.sqrt(np.clip(df["n"].fillna(600).astype(float), 100, 3000) / 1000.0) * np.where(part[:, 0] > 0, 0.5, 1.0)).values
    if "ovw" in df: w = w * df["ovw"].values
    if "vw" in df: w = w * df["vw"].values
    if "qw" in df: w = w * df["qw"].fillna(1.0).values          # pollster-quality weight (pollster_quality.weight), if set
    if POP_LEAD: w = w * pop_lead_mult(df, t1)                   # lead-dependent RV / adult discount (off; see POP_LEAD)
    # penalties: second differences on the trend knots, ridge on house effects (weighted so sparse pollsters shrink more)
    Dm = np.zeros((K - 2, K)); 
    for i in range(K - 2): Dm[i, i:i + 3] = [1, -2, 1]
    Pen = np.zeros((X.shape[1], X.shape[1])); Pen[:K, :K] = lam_t * Dm.T @ Dm; Pen[K:K + P, K:K + P] = lam_h * np.diag(df.groupby("pollster")["hpen"].first().reindex(pollsters).fillna(1.0).values if "hpen" in df else np.ones(P)); Pen[K + P:, K + P:] = 0.5 * np.eye(X.shape[1] - K - P)
    if NI: j = K + P + len(pops); Pen[j:j + NI, j:j + NI] = lpp * np.eye(NI)          # interactions shrink toward the global offset
    jd = K + P + len(pops) + NI
    for p_, c0, c1 in dblocks:                 # drift: difference penalty + pin the poll-weighted mean drift to zero
        m = c1 - c0; D1 = np.diff(np.eye(m), 1, axis=0); Dp = D1.T @ D1
        if hord == 2: D2 = np.diff(np.eye(m), 2, axis=0); Dp = D2.T @ D2 + 0.05 * Dp
        av = (DR[:, c0:c1] * (w ** 2)[:, None]).sum(0); av = av / av.sum()
        Pen[jd + c0:jd + c1, jd + c0:jd + c1] = lhd * Dp + 1e4 * np.outer(av, av)
    nu = ROBUST_NU if robust_nu == "default" else robust_nu
    w0 = w.copy()
    for it in range(8 if nu else 1):
        Xw = X * w[:, None]; yw = y * w
        beta = np.linalg.solve(Xw.T @ Xw + Pen, Xw.T @ yw)
        if nu:                                   # t-likelihood IRLS: a poll far from the trend (after house effects) counts less
            r = y - X @ beta; s = np.sqrt(np.average(r ** 2, weights=w0 ** 2)); u = (nu + 1) / (nu + (r / s) ** 2)
            w = w0 * np.sqrt(u)
    # centre the house effects on the weighted mean of pollsters that polled in the last 90 days, so the trend is the "typical pollster"
    house = pd.Series(beta[K:K + P], index=pollsters)
    def drift_at(tt):                          # length-P drift of every pollster at day tt (0 where no drift columns)
        out = np.zeros(P); hv = hatd(float(tt))
        for c, (p_, k) in enumerate(dcols): out[p_] += beta[jd + c] * hv[k]
        return out
    house_lvl = house.copy()
    if ND: house = house + drift_at(T)
    rq = df[df["end_date"] >= t1 - pd.Timedelta(days=90)]
    recent = rq.groupby("pollster")["qw"].sum().sort_values(ascending=False) if "qw" in df else rq["pollster"].value_counts()   # quality-weighted count when set
    recent = recent[recent > 0]; rc = house[recent.index].mul(recent).sum() / recent.sum() if len(recent) else 0.0
    cn = pd.Series(0.0, index=pollsters)
    if len(recent): cn[recent.index] = recent.values / recent.sum()
    beta_fit = beta.copy()                     # the fitted coefficients BEFORE the centring below moves level between trend and house
    house_path = None
    if ND:                                     # the typical recent pollster's level moves with its drift: rc per trend knot
        rck = np.array([float((house_lvl.values + drift_at(kk)) @ cn.values) for kk in knots])
        house = house - rc; beta[:K] += rck
        act = sorted({pollsters[p_] for p_, _ in dcols})
        house_path = pd.DataFrame({pp: [float(house_lvl[pp] + drift_at(kk)[pi[pp]] - rck[i]) for i, kk in enumerate(knots)] for pp in act},
                                  index=[t0 + pd.Timedelta(days=int(k)) for k in knots])
    else:
        house = house - rc; beta[:K] += rc
    trend = pd.DataFrame({"date": [t0 + pd.Timedelta(days=int(k)) for k in knots], "value": beta[:K]})
    # residuals from the fit itself (2026-09-30): the centring shifts beta[:K] by rc but not beta's house block, so
    # y - X @ beta carried a -rc offset in every row and inflated resid_sd (2.03 v 1.94) and the band's s2 by ~10 %
    resid = y - X @ beta_fit
    # 95 % band (2026-09-23, user: "put a CI on the current generic ballot estimate"): sandwich covariance of the penalised fit,
    # Cov = s2 A^-1 (Xw'Xw) A^-1, with the trend level's pollster centring (rc) carried through as a linear combination.
    A = Xw.T @ Xw + Pen; Ai = np.linalg.inv(A); H = Xw @ Ai @ Xw.T; edf = float(np.trace(H))
    s2 = float(np.sum((w * resid) ** 2) / max(len(y) - edf, 1.0)); Cov = s2 * Ai @ (Xw.T @ Xw) @ Ai
    cvec = np.zeros(P)
    if len(recent): cvec[[pi[p] for p in recent.index]] = recent.values / recent.sum()
    def _drift_vec(a, tt, scale):
        if ND:
            hv = hatd(float(tt))
            for c, (p_, k) in enumerate(dcols): a[jd + c] = cvec[p_] * hv[k] * scale
    def se_at(basis_row, tt=None):             # basis_row: length-K hat weights of a date
        a = np.zeros(X.shape[1]); a[:K] = basis_row; a[K:K + P] = cvec * basis_row.sum()
        if tt is not None: _drift_vec(a, tt, basis_row.sum())
        return float(np.sqrt(max(a @ Cov @ a, 0.0)))
    trend["se"] = [se_at(np.eye(K)[k], knots[k]) for k in range(K)]
    def hat(tt):
        return np.clip(np.where(tt <= knots, (tt - np.r_[knots[0] - knot_days, knots[:-1]]) / knot_days, (np.r_[knots[1:], knots[-1] + knot_days] - tt) / knot_days), 0, 1)
    se_now = se_at(hat(float(T)), float(T))
    # each poll's influence on TODAY's trend value (2026-09-25, poll page): today = a . beta, beta = Ai Xw' (w y), so
    # d today / d y_i = (a Ai Xw')_i w_i. These add to ~1; a poll far in the past has ~0, a recent large one the most.
    a_now = np.zeros(X.shape[1]); hn = hat(float(T)); a_now[:K] = hn; a_now[K:K + P] = cvec * hn.sum(); _drift_vec(a_now, float(T), hn.sum())
    infl = (a_now @ Ai @ Xw.T) * w
    pw = pd.DataFrame({"pollster": df["pollster"].values, "date": df[tcol].dt.date.astype(str).values, "pop": df["pop"].values,
                       "n": df["n"].values, "y": y, "w": w, "sample_w": np.sqrt(np.clip(df["n"].fillna(600).astype(float), 100, 3000) / 1000.0).values,
                       "partisan": part[:, 0] > 0, "overlap_w": df["ovw"].values if "ovw" in df else 1.0,
                       "variant_w": df["vw"].values if "vw" in df else 1.0, "influence": infl,
                       "house": [float(house.get(pp, 0.0)) for pp in df["pollster"].values]})
    return {"pw": pw, "se_now": se_now, "trend": trend, "house": house.sort_values(), "pop": dict(zip(pops, beta[K + P:K + P + len(pops)])), "pop_pp": {c: float(v) for c, v in zip(cells, beta[K + P + len(pops):K + P + len(pops) + NI])}, "pop_gap": gaps, "partisan": float(beta[-1]), "resid_sd": float(np.sqrt(np.average(resid ** 2, weights=w))), "house_path": house_path, "n": len(df), "t0": t0, "t1": t1, "beta": beta, "X": X, "resid": resid}


def trend_at(F, date):
    tr = F["trend"]; d = pd.Timestamp(date); return float(np.interp((d - F["t0"]).days, (tr["date"] - F["t0"]).dt.days.values, tr["value"].values))


def calibrate_history():
    """Run the estimator on the 538 generic polls of 2018 and 2022 (LV/RV/A, pollster, sample) and compare the
    election-day trend with the actual national House two-party margin."""
    g = D.polls("generic"); out = {}
    # actual national House two-party margins: 2018/2022 from the FEC totals in this repo; 2020 from the FEC
    # 2020 workbook (computed below when present); 2024 from the FEC summary (R 49.8 / D 47.2 -> -2.7)
    actuals = {2018: 8.46, 2020: None, 2022: -3.30, 2024: -2.7}
    try:
        h20 = D.fec_house(2020); actuals[2020] = round(100 * (h20["dem_votes"].sum() - h20["rep_votes"].sum()) / (h20["dem_votes"].sum() + h20["rep_votes"].sum()), 2)
    except Exception: actuals[2020] = 3.1
    for cyc, actual, eday in ((2018, actuals[2018], "2018-11-06"), (2020, actuals[2020], "2020-11-03"), (2022, actuals[2022], "2022-11-08"), (2024, actuals[2024], "2024-11-05")):
        q = g[g["cycle"] == cyc].copy(); q = q[q["end_date"] >= f"{cyc}-03-01"]
        if len(q) < 30: continue
        q["pop"] = q["population"].astype(str).str.lower().map(lambda p: "lv" if p.startswith("lv") else "a" if p == "a" else "rv"); q["n"] = q["sample_size"]; q["y"] = q["margin"]
        q["internal"] = q["internal"].fillna(False).astype(bool); q["partisan"] = q["partisan"].where(q["partisan"].notna(), None)
        F = fit(q, t1=eday); est = trend_at(F, eday)
        # the value the estimator would have given at each lead, fitted only on polls available then
        leads = {}
        for ld in (50, 30, 14, 0):
            asof = pd.Timestamp(eday) - pd.Timedelta(days=ld); qq = q[q["end_date"] <= asof]
            if len(qq) >= 30: leads[ld] = round(trend_at(fit(qq, t1=asof), asof) - actual, 2)
        out[cyc] = {"polls": len(q), "trend_election_day": round(est, 2), "actual": actual, "error_by_lead_days": leads, "lv_minus_rv": round(-F["pop"].get("rv", 0.0), 2)}
    return out


def diagnostics(df, F, days=90):
    t1 = F["t1"]; q = df[df["end_date"] >= t1 - pd.Timedelta(days=days)].copy(); q["house"] = q["pollster"].map(F["house"]); q["adj"] = q["y"] - q["house"] - q["pop"].map(lambda p: F["pop"].get(p, 0.0))
    out = {}
    out["now"] = round(trend_at(F, t1), 2); out["30d_ago"] = round(trend_at(F, t1 - pd.Timedelta(days=30)), 2); out["60d_ago"] = round(trend_at(F, t1 - pd.Timedelta(days=60)), 2); out["90d_ago"] = round(trend_at(F, t1 - pd.Timedelta(days=90)), 2)
    out["raw_mean_30d"] = round(float(q[q["end_date"] >= t1 - pd.Timedelta(days=30)]["y"].mean()), 2); out["adj_mean_30d"] = round(float(q[q["end_date"] >= t1 - pd.Timedelta(days=30)]["adj"].mean()), 2)
    for k in ("dem", "rep", "und"):
        s = df.set_index("end_date")[k].rolling("30D").mean(); out[f"{k}_30d"] = round(float(s.iloc[-1]), 1); out[f"{k}_30d_ago"] = round(float(s[s.index <= t1 - pd.Timedelta(days=30)].iloc[-1]), 1) if (s.index <= t1 - pd.Timedelta(days=30)).any() else None
    out["pop_effects_vs_lv"] = {k: round(v, 2) for k, v in F["pop"].items()}; out["partisan_effect"] = round(F["partisan"], 2); out["resid_sd"] = round(F["resid_sd"], 2)
    by = q.groupby("pop").agg(n=("y", "size"), raw=("y", "mean"), adj=("adj", "mean")).round(2); out["by_pop_90d"] = by.to_dict("index")
    hc = df[df["end_date"] >= t1 - pd.Timedelta(days=120)]["pollster"].value_counts(); out["house_effects_active"] = {p: {"effect": round(float(F["house"][p]), 2), "polls_120d": int(hc[p])} for p in hc.index[:25]}
    # composition: how much of the raw 30-day change is pollster mix? raw change minus adjusted change
    r_now = q[q["end_date"] >= t1 - pd.Timedelta(days=30)]; r_prev = df[(df["end_date"] < t1 - pd.Timedelta(days=30)) & (df["end_date"] >= t1 - pd.Timedelta(days=60))].copy()
    r_prev["house"] = r_prev["pollster"].map(F["house"]); r_prev["adj"] = r_prev["y"] - r_prev["house"] - r_prev["pop"].map(lambda p: F["pop"].get(p, 0.0))
    out["raw_change_30d"] = round(float(r_now["y"].mean() - r_prev["y"].mean()), 2) if len(r_prev) else None; out["adj_change_30d"] = round(float(r_now["adj"].mean() - r_prev["adj"].mean()), 2) if len(r_prev) else None
    return out


BSKY_CACHE = (ROOT / "data" / "state" / "bluesky_generic.csv")


def _add_bluesky(M: pd.DataFrame) -> pd.DataFrame:
    """Polling USA's national generic-ballot posts (bluesky_polls.national_generic) as a third source (2026-09-26). The post's
    date is the poll's END date (25 of 31 posts matched to VoteHub polls, 2026-07..09). A post is dropped when the same
    pollster has a poll ending within 2 days, or any poll ending within a day has identical D and R. No sample size or
    population is posted: n is left empty and pop is the pollster's most common population in the other sources (RV if none).
    Posts accumulate in data/cache/bluesky_generic.csv, since the feed is read 60 days back."""
    from . import bluesky_polls as B, names as R
    try:
        N = B.national_generic(60)
        if BSKY_CACHE.exists():
            old = pd.read_csv(BSKY_CACHE, parse_dates=["date"]); N = pd.concat([old, N], ignore_index=True).drop_duplicates(subset=["pollster", "date", "dem", "rep"])
        if len(N): N.to_csv(BSKY_CACHE, index=False)
    except Exception as e:
        print("  bluesky generic:", str(e)[:80])
        N = pd.read_csv(BSKY_CACHE, parse_dates=["date"]) if BSKY_CACHE.exists() else pd.DataFrame()
    if N.empty: return M
    N = N.copy(); N["key"] = N["pollster"].map(R.canon); keep = []
    for _, r in N.iterrows():
        near = (M["end_date"] - r["date"]).abs()
        if ((M["pollster"] == r["key"]) & (near <= pd.Timedelta(days=2))).any(): continue
        if ((near <= pd.Timedelta(days=1)) & (M["dem"] == r["dem"]) & (M["rep"] == r["rep"])).any(): continue
        keep.append(r)
    if not keep: return M
    A = pd.DataFrame(keep); pops = M.groupby("pollster")["pop"].agg(lambda x: x.mode().iat[0] if len(x.mode()) else "rv")
    A = pd.DataFrame({"pollster": A["key"], "end_date": A["date"], "start_date": A["date"] - pd.Timedelta(days=3), "n": float("nan"),
                      "pop": A["key"].map(pops).fillna("rv"), "internal": False, "partisan": A["partisan"].where(A["partisan"].notna(), None),
                      "dem": A["dem"], "rep": A["rep"], "y": A["dem"] - A["rep"], "und": 100 - A["dem"] - A["rep"], "src": "bluesky", "key": A["key"]})
    print(f"  bluesky generic: {len(N)} posts, {len(A)} not in VoteHub (newest {A.end_date.max().date()})")
    M = pd.concat([M, A], ignore_index=True).sort_values("end_date").reset_index(drop=True)
    M["partisan"] = M["partisan"].where(M["partisan"].notna(), None); M["internal"] = M["internal"].fillna(False).astype(bool)
    return M


def _add_pollresults(M: pd.DataFrame, kind: str) -> pd.DataFrame:
    """pollresults.org's national generic-ballot / Trump-approval posts (bluesky_polls.pr_national) as a fourth source
    (2026-09-29). They carry n and population, so a post is the same poll as an existing row when the pollster matches, the
    population matches and the end dates are within 2 days, or any row ending within a day has identical numbers and a
    sample size within 5 %. Posts accumulate in data/cache/pollresults_{kind}.csv (the feed is read 60 days back)."""
    from . import bluesky_polls as B, names as R
    cache = ROOT / "data" / "state" / f"pollresults_{kind}.csv"
    try:
        N = B.pr_national(60, kind)
        if cache.exists():
            old = pd.read_csv(cache, parse_dates=["start_date", "end_date"])
            N = pd.concat([old, N], ignore_index=True).drop_duplicates(subset=["pollster", "end_date", "pop", "dem", "rep"])
        if len(N): N.to_csv(cache, index=False)
    except Exception as e:
        print(f"  pollresults {kind}:", str(e)[:80])
        N = pd.read_csv(cache, parse_dates=["start_date", "end_date"]) if cache.exists() else pd.DataFrame()
    if N.empty: return M
    N = N.copy(); N["key"] = [series_key(R.canon(p), sp) for p, sp in zip(N["pollster"], N["sponsor"])]; keep = []
    # 2026-10-10: the same poll under ANOTHER name from Polling USA (no n, population guessed): ONE such row ending within a
    # day with identical D/R shares IS this post - it takes this post's name, n, population and start. Done in a first pass so
    # every other version of the survey (RV, adults) is then compared with the corrected row whatever the post order. Before,
    # the pollresults copy was dropped and the Polling USA one kept (wrong name, RV guessed for an LV result), so the survey's
    # other versions entered under the second name and the survey counted twice: The Honest Poll 10/4-6 ("Honest Polling"
    # 54-42 = its LV version), Centerline Research & Strategy 9/26-10/1 ("Centerline Research" 50-42 = its LV version),
    # WSJ 9/16-21 ("WSJ" = Wall Street Journal 50-42).
    # A Polling USA name that is a KNOWN, different pollster (it appears in the other sources) is a different poll, never
    # renamed: WSJ 9/16-21 and Verasight 9/16-21 were both 50-42 (the 2026-09-30 coincidence below).
    other_names = set(M.loc[M["src"] != "bluesky", "pollster"])
    done = set()
    for i, r in N.iterrows():
        near = (M["end_date"] - r["end_date"]).abs()
        bx = (near <= pd.Timedelta(days=1)) & (M["src"] == "bluesky") & M["n"].isna() & (M["dem"] == r["dem"]) & (M["rep"] == r["rep"])
        bx &= (M["pollster"] == r["key"]) | ~M["pollster"].isin(other_names)
        if int(bx.sum()) == 1:
            j = M.index[bx][0]
            M.loc[j, ["pollster", "n", "pop", "start_date"]] = [r["key"], float(r["n"]) if pd.notna(r["n"]) else float("nan"), r["pop"] or M.at[j, "pop"], r["start_date"]]
            if "key" in M.columns: M.loc[j, "key"] = r["key"]
            done.add(i)
    N = N.drop(index=list(done))
    for _, r in N.iterrows():
        near = (M["end_date"] - r["end_date"]).abs()
        hit = (M["pollster"] == r["key"]) & (near <= pd.Timedelta(days=2))
        # a Polling USA post (no n, population guessed) of the same poll: give it this post's n, population and start
        bs = hit & (M["src"] == "bluesky") & M["n"].isna() & (M["dem"] == r["dem"]) & (M["rep"] == r["rep"])
        if bs.any():
            j = M.index[bs][0]; M.loc[j, ["n", "pop", "start_date"]] = [float(r["n"]) if pd.notna(r["n"]) else float("nan"), r["pop"] or M.at[j, "pop"], r["start_date"]]
            continue
        if (hit & (M["pop"].astype(str) == str(r["pop"]))).any(): continue
        same = M[(near <= pd.Timedelta(days=1)) & (M["dem"] == r["dem"]) & (M["rep"] == r["rep"])]
        # 2026-09-30: when the post's pollster or sponsor is already a known pollster, identical numbers under ANOTHER known
        # name are a different poll. WSJ (Impact/National Research) 9/16-21 RV 50-42 was dropped as a copy of Verasight's
        # RV 50-42 (same dates, a row without n) - the identical-number rule is for sponsor-vs-pollster name mismatches.
        names = {r["key"]} | ({R.canon(r["sponsor"])} if isinstance(r.get("sponsor"), str) and r["sponsor"] else set())
        if len(same) and names & set(M["pollster"]): same = same[same["pollster"].isin(names)]
        if len(same) and (pd.isna(r["n"]) or (same["n"].astype(float).isna() | ((same["n"].astype(float) - float(r["n"])).abs() <= 0.05 * float(r["n"]))).any()):
            continue
        keep.append(r)
    if not keep: return M
    A = pd.DataFrame(keep)
    # pollresults tags a partisan pollster only sometimes; inherit the pollster's tag from the other sources (VoteHub,
    # Polling USA's cache), e.g. McLaughlin = REP
    known = {}
    for src in (M[["pollster", "partisan"]], (pd.read_csv(BSKY_CACHE).assign(pollster=lambda d: d["pollster"].map(R.canon))[["pollster", "partisan"]]
                                               if BSKY_CACHE.exists() and kind == "generic" else None)):
        if src is None: continue
        for k, v in src.dropna(subset=["partisan"]).groupby("pollster")["partisan"]:
            if len(v.mode()): known.setdefault(k, v.mode().iat[0])
    A["partisan"] = [p if isinstance(p, str) and p else known.get(k) for p, k in zip(A["partisan"], A["key"])]
    A = pd.DataFrame({"pollster": A["key"], "end_date": A["end_date"], "start_date": A["start_date"], "n": A["n"].astype(float),
                      "pop": A["pop"].fillna("rv"), "internal": False, "partisan": A["partisan"].where(A["partisan"].notna(), None),
                      "sponsors": A["sponsor"].fillna(""), "dem": A["dem"], "rep": A["rep"], "y": A["dem"] - A["rep"],
                      "und": 100 - A["dem"] - A["rep"], "src": "pollresults", "key": A["key"]})
    print(f"  pollresults {kind}: {len(N)} posts, {len(A)} not in the other sources (newest {A.end_date.max().date()})")
    return pd.concat([M, A], ignore_index=True).sort_values("end_date").reset_index(drop=True)


def merged(kind: str) -> pd.DataFrame:
    """VoteHub (the open polling API) + Polling USA's posts (generic ballot only) + pollresults.org's posts, pollster names
    harmonised (names.canon); duplicates are resolved in _add_bluesky / _add_pollresults, VoteHub's copy (which carries the
    internal / partisan flags) winning."""
    from . import names as R
    V = load_votehub("generic-ballot" if kind == "generic" else "approval")
    V = V[V["subject"].astype(str).str.contains("2026")] if kind == "generic" else V[(V["subject"].astype(str).str.contains("Trump")) & (V["end_date"] >= "2025-01-20")]
    V = V.copy(); V["src"] = "votehub"; V["key"] = [series_key(R.canon(p), sp) for p, sp in zip(V["pollster"], V["sponsors"])]
    M = V.sort_values("end_date").reset_index(drop=True); M["pollster"] = M["key"]
    if kind == "generic": M = _add_bluesky(M)
    M = _add_pollresults(M, kind)
    # Pollsters listed in KNOWN_LEAN are tagged whatever the feed said (x0.5 weight + the partisan offset). Empty since the
    # 2026-09-29 backtest (see KNOWN_LEAN): the tag made the archive fit worse; the pollster's own house effect carries its lean.
    for k, side in KNOWN_LEAN.items():
        M.loc[(M["pollster"] == k) & M["partisan"].isna(), "partisan"] = side
    # quality rating per poll (always recorded; used only when a QUALITY_ switch is set)
    try:
        from . import pollster_quality as Q
        rt = {p: Q.live_rating(p) for p in M["pollster"].unique()}
        M["q_score"] = M["pollster"].map(lambda p: rt[p]["score"]); M["q_grade"] = M["pollster"].map(lambda p: rt[p]["grade"])
        if QUALITY_EXCLUDE_BELOW is not None:
            drop = M["q_score"] <= QUALITY_EXCLUDE_BELOW
            if drop.any(): print(f"  quality screen ({kind}): {int(drop.sum())} polls excluded -", M.loc[drop, "pollster"].value_counts().to_dict())
            M = M[~drop].reset_index(drop=True)
        if QUALITY_GAMMA is not None: M["qw"] = Q.weight(M["q_score"].values, QUALITY_GAMMA)
    except Exception as e:
        print("  pollster quality:", str(e)[:80])
    M["partisan"] = M["partisan"].where(M["partisan"].notna(), None); M["internal"] = M["internal"].fillna(False).astype(bool)
    return M


def main():
    G = merged("generic"); FG = fit(G)
    A = merged("approval"); FA = fit(A)
    dg, da = diagnostics(G, FG), diagnostics(A, FA); cal = calibrate_history()
    print("GENERIC BALLOT (D-R), our trend:", {k: dg[k] for k in ("now", "30d_ago", "60d_ago", "90d_ago", "raw_mean_30d", "adj_mean_30d", "raw_change_30d", "adj_change_30d")})
    print("  shares 30d:", {k: dg[k] for k in ("dem_30d", "dem_30d_ago", "rep_30d", "rep_30d_ago", "und_30d", "und_30d_ago")})
    print("  population effects vs LV:", dg["pop_effects_vs_lv"], "| partisan/internal:", dg["partisan_effect"], "| resid sd", dg["resid_sd"]); print("  by population, 90d:", dg["by_pop_90d"])
    print("  house effects (active pollsters):", {k: v["effect"] for k, v in list(dg["house_effects_active"].items())[:18]})
    print("APPROVAL (net), our trend:", {k: da[k] for k in ("now", "30d_ago", "60d_ago", "90d_ago", "raw_change_30d", "adj_change_30d")}, "| pop:", da["pop_effects_vs_lv"])
    print("  approve/disapprove 30d:", {k: da[k] for k in ("dem_30d", "dem_30d_ago", "rep_30d", "rep_30d_ago")})
    print("CALIBRATION of this estimator on 538's 2018/2022 generic polls:", cal)
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "sources": {"generic": G["src"].value_counts().to_dict(), "approval": A["src"].value_counts().to_dict()}, "generic": {"diag": dg, "trend": [(d.date().isoformat(), round(v, 2)) for d, v in zip(FG["trend"]["date"], FG["trend"]["value"])], "house": {k: round(float(v), 2) for k, v in FG["house"].items()}, "n": int(FG["n"]), "last_poll": G["end_date"].max().date().isoformat()},
           "approval": {"diag": da, "trend": [(d.date().isoformat(), round(v, 2)) for d, v in zip(FA["trend"]["date"], FA["trend"]["value"])], "house": {k: round(float(v), 2) for k, v in FA["house"].items()}, "n": int(FA["n"]), "last_poll": A["end_date"].max().date().isoformat()},
           "calibration": cal}
    for key, F, Df in (("generic", FG, G), ("approval", FA, A)):
        out[key]["band"] = [(d.date().isoformat(), round(v - 1.96 * s, 2), round(v + 1.96 * s, 2)) for d, v, s in zip(F["trend"]["date"], F["trend"]["value"], F["trend"]["se"])]
        now = trend_at(F, F["t1"]); out[key]["now_ci"] = [round(now - 1.96 * F["se_now"], 2), round(now + 1.96 * F["se_now"], 2)]
        Q = field_prepare(Df) if FIELD_PREP else Df.copy()
        Q = Q[Q["end_date"] >= F["t1"] - pd.Timedelta(days=400)]
        part = ((Q["internal"]) | Q["partisan"].notna()).astype(float)
        adj = Q["y"] - Q["pollster"].map(F["house"]).fillna(0) - Q["pop"].map(lambda p: F["pop"].get(p, 0.0)) - F["partisan"] * part
        dcol = Q["mid"] if "mid" in Q else Q["end_date"]
        out[key]["polls"] = [(d.date().isoformat(), round(float(a), 1), round(float(r), 1), str(p), str(pp), (None if pd.isna(n) else int(n)))
                             for d, a, r, p, pp, n in zip(dcol, adj, Q["y"], Q["pollster"], Q["pop"], Q["n"])]
        pw = F["pw"]; pw = pw[pd.to_datetime(pw["date"]) >= F["t1"] - pd.Timedelta(days=150)].sort_values("date", ascending=False)
        out[key]["poll_weights"] = [{"pollster": r.pollster, "date": r.date, "pop": r.pop, "n": None if pd.isna(r.n) else int(r.n),
                                     "value": round(float(r.y), 1), "house": round(r.house, 2), "partisan": bool(r.partisan),
                                     "sample_w": round(float(r.sample_w), 3), "overlap_w": round(float(r.overlap_w), 3),
                                     "variant_w": round(float(r.variant_w), 3), "w": round(float(r.w), 3),
                                     "influence": round(float(r.influence), 4)} for r in pw.itertuples()]
    print("generic now 95% CI:", out["generic"]["now_ci"], "| approval:", out["approval"]["now_ci"])
    json.dump(out, open(CACHE / "generic_model.json", "w"), indent=1); G.to_csv(CACHE / "generic_polls.csv", index=False); A.to_csv(CACHE / "approval_polls_votehub.csv", index=False)
    try:                    # same-pollster poll-to-poll changes vs the trend (2026-09-30; private: web/data/pollster_deltas.json)
        from . import pollster_deltas as _PD; _PD.run({"generic": (G, FG), "approval": (A, FA)})
    except Exception as e: print("  pollster deltas failed:", str(e)[:120])


if __name__ == "__main__":
    main()
