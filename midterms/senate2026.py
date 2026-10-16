"""2026 Senate forecast: race table, fundamentals prior, poll blend and correlated simulation.

Races from the Wikipedia race table (nominees, incumbents, Cook PVI x2), polls from each race page, a time-varying prior
(lean / incumbency trends 1982-2024, at 2026: 0.79 E, 0.84 lean, 6.7 inc, sd 8.8).
    python -m midterms.senate2026 [E]
"""

import re
import sys
import numpy as np
import pandas as pd
from . import poll_overrides as PO
from . import poll_corrections as PC
from . import wiki_polls as W, model as M, rcv as RC
from .run2026 import GROUP_B_HISP, GROUP_B_ASIAN, CUBAN_WEIGHT, HISP_GROUPS

# Prior: MIT results; lean = mean of the two previous presidential margins vs the nation; E = the national House vote (year
# infoboxes). Only D-v-R contests enter the fit: races where an independent or third candidate took 20 %+ (Angus King ME
# 2012/2018/2024, Murkowski's write-in AK 2010, Crist FL 2010, Pressler SD 2014, Miller AK 2016) would enter as R landslides of
# 20-58 points; two Democrats that MIT files under party_simplified OTHER (IL, MD 2022) count as Democrats.
# Time-varying slopes. The Senate has nationalized: per-cycle fits 1982-2024 show the incumbency coefficient falling ~3.8 pts per
# decade (17-25 in the 1980s-2000s, 4.5-9.4 in 2016-24), the lean slope rising ~0.11 per decade and the residual sd falling ~1.35 per
# decade (15-20 -> 7-11), so a pooled 10-cycle fit is stale. Spec: margin = const + lean x (b1 + b1t t) + inc x (b2 + b2t t) +
# NAT_SLOPE E, t = (year - 2010) / 10, fitted on every D-v-R race 1982-2024 (jungle rounds and races with a 20 %+ independent left
# out); NAT_SLOPE from the 10-cycle pool (2006-24); residual sd extrapolated to the year by a log-linear trend of the squared
# residuals. Evaluated at 2026 (t = 1.6): const 0.696, lean 0.7097 + 0.0792 t = 0.836, inc 12.638 - 3.723 t = 6.68, E 0.794, sd 8.84.
# Walk-forward live-path backtest (Senate 2006-2024, 10 cycles, 1,615 race-dates, prior refitted on earlier cycles only) against the
# pooled 2006-24 D-v-R fit (const 2.22, lean 0.788, inc 10.00, E 0.795, sd 12.0): log loss -0.0080 (7/10 cycles; sign-flip p 0.059,
# race bootstrap p <0.001), Brier -0.0026, CRPS -0.23 (p 0.061 / <0.001); 2014-2024 better in 6/6 cycles, worse 2006/08/12. Caveat:
# the gain is largest in the cycles whose polls overstated Democrats, but unpolled races (no polls involved) show the same time pattern.
SEN_CONST = 0.696
B_LEAN, C_INC, PRIOR_SD, NAT_SLOPE, POLL_SD, POLL_SYS = 0.836, 6.68, 8.84, 0.794, M.SEN_POLL_SD, M.SEN_POLL_SYS
# Midterm race polls earn more weight. The shared per-race Senate polling miss is smaller in midterms: final-3-week non-partisan
# averages, competitive races 1998-2024, systematic part 4.1 midterm v 4.7 presidential. Senate backtest, POLL_SYS x 0.8 (2018-24 on
# the live-path harness, 2006-16 on raw_polls with the true E):
# log loss better in all five midterms (2006 +0.0033, 2010 +0.0074, 2014 +0.0031, 2018 +0.0115, 2022 +0.0045; cycle-block t 3.8,
# 4 df, p ~0.02; Brier better in all five) and worse in 2016/2020/2024, so it is midterm-only. x0.6 scored better still in every
# midterm but 0.8 is the measured ratio (4.1 / 5.0), so the tuned edge of the grid was not taken. Governors (gov2026's simpler
# harness) were mixed (3/5 midterms) and keep 5.0. The backtests still pass M.SEN_POLL_SYS explicitly.
MIDTERM_POLL_SYS_K = 0.8
POLL_SYS_TOTAL = POLL_SYS * MIDTERM_POLL_SYS_K  # 2026 is a midterm: 5.0 -> 4.0 - the measured total race-average miss
POLL_SYS = POLL_SYS_TOTAL  # (display and other callers; the live blend uses race_sys())


