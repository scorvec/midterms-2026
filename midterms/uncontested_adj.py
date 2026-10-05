"""TEST ONLY (2026-10-05, not wired into the live model): the national House vote with uncontested seats imputed.

Question: the national-mood fit (national_mood.py) compares the generic-ballot average G with the RAW national House two-party
vote V. V is distorted by seats without a Democrat or without a Republican (2018: 37 D-only seats, D +5.8M votes there vs
0.5M for R), Florida's unopposed races (no vote at all) and same-party top-two generals. A generic-ballot poll asks every
voter about a D-v-R contest, so the natural target may be V_adj: the vote with every seat a two-party contest.

Build (GitHub Actions, .github/workflows/uncontested-data.yml; nothing is downloaded on a laptop):
  district results  1990-2016 MIT Election Data + Science Lab, U.S. House 1976-2018 (MEDSL GitHub mirror of the Harvard
                    Dataverse file doi:10.7910/DVN/IG0UN2, CC0; source: the House Clerk's election statistics)
                    2018-2022 FEC "Federal Elections" workbooks (public domain; data_prep.fec_house)
                    2024 the House Clerk's "Statistics of the Presidential and Congressional Election of November 5, 2024"
                    (public domain; FEC's 2024 workbook does not exist)
  imputation        every seat without a D-v-R contest (one major party only, FL unopposed with no vote, same-party top-two
                    or jungle generals, independent winners): margin from the seat's nearest contested result on the SAME
                    lines (pair regression with a cycle-pair intercept = the swing between the two cycles, spline in the old
                    margin, incumbency), else a safe-seat model (cycle intercept + winner-party offset, fitted on seats that
                    are uncontested in some other cycle of the same map); two-party turnout = the state's median contested
                    two-party turnout that cycle x the seat's relative turnout on the same lines (else 1), floored at the votes
                    actually recorded in the seat. Validated by masking contested seats (10-fold by seat).
  uncertainty       per-seat errors (the CV RMSE of each imputation kind, on the lopsided subset) + a shared per-cycle error
                    per kind, propagated by simulation (2000 draws).
Outputs (derived per-cycle tables only, data/static/uncontested/): national_vote_adj.csv, v_adj_draws.csv,
imputation_cv.csv, contested_fe.csv (two-way FE contested-seat intercepts), seat_swing.csv (D seats under a uniform shift
of the actual contested margins).
    python -m midterms.uncontested_adj fetch | build
"""
from __future__ import annotations

import io, json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

from . import data_prep as D
from .fetch import open_url, report

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "static" / "uncontested"
MEDSL = RAW / "mit" / "house_1976_2018.csv"
MEDSL_URL = "https://raw.githubusercontent.com/MEDSL/constituency-returns/master/1976-2018-house.csv"
CLERK24 = RAW / "clerk" / "statistics2024.pdf"
CLERK24_URL = "https://clerk.house.gov/member_info/electionInfo/2024/statistics2024.pdf"
YEARS = list(range(1990, 2025, 2))
NATIONAL_MAPS = (1972, 1982, 1992, 2002, 2012, 2022)
# first general election on new lines between the national redistrictings (court-ordered / mid-decade); a seat is the "same
# seat" across cycles only within one map era
REDRAW = {"GA": (1996, 2006, 2024), "LA": (1994, 1996, 2024), "TX": (1996, 2004, 2006), "FL": (1996, 2016), "NC": (1998, 2000, 2016, 2020, 2024),
          "VA": (1998, 2016), "NY": (1998, 2024), "PA": (2018,), "AL": (2024,), "MN": (), "OH": ()}
SEED = 20261005


def fetch():
    """Download what is missing (GitHub Actions only; cached between runs)."""
    from . import bootstrap as B
    for dest, url in ((MEDSL, MEDSL_URL), (CLERK24, CLERK24_URL)):
        if dest.exists(): continue
        b = open_url(url, timeout=300); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(b)
        print(f"  {dest.relative_to(ROOT)}: {len(b) / 1e6:.1f} MB")
    for dest, url, md5 in B.FILES:
        if dest.startswith("fec/") and not (RAW / dest).exists():
            b = B._get(url); (RAW / dest).parent.mkdir(parents=True, exist_ok=True); (RAW / dest).write_bytes(b); print(f"  {dest}")
    report()


