"""State-legislative chamber forecasts (Phase 1, 2026-10-04; behind MIDTERMS_STATELEG=on, default OFF).

Chambers: MI, MN, WI, AZ, PA, NH, NC - both chambers each. Every seat's expected Democratic two-party margin is
    mu_i = a + beta * lean_i + c * inc_i + g * E + kappa * shift_s
  lean_i  district presidential two-party margin minus the national one (2024 president on the 2026 maps; built by
          stateleg_build from MEDSL precinct returns joined to the same precinct's legislative district labels)
  inc_i   +1 Democratic incumbent on the ballot, -1 Republican, 0 open
  E       the model's national environment (generic-ballot trend, no directional correction)
  shift_s the state's own signal: how far this state's Senate / governor race polls pull those races away from their
          fundamentals priors, net of the national average pull (the national part is E's job)
Simulation: margin = mu + g * s_E * Z (the House's national draw) + kappa * (the state's statewide-race surprise in the same
simulation) + state error (shared by both chambers, part chamber-specific) + seat error (t5). Multi-member districts (AZ House,
NH House) elect their top-k candidates: each candidate scores its party's share of the district (50 +- margin/2), plus an
incumbency bonus, plus its own deviation from the slate (sd fitted on multi-member returns); a party running fewer candidates
than seats can win at most that many. Seats with no Democrat or no Republican nominee are fixed. Staggered senates (PA, WI) add
the held-over seats by their current holders.

Fitted parameters (fit() -> data/static/stateleg/params.json; every value from the committed derived tables):
  c, g, state-cycle error, between-chamber correlation  - Klarner state legislative returns 1972-2022, consecutive elections in
      the same district under the same map (swing model with state-chamber-year effects)
  beta, a, seat error                                   - 2018 (2016 president lean) and 2022 (2020 president lean) results
  kappa                                                 - Klarner 4-year state legislative swing residuals v the change in the
      same state's governor-race residual (Wikipedia results, gov2026.history)
  candidate slate sd                                    - multi-member returns (Klarner, AZ/NH)
"""
from __future__ import annotations

import json, os
from pathlib import Path
import numpy as np, pandas as pd
from .paths import STATIC, CACHE, RAW, ROOT

SL = STATIC / "stateleg"
PARAMS = SL / "params.json"
ON = os.environ.get("MIDTERMS_STATELEG", "on").lower() in ("on", "1", "true")     # published as experimental since 2026-10-05
# The state-poll signal (kappa x the pull of the state's Senate / governor polls, and kappa x their simulated surprise) is fitted
# (kappa 0.074, se 0.024) and implemented, but TESTED AND NOT ADOPTED (2026-10-05): in the 2018/2022 backtest it changed seat log loss
# by +0.002 / -0.001 and chamber-control Brier 0.119 -> 0.125. Off unless MIDTERMS_STATELEG_KAPPA=on.
KAPPA_ON = os.environ.get("MIDTERMS_STATELEG_KAPPA", "off").lower() in ("on", "1", "true")
T_DF = 5

# (state, chamber, name, seats, up-rule, tie rule). up: "all" | "odd" (odd-numbered districts up in 2026)
# tie: "shared" | "gov" (the lieutenant governor elected with the 2026 governor breaks ties) | "D" / "R" (sitting tie-breaker)
CHAMBERS = [
    ("MI", "upper", "Michigan Senate", 38, "all", "gov"),
    ("MI", "lower", "Michigan House", 110, "all", "shared"),
    ("MN", "upper", "Minnesota Senate", 67, "all", "shared"),
    ("MN", "lower", "Minnesota House", 134, "all", "shared"),
    ("WI", "upper", "Wisconsin Senate", 33, "odd", "shared"),
    ("WI", "lower", "Wisconsin Assembly", 99, "all", "shared"),
    ("AZ", "upper", "Arizona Senate", 30, "all", "shared"),
    ("AZ", "lower", "Arizona House", 60, "all", "shared"),
    ("PA", "upper", "Pennsylvania Senate", 50, "odd", "gov"),
    ("PA", "lower", "Pennsylvania House", 203, "all", "shared"),
    ("NH", "upper", "New Hampshire Senate", 24, "all", "shared"),
    ("NH", "lower", "New Hampshire House", 400, "all", "shared"),
    ("NC", "upper", "North Carolina Senate", 50, "all", "D"),       # Lt Gov Rachel Hunt (D, elected 2024) breaks ties
    ("NC", "lower", "North Carolina House", 120, "all", "shared"),
    ("GA", "lower", "Georgia House", 180, "all", "shared"),          # added 2026-10-05 (user)
    ("IA", "lower", "Iowa House", 100, "all", "shared"),
    ("TX", "lower", "Texas House", 150, "all", "shared"),
]
SUPER = {("NC", "upper"): 30, ("NC", "lower"): 72}                    # 3/5 veto-override thresholds
EXPERIMENTAL = {("NH", "lower"): "candidate lists are not on Wikipedia: every seat is treated as contested by full slates; "
                                 "floterial districts are simulated as ordinary multi-member districts",
                ("IA", "lower"): "the chamber page describes candidates in prose: retirements are read from it, but most districts' "
                                 "nominees are not, so a seat is treated as contested unless the page says a party has no candidate",
                ("MI", "upper"): "no 2024 Senate election: district leans are 2020 precinct results moved to 2024 by each House district's "
                                 "measured swing, on the 2022 Senate map (the court-ordered 2026 redraw of the Detroit-area districts is not "
                                 "yet in the Census boundary files)"}


