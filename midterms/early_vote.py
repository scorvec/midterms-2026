"""Banked early votes and the national movement error: a ballot already cast is no longer exposed to late opinion movement.

What this does - and what it does not. An early ballot is not counted, so it says nothing about the level (who is ahead): the
polls already include people who have voted, and composition-based readings of early ballots are diagnostic only
(pa_early / fl_early). What a banked ballot does change is its exposure to opinion movement between now and
Election Day: a shock on day t can only move the ballots cast after t.

Model. Let the national opinion follow a random walk with daily variance q, and F_s(tau) be the share of state s's final vote
cast by tau days before the election. A shock on day tau reaches a fraction 1 - F_s(tau) of the state's ballots, so the
movement variance from now (L days out) to Election Day is
        V_s(L) = q * sum_{tau < L} (1 - F_s(tau))^2.
The movement we can measure is the change of the generic-ballot average between L days out and Election Day across past cycles
(the national_mood table: rms(G0 - GL) 1.25 at 7 d, 2.17 at 30, 3.43 at 120 over 15 cycles, growing like sqrt(L) as a
random walk does). Those cycles also had early voting (national pre-Election-Day share 11 % in 1996 -> 69 % in 2020), and the
poll averages include voters who had already voted, so what was measured is the diluted movement:
        m^2(L) = q * mean over cycles of sum (1 - F_c(tau))^2.
Hence each state's movement variance is the measured one rescaled by its exposure relative to the historical average:
        m_s^2(L) = m^2(L) * k_s^2,     k_s^2 = I_s(L) / I_hist(L),   I(L) = sum_{tau < L} (1 - F(tau))^2.
A state that banks more than past cycles typically did (Arizona, Colorado: ~90-100 % before Election Day) is less exposed to a late
swing; one that banks less (Mississippi, Alabama, New Hampshire) is more exposed than a single national number says. Nothing about
how anyone voted is assumed. The rest of the national error (polling error, the seat intercept) is untouched: it applies to every
ballot whenever it was cast.

Race-level movement. A race moves on its own too (a debate, a scandal, a late ad buy), and a banked ballot is just as shielded
from that. Measured on 538's raw_polls (Senate and governor generals 1998-2022, D v R, polls within 60 days of the election): for
two polls of the same race by different pollsters, E[(e_i - e_j)^2] = sampling + house effects + q_total |t_i - t_j|, so the slope
of the squared difference of their errors on the days between them is the race margin's daily movement variance (sampling
variance 1e4 (1/n_i + 1/n_j) removed, every race weighted equally, race-clustered bootstrap). Netting out the national part
(the national q x NAT_SLOPE^2) leaves the race's own movement, q_race (race_move_fit -> data/static/race_move_fit.json: about
0.25 pts^2 a day, a 2 pt sd over the last 17 days). Like the national figure it was measured on cycles that already had early
voting, so it is the diluted movement and takes the same per-state factor k_s: each race's own error is split into a movement
part sqrt(q_race L) x k_s and the rest (model.race_shock). A state banking at the historical pace keeps its total error; one
banking faster gets tighter, one banking slower wider. Nothing here moves any margin.

Inputs:
  * banked now: UF Election Lab 2026 early vote (election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv: voted_all, turn_2022),
    snapshot per day in data/raw/early_vote/uf_2026_<date>.csv (fetch());
  * each state's final pre-Election-Day share: 2022 EAVS (EAC, 2022_EAVS_for_Public_Release_nolabel_V1.csv, section F1:
    mail + UOCAVA + early in person + all-mail jurisdictions over the reported modes), cached in data/cache/early_share_2022.json;
    Montana did not report the F1 modes -> the median of the reporting states;
  * the historical national pre-Election-Day share per cycle (Census CPS voting supplement via MIT Election Data + Science Lab:
    in-person Election Day 89 % in 1996, 60 % in 2018, 31 % in 2020, 50 % in 2022; mail 29 % + early in person 31 % in 2024);
    1998-2016 linearly interpolated between 1996 and 2018 (no published per-year national series was found);
  * the timing profile h(tau) = (1 - tau / W)^P (share of a cycle's pre-Election-Day ballots cast by tau days out), fitted
    by profile_fit() to the one complete national path UF's tables give - 2024, from Wayback Machine captures, normalised by
    the Election-Day total (data/static/early_profile.json: W 40.0, P 2.90; the earlier assumption W 46, P 3 ran ahead of it
    mid-campaign, 6 % in by 28 days out against 2.8 % measured). The 2026 path so far follows the same shape with its scale
    left free (its final share is not known yet). One national profile ignores voting mode: North Carolina's in-person-heavy
    path stays near zero until one-stop voting opens ~19 days out, Pennsylvania's mail path rises from ~35 days. The same
    profile is used for the history and for 2026, so most of it cancels in k_s. A state's path runs from its banked share now
    to max(2022 share, banked now) on Election Day along the remaining part of h; a past cycle's path is its final share
    times h, i.e. already F h(L) banked at L days out.
"""

