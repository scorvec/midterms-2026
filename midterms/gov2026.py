"""2026 governors (2026-09-22). Same machinery as the Senate: a fundamentals prior, race polls with the live corrections,
precision blend, correlated simulation on the SHARED national draw.

Prior, fitted on every polled governor race in 538's raw_polls (even years 1998-2022, D-v-R, actual result):
    margin = b0 + b1*lean + b2*inc + b3*E        (lean: MIT presidential, mean of the two previous elections vs the nation;
                                                  E: national House margin; inc: the previous winner is on the ballot)
Polls: VoteHub (poll_type=governor), each answer given a party from the Wikipedia candidate list; the D and R nominees are the
highest-polling candidate of each party (Alaska's top-four ballot has several Republicans). Corrections as the Senate: sponsor /
pollster lean, undecided / third-party variance, the state's persistent polling miss, spread inflation, age fade.
    python -m midterms.gov2026
"""
import io, json, re, urllib.request
from pathlib import Path
import numpy as np, pandas as pd
from . import poll_overrides as PO
from . import model as M, wiki_polls as W
ROOT = Path(__file__).resolve().parents[1]
TWO_YEAR = {"NH", "VT"}
POLL_SD, POLL_SYS = M.SEN_POLL_SD, M.SEN_POLL_SYS


def _pres_lean():
    p = pd.read_csv(ROOT / "data" / "raw" / "mit" / "president_1976_2024.csv"); p = p[p.party_simplified.isin(["DEMOCRAT", "REPUBLICAN"])]
    v = p.pivot_table(index=["year", "state_po"], columns="party_simplified", values="candidatevotes", aggfunc="sum")
    m = (100 * (v.DEMOCRAT - v.REPUBLICAN) / (v.DEMOCRAT + v.REPUBLICAN)).rename("m").reset_index()
    nat = p.groupby(["year", "party_simplified"]).candidatevotes.sum().unstack(); nat = 100 * (nat.DEMOCRAT - nat.REPUBLICAN) / (nat.DEMOCRAT + nat.REPUBLICAN)
    m["rel"] = m.m - m.year.map(nat)
    def lean(y, st):
        ys = sorted([q for q in m.year.unique() if q < y])[-2:]; x = m[(m.state_po == st) & m.year.isin(ys)].rel
        return float(x.mean()) if len(x) == 2 else np.nan
    return lean


def history():
    """EVERY governor race 1998-2022 (even years) from the Wikipedia yearly pages' results tables: D% - R% (top candidate of each
    party), incumbent running from the status column. The first version used only 538's POLLED races, which skew competitive:
    the lean slope came out 0.37 and Wyoming's Democrat got 12 %."""
    from .data_prep import _ST
    lean = _pres_lean(); E = pd.read_csv(ROOT / "data" / "static" / "house_national_vote_1946.csv").set_index("year").E
    rows = []
    for y in range(1998, 2023, 2):
        h = W.fetch(f"{y} United States gubernatorial elections", max_age_h=10 ** 6)
        T = None
        for tb in pd.read_html(io.StringIO(h)):
            cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tb.columns]
            if any(c.startswith("Candidates") for c in cols) and any(c.startswith("State") for c in cols) and len(tb) >= 10: tb.columns = cols; T = tb; break
        if T is None: continue
        cc = [c for c in T.columns if c.startswith("Candidates")][0]; sc = [c for c in T.columns if c.startswith("State")][0]
        stc = [c for c in T.columns if c.startswith("Status") or c.startswith("Result")]
        pc = [c for c in T.columns if c.startswith("Party")]
        for _, r in T.iterrows():
            st = _ST.get(re.sub(r"\[.*?\]", "", str(r[sc])).strip())
            if not st: continue
            c = [(p, float(v)) for p, v in re.findall(r"\((Democratic|Republican|DFL|Democratic–Farmer–Labor|Democratic-NPL)[^)]*\)\s*([\d.]+)%", str(r[cc]))]
            d = max([v for p, v in c if not p.startswith("Rep")], default=None); rp = max([v for p, v in c if p.startswith("Rep")], default=None)
            if d is None or rp is None: continue
            status = str(r[stc[0]]) if stc else ""; party = str(r[pc[0]]) if pc else ""
            ran = bool(re.search(r"re-?elected|lost re-?election|defeated", status, re.I)) and not re.search(r"lost renomination|lost (the )?primary", status, re.I)
            ip = 1.0 if party.startswith(("Democratic", "DFL")) else (-1.0 if party.startswith("Republican") else 0.0)
            rows.append({"year": y, "state": st, "margin": d - rp, "inc": ip if ran else 0.0, "lean": lean(y, st), "E": E.get(y)})
    return pd.DataFrame(rows).dropna(subset=["lean", "E", "margin"])