# ------------------------------------------------------------------------------------------------ district results
def _pty(p):
    p = str(p).lower().strip()
    if p in ("nan", "", "none"): return "O"
    if "republican" in p and "democrat" not in p: return "R"
    if p.startswith("democrat") or p.startswith("democratic"): return "D"
    return "O"


def _last(n):
    n = re.sub(r"\(.*?\)|\".*?\"|“.*?”", " ", str(n)).replace(",", " ")
    w = [x for x in re.sub(r"[^A-Za-z' -]", " ", n).lower().split() if x not in ("jr", "sr", "ii", "iii", "iv", "jr.", "sr.")]
    return w[-1] if w else ""


def _seat_rows(cands, year, st, cd):
    """cands: DataFrame name, party (D/R/O), v -> one seat row."""
    c = cands.groupby("name", as_index=False).agg(v=("v", "sum"), party=("party", lambda s: "D" if (s == "D").any() else ("R" if (s == "R").any() else "O")))
    dv = float(c.loc[c.party == "D", "v"].sum()); rv = float(c.loc[c.party == "R", "v"].sum()); ov = float(c.loc[c.party == "O", "v"].sum())
    nd, nr = int((c.party == "D").sum()), int((c.party == "R").sum())
    w = c.sort_values("v", ascending=False).iloc[0] if len(c) else None
    return {"year": year, "state": st, "cd": int(cd), "seat": f"{st}-{int(cd)}", "dv": dv, "rv": rv, "ov": ov, "nd": nd, "nr": nr,
            "winner": None if w is None else w.party, "winner_name": None if w is None else w["name"],
            "d_names": "|".join(c.loc[c.party == "D", "name"]), "r_names": "|".join(c.loc[c.party == "R", "name"])}


def medsl(years):
    m = pd.read_csv(MEDSL, low_memory=False, encoding="latin-1")
    m = m[m.year.isin(years) & (m.stage == "gen") & ~m.special.astype(str).str.upper().eq("TRUE") & ~m.runoff.astype(str).str.upper().eq("TRUE")]
    m = m[~m.writein.astype(str).str.upper().eq("TRUE") & m.candidate.notna() & ~m.candidate.astype(str).str.contains("Blank|Scatter|Void|Over Vote|Under Vote", case=False)]
    m = m[~m.state_po.isin(["DC", "AS", "GU", "MP", "PR", "VI"])]
    # unopposed candidates carry 1 vote of a 1-vote total (the Clerk prints no count)
    m["v"] = np.where((m.candidatevotes <= 1) & (m.totalvotes <= 1), 0.0, m.candidatevotes.astype(float))
    m["party"] = m.party.map(_pty); m["name"] = m.candidate.astype(str).str.strip()
    m["cd"] = m.district.astype(int).replace(0, 1)
    rows = [_seat_rows(g[["name", "party", "v"]], y, st, cd) for (y, st, cd), g in m.groupby(["year", "state_po", "cd"])]
    return pd.DataFrame(rows)


def _fec_rows(year):
    """FEC workbook -> seat rows (candidate names kept, fusion lines combined as in data_prep.fec_house)."""
    x = pd.ExcelFile(RAW / "fec" / f"federalelections{year}.xlsx")
    sh = [s for s in x.sheet_names if "House Results" in s][0]; df = x.parse(sh); df.columns = [str(c).strip() for c in df.columns]
    st = df["STATE ABBREVIATION"].ffill(); draw = df["DISTRICT"].astype(str).str.strip()
    special = draw.str.contains("UNEXPIRED|SPECIAL", case=False, na=False) | df["CANDIDATE NAME"].astype(str).str.contains("UNEXPIRED", case=False)
    dist = pd.to_numeric(draw.str.extract(r"^(\d+)", expand=False), errors="coerce")
    votes = pd.to_numeric(df["GENERAL VOTES"] if "GENERAL VOTES" in df else df["GENERAL VOTES "], errors="coerce")
    comb_col = [c for c in df.columns if c.startswith("COMBINED GE PARTY TOTALS")]
    comb = pd.to_numeric(df[comb_col[0]], errors="coerce") if comb_col else pd.Series(np.nan, index=df.index)
    win = df["GE WINNER INDICATOR"].astype(str).str.strip().eq("W")
    name = df["CANDIDATE NAME"].astype(str)
    d = pd.DataFrame({"state": st, "district": dist, "party": df["PARTY"].map(D._party), "votes": votes.fillna(0.0), "comb": comb, "win": win,
                      "name": name, "special": special})
    d = d[d.district.notna() & ~d.special & ((d.votes > 0) | d.win) & ~d.name.str.contains(r"Votes:|Scattered|^nan$|Total|Blank|Over/Under", case=False)]
    d = d[~d.state.isin(["AS", "DC", "GU", "MP", "PR", "VI"])]
    d["cd"] = d.district.astype(int).replace(0, 1)
    rows = []
    for (s, k), g in d.groupby(["state", "cd"]):
        c = []
        for nm, gg in g.groupby("name"):
            cv = gg.comb.dropna().max() if gg.comb.notna().any() else np.nan
            c.append({"name": nm, "party": "D" if (gg.party == "D").any() else ("R" if (gg.party == "R").any() else "O"),
                      "v": float(cv if cv == cv else gg.votes.sum()), "win": bool(gg.win.any())})
        c = pd.DataFrame(c); r = _seat_rows(c[["name", "party", "v"]], year, s, k)
        if c.win.any():
            w = c[c.win].sort_values("v", ascending=False).iloc[0]; r["winner"], r["winner_name"] = w.party, w["name"]
        rows.append(r)
    return pd.DataFrame(rows)