def race_sys():
    """Each race's own systematic poll error: the measured total race-average miss minus the shared statewide shock the simulation
    adds on top (SEN_S_NAT x NAT_SLOPE). The total (measured across races and cycles) already contains the cycle-wide common miss,
    so drawing the shared shock as well would count it twice. Live harness: removing it was better in both midterms (2018, 2022),
    worse in presidential years - and the equivalent POLL_SYS x0.6 had scored better in all five midterms 2006-22."""
    return float(np.sqrt(max(POLL_SYS_TOTAL**2 - (SEN_S_NAT * NAT_SLOPE) ** 2, 1.5**2)))


SEN_POLL_BIAS = 0.0  # the pro-D Senate poll miss is a presidential-year (Trump on the ballot) phenomenon: raw_polls last-21-day mean error 2020 +4.8, 2018 +0.8, 2022 -0.9. Midterm-only backtest (2018+2022): PIT dev 0.12 at 0 vs 0.215 at -2, Brier 0.0406 vs 0.0397 - a wash on Brier, much better calibrated at 0. A grid dominated by 2020 gives -2, which does not describe a midterm.
NOW_D, NOW_R = 47, 53  # current Senate (independents with the Democrats)


# Independent challengers. The prior is fitted on D-v-R races only. With no Democrat on the ballot the prior mean of a funded
# independent moves by IND_SHIFT and its sd takes the class's spread; with a Democrat also on the ballot (a split anti-Republican
# vote, MT) there is no precedent and the prior is widened instead. Every 1976-2024 Senate race whose top non-Republican was not a
# Democrat (MIT results, Cook-style lean from the two previous presidential races), against an R-incumbent prior, splits by whether
# the independent funded a campaign, not by being an independent: Osborn NE 2024 +24.8 (June 30 money $1.58M, 1.00x the
# Republican), McMullin UT 2022 +20.6 ($3.25M, 0.85x), Orman KS 2014 +21.3 - but Harrington AR 2020 -4.0 ($0.00M), O'Hara MS 2002
# -43.7, Spannaus VA 1990 -53.1. Averaging the three successes alone (+20.3) is a selection effect: they are known because they
# overperformed. So the shift applies only to a funded independent (>= $1M and >= 25 % of the Republican's June 30 money, which
# every historical success clears and the flops fail) and is shrunk n/(n+k) for n=3 precedents, with the sd widened to carry the
# class's real spread. An unfunded independent gets the plain D-v-R prior, widened.
IND_SHIFT, IND_SHIFT_SD, SPLIT_PRIOR_MULT = 12.0, 9.0, 2.0
IND_MIN_MONEY, IND_MIN_RATIO = 1e6, 0.25
# In the Senate the shared term is calibrated to the measured shared statewide polling miss, and the polled races that decide the
# chamber are ~85 % poll; the House's wider national error is the environment error that its mostly unpolled seats carry in full.
SEN_S_NAT = (
    3.2  # the shared statewide shock in the blend's error split (race_sys); the simulation uses STATE_SHARED_MISS
)
STATE_SHARED_MISS = (
    3.1  # shared statewide miss, pts of margin, about zero (no direction). The cycle-wide miss over a long record is
)
# 3.1 (538 raw_polls Senate+governor 1998-2022, RMS about zero; 3.2-3.3 on the live-path harness 2006-24); the
# RMS of the 2018-24 cycle-mean Senate misses alone is 4.2, and with 4.2 the model's implied correlation between
# race errors was 0.34 against 0.13 measured, while race-level errors were larger than modelled. So the shared
# shock is 3.1 and each race gets RACE_EXTRA = sqrt(4.2^2 - 3.1^2) = 2.83 of its own, which keeps every race's
# total spread (and its odds) and only lowers the correlation. Walk-forward harness 2006-24 against a 4.2 shared
# shock: race log loss -0.0003 / CRPS -0.004 (unchanged), seat-count PIT sd 0.265 -> 0.299 (uniform 0.289),
# cycle-dates inside the 10-90 % seat range 100 % -> 90 % (nominal 80 %). Senate only: governors keep
# GOV_SHARED_MISS 4.2 with no extra (not tested there).
RACE_EXTRA = 2.83  # extra race-level outcome spread on top of the blend's posterior sd (Senate; see above)
GOV_SHARED_MISS = 4.2  # the governors' shared statewide miss (x b3 / NAT_SLOPE in gov2026.simulate)
WNC_SD = 1.3  # white non-college polling-miss factor, pts per 1 sd of the state's share
# Appointed incumbents: 12 appointees on a general ballot 2006-2022 beat an open-seat prior by +6.3 (median +3.8, se 3.1;
# Menendez -8, McSally -6 ... Gillibrand +23, Barrasso +26) against 10.4 for elected incumbents in the same fit (which codes
# appointees as open seats: predecessor = winner 6 years earlier, specials skipped). An appointee carries 0.6 x C_INC.
APPOINTEE_INC = 0.6
SPECIAL_INC_PARTY = {"OH": "R", "FL": "R"}  # 2025 appointees: Husted (Vance's seat), Moody (Rubio's seat)


