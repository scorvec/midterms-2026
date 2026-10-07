"""2026 governors (2026-09-22; prior reviewed 2026-10-05). Same machinery as the Senate: a fundamentals prior, race polls with the live
corrections, precision blend, correlated simulation on the SHARED national draw.

Prior (Prior), fitted on every governor race 1998-2024 from the Wikipedia yearly results pages:
    margin = b0 + b1*lean + b1t*lean*(year-2010)/10 + b2*inc + bs*successor + bq*(experienced D - experienced R) + b3*E
    (lean: MIT presidential, mean of the two previous elections vs the nation; E: national House margin; inc: an elected incumbent on
    the ballot; successor: an incumbent who took office mid-term and runs for the first time; experience: statewide elected office or
    Congress before the race, gov_quality). PRIOR_SPEC = {} is the prior before the review. Scored by midterms/gov_backtest.py.
Polls: VoteHub (poll_type=governor), each answer given a party from the Wikipedia candidate list; the D and R nominees are the
highest-polling candidate of each party (Alaska's top-four ballot has several Republicans). Corrections as the Senate: sponsor /
pollster lean, undecided / third-party variance, spread inflation, age fade.
    python -m midterms.gov2026
"""
import io, json, re, urllib.request
from pathlib import Path
import numpy as np, pandas as pd
from . import poll_overrides as PO
from . import poll_corrections as PC
from . import model as M, wiki_polls as W, rcv as RC
ROOT = Path(__file__).resolve().parents[1]
TWO_YEAR = {"NH", "VT"}
POLL_SD, POLL_SYS = M.SEN_POLL_SD, M.SEN_POLL_SYS      # the Senate's per-poll and race-average poll errors (see GOV_POLL_* below)

# ---- Governor model review (2026-10-05; midterms/gov_backtest.py, README "Governor model review"). Every switch below was scored
# on the leak-free governor harness (2006-2022, walk-forward prior, the live poll pipeline); the values here are the LIVE ones -
# a switch is on only where the harness showed a significant out-of-sample gain. Nothing here shifts either party.
INC_FIX = False         # history(): successors who ran count as full incumbents (tested; superseded by the successor term below)
# ADOPTED 2026-10-05 (README "Governor model review"): the lean slope's time trend, successor incumbents as their own term, nominee
# experience (D minus R). Leak-free harness 2006-2024, 234 races x 3-6 dates, against the previous prior: log loss -0.0111 (9 of 10
# cycles, sign-flip p 0.037), Brier -0.0043 (p 0.020), CRPS of the margin -0.27 (p 0.012), mean error -0.35 pts (p 0.016).
PRIOR_SPEC = {"lean_t": True, "succ": True, "qual": "all"}      # {} = the prior before the review
GOV_POLL_SD = None      # per-poll governor poll error; None = the Senate's POLL_SD
GOV_POLL_SYS = None     # race-average governor poll error (total, before the shared part is taken out); None = POLL_SYS
SHARED_K = 1.0          # scale of the shared statewide shock the governor simulation draws (x b3 / NAT_SLOPE)
RACE_SD_K = 1.0         # scale of each race's own outcome sd in the simulation
PRIOR_SD_K = 1.0        # scale of the prior sd in the blend (fundamentals v polls weight)
UND_PARAMS = None       # (free, slope, cap) for governor polls' undecided variance; None = model.UND_*
THIRD_MULT = None       # governor polls with a third candidate at 10 %+; None = model.THIRD_MULT
AGE_HALF = None         # governor poll staleness half-life (days); None = model.AGE_HALF


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