_STNAME = {k.upper(): v for k, v in D._ST.items()}


def clerk_2024():
    """The Clerk's 2024 statistics PDF: per district, candidate label lines ('1. Name, Party ....') followed by their votes."""
    import fitz
    lines = []
    for p in fitz.open(CLERK24): lines += [l.strip() for l in p.get_text().split("\n")]
    rows, st, sect, dist, queue, odd = [], None, False, None, [], []
    for l in lines:
        if not l: continue
        u = re.sub(r"\s*—\s*CONTINUED.*$", "", l.upper()).strip()
        if u in _STNAME and l == l.upper(): st = _STNAME[u]; sect = False; queue = []; continue
        if l.startswith("FOR UNITED STATES REPRESENTATIVE"): sect = True; queue = []; continue
        if l.startswith("FOR ") or l.startswith("Recapitulation"): sect = False; queue = []; continue
        if not sect: continue
        if u.startswith("AT LARGE"): dist = 1; continue
        m = re.match(r"^(?:(\d+)\.\s+)?(.*?)\s*\.{3,}\s*(.*)$", l)
        if m:
            if m.group(1): dist = int(m.group(1)); queue = []
            tail = m.group(3).strip()
            if tail and re.fullmatch(r"[\d,]+", tail): rows.append({"state": st, "cd": dist, "label": m.group(2), "votes": int(tail.replace(",", ""))})
            elif re.search(r"unopposed", tail + m.group(2), re.I): rows.append({"state": st, "cd": dist, "label": m.group(2), "votes": 0})
            else: queue.append((dist, m.group(2)))
            continue
        if re.fullmatch(r"[\d,]+", l):
            if queue: d_, lab = queue.pop(0); rows.append({"state": st, "cd": d_, "label": lab, "votes": int(l.replace(",", ""))})
            continue
        if re.search(r"unopposed", l, re.I) and queue:
            d_, lab = queue.pop(0); rows.append({"state": st, "cd": d_, "label": lab, "votes": 0}); continue
        odd.append(f"{st} {dist}: {l[:90]}")
    print(f"  clerk 2024: {len(rows)} candidate lines; {len(odd)} unparsed lines in House sections, e.g.", odd[:25])
    c = pd.DataFrame(rows)
    c["name"] = c.label.str.split(",").str[0].str.strip()
    c["party"] = c.label.map(lambda s: _pty(s.split(",", 1)[1]) if "," in s else "O")
    c.loc[c.label.str.contains("Write-in|Scattering|Blank", case=False) & ~c.label.str.contains(",", regex=False), "party"] = "O"
    c["v"] = c.votes.astype(float)
    out = [_seat_rows(g[["name", "party", "v"]], 2024, s, k) for (s, k), g in c.groupby(["state", "cd"])]
    return pd.DataFrame(out)


