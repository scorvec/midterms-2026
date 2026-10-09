"""Export the model for the forecast pages: web/data/model.json (+ polls.json, whatif.json).
House and Senate simulated once; every slider position is a shift of the simulated margins
(unit slope for the House, NAT_SLOPE for the Senate), so per-seat probabilities and seat
distributions are precomputed on a grid of national-mood values and the page needs no server.
Why a shift and not a re-run (checked 2026-09-19): a re-run at another E moves only the fundamentals prior and keeps
today's race polls fixed, so polled seats move by about half as much (House at D+4: re-run 224.3 seats vs shift
221.1). But "what if the mood is D+4" means the race polls are also ~4 pts too Democratic - the simulation's own
national shock moves every seat one for one (x NAT_SLOPE in the Senate). The shift is the consistent counterfactual.
    python -m midterms.build_web
"""
from pathlib import Path
import json, datetime as dt, numpy as np, pandas as pd, io, re
from . import model as M, run2026 as H, senate2026 as SN, rcv as RC

GRID_LO, GRID_HI, GRID_STEP = -10.0, 14.0, 0.5


def _gov_poll():
    from . import gov2026 as GV
    return tuple(round(float(x), 2) for x in GV.poll_errors())


def grid_for(E_now):
    """Slider grid CENTRED on today's environment (2026-09-29): E_now + 0.5 k inside [-10, 14]. Until then the grid was
    the fixed half points and the page/log showed the nearest one (E0 = round(2 E)/2), so a 0.5-pt trend move across a
    rounding boundary stepped the headline (9-29: p_maj 0.912 -> 0.894 while the exact-E simulation said 0.887). Every
    slider position is a shift of ONE simulation run at E_now, so evaluating it at E_now itself is exact, not interpolated."""
    k0, k1 = int(np.ceil((GRID_LO - E_now) / GRID_STEP - 1e-9)), int(np.floor((GRID_HI - E_now) / GRID_STEP + 1e-9))
    return [round(E_now + GRID_STEP * k, 2) for k in range(k0, k1 + 1)]