# status text of an incumbent who was on the general ballot. The first parser matched only "re-elected / lost re-election /
# defeated", so 19 incumbents who had succeeded mid-term and then ran (Ivey AL 2018, Reynolds IA 2018, Hochul NY 2022, McKee RI 2022,
# Parson MO 2020, Quinn IL 2010 ...: "Incumbent elected to full term"; Kernan IN 2004: "lost election to full term") counted as open seats.
_RAN_OLD = r"re-?elected|lost re-?election|defeated"
_RAN = r"re-?elected|lost re-?election|defeated|elected to (?:a )?(?:full|finish) term|lost election to (?:a )?full term"
_NOT_RAN = r"lost (?:re-?)?nomination|lost (?:the )?primary|lost nomination|withdrew"
_CAND = r"([^%▌]+?)\s*\(([^)]+)\)\s*([\d.]+)%"          # "Name (Party) 49.2%" (1998/2002 tables have no ▌ markers)
_DEM = ("Democratic", "DFL", "Democratic–Farmer–Labor", "Democratic-NPL")


def _surname(n):
    w = [x for x in re.findall(r"[a-z]+", re.sub(r"\(.*?\)", "", str(n)).lower()) if x not in ("jr", "sr", "ii", "iii", "iv")]
    return w[-1] if w else ""


def parse_results(y, html):
    """Rows of one yearly "United States gubernatorial elections" page's results table (D-v-R races only)."""
    from .data_prep import _ST
    T = None
    for tb in pd.read_html(io.StringIO(html)):
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tb.columns]
        if any(c.startswith("Candidates") for c in cols) and any(c.startswith("State") for c in cols) and len(tb) >= 10: tb.columns = cols; T = tb; break
    if T is None: return []
    cc = [c for c in T.columns if c.startswith("Candidates")][0]; sc = [c for c in T.columns if c.startswith("State")][0]
    stc = [c for c in T.columns if c.startswith("Status") or c.startswith("Result")]
    pc = [c for c in T.columns if c.startswith("Party")]
    rows = []
    for _, r in T.iterrows():
        st = _ST.get(re.sub(r"\[.*?\]", "", str(r[sc])).strip())
        if not st: continue
        c = [(re.sub(r"^(?:Others\s+|Y\s+(?=[A-Z]))", "", n.strip()), p, float(v)) for n, p, v in re.findall(_CAND, re.sub(r"\[.*?\]", "", str(r[cc])))]
        dd = [(n, v) for n, p, v in c if p.startswith(_DEM)]; rr = [(n, v) for n, p, v in c if p.startswith("Republican")]
        if not dd or not rr: continue
        (dn, d), (rn, rp) = max(dd, key=lambda x: x[1]), max(rr, key=lambda x: x[1])
        status = re.sub(r"\[.*?\]", "", str(r[stc[0]])) if stc else ""; party = str(r[pc[0]]) if pc else ""
        ip = 1.0 if party.startswith(("Democratic", "DFL")) else (-1.0 if party.startswith("Republican") else 0.0)
        ran_old = bool(re.search(_RAN_OLD, status, re.I)) and not re.search(r"lost renomination|lost (the )?primary", status, re.I)
        ran = bool(re.search(_RAN, status, re.I)) and not re.search(_NOT_RAN, status, re.I)
        rows.append({"year": y, "state": st, "margin": round(d - rp, 2), "dn": dn, "rn": rn,
                     "oth_max": max([v for n, p, v in c if not p.startswith(_DEM + ("Republican",))], default=0.0),
                     "inc_old": ip if ran_old else 0.0, "inc_fix": ip if ran else 0.0})
    return rows


RESULTS_2024 = ROOT / "data" / "static" / "gov_results_2024.csv"     # derived 2024 rows (gov_quality results2024, GitHub Actions)
USE_2024 = True         # history(): add the 2024 races (data/static/gov_results_2024.csv) to the governor history (a data update)


