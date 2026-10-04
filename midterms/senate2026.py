"""2026 Senate: races from the Wikipedia race table (nominees, incumbents, Cook PVI x2), polls from
each race page, prior refit on 326 races 2006-2024 (0.80 E, 0.76 lean, 10.4 inc, sd 13.4), poll blend, simulation.
    python -m midterms.senate2026 [E]
"""
import re, sys, numpy as np, pandas as pd
from . import poll_overrides as PO
from . import wiki_polls as W, model as M, rcv as RC
from .run2026 import GROUP_B_HISP, GROUP_B_ASIAN, CUBAN_WEIGHT, HISP_GROUPS

# Prior refit 2026-09-19 on 326 contested Senate races 2006-2024 (research/senate_prior_refit.py: MIT results, lean =
# mean of the two previous presidential margins vs the nation, national House vote from the year infoboxes). The old
# values (NAT_SLOPE 0.5, C_INC 9.52, no constant, sd 12.3) were fitted on 2018 + 2022 only and ran 1.8 pts too
# Republican leaving one cycle out; at E +8 they sat 6.7 pts right of the race polls. Midterm-only 0.70 / presidential
# 1.00 slopes bracket the pooled 0.80. The national shock (s_nat x NAT_SLOPE = 2.3) now also matches the measured
# shared statewide polling miss (2.1-2.5, research/senate_error_correlation.py).
SEN_CONST = 1.38
B_LEAN, C_INC, PRIOR_SD, NAT_SLOPE, POLL_SD, POLL_SYS = 0.761, 10.39, 13.4, 0.80, M.SEN_POLL_SD, M.SEN_POLL_SYS
# Midterm race polls earn more weight (2026-09-30, user: "it would make more sense to put more weight on the state polling").
# The shared per-race Senate polling miss is smaller in midterms: final-3-week non-partisan averages, competitive races 1998-2024,
# systematic part 4.1 midterm v 4.7 presidential (research/senate_poll_bias.py). Live Senate backtest, POLL_SYS x 0.8
# (research/midterm_swing_backtest.py: 2018-24 live harness; `old` = 2006-16 on raw_polls with the true E):
# log loss better in ALL FIVE midterms (2006 +0.0033, 2010 +0.0074, 2014 +0.0031, 2018 +0.0115, 2022 +0.0045; cycle-block t 3.8,
# 4 df, p ~0.02; Brier better in all five) and worse in 2016/2020/2024, so it is midterm-only. x0.6 scored better still in every
# midterm but 0.8 is the measured ratio (4.1 / 5.0), so the tuned edge of the grid was not taken. Governors (gov2026's simpler
# harness) were mixed (3/5 midterms) and keep 5.0. The backtests still pass M.SEN_POLL_SYS explicitly.
MIDTERM_POLL_SYS_K = 0.8
POLL_SYS_TOTAL = POLL_SYS * MIDTERM_POLL_SYS_K    # 2026 is a midterm: 5.0 -> 4.0 - the measured TOTAL race-average miss
POLL_SYS = POLL_SYS_TOTAL                         # (display / old callers; the live blend uses race_sys())


def race_sys():
    """Each race's own systematic poll error: the measured total race-average miss MINUS the shared statewide shock the simulation
    adds on top (SEN_S_NAT x NAT_SLOPE). 2026-10-03: the total (measured across races and cycles) already contains the cycle-wide
    common miss, so drawing the shared shock as well counted it twice. Live harness: removing it was better in both midterms (2018,
    2022), worse in presidential years - and the equivalent POLL_SYS x0.6 had scored better in all five midterms 2006-22."""
    return float(np.sqrt(max(POLL_SYS_TOTAL ** 2 - (SEN_S_NAT * NAT_SLOPE) ** 2, 1.5 ** 2)))
SEN_POLL_BIAS = 0.0   # 2026-09-16: the pro-D Senate poll miss is a PRESIDENTIAL-year (Trump on the ballot) phenomenon: raw_polls last-21-day mean error 2020 +4.8, 2018 +0.8, 2022 -0.9. Midterm-only backtest (2018+2022): PIT dev 0.12 at 0 vs 0.215 at -2, Brier 0.0406 vs 0.0397 - a wash on Brier, much better calibrated at 0. The 2020-dominated grid that gave -2 does not describe a midterm.
NOW_D, NOW_R = 47, 53                      # current Senate (independents with the Democrats)


