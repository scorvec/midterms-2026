"""North Carolina on election night: what the early vote reported first should look like, and the final margin it implies.

North Carolina reports its early in-person (one-stop) and mail ballots first, county by county, within about an hour of the
polls closing; the Election Day precincts follow. Who cast the banked ballots is known by party registration (the State
Board's absentee file), and so is who voted each way in every general since 2016 (its voter history). The model is a
registration accounting:

    margin of a county's ballots cast one way = sum over parties of (party share of those ballots) x (that party's net vote)

each party's net vote (Democrat minus Republican, points) = a level for the contest + a vote-method effect + a county effect,
fitted by weighted least squares on the county x method results of every statewide contest 2016-2024. On the night one
number is unknown, the contest's level shift theta (every party's net vote moves together): the reported early and mail
results fix it, and the Election Day vote still to come - each county's early + mail ballots by party x the same county's
Election Day to early + mail ratio in the last same-kind general (2022 for 2026), corrected statewide by the counties that
have finished - gives the final margin. Before the night it runs the other way: for any final margin, the early + mail
batch it implies.

Replayed on the ten statewide contests of 2016-2024, each from a fit without its year: the final margin from the early and
mail batch alone missed by 1.6 points on average (RMS 2.0), against 5.9 for the batch read as it stands; on the eight
contests 538's poll archives cover, 1.2 against 3.4 for the final three weeks of polls (the polls were closer in 2022 and
for the 2024 governor). `backtest` prints the table, the comparison and the error at each share of counties finished.

    python -m midterms.nc_night build        # one-time: voter history + results by method -> data/static/nc_night/*.csv
    python -m midterms.nc_night backtest     # leave-one-year-out replays
    python -m midterms.nc_night expect       # 2026: the early + mail batch each final Senate margin implies -> EXPECT
    python -m midterms.nc_night live [file]  # 2026 night: results file -> the final-margin estimate -> NIGHT

Aggregates only (counts by county, vote method and party); never voter rows."""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ncsbe"
STATIC = ROOT / "data" / "static" / "nc_night"
EXPECT = ROOT / "data" / "early" / "nc_night.json"  # before the night (early-diag.yml, nightly)
NIGHT = ROOT / "data" / "results" / "nc_night_2026.json"  # on the night (nc-night.yml)
GENERALS = {2016: "11/08/2016", 2018: "11/06/2018", 2020: "11/03/2020", 2022: "11/08/2022", 2024: "11/05/2024"}
RESULTS = {2016: "20161108", 2018: "20181106", 2020: "20201103", 2022: "20221108", 2024: "20241105"}
ELECTION_2026 = dt.date(2026, 11, 3)
ONE_STOP_LAST = dt.date(2026, 11, 1)  # the last day of one-stop voting (the Saturday before)
# voter history methods -> early (one-stop in person, curbside there included) / mail / eday (Election Day, transfers,
# provisionals: the results file reports provisionals apart, and they are folded into Election Day on both sides)
METHOD = {
    "ABSENTEE ONESTOP": "early",
    "ABSENTEE CURBSIDE": "early",
    "EARLY VOTING IN-PERSON": "early",
    "EARLY VOTING CURBSIDE": "early",
    "ABSENTEE BY MAIL": "mail",
    "ELECTION DAY IN-PERSON": "eday",
    "ELECTION DAY CURBSIDE": "eday",
    "TRANSFER": "eday",
    "PROVISIONAL": "eday",
}
PARTIES = ("DEM", "REP", "UNA")  # Libertarian, Green and No Labels voters count as unaffiliated
BATCH = ("early", "mail")
# the statewide partisan contests of each general (2018 had none: the House races summed over contested districts)
CONTESTS = {
    2016: ["US SENATE", "NC GOVERNOR", "US PRESIDENT"],
    2018: ["US HOUSE"],
    2020: ["US SENATE", "NC GOVERNOR", "US PRESIDENT"],
    2022: ["US SENATE"],
    2024: ["NC GOVERNOR", "US PRESIDENT"],
}
REF = {
    2016: 2020,
    2018: 2022,
    2020: 2016,
    2022: 2018,
    2024: 2020,
    2026: 2022,
}  # last same-kind general (2016/2018: the next)
U_RIDGE = 2.0  # county effects shrunk toward zero (in units of the mean cell weight)
PRIOR_SHARE = (
    1.0  # weight of the batch's level and of the projected turnout against the finished counties' (share of the
)
# state's projected Election Day vote)