def main():
    from .wiki_inputs import generic_aggregates
    agg = generic_aggregates()
    # National mood from OUR poll-level trend (generic.py: trend + house effects + population offsets on the merged VoteHub, Polling USA and
    # pollresults.org polls); the aggregators are shown for comparison only (user 2026-09-16). The correction used to be a judgement BIAS: none
    # (2026-09-19), -1 (09-22: this year LV polls run MORE Democratic than RV, 17 paired polls LV +1.4 D), -2 (09-30: split the
    # difference with the 1996-2024 record, 13 of 15 cycles overstated D by ~2.7 at 50 days), then for a day FITTED (-3.1).
    try: gm = json.load(open("data/cache/generic_model.json")); our = gm["generic"]["diag"]["now"]
    except Exception: gm, our = None, float(np.mean([a["margin"] for a in agg])) if agg else 7.5
    # Since 2026-10-03 (user: "I'm just against making an assumption of the direction of the polling error"): NO directional
    # correction - E = our trend - and the national error is the size of past misses about zero (national_mood.py, DIRECTION)
    from . import national_mood as NMOOD
    MOOD = NMOOD.apply(); BIAS = MOOD["c"]; MOOD["state_shared"] = SN.STATE_SHARED_MISS     # the simulated statewide miss
    E_now = round(our + BIAS, 2)                         # the simulation runs at the exact estimate (was snapped to 0.5 until 2026-09-22)
    E0 = E_now                                           # slider default = the exact estimate (a grid point: the grid is centred on it)
    GRID = grid_for(E_now); assert E0 in GRID
    # banked early votes (early_vote.py, 2026-10-04): the late-movement part of the national error is scaled per state by its
    # early-vote exposure relative to past cycles; off (M.MOVE None) if the inputs are missing
    from . import early_vote as EV
    try:
        rho_mv = EV.setup(20000, MOOD["s_house"], SN.STATE_SHARED_MISS / SN.NAT_SLOPE, NMOOD.RHO_STATEWIDE)
        MOOD["early_vote"] = {**M.MOVE["meta"], "m": round(M.MOVE["m"], 3), "k": {s_: round(v, 3) for s_, v in M.MOVE["k"].items()}}
    except Exception as e:
        print(f"  !! early-vote movement split off ({e})"); M.MOVE = None; rho_mv = None
    Z, ZS = NMOOD.draws(20000, rho=rho_mv)                    # House draw + the statewide draw (total correlation RHO_STATEWIDE)
    M.RECORD = []; M.RECORD_OFFICE = "house"                  # poll-by-poll record for web/polls.html (model.robust_blend)
    s, mg, S = H.run(E_now, P=M.Params(s_nat=MOOD["s_house"]), n=20000, nat_z=Z); ratings = pd.read_csv("data/cache/house2026_ratings.csv")[["seat", "rating_mean"]]
    # 2026-10-09: DataFrame.merge drops .attrs (pandas 2.3), which carry the demographic-factor update (factor_mean, factor_post_sd):
    # the about page's notes lost "today the polls put the Hispanic factor at ..." and the what-if page simulated the House with the
    # factors' PRIOR sds instead of the posterior ones the simulation used
    att = dict(s.attrs); s = s.merge(ratings, on="seat", how="left"); s.attrs.update(att)
    house = {"E0": E0, "E_now": E_now, "grid": GRID, "keys": [str(e) for e in GRID], "seats": [], "dist": {}}
    d0 = mg > 0
    for e in GRID:
        sh = e - E_now; d = (mg + sh) > 0; ds = d.sum(1)
        house["dist"][str(e)] = {"mean": round(float(ds.mean()), 1), "p10": int(np.percentile(ds, 10)), "p25": int(np.percentile(ds, 25)), "p50": int(np.median(ds)), "p75": int(np.percentile(ds, 75)), "p90": int(np.percentile(ds, 90)), "p_maj": round(float((ds >= 218).mean()), 3),
                                "hist": {str(k): int(v) for k, v in zip(*np.unique(ds, return_counts=True))}}
    dn = (mg > 0).sum(1); house["now"] = {"mean": round(float(dn.mean()), 1), "p10": int(np.percentile(dn, 10)), "p90": int(np.percentile(dn, 90)), "p_maj": round(float((dn >= 218).mean()), 3)}
    pgrid = np.stack([((mg + (e - E_now)) > 0).mean(0) for e in GRID], 1)      # seats x grid
    for i, r in s.iterrows():
        house["seats"].append({"seat": r["seat"], "state": r["state"], "cd": int(r["seat"].split("-")[1]), "member": None if pd.isna(r.get("member")) else str(r["member"]), "inc": int(r["inc"]), "open": bool(r["open"]),
                               "cook_pvi": float(r["cook_pvi"]), "mu": round(float(r["mu"]), 2), "n_polls": (0 if pd.isna(r.get("n")) else int(r["n"])), "poll_margin": (None if pd.isna(r.get("poll_margin")) else round(float(r["poll_margin"]), 1)), "rating": None if pd.isna(r.get("rating_mean")) else round(float(r["rating_mean"]), 2),
                               "money_d": None if pd.isna(r.get("money_d")) else round(float(r["money_d"])), "money_r": None if pd.isna(r.get("money_r")) else round(float(r["money_r"])), "p": [round(float(x), 3) for x in pgrid[i]],
                               **({"rcv": RC.house_note(r)} if RC.applies("house", r["seat"]) else {})})
    M.RECORD_OFFICE = "senate"
    SN.JOINT = SN.joint_setup(20000) if SN.JOINT_ON else None        # same-state Senate / governor component (off: README)
    SS, smg, sout = SN.run(E_now, nat_z=ZS)
    sen = {"E0": E0, "E_now": E_now, "nat_slope": SN.NAT_SLOPE, "now": {"p51plus": round(sout["p_dem_51plus"], 3), "p_r_lose": round(sout["p_r_lose"], 3), "mean": round(sout["dem_seats_mean"], 1)}, "now_d": SN.NOW_D, "now_r": SN.NOW_R, "d_up": sout["d_up"], "r_up": sout["r_up"], "races": [], "dist": {}}
    d_up = sout["d_up"]
    _s, _i = SN.seat_counts(SS, smg > 0, d_up); sen["now"]["p_ctrl"] = round(float(SN.d_controls(_s, _i).mean()), 3)
    for e in GRID:
        sh = (e - E_now) * SN.NAT_SLOPE; d = (smg + sh) > 0; seats, ind = SN.seat_counts(SS, d, d_up)
        sen["dist"][str(e)] = {"p_r_lose": round(float((seats + ind >= 51).mean()), 3), "ind_mean": round(float(ind.mean()), 2), "mean": round(float(seats.mean()), 1), "p10": int(np.percentile(seats, 10)), "p50": int(np.median(seats)), "p90": int(np.percentile(seats, 90)), "p50plus": round(float((seats >= 50).mean()), 3), "p51plus": round(float((seats >= 51).mean()), 3),
                              "p_ctrl": round(float(SN.d_controls(seats, ind).mean()), 3), "p_ctrl_ir": round(float(SN.d_controls(seats, ind, "R").mean()), 3), "p_ctrl_id": round(float(SN.d_controls(seats, ind, "D").mean()), 3),
                              "hist": {str(k): int(v) for k, v in zip(*np.unique(seats, return_counts=True))},
                              # (Democratic seats, independent seats) pairs: the chart stacks independents on each bar
                              "hist2": {f"{a}|{b}": int(v) for (a, b), v in zip(*np.unique(np.stack([seats, ind], 1), axis=0, return_counts=True))}}
    # joint control (same national draw, same slider shift): D House = 218+, D Senate = organises it (SN.d_controls; the VP is a Republican)
    joint = {}
    for e in GRID:
        dh = ((mg + (e - E_now)) > 0).sum(1) >= 218
        sseats, sind = SN.seat_counts(SS, (smg + (e - E_now) * SN.NAT_SLOPE) > 0, d_up); dsn = SN.d_controls(sseats, sind)   # organises the Senate (independents abstain)
        joint[str(e)] = {"dd": round(float((dh & dsn).mean()), 3), "dr": round(float((dh & ~dsn).mean()), 3), "rd": round(float((~dh & dsn).mean()), 3), "rr": round(float((~dh & ~dsn).mean()), 3)}
    sen["joint"] = joint
    spgrid = np.stack([((smg + (e - E_now) * SN.NAT_SLOPE) > 0).mean(0) for e in GRID], 1)
    for i, r in SS.iterrows():
        sen["races"].append({"state": r["state"], "special": bool(r["special"]), "incumbent": r["incumbent"], "inc_party": r["inc_party"], "inc": round(float(r["inc"]), 2), "cook_pvi": None if pd.isna(r["cook_pvi"]) else float(r["cook_pvi"]),
                             "dem": r["challenger"] or r["dem_nom"], "dem_party": r["challenger_party"] or "D", "rep": r.get("republican") or r["rep_nom"], "n_polls": int(r["n_polls"]), "poll_margin": None if pd.isna(r["poll_margin"]) else round(float(r["poll_margin"]), 1), "newest_poll": r["newest_poll"],
                             "mu": round(float(r["mu"]), 2), "sd": round(float(r["sd"]), 2), "mu_prior": round(float(r["mu_prior"]), 1), "prior_note": r.get("prior_note") or "", "cook": r.get("rat_Cook"), "sabato": r.get("rat_Sabato"), "p": [round(float(x), 3) for x in spgrid[i]],
                             **({"rcv": r["rcv"], "rcv_sd": round(float(r.get("rcv_sd") or 0), 2)} if r.get("rcv") else {})})
    polls = None
    if gm:
        polls = {"generic_now": gm["generic"]["diag"]["now"], "bias": BIAS, "mood": MOOD, "generic_trend": gm["generic"]["trend"], "approval_trend": gm["approval"]["trend"], "generic_band": gm["generic"].get("band"), "approval_band": gm["approval"].get("band"),
                 "generic_ci": gm["generic"].get("now_ci"), "approval_ci": gm["approval"].get("now_ci"), "generic_t1": [gm["generic"]["last_poll"], gm["generic"]["diag"]["now"]], "approval_t1": [gm["approval"]["last_poll"], gm["approval"]["diag"]["now"]], "generic_polls": gm["generic"].get("polls"), "approval_polls": gm["approval"].get("polls"), "approval_now": gm["approval"]["diag"]["now"],
                 "generic_diag": {k: gm["generic"]["diag"][k] for k in ("now", "30d_ago", "60d_ago", "90d_ago", "dem_30d", "dem_30d_ago", "rep_30d", "rep_30d_ago", "und_30d", "und_30d_ago", "raw_change_30d", "adj_change_30d", "by_pop_90d", "resid_sd")},
                 "house_effects": gm["generic"]["diag"]["house_effects_active"], "approval_diag": {k: gm["approval"]["diag"][k] for k in ("now", "30d_ago", "60d_ago", "90d_ago", "dem_30d", "rep_30d")}, "calibration": gm.get("calibration"), "n_generic": gm["generic"]["n"], "last_generic": gm["generic"]["last_poll"], "sources": gm.get("sources")}
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "asof": dt.date.today().isoformat(), "generic": agg, "polls": polls, "house": house, "senate": sen,
           "notes": {**methodology_notes(s), "backtests": ""}}
    out["notes"]["backtests"] = BACKTEST_NOTE
    out["mood"] = MOOD
    try:                                                                 # governors (midterms/gov2026.py), same national draw
        from . import gov2026 as GV
        M.RECORD_OFFICE = "governor"
        GS, gmg, ginfo = GV.run(E_now, nat_z=ZS); slope = ginfo["coef"]["E"]
        gov = {"E_now": E_now, "slope": slope, "coef": ginfo["coef"], "prior_sd": ginfo["prior_sd"], "now": GS.attrs["summary"], "dist": {}, "races": []}
        up_d = GS.attrs["summary"]["up_d"]
        for e in GRID:
            dd = (gmg + (e - E_now) * slope) > 0; dg = (GV.NOW_D - up_d) + dd.sum(1)
            gov["dist"][str(e)] = {"mean": round(float(dg.mean()), 1), "p10": int(np.percentile(dg, 10)), "p90": int(np.percentile(dg, 90)), "p_d_majority": round(float((dg >= 26).mean()), 3)}
        gp = np.stack([((gmg + (e - E_now) * slope) > 0).mean(0) for e in GRID], 1)
        for i, r in GS.iterrows():
            gov["races"].append({"state": r.state, "governor": r.governor, "inc_party": r.inc_party, "inc": int(r.inc), "dem": r.dem, "rep": r.rep, "lean": r.lean, "n_polls": int(r.n_polls),
                                 "poll_margin": None if pd.isna(r.poll_margin) else round(float(r.poll_margin), 1), "mu": round(float(r.mu), 2), "p": [round(float(x), 3) for x in gp[i]],
                                 **({"rcv": r.rcv, "rcv_sd": round(float(r.rcv_sd or 0), 2)} if getattr(r, "rcv", "") else {})})
        out["governor"] = gov
    except Exception as ex:
        print("governors skipped:", str(ex)[:120])
    from . import stateleg as SLG                                        # state legislatures (stateleg.py): MIDTERMS_STATELEG=on only
    if SLG.ON:
        try:
            from . import stateleg_wiki as SLW
            SLW.build()
            sl, sl_seats = SLG.run_live(E_now, Z, MOOD["s_house"], SS, smg, GS if "governor" in out else None, gmg if "governor" in out else None)
            out["state_legislatures"] = sl
            try:                                                         # per-seat file for the district maps (stateleg_seats.py)
                from . import stateleg_seats as SLX; SLX.export(sl, sl_seats, out.get("asof"))
            except Exception as ex: print("  !! stateleg seats export failed:", str(ex)[:160])
            print("State legislatures:", "; ".join(f"{c['name']} D {c['p_d']:.2f}" for c in sl["chambers"]))
        except Exception as ex:
            import traceback; traceback.print_exc(); print("state legislatures skipped:", str(ex)[:160])
    json.dump(out, open("web/data/model.json", "w"), separators=(",", ":"))
    polls_page(out, gm, E_now, BIAS)
    try:                                                     # the "What if?" page's ingredients (midterms/whatif.py)
        from . import whatif as WI
        WI.export(s, M.Params(s_nat=MOOD["s_house"]), SS, GS if "governor" in out else None, out["governor"]["slope"] if "governor" in out else None,
                  d_up, out["governor"]["now"]["up_d"] if "governor" in out else 0, E_now, MOOD)
    except Exception as ex:
        print("  !! whatif export failed:", str(ex)[:160])
    if "governor" in out: print("Governors:", out["governor"]["now"])
    nh = lambda d: {k: v for k, v in d.items() if not k.startswith("hist")}
    print(f"E0 {E0} (exact, = E_now); House at E0: {nh(house['dist'][str(E0)])}; Senate at E0: {nh(sen['dist'][str(E0)])}")