from __future__ import annotations
import csv
import io
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "early_vote"
SHARE_JSON = ROOT / "data" / "static" / "early_share_2022.json"
UF_URL = "https://election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv"
ELECTION = pd.Timestamp("2026-11-03")
PROFILE_JSON = ROOT / "data" / "static" / "early_profile.json"  # the fitted timing profile (profile_fit)
_PROFILE = json.loads(PROFILE_JSON.read_text())
W, P_SHAPE = float(_PROFILE["W"]), float(_PROFILE["P"])
HIST_PRE = {1996: 0.11, 2018: 0.40, 2020: 0.69, 2022: 0.50, 2024: 0.60}
for _y in range(1998, 2018, 2):
    HIST_PRE[_y] = round(0.11 + (0.40 - 0.11) * (_y - 1996) / (2018 - 1996), 3)
FIT_JSON = ROOT / "data" / "static" / "national_mood_fit.json"  # cycles + movement slope (national_mood.refit)
MOVE_LEAD_MAX = 60  # the random-walk fit uses leads <= 60 days (the forecast's range from here on)
RACE_FIT_JSON = ROOT / "data" / "static" / "race_move_fit.json"  # race-level movement (race_move_fit)
RAW_POLLS = ROOT / "data" / "raw" / "538repo" / "raw_polls.csv"


def fetch(day=None):
    """Snapshot UF's 2026 table (one file per day; re-running the same day overwrites). -> path."""
    RAW.mkdir(parents=True, exist_ok=True)
    # an honest user agent; one request a day; robots.txt allows
    # the path; the data are CC BY-NC-ND 4.0 - credited on about.html, used only to compute the factors, never republished
    # conditional request (ETag / Last-Modified): an unchanged table is not transferred again
    from . import fetch as F

    txt = F.get(UF_URL, RAW / "uf_2026_latest.csv.download", timeout=60)[0].decode("utf-8-sig")
    if not txt.lower().startswith("state,state_abbv"):
        raise RuntimeError("UF early-vote table: unexpected content")
    day = pd.Timestamp(day or pd.Timestamp.today()).strftime("%Y-%m-%d")
    p = RAW / f"uf_2026_{day}.csv"
    p.write_text(txt)
    return p


def banked(asof=None) -> dict:
    """{state: share of 2022 turnout already voted} from the newest snapshot on or before asof ({} if none)."""
    asof = pd.Timestamp(asof or pd.Timestamp.today()).normalize()
    snaps = sorted(p for p in RAW.glob("uf_2026_*.csv") if pd.Timestamp(p.stem[-10:]) <= asof)
    if not snaps:
        return {}
    out = {}
    for r in csv.DictReader(io.StringIO(snaps[-1].read_text().lstrip("\ufeff"))):
        try:
            v, t = float(r["voted_all"] or 0), float(r["turn_2022"] or 0)
        except ValueError:
            continue
        if t > 0:
            out[r["state_abbv"]] = min(v / t, 1.0)
    return out