# --- inputs ------------------------------------------------------------------------------------------------------------------
def build_voters(src=None):
    """Voter history -> ballots by general, county, method and party (the voter's registration when voting)."""
    src = Path(src or RAW / "ncvhis_Statewide.txt")
    cols = ["voted_county_desc", "election_lbl", "voting_method", "voted_party_cd"]
    want = set(GENERALS.values())
    parts = []

    def take(fh):
        for chunk in pd.read_csv(fh, sep="\t", usecols=cols, dtype=str, chunksize=2_000_000, encoding="latin-1"):
            ch = chunk[chunk["election_lbl"].isin(want)]
            ch = ch.assign(method=ch["voting_method"].map(METHOD)).dropna(subset=["method"])
            ch["party"] = ch["voted_party_cd"].where(ch["voted_party_cd"].isin(PARTIES), "UNA")
            parts.append(ch.groupby(["election_lbl", "voted_county_desc", "method", "party"]).size())

    if src.suffix == ".zip":
        with zipfile.ZipFile(src) as z, z.open(z.namelist()[0]) as fh:
            take(fh)
    else:
        with open(src, "rb") as fh:
            take(fh)
    s = pd.concat(parts).groupby(level=[0, 1, 2, 3]).sum().rename("n").reset_index()
    year = {v: k for k, v in GENERALS.items()}
    s.insert(0, "year", s.pop("election_lbl").map(year))
    return s.rename(columns={"voted_county_desc": "county"}).sort_values(["year", "county", "method", "party"])


def read_results(src):
    """A results_pct file (path or bytes) -> rows with early / mail / eday votes (provisionals in eday) and the precinct flag."""
    with zipfile.ZipFile(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src) as z:
        d = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype=str, encoding="latin-1")
    early = "Early Voting" if "Early Voting" in d else "One Stop"
    for c in (early, "Absentee by Mail", "Election Day", "Provisional"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0)
    d["eday"] = d["Election Day"] + d["Provisional"]
    d = d.rename(columns={early: "early", "Absentee by Mail": "mail"})
    party = d["Choice Party"].fillna("")
    d["side"] = np.where(party == "DEM", "dem", np.where(party == "REP", "rep", "oth"))
    return d


def contest_rows(d, contest):
    if contest == "US HOUSE":
        c = d[d["Contest Name"].str.startswith("US HOUSE OF REPRESENTATIVES", na=False)]
        both = c.groupby("Contest Name")["Choice Party"].agg(lambda p: {"DEM", "REP"} <= set(p))
        return c[c["Contest Name"].isin(both[both].index)]  # contested by both parties only
    return d[d["Contest Name"] == contest]


def by_method(c):
    """Contest rows -> (county, method) x dem / rep / oth votes."""
    g = c.groupby(["County", "side"])[["early", "mail", "eday"]].sum()
    out = g.stack().unstack("side").reindex(columns=["dem", "rep", "oth"]).fillna(0.0)
    out.index.names = ["county", "method"]
    return out.reset_index()


def build_results():
    """Results by method -> votes by general, contest, county and method for the Democrat, the Republican and everyone else."""
    rows = []
    for y, ymd in RESULTS.items():
        d = read_results(RAW / f"results_pct_{ymd}.zip")
        rows.extend(by_method(contest_rows(d, k)).assign(year=y, contest=k) for k in CONTESTS[y])
    return pd.concat(rows, ignore_index=True)[["year", "contest", "county", "method", "dem", "rep", "oth"]]


def build():
    STATIC.mkdir(parents=True, exist_ok=True)
    R = build_results()
    R.to_csv(STATIC / "results_by_method.csv", index=False)
    print(f"results: {len(R)} rows, {R.groupby(['year', 'contest']).size().to_dict()}")
    V = build_voters()
    V.to_csv(STATIC / "voters_by_method.csv", index=False)
    print(f"voters: {len(V)} rows; ballots by year {V.groupby('year')['n'].sum().to_dict()}")