# 2026-10-03: the live harnesses (backtest_all live_house / live_senate), leak-free, on today's settings: no directional
# correction nationally or by state, statewide miss 4.2 about zero (scratch final_bt arm S2)
BACKTEST_NOTE = ("House, each year forecast from the other years' data: on 15 Sep the model gave Democrats 227 seats (202-257) in 2018 "
                 "against 235 won, PIT 0.69, and 213 (191-234) in 2022 against 213, PIT 0.49. Over 12 dates from July to November the "
                 "result fell inside the 10-90 % range every time, and the model ran Republican at most dates (mean PIT 0.65). Senate, "
                 "2018-2024 at 6 dates each: the seat count fell inside the 10-90 % range at all 24, and the races ran the other way, "
                 "too Democratic at most dates (mean PIT 0.29): in all four cycles the Senate polls missed toward the Republicans, a "
                 "direction this model deliberately does not assume.")


def methodology_notes(s):
    """The about page's technical notes, with every number read from the live constants (2026-10-03: the hand-written text
    still said national error 4.1, Senate shared poll error 5.0, Senate national shock 2.9 x 0.8 and a Hispanic term of 15
    after all of them had changed)."""
    from . import money as MON
    from .race_poll_calibration import OUT as CAL_PATH
    from . import national_mood as NMOOD
    mood = NMOOD.mapping()
    P = M.Params(); b, c, _, _ = M.fit_prior((2018, 2022)); bm, cm, mm, _, _ = MON.coefficients()
    cal = json.loads(CAL_PATH.read_text()); spd, spr = cal["sponsor"]["D"]["lean"], -cal["sponsor"]["R"]["lean"]
    fm = s.attrs.get("factor_mean", {}) if hasattr(s, "attrs") else {}
    heat = M.heating_oil_shift("state"); hr = M.heat_retail()
    und_max = 100 * (M.UND_SLOPE * (M.UND_CAP - M.UND_FREE))
    house = (f"mu = {P.k0:.1f} + {b:.2f}*lean + {c:.1f}*inc + E (lean = 2 x Cook PVI 2026), fundraising: + {mm:.2f} x log(D / R individual contributions "
             f"through June 30, $10k floor) - out of sample it cut competitive-seat error 5.5 % (2018) and 3.8 % (2022), and moves incumbency from "
             f"{c:.1f} to {cm:.1f}; race polls from the state pages blended by precision (per-poll sd {M.HOUSE_POLL_SD}, shared {M.HOUSE_POLL_SYS}; a poll's "
             f"precision fades with a {M.AGE_HALF:.0f}-day half-life, no hard window); seats with no Democrat or no Republican on the ballot are decided "
             f"(CA-40 R-v-R, D-v-D top-two), an independent incumbent facing only a Democrat (CA-6) keeps incumbency; every race poll (House and Senate) "
             f"is corrected for its pollster's measured lean (538's 2016-22 record, shrunk) or, failing that, its sponsor tag: D-sponsored polls ran "
             f"{spd:.1f} too Democratic and R-sponsored {spr:.1f} too Republican against the non-partisan polls of the same race (1998-2022, same sign "
             f"every cycle), and count at {M.SPONSOR_WEIGHT:.2f} weight; polls with more than {M.UND_FREE:.0f} % undecided carry up to {und_max:.0f} % more "
             f"error, a third candidate at 10 %+ {100 * (M.THIRD_MULT - 1):.0f} % more; LV and RV race polls are not adjusted (94 % of late race polls are "
             f"LV; the generic-ballot fit carries LV/RV/adult offsets); Hispanic/Asian swing since 2024: + {H.GROUP_B_HISP:g} x centred Hispanic CVAP share "
             f"(Cubans at {H.CUBAN_WEIGHT:g}) + {H.GROUP_B_ASIAN:g} x centred Asian share, before the poll blend (2025 NJ/VA governor and CA Prop 50 "
             f"reversed the 2020-24 shift fully, specials did not; centred so the national environment is unchanged); " + (f"every race poll is also corrected "
             f"for its STATE's persistent polling miss (0.24 x the 2024 miss + 0.13 x 2022's, net of the national miss; out of sample -2.7 % Senate / "
             f"-0.9 % House log loss); " if M.STATE_DIRECTION else "no race poll is shifted for the direction its state's polls missed before; ") + f"the demographic factors are UPDATED from the district polls (Bayesian, no tuned parameters: 2022 House log loss "
             f"-5.1 %)" + (f" - today the polls put the Hispanic factor at {fm['h_load']:+.0f} per unit share against the +{H.GROUP_B_HISP:g} prior swing "
             f"and white non-college at {fm['w_load']:+.1f}" +
             (f"; regional Hispanic deviations (prior zero, set by the district polls): Texas {fm['h_tx']:+.1f}, Florida {fm['h_fl']:+.1f}, "
              f"West {fm['h_west']:+.1f}" if all(k in fm for k in ("h_tx", "h_fl", "h_west")) else "")
             if fm and "h_load" in fm and "w_load" in fm else "") +
             (f"; heating-oil adjustment (judgment, not backtested): fuel-oil household share x retail heating-oil rise (EIA weekly Maine "
              f"residential price ${hr['now']:.2f} for the week of {hr['now_date']}" + ("" if hr["in_season"] else
              " - EIA's survey runs October to March, so the last published week is held") +
              f", v ${hr['yr_ago']:.2f} a year earlier) x {M.HEAT_GAL:.0f} gal x {M.HEAT_PTS_PER_DOLLAR * 1000:.2f} pts per $1,000 x the share not "
              f"yet in polls, against the president's party (Maine {heat.get('ME', 0.0):+.1f})" if hr else
              "; heating-oil adjustment off (EIA price unavailable)") +
             f"; national environment E = our generic-ballot trend, with no correction "
             f"for the direction of a polling miss; national-vote error {mood['s_vote']:.1f} at {mood['lead']} days out (the root-mean-square miss "
             f"of the generic-ballot average against the House vote at this distance, 15 cycles 1996-2024, in either direction); errors t{M.T_DF}: national {mood['s_house']:.1f} (the vote error "
             f"plus the year-to-year error of the contested-seat intercept, {NMOOD.S_SEAT:g}), state {P.s_state:g}, urban {P.s_urban:g}, Hispanic / "
             f"Cuban / Asian group factors {P.s_hisp:g} per unit share, residual {P.s_res:g}")
    senate = (f"mu = {SN.SEN_CONST:.1f} + {SN.NAT_SLOPE:.2f}*E + {SN.B_LEAN:.2f}*lean + {SN.C_INC:.1f}*inc, prior sd {SN.PRIOR_SD} (refit on 321 D-v-R races "
              f"2006-2024), polls from the Wikipedia race pages (per-poll sd {SN.POLL_SD}; race-level systematic {SN.race_sys():.1f} = the measured "
              f"midterm total {SN.POLL_SYS_TOTAL:g} minus the shared statewide shock drawn separately); "
              f"only polls of the candidates actually on the ballot, full field where a third candidate polls 10 %+; the margin is the main challenger "
              f"minus the Republican - an independent where no Democrat is on the ballot (NE, ID, SD) or the independent leads the Democrat (MT). "
              f"Independent wins are not counted as Democratic seats. A FUNDED independent with no Democrat on the ballot (>= ${SN.IND_MIN_MONEY / 1e6:g}M "
              f"and >= {100 * SN.IND_MIN_RATIO:.0f} % of the Republican's June 30 money) gets the prior +{SN.IND_SHIFT:g} with sd {SN.IND_SHIFT_SD:g}: "
              f"the three famous successes (Orman +21, McMullin +21, Osborn +25 against the prior) are a selected sample - the unfunded independents of "
              f"the same class ran -4 (Harrington 2020), -44 (O'Hara 2002) and -53 (Spannaus 1990), so the shift is gated on money and shrunk for three "
              f"precedents. An unfunded independent (ID, SD in 2026) gets the plain prior, widened; with a Democrat also running (MT) the prior is widened "
              f"x{SN.SPLIT_PRIOR_MULT:g}; Hispanic/Asian swing term as in the House ({H.GROUP_B_HISP:g} x centred Hispanic CVAP share, Cubans at "
              f"{H.CUBAN_WEIGHT:g}, {H.GROUP_B_ASIAN:g} x Asian; state shares vs national), mean and group shocks both damped by the Senate's national "
              f"slope {SN.NAT_SLOPE}; " + (f"race polls corrected for the state's persistent polling miss (as the House) and for the white non-college polling "
              f"lean: statewide polls ran too Democratic by {M.WNC_POLL_LEAN} pts per state sd of white non-college share (Senate + governor 1998-2022, "
              f"10 of 13 cycles; not tied to turnout or to the national miss; out of sample Senate log loss -3.8 %) - governors get the same; "
              if M.STATE_DIRECTION else "no correction for the direction of a state's polling miss; ") + (
              f"heating-oil adjustment by state fuel-oil share; appointed incumbents (Husted, Moody) get {SN.APPOINTEE_INC} of the incumbency term "
              f"(12 appointees 2006-22 averaged +6.3 over an open-seat prior); shared statewide polling miss {SN.STATE_SHARED_MISS:g} pts in either direction (the root-mean-square of the cycle-wide Senate miss "
              f"2018-2024; race outcomes inside the model's 80 % ranges 78 % of the time with it, 67 % with the old 2.4), drawn with "
              f"correlation {NMOOD.RHO_STATEWIDE:g} to the House's national draw (the generic-ballot miss and the Senate's shared polling miss "
              f"correlated about +0.3 over 13 cycles, not 1)"))
    return {"house": house, "senate": senate}


