"""Banked early votes and the national movement error (2026-10-04, user: "Is there any legitimate statistical methodology that
can account for the fact that early voting has started ... VA ... near 11% of 2022 total turnout so far" -> part 1 "I would do
part 1, yes").

WHAT THIS DOES - and what it does not. An early ballot is not counted, so it says nothing about the LEVEL (who is ahead): the
polls already include people who have voted, and composition-based readings of early ballots are diagnostic only
(pa_early / fl_early). What a banked ballot DOES change is its exposure to opinion movement between now and
Election Day: a shock on day t can only move the ballots cast after t.

Model. Let the national opinion follow a random walk with daily variance q, and F_s(tau) be the share of state s's final vote
cast by tau days before the election. A shock on day tau reaches a fraction 1 - F_s(tau) of the state's ballots, so the
movement variance from now (L days out) to Election Day is
        V_s(L) = q * sum_{tau < L} (1 - F_s(tau))^2.
The movement we can MEASURE is the change of the generic-ballot average between L days out and Election Day across past cycles
(research/national_mood/table.csv: rms(G0 - GL) 1.25 at 7 d, 2.17 at 30, 3.43 at 120 over 15 cycles, growing like sqrt(L) as a
random walk does). Those cycles also had early voting (national pre-Election-Day share 11 % in 1996 -> 69 % in 2020), and the
poll averages include voters who had already voted, so what was measured is the DILUTED movement:
        m^2(L) = q * mean over cycles of sum (1 - F_c(tau))^2.
Hence each state's movement variance is the measured one RESCALED by its exposure relative to the historical average:
        m_s^2(L) = m^2(L) * k_s^2,     k_s^2 = I_s(L) / I_hist(L),   I(L) = sum_{tau < L} (1 - F(tau))^2.
A state that banks MORE than past cycles typically did (Arizona, Colorado: ~90-100 % before Election Day) is less exposed to a late
swing; one that banks less (Mississippi, Alabama, New Hampshire) is MORE exposed than the old single number said. Nothing about
how anyone voted is assumed. The rest of the national error (polling error, the seat intercept) is untouched: it applies to every
ballot whenever it was cast.

Inputs:
  * banked now: UF Election Lab 2026 early vote (election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv: voted_all, turn_2022),
    snapshot per day in data/raw/early_vote/uf_2026_<date>.csv (fetch());
  * each state's final pre-Election-Day share: 2022 EAVS (EAC, 2022_EAVS_for_Public_Release_nolabel_V1.csv, section F1:
    mail + UOCAVA + early in person + all-mail jurisdictions over the reported modes), cached in data/cache/early_share_2022.json;
    Montana did not report the F1 modes -> the median of the reporting states;
  * the historical national pre-Election-Day share per cycle (Census CPS voting supplement via MIT Election Data + Science Lab:
    in-person Election Day 89 % in 1996, 60 % in 2018, 31 % in 2020, 50 % in 2022; mail 29 % + early in person 31 % in 2024);
    1998-2016 linearly interpolated between 1996 and 2018 (no published per-year national series was found);
  * the timing profile h(tau) = (1 - tau / W)^P (share of a state's pre-Election-Day ballots cast by tau days out; W 46 days ~
    when mail ballots go out, P 3 back-loads it). An ASSUMPTION until the daily 2026 snapshots can fit it; the same profile is
    used for the history and for 2026, so most of it cancels in k_s. A state's path runs from its banked share now to
    max(2022 share, banked now) on Election Day along the remaining part of h.
"""
from __future__ import annotations
import csv, io, json, urllib.request
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "early_vote"
SHARE_JSON = ROOT / "data" / "static" / "early_share_2022.json"
UF_URL = "https://election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv"
ELECTION = pd.Timestamp("2026-11-03")
W, P_SHAPE = 46.0, 3.0
HIST_PRE = {1996: 0.11, 2018: 0.40, 2020: 0.69, 2022: 0.50, 2024: 0.60}
for _y in range(1998, 2018, 2):
    HIST_PRE[_y] = round(0.11 + (0.40 - 0.11) * (_y - 1996) / (2018 - 1996), 3)
FIT_JSON = ROOT / "data" / "static" / "national_mood_fit.json"   # cycles + movement slope (national_mood.refit)
MOVE_LEAD_MAX = 60          # the random-walk fit uses leads <= 60 days (the forecast's range from here on)