# ------------------------------------------------------------------ data

def nat_pres():
    """National presidential two-party margin by year (MIT, the file the governor/Senate models read)."""
    p = pd.read_csv(RAW / "mit" / "president_1976_2024.csv"); p = p[p.party_simplified.isin(["DEMOCRAT", "REPUBLICAN"])]
    v = p.groupby(["year", "party_simplified"]).candidatevotes.sum().unstack()
    return (100 * (v.DEMOCRAT - v.REPUBLICAN) / (v.DEMOCRAT + v.REPUBLICAN)).to_dict()


def nat_house():
    return pd.read_csv(STATIC / "house_national_vote_1946.csv").set_index("year")["E"].to_dict()


def klarner():
    k = pd.read_csv(SL / "klarner.csv.gz", dtype=str, keep_default_na=False)
    k = k[k["etype"] == "g"].copy()
    for c in ("year", "dseats", "eseats", "n_d", "n_r", "n_o", "w_d", "w_r", "w_o", "inc_d", "inc_r", "inc_o"):
        k[c] = pd.to_numeric(k[c], errors="coerce").fillna(0).astype(int)
    for c in ("d_votes", "r_votes", "o_votes", "top_d", "top_r"):
        k[c] = pd.to_numeric(k[c], errors="coerce").fillna(0.0)
    k["chamber"] = np.where(k["sen"] == "1", "upper", "lower")
    k["district"] = [kl_district(s, ch, dno, dname, dz) for s, ch, dno, dname, dz in zip(k["sab"], k["chamber"], k["dno"], k["dname"], k["ddez"])]
    return k


def kl_district(st, ch, dno, dname, ddez):
    try: n = int(float(dno))
    except Exception: return None
    if st == "MN" and ch == "lower":
        import re
        m = re.search(r"(\d+)\s*-?\s*([AB])", str(ddez).upper())
        return f"{int(m.group(1))}{m.group(2)}" if m else str(n)
    if st == "NH" and ch == "lower": return f"{str(dname).strip().title()} {n}"
    return str(n)


def kl_inc(q):
    return np.where((q["inc_d"] > 0) & (q["inc_r"] == 0), 1, np.where((q["inc_r"] > 0) & (q["inc_d"] == 0), -1, 0))


def kl_margin(q):
    return 100 * (q["d_votes"] - q["r_votes"]) / (q["d_votes"] + q["r_votes"]).where(lambda x: x > 0)


# ------------------------------------------------------------------ fitting

