"""Reconstructed forecast history (2026-09-30, user: "The history lines for the national probabilities are misleading since
we've changed the model methodology many times. Let's backfill the model with what it would have suggested before.")

    python -m midterms.backfill --rebuild            # every date from START to today, current code (~N min, see CLAUDE.md)
    python -m midterms.backfill --dates 2026-09-16,2026-09-30
    python -m midterms.backfill --check              # today's reconstruction vs web/data/model.json (must match)
    python -m midterms.backfill --status             # is the stored backfill built with the current code?
    python -m midterms.backfill --ensure             # daily run: rebuild only if the methodology changed

For a past date D the CURRENT model (House, Senate, governors, same constants, error sizes, BIAS, POLL_SYS, seeds) is run
with only the information released by D:
  * polls - generic ballot and race polls whose RELEASE date is <= D (the rule is `release()` below); the generic trend and
    its pollster house effects are refitted on that set, E_now = trend at its last poll + BIAS, as build_web does;
  * poll ages, recency and staleness are measured from D (asof = D, as the live code does from today);
  * FEC money: reports with coverage through June 30 (the live cut) that the FEC had RECEIVED by D (latest version of each
    report filed by D); a candidate with no report received by D is missing (no money term), not $0;
  * pollster experience (model.pollster_experience): VoteHub polls created by D;
  * heating-oil adjustment: the EIA weekly Maine residential heating-oil price as published on or before D
    (model.heat_retail(asof=D); outside the October-March survey season the last week is held).
Not as of D (hindsight, documented in CLAUDE.md): nominees, ballots and incumbents' retirement status are TODAY's (the race
tables are only kept in their current version); poll selection by on-ballot candidates therefore keeps pre-primary polls of
the eventual matchup; pollster tags borrowed from feed copies; the 538-based calibrations (fixed, fitted on past cycles anyway).
Special-election results are not a model input, so they need no as-of treatment.

Release date of a poll (the sources give field END dates):
  1. the earliest RECORDED appearance of the same poll (same race, same pollster by model._same_pollster, end within 3 days):
     Polling USA / pollresults.org Bluesky post dates, VoteHub created_at, the first published web/data/polls.json that lists
     it, the day it was added to data/manual/*.csv (both from the ledger, ledger.py). A source's FIRST version (the first
     polls.json) is not a record - everything already in it only proves "by then" -
     and caps the estimate instead;
     A record more than RECORD_MAX_LAG (14) days after the end date does not count: VoteHub adds older polls in catalogue
     batches (us-senator 2026-05-20: 27 polls, median 180 days after their end dates; governor 2026-04-27: 76 polls; the
     earliest us-senator created_at is 2026-01-22, so every 2025 Senate poll would otherwise have been invisible in January),
     while its same-week entries have p90 9-18 days since June;
  2. otherwise end date + LAG_DAYS (3): the median end-to-release lag in every source that records both (Polling USA 3, the
     pollresults.org bot 3, VoteHub generic / approval / Senate polls since June 3, Bluesky generic posts 2; measured 2026-09-30).
  Release is never before the end date.
Results: data/cache/backfill/<date>.json (compact, see _pack) + data/cache/backfill/meta.json (method stamp); race_history.py
reads them.
"""
from __future__ import annotations
import glob, hashlib, json, os, re, sys, time, datetime as dt
from functools import lru_cache
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "backfill"
START = "2026-01-05"          # first reconstructed date (a Monday); weekly Mondays until DAILY_FROM, daily from there
DAILY_FROM = "2026-06-01"
LAG_DAYS = 3
RECORD_MAX_LAG = 14           # a record more than 14 days after the end date is catalogue backfill, not the release (see docstring)
METHOD_FILES = ["model.py", "run2026.py", "senate2026.py", "gov2026.py", "generic.py", "money.py", "build_web.py", "wiki_polls.py",
                "race_poll_calibration.py", "backfill.py", "national_mood.py", "heating_oil.py", "names.py", "rcv.py"]