def share_2022() -> dict:
    """{state: 2022 share of ballots cast before Election Day} from EAVS (cached)."""
    if SHARE_JSON.exists():
        return json.loads(SHARE_JSON.read_text())["share"]
    f = RAW / "2022_EAVS_for_Public_Release_nolabel_V1.csv"
    d = pd.read_csv(f, usecols=["State_Abbr"] + [f"F1{c}" for c in "abcdefgh"], low_memory=False)
    for c in d.columns[1:]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
        d.loc[d[c] < 0, c] = np.nan
    g = d.groupby("State_Abbr").sum(min_count=1)
    parts = g[[f"F1{c}" for c in "bcdefgh"]].sum(1)
    pre = g[["F1c", "F1d", "F1f", "F1g"]].sum(1)
    sh = (pre / parts).where(parts > 0)
    sh = sh[[s for s in sh.index if s not in ("AS", "GU", "MP", "PR", "VI")]]
    med = float(sh.median())
    missing = sorted(sh[sh.isna()].index)
    sh = sh.fillna(med).round(4)
    SHARE_JSON.parent.mkdir(parents=True, exist_ok=True)
    SHARE_JSON.write_text(
        json.dumps(
            {
                "share": sh.to_dict(),
                "filled_with_median": missing,
                "median": round(med, 4),
                "source": "EAC 2022 EAVS v1, F1 (c+d+f+g) / (b..h)",
            },
            indent=1,
        )
    )
    return sh.to_dict()


def turnout_2022() -> dict:
    """{state: 2022 ballots} from the newest UF snapshot (turn_2022)."""
    snaps = sorted(RAW.glob("uf_2026_*.csv"))
    if not snaps:
        return {}
    return {
        r["state_abbv"]: float(r["turn_2022"])
        for r in csv.DictReader(io.StringIO(snaps[-1].read_text().lstrip("\ufeff")))
        if (r.get("turn_2022") or "").replace(".", "").isdigit()
    }


def h(tau, w=None, p=None):
    """Share of a cycle's pre-Election-Day ballots already cast tau days before the election (the fitted W, P unless given)."""
    return np.clip(1 - np.asarray(tau, float) / (W if w is None else w), 0, 1) ** (P_SHAPE if p is None else p)


def exposure(F_end, L, F_now=0.0):
    """I(L) = sum over the days tau = 0..L-1 still to come of (1 - F(tau))^2, F rising from F_now at tau = L to
    max(F_end, F_now) at tau = 0 along the remaining part of h."""
    if L <= 0:
        return 0.0
    tau = np.arange(L, dtype=float)
    F_end = max(F_end, F_now)
    hL = float(h(L))
    g = (h(tau) - hL) / (1 - hL) if hL < 1 else np.ones_like(tau)
    F = F_now + (F_end - F_now) * np.clip(g, 0, 1)
    return float(np.sum((1 - F) ** 2))


def hist_exposure(L, years=None):
    """Mean exposure of the cycles behind the measured movement (the national_mood table's years). Each cycle had already
    banked its final share times h(L) by L days out, so its path is F h(tau) - not a path starting from zero at L."""
    if years is None:
        years = json.loads(FIT_JSON.read_text())["years"]
    return float(np.mean([exposure(HIST_PRE[y], L, HIST_PRE[y] * float(h(L))) for y in years]))


def move_var(L) -> float:
    """Measured (diluted) movement variance m^2(L): q_obs * L, q_obs fitted through the origin on rms(G0 - GL)^2 over the
    table's cycles, leads 7..MOVE_LEAD_MAX."""
    q = float(json.loads(FIT_JSON.read_text())["move_q"])
    return q * max(float(L), 0.0)


