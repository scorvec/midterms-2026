"""Leak-free backtest of the state-legislative chamber model on 2018 and 2022 (python -m midterms.stateleg_backtest).

For each test cycle: the Klarner-fitted parameters (incumbency, national slope, state error, kappa, slate sd) use elections BEFORE
the cycle only; the lean slope and intercept come from the OTHER test cycle (2018 <- 2022, 2022 <- 2018: the only two midterms with
district presidential leans on their own maps), as the House backtest forecasts each year from the other years. Inputs known on
the forecast date (Oct 1): the lean (previous presidential election on the cycle's map), who is on the ballot (Klarner candidate
records: incumbents running, uncontested seats, slate sizes), E = the 538 generic-ballot average of the 30 days before Oct 1 with
NO directional correction, the national error at that lead (national_mood), and - arm "state" - the state's governor-race polls
(538 poll archive, 45 days before Oct 1) against the governor prior, as the live model reads its Senate/governor race pulls.
Scores: per seat (slot) Brier and log loss, chamber control Brier and calls, seat-count error; baselines: the chamber's current
majority holds, each seat stays with the party that won it last time (2018; same maps) or goes with the sign of its lean (2022).
Writes data/static/stateleg/backtest.json.
"""
from __future__ import annotations

import json
import numpy as np, pandas as pd
from . import stateleg as SLM, model as M, national_mood as NMOOD
from .paths import RAW

ASOF = {2018: "2018-10-01", 2022: "2022-10-01"}
ELECTION = {2018: "2018-11-06", 2022: "2022-11-08"}


def generic_E(cycle):
    E, n = M.national_env(pd.Timestamp(ASOF[cycle]), cycle)
    return E + 1.0                       # national_env subtracts a 1-pt pro-D bias; the live model takes no direction


def s_E(cycle):
    F = NMOOD._fit(); L = (pd.Timestamp(ELECTION[cycle]) - pd.Timestamp(ASOF[cycle])).days
    return float(np.hypot(NMOOD.at(F, L, direction=False)[1], NMOOD.S_SEAT))


def gov_polls(cycle):
    df = pd.read_csv(RAW / "538" / "governor_polls_historical.csv", low_memory=False)
    df["end_date"] = pd.to_datetime(df["end_date"], format="%m/%d/%y", errors="coerce")
    df = df[(df["cycle"] == cycle) & (df["stage"] == "general")]
    df["p"] = df["party"].map({"DEM": "D", "REP": "R"})
    key = ["poll_id", "question_id", "state"]
    d = df[df.p == "D"].groupby(key)["pct"].max(); r = df[df.p == "R"].groupby(key)["pct"].max(); e = df.groupby(key)["end_date"].max()
    out = pd.concat([d.rename("d"), r.rename("r"), e.rename("end")], axis=1).dropna().reset_index()
    out["margin"] = out["d"] - out["r"]
    return out


def state_pull(cycle, E, P):
    """Governor-race pull per state (posterior - prior, net of the national mean) and its posterior sd, from polls before Oct 1."""
    from . import gov2026 as GV
    from .data_prep import _ST
    H = GV.history(); hy = H[H["year"] == cycle].set_index("state")
    b = P["fit"]["kappa"].get("gov_prior")
    if not b or hy.empty: return {}, {}
    Hp = H[H["year"] < cycle]; X = np.c_[np.ones(len(Hp)), Hp["lean"], Hp["inc"], Hp["E"]]; psd = float((Hp["margin"] - X @ np.array(b)).std())
    po = gov_polls(cycle); asof = pd.Timestamp(ASOF[cycle]); po = po[(po["end"] <= asof) & (po["end"] > asof - pd.Timedelta(days=45))]
    pull, sd = {}, {}
    for st, r in hy.iterrows():
        name = next((k for k, v in _ST.items() if v == st), None)
        q = po[po["state"] == name] if name else po.iloc[0:0]
        prior = b[0] + b[1] * r["lean"] + b[2] * r["inc"] + b[3] * E
        if len(q):
            pv = 5.0 ** 2 / len(q) + 4.0 ** 2; w = psd ** 2 / (psd ** 2 + pv)
            pull[st] = w * (float(q["margin"].mean()) - prior); sd[st] = float(np.sqrt(1 / (1 / psd ** 2 + 1 / pv)))
    if pull:
        nat = float(np.mean(list(pull.values()))); pull = {s: v - nat for s, v in pull.items()}
    return pull, sd