def ballots_2026(raw=None):
    """Accepted 2026 early and mail ballots by county, method and party from the absentee file (nc_early.fetch_2026)."""
    if raw is None:
        from . import nc_early

        raw, _ = nc_early.fetch_2026()
    cols = ["ncid", "county_desc", "voter_party_code", "ballot_req_type", "ballot_rtn_status", "ballot_rtn_dt"]
    d = pd.read_csv(io.BytesIO(raw), usecols=cols, dtype=str, encoding="latin-1", on_bad_lines="skip").fillna("")
    d = d[d["ballot_rtn_status"].str.strip().str.upper().str.startswith("ACCEPTED")].copy()
    d["rtn"] = pd.to_datetime(d["ballot_rtn_dt"].str.strip(), format="%m/%d/%Y", errors="coerce")
    d = d.sort_values("rtn", kind="stable").drop_duplicates("ncid", keep="last")  # a reissued ballot counts once
    d["method"] = np.where(d["ballot_req_type"].str.strip().str.upper() == "MAIL", "mail", "early")
    p = d["voter_party_code"].str.strip().str.upper()
    d["party"] = p.where(p.isin(PARTIES), "UNA")
    out = d.groupby([d["county_desc"].str.strip().str.upper(), "method", "party"]).size().rename("n").reset_index()
    return out.rename(columns={"county_desc": "county"}).assign(year=2026)[["year", "county", "method", "party", "n"]]


# --- the accounting model -------------------------------------------------------------------------------------------------
def load():
    return pd.read_csv(STATIC / "voters_by_method.csv"), pd.read_csv(STATIC / "results_by_method.csv")


def shares(V):
    """(year, county, method) -> party shares of the ballots and the ballot count."""
    t = V.pivot_table(index=["year", "county", "method"], columns="party", values="n", aggfunc="sum").fillna(0.0)
    t = t.reindex(columns=list(PARTIES), fill_value=0.0)
    out = t.div(t.sum(1), axis=0)
    out["ballots"] = t.sum(1)
    return out


def cells(V, R):
    """County x method cells with the contest's votes, margin (points of its votes) and the ballots' party shares."""
    r = R.assign(votes=R["dem"] + R["rep"] + R["oth"])
    r = r[r["votes"] > 0].copy()
    r["margin"] = 100 * (r["dem"] - r["rep"]) / r["votes"]
    return r.join(shares(V), on=["year", "county", "method"]).dropna(subset=list(PARTIES))


class Fit:
    """Party levels per training contest, method effects per party (Election Day = 0), county effects (ridge)."""

    def __init__(self, C):
        self.counties = sorted(C["county"].unique())
        ci = {c: i for i, c in enumerate(self.counties)}
        keys = sorted(set(zip(C["year"], C["contest"])))
        ki = {k: i for i, k in enumerate(keys)}
        npar = len(PARTIES)
        o_m = len(keys) * npar  # method effects: early, mail
        o_u = o_m + len(BATCH) * npar  # county effects
        X = np.zeros((len(C), o_u + len(self.counties)))
        S = C[list(PARTIES)].to_numpy()
        for j, (k, m, c) in enumerate(zip(zip(C["year"], C["contest"]), C["method"], C["county"])):
            X[j, ki[k] * npar : (ki[k] + 1) * npar] = S[j]
            if m in BATCH:
                o = o_m + BATCH.index(m) * npar
                X[j, o : o + npar] = S[j]
            X[j, o_u + ci[c]] = 1.0
        w = C["votes"].to_numpy(float)
        w = w / w.mean()
        A = X.T @ (X * w[:, None])
        A[o_u:, o_u:] += U_RIDGE * np.eye(len(self.counties))
        b = np.linalg.solve(A, X.T @ (w * C["margin"].to_numpy()))
        self.alpha = np.mean(
            [b[ki[k] * npar : (ki[k] + 1) * npar] for k in keys], axis=0
        )  # the average contest's levels
        self.delta = {m: b[o_m + i * npar : o_m + (i + 1) * npar] for i, m in enumerate(BATCH)}
        self.delta["eday"] = np.zeros(npar)
        self.u = dict(zip(self.counties, b[o_u:]))

    def margin(self, S, method, counties, theta):
        """Margins of ballots with party shares S [n, 3] cast `method` in `counties`, at level shift theta (all parties)."""
        return (S * (self.alpha + self.delta[method])).sum(1) + theta + np.array([self.u.get(c, 0.0) for c in counties])