def races():
    t = pd.read_csv("data/raw/wiki/senate2026_races.csv")
    R = pd.read_csv("data/cache/senate2026_races.csv")
    from .data_prep import _ST

    noms = {}
    for _, r in t.iterrows():
        st = _ST.get(re.sub(r"\s*\(.*?\)", "", str(r[t.columns[0]])).strip())
        c = str(r[t.columns[-1]])
        dem = re.findall(r"▌([^▌\[]+?)\s*\((?:Democratic|DFL)\)", c)
        rep = re.findall(r"▌([^▌\[]+?)\s*\(Republican\)", c)
        inc, ip = str(r[t.columns[2]]), str(r[t.columns[3]])
        if ip.startswith("Republican") and any(inc.split()[-1] in x for x in rep):
            rep = [x for x in rep if inc.split()[-1] in x] + rep
        if ip.startswith(("Democratic", "DFL")) and any(inc.split()[-1] in x for x in dem):
            dem = [x for x in dem if inc.split()[-1] in x] + dem
        key = (st, "special" in str(r[t.columns[0]]).lower())
        ballot = [
            (n.strip(), p)
            for n, p in re.findall(
                r"▌([^▌\[]+?)\s*\((Democratic|DFL|Republican|Independent|Libertarian|Green|[^)]+)\)", c
            )
        ]
        # an interim appointee on the regular ballot (SC: Darline Graham, electoral history "2026 (appointed)") is an appointed
        # incumbent too, not only the specials in SPECIAL_INC_PARTY. Appointed = the incumbent's electoral history ends in
        # "(appointed)" (Hyde-Smith's "2018 (appointed) 2018 (special) 2020" is an elected incumbent).
        hist = next((col for col in t.columns if "Electoral history" in str(col)), None)
        appointed = bool(re.search(r"\(appointed\)\s*$", str(r[hist]).strip())) if hist else False
        noms[key] = {
            "dem_nom": dem[0].strip() if dem else None,
            "rep_nom": rep[0].strip() if rep else None,
            "inc_party": "D" if ip.startswith(("Democratic", "DFL")) else ("R" if ip.startswith("Republican") else "O"),
            "status": str(r[t.columns[6]]),
            "ballot": ballot,
            "appointed": appointed,
        }
    R["key"] = list(zip(R["state"], R["special"]))
    for k in ("dem_nom", "rep_nom", "inc_party", "status", "ballot", "appointed"):
        R[k] = R["key"].map(lambda x, k=k: (noms.get(x) or {}).get(k))
    # the specials are not in the race table: nominees from the race page's infobox; both seats are held by
    # Republican appointees who are on the ballot (Husted for Vance, OH; Moody for Rubio, FL)
    for i, r in R[R["special"]].iterrows():
        try:
            bx = W.infobox_nominees(W.fetch(f"2026 United States Senate special election in {W.STATES[r['state']]}"))
        except Exception:
            bx = []
        if bx:
            R.at[i, "ballot"] = bx
            R.at[i, "dem_nom"] = next((n for n, p in bx if p.startswith("Dem")), None)
            R.at[i, "rep_nom"] = next((n for n, p in bx if p.startswith("Rep")), None)
        R.at[i, "inc_party"] = SPECIAL_INC_PARTY.get(r["state"], r["inc_party"])
        R.at[i, "status"] = (
            "appointed incumbent running"
            if R.at[i, "rep_nom"] and str(r["incumbent"]).split()[-1] in str(R.at[i, "rep_nom"])
            else r["status"]
        )
    # lean on the same definition B_LEAN was fitted on (mean of the two previous presidential margins vs the nation, 50/50, MIT
    # returns); 2 x Cook PVI weights 2024 at 75 % and is rounded to whole points (rms 1.0, up to 1.9 pts off: NC, OR, OK, NE, MN).
    # Cook is the fallback.
    from .gov2026 import _pres_lean

    pl = _pres_lean()
    R["lean_cook"] = R["lean"]
    R["lean"] = [pl(2026, st) if pl(2026, st) == pl(2026, st) else lc for st, lc in zip(R["state"], R["lean_cook"])]
    R["inc"] = np.where(
        R["inc_retiring"] | R["status"].astype(str).str.contains("retiring|lost renomination|resign", case=False),
        0,
        R["inc_party"].map({"D": 1, "R": -1}).fillna(0),
    ).astype(float)
    appointee = R["status"].astype(str).str.contains("appointed incumbent running") | R["appointed"].fillna(
        False
    ).astype(bool)
    R.loc[appointee, "inc"] *= APPOINTEE_INC  # (0 stays 0: an appointee who is not on the ballot)
    return R.drop(columns="key")


