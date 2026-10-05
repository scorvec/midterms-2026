"""2026 House run: fundamentals prior on the 2026 maps (Wikipedia district table: Cook PVI 2026,
incumbent running / open), national environment from the generic-ballot aggregates, no race polls
yet, correlated Monte Carlo, slider grid.   python -m midterms.run2026 [E]
"""
import os, sys, json, numpy as np, pandas as pd
from . import model as M

# Hispanic / Asian swing since 2024 (research/specials_hispanic.py, research/offyear2025_hispanic.py, 2026-09-19).
# The 2020->24 move toward Trump in Hispanic places was ~ -25 to -44 pts per unit Hispanic CVAP share; in 2025
# it reversed fully in the higher-turnout races (NJ gov +45, VA gov +53, CA Prop 50 +82 per unit) but not in the
# low-turnout specials (~0). A midterm electorate sits between, and the Cook PVI lean already blends 2020 with 2024,
# so the value is +15 (= +1.5 pts of D margin per 10 pts of Hispanic CVAP share), Asian +11 - the lower end, because
# the POLLS imply a smaller individual-level differential than the off-year results do (Hart/TelevisaUnivision
# battlegrounds: Latino swing ~+12 vs ~+9.5 national; RGV generic ~+18) and the ecological coefficients carry the
# turnout composition of a low-turnout electorate. Cubans count at
# a quarter (FIU Apr 2026: Florida Latino net approval -10 vs -55 in CA/TX/AZ; FL SD-14 swung half the average).
# The term is centred on the 435-seat mean, so it moves seats relative to each other and leaves the national
# environment (which the generic ballot already measures, Hispanic respondents included) unchanged.
# 2026-09-22: 15 -> 12. Tested against the 2026 race polls (poll minus the prior WITHOUT the term, regressed on the
# term, precision-weighted): competitive seats (|prior| < 15, n 40) see 0.59 (se 0.43) of it; all 51 polled seats
# ~0 (se 0.36), dragged by safe seats with odd multi-candidate polls (IL-4). The polls cannot reject the term but
# centre below it; weighting that 0.59 x 15 = 8.9 (+-6.5) against the off-year evidence the 15 came from (taken as
# +-7.5) gives ~11.5 -> 12. The Asian term has too few polled Asian-heavy seats to test and stays at 11.
GROUP_B_HISP = 12.0
GROUP_B_ASIAN = 11.0
CUBAN_WEIGHT = 0.25
HISP_REGIONS = {"h_tx": ["TX"], "h_fl": ["FL"], "h_west": ["CA", "AZ", "NV", "NM", "CO"]}
HISP_GROUPS = ["mexican", "puerto_rican", "dominican", "central_american", "colomb", "venezuelan", "other_south_american", "other_hispanic"]


def group_loadings(s):
    """Centred Hispanic (Cuban-discounted), Cuban and Asian CVAP-share loadings per seat (midterms/demog.py)."""
    try:
        g = pd.read_csv("data/static/district_groups_2026.csv")
    except FileNotFoundError:
        s["h_load"] = s["c_load"] = s["a_load"] = s["group_shift"] = 0.0
        for load in HISP_REGIONS: s[load] = 0.0
        return s
    g["h_eff"] = g[["sh_" + x for x in HISP_GROUPS]].sum(axis=1) + CUBAN_WEIGHT * g["sh_cuban"]
    s = s.merge(g[["seat", "sh_hisp", "sh_cuban", "sh_asian", "h_eff"]], on="seat", how="left")
    miss = s["h_eff"].isna().sum()
    assert miss == 0, f"{miss} seats without group shares - rebuild with python -m midterms.demog"
    # centred on the NATIONAL shares (CVAP-weighted, = the US row of state_groups.csv), same baseline as the Senate
    w = g.set_index("seat").reindex(s["seat"])["cvap"].values
    for load, col in (("h_load", "h_eff"), ("c_load", "sh_cuban"), ("a_load", "sh_asian")):
        s[load] = s[col] - np.average(s[col], weights=w)
    # regional Hispanic deviations (2026-10-04; model.Params.s_htx/s_hfl/s_hwest): the same Cuban-discounted share inside
    # the region, zero outside, centred nationally - prior mean 0, the district polls set them in model.factor_update
    st = s["seat"].str[:2]
    for load, sts in HISP_REGIONS.items():
        x = np.where(st.isin(sts), s["h_eff"], 0.0); s[load] = x - np.average(x, weights=w)
    s["group_shift"] = GROUP_B_HISP * s["h_load"] + GROUP_B_ASIAN * s["a_load"]
    return s