METHOD_DATA = ["data/static/national_mood_fit.json", "data/static/rcv_rates.json"]      # fitted inputs whose change is a methodology change too (2026-10-03): the
                                                        # national-mood fit is deterministic from this table + national_mood.py


# ------------------------------------------------------------------------------------------------ release dates
@lru_cache(maxsize=1)
def race_records():
    """DataFrame office, seat, pollster, end, rec (record date) and cap (upper bound from a source's first version)."""
    from .data_prep import _ST
    rows = []
    b = pd.read_csv(ROOT / "data" / "state" / "bluesky_race_polls.csv", parse_dates=["end_date"])
    b = b[b["src"].isin(["usapolling", "pollresults"])]           # pollresults_site rows carry no post date (posted = end)
    rows += [{"office": o, "seat": s, "pollster": p, "end": e, "rec": pd.Timestamp(d), "kind": "bluesky"}
             for o, s, p, e, d in zip(b.office, b.seat, b.pollster, b.end_date, b.posted)]
    for f, office in (("us-senator", "senate"), ("governor", "governor"), ("us-representative", "house")):
        for p in json.loads((ROOT / "data" / "raw" / "votehub" / f"{f}.json").read_text()):
            if not p.get("created_at") or not p.get("end_date"): continue
            if office == "house":
                m = re.match(r"([A-Z]{2})-(\d+)", str(p.get("seat_name") or ""))
                if not m: continue
                seat = f"{m.group(1)}-{int(m.group(2))}"
            else:
                m = re.match(r"2026 (.+)$", str(p.get("subject") or "")); seat = _ST.get(m.group(1).strip()) if m else None
                if not seat: continue
            rows.append({"office": office, "seat": seat, "pollster": str(p.get("pollster")), "end": pd.Timestamp(p["end_date"]),
                         "rec": pd.Timestamp(p["created_at"]), "kind": "votehub"})
    # first appearances in the published poll list (since 2026-09-25) and hand-entered rows: the ledger (ledger.py)
    from . import ledger as LG
    L = LG.load()
    rows += [{"office": o, "seat": se, "pollster": p, "end": pd.Timestamp(e), "rec": pd.Timestamp(r), "kind": k}
             for o, se, p, e, r, k in zip(L.office, L.seat, L.pollster, L.end, L.rec, L.kind)]
    R = pd.DataFrame(rows); R["end"] = pd.to_datetime(R["end"]).dt.normalize(); R["rec"] = pd.to_datetime(R["rec"]).dt.normalize()
    return R


@lru_cache(maxsize=None)
def _race_index():
    R = race_records(); return {k: g for k, g in R.groupby(["office", "seat"])}


_REL = {}


def release(office, seat, pollster, end):
    """Release date (Timestamp) of one race poll; see the module docstring."""
    end = pd.Timestamp(end).normalize(); key = (office, str(seat), str(pollster), end)
    if key in _REL: return _REL[key]
    from .model import _same_pollster
    est = end + pd.Timedelta(days=LAG_DAYS)
    g = _race_index().get((office, str(seat)))
    rec = cap = None
    if g is not None:
        c = g[(g["end"] - end).abs() <= pd.Timedelta(days=3)]
        if len(c):
            c = c[c["pollster"].map(lambda o: _same_pollster(str(pollster), o)).astype(bool).values]
            real = c[~c["kind"].str.endswith(":first") & ((c["rec"] - end).dt.days <= RECORD_MAX_LAG)]; first = c[c["kind"].str.endswith(":first")]
            if len(real): rec = real["rec"].min()
            if len(first): cap = first["rec"].min()
    r = rec if rec is not None else (min(est, cap) if cap is not None else est)
    _REL[key] = max(r, end); return _REL[key]