def state_loadings():
    """Hispanic (Cubans at CUBAN_WEIGHT), Cuban and Asian CVAP shares per state, centred on the national shares -
    the same term as the House (run2026.group_loadings; its 2025 evidence - the NJ/VA governor and CA Prop 50 swings -
    comes from statewide races), scaled by NAT_SLOPE: the centring offset that a
    low-Hispanic state pays exists only to hold the national environment fixed, and E itself reaches a Senate race
    at 0.8 strength (candidate-driven races), so the whole term - mean and shocks - is damped the same way."""
    g = pd.read_csv("data/static/state_groups.csv").set_index("state")
    g["h_eff"] = g[["sh_" + x for x in HISP_GROUPS]].sum(axis=1) + CUBAN_WEIGHT * g["sh_cuban"]
    out = pd.DataFrame(
        {
            "h_load": g["h_eff"] - g.loc["US", "h_eff"],
            "c_load": g["sh_cuban"] - g.loc["US", "sh_cuban"],
            "a_load": g["sh_asian"] - g.loc["US", "sh_asian"],
        }
    ).drop(index="US")
    out["group_shift"] = NAT_SLOPE * (GROUP_B_HISP * out["h_load"] + GROUP_B_ASIAN * out["a_load"])
    st = g.drop(index="US")
    out["wnc_z"] = ((st["sh_wnc"] - st["sh_wnc"].mean()) / st["sh_wnc"].std()).reindex(out.index)
    return out[["h_load", "c_load", "a_load", "group_shift", "wnc_z"]] * pd.Series(
        {"h_load": NAT_SLOPE, "c_load": NAT_SLOPE, "a_load": NAT_SLOPE, "group_shift": 1.0, "wnc_z": 1.0}
    )


def ind_money(state):
    """(independent challenger's, Republican's) individual + self money through June 30 2026
    (FEC filings, data/static/fec_senate_june30_2026.csv)."""
    try:
        m = pd.read_csv("data/static/fec_senate_june30_2026.csv")
    except FileNotFoundError:
        return float("nan"), float("nan")
    q = m[(m.state == state) & (~m.special.astype(bool))]
    gi = q[q.side == "C"].money
    gr = q[q.side == "R"].money
    return (float(gi.iloc[0]) if len(gi) else float("nan"), float(gr.iloc[0]) if len(gr) else float("nan"))


def blend_race(mu_prior, psd, pm, ne, vm, extra_sys=0.0):
    """Precision blend of one race's prior with its poll average (shared by run() and the Senate backtest). extra_sys: a further
    race-level poll error added in quadrature (the ranked-choice transfer uncertainty, rcv.race_sd)."""
    wp, wq = 1 / psd**2, (1.0 / (POLL_SD**2 * vm / ne + race_sys() ** 2 + extra_sys**2) if ne == ne else 0.0)
    return (wp * mu_prior + wq * (pm if pm == pm else 0)) / (wp + wq), float(np.sqrt(1 / (wp + wq)))


# Same-state statewide error. Within a cycle, a state's Senate and governor polling errors
# correlate 0.58 (95 % 0.46-0.68; 538 raw_polls, non-partisan polls in the last 21 days, races with 2+ polls, 149 state-years 1998-2022):
# a shared state part of sd ~3.7 points (joint_state_sd). The two offices are simulated separately and share the national draw, which
# already gives a state's pair of races a correlation of ~0.31 - the backtest residuals of the two harnesses show 0.32 (the shared
# national shock is larger than the realized cycle-wide miss, so it carries the state part too). With JOINT set (joint_setup, before both simulations), every race's
# own error becomes c x z_state + sqrt(sd^2 - c^2) x z_own, z_state one unit draw per state shared by all of the state's statewide races:
# each race's total variance is unchanged (so every single-race number is), only the covariance between the state's races moves.
# c is capped at 0.9 x the race's own sd. Joint backtest: README "Tested and left off".
JOINT = None
JOINT_ON = False  # tested, not adopted: the joint Senate-governor backtest did not improve (README); True = build_web
# and backfill call joint_setup before the statewide simulations