def fit_swing(k, before=None):
    """Swing model on consecutive elections in the same single-member district and map: dm = c*dinc + alpha_{state,chamber,year};
    alpha = g*dE + u. Returns c, g, sd(u) (2-year state-cycle swing error), rho (upper/lower u correlation), n."""
    E = nat_house()
    q = k[(k["eseats"] == 1) & (k["dseats"] == 1) & (k["n_d"] == 1) & (k["n_r"] == 1) & (k["d_votes"] > 0) & (k["r_votes"] > 0)].copy()
    q = q[q["district"].notna()]
    if before: q = q[q["year"] < before]
    q["m"] = kl_margin(q); q["inc"] = kl_inc(q)
    q = q.sort_values("year"); grp = ["sab", "chamber", "district", "regime"]
    q["m0"] = q.groupby(grp)["m"].shift(); q["inc0"] = q.groupby(grp)["inc"].shift(); q["y0"] = q.groupby(grp)["year"].shift()
    d = q.dropna(subset=["m0"]).copy(); d = d[(d["year"] - d["y0"]).isin([2, 4]) & (d["year"] % 2 == 0)]
    d["dm"] = d["m"] - d["m0"]; d["dinc"] = d["inc"] - d["inc0"]
    d["dE"] = d["year"].map(E) - d["y0"].map(E); d = d.dropna(subset=["dE"])
    d["cell"] = d["sab"] + d["chamber"] + d["year"].astype(str) + d["y0"].astype(str)
    # within-cell regression for c
    dm_w = d["dm"] - d.groupby("cell")["dm"].transform("mean"); di_w = d["dinc"] - d.groupby("cell")["dinc"].transform("mean")
    c = float((dm_w * di_w).sum() / (di_w ** 2).sum())
    cell = d.assign(r=d["dm"] - c * d["dinc"]).groupby("cell").agg(a=("r", "mean"), n=("r", "size"), dE=("dE", "first"), sab=("sab", "first"),
                                                                       ch=("chamber", "first"), year=("year", "first"))
    cell = cell[cell["n"] >= 10]
    w = cell["n"].clip(upper=60); X = np.c_[np.ones(len(cell)), cell["dE"]]
    b = np.linalg.lstsq(X * np.sqrt(w.values)[:, None], cell["a"] * np.sqrt(w.values), rcond=None)[0]
    cell["u"] = cell["a"] - X @ b
    # sampling noise in a cell mean: e-variance / n
    e = (dm_w - c * di_w); se2 = float(e.var()) / cell["n"]
    sd_u = float(np.sqrt(max(np.average(cell["u"] ** 2, weights=w) - np.average(se2, weights=w), 0.1)))
    pv = cell.pivot_table(index=["sab", "year"], columns="ch", values="u").dropna()
    rho = float(np.corrcoef(pv["lower"], pv["upper"])[0, 1]) if len(pv) > 10 else 0.5
    return {"c": round(c, 3), "g": round(float(b[1]), 3), "g_const": round(float(b[0]), 3), "sd_u2": round(sd_u, 3), "rho_ch": round(rho, 3),
            "n_pairs": int(len(d)), "n_cells": int(len(cell)), "sd_e_diff": round(float(e.std()), 3)}


def fit_kappa(k, c, g, before=None):
    """kappa: state legislative 4-year swing residual (alpha - g dE) regressed on the change in the same state's governor-race
    residual between consecutive elections (governor prior: margin ~ lean + inc + E over every race 1998-2022)."""
    from . import gov2026 as GV
    H = GV.history()
    if before: H = H[H["year"] < before]
    X = np.c_[np.ones(len(H)), H["lean"], H["inc"], H["E"]]; b = np.linalg.lstsq(X, H["margin"], rcond=None)[0]
    H["r"] = H["margin"] - X @ b
    H = H.sort_values("year"); H["r0"] = H.groupby("state")["r"].shift(); H["y0"] = H.groupby("state")["year"].shift()
    H = H[(H["year"] - H["y0"]) == 4].dropna(subset=["r0"]); H["dr"] = H["r"] - H["r0"]
    E = nat_house()
    q = k[(k["eseats"] == 1) & (k["dseats"] == 1) & (k["n_d"] == 1) & (k["n_r"] == 1) & (k["d_votes"] > 0) & (k["r_votes"] > 0)].copy()
    if before: q = q[q["year"] < before]
    q["m"] = kl_margin(q); q["inc"] = kl_inc(q)
    key = ["sab", "chamber", "district", "regime"]
    a = q[key + ["year", "m", "inc"]]; b4 = a.assign(year=a["year"] + 4)
    j = a.merge(b4, on=key + ["year"], suffixes=("", "0"))
    j["r"] = (j["m"] - j["m0"]) - c * (j["inc"] - j["inc0"]) - g * (j["year"].map(E) - (j["year"] - 4).map(E))
    cell = j.groupby(["sab", "chamber", "year"]).agg(u=("r", "mean"), n=("r", "size")).reset_index()
    cell = cell[cell["n"] >= 10].merge(H[["state", "year", "dr"]], left_on=["sab", "year"], right_on=["state", "year"])
    if len(cell) < 20: return {"kappa": 0.0, "n": int(len(cell))}
    w = cell["n"].clip(upper=60).values; x = cell["dr"].values; y = cell["u"].values
    xm, ym = np.average(x, weights=w), np.average(y, weights=w)
    kap = float(np.sum(w * (x - xm) * (y - ym)) / np.sum(w * (x - xm) ** 2))
    res = y - ym - kap * (x - xm); se = float(np.sqrt(np.sum(w * res ** 2) / np.sum(w) / np.sum(w * (x - xm) ** 2) * np.sum(w) / (len(x) - 2)))
    # cluster-robust by state would be wider; reported as is with n
    return {"kappa": round(kap, 3), "kappa_se": round(se, 3), "n": int(len(cell)), "gov_prior": [round(float(v), 3) for v in b]}