# Independent challengers (2026-09-18). The prior is fitted on D-v-R races only. Every serious, Democratic-backed
# independent since 1976 (MIT results, Cook-style lean from the two previous presidential races) BEAT that prior:
#   KS 2014 Orman +20.0, UT 2022 McMullin +17.5, NE 2024 Osborn +23.4 -> mean 20.3, sd 3.0
# (contrast: AR 2020 Harrington, a little-known Libertarian after the Democrat withdrew, -3.4).
# So with no Democrat on the ballot the prior mean moves by IND_SHIFT and its sd takes the spread; with a Democrat
# also on the ballot (a split anti-Republican vote, MT) there is no precedent and the prior is widened instead.
# Re-examined 2026-09-20 (research/independents.py, every 1976-2024 Senate race whose top non-Republican was not a
# Democrat). Against an R-incumbent prior the class splits by whether the independent FUNDED a campaign, not by
# being an independent: Osborn 2024 +24.8 (June 30 money $1.58M, 1.00x the Republican), McMullin 2022 +20.6
# ($3.25M, 0.85x), Orman 2014 +21.3 - but Harrington AR 2020 -4.0 ($0.00M), O'Hara MS 2002 -43.7, Spannaus VA 1990
# -53.1. The old +20.3 averaged the three successes only, which is the selection: they are famous BECAUSE they
# overperformed. Now: the shift applies only to a funded independent (>= $1M and >= 25 % of the Republican's June 30
# money, which every historical success clears and the flops fail) and is shrunk n/(n+k) for n=3 precedents, with the
# sd widened to carry the class's real spread. An unfunded independent gets the plain D-v-R prior, widened.
IND_SHIFT, IND_SHIFT_SD, SPLIT_PRIOR_MULT = 12.0, 9.0, 2.0
IND_MIN_MONEY, IND_MIN_RATIO = 1e6, 0.25
# The Senate keeps 2.9 x NAT_SLOPE = 2.3: in the Senate the shared term is calibrated to the measured shared statewide
# POLLING miss (2.1-2.5, research/senate_error_correlation.py), and the polled races that decide the chamber are ~85 %
# poll. The House's wider 4.1 (2026-09-22) is the environment error that its mostly unpolled seats carry in full.
SEN_S_NAT = 3.2     # the shared statewide shock in the BLEND's error split (race_sys); the simulation uses STATE_SHARED_MISS
STATE_SHARED_MISS = 4.2     # 2026-10-03: shared statewide miss, pts of margin, about ZERO (no direction): RMS of the cycle-mean
                            # Senate miss of the model's own race means 2018-24 (leak-free harness, no directional corrections)
RACE_EXTRA = 0.0            # extra race-level outcome spread on top of the blend's posterior sd (set below from the harness)
# 2026-09-30: scaled with the House s_nat 4.1 -> 4.5 (user decision), was 2.9
WNC_SD = 1.3        # white non-college polling-miss factor, pts per 1 sd of the state's share
# Appointed incumbents (2026-09-22): 12 appointees on a general ballot 2006-2022 beat an OPEN-seat prior by +6.3
# (median +3.8, se 3.1; Menendez -8, McSally -6 ... Gillibrand +23, Barrasso +26) against C_INC 10.4 for elected
# incumbents. The refit coded them as open (predecessor = winner 6 years earlier, specials skipped), yet the live
# model gave Husted and Moody the full 10.4. Now 0.6 x C_INC.
APPOINTEE_INC = 0.6
SPECIAL_INC_PARTY = {"OH": "R", "FL": "R"}      # 2025 appointees: Husted (Vance's seat), Moody (Rubio's seat)