def held_seats(cycle):
    """Seats NOT up in the cycle in the staggered senates, with the party that won them at their last election."""
    k = SLM.klarner(); out = []
    for st, ch, name, total, up, tie in SLM.CHAMBERS:
        if ch != "upper": continue
        cur = k[(k["sab"] == st) & (k["chamber"] == ch) & (k["year"] == cycle)]
        if len(cur) >= total * 0.9: continue
        prev = k[(k["sab"] == st) & (k["chamber"] == ch) & (k["year"].isin([cycle - 2]))]
        if len(prev) + len(cur) < total * 0.9: prev = k[(k["sab"] == st) & (k["chamber"] == ch) & (k["year"].isin([cycle - 2, cycle - 4]))]
        prev = prev[~prev["district"].isin(cur["district"])].sort_values("year").drop_duplicates("district", keep="last")
        for r in prev.itertuples(): out.append({"state": st, "chamber": ch, "district": r.district, "party": "D" if r.w_d > 0 else ("R" if r.w_r > 0 else "O")})
    return pd.DataFrame(out)


def previous_control(cycle):
    """Chamber composition before the election from the previous election's winners (+ the held seats' last results)."""
    k = SLM.klarner(); res = {}
    for st, ch, name, total, up, tie in SLM.CHAMBERS:
        q = k[(k["sab"] == st) & (k["chamber"] == ch) & (k["year"].isin([cycle - 2, cycle - 4]))].sort_values("year")
        q = q.drop_duplicates(["district"], keep="last")
        d, r = int(q["w_d"].sum()), int(q["w_r"].sum())
        res[(st, ch)] = "D" if d > total / 2 else ("R" if r > total / 2 else "T")
    return res