def history():
    """EVERY governor race 1998-2022 (even years) from the Wikipedia yearly pages' results tables: D% - R% (top candidate of each
    party), incumbent running from the status column. The first version used only 538's POLLED races, which skew competitive:
    the lean slope came out 0.37 and Wyoming's Democrat got 12 %.
    Columns: margin, inc (INC_FIX picks inc_fix or inc_old), lean, E, the nominees (dn, rn), the largest other candidate's share
    (oth_max), and the same state's previous governor race within 4 years (prev_*; inc_same = the incumbent on the ballot won it).
    USE_2024 adds 2024 from the committed derived table."""
    lean = _pres_lean(); E = pd.read_csv(ROOT / "data" / "static" / "house_national_vote_1946.csv").set_index("year").E
    rows = []
    for y in range(1998, 2023, 2): rows += parse_results(y, W.fetch(f"{y} United States gubernatorial elections", max_age_h=10 ** 6))
    H = pd.DataFrame(rows)
    if USE_2024 and RESULTS_2024.exists(): H = pd.concat([H, pd.read_csv(RESULTS_2024)], ignore_index=True)
    H["lean"] = [lean(y, st) for y, st in zip(H.year, H.state)]; H["E"] = H.year.map(E)
    H = H.dropna(subset=["lean", "E", "margin"])
    H["inc"] = H["inc_fix"] if INC_FIX else H["inc_old"]
    H["q_d"] = [experience(y, st, n) for y, st, n in zip(H.year, H.state, H.dn)]; H["q_r"] = [experience(y, st, n) for y, st, n in zip(H.year, H.state, H.rn)]
    H.loc[H.inc_fix > 0, "q_d"] = 1.0; H.loc[H.inc_fix < 0, "q_r"] = 1.0       # a sitting governor is experienced by definition
    return _with_prev(H).sort_values(["year", "state"]).reset_index(drop=True)


QUALITY = ROOT / "data" / "static" / "gov_candidate_quality.csv"     # nominees' prior office (gov_quality, GitHub Actions)
_QUAL = None


def experience(year, state, name):
    """1 if the nominee had held statewide elected office or a seat in Congress before the race (gov_quality), 0 if not, NaN unknown."""
    global _QUAL
    if _QUAL is None:
        _QUAL = {}
        if QUALITY.exists():
            for r in pd.read_csv(QUALITY, keep_default_na=False).itertuples(): _QUAL[(int(r.year), r.state, _surname(r.name))] = float(r.experienced)
    return _QUAL.get((int(year), state, _surname(name)), np.nan) if _QUAL else np.nan


def _with_prev(H):
    H = H.sort_values(["state", "year"]).copy(); g = H.groupby("state")
    for k in ("margin", "E", "lean", "inc", "year", "dn", "rn"): H["prev_" + k] = g[k].shift(1)
    far = (H.year - H.prev_year) > 4
    H.loc[far, [c for c in H.columns if c.startswith("prev_")]] = np.nan
    w = np.where(H.prev_margin > 0, H.prev_dn, H.prev_rn)
    H["inc_same"] = [float(i != 0 and isinstance(x, str) and _surname(x) == _surname(dn if i > 0 else rn)) for i, x, dn, rn in zip(H.inc, w, H.dn, H.rn)]
    return H


def fit(R):
    X = np.column_stack([np.ones(len(R)), R.lean, R.inc, R.E]); b = np.linalg.lstsq(X, R.margin, rcond=None)[0]
    return b, float((R.margin - X @ b).std())