def races():
    t = pd.read_csv("data/raw/wiki/senate2026_races.csv"); R = pd.read_csv("data/cache/senate2026_races.csv")
    from .data_prep import _ST
    noms = {}
    for _, r in t.iterrows():
        st = _ST.get(re.sub(r"\s*\(.*?\)", "", str(r[t.columns[0]])).strip()); c = str(r[t.columns[-1]])
        dem = re.findall(r"▌([^▌\[]+?)\s*\((?:Democratic|DFL)\)", c); rep = re.findall(r"▌([^▌\[]+?)\s*\(Republican\)", c)
        inc, ip = str(r[t.columns[2]]), str(r[t.columns[3]])
        if ip.startswith("Republican") and any(inc.split()[-1] in x for x in rep): rep = [x for x in rep if inc.split()[-1] in x] + rep
        if (ip.startswith("Democratic") or ip.startswith("DFL")) and any(inc.split()[-1] in x for x in dem): dem = [x for x in dem if inc.split()[-1] in x] + dem
        key = (st, "special" in str(r[t.columns[0]]).lower())
        ballot = [(n.strip(), p) for n, p in re.findall(r"▌([^▌\[]+?)\s*\((Democratic|DFL|Republican|Independent|Libertarian|Green|[^)]+)\)", c)]
        noms[key] = {"dem_nom": dem[0].strip() if dem else None, "rep_nom": rep[0].strip() if rep else None, "inc_party": "D" if (ip.startswith("Democratic") or ip.startswith("DFL")) else ("R" if ip.startswith("Republican") else "O"), "status": str(r[t.columns[6]]), "ballot": ballot}
    R["key"] = list(zip(R["state"], R["special"]))
    for k in ("dem_nom", "rep_nom", "inc_party", "status", "ballot"): R[k] = R["key"].map(lambda x: (noms.get(x) or {}).get(k))
    # the specials are not in the race table: nominees from the race page's infobox; both seats are held by
    # Republican appointees who are on the ballot (Husted for Vance, OH; Moody for Rubio, FL)
    for i, r in R[R["special"]].iterrows():
        try: bx = W.infobox_nominees(W.fetch(f"2026 United States Senate special election in {W.STATES[r['state']]}"))
        except Exception: bx = []
        if bx:
            R.at[i, "ballot"] = bx
            R.at[i, "dem_nom"] = next((n for n, p in bx if p.startswith("Dem")), None); R.at[i, "rep_nom"] = next((n for n, p in bx if p.startswith("Rep")), None)
        R.at[i, "inc_party"] = SPECIAL_INC_PARTY.get(r["state"], r["inc_party"])
        R.at[i, "status"] = "appointed incumbent running" if R.at[i, "rep_nom"] and str(r["incumbent"]).split()[-1] in str(R.at[i, "rep_nom"]) else r["status"]
    # lean on the SAME definition B_LEAN was fitted on (research/senate_prior_refit: mean of the two previous presidential margins
    # vs the nation, 50/50, MIT returns) - 2 x Cook PVI weights 2024 at 75 % and is rounded to whole points (2026-10-03: rms 1.0,
    # up to 1.9 pts off; NC, OR, OK, NE, MN). Cook stays the fallback.
    from .gov2026 import _pres_lean
    pl = _pres_lean(); R["lean_cook"] = R["lean"]
    R["lean"] = [pl(2026, st) if pl(2026, st) == pl(2026, st) else lc for st, lc in zip(R["state"], R["lean_cook"])]
    R["inc"] = np.where(R["inc_retiring"] | R["status"].astype(str).str.contains("retiring|lost renomination|resign", case=False), 0, R["inc_party"].map({"D": 1, "R": -1}).fillna(0)).astype(float)
    R.loc[R["status"].astype(str).str.contains("appointed incumbent running"), "inc"] *= APPOINTEE_INC
    return R.drop(columns="key")


def state_loadings():
    """Hispanic (Cubans at CUBAN_WEIGHT), Cuban and Asian CVAP shares per state, centred on the national shares -
    the same term as the House (run2026.group_loadings; evidence in research/offyear2025_hispanic.py, where the
    NJ/VA governor and CA Prop 50 swings were STATEWIDE races), scaled by NAT_SLOPE: the centring offset that a
    low-Hispanic state pays exists only to hold the national environment fixed, and E itself reaches a Senate race
    at 0.8 strength (candidate-driven races), so the whole term - mean and shocks - is damped the same way."""
    g = pd.read_csv("data/static/state_groups.csv").set_index("state")
    g["h_eff"] = g[["sh_" + x for x in HISP_GROUPS]].sum(axis=1) + CUBAN_WEIGHT * g["sh_cuban"]
    out = pd.DataFrame({"h_load": g["h_eff"] - g.loc["US", "h_eff"], "c_load": g["sh_cuban"] - g.loc["US", "sh_cuban"],
                        "a_load": g["sh_asian"] - g.loc["US", "sh_asian"]}).drop(index="US")
    out["group_shift"] = NAT_SLOPE * (GROUP_B_HISP * out["h_load"] + GROUP_B_ASIAN * out["a_load"])
    st = g.drop(index="US"); out["wnc_z"] = ((st["sh_wnc"] - st["sh_wnc"].mean()) / st["sh_wnc"].std()).reindex(out.index)
    return out[["h_load", "c_load", "a_load", "group_shift", "wnc_z"]] * pd.Series({"h_load": NAT_SLOPE, "c_load": NAT_SLOPE, "a_load": NAT_SLOPE, "group_shift": 1.0, "wnc_z": 1.0})