def eday_projection(V, year, ref, how="ratio"):
    """Election Day ballots by county x party.
    ratio  - this year's early + mail ballots x the reference general's Election Day to early + mail ratio for the same county
             and party: a party that banks fewer ballots is assumed to turn out less (replays: RMS 2.0 on the final);
    totals - the reference general's total ballots for the county and party minus this year's banked ones: a party that banks
             fewer ballots is assumed to vote on Election Day instead (replays: RMS 3.3 at the reference turnout, 2.3 even at
             the true one). Shown beside the ratio projection as the risk of a shift between ways of voting (2024: Republicans
             moved to early voting and the ratio projection came out 2.6-3.8 points too Republican)."""
    t = V.pivot_table(index=["year", "county", "party"], columns="method", values="n", aggfunc="sum").fillna(0.0)
    em = t["early"] + t["mail"]
    if how == "totals":
        tot = t.loc[ref].sum(1)
        e = pd.concat([tot.rename("t"), em.loc[year].rename("em")], axis=1).fillna(0.0)
        out = (e["t"] - e["em"]).clip(lower=0.05 * e["t"])
    else:
        q = t.loc[ref, "eday"] / em.loc[ref].clip(lower=1)
        e = pd.concat([em.loc[year].rename("em"), q.rename("q")], axis=1).fillna(0.0)
        out = e["em"] * e["q"]
    return out.unstack("party").reindex(columns=list(PARTIES), fill_value=0.0)


def batch_margin(F, Vt, theta):
    """Predicted early + mail batch (votes, margin) by county at theta. Vt: this year's ballots (county, method, party, n)."""
    sh = shares(Vt.assign(year=0)).loc[0]
    rows = []
    for m in BATCH:
        if m not in sh.index.get_level_values("method"):
            continue
        s = sh.xs(m, level="method")
        s = s[s.index.isin(F.counties)]
        rows.append(
            pd.DataFrame({"votes": s["ballots"], "margin": F.margin(s[list(PARTIES)].to_numpy(), m, s.index, theta)})
        )
    b = pd.concat(rows)
    return b.groupby(level=0).apply(
        lambda g: pd.Series({"votes": g["votes"].sum(), "margin": np.average(g["margin"], weights=g["votes"])})
    )


def estimate(F, obs, E, vpb=1.0):
    """Final margin from the reported results.
    obs: one row per county x method reported (county, method, votes, margin, party shares); Election Day rows only for
    counties whose Election Day count is complete. E: projected Election Day ballots by county x party. vpb: the contest's
    votes per Election Day ballot. -> (final margin, theta, kappa)."""
    S = obs[list(PARTIES)].to_numpy()
    base = np.zeros(len(obs))
    for m in ("early", "mail", "eday"):
        k = (obs["method"] == m).to_numpy()
        if k.any():
            base[k] = F.margin(S[k], m, obs["county"][k], 0.0)
    w = obs["votes"].to_numpy(float)
    res = obs["margin"].to_numpy() - base
    kb, ke = obs["method"].isin(BATCH).to_numpy(), obs["method"].eq("eday").to_numpy()
    theta = float(np.average(res[kb], weights=w[kb]))  # the early + mail batch's level
    Ed = E[E.index.isin(F.counties)]
    proj = Ed.sum(1) * vpb
    prior = PRIOR_SHARE * float(proj.sum())
    # Election Day's own level: the finished counties' Election Day votes, shrunk toward the batch's (the gap between the two
    # ways of voting moves from year to year)
    theta_e = float((np.sum(w[ke] * res[ke]) + prior * theta) / (w[ke].sum() + prior))
    done = set(obs.loc[ke, "county"])
    p_done = float(proj[proj.index.isin(done)].sum())
    kappa = (float(w[ke].sum()) + prior) / (p_done + prior)  # finished counties' turnout against the projection
    rest = Ed[~Ed.index.isin(done)]
    n = rest.sum(1).to_numpy() * vpb * kappa
    m = F.margin(rest.div(rest.sum(1).clip(lower=1), axis=0).to_numpy(), "eday", rest.index, theta_e)
    total = float(np.sum(w * obs["margin"].to_numpy()) + np.sum(n * m))
    return total / (w.sum() + n.sum()), theta, kappa