def hist_table(cycle):
    """Seats up in a past cycle (target states) with lean, incumbency, slates and the result (Klarner)."""
    k = klarner(); np_ = nat_pres(); L = pd.read_csv(SL / "lean_hist.csv", dtype={"district": str})
    L = L[L["cycle"] == cycle].copy(); L["lean"] = L["lean"] - np_[int(L["pres_year"].iloc[0])]
    L["pref"] = L["src"].str.contains("tiger").astype(int)               # shapes x TIGER over town-name matching where both exist
    L = L.sort_values("pref", ascending=False).drop_duplicates(["state", "chamber", "district"])
    q = k[(k["year"] == cycle) & k["sab"].isin([c[0] for c in CHAMBERS])].copy()
    q = q.rename(columns={"sab": "state"})
    q = q.merge(L[["state", "chamber", "district", "lean"]], on=["state", "chamber", "district"], how="left")
    q["k"] = q["eseats"].clip(lower=1); q["inc"] = kl_inc(q); q["m"] = kl_margin(q)
    q = q[q.groupby(["state", "chamber"])["district"].transform("size") >= 5]        # a chamber not up (a lone special) is dropped
    return q.reset_index(drop=True)


def fit_levels(cycles=(2018, 2022), c=None, g=None):
    """beta (lean slope), a (intercept net of g*E), seat error sd and state-chamber-cycle error sd on single-member contested seats."""
    E = nat_house(); rows = []
    for y in cycles:
        q = hist_table(y)
        q = q[(q["k"] == 1) & (q["n_d"] == 1) & (q["n_r"] == 1) & q["lean"].notna() & q["m"].notna()].copy(); q["year"] = y; rows.append(q)
    q = pd.concat(rows, ignore_index=True); q["cell"] = q["state"] + q["chamber"] + q["year"].astype(str)
    # within-cell OLS for beta (and c for comparison)
    def w(v): return v - q.groupby("cell")[v.name].transform("mean")
    Xw = np.c_[w(q["lean"]), w(q["inc"].astype(float))]; yw = w(q["m"])
    bb = np.linalg.lstsq(Xw, yw, rcond=None)[0]; beta, c_lvl = float(bb[0]), float(bb[1])
    cc = c if c is not None else c_lvl
    q["r0"] = q["m"] - beta * q["lean"] - cc * q["inc"]
    cell = q.groupby("cell").agg(a=("r0", "mean"), n=("r0", "size"), year=("year", "first"), state=("state", "first"), chamber=("chamber", "first"))
    cell["aE"] = cell["a"] - g * cell["year"].map(E)
    a = float(np.average(cell["aE"], weights=cell["n"].clip(upper=60)))
    resid = q["r0"] - q["cell"].map(cell["a"])
    sd_d = float(resid.std()); se2 = sd_d ** 2 / cell["n"]
    sd_u = float(np.sqrt(max(np.average((cell["aE"] - a) ** 2, weights=cell["n"].clip(upper=60)) - np.average(se2), 0.1)))
    return {"beta": round(beta, 3), "c_levels": round(c_lvl, 3), "a": round(a, 3), "sd_seat": round(sd_d, 3), "sd_state_level": round(sd_u, 3),
            "n_seats": int(len(q)), "n_cells": int(len(cell)), "cells": {k_: round(float(v - a), 2) for k_, v in cell["aE"].items()}}


def fit_slate(k, before=None):
    """sd of a candidate's votes relative to its party slate's mean in multi-member districts with full two-party slates."""
    q = k[(k["eseats"] >= 2) & (k["n_d"] == k["eseats"]) & (k["n_r"] == k["eseats"])]
    if before: q = q[q["year"] < before]
    # (top - mean) / mean for each party: for k candidates, top - mean ~ sd * E[max of k standard normals - mean]
    em = {2: 0.564, 3: 0.846, 4: 1.029, 5: 1.163, 6: 1.267, 7: 1.352, 8: 1.424, 9: 1.485, 10: 1.539}
    vals = []
    for p in ("d", "r"):
        mean = q[f"{p}_votes"] / q["eseats"]; rel = (q[f"top_{p}"] - mean) / mean
        f = q["eseats"].map(em); ok = f.notna() & np.isfinite(rel)
        vals.append((rel[ok] / f[ok]).values)
    v = np.concatenate(vals)
    return {"slate_sd": round(float(np.median(v)), 4), "n": int(len(v))}


