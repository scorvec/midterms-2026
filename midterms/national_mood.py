"""The national environment E and its errors (2026-10-03).

NOT LIVE (2026-10-09 docstring fix: the switch below is APPROVAL = False - the user left it off on 2026-10-03, "Yea just leave it").
Built 2026-10-03 (user: "any value in using a presidential approval metric for midterms?" -> "Sure go for it"), APPROVAL = True would be: E = G + c0(L) + c1(L) * x, x = net presidential approval on Gallup's scale signed toward the Democrats
(midterms/approval_hist.py; live from our approval trend - 2.5). The generic ballot's miss depends on the president's standing:
the average overstated Democrats more under popular Republican or unpopular Democratic presidents and less (or not at all)
under unpopular Republican or popular Democratic ones. research/approval_value.py: corr(x, miss) +0.67 at 30 days (t 3.3, 15
cycles), +0.56 at 60 (t 2.5); robust to dropping 2002, 1998 or 2018; leave-one-out vs NO correction better in 12/15 at 30
days (paired t 2.6) and 11/15 at 60 (t 1.9); vs a fixed correction 10/15 (t 1.3, n.s.). It assumes no FIXED direction: the
sign follows approval and could flip. The error s(L) is the leave-one-out RMS of that model, smoothed over leads.
Only the fitted coefficients are distributed (data/static/national_mood_fit.json); see TABLE below for refitting.

Before that, the same day:
LIVE RULE (user, 2026-10-03: "I'm just against making an assumption of the direction of the polling error"): E = our generic-ballot
trend, NO directional correction (DIRECTION = False). The national error is the size of past misses ABOUT ZERO - the RMS of V - G,
not the spread around an assumed bias - fitted as s0(L) = a + b sqrt(L) on the 15-cycle table (3.4 on election day, 4.3 at 30 days,
5.1 at 120; our own estimator's 7 like-for-like cycles give 3.4 / 3.8 / 4.2). Why no correction: on cycles where the average is built
like ours (research/national_mood_ours.py: HuffPost Pollster poll files 2012-16 + the 538 archive 2018-24, all through generic.fit)
the overstatement is -2.8 at 30 days (6 of 7 cycles, t -2.4) but no fitted correction beats a flat -2 out of sample, a midterm-only
correction is worst (3 midterms, 2018 the other sign), and the 1996-2010 record that drove the pooled -3.1 is 538's averages, not
ours. Leak-free harnesses, no direction vs -2: House 0.1261 -> 0.1177 (12/12 better, mean PIT 0.77 -> 0.64), Senate 0.1512 ->
0.1564 (8/24) - the two lean opposite ways, neither decides it.

The fitted-correction machinery below (c(L), s(L) about the mean) is kept for research; DIRECTION = True restores it.
History - the fitted mapping (live 2026-10-03 for a day; user: "The national mood number I think is the most important and it is
just using a hand wavy polling average -2"):

research/national_mood.py fits, on every cycle 1996-2024 with a generic-ballot average on the day (538's historical average to 2016,
our own estimator 2018-24, release-lagged), how the average G at L days out relates to the national House two-party vote V:
    E = G + c(L),   c(L) = c0 + c1 sqrt(L)    (-2.61 at election day, -3.12 at 30 days, -3.64 at 120: polls overstated Democrats
                                              in 13 of 15 cycles; the gap narrows as the election nears)
    sd of V - E = s(L) = s0 + s1 sqrt(L)      (2.40 at election day, 3.12 at 30 days, 3.85 at 120; predictive, 15 cycles)
Out of sample (leave one cycle out) the fitted level and the old hand-set -2 score the same at 30 days (RMSE 2.75 v 2.81); what the
mapping adds is that BOTH the correction and its error are estimated and depend on the lead, instead of two constants.
Midterm-drift and slope terms were tested and did not help out of sample (research/national_mood.py).

Errors built on it:
  * House: s_house = sqrt(s(L)^2 + S_SEAT^2). The seat model's contested-seat intercept over the national vote moves from year to
    year (2018 -0.34, 2020 -1.07, 2022 +1.93; sd 1.56, inflated for three years to 2.0) - an error the national vote does not carry.
  * Senate/governors: the shared statewide polling shock scales with s(L) (SEN_PER_HOUSE, the 2026-09-30 ratio 3.2 / 4.5), and it
    is drawn with correlation RHO_STATEWIDE to the House draw: the generic-ballot miss and the Senate's shared polling miss
    correlated +0.29 at election day, +0.31 at 14 days, +0.07 at 30 days over 13 cycles (538 raw_polls) - not the 1.0 one shared
    draw implied. Each race's own systematic poll error is the measured total minus that shared part (senate2026.race_sys).

Evidence on the live harnesses (2026-10-03, leak-free: pollster leans AND c, s refitted without the test cycle; current code
otherwise; old = E = G - 2, House s 4.5, Senate shared 3.2):
    Senate 2018-24 x 6 dates: log loss 0.1512 -> 0.1476 (better 17/24, cycle-block t 1.1, 3 df); the level alone 0.1481 (22/24, t 2.4)
    House 2018/22 x 6 dates:  log loss 0.1261 -> 0.1400 (better 0/12, t -2.2, 1 df); the level alone 0.1381
The two harnesses lean OPPOSITE ways under the old setting: House mean PIT 0.77 (too Republican at every date: in 2018 the polls
understated Democrats, in 2022 Democrats won more seats than their national vote implied), Senate mean PIT 0.27 (too Democratic:
statewide polls overstated Democrats). Any Republican shift of E helps one and hurts the other, so neither tests the national
correction cleanly; the 15-cycle national-vote test above leaves the fitted mapping and the old -2 tied. The Senate double-count fix
alone (per-race error net of the shared shock, shared still 3.2) is a wash there (0.1512 -> 0.1517, 10/24): kept because the old
rule added the shared shock on top of a TOTAL that already contained it.
"""
import json
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ELECTION = pd.Timestamp("2026-11-03")
S_SEAT = 2.0
SEN_PER_HOUSE = 3.2 / 4.5
RHO_STATEWIDE = 0.3
APPROVAL = False       # approval-adjusted correction (above) - built and tested 2026-10-03, OFF pending the user; False -> the no-direction rule below
DIRECTION = False      # False = no directional correction (user 2026-10-03); True = the fitted c(L) and s(L) about the mean
FALLBACK = {"c": [0.0, 0.0], "s": [4.5, 0.0], "s0": [4.5, 0.0]}     # if neither the fit nor the table is available