def joint_state_sd(before=None):
    """sqrt of the within-cycle covariance of the same state's Senate and governor race-average polling errors (cycles < `before`)."""
    global _JSD
    if "_JSD" not in globals():
        _JSD = {}
    if before in _JSD:
        return _JSD[before]
    from pathlib import Path

    r = pd.read_csv(
        Path(__file__).resolve().parents[1] / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False
    )
    r = r[
        r.type_simple.isin(["Sen-G", "Gov-G"])
        & (r.cycle % 2 == 0)
        & (r.time_to_election <= 21)
        & r.partisan.isna()
        & r.cand1_party.isin(["DEM", "REP"])
        & r.cand2_party.isin(["DEM", "REP"])
        & (r.cand1_party != r.cand2_party)
    ]
    if before is not None:
        r = r[r.cycle < before]
    r = r.assign(e=(r.margin_poll - r.margin_actual) * np.where(r.cand1_party == "DEM", 1, -1))
    m = r.groupby(["cycle", "location", "type_simple", "race_id"]).e.agg(["mean", "size"]).reset_index()
    m = (
        m[m["size"] >= 2]
        .groupby(["cycle", "location", "type_simple"])["mean"]
        .mean()
        .unstack()
        .dropna(subset=["Sen-G", "Gov-G"])
    )
    d = m - m.groupby(level=0).transform("mean")
    _JSD[before] = float(np.sqrt(max((d["Sen-G"] * d["Gov-G"]).mean(), 0.0))) if len(d) >= 10 else 0.0
    return _JSD[before]


def joint_setup(n, states=None, seed=23, c=None):
    """Shared unit t5 draws per state for the statewide simulations of one run (call with the run's n before SN.run and GV.run)."""
    global JOINT
    from .data_prep import _ST

    rng = np.random.default_rng(seed)
    states = sorted(states or set(_ST.values()))
    JOINT = {
        "c": joint_state_sd() if c is None else c,
        "z": {st: rng.standard_t(M.T_DF, n) * np.sqrt((M.T_DF - 2) / M.T_DF) for st in states},
    }
    return JOINT


def simulate_senate(S, n=20000, seed=11, nat_z=None, shared=None, extra=None):
    """Margins [n, races]: shared national shock x NAT_SLOPE, group factors, white non-college factor, race residual.
    Columns used: mu, sd, h_load/c_load/a_load (0 if absent), wnc_z."""
    rng = np.random.default_rng(seed)

    def t(size, s):
        return rng.standard_t(M.T_DF, size) * s * np.sqrt((M.T_DF - 2) / M.T_DF)

    el = S["elast"].fillna(1.0).values[None, :] if "elast" in S else 1.0  # per-state sensitivity to the national shock
    s_sh = (
        STATE_SHARED_MISS if shared is None else shared
    ) / NAT_SLOPE  # the statewide shock about zero (pts of margin / slope)
    nat = M.national_shock(n, s_sh, S["state"].values, nat_z, t) * NAT_SLOPE * el  # movement split: model.MOVE
    sd_tot = np.sqrt(S["sd"].values ** 2 + (RACE_EXTRA if extra is None else extra) ** 2)
    res = t((n, len(S)), 1.0) * sd_tot[None, :]
    if JOINT is not None:  # same-state component (JOINT above)
        for j, st in enumerate(S["state"].values):
            z = JOINT["z"].get(st)
            if z is None or len(z) < n:
                continue
            c = min(JOINT["c"], 0.9 * sd_tot[j])
            res[:, j] = res[:, j] * np.sqrt(sd_tot[j] ** 2 - c**2) / sd_tot[j] + c * z[:n]
    P = M.Params()
    grp = 0.0
    for col, sd in (
        ("h_load", P.s_hisp),
        ("c_load", P.s_cuban),
        ("a_load", P.s_asian),
    ):  # same group shocks as the House
        if sd > 0 and col in S:
            grp = grp + t(n, sd)[:, None] * S[col].fillna(0).values[None, :]
    # correlated polling-miss factor across states: white non-college share (z over the 50 states). Measured on 538
    # raw_polls Senate+governor 1998-2022: per-cycle slope sd 1.3 pts per 1 sd; zero-mean on purpose (2010-2020 leaned one way,
    # 2022 did not, so no direction is assumed)
    grp = grp + t(n, WNC_SD)[:, None] * S["wnc_z"].fillna(0).values[None, :]
    return S["mu"].values[None, :] + nat + grp + res