def history_polled():
    """One row per even-year polled D-v-R governor race 1998-2022 with the actual margin (538 raw_polls) - the backtest's poll source."""
    r = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False)
    g = r[(r.type_simple == "Gov-G") & (r.cycle % 2 == 0) & (r.location.str.len() == 2)].copy()
    g = g[(g.cand1_party.isin(["DEM", "REP"])) & (g.cand2_party.isin(["DEM", "REP"])) & (g.cand1_party != g.cand2_party)]
    g["d_act"] = np.where(g.cand1_party == "DEM", g.cand1_actual, g.cand2_actual); g["r_act"] = np.where(g.cand1_party == "REP", g.cand1_actual, g.cand2_actual)
    g["d_name"] = np.where(g.cand1_party == "DEM", g.cand1_name, g.cand2_name); g["r_name"] = np.where(g.cand1_party == "REP", g.cand1_name, g.cand2_name)
    R = g.groupby(["cycle", "location"]).agg(d=("d_act", "first"), r=("r_act", "first"), dn=("d_name", "first"), rn=("r_name", "first")).reset_index()
    R["margin"] = R.d - R.r; R["winner"] = np.where(R.margin > 0, R.dn, R.rn)
    prev = {(c, l): w for c, l, w in zip(R.cycle, R.location, R.winner)}
    def inc(c, l, dn, rn):
        w = prev.get((c - (2 if l in TWO_YEAR else 4), l))
        return 1.0 if w == dn else (-1.0 if w == rn else 0.0)
    R["inc"] = [inc(c, l, dn, rn) for c, l, dn, rn in zip(R.cycle, R.location, R.dn, R.rn)]
    lean = _pres_lean(); R["lean"] = [lean(c, l) for c, l in zip(R.cycle, R.location)]
    E = pd.read_csv(ROOT / "data" / "static" / "house_national_vote_1946.csv").set_index("year").E; R["E"] = R.cycle.map(E)
    return R.dropna(subset=["lean", "E", "margin"]).rename(columns={"cycle": "year", "location": "state"})


def fit(R):
    X = np.column_stack([np.ones(len(R)), R.lean, R.inc, R.E]); b = np.linalg.lstsq(X, R.margin, rcond=None)[0]
    return b, float((R.margin - X @ b).std())


def races():
    h = W.fetch("2026 United States gubernatorial elections")
    T = [t for t in pd.read_html(io.StringIO(h)) if "Candidates" in [str(c) for c in t.columns] and "State" in [str(c) for c in t.columns]][0]
    from .data_prep import _ST
    rows = []
    for _, r in T.iterrows():
        st = _ST.get(re.sub(r"\[.*?\]", "", str(r["State"])).strip())
        if not st: continue
        cands = [(n.strip(), p) for n, p in re.findall(r"▌([^▌\[]+?)\s*\(([^)]+)\)", str(r["Candidates"]))]
        party = str(r["Party"]); status = str(r["Status"]); gov = re.sub(r"\[.*?\]", "", str(r["Governor"])).strip()
        ip = "D" if party.startswith(("Democratic", "DFL")) else ("R" if party.startswith("Republican") else "O")
        running = bool(re.search(r"renominated|nominated to full term|running", status, re.I)) and not re.search(r"term.limited|retir|not running|lost", status, re.I)
        rows.append({"state": st, "governor": gov, "inc_party": ip, "status": status, "cands": cands, "inc_running": running})
    return pd.DataFrame(rows)