def load_seats():
    s = pd.read_csv("data/cache/house2026_seats.csv"); s["state"] = s["seat"].str[:2]
    if "lean" not in s or s["lean"].isna().all(): s["lean"] = 2.0 * s["cook_pvi"]      # Cook share points -> margin units (see wiki_inputs)
    if os.environ.get("MIDTERMS_CD_LEAN", "cook").lower() == "ours":
        # our own lean (cd_lean_build.py: 2024/2020 president on the 2026 maps from MEDSL / VEST / Census block files, 75/25 like
        # Cook, margin units). Built and validated 2026-10-04, NOT the default: the prior coefficients were fitted on 538/Cook leans
        try:
            cl = pd.read_csv("data/static/cd_lean_2026.csv").set_index("seat")["lean_75_25"]
            s["lean_cook"] = s["lean"]; s["lean"] = s["seat"].map(cl).fillna(s["lean"])
        except FileNotFoundError: pass
    u = pd.read_csv("data/raw/538repo/urbanization-index-2022.csv"); u["seat"] = u["state"] + "-" + u["cd"].astype(int).astype(str)
    s = s.merge(u[["seat", "urbanindex"]], on="seat", how="left")
    # redrawn states: 538's 2022 file is keyed by district NUMBER, so those seats carried an unrelated old district's value
    # (CA-41, MO-5, TN-9 off by 1.6-1.8 index units). midterms/urban.py rebuilds the index on the 2026 map (r 0.994 with
    # 538 on 214 unchanged seats, mapped to its scale); used for the states whose map changed
    try:
        u26 = pd.read_csv("data/static/urbanization_2026.csv").set_index("seat")["urbanindex"]
        redrawn = s["state"].isin(["AL", "CA", "FL", "GA", "LA", "MO", "NC", "NY", "OH", "TN", "TX", "UT"])
        s.loc[redrawn, "urbanindex"] = s.loc[redrawn, "seat"].map(u26)
    except FileNotFoundError: pass
    s["u_load"] = ((s["urbanindex"] - s["urbanindex"].mean()) / s["urbanindex"].std()).fillna(0)
    try:                                                            # white non-college loading (demog.district_wnc)
        wn = pd.read_csv("data/static/district_wnc_2026.csv"); us = pd.read_csv("data/static/state_groups.csv").set_index("state").loc["US", "sh_wnc"]
        s = s.merge(wn[["seat", "sh_wnc"]], on="seat", how="left"); s["w_load"] = (s["sh_wnc"] - us).fillna(0)
    except FileNotFoundError: s["w_load"] = 0.0
    return ballot_structure(group_loadings(s))


def ballot_structure(s):
    """Seats that are not a Democrat-v-Republican contest (2026-09-22). From the nominee list (Wikipedia 'cands'):
      * no Democrat on the general ballot and >= 1 Republican (California top-two R-v-R, e.g. CA-40 Calvert v Kim)
        -> a certain Republican seat; the model had simulated CA-40 as D-v-R at 30 % D;
      * no Republican and >= 1 Democrat (D-v-D top-two, unopposed, minor-party-only opposition) -> a certain D seat,
        EXCEPT where the incumbent runs as an independent (CA-6 Kiley v Pan): that is a D-v-incumbent race, so it stays
        simulated and the incumbent keeps the incumbency term (inc -1: he is the only non-Democrat on the ballot).
    'Democratic-NPL' (North Dakota) and 'DFL' count as Democratic."""
    import re as _re
    dem = s["cands"].astype(str).map(lambda c: len(_re.findall(r"\((?:Democratic(?:-NPL)?|DFL)\)", c)))
    rep = s["cands"].astype(str).map(lambda c: len(_re.findall(r"\(Republican\)", c)))
    ind_inc = (s["party"].astype(str) == "Independent") & (s["inc_running_party"].astype(str) == "O")
    s["uncontested"] = False; s["winner"] = None; s["ballot_note"] = ""
    r_only = (dem == 0) & (rep >= 1); d_only = (rep == 0) & (dem >= 1) & ~ind_inc
    s.loc[r_only, ["uncontested", "winner", "ballot_note"]] = [True, "R", "no Democrat on the ballot"]
    s.loc[d_only, ["uncontested", "winner", "ballot_note"]] = [True, "D", "no Republican on the ballot"]
    ii = ind_inc & (rep == 0)
    s.loc[ii, "inc"] = -1; s.loc[ii, "ballot_note"] = "independent incumbent v Democrat, no Republican: incumbent keeps incumbency"
    return s