def theta_for_final(F, Vt, E, final, vpb=1.0):
    """The level shift at which the early + mail batch plus the projected Election Day vote gives `final` (linear in theta)."""

    def at(th):
        b = batch_margin(F, Vt, th)
        Ed = E[E.index.isin(F.counties)]
        n = Ed.sum(1).to_numpy() * vpb
        m = F.margin(Ed.div(Ed.sum(1).clip(lower=1), axis=0).to_numpy(), "eday", Ed.index, th)
        return float((np.sum(b["votes"] * b["margin"]) + np.sum(n * m)) / (b["votes"].sum() + n.sum()))

    f0 = at(0.0)
    return final - f0  # the final moves one for one with theta (every ballot's margin does)


# --- backtest -----------------------------------------------------------------------------------------------------------
def _observed(C, year, contest, done=()):
    c = C[(C["year"] == year) & (C["contest"] == contest)]
    return c[c["method"].isin(BATCH) | (c["method"].eq("eday") & c["county"].isin(done))]


POLL_TYPE = {"US PRESIDENT": "Pres-G", "US SENATE": "Sen-G", "NC GOVERNOR": "Gov-G"}


def poll_average(year, contest):
    """The final three weeks' poll average (each pollster's polls averaged first, then the pollsters), Democrat minus
    Republican, from 538's archives: raw_polls to 2022, the governor archive for 2024; None where neither has the race."""
    raw = ROOT / "data" / "raw"
    if year <= 2022 and contest in POLL_TYPE:
        r = pd.read_csv(raw / "538repo" / "raw_polls.csv", low_memory=False)
        r = r[(r["location"] == "NC") & (r["cycle"] == year) & (r["type_simple"] == POLL_TYPE[contest])]
        two = r["cand1_party"].isin(["DEM", "REP"]) & r["cand2_party"].isin(["DEM", "REP"])
        r = r[two & (r["cand1_party"] != r["cand2_party"])]
        if r.empty:
            return None
        m = pd.Series(np.where(r["cand1_party"] == "DEM", 1, -1) * r["margin_poll"], index=r.index)
        return float(m.groupby(r["pollster"]).mean().mean())
    if year == 2024 and contest == "NC GOVERNOR":
        g = pd.read_csv(raw / "538" / "governor_polls_historical.csv", low_memory=False)
        g = g[(g["state"] == "North Carolina") & (g["cycle"] == 2024)]
        g = g[pd.to_datetime(g["end_date"], format="%m/%d/%y", errors="coerce") >= "2024-10-15"]
        q = g.pivot_table(index=["question_id", "pollster"], columns="party", values="pct", aggfunc="first")
        q = q[["DEM", "REP"]].dropna()
        return float((q["DEM"] - q["REP"]).groupby(level="pollster").mean().mean()) if len(q) else None
    return None