def votehub_polls(refresh=True):
    f = ROOT / "data" / "raw" / "votehub" / "governor.json"
    if refresh:                                    # at most once in 6 h (the daily run downloads it first, weekly._download_votehub)
        from . import fetch as F; F.get("https://api.votehub.com/polls?poll_type=governor", f, min_age_h=6)
    return json.loads(f.read_text())


def _sur(n): return re.sub(r"[^a-z]", "", str(n).split("(")[0].strip().split()[-1].lower()) if str(n).strip() else ""


def race_polls(R, refresh=True):
    from .data_prep import _ST
    out = []
    by = {r.state: r for r in R.itertuples()}
    for p in votehub_polls(refresh):
        m = re.match(r"2026 (.+)$", str(p.get("subject") or "")); st = _ST.get(m.group(1).strip()) if m else None
        if st not in by: continue
        party = {_sur(n): ("D" if pt.startswith(("Democratic", "DFL")) else "R" if pt == "Republican" else "O") for n, pt in by[st].cands}
        d = r_ = None; dn = rn = None; tot = other = 0.0
        for a in p.get("answers") or []:
            pct = float(a.get("pct") or 0); tot += pct; pty = party.get(_sur(a.get("choice")))
            if pty == "D" and (d is None or pct > d): d, dn = pct, a["choice"]
            elif pty == "R" and (r_ is None or pct > r_): r_, rn = pct, a["choice"]
        if d is None or r_ is None: continue
        if st == "AK":        # top-four + ranked choice: the first-round R (and D) vote is split, the final round consolidates it
            d = sum(float(a.get("pct") or 0) for a in p.get("answers") or [] if party.get(_sur(a.get("choice"))) == "D")
            r_ = sum(float(a.get("pct") or 0) for a in p.get("answers") or [] if party.get(_sur(a.get("choice"))) == "R")
        other = tot - d - r_
        tag = {"DEM": " (D)", "REP": " (R)"}.get(p.get("partisan"), "")
        out.append({"seat": st, "pollster": str(p.get("pollster")) + tag, "start_date": p.get("start_date") or p["end_date"], "end_date": p["end_date"],
                    "dem": d, "rep": r_, "margin": d - r_, "dem_name": dn, "rep_name": rn, "und": max(0.0, 100 - tot), "other": other,
                    "n": p.get("sample_size"), "grade": 1.5})
    df = pd.DataFrame(out); df["end_date"] = pd.to_datetime(df["end_date"]); df["src"] = "votehub"
    # One survey, one poll (2026-10-03). VoteHub lists each VERSION of a survey as its own entry - LV and RV, head-to-head and
    # full field (NYT/Siena PA 8/21 LV 55-39 and RV 54-37; Marquette WI 8/20 LV 49-44 and RV 44-44; Maine's NYT/Siena 6/26 with and
    # without Bennett) - and each was counting as a separate poll (Marquette's two versions held 33 % of the WI poll weight).
    # Versions are averaged into one row, as wiki_polls rule 3 does for the Senate and the Wikipedia governor tables.
    if len(df):
        # data-entry screen: a field period over 21 days is a typo, not a poll (VoteHub's Siena NY entry "2025-06-23 to 2026-06-26",
        # Hochul 44-19, is the June 2025 poll with the year mistyped - it was counting as a June 2026 poll)
        fl = (df["end_date"] - pd.to_datetime(df["start_date"])).dt.days
        bad = df[(fl > 21) | (fl < 0)]
        for b_ in bad.itertuples(): print(f"  !! governor VoteHub entry dropped (field {b_.start_date} to {b_.end_date.date()}): {b_.seat} {b_.pollster} {b_.dem}-{b_.rep}")
        df = df.drop(bad.index)
        # versions of one survey (same pollster, dates and matchup; a partisan tag on only some versions - TIPP MI 5/23 RV tagged REP,
        # LV not - is applied to the survey)
        df["pbase"] = df["pollster"].str.replace(r"\s*\((?:D|R)\)$", "", regex=True)
        key = ["seat", "pbase", "start_date", "end_date", "dem_name", "rep_name"]
        agg = {c: "first" for c in df.columns if c not in key}; agg.update({k: "mean" for k in ("dem", "rep", "margin", "und", "other")}); agg["n"] = "max"
        agg["pollster"] = lambda x: max(x, key=len)                    # the tagged spelling if any version carries one
        nv = len(df); df = df.groupby(key, as_index=False, sort=False).agg(agg).drop(columns="pbase")
        if nv > len(df): print(f"  governor VoteHub: {nv} entries -> {len(df)} surveys (versions averaged)")
    df = df.drop(columns=["start_date"])
    # second source (2026-09-22, user: "we need more polls"): the Wikipedia race pages, same on-ballot rules as the Senate.
    # Alaska stays VoteHub-only (its party-summed ranked-choice margin). Duplicates: same state, end dates within 3 days,
    # margins within 1.5 -> keep one.
    wk = []
    for r in R.itertuples():
        if r.state == "AK": continue
        try: w = W.governor_polls(r.state, r.cands)
        except Exception: continue
        if w is None or not len(w): continue
        w = w.assign(seat=r.state, grade=1.5, src="wiki")
        wk.append(w[["seat", "pollster", "end_date", "dem", "rep", "margin", "dem_name", "rep_name", "und", "other", "grade", "src"]])
    if wk:
        wk = pd.concat(wk, ignore_index=True); wk["end_date"] = pd.to_datetime(wk["end_date"])
        def dup(x): return M._same_poll(x.pollster, x.end_date, x.margin, df[df.seat == x.seat])
        wk = wk[~wk.apply(dup, axis=1)]
        df = pd.concat([df, wk], ignore_index=True)
    clean = lambda n: re.sub(r"\s*\(.*?\)", "", str(n)).strip()
    df["dem_name"] = df.dem_name.map(clean); df["rep_name"] = df.rep_name.map(clean)
    # VoteHub carries no campaign-internal flag (OR DHM 9/11 = Drazan internal ran untagged): borrow the feeds' sponsor tag
    df = W.borrow_feed_tags(df, "governor", lambda r: r["seat"])
    return df