def fetch(day=None):
    """Snapshot UF's 2026 table (one file per day; re-running the same day overwrites). -> path."""
    RAW.mkdir(parents=True, exist_ok=True)
    # an honest user agent (2026-10-04, user: collect data "in an appropriate manner"); one request a day; robots.txt allows
    # the path; the data are CC BY-NC-ND 4.0 - credited on about.html, used only to compute the factors, never republished
    # conditional request (ETag / Last-Modified): an unchanged table is not transferred again
    from . import fetch as F
    txt = F.get(UF_URL, RAW / "uf_2026_latest.csv.download", timeout=60)[0].decode("utf-8-sig")
    if not txt.lower().startswith("state,state_abbv"): raise RuntimeError("UF early-vote table: unexpected content")
    day = pd.Timestamp(day or pd.Timestamp.today()).strftime("%Y-%m-%d")
    p = RAW / f"uf_2026_{day}.csv"; p.write_text(txt); return p


def banked(asof=None) -> dict:
    """{state: share of 2022 turnout already voted} from the newest snapshot on or before asof ({} if none)."""
    asof = pd.Timestamp(asof or pd.Timestamp.today()).normalize()
    snaps = sorted(p for p in RAW.glob("uf_2026_*.csv") if pd.Timestamp(p.stem[-10:]) <= asof)
    if not snaps: return {}
    out = {}
    for r in csv.DictReader(io.StringIO(snaps[-1].read_text().lstrip("\ufeff"))):
        try: v, t = float(r["voted_all"] or 0), float(r["turn_2022"] or 0)
        except ValueError: continue
        if t > 0: out[r["state_abbv"]] = min(v / t, 1.0)
    return out


def share_2022() -> dict:
    """{state: 2022 share of ballots cast before Election Day} from EAVS (cached)."""
    if SHARE_JSON.exists(): return json.loads(SHARE_JSON.read_text())["share"]
    f = RAW / "2022_EAVS_for_Public_Release_nolabel_V1.csv"
    d = pd.read_csv(f, usecols=["State_Abbr"] + [f"F1{c}" for c in "abcdefgh"], low_memory=False)
    for c in d.columns[1:]:
        d[c] = pd.to_numeric(d[c], errors="coerce"); d.loc[d[c] < 0, c] = np.nan
    g = d.groupby("State_Abbr").sum(min_count=1)
    parts = g[[f"F1{c}" for c in "bcdefgh"]].sum(1); pre = g[["F1c", "F1d", "F1f", "F1g"]].sum(1)
    sh = (pre / parts).where(parts > 0)
    sh = sh[[s for s in sh.index if s not in ("AS", "GU", "MP", "PR", "VI")]]
    med = float(sh.median()); missing = sorted(sh[sh.isna()].index)
    sh = sh.fillna(med).round(4)
    SHARE_JSON.parent.mkdir(parents=True, exist_ok=True)
    SHARE_JSON.write_text(json.dumps({"share": sh.to_dict(), "filled_with_median": missing, "median": round(med, 4),
                                      "source": "EAC 2022 EAVS v1, F1 (c+d+f+g) / (b..h)"}, indent=1))
    return sh.to_dict()


def turnout_2022() -> dict:
    """{state: 2022 ballots} from the newest UF snapshot (turn_2022)."""
    snaps = sorted(RAW.glob("uf_2026_*.csv"))
    if not snaps: return {}
    return {r["state_abbv"]: float(r["turn_2022"]) for r in csv.DictReader(io.StringIO(snaps[-1].read_text().lstrip("\ufeff")))
            if (r.get("turn_2022") or "").replace(".", "").isdigit()}


def h(tau):
    """Share of a cycle's pre-Election-Day ballots already cast tau days before the election."""
    return np.clip(1 - np.asarray(tau, float) / W, 0, 1) ** P_SHAPE


def exposure(F_end, L, F_now=0.0):
    """I(L) = sum over the days tau = 0..L-1 still to come of (1 - F(tau))^2, F rising from F_now at tau = L to
    max(F_end, F_now) at tau = 0 along the remaining part of h."""
    if L <= 0: return 0.0
    tau = np.arange(L, dtype=float); F_end = max(F_end, F_now); hL = float(h(L))
    g = (h(tau) - hL) / (1 - hL) if hL < 1 else np.ones_like(tau)
    F = F_now + (F_end - F_now) * np.clip(g, 0, 1)
    return float(np.sum((1 - F) ** 2))