def fit(before=None, cycles=(2018, 2022), save=True):
    k = klarner()
    sw = fit_swing(k, before)
    lv = fit_levels(cycles, c=sw["c"], g=sw["g"])
    kp = fit_kappa(k, sw["c"], sw["g"], before)
    sl = fit_slate(k, before)
    # state error: the levels estimate (target states, 2 cycles) is noisy; the swing model's 2-year state-cycle swing error
    # over all states is the better-measured quantity: a level error that persists ~half from cycle to cycle has sd(u2)/sqrt(2(1-r));
    # take the levels value floored at sd_u2 / sqrt(2) (independent cycles)
    sd_state = max(lv["sd_state_level"], sw["sd_u2"] / np.sqrt(2))
    P = {"c": sw["c"], "g": sw["g"], "beta": lv["beta"], "a": lv["a"], "sd_seat": lv["sd_seat"], "sd_state": round(float(sd_state), 3),
         "rho_ch": sw["rho_ch"], "kappa": kp["kappa"], "slate_sd": sl["slate_sd"], "fit": {"swing": sw, "levels": lv, "kappa": kp, "slate": sl},
         "before": before, "cycles": list(cycles)}
    if save: PARAMS.write_text(json.dumps(P, indent=1))
    return P


# ------------------------------------------------------------------ seat tables

def seats_2026():
    """Districts up in 2026 with lean, slate sizes, incumbency, fixed winners; and the held-over seats."""
    np_ = nat_pres(); L = pd.read_csv(SL / "lean_2026.csv", dtype={"district": str})
    L["lean"] = L["lean24"] - np_[2024]
    R = pd.read_csv(SL / "medsl_results.csv.gz", dtype={"district": str})
    mag = R[(R["year"] == 2024) & ~R["special"]].groupby(["state", "chamber", "district"])["magnitude"].max().rename("k").reset_index()
    OS = pd.read_csv(SL / "openstates_current.csv", dtype={"district": str})
    OS["party"] = OS["party"].map(lambda p: "D" if str(p).startswith("Democratic") else ("R" if str(p).startswith("Republican") else "O"))
    OS["district"] = [_os_dist(s, c, d) for s, c, d in zip(OS["state"], OS["chamber"], OS["district"])]
    try: C = pd.read_csv(CACHE / "stateleg_candidates.csv", dtype={"district": str})
    except FileNotFoundError: C = pd.DataFrame(columns=["state", "chamber", "district", "n_dem", "n_rep", "dem", "rep", "inc_names", "inc_marks"])
    C = official_nominees(C)
    rows, held = [], []
    for st, ch, name, n, up, tie in CHAMBERS:
        Lq = L[(L["state"] == st) & (L["chamber"] == ch)].set_index("district")
        dists = sorted(Lq.index, key=lambda d: (int("".join(x for x in d if x.isdigit()) or 0) if st != "NH" else 0, d))
        ret = " ".join(C.loc[(C["state"] == st) & (C["chamber"] == ch) & (C["district"] == "_retirements"), "dem"].fillna("").astype(str)).lower()
        for d in dists:
            num = int("".join(x for x in d if x.isdigit()) or 0)
            km = mag[(mag.state == st) & (mag.chamber == ch) & (mag.district == d)]["k"]
            k_ = int(km.iloc[0]) if len(km) else (2 if (st, ch) == ("AZ", "lower") else 1)
            hold = OS[(OS.state == st) & (OS.chamber == ch) & (OS.district == d)]
            if up == "odd" and num % 2 == 0:
                held.append({"state": st, "chamber": ch, "district": d, "party": hold["party"].iloc[0] if len(hold) else None,
                             "member": hold["name"].iloc[0] if len(hold) else None}); continue
            c = C[(C["state"] == st) & (C["chamber"] == ch) & (C["district"] == d)]
            c = c.iloc[0] if len(c) else None
            nd = c["n_dem"] if c is not None and pd.notna(c.get("n_dem")) else np.nan
            nr = c["n_rep"] if c is not None and pd.notna(c.get("n_rep")) else np.nan
            known = (nd == nd) and (nr == nr)                   # both slates known (general-election box / breakdown table)
            inc_d = inc_r = 0
            for _, h in hold.iterrows():
                running = _inc_running(h, c, ret)
                if running and h["party"] == "D": inc_d += 1
                if running and h["party"] == "R": inc_r += 1
            rows.append({"state": st, "chamber": ch, "district": d, "k": k_, "lean": float(Lq.at[d, "lean"]), "lean_src": Lq.at[d, "src"],
                         "n_d": int(min(nd, k_)) if nd == nd else k_, "n_r": int(min(nr, k_)) if nr == nr else k_, "cands_known": bool(known),
                         "inc_d": inc_d, "inc_r": inc_r, "members": "; ".join(f"{h['name']} ({h['party']})" for _, h in hold.iterrows()),
                         "dem": c["dem"] if c is not None and isinstance(c.get("dem"), str) else "", "rep": c["rep"] if c is not None and isinstance(c.get("rep"), str) else ""})
    S = pd.DataFrame(rows); S["inc"] = np.where((S["k"] == 1) & (S["inc_d"] > 0), 1, np.where((S["k"] == 1) & (S["inc_r"] > 0), -1, 0))
    return S, pd.DataFrame(held)


