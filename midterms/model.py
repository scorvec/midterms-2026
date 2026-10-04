"""Midterm model core: fundamentals prior, poll blend, correlated errors, Monte Carlo.

Design (see README): every seat has an expected Democratic two-party margin
    mu_i = k0 + b * lean_i + c * inc_i + E
with E the national environment (expected national House two-party margin), then a
poll blend where polls exist. The simulation draws
    margin_i = mu_i + eta_nat + eta_state[s(i)] + u_i * eta_urban + sum_g h_gi * eta_g + eps_i
(eta_g = Hispanic / Cuban / Asian group shocks on centred CVAP-share loadings, 2026 seat table only) with eta_nat ~ t5(0, s_nat), eta_state ~ t5(0, s_state) per state, eta_urban ~ t5(0, s_urban)
times a centred urbanization loading u_i, eps_i ~ t5(0, s_res). The national term is
additive and unit-slope, so the interactive slider (user: "a slider that can adjust the
national generic ballot mood") is a shift of every simulated margin, not a re-run.
"""
from __future__ import annotations
import re
import numpy as np, pandas as pd
from dataclasses import dataclass, field
from . import data_prep as D

# Poll error splits into a part every poll in a race shares (systematic) and a per-poll part. From
# 538 raw_polls, polls in the last 60 days, races with >= 3 polls (2014-2022):
#   Senate: within-race sd 4.0-4.3, systematic 3.5-6.1 (use 5.0);  House: within 5.4-6.9, systematic 2.9-5.3 (use 4.3)
# Averaging n polls only shrinks the within-race part; the systematic part is the floor.
HOUSE_POLL_SD = 5.6          # within-race, per poll
HOUSE_POLL_SYS = 4.3
SEN_POLL_SD = 4.2
SEN_POLL_SYS = 5.0
SPREAD_FREE = 6.0            # weighted sd of a race's polls that costs nothing (ordinary sampling + house effects)
SPREAD_SCALE = 6.0           # each further 6 pts of disagreement doubles the poll variance
PRIOR_SD = 6.7               # fundamentals residual, contested seats 2018+2022 (5.7 in |lean|<5)
HALF_LIFE = 21.0             # poll recency weight, days
T_DF = 5
# Movement split (midterms/early_vote.py, 2026-10-04): None = one national draw x s_nat (backtests, backfill). When set by
# early_vote.setup: {z: unit national movement draw, m: measured movement sd (national pts), k: {state: exposure factor}}; the
# national shock becomes z_rest x sqrt(s^2 - m^2) + z x m x k_state, so banked early votes damp a late swing state by state.
MOVE = None


@dataclass
class Params:
    k0: float = 1.1           # contested-margin intercept over the national House margin (2018: -0.36, 2022: +2.61)
    b_lean: float = 0.953
    c_inc: float = 4.33
    s_nat: float = 4.5        # 2026-09-30 user decision (4.1 -> 4.5, with BIAS -2). national-environment error at ~7 weeks (generic ballot -> result), pts. Was 2.9 until
                              # 2026-09-22: our own generic estimator missed by -1.6 / +3.3 / +5.5 / +5.1 at 50 days in
                              # 2018-24 (RMS 4.2; the two midterms alone 4.1). Live-path backtest 2018+2022 x 6 dates
                              # (backtest_all live): every score improves from 2.9 to 4.1 - PIT dev 0.198 -> 0.159,
                              # 80 % band coverage 10/12 -> 12/12, Brier 0.0382 -> 0.0379 - but that is two national draws.
    s_state: float = 2.5      # shared state error
    s_urban: float = 2.0      # urban/rural factor sd per unit loading (loading = urbanindex z-score)
    s_res: float = 5.0        # idiosyncratic race error; total race sd ~ sqrt(2.5^2+2^2+5^2) ~ 5.9 + national
    poll_sd: float = HOUSE_POLL_SD
    poll_sys: float = HOUSE_POLL_SYS
    # group factors (2026 only; seat tables without the loading columns skip them): sd in pts per unit of
    # centred CVAP share, so 15 = +/-1.5 pts per 10 pts of share at 1 sd. See run2026.GROUP_* and README.
    s_hisp: float = 15.0      # shared Hispanic shock (loading = Cuban-discounted Hispanic share, centred)
    s_cuban: float = 15.0     # Cuban-specific shock on top (Florida Cubans move on their own; FIU 2026)
    s_asian: float = 15.0     # shared Asian shock
    # regional Hispanic deviations (2026-10-04, user: the Latino swing "is likely not distributed evenly, likely to be larger
    # in Texas vs Florida" -> "Regional factors, neutral priors"): Texas, Florida (non-Cuban; Cubans keep c_load) and the
    # West (CA AZ NV NM CO) on top of the shared factor, prior MEAN 0 and sd 8, learned from the district polls by
    # factor_update. 2022 House test (research/hisp_region/hr_bt.py): -0.1 % log loss vs shared only, 5 of 6 dates - a wash
    # that costs nothing; on 2026-10-04 the polls put them at TX +3.5 (sd 6.4), FL +0.6 (7.4), West -4.2 (6.8).
    s_htx: float = 8.0
    s_hfl: float = 8.0
    s_hwest: float = 8.0
    # white non-college factor (2026-09-22): the Senate's measured statewide polling-miss factor (1.3 pts per state sd of
    # share = 11.4 per unit share, 1998-2022), zero mean. House check: 2022 seat errors ran +10.3 (se 3.7) per unit share
    # after the urban control - one draw of the same size. Loading = district share - national share.
    s_wnc: float = 11.4


def fit_prior(years=(2018, 2022), exclude=None):
    """OLS of contested margins on lean + incumbency with year intercepts; returns (b_lean, c_inc, {year: intercept}, resid sd)."""
    rows = []
    for y in years:
        if y == exclude: continue
        h = D.fec_house(y); m = h.merge(D.partisan_lean(y), on="seat"); m["year"] = y; rows.append(m)
    M = pd.concat(rows); C = M[~M["uncontested"] & M["margin"].notna()].copy()
    C["inc"] = C["inc_party"].map({"D": 1, "R": -1}).fillna(0)
    yrs = sorted(C["year"].unique()); X = np.column_stack([C["lean"], C["inc"]] + [(C["year"] == y).astype(float) for y in yrs])
    b, *_ = np.linalg.lstsq(X, C["margin"], rcond=None); res = C["margin"] - X @ b
    return b[0], b[1], {y: b[2 + i] for i, y in enumerate(yrs)}, float(res.std())


# ---- race-poll corrections (2026-09-18), measured on 538 raw_polls: House/Senate/Governor generals 1998-2022,
# last 21 days, 5,689 polls with the actual result. Sponsor effect is taken RELATIVE TO the non-partisan polls of
# the same race, so each race's own polling miss cancels:
#   D-sponsored +4.26 (se 0.30; midterms +4.38)   R-sponsored -4.07 (se 0.30; midterms -3.87)
#   scatter around the race's non-partisan mean: partisan sd 5.0 vs non-partisan 3.8  -> weight (3.8/5.0)^2
# Error by undecided share in the poll (non-partisan): sd 6.3-6.7 up to 12 %, 7.0 at 12-16, 8.1 at 16-25.
# Error by third-party share in the RESULT: abs 5.0-5.3 below 10 %, 6.6 above (x1.27).
# LV vs RV in race polls: 94 % of late polls are LV; within races polled both ways RV ran ~+1.2 D on 61 polls,
# sign flips 2018 v 2022 -> no adjustment (the generic-ballot fit does carry LV/RV/adult offsets).
# sponsor effects and per-pollster leans: data/cache/race_poll_calibration.json (python -m midterms.race_poll_calibration)
SPONSOR_WEIGHT = (3.8 / 5.0) ** 2
UND_FREE, UND_SLOPE, UND_CAP = 12.0, 0.02, 25.0    # sd x (1 + 0.02 per point of undecided above 12, to 25)
THIRD_MULT = 1.27