@lru_cache(maxsize=1)
def generic_frame():
    """The merged generic-ballot poll set of the latest live run (generic.main writes it) with a release column."""
    from . import names as R
    G = pd.read_csv(ROOT / "data" / "cache" / "generic_polls.csv", parse_dates=["end_date", "start_date"])
    G["partisan"] = G["partisan"].where(G["partisan"].notna(), None)
    recs = []                                                                     # key, end, rec, first(bool)
    for p in json.loads((ROOT / "data" / "raw" / "votehub" / "generic-ballot.json").read_text()):
        if p.get("created_at"): recs.append((R.canon(p["pollster"]), pd.Timestamp(p["end_date"]), pd.Timestamp(p["created_at"]), False))
    bs = pd.read_csv(ROOT / "data" / "state" / "bluesky_generic.csv", parse_dates=["date", "posted"])
    recs += [(R.canon(p), e, pd.Timestamp(d).normalize(), False) for p, e, d in zip(bs.pollster, bs.date, bs.posted)]
    pr = pd.read_csv(ROOT / "data" / "state" / "pollresults_generic.csv", parse_dates=["end_date", "posted"])
    recs += [(R.canon(p), e, pd.Timestamp(d).normalize(), False) for p, e, d in zip(pr.pollster, pr.end_date, pr.posted)]
    T = pd.DataFrame(recs, columns=["key", "end", "rec", "first"]); T["end"] = T["end"].dt.normalize()
    idx = {k: g for k, g in T.groupby("key")}
    rel = []
    for k, e in zip(G["pollster"], G["end_date"].dt.normalize()):
        g = idx.get(k); est = e + pd.Timedelta(days=LAG_DAYS); r = est
        if g is not None:
            c = g[(g["end"] - e).abs() <= pd.Timedelta(days=2)]
            real, first = c[~c["first"] & ((c["rec"] - e).dt.days <= RECORD_MAX_LAG)], c[c["first"]]
            if len(real): r = real["rec"].min()
            elif len(first): r = min(est, first["rec"].min())
        rel.append(max(r, e))
    G["release"] = rel
    return G


def generic_E(D):
    """(E_now, trend now, last poll end) from the generic polls released by D - build_web's formula."""
    from . import generic as GN, build_web as B
    G = generic_frame(); g = G[G["release"] <= pd.Timestamp(D)].drop(columns=["release"]).reset_index(drop=True)
    F = GN.fit(g); now = round(GN.trend_at(F, F["t1"]), 2)
    from . import national_mood as NMOOD                 # 2026-10-03: the fitted lead-dependent correction, as build_web
    return round(now + NMOOD.mapping(D, approval_asof(D))["c"], 2), now, F["t1"].date().isoformat()


@lru_cache(maxsize=None)
def approval_asof(D):
    """Our approval trend as of D (polls ending RELEASE_LAG days before D, as the generic): what national_mood's approval mode
    reads instead of today's value, so a reconstructed date never sees later approval polls. None when the mode is off."""
    from . import national_mood as NMOOD, generic as GN
    if not NMOOD.APPROVAL: return None
    A = pd.read_csv(ROOT / "data" / "cache" / "approval_polls_votehub.csv", parse_dates=["end_date", "start_date"])
    A["partisan"] = A["partisan"].where(A["partisan"].notna(), None)
    a = A[A["end_date"] <= pd.Timestamp(D) - pd.Timedelta(days=LAG_DAYS)].reset_index(drop=True)
    if len(a) < 20: return None
    F = GN.fit(a); return round(float(GN.trend_at(F, F["t1"])), 2)


@lru_cache(maxsize=1)
def bias():
    import inspect
    from . import build_web as B
    return float(re.search(r"\n\s*BIAS = (-?[\d.]+)", inspect.getsource(B.main)).group(1))