NOW_D, NOW_R = 24, 26        # governors before the election (Wikipedia infobox, 2026-09-22); 18 of each are up


def run(E, asof=None, n=20000, seed=13, nat_z=None, refresh=True):
    from . import senate2026 as SN
    asof = pd.Timestamp(asof or pd.Timestamp.today().normalize())
    Hh = history(); b, sd = fit(Hh)
    R = races(); lean = _pres_lean(); R["lean"] = [lean(2026, s) for s in R.state]
    P = race_polls(R, refresh)
    # nominee = the highest-polling candidate of each party in the newest polls; the D-R poll margin is theirs
    corr = M.state_poll_correction(statewide=True); heat = M.heating_oil_shift("state"); G = SN.state_loadings()
    # the race's own systematic poll error = the measured total minus the shared statewide shock the simulation draws (governors feel
    # it at b[3]), as senate2026.race_sys (2026-10-03: it was counted twice)
    sys_r = float(np.sqrt(max(POLL_SYS ** 2 - (SN.SEN_S_NAT * b[3]) ** 2, 1.5 ** 2)))
    rows = []
    for r in R.itertuples():
        q = P[P.seat == r.state].copy()
        # nominees from the BALLOT when it lists exactly one of each (2026-10-03; it used to be whoever the newest poll named), and
        # polls kept by SURNAME - the exact-string match dropped Maine's "Robert B. Charles" rows against VoteHub's "Bobby Charles"
        # and New Mexico's "Greg Hull" against "Gregg Hull"
        bd = [c for c, pt in r.cands if pt.startswith(("Democratic", "DFL"))]; br = [c for c, pt in r.cands if pt == "Republican"]
        newest = q.sort_values("end_date") if len(q) else None
        dn = bd[0] if len(bd) == 1 else (newest.dem_name.iloc[-1] if newest is not None else (bd[0] if bd else None))
        rn = br[0] if len(br) == 1 else (newest.rep_name.iloc[-1] if newest is not None else (br[0] if br else None))
        if len(q) and r.state != "AK": q = q[(q.dem_name.map(_sur) == _sur(dn)) & (q.rep_name.map(_sur) == _sur(rn))]
        if len(q): q = PO.apply(q, r.state, "governor")       # exact figures for rounded scraped copies (manual exact=1)
        man = M.manual_race_polls(r.state, "governor", q if len(q) else None, names=(dn, rn))
        if len(man): q = pd.concat([q, man], ignore_index=True)
        if len(q): q = M.collapse_versions(q.assign(_race=r.state), "_race").drop(columns="_race")       # one survey, one row
        inc = (1.0 if r.inc_party == "D" else -1.0 if r.inc_party == "R" else 0.0) if r.inc_running else 0.0
        # Hispanic / Asian swing term (2026-10-03): the House and Senate carry it and the 2025 evidence for it is itself GOVERNOR
        # races (NJ, VA), yet the governor prior had no mean term while the simulation drew the group shocks. Scaled like the
        # Senate's (state_loadings is NAT_SLOPE-scaled): by the governors' own national slope b[3].
        gscale = b[3] / SN.NAT_SLOPE
        mu_prior = b[0] + b[1] * r.lean + b[2] * inc + b[3] * E + gscale * G.group_shift.get(r.state, 0.0)
        qq = M.prepare_race_polls(q.assign(margin=q.margin - corr.get(r.state, 0.0))) if len(q) else q
        sub = M.substate_polls(r.state, "governor", POLL_SD)
        if len(sub): qq = pd.concat([qq, sub.assign(margin=sub.margin - corr.get(r.state, 0.0))], ignore_index=True)
        pa = M.poll_average(qq, asof) if len(qq) else pd.DataFrame()
        pm, ne, vm = (pa.poll_margin.iloc[0], pa.n_eff.iloc[0], float(pa.vmult.iloc[0])) if len(pa) else (np.nan, np.nan, 1.0)
        wp, wq = 1 / sd ** 2, (1.0 / (POLL_SD ** 2 * vm / ne + sys_r ** 2) if ne == ne else 0.0)
        mu = (wp * mu_prior + wq * (pm if pm == pm else 0)) / (wp + wq)
        sd_r = float(np.sqrt(1 / (wp + wq)))
        if M.ROBUST_NU and len(qq): mu, sd_r = M.robust_blend(mu_prior, sd, qq, asof, POLL_SD, sys_r, M.ROBUST_NU)
        mu = mu + b[3] * heat.get(r.state, 0.0)
        rows.append({"state": r.state, "governor": r.governor, "inc_party": r.inc_party, "inc": inc, "dem": dn, "rep": rn, "lean": round(r.lean, 1),
                     "n_polls": int(len(qq)) if len(qq) else 0, "poll_margin": pm, "mu_prior": mu_prior, "mu": mu, "sd": sd_r,
                     "h_load": gscale * G.h_load.get(r.state, 0.0), "c_load": gscale * G.c_load.get(r.state, 0.0), "a_load": gscale * G.a_load.get(r.state, 0.0), "wnc_z": G.wnc_z.get(r.state, 0.0),
                     "group_shift": gscale * G.group_shift.get(r.state, 0.0),
                     "newest_poll": q.end_date.max().date().isoformat() if len(q) else None})
    S = pd.DataFrame(rows)
    # governors ride the national tide at b[3], not the Senate's 0.8: rescale the shared-draw simulation
    Sx = S.assign(elast=b[3] / SN.NAT_SLOPE)
    mg = SN.simulate_senate(Sx, n=n, seed=seed, nat_z=nat_z); d = mg > 0
    S["p_dem"] = d.mean(0)
    up_d = int((S.inc_party == "D").sum()); dg = (NOW_D - up_d) + d.sum(1)
    summ = {"mean": round(float(dg.mean()), 1), "p10": int(np.percentile(dg, 10)), "p90": int(np.percentile(dg, 90)), "p_d_majority": round(float((dg >= 26).mean()), 3),
            "up_d": up_d, "up_r": int((S.inc_party == "R").sum()), "now_d": NOW_D, "now_r": NOW_R}
    S.attrs["summary"] = summ
    return S, mg, {"coef": dict(zip(("const", "lean", "inc", "E"), np.round(b, 3))), "prior_sd": round(sd, 2), "n_hist": len(Hh)}