class Prior:
    """Governor fundamentals prior, fitted on the races in H:  margin = b0 + b1 lean + b2 inc + b3 E [+ extra terms].
    spec (PRIOR_SPEC by default):
      lean_t: lean x (year - 2010) / 10 - a lean slope that changes over time (nationalization)
      half:   recency weights 0.5 ** (age in years / half)
      prev:   the same state's previous governor race, as its residual against the base fit ("all"; "split" = separately when the
              incumbent on the ballot won that race himself and otherwise; "inc" = the incumbent's own only). Symmetric in party.
      het:    prior sd by open seat v incumbent ("inc") or growing with the prior's distance from 0 ("lean")
      excl_third: leave races where another candidate took >= this share out of the fit
      succ:   incumbents who succeeded mid-term (history's inc_fix - inc_old) as their own term
      qual:   nominee experience, D minus R (statewide elected office or Congress before the race; gov_quality): "all" / "open" seats
    b[3] is always the national slope."""

    def __init__(self, H, spec=None):
        self.spec = dict(PRIOR_SPEC if spec is None else spec); s = self.spec
        tr = H[H.oth_max < s["excl_third"]] if s.get("excl_third") else H
        self.ymax = int(tr.year.max())
        self.w = (0.5 ** ((self.ymax + 2 - tr.year) / s["half"])).values if s.get("half") else np.ones(len(tr))
        Xb = self._base(tr); self.b_base = self._wls(Xb, tr.margin.values, self.w)
        X = self.design(tr); self.b = self._wls(X, tr.margin.values, self.w); res = tr.margin.values - X @ self.b
        self.sd = float(np.sqrt(np.average(res ** 2, weights=self.w) * len(tr) / (len(tr) - 1)))      # = the old fit()'s residual .std()
        if s.get("het") == "inc":
            ii = (tr.inc != 0).values
            self.sd_by = (float(np.sqrt(np.average(res[ii] ** 2, weights=self.w[ii]))), float(np.sqrt(np.average(res[~ii] ** 2, weights=self.w[~ii]))))
        if s.get("het") == "lean":
            A = np.column_stack([np.ones(len(tr)), np.abs(X @ self.b) / 10]); sw = np.sqrt(self.w)
            self.sd_c = np.linalg.lstsq(A * sw[:, None], np.abs(res) * sw, rcond=None)[0] * np.sqrt(np.pi / 2)

    @staticmethod
    def _wls(X, y, w):
        sw = np.sqrt(w); return np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)[0]

    @staticmethod
    def _base(D): return np.column_stack([np.ones(len(D)), D.lean, D.inc, D.E])

    def prev_resid(self, D):
        if "prev_margin" not in D: return np.zeros(len(D))
        P = pd.DataFrame({"lean": D.prev_lean, "inc": D.prev_inc, "E": D.prev_E}).fillna(0.0)
        r = D.prev_margin.values - self._base(P) @ self.b_base
        return np.where(D.prev_margin.notna().values, r, 0.0)

    def design(self, D):
        s = self.spec; cols = [self._base(D)]
        if s.get("lean_t"): cols.append((D.lean * (D.year - 2010) / 10).values[:, None])
        if s.get("succ"): cols.append((D.inc_fix - D.inc_old).values[:, None])      # successor incumbents, own coefficient
        if s.get("qual"):                                                            # experienced D minus experienced R (0 / +-1)
            q = (D.q_d - D.q_r).fillna(0).values
            cols.append((q * (D.inc.values == 0) if s["qual"] == "open" else q)[:, None])
        if s.get("prev"):
            pr = self.prev_resid(D); same = D.inc_same.fillna(0).values if "inc_same" in D else np.zeros(len(D))
            if s["prev"] == "all": cols.append(pr[:, None])
            elif s["prev"] == "inc": cols.append((pr * same)[:, None])
            else: cols.append(np.column_stack([pr * same, pr * (1 - same)]))
        return np.column_stack(cols)

    def names(self):
        s = self.spec
        return (("const", "lean", "inc", "E") + (("lean_t",) if s.get("lean_t") else ()) + (("succ",) if s.get("succ") else ())
                + (("qual",) if s.get("qual") else ())
                + ((("prev",) if s["prev"] in ("all", "inc") else ("prev_same", "prev_other")) if s.get("prev") else ()))

    def predict(self, D):
        mu = self.design(D) @ self.b; s = self.spec
        if s.get("het") == "inc": sd = np.where(D.inc.values != 0, *self.sd_by)
        elif s.get("het") == "lean": sd = np.clip(self.sd_c[0] + self.sd_c[1] * np.abs(mu) / 10, 4.0, None)
        else: sd = np.full(len(D), self.sd)
        return mu, sd * PRIOR_SD_K

    @property
    def slope(self): return float(self.b[3])