def sponsor_of(pollster, challenger_party="D"):
    """'D' / 'R' = poll sponsored by the challenger's side / the Republican's; '' = independent or bipartisan.
    Wikipedia marks sponsors in the pollster name: 'Public Policy Polling (D)'. A joint (R)/(D) poll is
    bipartisan. With an independent challenger, a Democratic or the independent's own poll is challenger-sponsored
    (Democrats back Osborn, Bengs, Achilles)."""
    tags = set(re.findall(r"\((D|R|I)\)", str(pollster)))
    if len(tags) != 1: return ""
    t = tags.pop()
    if challenger_party == "I": return "D" if t in ("D", "I") else "R"
    return t if t in ("D", "R") else ""


# Pollster experience (2026-09-23, prompted by a brand-new pollster putting Achilles +11 in Idaho). 538 raw_polls, ~8,300
# rated polls 1998-2022, error against the other polls of the same race by how many rated polls the pollster had before:
#   first poll 4.65 (variance x1.75), 1-4 prior 4.24 (x1.45), 5-19 3.85 (x1.20), 20+ 3.52-3.54 (x1.00).
# Experience = 538's number_polls_pollster_total + the pollster's polls in VoteHub's 2025-26 feeds (the ratings file stops in
# 2023, so Quantus / Bullfinch / Advanced Targeting Research would otherwise all read as new).
_EXP = None
_GENERIC_WORDS = {"the", "new", "public", "research", "university", "college", "center", "institute", "american", "national", "polling",
                  "strategies", "group", "associates", "insights", "news", "poll", "data", "survey", "opinion", "partners", "&"}


def _pnorm(s): return re.sub(r"[^a-z0-9 &/]", "", re.sub(r"\(.*?\)", "", str(s)).lower()).strip()


def _first_word(n):
    """The pollster's FIRST distinctive word ('advanced targeting research' -> 'advanced'); matching on any word let
    'targeting' tie Advanced Targeting Research to the DCCC Targeting Team."""
    ws = [w for w in _pnorm(n).replace("/", " ").split() if w not in _GENERIC_WORDS and len(w) > 2]
    return ws[0] if ws else ""


def _xnorm(s):
    """Experience-lookup key (2026-10-03): a leading 'The' and a space after '/' dropped - 'The New York Times/Siena
    University' (the pollresults.org spelling) had matched only by its first word and read as a 19-poll newcomer (x1.2
    variance) while 'New York Times/Siena University' read 230 - and that 230 was a first-word match on 'new', not Siena. Siena
    College renamed itself Siena University; 538's ratings file has 'The New York Times/Siena College' (120 polls). Only this
    lookup uses the key; other name-keyed tables keep _pnorm."""
    n = re.sub(r"^the ", "", re.sub(r"/\s+", "/", _pnorm(s)))
    for new, old in (("siena university", "siena college"), ("marist university", "marist college")):     # renamed schools
        n = n.replace(new, old)
    return n


def pollster_experience(name):
    global _EXP
    if _EXP is None:
        from pathlib import Path
        import json, collections
        R = Path(__file__).resolve().parents[1]
        full, first = collections.Counter(), collections.Counter()
        try:
            r = pd.read_csv(R / "data" / "raw" / "538repo" / "pollster-ratings-combined.csv")
            for n, k in zip(r.pollster, r.number_polls_pollster_total.fillna(0)):
                # 2026-10-02: 538 files some firms under a person with the brand in brackets ("Scott Rasmussen (Rasmussen
                # Reports)", 785 polls) - index the bracketed name too, or Rasmussen read as a 3-poll newcomer (x1.45 variance)
                import re as _re
                names = [n] + _re.findall(r"\(([^)]+)\)", str(n))
                for nn in names:
                    full[_xnorm(nn)] += int(k); w = _first_word(_xnorm(nn))
                    if w: first[w] = max(first[w], int(k))
        except Exception: pass
        for f in ("generic-ballot", "approval", "us-senator", "us-representative", "governor"):
            fp = R / "data" / "raw" / "votehub" / f"{f}.json"
            if not fp.exists(): continue
            for p in json.loads(fp.read_text()):
                n = _xnorm(p.get("pollster")); full[n] += 1; w = _first_word(n)
                if w: first[w] += 1
        _EXP = (full, first)
    full, first = _EXP; n = _xnorm(name)
    def one(k):
        if k in full: return full[k]
        w = _first_word(k); return first.get(w, 0) if w else 0
    # a joint poll ('KSTP/SurveyUSA', 'Catawba College/YouGov') has the experience of its most experienced partner (2026-10-03)
    parts = [x.strip() for x in n.split("/") if x.strip()] if "/" in n else []
    return max([one(n)] + [one(x) for x in parts])


def experience_mult(k):
    return 1.75 if k < 1 else (1.45 if k < 5 else (1.20 if k < 20 else 1.0))


QUALITY_FN = None       # name -> weight multiplier from the pollster's track record; None = off (2026-10-03 test)
CAL_OVERRIDE = None     # backtests: a calibration dict fitted on earlier cycles only (race_poll_calibration.calibration(before=year))


def prepare_race_polls(p: pd.DataFrame, challenger_party="D") -> pd.DataFrame:
    """Sponsor shift + weights and a variance multiplier (vmult) for undecideds and a strong third candidate."""
    import json
    from .race_poll_calibration import OUT as CAL_PATH, lean_for, main as build_cal
    if CAL_OVERRIDE is not None: cal = CAL_OVERRIDE
    else:
        if not CAL_PATH.exists(): build_cal()
        cal = json.loads(CAL_PATH.read_text())
    p = p.copy(); sp = p["pollster"].map(lambda x: sponsor_of(x, challenger_party)); p["sponsor"] = sp
    # expected lean of each poll: the pollster's own 2016-22 record where 538 has >= 5 late polls (shrunk toward
    # the tag's sponsor effect), else the sponsor effect of its (D)/(R) tag, else 0
    p["lean"] = [lean_for(pol, tag, cal) for pol, tag in zip(p["pollster"], sp)]
    p["margin_raw"] = p["margin"]; p["margin"] = p["margin"] - p["lean"]
    und = p["und"] if "und" in p else 100 - p["dem"] - p["rep"]
    m = 1 + UND_SLOPE * (und.clip(UND_FREE, UND_CAP) - UND_FREE)
    if "other" in p: m = m * np.where(p["other"].fillna(0) >= 10, THIRD_MULT, 1.0)
    p["vmult"] = m ** 2 * p["pollster"].map(lambda x: experience_mult(pollster_experience(x))).values
    g = p["grade"] if "grade" in p else pd.Series(1.5, index=p.index)
    p["grade"] = g * np.where(sp != "", SPONSOR_WEIGHT, 1.0) / p["vmult"]
    if QUALITY_FN is not None:                    # pollster track record (pollster_record.py)
        p["qmult"] = p["pollster"].map(QUALITY_FN).astype(float).values; p["grade"] = p["grade"] * p["qmult"]
    return p