FIT_JSON = ROOT / "data" / "static" / "national_mood_fit.json"   # committed: the fitted coefficients (fit / fit_approval below)
# Optional: the underlying table (G and V per cycle and lead; + Gallup approval x) is NOT distributed with this repository
# (part of it is derived from archives whose terms are unclear). If a copy is placed here, `python -m midterms.national_mood
# --refit` rebuilds FIT_JSON from it.
TABLE = ROOT / "data" / "raw" / "national_mood" / "table.csv"
TABLE_APP = ROOT / "data" / "raw" / "national_mood" / "table_approval.csv"


def fit(T, exclude=None):
    """The mapping from the table: E = G + c(L), error sd s(L), from every cycle except `exclude`. The per-lead mean and RMS of
    V - G are noisy with 15 cycles, so both are fitted as a + b sqrt(L) across the leads; s is predictive (x sqrt(1 + 1/n): the
    level c is itself estimated from n cycles). Returns dict(c=(a, b), s=(a, b), n, df)."""
    d = T[T.G.notna()]
    if exclude is not None: d = d[d.year != exclude]
    d = d.assign(e=d.V - d.G); leads = sorted(d.lead.unique())
    g = d.groupby("lead").e; L = np.sqrt(np.array(leads, float))
    m = g.mean().reindex(leads).values
    r = g.apply(lambda x: float(np.sqrt(np.mean((x - x.mean()) ** 2) * len(x) / max(len(x) - 1, 1)))).reindex(leads).values
    r0 = g.apply(lambda x: float(np.sqrt(np.mean(x ** 2)))).reindex(leads).values     # RMS about ZERO: no direction assumed
    n = int(d.year.nunique()); r = r * np.sqrt(1 + 1 / n)
    X = np.c_[np.ones(len(L)), L]; ls = lambda y: tuple(float(v) for v in np.linalg.lstsq(X, y, rcond=None)[0])
    return {"c": ls(m), "s": ls(r), "s0": ls(r0), "n": n, "df": n - 1}


def at(F, lead, direction=True):
    """(c, s) of a fit at `lead` days (clipped 0-120, the fitted range); direction=False -> (0, RMS about zero)."""
    L = np.sqrt(np.clip(float(lead), 0, 120))
    if not direction: return 0.0, float(F["s0"][0] + F["s0"][1] * L)
    return float(F["c"][0] + F["c"][1] * L), float(F["s"][0] + F["s"][1] * L)