OFFICIAL = {("GA", "lower"): "ga_house_nominees_2026.csv"}     # official nominee tables (ga_sos.py) replace the Wikipedia parse


def official_nominees(C):
    """Georgia House: nominees from the Georgia Secretary of State's official primary + runoff results (ga_sos.build). A party with no
    State House primary candidate in a district has no nominee (Georgia lists unopposed primary candidates on the ballot)."""
    for (st, ch), f in OFFICIAL.items():
        try: T = pd.read_csv(SL / f, dtype={"district": str}, keep_default_na=False)
        except FileNotFoundError: continue
        C = C[~((C["state"] == st) & (C["chamber"] == ch) & (C["district"] != "_retirements"))]
        add = pd.DataFrame({"state": st, "chamber": ch, "district": T["district"], "n_dem": (T["d_nominee"] != "").astype(int),
                            "n_rep": (T["r_nominee"] != "").astype(int), "n_oth": 0, "dem": T["d_nominee"], "rep": T["r_nominee"],
                            "inc_marks": "", "inc_names": "", "source": "Georgia SOS"})
        C = pd.concat([C, add], ignore_index=True)
    return C


def _os_dist(st, ch, d):
    s = str(d).strip()
    if st == "NH" and ch == "lower":
        import re
        m = re.match(r"([A-Za-z]+)\D*(\d+)", s); return f"{m.group(1).title()} {int(m.group(2))}" if m else s
    if st == "MN" and ch == "lower":
        import re
        m = re.match(r"0*(\d+)\s*([AB])", s.upper()); return f"{int(m.group(1))}{m.group(2)}" if m else s
    try: return str(int(s))
    except ValueError: return s


def _surname(n):
    import re
    p = [x for x in re.sub(r"[^A-Za-z' -]", " ", str(n)).split() if x.lower() not in ("jr", "sr", "ii", "iii", "iv")]
    return p[-1].lower() if p else ""


def _inc_running(h, c, ret_text):
    """Is this sitting member on the 2026 general ballot? Nominee list when the page has one; else not if the summary table marks
    the member with a dagger or the name is in the page's retirement / outgoing lists; else assumed running."""
    sn = _surname(h["name"])
    side = {"D": "n_dem", "R": "n_rep"}.get(h["party"])
    if c is not None and side and pd.notna(c.get(side)) and c.get("source") != "prose":   # that party's nominee list is known
        names = f"{c.get('dem') or ''}; {c.get('rep') or ''}".lower()
        return bool(sn) and sn in names
    if c is not None and isinstance(c.get("inc_names"), str):
        for x in c["inc_names"].split("; "):
            nm, pty, retiring, status = (x.split("|") + ["", "", "", ""])[:4]
            if _surname(nm) == sn:
                if retiring == "1" or "retir" in status.lower() or "not running" in status.lower(): return False
    full = str(h["name"]).lower()
    if full and full in ret_text: return False
    return True


# ------------------------------------------------------------------ simulation

def _t(rng, size, sd):
    return rng.standard_t(T_DF, size) * sd * np.sqrt((T_DF - 2) / T_DF)


def simulate(S, P, n, rng, nat=None, state_sig=None, shift=None):
    """D seats won per simulation per district row -> array [n, rows] (0..k). nat: [n] national margin shock in seat units
    (already x g); state_sig: {state: [n] statewide-race surprise, national part removed}; shift: {state: mean pull}."""
    st = S["state"].values; ch = S["chamber"].values
    states = sorted(set(st)); si = {s: i for i, s in enumerate(states)}
    mu = P["a"] + P["beta"] * S["lean"].values + P["c"] * S["inc"].values + P["g"] * S["E"].values
    if shift: mu = mu + P["kappa"] * np.array([shift.get(s, 0.0) for s in st])
    sd_s = P["sd_state"]; rho = min(max(P["rho_ch"], 0.0), 1.0)
    common = np.zeros((n, len(states)))
    for s in states:
        j = si[s]; sig = state_sig.get(s) if state_sig else None
        if sig is not None and P["kappa"] != 0:
            ks = P["kappa"] * sig; rest = max(sd_s ** 2 * rho - float(np.var(ks)), 0.0)
            common[:, j] = ks + _t(rng, n, np.sqrt(rest))
        else: common[:, j] = _t(rng, n, sd_s * np.sqrt(rho))
    cham = {}
    for key in sorted(set(zip(st, ch))): cham[key] = _t(rng, n, sd_s * np.sqrt(1 - rho))
    m = mu[None, :] + common[:, [si[s] for s in st]] + np.stack([cham[(a, b)] for a, b in zip(st, ch)], 1) + _t(rng, (n, len(S)), P["sd_seat"])
    if nat is not None: m = m + nat[:, None]
    return m, mu