# Poll staleness (2026-09-22, live backtest 2018+2022 x 6 dates at s_nat 4.1, log loss): old hard 60-d window with no age
# discount 0.1209; discount half-life 21 d 0.1211, 42 d 0.1205, 63 d 0.1201 (better Jul-mid Sep, a hair worse Oct-Nov).
AGE_DISCOUNT = True          # see poll_average
AGE_HALF = 63.0              # staleness half-life of a poll's precision against the prior (days)
POLL_WINDOW = 150
# Lead-dependent staleness (research hook, 2026-10-04; research/late_halflife/): when AGE_HALF_LATE is set, as-of dates on or
# after LATE_FROM (month, day) of their year use it in place of AGE_HALF in poll_average and robust_blend. None = live model.
AGE_HALF_LATE = None
LATE_FROM = (10, 1)


def age_half(asof):
    """Staleness half-life (days) in force at `asof`: AGE_HALF, or AGE_HALF_LATE from LATE_FROM on when that hook is set."""
    if AGE_HALF_LATE is None: return AGE_HALF
    a = pd.Timestamp(asof)
    return AGE_HALF_LATE if (a.month, a.day) >= LATE_FROM else AGE_HALF


# White non-college polling lean (2026-09-25, research/wnc_turnout_link.py; prompted by Lakshya Jain's note that white
# drop-off respondents barely swing while completers swing 10 left). Senate + governor generals 1998-2022: race error on the
# state's z-scored white non-college share has a NONZERO MEAN - polls too Democratic by 1.17 pts per state sd (inverse-var;
# cycle-scatter se 0.33), positive in 10 of 13 cycles, midterms +1.43 (6 of 7). It does NOT track the cycle's common miss
# (r +0.24, permutation p 0.44) or VEP turnout (r -0.08, p 0.79), so it is a fixed lean, not a turnout-scaled factor.
# Live Senate backtest (2018/20/22 x 6 dates, lean fitted on EARLIER cycles only: 0.86 / 1.12 / 1.21), on top of the
# persistence correction: Brier 0.0451 -> 0.0424, log loss 0.1539 -> 0.1481, better in 16 of 18 cases and every cycle.
# Statewide races only (Senate, governor): it was measured there; the House keeps the zero-mean WNC factor.
WNC_POLL_LEAN = 1.17
# 2026-10-03 (user: "I would turn it off to be consistent with the national poll"): no directional STATE corrections either -
# neither the persistent state miss nor the white non-college lean. Both stay measured (and tested: Senate log loss -2.7 % /
# a further -3.8 % out of sample); STATE_DIRECTION = True restores them. The state-level error is widened instead (senate2026).
STATE_DIRECTION = False


def state_poll_correction(year=2026, statewide=False):
    """{state: correction} to SUBTRACT from a race poll's D-R margin (research/state_poll_persistence.build_2026).
    State polling misses persist net of the national miss: next-cycle slope 0.24 on the previous cycle and 0.13 on the one
    before (538 raw_polls 1998-2022, 463 state-cycles). Out of sample it cut Senate log loss 2.7 % (2010-2022, better in
    2014/2018/2022, worse 2010, flat 2020) and House 0.9 % (2018 and 2022 both better). 2026 uses the 2024 misses
    (presidential + Senate polls, 538 archive) and 2022's (raw_polls)."""
    if not STATE_DIRECTION: return {}
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "data" / "cache" / f"state_poll_correction_{year}.csv"
    c = dict(zip(*pd.read_csv(f)[["state", "correction"]].values.T)) if f.exists() else {}
    if statewide and WNC_POLL_LEAN:
        from . import senate2026 as SN
        for st, z in SN.state_loadings()["wnc_z"].dropna().items(): c[st] = c.get(st, 0.0) + WNC_POLL_LEAN * float(z)
    return c


# Heating-oil price shock (2026-09-22, judgment adjustment, NOT backtested - no past cycle had a comparable shock).
# shift (D margin, against the president's party) = fuel-oil household share x dP x GAL x PTS_PER_DOLLAR x UNPRICED
#   dP              retail heating oil now minus a year ago (EIA weekly Maine residential No. 2 price, midterms/heating_oil.py)
#   GAL 650         gallons a year for a typical oil-heated home (New England)
#   PTS_PER_DOLLAR  0.78 generic pts per $1/gal of gasoline (-2.5 net approval per $1 x 0.31 generic pts per approval pt)
#                   spread over ~750 gal of household gasoline a year -> 0.00104 pts per $ of annual household cost
#   UNPRICED 0.5    most of the season's oil is delivered Dec-Mar; by Nov 3 voters have seen the fall fill and pre-buy
#                   prices, and fuel-price effects show up in polls within weeks - so half is taken as not yet in the polls
# Applied after the poll blend (a forward adjustment of the final margin), House by district share, Senate by state share.
HEAT_GAL, HEAT_PTS_PER_DOLLAR, HEAT_UNPRICED, PRESIDENT_PARTY = 650.0, 0.78 / 750.0, 0.5, "R"
# RETAIL inputs (heat_retail): "now" = the newest EIA weekly price on or before the run date, "yr_ago" = the EIA price a year
# before that week, "at_polls" = the price when the Maine polls were fielded (HEAT_POLLS_DATE): the rise after that date is
# wholly unpriced, the rise before it half-priced. Until 2026-10-04 these came from a hand-set Maine retail survey value moved
# by heating-oil futures; since then from EIA alone. EIA's residential survey runs October-March, so outside the season the
# last published week is held (heat_retail()["in_season"] False) - including for "at_polls" when the polls fell between seasons.
HEAT_POLLS_DATE = "2026-09-08"
HEAT_RETAIL = None   # set by backfill.run_asof for a past date (with HEAT_LIVE False); None = read the EIA series live


# State poll-error SIZE (2026-09-27): the systematic poll sd in robust_blend is multiplied per state by how large that state's
# past misses were (research/state_poll_persistence.state_sys_mult; cache state_sys_mult_2026.csv). STATE_SYS_OVERRIDE is the
# backtests' hook ({state: mult} fitted before the test year); None = the 2026 cache; {} = off.
# TESTED AND REJECTED 2026-09-27 (research/state_sys_backtest.py, walk-forward): Senate log loss +2.7 to +4.2 % at every k and
# in EVERY cycle (2018/2020/2022), House +0.05 to +0.55 %, governors -0.2 % (k 0.3) to +1.2 %. The past error size is real
# (slope 0.3 on raw_polls) but the robust blend's shared-miss term plus the persistence LEAN already carry it; widening
# poll_sys only hands weight back to the fundamentals prior in the states where polls were still the better guide. OFF.
STATE_SYS_OVERRIDE = None
STATE_SYS_ON = False