def seat_counts(S, d, d_up):
    """(Democratic seats, independent-challenger wins) per simulation.  A win by an independent challenger
    (NE Osborn, ID Achilles, SD Bengs, MT Bodnar) is not counted as a Democratic seat: none has said he would
    caucus with the Democrats, and Osborn has said he would caucus with neither party.  It does cost the
    Republicans the seat, which is what P(R loses the majority) counts."""
    isI = (S["challenger_party"].fillna("D") != "D").values
    return (NOW_D - d_up) + d[:, ~isI].sum(1), d[:, isI].sum(1)


def d_controls(seats, ind, join="abstain"):
    """Per simulation: do the Democrats organise the Senate? Kalshi's CONTROLS-2026 settles on the party of the president
    pro tempore on 2027-02-01, so there is always a winner. Pro tem is elected by a majority of senators voting
    and the Vice President (a Republican) breaks a tie, so the Democrats need strictly more votes than the Republicans.
    `join`: what the independent challengers do ('D', 'R', or 'abstain' - Osborn's stated line; he is ~75 % of the cases
    where independents hold the balance). 50 D - 49 R - 1 I is the case that turns on it."""
    r = 100 - seats - ind
    return (seats + (ind if join == "D" else 0)) > (r + (ind if join == "R" else 0))


# Cross-race common trend (the core of the Linzer / Economist dynamic model, tested alone): the cycle's Senate polls give a smooth
# common movement g(t) net of race and pollster effects; every poll is moved by g(today) - g(its end date), so a stale poll in a
# thinly polled race carries the movement measured in the others. Senate harness 2006-2024: log loss -0.0035 (8/10 cycles, boot p
# 0.05), CRPS -0.053 (8/10, perm p 0.025, boot p 0.002), insensitive to the smoothness (lam 10-100); the gain is inside 45 days. Not
# used for governors (CRPS +0.067), and not taken from the national generic-ballot change (that version was worse: log loss +0.0024,
# boot p 0.008).
SEN_COMMON_TREND = True
CT_KNOT, CT_LAM, CT_WINDOW, CT_MIN = 14, 30.0, 150, 25


def common_trend(polls, asof, knot=CT_KNOT, lam=CT_LAM, window=CT_WINDOW):
    """g(date) from one cycle's race polls (columns race, pollster, end_date, margin): margin = race effect + pollster effect (ridge 4)
    + hat-basis trend every `knot` days (second-difference penalty `lam`); flat after the newest poll. None if fewer than CT_MIN polls."""
    asof = pd.Timestamp(asof)
    q = polls[(polls.end_date > asof - pd.Timedelta(days=window)) & (polls.end_date <= asof)].copy()
    if len(q) < CT_MIN:
        return None
    q["pb"] = q.pollster.astype(str).str.replace(r"\s*\((?:D|R|I)\)", "", regex=True).str.lower()
    t0 = asof - pd.Timedelta(days=window)
    t = (q.end_date - t0).dt.days.values.astype(float)
    kn = np.arange(0, window + knot, knot)
    B = np.clip(1 - np.abs(t[:, None] - kn[None, :]) / knot, 0, 1)
    Rd = pd.get_dummies(q.race).values.astype(float)
    Pd = pd.get_dummies(q.pb).values.astype(float)
    X = np.hstack([B, Rd, Pd])
    K, nr = len(kn), Rd.shape[1]
    D2 = np.diff(np.eye(K), 2, axis=0)
    Pen = np.zeros((X.shape[1], X.shape[1]))
    Pen[:K, :K] = lam * D2.T @ D2 + 1e-3 * np.eye(K)
    Pen[K : K + nr, K : K + nr] = 1e-4 * np.eye(nr)
    Pen[K + nr :, K + nr :] = 4.0 * np.eye(Pd.shape[1])
    g = np.linalg.solve(X.T @ X + Pen, X.T @ q.margin.values)[:K]
    tmax = float(t.max())
    return lambda d: float(np.clip(1 - np.abs(min(float((pd.Timestamp(d) - t0).days), tmax) - kn) / knot, 0, 1) @ g)


def common_trend_adjust(collected, asof):
    pool = pd.concat(
        [
            p[~p.get("_sub", pd.Series(False, index=p.index)).fillna(False).astype(bool)].assign(
                race=r["state"] + ("-sp" if r["special"] else "")
            )[["race", "pollster", "end_date", "margin"]]
            for r, p, _ in collected
            if not p.empty
        ],
        ignore_index=True,
    )
    ctr = common_trend(pool, asof)
    if ctr is None:
        return collected
    g_now = ctr(asof)
    out = []
    for r, p_raw, meta in collected:
        p = p_raw
        if not p.empty:
            p = p.copy()
            p["margin"] = p["margin"] + g_now - np.array([ctr(t) for t in p["end_date"]])
        out.append((r, p, meta))
    print(
        f"  Senate common trend: {len(pool)} polls; g(today) - g(30 days ago) = {g_now - ctr(pd.Timestamp(asof) - pd.Timedelta(days=30)):+.2f}"
    )
    return out