def race_move_var(L) -> float:
    """A race's own measured (diluted) movement variance over the last L days, pts of margin squared: q_race x L."""
    q = float(json.loads(RACE_FIT_JSON.read_text())["q_race"])
    return q * min(max(float(L), 0.0), MOVE_LEAD_MAX)


def race_move_fit(write=True, n_boot=300, seed=1) -> dict:
    """q_race from 538's raw_polls (see the module docstring): Senate + governor generals, D v R, polls within MOVE_LEAD_MAX
    days, sample 200-5000; every pair of polls of one race by two different pollsters; slope of (e_i - e_j)^2 minus the
    sampling variance on |t_i - t_j|, each race weighted equally; race-clustered bootstrap sd. The national part, the
    national_mood movement slope x NAT_SLOPE^2, is netted out."""
    from . import senate2026 as SN

    d = pd.read_csv(RAW_POLLS)
    d = d[
        d.type_simple.isin(["Sen-G", "Gov-G"])
        & d.cand1_party.eq("DEM")
        & d.cand2_party.eq("REP")
        & d.samplesize.between(200, 5000)
        & (d.time_to_election <= MOVE_LEAD_MAX)
    ]
    rows = []
    for rid, g in d.groupby("race_id"):
        if len(g) < 2:
            continue
        e = (g.margin_poll - g.margin_actual).values
        t, n, ps = g.time_to_election.values, g.samplesize.values.astype(float), g.pollster.values
        i, j = np.triu_indices(len(g), 1)
        keep = ps[i] != ps[j]  # one pollster's repeat polls share a house effect
        rows.append(
            pd.DataFrame(
                {
                    "dt": np.abs(t[i] - t[j])[keep],
                    "y": ((e[i] - e[j]) ** 2 - 1e4 * (1 / n[i] + 1 / n[j]))[keep],
                    "race": rid,
                }
            )
        )
    P = pd.concat(rows, ignore_index=True)
    w = (1 / P.groupby("race").race.transform("size")).values

    def slope(idx):
        X = np.c_[np.ones(len(idx)), P.dt.values[idx]] * np.sqrt(w[idx])[:, None]
        return float(np.linalg.lstsq(X, P.y.values[idx] * np.sqrt(w[idx]), rcond=None)[0][1])

    allidx = np.arange(len(P))
    q_total = slope(allidx)
    grp = P.groupby("race").indices
    races = list(grp)
    rng = np.random.default_rng(seed)
    boot = [slope(np.concatenate([grp[r] for r in rng.choice(races, len(races))])) for _ in range(n_boot)]
    q_nat = float(json.loads(FIT_JSON.read_text())["move_q"]) * SN.NAT_SLOPE**2
    rec = {
        "q_total": round(q_total, 4),
        "q_total_sd": round(float(np.std(boot)), 4),
        "q_national": round(q_nat, 4),
        "q_race": round(max(q_total - q_nat, 0.0), 4),
        "pairs": len(P),
        "races": int(P.race.nunique()),
        "source": "538 raw_polls (git blob 08735ea), Sen-G + Gov-G D v R 1998-2022, polls <= 60 days, pairs of pollsters",
    }
    if write:
        RACE_FIT_JSON.write_text(json.dumps(rec, indent=1))
    return rec


def k_factors(asof=None, states=None) -> dict:
    """{state: k_s} with k_s^2 = I_s(L) / I_hist(L); plus diagnostics under '_meta'."""
    asof = pd.Timestamp(asof or pd.Timestamp.today()).normalize()
    L = max(int((ELECTION - asof).days), 0)
    sh = share_2022()
    bk = banked(asof)
    Ih = hist_exposure(L)
    states = states or sorted(sh)
    k = {}
    for s in states:
        Is = exposure(sh.get(s, float(np.median(list(sh.values())))), L, bk.get(s, 0.0))
        k[s] = float(np.sqrt(Is / Ih)) if Ih > 0 else 1.0
    k["_meta"] = {
        "lead": L,
        "I_hist": round(Ih, 2),
        "m2": round(move_var(L), 3),
        "m_race": round(float(np.sqrt(race_move_var(L))), 3),
        "banked_states": len(bk),
        "snapshot": max(
            (p.stem[-10:] for p in RAW.glob("uf_2026_*.csv") if pd.Timestamp(p.stem[-10:]) <= asof), default=None
        ),
    }
    return k