def run_cycle(cycle, n=10000, seed=3, arm="base"):
    other = 2022 if cycle == 2018 else 2018
    P = SLM.fit(before=cycle, cycles=(other,), save=False)
    if arm == "nokappa": P["kappa"] = 0.0
    E = generic_E(cycle); sE = s_E(cycle)
    q = SLM.hist_table(cycle)
    q = q[q["state"].isin({c[0] for c in SLM.CHAMBERS})].copy()
    miss = q["lean"].isna(); nmiss = int(miss.sum())
    q = q[~miss].reset_index(drop=True)
    q["E"] = E; q["inc"] = np.where((q["k"] == 1), q["inc"], 0)
    rng = np.random.default_rng(seed); Z = M.national_z(n, seed=seed + 1)
    pull, psd = state_pull(cycle, E, P) if arm == "state" else ({}, {})
    sig = {s: rng.normal(0, psd[s], n) for s in pull} if pull else None
    if sig:
        mean_all = np.mean(np.stack(list(sig.values())), 0); sig = {s: v - mean_all for s, v in sig.items()}
    S = q.rename(columns={"n_d": "n_d", "n_r": "n_r"})
    m, mu = SLM.simulate(S, P, n, rng, nat=P["g"] * sE * Z, state_sig=sig, shift=pull or None)
    W = SLM.seats_won(S, m, P, rng)
    H = held_seats(cycle); prev = previous_control(cycle)
    # seat (slot) scores
    br, ll, br_all, n_c = [], [], [], 0
    for j in range(len(S)):
        kk = int(S["k"].iat[j]); y = int(S["w_d"].iat[j])
        for t in range(1, kk + 1):
            p = float((W[:, j] >= t).mean()); o = float(y >= t); pc = min(max(p, 1e-3), 1 - 1e-3)
            br_all.append((p - o) ** 2)
            if S["n_d"].iat[j] > 0 and S["n_r"].iat[j] > 0:
                br.append((p - o) ** 2); ll.append(-(o * np.log(pc) + (1 - o) * np.log(1 - pc)))
    # baselines (seat): previous winner's party (same map) or lean sign
    base_err = []
    k = SLM.klarner()
    for j in range(len(S)):
        if not (S["n_d"].iat[j] > 0 and S["n_r"].iat[j] > 0): continue
        kk = int(S["k"].iat[j]); y = int(S["w_d"].iat[j])
        pr = k[(k["sab"] == S["state"].iat[j]) & (k["chamber"] == S["chamber"].iat[j]) & (k["district"] == S["district"].iat[j]) & (k["year"] < cycle) & (k["regime"] == S["regime"].iat[j])].sort_values("year")
        if len(pr) and cycle == 2018: guess = int(pr["w_d"].iat[-1])
        else: guess = kk if S["lean"].iat[j] > 0 else 0
        for t in range(1, kk + 1): base_err.append(float((guess >= t) != (y >= t)))
    ch_rows = []
    for st, ch, name, total, up, tie in SLM.CHAMBERS:
        idx = np.where((S["state"] == st) & (S["chamber"] == ch))[0]
        if not len(idx): continue
        hq = H[(H["state"] == st) & (H["chamber"] == ch)] if len(H) else H
        hd = int((hq["party"] == "D").sum()) if len(hq) else 0; hr = int((hq["party"] == "R").sum()) if len(hq) else 0
        ds = hd + W[:, idx].sum(1); actual = hd + int(S["w_d"].values[idx].sum()); act_r = hr + int(S["w_r"].values[idx].sum())
        tot_eff = hd + hr + int(S["k"].values[idx].sum())
        cd, cr, sh = SLM.control(ds, tot_eff, "shared" if tie == "gov" else tie)
        outcome = "D" if actual > tot_eff / 2 else ("R" if act_r > tot_eff / 2 else "T")
        pD = float(cd.mean())
        ch_rows.append({"cycle": cycle, "chamber": name, "seats_modelled": tot_eff, "p_d": round(pD, 3), "mean_d": round(float(ds.mean()), 1),
                        "p10": int(np.percentile(ds, 10)), "p90": int(np.percentile(ds, 90)), "actual_d": actual, "outcome": outcome,
                        "pit": round(float((ds < actual).mean() + 0.5 * (ds == actual).mean()), 3),
                        "prev_majority": prev.get((st, ch)), "brier": round((pD - (outcome == "D")) ** 2, 4),
                        "call_ok": (pD > 0.5) == (outcome == "D") if outcome != "T" else None,
                        "naive_ok": prev.get((st, ch)) == outcome})
    return {"cycle": cycle, "arm": arm, "E": round(E, 2), "s_E": round(sE, 2), "n_slots_contested": len(br), "seat_brier": round(float(np.mean(br)), 4),
            "seat_logloss": round(float(np.mean(ll)), 4), "seat_brier_all": round(float(np.mean(br_all)), 4), "baseline_seat_error_rate": round(float(np.mean(base_err)), 4),
            "seats_without_lean": nmiss, "chambers": ch_rows, "params": {k_: P[k_] for k_ in ("a", "beta", "c", "g", "sd_seat", "sd_state", "rho_ch", "kappa", "slate_sd")}}


def main():
    out = []
    for cyc in (2018, 2022):
        for arm in ("base", "state", "nokappa"):
            try: r = run_cycle(cyc, arm=arm)
            except Exception as e:
                import traceback; traceback.print_exc(); print("!!", cyc, arm, e); continue
            out.append(r)
            print(f"\n{cyc} [{arm}] E {r['E']:+.1f} s_E {r['s_E']:.1f}: seat Brier {r['seat_brier']:.4f} log loss {r['seat_logloss']:.4f} "
                  f"(n {r['n_slots_contested']}), baseline seat error rate {r['baseline_seat_error_rate']:.3f}, no-lean seats {r['seats_without_lean']}")
            print(pd.DataFrame(r["chambers"]).to_string(index=False))
    (SLM.SL / "backtest.json").write_text(json.dumps(out, indent=1, default=lambda o: bool(o) if isinstance(o, (np.bool_,)) else str(o)))


if __name__ == "__main__":
    main()