# ------------------------------------------------------------------------------------------------ money as of D
@lru_cache(maxsize=1)
def _report_table():
    """data/static/fec_reports_2026.csv (fec_money.compact): every report with coverage through June 30, by candidate."""
    t = pd.read_csv(ROOT / "data" / "static" / "fec_reports_2026.csv", dtype={"chain0": str, "file_number": str})
    return {c: g.to_dict("records") for c, g in t.groupby("cand_id")}


def _reports(cid):
    return _report_table().get(cid)


def money_asof(cid, D, cut="2026-06-30"):
    """(individual contributions, self-funding) through `cut` in reports RECEIVED by D (each report's latest version filed
    by D). None if the candidate has no report received by D."""
    reps = _reports(str(cid).strip()) if isinstance(cid, str) else None
    if not reps: return None
    D = str(pd.Timestamp(D).date()); best = {}
    for r in reps:
        if str(r.get("coverage_end_date", ""))[:10] > cut or str(r.get("receipt_date", ""))[:10] > D: continue
        k = (r.get("committee_id"), str(r.get("chain0")))
        if k not in best or str(r.get("receipt_date")) > str(best[k].get("receipt_date")): best[k] = r
    if not best: return None
    f = lambda k: sum(float(0 if pd.isna(r.get(k)) else r.get(k) or 0) for r in best.values())
    return f("total_individual_contributions_period"), f("candidate_contribution_period") + f("loans_made_by_candidate_period")


def seat_money_asof(D):
    """money.seat_money(2026) with the money as of D."""
    from . import money as MON
    j = pd.read_csv(ROOT / "data" / "static" / "fec_june30_2026.csv")
    ind, slf = [], []
    for cid, iv, sv in zip(j["cand_id"], j["indiv_jun30"], j["self_jun30"]):
        if pd.isna(iv): ind.append(np.nan); slf.append(np.nan); continue      # live: unmatched / late entrant -> missing
        m = money_asof(cid, D)
        ind.append(np.nan if m is None else m[0]); slf.append(np.nan if m is None else m[1])
    j["indiv_jun30"], j["self_jun30"] = ind, slf
    j["money"] = j["indiv_jun30"] + j["self_jun30"].fillna(0.0)
    p = j.pivot_table(index="seat", columns="party", values="money", aggfunc=lambda v: v.sum(min_count=1), dropna=False).dropna(subset=["D", "R"])
    return pd.DataFrame({"seat": p.index, "lr": MON._lr(p["D"].values, p["R"].values), "money_d": p["D"].values, "money_r": p["R"].values})


def ind_money_asof(D):
    m = pd.read_csv(ROOT / "data" / "static" / "fec_senate_june30_2026.csv")
    def f(state):
        q = m[(m.state == state) & (~m.special.astype(bool))]
        def one(side):
            r = q[q.side == side]
            if not len(r) or pd.isna(r.money.iloc[0]): return float("nan")
            v = money_asof(r.cand_id.iloc[0], D); return float("nan") if v is None else v[0] + v[1]
        return one("C"), one("R")
    return f


# ------------------------------------------------------------------------------------------------ pollster experience as of D
def experience_asof(D):
    """model._EXP with VoteHub polls created by D (538's counts are fixed)."""
    import collections
    from . import model as M
    full, first = collections.Counter(), collections.Counter()
    r = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "pollster-ratings-combined.csv")
    import re
    for n, k in zip(r.pollster, r.number_polls_pollster_total.fillna(0)):
        for nn in [n] + re.findall(r"\(([^)]+)\)", str(n)):          # as model.pollster_experience: bracketed brands, _xnorm keys
            full[M._xnorm(nn)] += int(k); w = M._first_word(M._xnorm(nn))
            if w: first[w] = max(first[w], int(k))
    D = str(pd.Timestamp(D).date())
    for f in ("generic-ballot", "approval", "us-senator", "us-representative", "governor"):
        fp = ROOT / "data" / "raw" / "votehub" / f"{f}.json"
        if not fp.exists(): continue
        for p in _votehub(str(fp)):
            if str(p.get("created_at") or p.get("end_date") or "")[:10] > D: continue
            n = M._xnorm(p.get("pollster")); full[n] += 1; w = M._first_word(n)
            if w: first[w] += 1
    return (full, first)