def state_sys_mult():
    if not STATE_SYS_ON: return {}
    if STATE_SYS_OVERRIDE is not None: return STATE_SYS_OVERRIDE
    global _STATE_SYS
    if "_STATE_SYS" not in globals():
        from pathlib import Path
        f = Path(__file__).resolve().parents[1] / "data" / "cache" / "state_sys_mult_2026.csv"
        _STATE_SYS = dict(zip(*pd.read_csv(f)[["state", "sys_mult"]].values.T)) if f.exists() else {}
    return _STATE_SYS


HEAT_LIVE = True     # False while backfill.run_asof sets HEAT_RETAIL for a past date itself


def heat_retail(asof=None):
    """{"now", "now_date", "yr_ago", "at_polls", "in_season", "source"} from the EIA weekly Maine residential heating-oil price
    as of `asof` (default today); None when the EIA series is unavailable (the adjustment is then off)."""
    if not HEAT_LIVE: return None if HEAT_RETAIL is None else dict(HEAT_RETAIL)
    from . import heating_oil as HO
    s = HO.series()
    if s is None or s.empty: return None
    D = pd.Timestamp(asof if asof is not None else pd.Timestamp.today()).normalize()
    now = HO.price_asof(s, D)
    if now is None: return None
    ago = HO.price_asof(s, now[0] - pd.Timedelta(weeks=52))       # the same survey week a year earlier (EIA weeks are Mondays)
    if ago is None: return None
    # the polls had priced everything up to their fieldwork; before that date "at_polls" is simply the price then
    at = HO.price_asof(s, min(D, pd.Timestamp(HEAT_POLLS_DATE))) if D >= pd.Timestamp(HEAT_POLLS_DATE) else now
    return {"now": round(now[1], 3), "now_date": str(now[0].date()), "yr_ago": round(ago[1], 3), "yr_ago_date": str(ago[0].date()),
            "at_polls": round(at[1], 3), "at_polls_date": str(at[0].date()), "in_season": bool((D - now[0]).days <= 10),
            "source": HO.LABEL}


def heating_oil_shift(level="seat"):
    """{seat or state: D-margin shift}. Positive helps the Democrat while the president is a Republican."""
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "data" / "static" / ("district_heat_oil_2026.csv" if level == "seat" else "state_heat_oil.csv")
    if not f.exists(): return {}
    d = pd.read_csv(f); sign = 1.0 if PRESIDENT_PARTY == "R" else -1.0
    R = heat_retail()
    if R is None: return {}
    dp = R["now"] - R["yr_ago"]
    if dp <= 0: return {}
    unpriced = (R["now"] - R["at_polls"]) / dp + HEAT_UNPRICED * (R["at_polls"] - R["yr_ago"]) / dp     # 0.20 + 0.5 x 0.80 = 0.60
    k = sign * max(dp, 0.0) * HEAT_GAL * HEAT_PTS_PER_DOLLAR * unpriced
    key = "seat" if level == "seat" else "state"
    return dict(zip(d[key], (d.sh_oil * k).round(3)))


# Sub-state polls (2026-09-22, user: "sole district polls ... can be very valuable given how massive some states are").
# A poll of one county / legislative / congressional district is projected statewide by UNIFORM SWING:
#     implied statewide margin = local poll margin - (area's 2024 presidential margin - state's 2024 presidential margin)
# and carries the area's typical DIFFERENTIAL swing as extra variance: counties of 300k-1M votes swung 2.9 (2020->24) to 4.0
# (2016->20) pts sd away from their state (county presidential results), so SUBSTATE_SD = 4.0. A LEAKED internal keeps the
# partisan-poll weight (0.58) but NOT the sponsor shift - the shift measures sponsors releasing flattering numbers, and a leak
# showing the sponsor losing is the opposite selection. Rows live in data/manual/substate_polls.csv.
SUBSTATE_SD = 4.0


def substate_polls(state, office, poll_sd):
    """Pseudo-polls (already 'prepared': margin, grade, vmult) for one statewide race."""
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "data" / "manual" / "substate_polls.csv"
    if not f.exists(): return pd.DataFrame()
    d = pd.read_csv(f, parse_dates=["end_date"]); d = d[(d.state == state) & (d.office == office)].copy()
    if d.empty: return d
    d["margin_raw"] = d.dem - d.rep
    d["margin"] = d.margin_raw - (d.geo_pres24_margin - d.state_pres24_margin)
    sp = np.where(d.sponsor.isin(["D", "R"]), SPONSOR_WEIGHT, 1.0)
    shift = np.where((d.leaked == 1) | ~d.sponsor.isin(["D", "R"]), 0.0, np.where(d.sponsor == "D", 4.26, -4.07))
    d["margin"] = d.margin - shift
    d["vmult"] = (poll_sd ** 2 + SUBSTATE_SD ** 2) / poll_sd ** 2
    d["grade"] = 1.5 * sp / d.vmult; d["seat"] = state; d["pollster"] = d.pollster + " [" + d.geo + ", projected statewide]"
    d["und"] = (100 - d.dem - d.rep).clip(lower=0); d["other"] = 0.0
    return d[["seat", "pollster", "end_date", "dem", "rep", "margin", "margin_raw", "und", "other", "grade", "vmult"]]


# Robust Bayesian race blend (2026-09-22, user: "discount surprising polls/trends unless there is more evidence").
# Per race: true margin theta ~ N(prior mu, prior sd); every poll shares a systematic miss delta ~ N(0, poll_sys); each poll
# i = theta + delta + e_i with e_i ~ Student-t(nu, s_i). A lone poll far from the prior and the other polls is mostly explained
# as a tail error of ITS OWN (discounted); several agreeing polls cannot all be tail errors, so agreement overrides the discount.
# s_i = poll_sd * sqrt(1 / q_i), q_i = (grade / 1.5) x age factor, where grade already carries sponsor weight / vmult - the same
# undecided / third-party / sponsor / staleness scaling as the Gaussian path, each applied once. Posterior of theta on a grid (delta integrated out on a second grid); returns its mean and sd.
# Backtest (2026-09-22), same data and corrections: Senate 2006-2022 log loss normal 0.1520 / nu 10 0.1493 / nu 5 0.1496 / nu 3 0.1500
# (better 2018, 2020, 2022; slightly worse 2006-14, when few polls reach the final weeks); House 2018+2022 0.1198 -> 0.1193 (nu 10).
# The same idea for the NATIONAL generic trend (generic.ROBUST_NU, IRLS) was a wash (RMSE 4.04 -> 4.04; midterms 3.91 -> 3.94) - the
# generic's misses are shared industry misses, not outlier polls - so it stays Gaussian.
ROBUST_AGE_HALF = None   # per-poll staleness half-life in the robust blend (None = AGE_HALF 63). Tested 2026-09-22: 21 d (the Gaussian path's recency ranking) Senate 0.1551 / House 0.1205, 35 d 0.1517, 63 d 0.1493 / 0.1193 - chasing recent polls loses.
ROBUST_NU = 10       # None = the Gaussian blend (poll_average + precision weights)


# Poll-by-poll record (2026-09-25, user: a page "that gives info on each poll and how it was weighted"). build_web sets RECORD to a
# list and RECORD_OFFICE before each chamber's run; robust_blend then appends what it did with every poll of every race. Off
# (None) everywhere else, so backtests and research runs are untouched.
RECORD = None
RECORD_OFFICE = ""