def _add_bluesky_house(hp):
    """District polls from the Polling USA Bluesky feed (midterms/bluesky_polls.py): both names must be the seat's D and R
    nominees (surname, first five letters); copies of polls already held (same seat, end date +-3 d, margin +-1.5) dropped."""
    import re as _re
    try: b = pd.read_csv("data/state/bluesky_race_polls.csv", parse_dates=["end_date"])
    except FileNotFoundError: return hp
    b = b[b.office == "house"]
    if b.empty: return hp
    seats = pd.read_csv("data/cache/house2026_seats.csv").set_index("seat")
    keep = []
    for r in b.itertuples():
        if r.seat not in seats.index: continue
        c = _re.findall(r"▌([^▌\[]+?)\s*\(([^)]+)\)", str(seats.at[r.seat, "cands"]))
        dn = {M._sur5(n) for n, pt in c if pt.startswith(("Democratic", "DFL"))}; rn = {M._sur5(n) for n, pt in c if pt == "Republican"}
        if M._sur5(r.dem_name) in dn and M._sur5(r.rep_name) in rn: keep.append(r.Index)
    b = b.loc[keep]
    if hp is not None and len(hp):
        b = b[[not M._same_poll(r.pollster, r.end_date, r.margin, hp[hp.seat == r.seat]) for r in b.itertuples()]]
    cols = ["seat", "pollster", "end_date", "n", "dem", "rep", "margin", "dem_name", "rep_name", "und", "other"]
    return b[cols] if hp is None else pd.concat([hp, b[cols]], ignore_index=True)


def run(E, P=None, n=20000, seed=7, asof=None, nat_z=None):
    asof = asof or pd.Timestamp.today().normalize()
    P = P or M.Params(); b, c, k, sd = M.fit_prior((2018, 2022))
    s = load_seats()
    from . import money as MON
    mon = MON.seat_money(2026)
    try: hp = pd.read_csv("data/cache/house2026_polls.csv", parse_dates=["end_date"])
    except FileNotFoundError: hp = None
    try:                                     # second source (2026-09-22): VoteHub race polls not already in the Wikipedia set
        vh = pd.read_csv("data/cache/house2026_polls_votehub.csv", parse_dates=["end_date"])
        hp = vh if hp is None else pd.concat([hp, vh], ignore_index=True)
    except FileNotFoundError: pass
    hp = _add_bluesky_house(hp)
    if hp is not None and len(hp):                    # VoteHub / Wikipedia rows without a sponsor tag borrow it from the feeds (2026-09-30)
        from . import wiki_polls as _W; hp = _W.borrow_feed_tags(hp, "house", lambda r: r["seat"])
    if hp is not None and len(hp): hp = M.collapse_versions(hp, "seat")         # one survey, one row (versions across / within sources)
    if hp is not None and len(hp):                    # persistent state polling misses (model.state_poll_correction)
        hp = hp.copy(); hp["margin"] = hp["margin"] - hp["seat"].str[:2].map(M.state_poll_correction()).fillna(0.0)
    s = M.house_blend(s, E, P, b, c, polls=hp, asof=asof, money=mon, mcoef=(MON.coefficients() if mon is not None else None))
    # latent-factor update from the district polls (model.factor_update): second pass with the shifted priors
    upd = M.factor_update(s, P)
    if len(upd) == 3 and np.any(upd[0] != 0):
        shift, post, fmean = upd
        s0 = s[["seat"]].copy(); s0["fshift"] = shift
        s1 = load_seats(); s1["group_shift"] = s1["group_shift"] + s1["seat"].map(dict(zip(s0.seat, s0.fshift))).fillna(0)
        s = M.house_blend(s1, E, P, b, c, polls=hp, asof=asof, money=mon, mcoef=(MON.coefficients() if mon is not None else None))
        s["factor_shift"] = s["seat"].map(dict(zip(s0.seat, s0.fshift)))
        from dataclasses import replace
        P = replace(P, **post); s.attrs["factor_mean"] = fmean; s.attrs["factor_post_sd"] = post
    s["heat_oil_shift"] = s["seat"].map(M.heating_oil_shift("seat")).fillna(0.0); s["mu"] = s["mu"] + s["heat_oil_shift"]   # forward, post-blend
    mg = M.simulate(s, P, n=n, seed=seed, nat_z=nat_z); S = M.summarize(mg, s)
    return s, mg, S

if __name__ == "__main__":
    E = float(sys.argv[1]) if len(sys.argv) > 1 else 7.8
    s, mg, S = run(E)
    print(f"E = {E:+.1f} (default: our generic trend, no bias correction)")
    print(f"Dem seats mean {S['dem_seats_mean']:.1f}, p10/p50/p90 {S['dem_seats_p10']:.0f}/{S['dem_seats_p50']:.0f}/{S['dem_seats_p90']:.0f}, sd {S['seat_sd']:.1f}, P(D majority) {S['p_dem_majority']:.3f}")
    s["p_dem"] = S["p_dem_seat"]
    comp = s[(s["p_dem"] > 0.1) & (s["p_dem"] < 0.9)].sort_values("p_dem")
    print("competitive seats (10-90%):", len(comp)); print(comp[["seat", "lean", "inc", "open", "mu", "p_dem", "member"]].to_string()[:2500])
    print("\nslider grid (E -> Dem seats mean, P majority):")
    for e in np.arange(-6, 12.1, 2): S2 = M.summarize(mg, s, shift=e - E); print(f"  E {e:+.0f}: {S2['dem_seats_mean']:.0f} seats, P(D maj) {S2['p_dem_majority']:.2f}")
    s.to_csv("data/cache/house2026_run.csv", index=False)