def load():
    """Seat-year table 1990-2024 with era ids, incumbency (+1 D incumbent running, -1 R, 0 open) and contest flags."""
    h = pd.concat([medsl([y for y in YEARS if y <= 2016]), *[_fec_rows(y) for y in (2018, 2020, 2022)], clerk_2024()], ignore_index=True)
    n = h.groupby(["year", "state"]).seat.transform("size"); h["n_state"] = n
    def era(r):
        if r.n_state == 1 and not (r.state == "MT" and r.year >= 2022): return "AL"
        cands = [y for y in NATIONAL_MAPS if y <= r.year] + [y for y in REDRAW.get(r.state, ()) if y <= r.year]
        return str(max(cands))
    h["era"] = h.apply(era, axis=1); h["sk"] = h.seat + "@" + h.era
    # incumbency: a candidate whose last name matches the state's winner of the same party two years earlier
    prev = {(y, s): set() for y, s in zip(h.year, h.state)}
    for r in h.itertuples():
        if r.winner in ("D", "R") and r.winner_name: prev.setdefault((r.year + 2, r.state), set()).add((r.winner, _last(r.winner_name)))
    def inc(r):
        p = prev.get((r.year, r.state), set())
        di = any(("D", _last(x)) in p for x in str(r.d_names).split("|") if x); ri = any(("R", _last(x)) in p for x in str(r.r_names).split("|") if x)
        return 1 if di and not ri else (-1 if ri and not di else 0)
    h["inc"] = h.apply(inc, axis=1)
    h["contested"] = (h.dv > 0) & (h.rv > 0)
    h["m"] = np.where(h.contested, 100 * (h.dv - h.rv) / (h.dv + h.rv), np.nan)
    h["T"] = np.where(h.contested, h.dv + h.rv, np.nan)
    h["rec"] = h.dv + h.rv + h.ov
    def kind(r):
        if r.contested: return "contested"
        if r.dv > 0 and r.nd >= 1 and r.nr == 0 and r.nd >= 2: return "same-party D"
        if r.rv > 0 and r.nr >= 2 and r.nd == 0: return "same-party R"
        if r.dv > 0: return "D-only"
        if r.rv > 0: return "R-only"
        if r.rec == 0: return "no vote (unopposed)"
        return "no major party"
    h["kind"] = h.apply(kind, axis=1)
    return h.sort_values(["year", "state", "cd"]).reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ imputation
KNOT = 30.0


def _xpair(mp, g, inc, incp):
    """Regressors of the pair model (no intercept: the cycle-pair intercept is separate)."""
    g = np.abs(g); b2, b4 = (g == 2).astype(float), (g == 4).astype(float); b6 = 1.0 - b2 - b4
    hp, hm = np.maximum(mp - KNOT, 0), np.minimum(mp + KNOT, 0)
    return np.column_stack([mp * b2, mp * b4, mp * b6, hp, hm, inc, incp])


def pairs(h):
    """Every (seat-cycle, other contested cycle on the same lines) pair; the nearest one first."""
    c = h[h.contested][["sk", "year", "m", "inc", "T", "state"]]
    p = h[["sk", "year", "inc", "contested", "m", "state", "winner", "kind"]].merge(c.rename(columns={"year": "yp", "m": "mp", "inc": "incp", "T": "Tp", "state": "_s"}), on="sk")
    p = p[p.year != p.yp].copy(); p["g"] = p.yp - p.year; p["ag"] = p.g.abs()
    p = p[p.ag <= 8]
    p["rank"] = p.ag * 2 + (p.g > 0)          # nearest first; the earlier cycle before the later at equal distance
    return p.sort_values(["sk", "year", "rank"])


def fit_pair(P):
    """OLS of m on the pair regressors with one intercept per (cycle, other cycle). Returns (beta, {(y, yp): alpha}, resid sd)."""
    X = _xpair(P.mp.values, P.g.values, P.inc.values, P.incp.values); key = list(zip(P.year, P.yp))
    ks = sorted(set(key)); ix = {k: i for i, k in enumerate(ks)}; Dm = np.zeros((len(P), len(ks))); Dm[np.arange(len(P)), [ix[k] for k in key]] = 1
    b = np.linalg.lstsq(np.c_[X, Dm], P.m.values, rcond=None)[0]
    return b[:X.shape[1]], {k: b[X.shape[1] + i] for k, i in ix.items()}