@lru_cache(maxsize=None)
def _votehub(path):
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------------------------------------------ the as-of run
class _State:
    D = None; office = None


def _filter(polls):
    if _State.D is None or polls is None or not len(polls) or "seat" not in polls or "end_date" not in polls: return polls
    rel = np.array([release(_State.office, s, p, e) for s, p, e in zip(polls["seat"], polls["pollster"], polls["end_date"])], dtype="datetime64[ns]")
    return polls[rel <= np.datetime64(_State.D)]


def _memo_df(fn):
    cache = {}
    def w(*a, **k):
        key = repr((a, sorted(k.items())))
        if key not in cache: cache[key] = fn(*a, **k)
        v = cache[key]
        if isinstance(v, tuple): return tuple(x.copy() if hasattr(x, "copy") else x for x in v)
        return v.copy() if hasattr(v, "copy") else v
    return w


_PATCHED = False


def _install():
    """Wrap the live model's choke points once: poll filters by release date, memoised loaders (they do not depend on D)."""
    global _PATCHED
    if _PATCHED: return
    from . import model as M, run2026 as H, senate2026 as SN, gov2026 as GV, wiki_polls as W, money as MON
    orig_fetch = W.fetch
    W.fetch = lambda title, max_age_h=None: orig_fetch(title, max_age_h=10 ** 9)      # cache only: never re-download mid-backfill
    pa, rb = M.poll_average, M.robust_blend
    M.poll_average = lambda polls, asof, *a, **k: pa(_filter(polls), asof, *a, **k)
    M.robust_blend = lambda mu_prior, prior_sd, polls, asof, *a, **k: rb(mu_prior, prior_sd, _filter(polls), asof, *a, **k)
    from . import rcv as RC
    rs = RC.race_sd; RC.race_sd = lambda polls, *a, **k: rs(_filter(polls), *a, **k)     # ranked-choice transfer sd: released polls only
    W.senate_polls = _memo_df(W.senate_polls); W.governor_polls = _memo_df(W.governor_polls)
    GV.race_polls = _memo_df(GV.race_polls); GV.history = _memo_df(GV.history); GV.races = _memo_df(GV.races)
    SN.races = _memo_df(SN.races); SN.state_loadings = _memo_df(SN.state_loadings)
    H.load_seats = _memo_df(H.load_seats); M.fit_prior = _memo_df(M.fit_prior)
    sc = _memo_df(M.state_poll_correction); M.state_poll_correction = lambda *a, **k: dict(sc(*a, **k))
    _PATCHED = True