def _f0(v):
    try: v = float(v)
    except (TypeError, ValueError): return 0.0
    return v if np.isfinite(v) else 0.0


def _record(mu_prior, prior_sd, p, asof, age, q, s, y, th, de, ll, lp, post, nu, poll_sys, mu, sd):
    """Effective weights of the Student-t blend at the posterior: w_i = (nu+1)/(nu+r_i^2) / s_i^2 with r_i the poll's
    standardised residual from (theta + delta) at the posterior means; 'discount' = (nu+1)/(nu+r^2) (1 = taken at face value)."""
    joint = np.exp(ll + lp[:, None] - (ll + lp[:, None]).max()); joint /= joint.sum()
    dhat = float((joint.sum(0) * de).sum())
    r = (y - mu - dhat) / s; disc = (nu + 1) / (nu + r ** 2); w = disc / s ** 2
    pw = 1.0 / (1.0 / w.sum() + poll_sys ** 2); share_polls = pw / (pw + 1.0 / prior_sd ** 2)
    seat = str(p["seat"].iloc[0]) if "seat" in p else ""; corr = state_poll_correction(statewide=len(seat) == 2)
    rows = []
    for i, (_, x) in enumerate(p.iterrows()):
        pub = float(x["dem"] - x["rep"]) if pd.notna(x.get("dem")) and pd.notna(x.get("rep")) else None
        exp_k = pollster_experience(x["pollster"]) if "[" not in str(x["pollster"]) else None
        rows.append({"pollster": str(x["pollster"]), "end": pd.Timestamp(x["end_date"]).date().isoformat(), "age": int(age[i]),
                     "dem": None if pd.isna(x.get("dem")) else float(x["dem"]), "rep": None if pd.isna(x.get("rep")) else float(x["rep"]),
                     "und": None if pd.isna(x.get("und")) else float(x["und"]), "other": None if pd.isna(x.get("other")) else float(x.get("other") or 0),
                     "published": pub, "lean": _f0(x.get("lean")), "state_corr": float(corr.get(seat[:2], 0.0)) if "[" not in str(x["pollster"]) else 0.0,
                     "adjusted": float(y[i]), "sponsor": str(x.get("sponsor") or ""), "vmult": round(_f0(x.get("vmult")) or 1.0, 3),
                     "experience": exp_k, "precision": round(float(q[i]), 3), "sd": round(float(s[i]), 2),
                     "discount": round(float(disc[i] * nu / (nu + 1)), 3), "share": round(float(w[i] / w.sum()), 3)})   # 1 = at the consensus
    RECORD.append({"office": RECORD_OFFICE, "seat": seat, "asof": pd.Timestamp(asof).date().isoformat(), "prior": round(float(mu_prior), 2),
                   "prior_sd": round(float(prior_sd), 2), "posterior": round(mu, 2), "posterior_sd": round(sd, 2),
                   "polls_share": round(float(share_polls), 3), "shared_miss": round(dhat, 2), "polls": rows})


def robust_blend(mu_prior, prior_sd, polls, asof, poll_sd, poll_sys, nu):
    from scipy.stats import t as T, norm
    if polls is None or not len(polls): return float(mu_prior), float(prior_sd)
    asof = pd.Timestamp(asof); p = polls[(polls["end_date"] <= asof) & (polls["end_date"] > asof - pd.Timedelta(days=POLL_WINDOW))]
    if not len(p): return float(mu_prior), float(prior_sd)
    if "seat" in p and len(str(p["seat"].iloc[0])) >= 2: poll_sys = poll_sys * float(state_sys_mult().get(str(p["seat"].iloc[0])[:2], 1.0))
    age = (asof - p["end_date"]).dt.days.clip(lower=0).values
    q = (p["grade"].fillna(1.5).clip(0.05, 3.0).values / 1.5) * 0.5 ** (age / (ROBUST_AGE_HALF or age_half(asof)))
    # 2026-10-03: the variance multiplier enters ONCE. prepare_race_polls (and substate_polls) already divide `grade` by vmult,
    # so q carries 1/vmult; multiplying the variance by vmult again squared every penalty (a new pollster's x1.75 became x3.06,
    # a 25 %-undecided poll's x1.59 became x2.53). Live Senate backtest 2018-24 x 6 dates: log loss 0.1618 -> 0.1602, better
    # in 19/24 cases and in every cycle (cycle-block t 2.5); House 2018/22 a wash (0.1179 -> 0.1177).
    s = poll_sd * np.sqrt(1.0 / np.clip(q, 1e-3, None)); y = p["margin"].values.astype(float)
    th = np.arange(mu_prior - 5 * prior_sd - 20, mu_prior + 5 * prior_sd + 20, 0.25)
    de = np.linspace(-4 * poll_sys, 4 * poll_sys, 49)
    ll = np.zeros((len(th), len(de)))
    for yi, si in zip(y, s):
        ll += T.logpdf((yi - th[:, None] - de[None, :]) / si, nu) - np.log(si)
    ll += norm.logpdf(de, 0, poll_sys)[None, :]
    lp = norm.logpdf(th, mu_prior, prior_sd)
    m = ll.max(); like = np.log(np.exp(ll - m).sum(1)) + m
    post = lp + like; post = np.exp(post - post.max()); post /= post.sum()
    mu = float((th * post).sum()); sd = float(np.sqrt(((th - mu) ** 2 * post).sum()))
    if RECORD is not None:
        try: _record(mu_prior, prior_sd, p, asof, age, q, s, y, th, de, ll, lp, post, nu, poll_sys, mu, sd)
        except Exception as e: print("  poll record failed:", str(e)[:80])
    return mu, sd


def _pkey(p):
    """Pollster identity for duplicate checks: first word, lower case, sponsor tags dropped ('Advanced Targeting Research' ->
    'advanced', 'Marist Poll' / 'Marist' -> 'marist'). A second source often reports the same poll with leaners pushed or not
    (Idaho ATR 48-39 on Wikipedia, 50-39 on Bluesky), so same pollster + end date within 3 days = same poll, whatever the numbers."""
    w = re.sub(r"\(.*?\)", "", str(p)).strip().lower().replace("the ", "").split()
    return w[0] if w else ""


_PSTOP = {"university", "college", "polling", "poll", "research", "institute", "center", "survey", "surveys", "insights",
          "strategies", "strategic", "associates", "group", "partners", "consulting", "llc", "inc", "and", "of", "the", "for",
          "data", "news", "public", "opinion", "american", "national", "media", "new", "york", "times", "post", "daily", "co",
          "st", "saint", "texas", "politics"}   # 2026-10-02: 'St. Anselm' = 'Saint Anselm College'; 'Texas', 'Politics' are not identities


_PALIAS = {"fox": {"beacon", "shaw"}, "beacon": {"fox", "shaw"}, "shaw": {"fox", "beacon"},   # sponsor listed in place of its pollsters
           # 2026-10-02: one poll under two names across sources (each pair was in the model twice, identical numbers and dates)
           "pennlive": {"bravo"}, "bravo": {"pennlive"},                               # PennLive = sponsor of Bravo Group's PA polls
           "slingshot": {"tpor"}, "slightshot": {"slingshot", "tpor"}, "tpor": {"slingshot"},   # Texas Public Opinion Research = Slingshot
           "tsu": {"southern"}, "southern": {"tsu", "barbara"}, "barbara": {"southern", "tsu"},  # TSU / its Barbara Jordan center
           "uc": {"governmental"}}                                    # UC Berkeley IGS = University of California Berkeley IGS (not Citrin)