def pred_pair(Q, beta, alpha):
    a = np.array([alpha.get((y, yp), np.nan) for y, yp in zip(Q.year, Q.yp)])
    return a + _xpair(Q.mp.values, Q.g.values, Q.inc.values, Q.incp.values) @ beta


def safe_set(h):
    """Seats (on fixed lines) that are uncontested in at least one cycle."""
    return set(h.loc[~h.contested, "sk"])


def fit_safe(S):
    """Safe-seat model: m = a_y + theta_D [winner D] + theta_R [winner R] + c inc (theta_R absorbs nothing - both kept, no global intercept)."""
    yrs = sorted(S.year.unique()); X = np.column_stack([(S.year == y).astype(float) for y in yrs] + [(S.winner == "R").astype(float), S.inc.values])
    b = np.linalg.lstsq(X, S.m.values, rcond=None)[0]
    return {"a": dict(zip(yrs, b[:len(yrs)])), "thR": b[len(yrs)], "c": b[len(yrs) + 1]}


def pred_safe(Q, F):
    return np.array([F["a"].get(y, np.nan) for y in Q.year]) + F["thR"] * (Q.winner == "R").values + F["c"] * Q.inc.values


def turnout_frame(h):
    """log state median contested two-party turnout per cycle, and each seat's relative turnout in its other contested cycles."""
    c = h[h.contested].copy(); c["lnT"] = np.log(c["T"])
    med = c.groupby(["year", "state"]).lnT.median(); nat = c.groupby("year").lnT.median()
    c["rel"] = c.lnT - [med[(y, s)] for y, s in zip(c.year, c.state)]
    st_rel = (med - nat.reindex(med.index.get_level_values(0)).values).groupby(level=1).mean()
    return c, med, nat, st_rel


def pred_turnout(Q, c, med, nat, st_rel, exclude_self=True):
    """log T for rows of Q (year, state, sk): state median (excluding the seat itself) + the seat's mean relative turnout in
    its other contested cycles on the same lines."""
    out = []
    cg = c.groupby(["year", "state"])
    rel_by = c.groupby("sk")
    for r in Q.itertuples():
        try:
            g = cg.get_group((r.year, r.state)); g = g[g.sk != r.sk] if exclude_self else g
        except KeyError: g = c.iloc[:0]
        base = g.lnT.median() if len(g) else nat.get(r.year, np.nan) + st_rel.get(r.state, 0.0)
        try:
            o = rel_by.get_group(r.sk); o = o[o.year != r.year]
            rel = o.rel.mean() if len(o) else 0.0
        except KeyError: rel = 0.0
        out.append(base + rel)
    return np.array(out)


def impute_point(h, folds=None):
    """Point imputation of every non-contested seat-year: kind ('pair' | 'safe'), margin, log turnout."""
    P = pairs(h); beta, alpha = fit_pair(P[P.contested])
    S = h[h.contested & h.sk.isin(safe_set(h))]; F = fit_safe(S)
    c, med, nat, st_rel = turnout_frame(h)
    U = h[~h.contested].copy()
    near = P[~P.contested].groupby(["sk", "year"]).head(1).set_index(["sk", "year"])
    U = U.join(near[["yp", "mp", "incp", "g"]], on=["sk", "year"])
    has = U.yp.notna() & np.array([(y, yp) in alpha for y, yp in zip(U.year, U.yp.fillna(0).astype(int))])
    U["how"] = np.where(has, "pair", "safe")
    U["m_imp"] = np.nan
    if has.any(): U.loc[has, "m_imp"] = pred_pair(U[has].assign(yp=U.yp[has].astype(int)), beta, alpha)
    U.loc[~has, "m_imp"] = pred_safe(U[~has], F)
    U["m_imp"] = U.m_imp.clip(-95, 95)
    U["lt_imp"] = pred_turnout(U, c, med, nat, st_rel, exclude_self=False)
    U["T_imp"] = np.maximum(np.exp(U.lt_imp), U.rec); U["floor"] = np.exp(U.lt_imp) < U.rec
    return U, (beta, alpha, F)