def backtest(stages=(0.0, 0.25, 0.5), draws=40, seed=3):
    """Leave-one-year-out: each contest's final from its early + mail batch (and, at later stages, a random share of counties
    with their Election Day count complete), projected Election Day turnout from the reference general."""
    V, R = load()
    C = cells(V, R)
    rng = np.random.default_rng(seed)
    rows = []
    for (y, k), c in C.groupby(["year", "contest"]):
        F = Fit(C[C["year"] != y])
        actual = float(np.average(c["margin"], weights=c["votes"]))
        b = c[c["method"].isin(BATCH)]
        eb = V[(V["year"] == y) & (V["method"] == "eday")]
        vpb = c.loc[c["method"] == "eday", "votes"].sum() / eb["n"].sum()
        E = eday_projection(V, y, REF[y])
        row = {"year": y, "contest": k, "actual": actual, "batch": float(np.average(b["margin"], weights=b["votes"]))}
        pa = poll_average(y, k)
        row["polls_err"] = np.nan if pa is None else pa - actual
        for f in stages:
            errs = []
            for _ in range(draws if f > 0 else 1):
                done = rng.permutation(F.counties)[: round(f * len(F.counties))]
                est, _, _ = estimate(F, _observed(C, y, k, done), E, vpb)
                errs.append(est - actual)
            row[f"err_{int(100 * f)}"] = float(np.sqrt(np.mean(np.square(errs))))  # RMS over completion orders
            if f == 0:
                row["est_0"] = est
                row["err_0_signed"] = est - actual
        rows.append(row)
    return pd.DataFrame(rows)


# --- 2026 ---------------------------------------------------------------------------------------------------------------
def _forecast_nc():
    """The forecast's North Carolina Senate margin (median) and 10-90 % range, from today's published model."""
    m = json.loads((ROOT / "web" / "data" / "model.json").read_text())
    r = next(x for x in m["senate"]["races"] if x["state"] == "NC")
    return {"challenger": r["dem"], "republican": r["rep"], "median": r["mu"], "asof": m["asof"]}


def _bt_sd():
    p = STATIC / "backtest.json"
    return json.loads(p.read_text()) if p.exists() else None


PROJECTIONS = ("ratio", "totals")


def _setup(raw=None):
    """(fit on 2016-2024, 2026 banked ballots, Election Day projections, Senate votes per Election Day ballot, file date)."""
    file_date = None
    if raw is None:
        from . import nc_early

        raw, file_date = nc_early.fetch_2026()
    V, R = load()
    C = cells(V, R)
    V26 = ballots_2026(raw)
    VV = pd.concat([V, V26])
    E = {h: eday_projection(VV, 2026, REF[2026], h) for h in PROJECTIONS}
    eb = V[(V["year"] == REF[2026]) & (V["method"] == "eday")]["n"].sum()
    vpb = float(
        C[(C["year"] == REF[2026]) & (C["method"] == "eday")]["votes"].sum() / eb
    )  # Senate votes per ballot, 2022
    return Fit(C), V26, E, vpb, file_date


def expect(raw=None, finals=range(-6, 21)):
    """2026, before the night: the early + mail batch each final Senate margin implies, under each Election Day projection
    (statewide; by county at the forecast's median). Until early voting closes (Nov 1) the banked ballots are a partial count
    and the totals projection overstates the Election Day vote still to come."""
    F, V26, E, vpb, file_date = _setup(raw)
    fc = _forecast_nc()
    still_open = file_date is not None and file_date <= ONE_STOP_LAST
    out = {
        "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"),
        "contest": "US SENATE",
        "forecast": fc,
        "banked": {
            "file_date": str(file_date) if file_date else None,
            "ballots": int(V26["n"].sum()),
            "by_party": V26.groupby("party")["n"].sum().astype(int).to_dict(),
        },
        "early_voting_open": still_open,
        "backtest": _bt_sd(),
    }
    for h in PROJECTIONS:
        if h == "totals" and still_open:
            out[h] = {"note": "shown once one-stop voting has closed: until then the banked count is partial"}
            continue
        b0 = batch_margin(F, V26, theta_for_final(F, V26, E[h], 0.0, vpb))
        at0 = float(np.average(b0["margin"], weights=b0["votes"]))  # the batch moves one for one with the final (theta)
        b = batch_margin(F, V26, theta_for_final(F, V26, E[h], fc["median"], vpb))
        out[h] = {
            "eday_projected": int(E[h].sum().sum()),
            "batch_at_even_final": round(at0, 2),
            "batch_for_final": {int(f): round(at0 + f, 2) for f in finals},
            "batch_at_median": round(float(np.average(b["margin"], weights=b["votes"])), 2),
            "by_county_at_median": {c: [int(v), round(float(m), 1)] for c, (v, m) in b.iterrows()},
        }
    return out


