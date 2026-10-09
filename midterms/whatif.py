"""Data for the "What if?" page (web/whatif.html, 2026-10-03, user: "add some other fun statistical tools/sliders to the site to
see how things can change based on certain assumptions ... more meant to be educational than an actual bold call").

The page re-runs the simulation in the browser. It needs each race's INGREDIENTS, not just its odds:
  * every race's published mean and the pieces of its error (shared statewide / national shock, group factors, own error),
    exactly as model.simulate (House) and senate2026.simulate_senate (Senate, governors) draw them, so the page's defaults
    reproduce the published forecast;
  * for every polled race, its fundamentals prior and each poll's adjusted margin, error and robustness discount (from the
    poll record behind polls.html), so the page can re-blend when a slider changes how polls count. A precision-weighted
    blend reproduces the model's Student-t posteriors to 0.1 pt RMS (max 0.3; checked 2026-10-03); the page adds only the
    CHANGE that blend implies, so at default settings every race sits exactly on the published mean;
  * each poll's pollster track record (midterms/pollster_record.py, every cycle to date) for the "trust the best
    pollsters" slider - tested as a model change and NOT adopted (a wash), offered here as an assumption to play with.
    python -m midterms.whatif          (normally called by build_web after the poll page is written)
"""
import json
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# statewide (Senate + governor) poll miss, polls in the last 21 days, mean over races, + = polls overstated Democrats
# (538 raw_polls 2010-2022; 2024 Senate from the 538 archive, polls ending Oct 15 on)
HIST_MISS = {"2010": -0.3, "2012": -2.9, "2014": 3.4, "2016": 4.8, "2018": 0.1, "2020": 6.8, "2022": 1.1, "2024": 3.9}


def _r(x, k=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), k)


def _poll_records():
    P = json.loads((ROOT / "web" / "data" / "polls.json").read_text())
    try:
        from . import pollster_record as LF
        S, ids = LF.scores(2027), LF.name_ids()
        from . import model as M
        def score(name):
            k = M._xnorm(name); i = ids.get(k)
            if i is not None and i in S: return S[i][0]
            if "/" in k:                                     # joint poll: its best-rated partner (lowest excess miss)
                v = [S[ids[x.strip()]][0] for x in k.split("/") if ids.get(x.strip()) in S]
                if v: return min(v)
            return 0.0
    except Exception as e:
        print("  whatif: pollster scores unavailable:", str(e)[:100]); score = lambda n: 0.0
    out = {}
    for r in P["races"]:
        ps = [p for p in r["polls"] if p.get("sd")]
        if not ps: continue
        out[(r["office"], r["seat"])] = {
            "prior": _r(r["prior"]), "prior_sd": _r(r["prior_sd"]),
            "polls": [[_r(p["adjusted"]), _r(p["sd"]), _r(p.get("discount", 1.0), 3), _r(score(p["pollster"]), 2), p["pollster"][:40], p["end"]] for p in ps]}
    return out, P["constants"]