def cv(h, k=10):
    """Mask contested seats (10 folds by seat-on-lines), predict margin (pair model where the seat has another contested cycle on
    the same lines, else the safe-seat model) and turnout; report errors."""
    rng = np.random.default_rng(SEED); sks = h.sk.unique(); fold = dict(zip(sks, rng.integers(0, k, len(sks))))
    P = pairs(h); safe = safe_set(h); rows = []
    c, med, nat, st_rel = turnout_frame(h)
    pf = P.sk.map(fold).values; hf = h.sk.map(fold).values
    for f in range(k):
        beta, alpha = fit_pair(P[P.contested.values & (pf != f)])
        F = fit_safe(h[h.contested.values & (hf != f) & h.sk.isin(safe).values])
        T = h[h.contested.values & (hf == f)].copy()
        # the masked seat keeps its OTHER contested cycles as predictors, exactly like an uncontested seat
        near = P[P.contested.values & (pf == f)].groupby(["sk", "year"]).head(1).set_index(["sk", "year"])
        T = T.join(near[["yp", "mp", "incp", "g"]], on=["sk", "year"])
        hasp = T.yp.notna() & np.array([(y, yp) in alpha for y, yp in zip(T.year, T.yp.fillna(0).astype(int))])
        T["pred_pair"] = np.nan
        if hasp.any(): T.loc[hasp, "pred_pair"] = pred_pair(T[hasp].assign(yp=T.yp[hasp].astype(int)), beta, alpha)
        T["pred_safe"] = pred_safe(T, F)
        T["lt_pred"] = pred_turnout(T, c, med, nat, st_rel)
        rows.append(T)
    R = pd.concat(rows); R["lnT"] = np.log(R["T"]); R["safe"] = R.sk.isin(safe)
    R["lop"] = R.safe | (R.mp.abs() > 40)
    return R


def _rmse(x): x = np.asarray(x, float); x = x[np.isfinite(x)]; return float(np.sqrt(np.mean(x ** 2))) if len(x) else np.nan


def cv_summary(R):
    out = []
    def add(name, e, sub):
        e = np.asarray(e, float); ok = np.isfinite(e)
        if ok.sum() == 0: return
        cyc = pd.Series(e[ok]).groupby(R.year.values[ok]).mean()
        cn = pd.Series(e[ok]).groupby(R.year.values[ok]).size()
        sh2 = max(cyc.var(ddof=1) - np.mean(np.var(e[ok]) / cn), 0.0) if len(cyc) > 2 else np.nan
        out.append({"target": name, "subset": sub, "n": int(ok.sum()), "rmse": round(_rmse(e), 3), "bias": round(float(e[ok].mean()), 3),
                    "mae": round(float(np.abs(e[ok]).mean()), 3), "shared_sd": round(float(np.sqrt(sh2)), 3) if sh2 == sh2 else np.nan})
    for sub, msk in (("all contested", np.ones(len(R), bool)), ("lopsided (uncontested in another cycle on the same lines, or old margin > 40)", R.lop.values),
                     ("uncontested in another cycle on the same lines", R.safe.values)):
        for g in (2, 4, 6, 8):
            mm = msk & (R.g.abs() == g).values
            add(f"margin, pair model, nearest contested cycle {g} yr away", np.where(mm, R.m - R.pred_pair, np.nan), sub)
        add("margin, pair model, any distance", np.where(msk, R.m - R.pred_pair, np.nan), sub)
        add("margin, safe-seat model", np.where(msk, R.m - R.pred_safe, np.nan), sub)
        add("log two-party turnout", np.where(msk, R.lnT - R.lt_pred, np.nan), sub)
    return pd.DataFrame(out)