def fit_approval(T, exclude=None):
    """Per lead: miss e = V - G regressed on x (intercept + slope); the error is the leave-one-cycle-out RMS of that regression
    (predictive). c0, c1 and s are each smoothed over leads as a + b sqrt(L). Returns dict(c0, c1, s, n)."""
    d = T[T.G.notna() & T.x.notna()]
    if exclude is not None: d = d[d.year != exclude]
    leads = sorted(d.lead.unique()); c0, c1, s = [], [], []
    for L in leads:
        q = d[d.lead == L]; e = (q.V - q.G).values; x = q.x.values; X = np.c_[np.ones(len(q)), x]
        b = np.linalg.lstsq(X, e, rcond=None)[0]; c0.append(b[0]); c1.append(b[1]); err = []
        for i in range(len(q)):
            m = np.arange(len(q)) != i; bb = np.linalg.lstsq(X[m], e[m], rcond=None)[0]; err.append(e[i] - X[i] @ bb)
        s.append(float(np.sqrt(np.mean(np.square(err)))))
    S = np.c_[np.ones(len(leads)), np.sqrt(np.array(leads, float))]; ls = lambda y: tuple(float(v) for v in np.linalg.lstsq(S, np.array(y), rcond=None)[0])
    return {"c0": ls(c0), "c1": ls(c1), "s": ls(s), "n": int(d.year.nunique())}


def at_approval(A, lead, x):
    L = np.sqrt(np.clip(float(lead), 0, 120)); g = lambda k: float(A[k][0] + A[k][1] * L)
    return g("c0") + g("c1") * x, g("s")


def _fit():
    try:
        return json.loads(FIT_JSON.read_text())
    except Exception as e:
        print(f"  !! national-mood fit unavailable ({e}) - falling back to E = G, error 4.5"); return FALLBACK


def refit():
    """Rebuild FIT_JSON from the local tables (see TABLE); also the late-movement slope early_vote reads."""
    from . import early_vote as EV
    T = pd.read_csv(TABLE); F = fit(T); F["app"] = fit_approval(pd.read_csv(TABLE_APP))
    d = T[T.G.notna()].pivot(index="year", columns="lead", values="G")
    leads = [c for c in d.columns if 0 < c <= EV.MOVE_LEAD_MAX]
    y = np.array([float(((d[0] - d[c]) ** 2).dropna().mean()) for c in leads]); x = np.array(leads, float)
    old = json.loads(FIT_JSON.read_text()) if FIT_JSON.exists() else {}
    out = {"description": old.get("description", ""), **{k: F[k] for k in ("c", "s", "s0", "n", "df")}, "app": F["app"],
           "years": [int(v) for v in sorted(T.year.unique())], "move_q": float(x @ y / (x @ x)), "move_lead_max": EV.MOVE_LEAD_MAX}
    FIT_JSON.write_text(json.dumps(out, indent=1)); return out


def lead(asof=None):
    return max(int((ELECTION - pd.Timestamp(asof if asof is not None else pd.Timestamp.today()).normalize()).days), 0)


def mapping(asof=None, approval_now=None):
    """-> dict(lead, c, s_vote, s_house, sen_s_nat[, x, approval]) for the as-of date (default today). `approval_now` = our
    approval trend's value at asof (backfill passes its as-of fit); default = today's generic_model.json."""
    F = _fit(); L = lead(asof); extra = {}
    if APPROVAL and "app" in F:
        from . import approval_hist as AH
        if approval_now is None: approval_now = json.loads((ROOT / "data" / "cache" / "generic_model.json").read_text())["approval"]["diag"]["now"]
        x, net, _ = AH.live_x(now=approval_now); c, s = at_approval(F["app"], L, x)
        extra = {"x": round(x, 2), "approval": round(float(approval_now), 2), "approval_gallup": round(net, 2)}
    else:
        c, s = at(F, L, DIRECTION)
    return {"lead": L, "direction": DIRECTION, "approval_mode": bool(APPROVAL), "c": round(c, 3), "s_vote": round(s, 3),
            "s_house": round(float(np.hypot(s, S_SEAT)), 3), "sen_s_nat": round(SEN_PER_HOUSE * s, 3), **extra}


def draws(n=20000, seed=5, rho=None):
    """(House national draw, statewide shared draw): unit-sd t5, correlated RHO_STATEWIDE (or `rho`: with the movement split
    on, early_vote.setup returns the correlation the non-movement parts need so the TOTAL stays RHO_STATEWIDE)."""
    from . import model as M
    rho = RHO_STATEWIDE if rho is None else rho
    zh = M.national_z(n, seed); ze = M.national_z(n, seed + 101)
    return zh, rho * zh + np.sqrt(1 - rho ** 2) * ze


def apply(asof=None, approval_now=None):
    """Set the statewide shared shock for this run (senate2026.SEN_S_NAT; governors use it through simulate_senate)."""
    from . import senate2026 as SN
    m = mapping(asof, approval_now); SN.SEN_S_NAT = m["sen_s_nat"]; return m


if __name__ == "__main__":
    import sys
    if "--refit" in sys.argv: print(refit())
    else: print(mapping())