def export(s, P, SS, GS, gslope, d_up, gov_up_d, E_now, mood, path=None):
    from . import model as M, senate2026 as SN, gov2026 as GV
    rec, C = _poll_records()
    sysd = {"house": C["house_poll_sys"], "senate": C["sen_poll_sys"], "governor": C["gov_poll_sys"]}
    T = M.T_DF
    # 2026-10-09: the House simulation draws the demographic factors with their POSTERIOR sds (run2026.run: model.factor_update ->
    # dataclasses.replace(P, **post)); the page used the prior sds (Hispanic / Asian 15, WNC 11.4, urban 2) and left out the regional
    # Hispanic factors (Texas / Florida / West, since 2026-10-04), so its House races did not reproduce the published odds
    post = (getattr(s, "attrs", None) or {}).get("factor_post_sd") or {}
    if post:
        from dataclasses import replace
        P = replace(P, **{k: float(v) for k, v in post.items() if hasattr(P, k)})
    # ---- House: model.simulate's pieces
    st_names = sorted(set(s["state"])); si = {x: i for i, x in enumerate(st_names)}
    resf = np.where(s["n_eff"].notna(), s["mu_local_sd"].astype(float).values / M.PRIOR_SD, 1.0)
    seats = []
    for i, r in s.reset_index(drop=True).iterrows():
        fixed = bool(r.get("uncontested", False))
        row = {"seat": r["seat"], "st": si[r["state"]], "mu": _r(r["mu"]), "res": _r(P.s_res * resf[i], 3),
               "u": _r(r.get("u_load", 0) or 0, 3), "h": _r(r.get("h_load", 0) or 0, 4), "c": _r(r.get("c_load", 0) or 0, 4),
               "a": _r(r.get("a_load", 0) or 0, 4), "w": _r(r.get("w_load", 0) or 0, 4),
               "htx": _r(r.get("h_tx", 0) or 0, 4), "hfl": _r(r.get("h_fl", 0) or 0, 4), "hwe": _r(r.get("h_west", 0) or 0, 4)}
        if fixed: row["fix"] = "D" if r.get("winner") == "D" else "R"
        k = ("house", r["seat"])
        if k in rec: row["bl"] = rec[k]
        seats.append(row)
    house = {"states": st_names, "seats": seats, "need": 218,
             "sd": {"nat": P.s_nat, "state": P.s_state, "urban": P.s_urban, "hisp": P.s_hisp, "cuban": P.s_cuban, "asian": P.s_asian, "wnc": P.s_wnc,
                    "htx": P.s_htx, "hfl": P.s_hfl, "hwest": P.s_hwest},
             "sys": sysd["house"]}

    def statewide(F, office, slope_mult):
        races = []
        for _, r in F.reset_index(drop=True).iterrows():
            el = float(r["elast"]) if "elast" in F and pd.notna(r.get("elast")) else 1.0
            # 2026-10-09: the Senate simulation adds RACE_EXTRA to each race's own sd and draws the shared shock at STATE_SHARED_MISS;
            # governors draw theirs at GOV_SHARED_MISS with no extra - the engine has one shared draw, so governors' loading carries the ratio
            sd_own = float(np.hypot(r["sd"], SN.RACE_EXTRA)) if office == "senate" else float(r["sd"])
            el_mult = 1.0 if office == "senate" else SN.GOV_SHARED_MISS / SN.STATE_SHARED_MISS
            row = {"st": r["state"], "mu": _r(r["mu"]), "sd": _r(sd_own, 3), "el": _r(el * slope_mult * el_mult, 4),
                   "h": _r(r.get("h_load", 0) or 0, 4), "c": _r(r.get("c_load", 0) or 0, 4), "a": _r(r.get("a_load", 0) or 0, 4),
                   "z": _r(r.get("wnc_z", 0) or 0, 4)}
            if office == "senate":
                row.update(sp=bool(r.get("special", False)), d=str(r.get("challenger") or r.get("dem_nom") or ""), r=str(r.get("republican") or r.get("rep_nom") or ""),
                           ind=str(r.get("challenger_party") or "D") != "D", inc=str(r.get("inc_party") or ""))
            else:
                row.update(d=str(r.get("dem") or ""), r=str(r.get("rep") or ""), inc=str(r.get("inc_party") or ""))
            k = (office, r["state"])
            if k in rec: row["bl"] = rec[k]
            races.append(row)
        return races
    pm = M.Params()
    sen = {"races": statewide(SS, "senate", 1.0), "holdD": SN.NOW_D - d_up, "holdR": None, "sys": sysd["senate"], "slope": SN.NAT_SLOPE}
    sen["holdR"] = 100 - sen["holdD"] - len(sen["races"])
    gov = {"races": statewide(GS, "governor", float(gslope) / SN.NAT_SLOPE), "holdD": GV.NOW_D - gov_up_d, "need": 26, "sys": sysd["governor"], "slope": float(gslope)} if GS is not None else None
    doc = {"generated": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%MZ"), "E0": E_now, "t_df": T,
           "shared": {"state_miss": SN.STATE_SHARED_MISS, "nat_slope": SN.NAT_SLOPE, "rho": 0.3,
                      "group_sd": {"hisp": pm.s_hisp, "cuban": pm.s_cuban, "asian": pm.s_asian}, "wnc_sd": SN.WNC_SD},
           "house": house, "senate": sen, "governor": gov, "hist_miss": HIST_MISS,
           "mood": {k: mood[k] for k in ("lead", "s_vote", "s_house") if k in mood}}
    path = Path(path or ROOT / "web" / "data" / "whatif.json")
    path.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False))
    nb = sum(1 for x in seats if "bl" in x) + sum(1 for x in sen["races"] if "bl" in x) + (sum(1 for x in gov["races"] if "bl" in x) if gov else 0)
    print(f"  whatif.json: {len(seats)} House seats, {len(sen['races'])} Senate, {len(gov['races']) if gov else 0} governor races, "
          f"{nb} with poll blends, {path.stat().st_size // 1024} KB")
    return doc