GOV_POLL_FIT = False      # poll_errors() from poll_error_fit(): the Senate's values x the governor / Senate ratio measured on raw_polls
POLL_FIT_BEFORE = None    # gov_backtest's hook: measure on cycles before this year only (None = every cycle, the live value)
_PFIT = {}


def poll_error_fit(before=None):
    """Governor poll errors measured the way the Senate's were (538 raw_polls, even-year generals, polls in the last 60 days, races
    with >= 3 polls): within-race sd (pooled) and race-average systematic sd (variance of race means net of within / n), for Gov-G and
    Sen-G alike; returned as the Senate's POLL_SD / POLL_SYS times the governor / Senate ratio of each (the Senate constants stay the
    anchor, so only the measured DIFFERENCE between the offices enters)."""
    if before in _PFIT: return _PFIT[before]
    r = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "raw_polls.csv", low_memory=False)
    r = r[r.type_simple.isin(["Sen-G", "Gov-G"]) & (r.cycle % 2 == 0) & (r.time_to_election <= 60) & r.cand1_party.isin(["DEM", "REP"])
          & r.cand2_party.isin(["DEM", "REP"]) & (r.cand1_party != r.cand2_party)]
    if before is not None: r = r[r.cycle < before]
    r = r.assign(e=(r.margin_poll - r.margin_actual) * np.where(r.cand1_party == "DEM", 1, -1))
    r = r.groupby(["poll_id", "race_id"], as_index=False).agg(t=("type_simple", "first"), e=("e", "mean"))
    def split(d):
        n = d.groupby("race_id").e.size(); d = d[d.race_id.isin(n[n >= 3].index)]; g = d.groupby("race_id").e
        n, m, v = g.size(), g.mean(), g.var(ddof=1)
        return float(np.sqrt((v * (n - 1)).sum() / (n - 1).sum())), float(np.sqrt(max(m.var(ddof=1) - (v / n).mean(), 0.0)))
    (wg, sg), (ws, ss) = split(r[r.t == "Gov-G"]), split(r[r.t == "Sen-G"])
    _PFIT[before] = (POLL_SD * wg / ws, POLL_SYS * sg / ss)
    return _PFIT[before]


def poll_errors():
    """(per-poll sd, total race-average sd) for governor polls."""
    if GOV_POLL_FIT: return poll_error_fit(POLL_FIT_BEFORE)
    return (GOV_POLL_SD or POLL_SD, GOV_POLL_SYS or POLL_SYS)


def race_sys(b3):
    """The race's own systematic poll error = the measured total race-average miss minus the shared statewide shock the simulation
    draws (governors feel it at b3), as senate2026.race_sys (2026-10-03: it was counted twice)."""
    from . import senate2026 as SN
    return float(np.sqrt(max(poll_errors()[1] ** 2 - (SN.SEN_S_NAT * b3) ** 2, 1.5 ** 2)))


PULL_K = 0.0            # polls moved toward the state's non-national fundamentals (prior - b3 E) by PULL_K per point (symmetric)


def prepare(q):
    """Live race-poll corrections with the governor-specific undecided / third-candidate variants."""
    return M.prepare_race_polls(q, und_params=UND_PARAMS, third_mult=THIRD_MULT)


def blend(mu_prior, psd, qq, asof, b3, rsd=0.0):
    """THE governor race blend, shared by run() and gov_backtest: precision blend of the prior with the poll average (or the
    robust Student-t blend, model.ROBUST_NU). Returns (mu, sd, poll_margin, n_eff)."""
    psd_, sysr = float(psd), float(np.hypot(race_sys(b3), rsd)); sd_poll = poll_errors()[0]
    old = M.AGE_HALF
    if AGE_HALF is not None: M.AGE_HALF = AGE_HALF
    try:
        pa = M.poll_average(qq, asof) if qq is not None and len(qq) else pd.DataFrame()
        pm, ne, vm = (pa.poll_margin.iloc[0], pa.n_eff.iloc[0], float(pa.vmult.iloc[0])) if len(pa) else (np.nan, np.nan, 1.0)
        wp, wq = 1 / psd_ ** 2, (1.0 / (sd_poll ** 2 * vm / ne + sysr ** 2) if ne == ne else 0.0)
        mu = (wp * mu_prior + wq * (pm if pm == pm else 0)) / (wp + wq); sd = float(np.sqrt(1 / (wp + wq)))
        if M.ROBUST_NU and qq is not None and len(qq): mu, sd = M.robust_blend(mu_prior, psd_, qq, asof, sd_poll, sysr, M.ROBUST_NU)
    finally:
        M.AGE_HALF = old
    return float(mu), float(sd), pm, ne