def run_asof(D, n=20000, filtered=True, verbose=False, E_override=None):
    """The current model as of D -> compact result dict (_pack). E_override: run at that national environment instead of
    the as-of generic trend + BIAS (the validation's decomposition)."""
    from . import model as M, run2026 as H, senate2026 as SN, gov2026 as GV, money as MON
    _install()
    D = pd.Timestamp(D).normalize(); t0 = time.time()
    _State.D = D if filtered else None
    M._EXP = experience_asof(D) if filtered else None
    M.HEAT_RETAIL = M.heat_retail(asof=D) if filtered else None; M.HEAT_LIVE = not filtered   # EIA price as of D
    seat_money, ind_money = MON.seat_money, SN.ind_money
    if filtered:
        sm = seat_money_asof(D); MON.seat_money = lambda year, _sm=sm, _o=seat_money: _sm.copy() if year == 2026 else _o(year)
        SN.ind_money = ind_money_asof(D)
    try:
        E_now, gnow, glast = generic_E(D if filtered else pd.Timestamp("2100-01-01"))
        if E_override is not None: E_now = float(E_override)
        from . import national_mood as NMOOD
        mood = NMOOD.apply(D, approval_asof(D))          # the errors at D's lead (SEN_S_NAT set for the statewide races)
        Z, ZS = NMOOD.draws(20000)
        _State.office = "house"; s, mg, S = H.run(E_now, P=M.Params(s_nat=mood["s_house"]), n=n, nat_z=Z, asof=D)
        _State.office = "senate"; SS, smg, sout = SN.run(E_now, asof=D, nat_z=ZS)
        _State.office = "governor"; GS, gmg, ginfo = GV.run(E_now, asof=D, nat_z=ZS, refresh=False)
    finally:
        MON.seat_money, SN.ind_money = seat_money, ind_money; M.HEAT_RETAIL = None; M.HEAT_LIVE = True; _State.D = None; M._EXP = None
    out = _pack(D, E_now, gnow, glast, s, mg, SS, smg, sout, GS, gmg)
    out["secs"] = round(time.time() - t0, 1)
    if verbose: print(f"{D.date()}  E {E_now:+.2f}  House {out['nat']['h_mean']} p_maj {out['nat']['h']:.3f}  Senate p_ctrl {out['nat']['s']:.3f}  gov {out['nat']['g_mean']}  ({out['secs']} s)", flush=True)
    return out


def _pack(D, E_now, gnow, glast, s, mg, SS, smg, sout, GS, gmg):
    from . import senate2026 as SN, gov2026 as GV
    dn = (mg > 0).sum(1)
    d_up = sout["d_up"]; seats, ind = SN.seat_counts(SS, smg > 0, d_up)
    up_d = GS.attrs["summary"]["up_d"]; dg = (GV.NOW_D - up_d) + (gmg > 0).sum(1)
    hp, sp, gp = (mg > 0).mean(0), (smg > 0).mean(0), (gmg > 0).mean(0)
    dh = dn >= 218; dsn = SN.d_controls(seats, ind)
    return {"date": str(D.date()), "E": E_now, "generic_now": gnow, "generic_last": glast,
            "nat": {"h": round(float(dh.mean()), 4), "h_mean": round(float(dn.mean()), 2), "s": round(float(dsn.mean()), 4),
                    "s51": round(float((seats >= 51).mean()), 4), "s_mean": round(float(seats.mean()), 2), "g_mean": round(float(dg.mean()), 2),
                    "both": round(float((dh & dsn).mean()), 4)},
            "house": {r: [round(float(p), 4), round(float(m), 2), int(0 if pd.isna(k) else k)] for r, p, m, k in zip(s["seat"], hp, s["mu"], s.get("n", pd.Series([0] * len(s))))},
            "senate": {st + ("|sp" if sp_ else ""): [round(float(p), 4), round(float(m), 2), int(k), cp or "D"]
                       for st, sp_, p, m, k, cp in zip(SS["state"], SS["special"], sp, SS["mu"], SS["n_polls"], SS["challenger_party"])},
            "gov": {st: [round(float(p), 4), round(float(m), 2)] for st, p, m in zip(GS["state"], gp, GS["mu"])}}   # (gov2026's n_polls counts every poll, not those released by D)


def _init():
    pass


# ------------------------------------------------------------------------------------------------ method stamp / storage
def method_stamp():
    h = hashlib.sha1()
    for f in METHOD_FILES: h.update((ROOT / "midterms" / f).read_bytes())
    for f in METHOD_DATA:
        if (ROOT / f).exists(): h.update((ROOT / f).read_bytes())
    return {"code_sha1": h.hexdigest()[:12], "git_head": (os.environ.get("GITHUB_SHA") or "")[:7] or None,
            "files": METHOD_FILES, "lag_days": LAG_DAYS}


def dates(start=START, end=None):
    end = pd.Timestamp(end or dt.date.today()); daily = pd.Timestamp(DAILY_FROM)
    wk = list(pd.date_range(start, min(daily - pd.Timedelta(days=1), end), freq="7D"))
    return [x.date().isoformat() for x in wk + list(pd.date_range(max(daily, pd.Timestamp(start)), end, freq="D"))]