def run(E, asof=None, n=20000, seed=11, nat_z=None):
    asof = asof or pd.Timestamp.today().normalize()
    R = races()
    rows = []
    GL = state_loadings()
    # 35 seats up + the seats not up must make 100: a party label the parser misses (Minnesota's "DFL") would keep a seat in the
    # not-up count and add it again on a Democratic win (a 101-seat Senate)
    assert (NOW_D - (R["inc_party"] == "D").sum()) + (NOW_R - (R["inc_party"] == "R").sum()) + len(R) == 100, (
        "Senate seat bookkeeping does not add to 100"
    )
    collected = []
    for _, r in R.iterrows():
        meta = {"challenger": r["dem_nom"], "challenger_party": "D"}
        try:
            p, meta = W.senate_polls(
                r["state"],
                special=bool(r["special"]),
                dem=r["dem_nom"],
                rep=r["rep_nom"],
                ballot=r["ballot"] if isinstance(r["ballot"], list) else None,
                return_meta=True,
            )
        except Exception as e:
            p = pd.DataFrame()
            print(f"  {r['state']}: polls failed ({str(e)[:60]})")
        if not meta.get("challenger"):
            meta = {"challenger": r["dem_nom"], "challenger_party": "D"}
        if not p.empty:
            p = PO.apply(p, r["state"], "senate")  # exact figures for rounded scraped copies (manual exact=1)
        # hand-entered polls not yet in the scraped sources (data/manual/race_polls.csv) and the Bluesky / pollresults feeds. Their rows
        # carry no regular/special flag, so they go to the state's race when it has only one this year (this includes the OH and FL
        # specials, the only Senate races in those states)
        if (R["state"] == r["state"]).sum() == 1:
            rcv3 = RC.applies("senate", r["state"]) and isinstance(r["ballot"], list) and len(r["ballot"]) > 2
            man = M.manual_race_polls(
                r["state"],
                "senate",
                p if not p.empty else None,
                names=(meta.get("challenger") or r["dem_nom"], meta.get("republican") or r["rep_nom"]),
                final_exempt=rcv3,
            )
            if rcv3:  # ranked choice: feed rows
                man = RC.convert_rows(
                    man,
                    RC.other_type_of(
                        r["ballot"], (meta.get("challenger") or r["dem_nom"], meta.get("republican") or r["rep_nom"])
                    ),
                )
            if len(man):
                p = pd.concat([p, man], ignore_index=True) if not p.empty else man
            if rcv3 and not p.empty:
                p = RC.final_over_transfer(p)
        if not p.empty:
            p = p[
                ~p["dem_name"].str.contains("Generic", case=False) & ~p["rep_name"].str.contains("Generic", case=False)
            ]
            p = PC.apply(p, r["state"], "senate")  # known duplicates / sponsors (data/manual/poll_corrections.csv)
            p = M.collapse_versions(p.assign(_race=r["state"]), "_race").drop(columns="_race")  # one survey, one row
            p["grade"] = 1.5
            p["seat"] = r["state"]
            p["margin"] = p["margin"] + SEN_POLL_BIAS - M.state_poll_correction(statewide=True).get(r["state"], 0.0)
            p = M.prepare_race_polls(p, meta.get("challenger_party", "D"))
            if (R["state"] == r["state"]).sum() == 1:
                sub = M.substate_polls(r["state"], "senate", POLL_SD)
                if len(sub):
                    p = pd.concat([p, sub.assign(_sub=True)], ignore_index=True)
        collected.append((r, p, meta))
    if SEN_COMMON_TREND and collected:
        collected = common_trend_adjust(collected, asof)
    for r, p, meta in collected:
        pa = M.poll_average(p, asof) if not p.empty else pd.DataFrame()
        pm, ne, np_, vm = (
            (pa["poll_margin"].iloc[0], pa["n_eff"].iloc[0], int(pa["n"].iloc[0]), float(pa["vmult"].iloc[0]))
            if len(pa)
            else (np.nan, np.nan, 0, 1.0)
        )
        gl = GL.loc[r["state"]]
        mu_prior = SEN_CONST + NAT_SLOPE * E + B_LEAN * r["lean"] + C_INC * r["inc"] + gl["group_shift"]
        psd = PRIOR_SD
        prior_note = ""
        if meta.get("challenger_party", "D") != "D":
            dem_on_ballot = any(
                str(pp).startswith(("Dem", "DFL")) for _, pp in (r["ballot"] if isinstance(r["ballot"], list) else [])
            )
            if dem_on_ballot:
                psd = PRIOR_SD * SPLIT_PRIOR_MULT
                prior_note = "independent, Democrat also on the ballot: prior widened x2"
            else:
                mi, mr = ind_money(r["state"])
                funded = mi == mi and mr == mr and mi >= IND_MIN_MONEY and mi >= IND_MIN_RATIO * mr
                if funded:
                    mu_prior += IND_SHIFT
                    prior_note = (
                        f"funded independent, no Democrat: prior +{IND_SHIFT} (${mi / 1e6:.1f}M, {mi / mr:.2f}x)"
                    )
                else:
                    prior_note = (
                        f"independent, no Democrat, NOT funded (${mi / 1e6:.2f}M vs ${mr / 1e6:.2f}M): no shift"
                    )
                psd = float(np.hypot(PRIOR_SD, IND_SHIFT_SD))
        # ranked choice (midterms/rcv.py): polls are final-round margins; the transfer-rate uncertainty of converted first rounds is a
        # race-level poll error on top of race_sys()
        rcv_on = RC.applies("senate", r["state"])
        rsd = RC.race_sd(p, asof) if rcv_on and not p.empty else 0.0
        if M.ROBUST_NU and not p.empty:
            mu, sd = M.robust_blend(
                mu_prior, psd, p, asof, POLL_SD, float(np.hypot(race_sys(), rsd)), M.ROBUST_NU
            )  # robust Student-t race blend
        else:
            mu, sd = blend_race(mu_prior, psd, pm, ne, vm, rsd)
        # heating-oil shift is in generic-ballot points: it reaches a Senate race at NAT_SLOPE, as E does (governors use their own
        # slope b[3])
        mu = mu + NAT_SLOPE * M.heating_oil_shift("state").get(r["state"], 0.0) * (
            1.0 if meta.get("challenger_party", "D") in ("D", "I") else 0.0
        )
        rows.append(
            {
                **r.to_dict(),
                "challenger": meta["challenger"],
                "challenger_party": meta["challenger_party"],
                "republican": meta.get("republican"),
                "prior_note": prior_note,
                "poll_margin": pm,
                "n_eff": ne,
                "n_polls": np_,
                "mu_prior": mu_prior,
                "group_shift": gl["group_shift"],
                "h_load": gl["h_load"],
                "c_load": gl["c_load"],
                "a_load": gl["a_load"],
                "wnc_z": gl["wnc_z"],
                "mu": mu,
                "sd": sd,
                "newest_poll": (p["end_date"].max().date().isoformat() if not p.empty else None),
                "rcv": RC.NOTE if rcv_on else "",
                "rcv_sd": rsd,
            }
        )
    S = pd.DataFrame(rows)
    mg = simulate_senate(S, n=n, seed=seed, nat_z=nat_z)
    d = mg > 0
    d_up = (S["inc_party"] == "D").sum()
    r_up = (S["inc_party"] == "R").sum()
    seats, ind = seat_counts(S, d, d_up)
    S["p_dem"] = d.mean(0)
    out = {
        "p_r_lose": float((seats + ind >= 51).mean()),
        "ind_mean": float(ind.mean()),
        "E": E,
        "d_up": int(d_up),
        "r_up": int(r_up),
        "dem_seats_mean": float(seats.mean()),
        "p10": float(np.percentile(seats, 10)),
        "p50": float(np.median(seats)),
        "p90": float(np.percentile(seats, 90)),
        "p_dem_50plus": float((seats >= 50).mean()),
        "p_dem_51plus": float((seats >= 51).mean()),
    }
    return S, mg, out


if __name__ == "__main__":
    E = float(sys.argv[1]) if len(sys.argv) > 1 else 6.7
    S, mg, out = run(E)
    print(out)
    print(
        S.sort_values("p_dem")[
            [
                "state",
                "special",
                "incumbent",
                "inc",
                "cook_pvi",
                "dem_nom",
                "rep_nom",
                "n_polls",
                "poll_margin",
                "mu_prior",
                "mu",
                "sd",
                "p_dem",
                "rat_Cook",
            ]
        ].to_string()
    )
    S.to_csv("data/cache/senate2026_run.csv", index=False)