def simulate(S, b3, n=20000, seed=13, nat_z=None):
    """Governor margins [n, races] on the shared national draw: senate2026.simulate_senate at the governors' own national slope
    (elast = b3 / NAT_SLOPE), x SHARED_K, with each race's sd x RACE_SD_K."""
    from . import senate2026 as SN
    return SN.simulate_senate(S.assign(elast=SHARED_K * b3 / SN.NAT_SLOPE, sd=S["sd"] * RACE_SD_K), n=n, seed=seed, nat_z=nat_z)


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
        if RC.applies("governor", st):
            # ranked choice (midterms/rcv.py, 2026-10-04): a two-name entry is a final-round version; a full field is converted with the
            # transfer rates measured on past tabulations - replaces the party sum below, which moved EVERY eliminated Republican's vote
            # to the Republican finalist (in past Alaska counts ~49 % went there, ~12 % to the other finalist, ~39 % exhausted)
            cv = RC.votehub_answers([(a.get("choice"), a.get("pct")) for a in p.get("answers") or []], lambda c: party.get(_sur(c)))
            if cv is None: continue
            d, r_, mg_, dn, rn, oth_, how, rsd = cv
            out.append({"seat": st, "pollster": str(p.get("pollster")) + {"DEM": " (D)", "REP": " (R)"}.get(p.get("partisan"), ""),
                        "start_date": p.get("start_date") or p["end_date"], "end_date": p["end_date"], "dem": d, "rep": r_, "margin": mg_,
                        "dem_name": dn, "rep_name": rn, "und": max(0.0, 100 - sum(float(a.get("pct") or 0) for a in p.get("answers") or [])),
                        "other": oth_, "n": p.get("sample_size"), "grade": 1.5, "rcv": how, "rcv_sd": rsd})
            continue
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
        df = RC.prefer_final(df, ["seat", "pbase", "start_date", "end_date"])    # ranked choice: a final round is not averaged with round one
        key = ["seat", "pbase", "start_date", "end_date", "dem_name", "rep_name"]
        agg = {c: "first" for c in df.columns if c not in key}; agg.update({k: "mean" for k in ("dem", "rep", "margin", "und", "other")}); agg["n"] = "max"
        if "rcv_sd" in df: agg["rcv_sd"] = "mean"
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


def prev_2026(R, H):
    """The previous governor race of each 2026 state (2022; 2024 for the two-year states, which history() does not hold yet - those
    get no previous race) as the prev_* / inc_same columns Prior.design reads."""
    last = H.sort_values("year").groupby("state").tail(1).set_index("state")
    R = R.copy()
    for k in ("margin", "E", "lean", "inc", "year", "dn", "rn"):
        R["prev_" + k] = [last[k].get(st) if st in last.index and (st not in TWO_YEAR or last.year.get(st) == 2024) and 2026 - last.year.get(st) <= 4 else np.nan
                          for st in R.state]
    w = np.where(R.prev_margin > 0, R.prev_dn, R.prev_rn)
    R["inc_same"] = [float(i != 0 and isinstance(x, str) and _surname(x) == _surname(g)) for i, x, g in zip(R.inc, w, R.governor)]
    return R