def _ptoks(p, first=False):
    """Distinctive pollster name tokens: sponsor tags dropped, split on any non-letter ('CNN/SSRS' -> {cnn, ssrs}).
    Each '/'-joined partner's FIRST distinctive token when first=True ('Data for Progress' -> progress)."""
    t = set()
    for part in re.sub(r"\(.*?\)", "", str(p)).lower().split("/"):
        w = [x for x in re.findall(r"[a-z0-9]+", part) if x not in _PSTOP and len(x) > 1]
        t |= set(w[:1] if first else w)
    return t | set().union(*[_PALIAS.get(x, set()) for x in t])


def _same_pollster(a, b):
    ka, kb = _pkey(a), _pkey(b)
    if ka and ka == kb and ka not in _PSTOP: return True          # a shared generic first word ('Data', 'New') is not an identity
    ta, tb = _ptoks(a), _ptoks(b)
    if (_ptoks(a, True) & tb) or (_ptoks(b, True) & ta): return True
    words = lambda p: re.findall(r"[a-z]+", re.sub(r"\(.*?\)", "", str(p)).lower())
    bare = lambda p: "".join(w for w in words(p) if w not in ("inc", "llc"))
    if bare(a) == bare(b): return True                            # 'AtlasIntel' = 'Atlas Intel', 'Research & Polling Inc.' = 'Research & Polling'
    def ini(p):                                          # 'UNH' = University of New Hampshire, 'DFP' = Data for Progress
        w = words(p); return {"".join(x[0] for x in w), "".join(x[0] for x in w if x not in ("of", "the", "and", "for"))} - {""}
    return any(len(i) >= 2 and i in tb for i in ini(a)) or any(len(i) >= 2 and i in ta for i in ini(b))


def _same_poll(a_pollster, a_end, a_margin, others):
    """Same poll = same pollster (name tokens overlap) and end dates within 3 days. Until 2026-09-23 a margin within 1.5
    ALSO counted, whatever the pollster - which merged different pollsters that happened to agree (GA governor: YouGov R+3
    9/17 dropped as a copy of Rasmussen R+3 9/14). The margin is no longer used: two sources report one poll with leaners
    pushed or not, so it cannot identify a poll anyway."""
    if others is None or not len(others): return False
    near = (pd.to_datetime(others["end_date"]) - pd.Timestamp(a_end)).abs() <= pd.Timedelta(days=3)
    same_p = others["pollster"].map(lambda o: _same_pollster(a_pollster, o))
    return bool((near & same_p).any())


def _sur5(n): return re.sub(r"[^a-z]", "", str(n).split("(")[0].strip().split()[-1].lower())[:5] if str(n).strip() else ""


def manual_race_polls(state, office, existing=None, names=None):
    """Hand-entered race polls (data/manual/race_polls.csv) that the scraped sources do not carry yet - e.g. a release read from
    the pollster's PDF on the day. Dropped automatically once a scraped copy exists (same state, end date within 3 days, margin
    within 1.5). Raw rows: the caller prepares them like any other poll."""
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "data" / "manual" / "race_polls.csv"
    if not f.exists(): return pd.DataFrame()
    d = pd.read_csv(f, parse_dates=["start_date", "end_date"]); d = d[(d.state == state) & (d.office == office)].copy()
    # second hand-off: the Polling USA Bluesky feed (midterms/bluesky_polls.py), after the hand-entered rows
    fb = Path(__file__).resolve().parents[1] / "data" / "state" / "bluesky_race_polls.csv"
    if fb.exists():
        b = pd.read_csv(fb, parse_dates=["end_date"]); b = b[(b.state == state) & (b.office == office)]
        if names:                                   # only the matchup actually on the ballot (surname, first five letters)
            want = {_sur5(names[0]), _sur5(names[1])}
            b = b[[{_sur5(x), _sur5(y)} == want for x, y in zip(b.dem_name, b.rep_name)]]
        if len(b):
            b = b.assign(start_date=pd.NaT)
            if len(d):                              # drop Bluesky copies of hand-entered polls
                dd = d.assign(margin=d.dem - d.rep); b = b[[not _same_poll(r.pollster, r.end_date, r.dem - r.rep, dd) for r in b.itertuples()]]
            d = pd.concat([d, b[[c for c in b.columns if c in d.columns or c in ("dem_name", "rep_name", "n", "other", "und")]]], ignore_index=True)
    if d.empty: return d
    d["margin"] = d.dem - d.rep; d["seat"] = state; d["grade"] = 1.5
    if existing is not None and len(existing):
        e = existing.copy(); e["end_date"] = pd.to_datetime(e["end_date"])
        if "margin" not in e: e["margin"] = e["dem"] - e["rep"]
        d = d[[not _same_poll(r.pollster, r.end_date, r.margin, e) for r in d.itertuples()]]
    return d[["seat", "pollster", "end_date", "n", "dem", "rep", "margin", "dem_name", "rep_name", "und", "other", "grade"]]