def setup(n, s_house, s_state, rho, asof=None, seed=29):
    """Turn the movement split on for this run: model.MOVE = {z, m, m_race, k}. Returns the correlation the remaining House and
    statewide draws need so that the House-statewide correlation of the turnout-weighted national average stays `rho`
    (national_mood.RHO_STATEWIDE, the measured correlation of the total misses):
        rho * T_h * T_s = (m kbar)^2 + rho_r * r_h * r_s,   T = sqrt(r^2 + (m kbar)^2),   kbar = 2022-turnout-weighted mean k.
    s_house, s_state: the total national sd of the House and the statewide shared shock (national-vote points)."""
    from . import model as M

    k = k_factors(asof)
    meta = k.pop("_meta")
    m2 = min(meta["m2"], 0.9 * min(s_house, s_state) ** 2)
    rh, rs = np.sqrt(s_house**2 - m2), np.sqrt(s_state**2 - m2)
    tw = turnout_2022()
    kbar = float(sum(k.get(s, 1.0) * w for s, w in tw.items()) / sum(tw.values())) if tw else 1.0
    mk2 = m2 * kbar**2
    Th, Ts = np.sqrt(rh**2 + mk2), np.sqrt(rs**2 + mk2)
    rho_r = float(np.clip((rho * Th * Ts - mk2) / (rh * rs), -0.99, 0.99))
    meta["kbar"] = round(kbar, 3)
    M.MOVE = {
        "z": M.national_z(n, seed),
        "m": float(np.sqrt(m2)),
        "m_race": float(meta["m_race"]),  # each race's own late movement (model.race_shock)
        "k": k,
        "meta": {**meta, "rho_r": round(rho_r, 3)},
    }
    return rho_r


def wayback_captures(year: int) -> list[Path]:
    """UF's national table for `year` as the Wayback Machine captured it, one file per capture day, into RAW/wayback. UF keeps
    no dated history of its tables, and a fit needs the path of a whole cycle; captures never change, so each is fetched once."""
    from . import fetch as F

    src = f"election.lab.ufl.edu/data-downloads/earlyvote/{year}/US.csv"
    have = sorted((RAW / "wayback").glob(f"uf_{year}_*.csv"))
    stamps = None
    for attempt in range(3):  # the capture index answers 503 when busy
        try:
            cdx = F.open_url(
                f"https://web.archive.org/cdx/search/cdx?url={src}&output=json&fl=timestamp&filter=statuscode:200"
                "&collapse=timestamp:8",
                timeout=90,
            )
            stamps = [ts for (ts,) in json.loads(cdx)[1:]]
            break
        except Exception as e:
            print(f"  Wayback index {year}: {e}")
            time.sleep(20 * (attempt + 1))
    if stamps is None:  # the captures already stored are the same files
        return have
    out = []
    for ts in stamps:
        p = RAW / "wayback" / f"uf_{year}_{ts}.csv"
        if not p.exists():
            time.sleep(2)  # gentle on the archive
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(F.open_url(f"https://web.archive.org/web/{ts}id_/https://{src}", timeout=90))
        out.append(p)
    return out


def national_path(files, election) -> dict:
    """{days out (0-60): ballots cast} summed over the states of UF national tables; the newest file of a day wins. File
    names end in the capture or snapshot day: uf_<year>_<YYYYMMDDhhmmss>.csv or uf_<year>_<YYYY-MM-DD>.csv."""
    path = {}
    for p in sorted(files, key=lambda q: q.stem.split("_", 2)[2].replace("-", "")):
        stamp = p.stem.split("_", 2)[2]
        tau = int((pd.Timestamp(election) - pd.Timestamp(stamp[:10] if "-" in stamp else stamp[:8])).days)
        if not 0 <= tau <= 60:
            continue
        rows = csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig")))
        path[tau] = sum(int(float(r["voted_all"])) for r in rows if r.get("voted_all") not in (None, "", "NA"))
    return path