def _nominees(r, q):
    """(D, R) nominee: the ballot's only candidate of the party, else the newest poll's (Alaska's top-four ballot has several)."""
    bd = [c for c, pt in r.cands if pt.startswith(("Democratic", "DFL"))]; br = [c for c, pt in r.cands if pt == "Republican"]
    newest = q.sort_values("end_date") if len(q) else None
    dn = bd[0] if len(bd) == 1 else (newest.dem_name.iloc[-1] if newest is not None else (bd[0] if bd else None))
    rn = br[0] if len(br) == 1 else (newest.rep_name.iloc[-1] if newest is not None else (br[0] if br else None))
    return dn, rn


def run(E, asof=None, n=20000, seed=13, nat_z=None, refresh=True):
    from . import senate2026 as SN
    asof = pd.Timestamp(asof or pd.Timestamp.today().normalize())
    Hh = history(); PR = Prior(Hh); b = PR.b; b3 = PR.slope
    R = races(); lean = _pres_lean(); R["lean"] = [lean(2026, s) for s in R.state]
    R["inc"] = [(1.0 if ip == "D" else -1.0 if ip == "R" else 0.0) if run_ else 0.0 for ip, run_ in zip(R.inc_party, R.inc_running)]
    R["year"] = 2026; R["E"] = E; R = prev_2026(R, Hh)
    # a governor who succeeded mid-term and runs for the first time ("Incumbent nominated to full term": Rhoden, SD) is coded the way
    # history() codes the same case in the past when the prior has the successor term: inc 0 + succ +-1. Without it every incumbent
    # running is +-1 (the model before the 2026-10-05 review, whose fit had coded past successors as open seats)
    succ = R.status.astype(str).str.contains("full term", case=False).values
    R["inc_fix"] = R["inc"]; R["inc_old"] = np.where(succ, 0.0, R["inc"])
    if PR.spec.get("succ"): R["inc"] = R["inc_old"]
    P = race_polls(R, refresh)
    nom = {r.state: _nominees(r, P[P.seat == r.state]) for r in R.itertuples()}
    R["q_d"] = [experience(2026, st, nom[st][0]) for st in R.state]; R["q_r"] = [experience(2026, st, nom[st][1]) for st in R.state]
    R.loc[R.inc_fix > 0, "q_d"] = 1.0; R.loc[R.inc_fix < 0, "q_r"] = 1.0
    mu_f, sd_f = PR.predict(R); R["mu_fund"] = mu_f; R["prior_sd"] = sd_f
    # nominee = the highest-polling candidate of each party in the newest polls; the D-R poll margin is theirs
    corr = M.state_poll_correction(statewide=True); heat = M.heating_oil_shift("state"); G = SN.state_loadings()
    sd_poll = poll_errors()[0]
    rows = []
    for r in R.itertuples():
        q = P[P.seat == r.state].copy()
        # nominees from the BALLOT when it lists exactly one of each (2026-10-03; it used to be whoever the newest poll named), and
        # polls kept by SURNAME - the exact-string match dropped Maine's "Robert B. Charles" rows against VoteHub's "Bobby Charles"
        # and New Mexico's "Greg Hull" against "Gregg Hull"
        dn, rn = nom[r.state]
        if len(q) and r.state != "AK": q = q[(q.dem_name.map(_sur) == _sur(dn)) & (q.rep_name.map(_sur) == _sur(rn))]
        if len(q): q = PO.apply(q, r.state, "governor")       # exact figures for rounded scraped copies (manual exact=1)
        man = M.manual_race_polls(r.state, "governor", q if len(q) else None, names=(dn, rn))
        if RC.applies("governor", r.state): man = RC.convert_rows(man, RC.other_type_of(r.cands, (dn, rn)))
        if len(man): q = pd.concat([q, man], ignore_index=True)
        if len(q): q = PC.apply(q, r.state, "governor")        # known duplicates / sponsors (data/manual/poll_corrections.csv)
        if len(q): q = M.collapse_versions(q.assign(_race=r.state), "_race").drop(columns="_race")       # one survey, one row
        # Hispanic / Asian swing term (2026-10-03): the House and Senate carry it and the 2025 evidence for it is itself GOVERNOR
        # races (NJ, VA), yet the governor prior had no mean term while the simulation drew the group shocks. Scaled like the
        # Senate's (state_loadings is NAT_SLOPE-scaled): by the governors' own national slope b3.
        gscale = b3 / SN.NAT_SLOPE
        mu_prior = r.mu_fund + gscale * G.group_shift.get(r.state, 0.0)
        qq = prepare(q.assign(margin=q.margin - corr.get(r.state, 0.0) + PULL_K * (r.mu_fund - b3 * E))) if len(q) else q
        sub = M.substate_polls(r.state, "governor", sd_poll)
        if len(sub): qq = pd.concat([qq, sub.assign(margin=sub.margin - corr.get(r.state, 0.0))], ignore_index=True)
        rcv_on = RC.applies("governor", r.state); rsd = RC.race_sd(qq, asof) if rcv_on and len(qq) else 0.0   # ranked-choice transfer sd
        mu, sd_r, pm, ne = blend(mu_prior, r.prior_sd, qq, asof, b3, rsd)
        mu = mu + b3 * heat.get(r.state, 0.0)
        rows.append({"state": r.state, "governor": r.governor, "inc_party": r.inc_party, "inc": r.inc, "dem": dn, "rep": rn, "lean": round(r.lean, 1),
                     "n_polls": int(len(qq)) if len(qq) else 0, "poll_margin": pm, "mu_prior": mu_prior, "prior_sd": r.prior_sd, "mu": mu, "sd": sd_r,
                     "h_load": gscale * G.h_load.get(r.state, 0.0), "c_load": gscale * G.c_load.get(r.state, 0.0), "a_load": gscale * G.a_load.get(r.state, 0.0), "wnc_z": G.wnc_z.get(r.state, 0.0),
                     "group_shift": gscale * G.group_shift.get(r.state, 0.0),
                     "newest_poll": q.end_date.max().date().isoformat() if len(q) else None, "rcv": RC.NOTE if rcv_on else "", "rcv_sd": rsd})
    S = pd.DataFrame(rows)
    # governors ride the national tide at b3, not the Senate's 0.8: the shared-draw simulation is rescaled (gov2026.simulate)
    mg = simulate(S, b3, n=n, seed=seed, nat_z=nat_z); d = mg > 0
    S["p_dem"] = d.mean(0)
    up_d = int((S.inc_party == "D").sum()); dg = (NOW_D - up_d) + d.sum(1)
    summ = {"mean": round(float(dg.mean()), 1), "p10": int(np.percentile(dg, 10)), "p90": int(np.percentile(dg, 90)), "p_d_majority": round(float((dg >= 26).mean()), 3),
            "up_d": up_d, "up_r": int((S.inc_party == "R").sum()), "now_d": NOW_D, "now_r": NOW_R}
    S.attrs["summary"] = summ
    names = PR.names()
    coef = {k: float(v) for k, v in zip(names, np.round(b, 3))}
    coef["lean_now"] = round(coef["lean"] + coef.get("lean_t", 0.0) * (2026 - 2010) / 10, 3)     # the 2026 lean slope
    return S, mg, {"coef": coef, "prior_sd": round(float(np.mean(sd_f)), 2), "n_hist": len(Hh),
                   "poll_sd": poll_errors()[0], "poll_sys": poll_errors()[1]}


if __name__ == "__main__":
    Hh = history(); PR = Prior(Hh); b = PR.b
    print(f"governor prior on {len(Hh)} races (all, Wikipedia) 1998-{int(Hh.year.max())}: const {b[0]:+.2f}, lean {b[1]:.3f}, incumbency {b[2]:.2f}, national E {b[3]:.3f}, "
          f"extra {np.round(b[4:], 3).tolist()}, resid sd {PR.sd:.2f}; backtest: python -m midterms.gov_backtest")