def ind_money(state):
    """(independent challenger's, Republican's) individual + self money through June 30 2026
    (research/senate_money.money_2026 -> data/cache/fec_senate_june30_2026.csv)."""
    try: m = pd.read_csv("data/static/fec_senate_june30_2026.csv")
    except FileNotFoundError: return float("nan"), float("nan")
    q = m[(m.state == state) & (~m.special.astype(bool))]
    gi = q[q.side == "C"].money; gr = q[q.side == "R"].money
    return (float(gi.iloc[0]) if len(gi) else float("nan"), float(gr.iloc[0]) if len(gr) else float("nan"))


def blend_race(mu_prior, psd, pm, ne, vm, extra_sys=0.0):
    """Precision blend of one race's prior with its poll average (shared by run() and backtest_all.live_senate). extra_sys: a further
    race-level poll error added in quadrature (the ranked-choice transfer uncertainty, rcv.race_sd)."""
    wp, wq = 1 / psd ** 2, (1.0 / (POLL_SD ** 2 * vm / ne + race_sys() ** 2 + extra_sys ** 2) if ne == ne else 0.0)
    return (wp * mu_prior + wq * (pm if pm == pm else 0)) / (wp + wq), float(np.sqrt(1 / (wp + wq)))


def simulate_senate(S, n=20000, seed=11, nat_z=None):
    """Margins [n, races]: shared national shock x NAT_SLOPE, group factors, white non-college factor, race residual.
    Columns used: mu, sd, h_load/c_load/a_load (0 if absent), wnc_z."""
    rng = np.random.default_rng(seed); t = lambda size, s: rng.standard_t(M.T_DF, size) * s * np.sqrt((M.T_DF - 2) / M.T_DF)
    el = S["elast"].fillna(1.0).values[None, :] if "elast" in S else 1.0     # per-state sensitivity to the national shock
    s_sh = STATE_SHARED_MISS / NAT_SLOPE                                  # the statewide shock about zero (pts of margin / slope)
    nat = M.national_shock(n, s_sh, S["state"].values, nat_z, t) * NAT_SLOPE * el     # movement split: model.MOVE
    res = t((n, len(S)), 1.0) * np.sqrt(S["sd"].values ** 2 + RACE_EXTRA ** 2)[None, :]
    P = M.Params(); grp = 0.0
    for col, sd in (("h_load", P.s_hisp), ("c_load", P.s_cuban), ("a_load", P.s_asian)):       # same group shocks as the House
        if sd > 0 and col in S: grp = grp + t(n, sd)[:, None] * S[col].fillna(0).values[None, :]
    # correlated polling-miss factor across states: white non-college share (z over the 50 states). Measured on 538
    # raw_polls Senate+governor 1998-2022 (research/senate_error_correlation.py): per-cycle slope sd 1.3 pts per 1 sd;
    # zero-mean on purpose (2010-2020 leaned one way, 2022 did not; user 2026-09-19: no fudge factors)
    grp = grp + t(n, WNC_SD)[:, None] * S["wnc_z"].fillna(0).values[None, :]
    return S["mu"].values[None, :] + nat + grp + res


def seat_counts(S, d, d_up):
    """(Democratic seats, independent-challenger wins) per simulation.  A win by an independent challenger
    (NE Osborn, ID Achilles, SD Bengs, MT Bodnar) is NOT counted as a Democratic seat: none has said he would
    caucus with the Democrats, and Osborn has said he would caucus with neither party.  It does cost the
    Republicans the seat, which is what P(R loses the majority) counts."""
    isI = (S["challenger_party"].fillna("D") != "D").values
    return (NOW_D - d_up) + d[:, ~isI].sum(1), d[:, isI].sum(1)