def seats_won(S, m, P, rng):
    """[n, rows] Democratic seats won from simulated district margins: single-member = margin > 0; multi-member = top-k candidates."""
    n = m.shape[0]; out = np.zeros(m.shape, dtype=np.int16)
    k = S["k"].values; nd = S["n_d"].values; nr = S["n_r"].values
    single = (k == 1)
    con = single & (nd > 0) & (nr > 0)
    out[:, con] = (m[:, con] > 0)
    out[:, single & (nd > 0) & (nr == 0)] = 1
    for j in np.where(~single)[0]:
        kk, a, b = int(k[j]), int(nd[j]), int(nr[j])
        if a == 0: continue
        if b == 0: out[:, j] = min(a, kk); continue
        sD = 50 + m[:, j] / 2; sR = 50 - m[:, j] / 2
        incb = P["c"] / 2
        dsc = sD[:, None] * (1 + rng.normal(0, P["slate_sd"], (n, a))); rsc = sR[:, None] * (1 + rng.normal(0, P["slate_sd"], (n, b)))
        di, ri = int(S["inc_d"].values[j]), int(S["inc_r"].values[j])
        if di: dsc[:, :min(di, a)] += incb
        if ri: rsc[:, :min(ri, b)] += incb
        allsc = np.concatenate([dsc, rsc], 1); top = np.argsort(-allsc, axis=1)[:, :kk]
        out[:, j] = (top < a).sum(1)
    return out


def control(dseats, total, tie, gov_d=None):
    """-> arrays (D control, R control, shared) per simulation."""
    half = total / 2
    dmaj = dseats > half; rmaj = (total - dseats) > half; tie_ = ~dmaj & ~rmaj
    if tie == "D": return dmaj | tie_, rmaj, np.zeros_like(dmaj)
    if tie == "R": return dmaj, rmaj | tie_, np.zeros_like(dmaj)
    if tie == "gov" and gov_d is not None: return dmaj | (tie_ & gov_d), rmaj | (tie_ & ~gov_d), np.zeros_like(dmaj)
    return dmaj, rmaj, tie_


# ------------------------------------------------------------------ live run

def statewide_signal(races, Z):
    """From the Senate / governor runs: per state, the mean poll pull (mu - mu_prior) net of the national average pull, and per
    simulation the race surprise (draw - mu) with its projection on the national draw and the cross-state mean removed."""
    pull, sims = {}, {}
    allp = []
    for R, M_ in races:
        if R is None: continue
        for i, r in R.reset_index(drop=True).iterrows():
            if str(r.get("challenger_party", "D") or "D") != "D": continue        # independent-v-R races say little about the D-R legislative vote
            d = M_[:, i] - r["mu"]; zc = Z - Z.mean(); bz = float((d * zc).sum() / (zc ** 2).sum()); d = d - bz * zc
            pull.setdefault(r["state"], []).append(float(r["mu"] - r["mu_prior"])); sims.setdefault(r["state"], []).append(d)
            allp.append(float(r["mu"] - r["mu_prior"]))
    nat_pull = float(np.mean(allp)) if allp else 0.0
    shift = {s: float(np.mean(v)) - nat_pull for s, v in pull.items()}
    sig = {s: np.mean(v, 0) for s, v in sims.items()}
    if sig:
        mean_all = np.mean(np.stack(list(sig.values())), 0); sig = {s: v - mean_all for s, v in sig.items()}
    return shift, sig