def polls_page(out, gm, E_now, bias):
    """web/data/polls.json for web/polls.html: every poll the model used and how it was weighted."""
    rec = M.RECORD or []; M.RECORD = None
    names = {}
    for r in out["senate"]["races"]:
        names[("senate", r["state"])] = f"{r.get('dem') or '?'} ({r.get('dem_party') or 'D'}) v {r.get('rep') or '?'} (R)"
    for r in (out.get("governor") or {}).get("races", []):
        names[("governor", r["state"])] = f"{r.get('dem') or '?'} (D) v {r.get('rep') or '?'} (R)"
    for r in out["house"]["seats"]:
        names[("house", r["seat"])] = r.get("member") or ""
    last = {}
    for r in rec:                          # the House blends twice (before and after the demographic-factor update): keep the final pass
        r["label"] = names.get((r["office"], r["seat"]), ""); last[(r["office"], r["seat"])] = r
    races = list(last.values())
    doc = {"generated": out["generated"], "asof": out["asof"], "E_now": E_now, "bias": bias, "races": races,
           "national": {k: {"now": (gm or {}).get(k, {}).get("diag", {}).get("now"), "polls": (gm or {}).get(k, {}).get("poll_weights", [])}
                        for k in ("generic", "approval")},
           "constants": {"half_life_days": M.AGE_HALF, "window_days": M.POLL_WINDOW, "nu": M.ROBUST_NU, "sponsor_weight": round(M.SPONSOR_WEIGHT, 2),
                         "house_poll_sd": M.HOUSE_POLL_SD, "house_poll_sys": M.HOUSE_POLL_SYS, "sen_poll_sd": SN.POLL_SD, "sen_poll_sys": round(SN.race_sys(), 2), "sen_shared": SN.STATE_SHARED_MISS,
                         "gov_poll_sd": _gov_poll()[0], "gov_poll_sys": _gov_poll()[1]}}   # gov2026.poll_errors (the Senate's unless switched)
    def clean(x):                                     # NaN is not JSON: the page's parser rejects the whole file
        if isinstance(x, dict): return {k: clean(v) for k, v in x.items()}
        if isinstance(x, list): return [clean(v) for v in x]
        if isinstance(x, float) and not np.isfinite(x): return None
        return x
    json.dump(clean(doc), open("web/data/polls.json", "w"), separators=(",", ":"), allow_nan=False)
    print(f"polls page: {len(races)} races with polls, {sum(len(r['polls']) for r in races)} race polls; "
          f"{len(doc['national']['generic']['polls'])} generic, {len(doc['national']['approval']['polls'])} approval polls")


if __name__ == "__main__":
    main()