def d_controls(seats, ind, join="abstain"):
    """Per simulation: do the Democrats organise the Senate? Kalshi's CONTROLS-2026 settles on the party of the president
    pro tempore on 2027-02-01 (2026-09-26), so there is always a winner. Pro tem is elected by a majority of senators voting
    and the Vice President (a Republican) breaks a tie, so the Democrats need strictly more votes than the Republicans.
    `join`: what the independent challengers do ('D', 'R', or 'abstain' - Osborn's stated line; he is ~75 % of the cases
    where independents hold the balance). 50 D - 49 R - 1 I is the case that turns on it."""
    r = 100 - seats - ind
    return (seats + (ind if join == "D" else 0)) > (r + (ind if join == "R" else 0))


def run(E, asof=None, n=20000, seed=11, nat_z=None):
    asof = asof or pd.Timestamp.today().normalize()
    R = races(); rows = []; GL = state_loadings()
    # 35 seats up + the seats not up must make 100: Minnesota's "DFL" label once dropped it from the Democratic
    # seats up, so the not-up count kept it AND a Democratic win added it again (a 101-seat Senate, D +1 in every run)
    assert (NOW_D - (R["inc_party"] == "D").sum()) + (NOW_R - (R["inc_party"] == "R").sum()) + len(R) == 100, "Senate seat bookkeeping does not add to 100"
    for _, r in R.iterrows():
        meta = {"challenger": r["dem_nom"], "challenger_party": "D"}
        try: p, meta = W.senate_polls(r["state"], special=bool(r["special"]), dem=r["dem_nom"], rep=r["rep_nom"], ballot=r["ballot"] if isinstance(r["ballot"], list) else None, return_meta=True)
        except Exception as e: p = pd.DataFrame(); print(f"  {r['state']}: polls failed ({str(e)[:60]})")
        if not meta.get("challenger"): meta = {"challenger": r["dem_nom"], "challenger_party": "D"}
        if not p.empty: p = PO.apply(p, r["state"], "senate")      # exact figures for rounded scraped copies (manual exact=1)
        # hand-entered polls not yet in the scraped sources (data/manual/race_polls.csv) and the Bluesky / pollresults feeds. Their rows
        # carry no regular/special flag, so they go to the state's race when it has only ONE this year - 2026-10-03: the OH and FL
        # specials (the only Senate races there) had been skipped entirely, so a feed-only poll of either never reached the model
        if (R["state"] == r["state"]).sum() == 1:
            man = M.manual_race_polls(r["state"], "senate", p if not p.empty else None, names=(meta.get("challenger") or r["dem_nom"], meta.get("republican") or r["rep_nom"]))
            if RC.applies("senate", r["state"]) and isinstance(r["ballot"], list) and len(r["ballot"]) > 2:     # ranked choice: feed rows
                man = RC.convert_rows(man, RC.other_type_of(r["ballot"], (meta.get("challenger") or r["dem_nom"], meta.get("republican") or r["rep_nom"])))
            if len(man): p = pd.concat([p, man], ignore_index=True) if not p.empty else man
        if not p.empty:
            p = p[~p["dem_name"].str.contains("Generic", case=False) & ~p["rep_name"].str.contains("Generic", case=False)]
            p = M.collapse_versions(p.assign(_race=r["state"]), "_race").drop(columns="_race")      # one survey, one row
            p["grade"] = 1.5; p["seat"] = r["state"]; p["margin"] = p["margin"] + SEN_POLL_BIAS - M.state_poll_correction(statewide=True).get(r["state"], 0.0)
            p = M.prepare_race_polls(p, meta.get("challenger_party", "D"))
            if (R["state"] == r["state"]).sum() == 1:
                sub = M.substate_polls(r["state"], "senate", POLL_SD)
                if len(sub): p = pd.concat([p, sub], ignore_index=True)
        pa = M.poll_average(p, asof) if not p.empty else pd.DataFrame()
        pm, ne, np_, vm = (pa["poll_margin"].iloc[0], pa["n_eff"].iloc[0], int(pa["n"].iloc[0]), float(pa["vmult"].iloc[0])) if len(pa) else (np.nan, np.nan, 0, 1.0)
        gl = GL.loc[r["state"]]
        mu_prior = SEN_CONST + NAT_SLOPE * E + B_LEAN * r["lean"] + C_INC * r["inc"] + gl["group_shift"]; psd = PRIOR_SD; prior_note = ""
        if meta.get("challenger_party", "D") != "D":
            dem_on_ballot = any(str(pp).startswith(("Dem", "DFL")) for _, pp in (r["ballot"] if isinstance(r["ballot"], list) else []))
            if dem_on_ballot: psd = PRIOR_SD * SPLIT_PRIOR_MULT; prior_note = "independent, Democrat also on the ballot: prior widened x2"
            else:
                mi, mr = ind_money(r["state"])
                funded = mi == mi and mr == mr and mi >= IND_MIN_MONEY and mi >= IND_MIN_RATIO * mr
                if funded:
                    mu_prior += IND_SHIFT; prior_note = f"funded independent, no Democrat: prior +{IND_SHIFT} (${mi/1e6:.1f}M, {mi/mr:.2f}x)"
                else:
                    prior_note = f"independent, no Democrat, NOT funded (${mi/1e6:.2f}M vs ${mr/1e6:.2f}M): no shift"
                psd = float(np.hypot(PRIOR_SD, IND_SHIFT_SD))
        # ranked choice (midterms/rcv.py): polls are final-round margins; the transfer-rate uncertainty of converted first rounds is a
        # race-level poll error on top of race_sys()
        rcv_on = RC.applies("senate", r["state"]); rsd = RC.race_sd(p, asof) if rcv_on and not p.empty else 0.0
        if M.ROBUST_NU and not p.empty: mu, sd = M.robust_blend(mu_prior, psd, p, asof, POLL_SD, float(np.hypot(race_sys(), rsd)), M.ROBUST_NU)   # robust Student-t race blend
        else: mu, sd = blend_race(mu_prior, psd, pm, ne, vm, rsd)
        # heating-oil shift is in generic-ballot points: it reaches a Senate race at NAT_SLOPE, as E does (governors use their own
        # slope b[3]; until 2026-10-03 the Senate took it at full strength)
        mu = mu + NAT_SLOPE * M.heating_oil_shift("state").get(r["state"], 0.0) * (1.0 if meta.get("challenger_party", "D") in ("D", "I") else 0.0)
        rows.append({**r.to_dict(), "challenger": meta["challenger"], "challenger_party": meta["challenger_party"], "republican": meta.get("republican"), "prior_note": prior_note, "poll_margin": pm, "n_eff": ne, "n_polls": np_, "mu_prior": mu_prior, "group_shift": gl["group_shift"], "h_load": gl["h_load"], "c_load": gl["c_load"], "a_load": gl["a_load"], "wnc_z": gl["wnc_z"], "mu": mu, "sd": sd, "newest_poll": (p["end_date"].max().date().isoformat() if not p.empty else None),
                     "rcv": RC.NOTE if rcv_on else "", "rcv_sd": rsd})
    S = pd.DataFrame(rows)
    mg = simulate_senate(S, n=n, seed=seed, nat_z=nat_z); d = mg > 0
    d_up = (S["inc_party"] == "D").sum(); r_up = (S["inc_party"] == "R").sum()
    seats, ind = seat_counts(S, d, d_up)
    S["p_dem"] = d.mean(0)
    out = {"p_r_lose": float((seats + ind >= 51).mean()), "ind_mean": float(ind.mean()), "E": E, "d_up": int(d_up), "r_up": int(r_up), "dem_seats_mean": float(seats.mean()), "p10": float(np.percentile(seats, 10)), "p50": float(np.median(seats)), "p90": float(np.percentile(seats, 90)), "p_dem_50plus": float((seats >= 50).mean()), "p_dem_51plus": float((seats >= 51).mean())}
    return S, mg, out


if __name__ == "__main__":
    E = float(sys.argv[1]) if len(sys.argv) > 1 else 6.7
    S, mg, out = run(E)
    print(out)
    print(S.sort_values("p_dem")[["state", "special", "incumbent", "inc", "cook_pvi", "dem_nom", "rep_nom", "n_polls", "poll_margin", "mu_prior", "mu", "sd", "p_dem", "rat_Cook"]].to_string())
    S.to_csv("data/cache/senate2026_run.csv", index=False)