def run_live(E, Z, s_E, SS=None, smg=None, GS=None, gmg=None, n=20000, seed=17, grid=None, P=None):
    P = P or json.loads(PARAMS.read_text())
    if not KAPPA_ON: P = {**P, "kappa": 0.0}
    rng = np.random.default_rng(seed)
    S, H = seats_2026(); S["E"] = E
    shift, sig = statewide_signal([(SS, smg), (GS, gmg)], np.asarray(Z))
    S = S[S["state"].isin([c[0] for c in CHAMBERS])].reset_index(drop=True)
    nat = P["g"] * s_E * np.asarray(Z[:n])
    m, mu = simulate(S, P, n, rng, nat=nat, state_sig={s: v[:n] for s, v in sig.items()}, shift=shift)
    W = seats_won(S, m, P, rng)
    gov_d = {}
    if GS is not None and gmg is not None:
        for i, r in GS.reset_index(drop=True).iterrows(): gov_d[r["state"]] = gmg[:n, i] > 0
    out = {"E": E, "params": {k: P[k] for k in ("a", "beta", "c", "g", "sd_seat", "sd_state", "rho_ch", "kappa", "slate_sd")},
           "shift": {s: round(v, 2) for s, v in shift.items()}, "chambers": []}
    S["p_d"] = (W / S["k"].values[None, :]).mean(0); S["mu"] = mu + P["kappa"] * S["state"].map(shift).fillna(0).values
    for st, ch, name, total, up, tie in CHAMBERS:
        idx = np.where((S["state"] == st) & (S["chamber"] == ch))[0]
        if not len(idx): continue
        hq = H[(H["state"] == st) & (H["chamber"] == ch)] if len(H) else H
        held_d = int((hq["party"] == "D").sum()) if len(hq) else 0; held_r = int((hq["party"] == "R").sum()) if len(hq) else 0
        ds = held_d + W[:, idx].sum(1)
        cd, cr, sh = control(ds, total, tie, gov_d.get(st))
        rec = {"state": st, "chamber": ch, "name": name, "seats": total, "up": int(S["k"].values[idx].sum()), "held_d": held_d, "held_r": held_r,
               "p_d": round(float(cd.mean()), 3), "p_r": round(float(cr.mean()), 3), "p_shared": round(float(sh.mean()), 3),
               "mean_d": round(float(ds.mean()), 1), "p10": int(np.percentile(ds, 10)), "p50": int(np.median(ds)), "p90": int(np.percentile(ds, 90)),
               "hist": {str(a): int(b) for a, b in zip(*np.unique(ds, return_counts=True))}, "tie_rule": tie,
               "now_d": int(len(_holders(st, ch, "D"))), "now_r": int(len(_holders(st, ch, "R"))),
               "experimental": EXPERIMENTAL.get((st, ch))}
        if (st, ch) in SUPER: rec["p_r_super"] = round(float(((total - ds) >= SUPER[(st, ch)]).mean()), 3)
        # tipping seats: per simulation, the district holding the seat that gives control (single-member) - sorted by D margin
        need = int(np.floor(total / 2) + 1) - held_d
        sub = S.iloc[idx]; single = (sub["k"] == 1).values
        if single.all() and 0 < need <= len(idx):
            order = np.argsort(-m[:, idx], axis=1); piv = order[:, need - 1]
            cnt = np.bincount(piv, minlength=len(idx)) / len(piv)
            top = np.argsort(-cnt)[:12]
            rec["tipping"] = [{"district": sub["district"].iat[t], "p_tip": round(float(cnt[t]), 3), "p_d": round(float(sub["p_d"].iat[t]), 3),
                               "lean": round(float(sub["lean"].iat[t]), 1), "inc": int(sub["inc"].iat[t]), "members": sub["members"].iat[t]} for t in top if cnt[t] > 0]
        else:
            close = sub.assign(c=np.abs(sub["p_d"] - 0.5)).sort_values("c").head(12)
            rec["tipping"] = [{"district": r.district, "p_tip": None, "p_d": round(float(r.p_d), 3), "lean": round(float(r.lean), 1), "inc": int(r.inc),
                               "k": int(r.k), "members": r.members} for r in close.itertuples()]
        rec["seat_list"] = [{"district": r.district, "k": int(r.k), "p_d": round(float(r.p_d), 3), "lean": round(float(r.lean), 1), "inc": int(r.inc),
                             "fixed": bool((r.n_d == 0) or (r.n_r == 0))} for r in sub.itertuples()]
        if grid is not None:
            rec["grid"] = {}
            for e in grid:
                sh_ = P["g"] * (e - E)
                Wg = seats_won(S.iloc[idx].reset_index(drop=True), m[:, idx] + sh_, P, np.random.default_rng(seed + 1))
                dsg = held_d + Wg.sum(1); a_, b_, c_ = control(dsg, total, tie, gov_d.get(st))
                rec["grid"][str(e)] = [round(float(a_.mean()), 3), round(float(dsg.mean()), 1)]
        out["chambers"].append(rec)
    return out, S


_OS = None
def _holders(st, ch, party):
    global _OS
    if _OS is None:
        _OS = pd.read_csv(SL / "openstates_current.csv", dtype={"district": str})
        _OS["p"] = _OS["party"].map(lambda p: "D" if str(p).startswith("Democratic") else ("R" if str(p).startswith("Republican") else "O"))
    q = _OS[(_OS.state == st) & (_OS.chamber == ch) & (_OS.p == party)]
    return q["name"].tolist()


if __name__ == "__main__":
    import sys
    if "fit" in sys.argv:
        P = fit(); print(json.dumps({k: v for k, v in P.items() if k != "fit"}, indent=1)); print(json.dumps(P["fit"], indent=1)[:4000])