# ------------------------------------------------------------------------------------------------ national totals
def national(h, U, draws=2000):
    ref = pd.read_csv(ROOT / "data" / "static" / "house_national_vote_1946.csv").set_index("year")
    R = pd.DataFrame(index=sorted(h.year.unique()))
    g = h.groupby("year"); R["V_ref"] = ref.E.reindex(R.index)
    R["V_raw"] = 100 * (g.dv.sum() - g.rv.sum()) / (g.dv.sum() + g.rv.sum())
    C = h[h.contested].groupby("year"); R["V_contested"] = 100 * (C.dv.sum() - C.rv.sum()) / (C.dv.sum() + C.rv.sum())
    R["n_seats"] = g.size(); R["n_contested"] = C.size()
    for k in ("D-only", "R-only", "no vote (unopposed)", "same-party D", "same-party R", "no major party"):
        R[f"n_{k}"] = h[h.kind == k].groupby("year").size().reindex(R.index).fillna(0).astype(int)
    R["dv_uncontested_M"] = h[~h.contested].groupby("year").dv.sum() / 1e6; R["rv_uncontested_M"] = h[~h.contested].groupby("year").rv.sum() / 1e6
    U = U.copy(); U["Dimp"] = U.T_imp * (100 + U.m_imp) / 200; U["Rimp"] = U.T_imp * (100 - U.m_imp) / 200
    Ui = U.groupby("year")
    R["n_imputed_pair"] = U[U.how == "pair"].groupby("year").size().reindex(R.index).fillna(0).astype(int)
    R["n_imputed_safe"] = U[U.how == "safe"].groupby("year").size().reindex(R.index).fillna(0).astype(int)
    R["n_turnout_floor"] = U[U["floor"]].groupby("year").size().reindex(R.index).fillna(0).astype(int)
    R["D_imp_M"] = Ui.Dimp.sum() / 1e6; R["R_imp_M"] = Ui.Rimp.sum() / 1e6
    Dc, Rc = C.dv.sum(), C.rv.sum()
    R["V_adj"] = 100 * ((Dc + Ui.Dimp.sum()) - (Rc + Ui.Rimp.sum())) / (Dc + Rc + Ui.Dimp.sum() + Ui.Rimp.sum())
    return R


def simulate_vadj(h, U, S, draws=2000):
    """V_adj draws: per-seat margin error (CV RMSE of the kind used, lopsided subset) + one shared error per cycle and kind;
    log-turnout error per seat + shared."""
    rng = np.random.default_rng(SEED + 1)
    lop = S[S.subset.str.startswith("lopsided")].set_index("target")
    sd_pair = {g: lop.loc[f"margin, pair model, nearest contested cycle {g} yr away", "rmse"] if f"margin, pair model, nearest contested cycle {g} yr away" in lop.index else lop.loc["margin, pair model, any distance", "rmse"] for g in (2, 4, 6, 8)}
    sh_pair = float(np.nan_to_num(lop.loc["margin, pair model, any distance", "shared_sd"])); bias_pair = abs(lop.loc["margin, pair model, any distance", "bias"])
    sd_safe = lop.loc["margin, safe-seat model", "rmse"]; sh_safe = float(np.nan_to_num(lop.loc["margin, safe-seat model", "shared_sd"])); bias_safe = abs(lop.loc["margin, safe-seat model", "bias"])
    sd_t = lop.loc["log two-party turnout", "rmse"]; sh_t = float(np.nan_to_num(lop.loc["log two-party turnout", "shared_sd"]))
    # the shared error is at least the subset's mean CV error: imputed seats are lopsided, the CV seats were contested
    sh_pair, sh_safe = max(sh_pair, bias_pair), max(sh_safe, bias_safe)
    C = h[h.contested].groupby("year"); out = {}
    for y, u in U.groupby("year"):
        n = len(u); pair = (u.how == "pair").values
        sd = np.where(pair, [sd_pair.get(int(abs(g)) if g == g else 8, sd_pair[8]) for g in u.g.fillna(8)], sd_safe)
        e = rng.standard_normal((draws, n)) * sd + np.where(pair, rng.standard_normal((draws, 1)) * sh_pair, rng.standard_normal((draws, 1)) * sh_safe)
        m = np.clip(u.m_imp.values[None, :] + e, -98, 98)
        lt = u.lt_imp.values[None, :] + rng.standard_normal((draws, n)) * sd_t + rng.standard_normal((draws, 1)) * sh_t
        T = np.maximum(np.exp(lt), u.rec.values[None, :])
        Dm, Rm = (T * (100 + m) / 200).sum(1), (T * (100 - m) / 200).sum(1)
        Dc, Rc = C.dv.sum()[y], C.rv.sum()[y]
        out[y] = 100 * ((Dc + Dm) - (Rc + Rm)) / (Dc + Rc + Dm + Rm)
    params = {"sd_pair": sd_pair, "shared_pair": sh_pair, "sd_safe": sd_safe, "shared_safe": sh_safe, "sd_log_turnout": sd_t, "shared_log_turnout": sh_t}
    return pd.DataFrame(out), params