def county_obs(d, contest, V26):
    """2026 results file -> observed cells and the counties whose Election Day count is complete: every county's early and mail
    votes once reported, its Election Day votes once every real precinct listed shows some (a precinct not in yet shows zero)."""
    c = contest_rows(d, contest)
    bm = by_method(c)
    bm["votes"] = bm["dem"] + bm["rep"] + bm["oth"]
    bm = bm[bm["votes"] > 0].copy()
    real = c[c["Real Precinct"].eq("Y")].groupby(["County", "Precinct"])["eday"].sum()
    complete = {cty for cty, g in real.groupby(level=0) if (g > 0).all()}
    bm = bm[bm["method"].isin(BATCH) | (bm["method"].eq("eday") & bm["county"].isin(complete))]
    bm["margin"] = 100 * (bm["dem"] - bm["rep"]) / bm["votes"]
    return bm.join(shares(V26.assign(year=0)).loc[0][list(PARTIES)], on=["county", "method"]), complete


def live(src=None, raw=None):
    """2026, on the night: the reported results -> the final Senate margin under each Election Day projection. None when the
    results file has not changed since the last read (nothing to write)."""
    from . import fetch as F_

    if src is None:
        url = f"https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{ELECTION_2026:%Y_%m_%d}/results_pct_{ELECTION_2026:%Y%m%d}.zip"
        src, changed = F_.get(url, RAW / f"results_pct_{ELECTION_2026:%Y%m%d}.zip", timeout=120)
        if not changed and NIGHT.exists():
            return None
    F, V26, E, vpb, _ = _setup(raw)
    obs0, complete = county_obs(read_results(src), "US SENATE", V26)
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ")
    if obs0.empty:
        return {"generated": now, "status": "no results yet"}
    out = {
        "generated": now,
        "contest": "US SENATE",
        "counties_batch": int(obs0.loc[obs0["method"].isin(BATCH), "county"].nunique()),
        "counties_complete": len(complete),
        "reported_votes": {k: int(v) for k, v in obs0.groupby("method")["votes"].sum().items()},
        "reported_margin": round(float(np.average(obs0["margin"], weights=obs0["votes"])), 2),
        "forecast": _forecast_nc(),
        "backtest": _bt_sd(),
    }
    for h in PROJECTIONS:
        obs = obs0.copy()
        k = obs["method"].eq("eday")
        if (
            k.any()
        ):  # a finished county's Election Day ballots: the projection's party mix (the voter history comes weeks later)
            es = E[h].div(E[h].sum(1).clip(lower=1), axis=0)
            obs.loc[k, list(PARTIES)] = es.reindex(obs.loc[k, "county"]).to_numpy()
        obs = obs.dropna(subset=list(PARTIES))
        est, theta, kappa = estimate(F, obs, E[h], vpb)
        out[h] = {"estimate": round(est, 2), "theta": round(theta, 2), "kappa": round(kappa, 3)}
    return out


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["build"]:
        build()
    elif cmd == ["backtest"]:
        T = backtest()
        pd.set_option("display.width", 200)
        print(T.round(2).to_string(index=False))
        sd = {
            c: round(float(np.sqrt(np.mean(T[c] ** 2))), 2)
            for c in T.columns
            if c.startswith("err_") and c != "err_0_signed"
        }
        print("RMS error by share of counties with Election Day complete:", sd)
        P = T[T["polls_err"].notna()]
        vs = {
            "contests": len(P),
            "polls_mae": round(float(P["polls_err"].abs().mean()), 2),
            "batch_mae": round(float(P["err_0_signed"].abs().mean()), 2),
        }
        print("where the poll archives have the race:", vs)
        _write(STATIC / "backtest.json", {"rmse": sd, "contests": len(T), "vs_polls": vs})
    elif cmd == ["expect"]:
        _write(EXPECT, expect())
    elif cmd == ["live"]:
        doc = live(sys.argv[2] if len(sys.argv) > 2 else None)
        if doc is not None:
            _write(NIGHT, doc)
    else:
        sys.exit(__doc__)