def backtest():
    """Leave-one-cycle-out prior (all races) + raw_polls polls within 3 weeks of the election, scored on the polled races."""
    Hall = history(); Hh = history_polled(); r = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False)
    g = r[(r.type_simple == "Gov-G") & (r.cycle % 2 == 0) & r.cand1_party.isin(["DEM", "REP"]) & r.cand2_party.isin(["DEM", "REP"])].copy()
    g["m"] = np.where(g.cand1_party == "DEM", g.margin_poll, -g.margin_poll)
    out = []
    for y in sorted(Hh.year.unique()):
        if y < 2006: continue
        b, sd = fit(Hall[Hall.year != y]); te = Hh[Hh.year == y]
        for t in te.itertuples():
            mp = b[0] + b[1] * t.lean + b[2] * t.inc + b[3] * t.E
            q = g[(g.cycle == y) & (g.location == t.state)].m
            if len(q): pm = q.mean(); ne = min(len(q), 6); wq = 1 / (POLL_SD ** 2 / ne + POLL_SYS ** 2)
            else: pm, wq = 0.0, 0.0
            wp = 1 / sd ** 2; mu = (wp * mp + wq * pm) / (wp + wq); s = np.sqrt(1 / (wp + wq) + 2.9 ** 2 * b[3] ** 2)
            from math import erf, sqrt
            p = 0.5 * (1 + erf(mu / (s * sqrt(2)))); out.append({"year": y, "state": t.state, "p": p, "y": float(t.margin > 0), "prior_only": 0.5 * (1 + erf(mp / (np.hypot(sd, 2.9 * b[3]) * sqrt(2))))})
    B = pd.DataFrame(out)
    print(f"governor backtest 2006-2022 ({len(B)} races, polls in the last 3 weeks): Brier blend {np.mean((B.p - B.y) ** 2):.4f}, prior only {np.mean((B.prior_only - B.y) ** 2):.4f}")
    print(B.groupby(pd.cut(B.p, [0, .1, .3, .5, .7, .9, 1], include_lowest=True), observed=True).agg(n=("y", "size"), pred=("p", "mean"), actual=("y", "mean")).round(3).to_string())
    return B


if __name__ == "__main__":
    Hh = history(); b, sd = fit(Hh)
    print(f"governor prior on {len(Hh)} races (all, Wikipedia) 1998-2022: const {b[0]:+.2f}, lean {b[1]:.3f}, incumbency {b[2]:.2f}, national E {b[3]:.3f}, resid sd {sd:.2f}")
    backtest()