def hist_exposure(L, years=None):
    """Mean exposure of the cycles behind the measured movement (the national_mood table's years)."""
    if years is None: years = json.loads(FIT_JSON.read_text())["years"]
    return float(np.mean([exposure(HIST_PRE[y], L) for y in years]))


def move_var(L) -> float:
    """Measured (diluted) movement variance m^2(L): q_obs * L, q_obs fitted through the origin on rms(G0 - GL)^2 over the
    table's cycles, leads 7..MOVE_LEAD_MAX."""
    q = float(json.loads(FIT_JSON.read_text())["move_q"]); return q * max(float(L), 0.0)


def k_factors(asof=None, states=None) -> dict:
    """{state: k_s} with k_s^2 = I_s(L) / I_hist(L); plus diagnostics under '_meta'."""
    asof = pd.Timestamp(asof or pd.Timestamp.today()).normalize(); L = max(int((ELECTION - asof).days), 0)
    sh = share_2022(); bk = banked(asof); Ih = hist_exposure(L)
    states = states or sorted(sh)
    k = {}
    for s in states:
        Is = exposure(sh.get(s, float(np.median(list(sh.values())))), L, bk.get(s, 0.0))
        k[s] = float(np.sqrt(Is / Ih)) if Ih > 0 else 1.0
    k["_meta"] = {"lead": L, "I_hist": round(Ih, 2), "m2": round(move_var(L), 3), "banked_states": len(bk),
                  "snapshot": max((p.stem[-10:] for p in RAW.glob("uf_2026_*.csv") if pd.Timestamp(p.stem[-10:]) <= asof), default=None)}
    return k


def setup(n, s_house, s_state, rho, asof=None, seed=29):
    """Turn the movement split on for this run: model.MOVE = {z, m, k}. Returns the correlation the REMAINING House and
    statewide draws need so that the House-statewide correlation of the turnout-weighted national average stays `rho`
    (national_mood.RHO_STATEWIDE, the measured correlation of the total misses):
        rho * T_h * T_s = (m kbar)^2 + rho_r * r_h * r_s,   T = sqrt(r^2 + (m kbar)^2),   kbar = 2022-turnout-weighted mean k.
    s_house, s_state: the total national sd of the House and the statewide shared shock (national-vote points)."""
    from . import model as M
    k = k_factors(asof); meta = k.pop("_meta"); m2 = min(meta["m2"], 0.9 * min(s_house, s_state) ** 2)
    rh, rs = np.sqrt(s_house ** 2 - m2), np.sqrt(s_state ** 2 - m2)
    tw = turnout_2022(); kbar = float(sum(k.get(s, 1.0) * w for s, w in tw.items()) / sum(tw.values())) if tw else 1.0
    mk2 = m2 * kbar ** 2; Th, Ts = np.sqrt(rh ** 2 + mk2), np.sqrt(rs ** 2 + mk2)
    rho_r = float(np.clip((rho * Th * Ts - mk2) / (rh * rs), -0.99, 0.99)); meta["kbar"] = round(kbar, 3)
    M.MOVE = {"z": M.national_z(n, seed), "m": float(np.sqrt(m2)), "k": k, "meta": {**meta, "rho_r": round(rho_r, 3)}}
    return rho_r


def kvec(states) -> np.ndarray:
    from . import model as M
    k = M.MOVE["k"]; return np.array([k.get(s, 1.0) for s in states], float)


if __name__ == "__main__":
    import sys
    if "--fetch" in sys.argv: print("snapshot:", fetch())
    k = k_factors(); meta = k.pop("_meta"); sh = share_2022(); bk = banked()
    print(f"lead {meta['lead']} d, measured movement sd {np.sqrt(meta['m2']):.2f} pts, I_hist {meta['I_hist']}, snapshot {meta['snapshot']}")
    for s in sorted(k, key=k.get):
        print(f"  {s}  2022 pre-ED {sh.get(s, float('nan')):.2f}  banked now {bk.get(s, 0):.3f}  k {k[s]:.3f}  movement sd {k[s] * np.sqrt(meta['m2']):.2f}")