def profile_fit(write=True) -> dict:
    """Fit W, P of h to the 2024 national path (share of the Election-Day total by days out, from the Wayback captures) and
    report how the 2026 path so far fits that shape with its own scale (the 2026 final share is not known yet). Writes
    PROFILE_JSON; only shares are stored - UF's tables themselves are not republished (CC BY-NC-ND 4.0)."""
    from scipy.optimize import minimize

    p24 = national_path(wayback_captures(2024), "2024-11-05")
    us24 = sorted((t, v / p24[0]) for t, v in p24.items())
    p26 = national_path(wayback_captures(2026) + sorted(RAW.glob("uf_2026_*.csv")), ELECTION)
    t22 = sum(turnout_2022().values())
    us26 = sorted((t, v / t22) for t, v in p26.items() if v > 0)
    r = minimize(
        lambda x: sum((float(h(t, *x)) - s) ** 2 for t, s in us24),
        [W, P_SHAPE],
        method="Nelder-Mead",
        options=dict(xatol=1e-4, fatol=1e-12),
    )
    w, p = (float(v) for v in r.x)
    H = np.array([float(h(t, w, p)) for t, _ in us26])
    b = np.array([s for _, s in us26])
    scale = float(b @ H / (H @ H))
    rec = {
        "description": "Timing profile h(tau) = (1 - tau/W)^P of pre-Election-Day ballots (early_vote.profile_fit): W, P fitted "
        "to the 2024 national path; check_2026 = the 2026 path so far (banked share of 2022 turnout) against that shape "
        "with its scale free. Source: UF Election Lab national early-vote tables (2024 via Wayback Machine captures); "
        "only derived shares are stored.",
        "W": round(w, 1),
        "P": round(p, 2),
        "fitted": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "fit_2024": {
            "points": [[t, round(s, 4)] for t, s in us24],
            "rms": round(float(np.sqrt(r.fun / len(us24))), 4),
        },
        "check_2026": {
            "points": [[t, round(s, 4)] for t, s in us26],
            "scale": round(scale, 3),
            "rms": round(float(np.sqrt(np.mean((b - scale * H) ** 2))), 4),
        },
        "previous": {"W": 46.0, "P": 3.0},
    }
    if write:
        PROFILE_JSON.write_text(json.dumps(rec, indent=1) + "\n")
    return rec


def kvec(states) -> np.ndarray:
    from . import model as M

    k = M.MOVE["k"]
    return np.array([k.get(s, 1.0) for s in states], float)


if __name__ == "__main__":
    import sys

    if "--fetch" in sys.argv:
        print("snapshot:", fetch())
    if "profile" in sys.argv:  # refit the timing profile (Wayback captures + the local 2026 snapshots)
        rec = profile_fit()
        print(f"W {rec['W']}  P {rec['P']}  2024 rms {rec['fit_2024']['rms']}  2026 check {rec['check_2026']}")
        sys.exit(0)
    k = k_factors()
    meta = k.pop("_meta")
    sh = share_2022()
    bk = banked()
    print(
        f"lead {meta['lead']} d, measured movement sd {np.sqrt(meta['m2']):.2f} pts, I_hist {meta['I_hist']}, snapshot {meta['snapshot']}"
    )
    for s in sorted(k, key=k.get):
        print(
            f"  {s}  2022 pre-ED {sh.get(s, float('nan')):.2f}  banked now {bk.get(s, 0):.3f}  k {k[s]:.3f}  movement sd {k[s] * np.sqrt(meta['m2']):.2f}"
        )