# ------------------------------------------------------------------------------------------------ contested-seat intercepts
def contested_fe(h, iters=200):
    """Two-way fixed effects on contested seats: m_iy = a_y + u_(seat on its lines) + c inc_iy + e. a_y is identified within a map
    era up to a constant (eras are linked only through at-large seats, so the level is compared WITHIN eras)."""
    c = h[h.contested].copy(); y = c.m.values.astype(float); inc = c.inc.values.astype(float)
    a = pd.Series(0.0, index=sorted(c.year.unique())); u = pd.Series(0.0, index=c.sk.unique()); b = 0.0
    for _ in range(iters):
        r = y - b * inc - u.reindex(c.sk).values; a = pd.Series(r).groupby(c.year.values).mean()
        r = y - b * inc - a.reindex(c.year).values; u = pd.Series(r).groupby(c.sk.values).mean()
        r = y - a.reindex(c.year).values - u.reindex(c.sk).values; b = float(inc @ r / (inc @ inc))
    res = y - a.reindex(c.year).values - u.reindex(c.sk).values - b * inc
    nseat = c.groupby("sk").size(); multi = c.sk.map(nseat).values > 1
    out = pd.DataFrame({"year": a.index, "a_fe": a.values})
    out["n_multi"] = pd.Series(multi).groupby(c.year.values).sum().reindex(a.index).values
    print(f"  contested FE: incumbency {b:.2f}, residual sd (seats seen twice+) {res[multi].std():.2f}")
    return out, b


def seat_swing(h, grid=np.round(np.arange(-25, 25.01, 0.1), 2)):
    """D seats when every contested margin moves by d (uniform swing from the ACTUAL result); uncontested seats stay with their
    winner. d = 0 reproduces the result (independent winners counted as not D)."""
    rows = []
    for y, g in h.groupby("year"):
        m = g.m.values[g.contested.values]; fixed = int(((~g.contested) & (g.winner == "D")).sum())
        rows.append(pd.DataFrame({"year": y, "d": grid, "dem_seats": fixed + (m[None, :] + grid[:, None] > 0).sum(1)}))
    return pd.concat(rows)


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    print("=====BEGIN")
    h = load()
    print(h.groupby("year").agg(seats=("seat", "size"), contested=("contested", "sum"), D=("winner", lambda w: (w == "D").sum())).T.to_string())
    print(pd.crosstab(h.year, h.kind).to_string())
    U, (beta, alpha, F) = impute_point(h)
    print("  pair beta (m' x gap2, gap4, gap6+, hinge+30, hinge-30, inc, inc'):", np.round(beta, 3), " safe:", {k: (round(v, 2) if not isinstance(v, dict) else None) for k, v in F.items()})
    R = cv(h); S = cv_summary(R); S.to_csv(OUT / "imputation_cv.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 80): print(S.to_string())
    N = national(h, U); Dr, params = simulate_vadj(h, U, S)
    N["V_adj_mean"] = Dr.mean().reindex(N.index); N["V_adj_sd"] = Dr.std().reindex(N.index)
    N["V_adj_p05"] = Dr.quantile(0.05).reindex(N.index); N["V_adj_p95"] = Dr.quantile(0.95).reindex(N.index)
    fe, b_inc = contested_fe(h); N = N.join(fe.set_index("year"))
    N["era"] = ["1992" if y < 2002 else "2002" if y < 2012 else "2012" if y < 2022 else "2022" for y in N.index]
    N.index.name = "year"; N.round(4).to_csv(OUT / "national_vote_adj.csv")
    Dr.round(3).to_csv(OUT / "v_adj_draws.csv", index=False)
    seat_swing(h).to_csv(OUT / "seat_swing.csv", index=False)
    (OUT / "params.json").write_text(json.dumps({"draw_errors": params, "pair_beta": [round(float(x), 4) for x in beta], "safe_thetaR": round(float(F["thR"]), 3),
                                                  "safe_inc": round(float(F["c"]), 3), "fe_inc": round(b_inc, 3), "knot": KNOT, "seed": SEED}, indent=1))
    with pd.option_context("display.width", 250): print(N.round(2).to_string())
    # spot checks
    print(U.sort_values("year")[["year", "seat", "kind", "how", "yp", "mp", "m_imp", "T_imp", "rec", "winner"]].tail(40).to_string())


def probe():
    h = load(); print("=====BEGIN"); print(h.groupby("year").agg(seats=("seat", "size"), contested=("contested", "sum")).T.to_string())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    {"fetch": fetch, "probe": probe, "build": build}[cmd]()