def collapse_versions(p: pd.DataFrame, race_col="seat") -> pd.DataFrame:
    """One survey, one row (2026-10-03). Within a race, rows whose end dates are within 3 days are versions of ONE survey when
    the pollster name is the same once sponsor tags are dropped (ID-1 'SurveyUSA' / 'SurveyUSA (I)' 7/14, SC 'Impact Research (D)'
    8/22 and 8/24, MI governor 'TIPP Insights' / 'TIPP Insights (R)' 5/23) or the D and R shares are IDENTICAL under two names
    (IL-4 'Independent Center Voice' 8/1 = 'The Bullfinch Group' 8/4). Versions are averaged; the tagged spelling and the latest
    end date are kept. Deliberately NOT the looser _same_pollster token match: 'Catawba College/YouGov' (NC 9/15) is a different
    survey from YouGov's own 9/18 poll."""
    if p is None or len(p) < 2 or race_col not in p: return p
    p = p.reset_index(drop=True).copy(); p["end_date"] = pd.to_datetime(p["end_date"])
    base = p["pollster"].astype(str).str.replace(r"\s*\((?:D|R|I)\)", "", regex=True).str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    parent = list(range(len(p)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for _, g in p.groupby(race_col).groups.items():
        g = list(g)
        for a_ in range(len(g)):
            for b_ in range(a_ + 1, len(g)):
                i, j = g[a_], g[b_]
                if abs((p.at[i, "end_date"] - p.at[j, "end_date"]).days) > 3: continue
                same_name = base[i] == base[j] and base[i] != ""
                same_nums = pd.notna(p.at[i, "dem"]) and p.at[i, "dem"] == p.at[j, "dem"] and p.at[i, "rep"] == p.at[j, "rep"]
                if same_name or same_nums: parent[find(i)] = find(j)
    grp = [find(i) for i in range(len(p))]
    if len(set(grp)) == len(p): return p
    p["_g"] = grp
    num = [c for c in ("dem", "rep", "margin", "und", "other") if c in p]
    agg = {c: "first" for c in p.columns if c not in ("_g",)}; agg.update({c: "mean" for c in num}); agg["end_date"] = "max"
    if "n" in p: agg["n"] = "max"
    agg["pollster"] = lambda x: max(x.astype(str), key=len)
    out = p.groupby("_g", sort=False).agg(agg).reset_index(drop=True)
    print(f"  versions collapsed: {len(p)} rows -> {len(out)} polls")
    return out


def poll_average(polls: pd.DataFrame, asof, window=None, half_life=HALF_LIFE, spread=True, age_discount=None):
    """Per seat: recency- and grade-weighted margin, effective n. polls: rows with seat, end_date, margin, grade."""
    window = POLL_WINDOW if window is None else window; age_discount = AGE_DISCOUNT if age_discount is None else age_discount
    asof = pd.Timestamp(asof); p = polls[(polls["end_date"] <= asof) & (polls["end_date"] > asof - pd.Timedelta(days=window))].copy()
    if p.empty: return pd.DataFrame(columns=["seat", "poll_margin", "n_eff", "n"])
    age = (asof - p["end_date"]).dt.days.clip(lower=0); w = 0.5 ** (age / half_life)
    g = p["grade"].fillna(1.5).clip(0.1, 3.0) / 2.0; w = w * g
    p["w"] = w; p["g"] = g; p["ga"] = g * 0.5 ** (age / age_half(asof))
    if "vmult" not in p: p["vmult"] = 1.0
    def agg(q):
        w = q["w"]; m = np.average(q["margin"], weights=w)
        # Polls that disagree with each other are worth less than their count suggests. Idaho 2026: -24, +0.5,
        # -34, +9 inside one 60-day window carried the race to 52 % for the independent (market 3 %). The spread
        # inflates the poll VARIANCE - the race falls back toward its fundamentals - rather than picking a side
        # (a trimmed/robust mean picks one, and with recency weights it picked the newest outlier: Idaho went to 80 %).
        sd = np.sqrt(np.average((q["margin"] - m) ** 2, weights=w)) if len(q) > 1 else 0.0
        mult = np.average(q["vmult"], weights=w) * (1 + (max(sd - SPREAD_FREE, 0.0) / SPREAD_SCALE) ** 2 if spread else 1.0)
        n_eff = w.sum() ** 2 / (w ** 2).sum()
        # 2026-09-22: n_eff is scale-free, so recency only ranked polls AGAINST EACH OTHER - a lone 59-day-old poll
        # counted as much against the prior as yesterday's, then vanished at day 61 (OH-10 10 -> 30 % overnight).
        # Discount by the grade-weighted mean age factor: a lone poll counts 0.5 at 21 days, 0.14 at 60, 0.02 at 120.
        if age_discount: n_eff = n_eff * q["ga"].sum() / q["g"].sum()
        return pd.Series({"poll_margin": m, "n_eff": n_eff, "n": len(q), "vmult": mult, "spread": sd})
    return p.groupby("seat")[["margin", "w", "g", "ga", "vmult"]].apply(agg).reset_index()


def national_env(asof, cycle, window=30):
    """Generic-ballot average (D-R) over the last `window` days -> expected national House two-party margin.
    Bias: the final generic average ran +0.1 (2018) and +2.1 (2022) too Democratic against the result; use -1."""
    g = D.polls("generic"); g = g[g["cycle"] == cycle]; asof = pd.Timestamp(asof)
    q = g[(g["end_date"] <= asof) & (g["end_date"] > asof - pd.Timedelta(days=window))]
    if q.empty: return np.nan, 0
    w = 0.5 ** (((asof - q["end_date"]).dt.days) / HALF_LIFE) * (q["grade"].fillna(1.5).clip(0.5, 3) / 2)
    return float(np.average(q["margin"], weights=w)) - 1.0, len(q)


def build_house(year, asof, P: Params, prior_years=(2018, 2022), loyo=True, E=None):
    """Seat table for the House: lean, incumbency, prior mu, poll blend, final mu (at the national E), plus factor loadings."""
    h = D.fec_house(year); pl = D.partisan_lean(year); u = D.urbanization() if year >= 2022 else None
    b, c, k, sd = fit_prior(prior_years, exclude=(year if loyo else None))
    if E is None: E, n_g = national_env(asof, year)
    m = h.merge(pl, on="seat", how="left")
    m["inc"] = m["inc_party"].map({"D": 1, "R": -1}).fillna(0)
    m["mu_prior"] = P.k0 + b * m["lean"] + c * m["inc"] + E
    hp = D.polls("house"); hp = hp[(hp["cycle"] == year) & (hp["stage"].astype(str).str.lower() == "general")]
    pa = poll_average(hp, asof); m = m.merge(pa, on="seat", how="left")
    # blend: local part only (the national E is shared); poll sd per race = poll_sd / sqrt(n_eff)
    poll_var = P.poll_sd ** 2 / m["n_eff"].fillna(1.0) + P.poll_sys ** 2                  # shared error does not average away
    wq = np.where(m["n_eff"].notna(), 1.0 / poll_var, 0.0); wp = 1.0 / (PRIOR_SD ** 2)
    m["mu"] = np.where(m["n_eff"].notna(), (wp * m["mu_prior"] + wq * m["poll_margin"].fillna(0)) / (wp + wq), m["mu_prior"])
    m["mu_local_sd"] = np.sqrt(1.0 / (wp + wq))
    if u is not None:
        m = m.merge(u[["seat", "urbanindex"]], on="seat", how="left"); m["u_load"] = (m["urbanindex"] - m["urbanindex"].mean()) / m["urbanindex"].std()
    else:
        m["u_load"] = 0.0
    m["E"] = E; m["b_lean"], m["c_inc"] = b, c
    return m


def house_blend(s: pd.DataFrame, E: float, P: Params, b: float, c: float, polls=None, asof=None, money=None, mcoef=None) -> pd.DataFrame:
    """THE House prior + poll blend, shared by the live run (run2026) and the backtest (backtest_all.live_house), so the
    two cannot drift apart again (until 2026-09-22 the backtest scored an older path with no money term, no race-poll
    corrections and no spread inflation). s: seat table with seat, lean, inc (and optionally group_shift);
    polls: race polls with seat, pollster, end_date, margin, dem, rep (raw - corrected here); money: seat, lr[, money_d, money_r];
    mcoef: money.coefficients(...) -> (b, c, m, lr0, delta)."""
    s = s.copy()
    s["mu_prior"] = P.k0 + b * s["lean"] + c * s["inc"] + E
    if money is not None and mcoef is not None:
        bm, cm, mm, lr0, delta = mcoef
        s = s.merge(money, on="seat", how="left"); has = s["lr"].notna()
        s.loc[has, "mu_prior"] = P.k0 + delta + bm * s.loc[has, "lean"] + cm * s.loc[has, "inc"] + mm * (s.loc[has, "lr"] - lr0) + E
    if "group_shift" in s: s["mu_prior"] = s["mu_prior"] + s["group_shift"].fillna(0)   # before the blend: race polls already see the swing
    pa = pd.DataFrame(columns=["seat", "poll_margin", "n_eff", "n", "vmult", "spread"])
    if polls is not None and len(polls):
        q = polls.copy(); q["grade"] = 1.5
        pq = prepare_race_polls(q); pa = poll_average(pq, asof)
    s = s.merge(pa, on="seat", how="left")
    poll_var = P.poll_sd ** 2 * s["vmult"].astype(float).fillna(1.0) / s["n_eff"].astype(float).fillna(1.0) + P.poll_sys ** 2
    has = s["n_eff"].notna().values
    wq = np.where(has, 1.0 / poll_var, 0.0); wp = 1.0 / (PRIOR_SD ** 2)
    s["mu"] = np.where(has, (wp * s["mu_prior"] + wq * s["poll_margin"].astype(float).fillna(0)) / (wp + wq), s["mu_prior"])
    s["mu_local_sd"] = np.sqrt(1.0 / (wp + wq)); s["E"] = E
    if ROBUST_NU and polls is not None and len(polls):
        for i in np.where(has)[0]:
            mu, sd = robust_blend(s["mu_prior"].iat[i], PRIOR_SD, pq[pq["seat"] == s["seat"].iat[i]], asof, P.poll_sd, P.poll_sys, ROBUST_NU)
            s.iat[i, s.columns.get_loc("mu")] = mu; s.iat[i, s.columns.get_loc("mu_local_sd")] = sd
    return s


FACTORS = (("h_load", "s_hisp"), ("c_load", "s_cuban"), ("a_load", "s_asian"), ("w_load", "s_wnc"), ("u_load", "s_urban"),
           ("h_tx", "s_htx"), ("h_fl", "s_hfl"), ("h_west", "s_hwest"))


def factor_update(s: pd.DataFrame, P: Params):
    """Condition the simulation's latent demographic factors on the district polls (2026-09-22).
    The model draws Hispanic / Cuban / Asian / white-non-college / urban factor shocks with known prior sds but let polls
    inform only their own seat. Here the poll-minus-prior residuals of the polled seats (national mean removed - that is the
    generic ballot's job) give a Gaussian posterior for the factors; every seat's prior moves by loading x posterior mean
    (leave-self-out for polled seats) and the simulation draws the factors from the posterior sds. No tuned parameters.
    Backtest: 2022 House (the only cycle with district demographics on its map) log loss 0.1008 -> 0.0957, Brier -8 %;
    recovered 2022 factors match the known swings (Hispanic and Asian seats toward R, urban toward R, WNC toward D).
    The Senate analogue (2006-2022, leave-one-state-out) was neutral (+0.6 % log loss): nearly every competitive Senate race
    is polled directly, so it is House only. Returns (shift per seat, {sd_attr: posterior sd})."""
    cols = [(c, a) for c, a in FACTORS if c in s and getattr(P, a) > 0]
    if not cols or "n_eff" not in s: return np.zeros(len(s)), {}
    L = np.column_stack([s[c].fillna(0).values for c, _ in cols]); prior = np.array([getattr(P, a) for _, a in cols]) ** 2
    pol = s["n_eff"].notna().values
    pv = P.poll_sd ** 2 * s["vmult"].astype(float).fillna(1).values / s["n_eff"].astype(float).fillna(1).values + P.poll_sys ** 2
    w = np.where(pol, 1 / (pv + PRIOR_SD ** 2), 0.0); r = np.where(pol, (s["poll_margin"] - s["mu_prior"]).astype(float).fillna(0).values, 0.0)
    if w.sum() == 0: return np.zeros(len(s)), {}
    r = r - np.sum(w * r) / np.sum(w)
    A = L.T @ (L * w[:, None]) + np.diag(1 / prior); f = np.linalg.solve(A, L.T @ (w * r))
    shift = L @ f
    for i in np.where(pol)[0]:                                     # leave-self-out for the seats whose polls built f
        wi = w.copy(); wi[i] = 0.0; Ai = L.T @ (L * wi[:, None]) + np.diag(1 / prior); shift[i] = L[i] @ np.linalg.solve(Ai, L.T @ (wi * r))
    post_sd = np.sqrt(np.diag(np.linalg.inv(A)))
    return shift, {a: float(sd) for (_, a), sd in zip(cols, post_sd)}, dict(zip([c for c, _ in cols], f))


def simulate(seats: pd.DataFrame, P: Params, n=20000, seed=1, nat_z=None):
    """→ margins [n, seats] with the correlated error structure. Uncontested seats are fixed at the winner."""
    rng = np.random.default_rng(seed); k = len(seats)
    t = lambda size, s: rng.standard_t(T_DF, size) * s * np.sqrt((T_DF - 2) / T_DF)
    # nat_z: a shared unit-sd t5 national draw (build_web passes the same one to the Senate, so the chambers move
    # together; before 2026-09-22 each drew its own and a joint-control probability would have been wrong)
    states = seats["state"].values; us = {s: i for i, s in enumerate(sorted(set(states)))}; si = np.array([us[s] for s in states])
    nat = national_shock(n, P.s_nat, states, nat_z, t)
    st = t((n, len(us)), P.s_state)[:, si]
    urb = t(n, P.s_urban)[:, None] * seats["u_load"].fillna(0).values[None, :]
    for col, sd in (("h_load", P.s_hisp), ("c_load", P.s_cuban), ("a_load", P.s_asian), ("w_load", P.s_wnc),
                    ("h_tx", P.s_htx), ("h_fl", P.s_hfl), ("h_west", P.s_hwest)):
        if col in seats and sd > 0:
            urb = urb + t(n, sd)[:, None] * seats[col].fillna(0).values[None, :]
    res = t((n, k), P.s_res) * np.where(seats["n_eff"].notna(), seats["mu_local_sd"].values / PRIOR_SD, 1.0)[None, :]
    mg = seats["mu"].values[None, :] + nat + st + urb + res
    fixed = seats["uncontested"].values
    if fixed.any():
        fx = np.where(seats["winner"].values == "D", 50.0, -50.0); mg[:, fixed] = fx[fixed]
    return mg


def summarize(mg, seats, shift=0.0):
    d = (mg + shift) > 0; ds = d.sum(1)
    return {"dem_seats_mean": float(ds.mean()), "dem_seats_p10": float(np.percentile(ds, 10)), "dem_seats_p50": float(np.median(ds)), "dem_seats_p90": float(np.percentile(ds, 90)),
            "p_dem_majority": float((ds >= 218).mean()), "seat_sd": float(ds.std()), "p_dem_seat": d.mean(0)}


def national_shock(n, s, states, nat_z=None, t=None):
    """[n, len(states)] national shock: one draw x s, or (MOVE set) the rest x sqrt(s^2 - m^2) + movement x m x k_state."""
    mv = MOVE
    if mv is not None and len(mv["z"]) < n: print(f"  !! MOVE draw has {len(mv['z'])} < {n} sims - movement split skipped"); mv = None
    if mv is None:
        return (t(n, s) if nat_z is None else np.asarray(nat_z) * s)[:, None]
    m = min(mv["m"], 0.95 * s); r = np.sqrt(s ** 2 - m ** 2)
    base = (t(n, r) if nat_z is None else np.asarray(nat_z) * r)[:, None]
    k = np.array([mv["k"].get(st, 1.0) for st in states], float)
    return base + np.asarray(mv["z"][:n])[:, None] * m * k[None, :]


def national_z(n, seed=5):
    """Unit-sd t5 national draw shared by the House and Senate simulations."""
    return np.random.default_rng(seed).standard_t(T_DF, n) * np.sqrt((T_DF - 2) / T_DF)