def build(ds, rebuild=False):
    _init(); OUT.mkdir(parents=True, exist_ok=True)
    stamp = method_stamp(); meta_f = OUT / "meta.json"
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    if rebuild or meta.get("code_sha1") != stamp["code_sha1"]:
        for f in OUT.glob("20*.json"): f.unlink()
        meta = dict(stamp, built=None, dates=[])
    t0 = time.time()
    for d in ds:
        r = run_asof(d, verbose=True); (OUT / f"{d}.json").write_text(json.dumps(r, separators=(",", ":")))
        meta["dates"] = sorted(set(meta.get("dates", [])) | {d})
        meta.update(stamp, built=dt.datetime.now().isoformat(timespec="seconds")); meta_f.write_text(json.dumps(meta, indent=1))
    print(f"backfill: {len(ds)} dates in {time.time() - t0:.0f} s -> {OUT}")


def ensure_current():
    """Daily run: rebuild every date when the methodology (METHOD_FILES) changed since the stored backfill; otherwise leave
    it alone - race_history appends the day's as-issued point after the backfill's last date."""
    mf = OUT / "meta.json"; meta = json.loads(mf.read_text()) if mf.exists() else {}
    if meta.get("code_sha1") == method_stamp()["code_sha1"] and meta.get("dates"):
        print(f"backfill current ({meta['code_sha1']}, {len(meta['dates'])} dates to {max(meta['dates'])})"); return False
    print(f"backfill: methodology changed ({meta.get('code_sha1')} -> {method_stamp()['code_sha1']}), rebuilding"); build(dates(), rebuild=True); return True


def check():
    """Today's reconstruction against the published web/data/model.json."""
    _init()
    m = json.loads((ROOT / "web" / "data" / "model.json").read_text()); D = m["asof"]
    r = run_asof(D, verbose=True)
    gi = m["house"]["grid"].index(m["house"]["E0"]); k = m["house"]["keys"][gi]
    print(f"E_now live {m['house']['E_now']} / reconstructed {r['E']}")
    print(f"House p_maj live {m['house']['dist'][k]['p_maj']} / {r['nat']['h']:.3f}; mean {m['house']['dist'][k]['mean']} / {r['nat']['h_mean']}")
    print(f"Senate p_ctrl live {m['senate']['dist'][k]['p_ctrl']} / {r['nat']['s']:.3f}; governors {m['governor']['now']['mean']} / {r['nat']['g_mean']}")
    worst = []
    for sec, rows, key in (("house", m["house"]["seats"], lambda x: x["seat"]), ("senate", m["senate"]["races"], lambda x: x["state"] + ("|sp" if x.get("special") else "")),
                           ("gov", m["governor"]["races"], lambda x: x["state"])):
        for x in rows:
            a = x["p"][gi]; b = r[sec][key(x)][0]; worst.append((abs(a - b), sec, key(x), a, b))
    worst.sort(reverse=True); print("largest race differences (|dp|, section, race, live, reconstructed):", [(round(w[0], 3),) + w[1:] for w in worst[:8]])
    return r


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--check" in a: check()
    elif "--ensure" in a: ensure_current()
    elif "--status" in a:
        mf = OUT / "meta.json"; meta = json.loads(mf.read_text()) if mf.exists() else {}
        st = method_stamp(); print("backfill", "CURRENT" if meta.get("code_sha1") == st["code_sha1"] else "STALE (methodology changed: run --rebuild)", meta.get("code_sha1"), st["code_sha1"], len(meta.get("dates", [])), "dates")
    elif "--dates" in a:
        build(a[a.index("--dates") + 1].split(","), rebuild="--rebuild" in a)
    else:
        build(dates(), rebuild="--rebuild" in a)
