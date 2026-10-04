"""Pollster quality ratings (2026-09-29, user: "Big Data is a very low rated pollster" -> "We need to make sure our quality
screen is good").

One quality SCORE per pollster on 538's star scale (0.5 = F ... 3.0 = A+), kept separate from the partisan tag:

  538 archived ratings (github.com/fivethirtyeight/data, pollster-ratings/, public), one file per vintage, each used ONLY for
  elections after its release (no hindsight):
      2016  (June 2016)   -> 2016 cycle          2021  (Mar 2021)   -> 2022 cycle
      2018  (May 2018)    -> 2018 cycle          combined (Jan 2024, 3-star scale, polls to 2023-09) -> 2024 and 2026
      2020  (spring 2020, polls to 2020-03) -> 2020 cycle
    Letter grades -> stars with LETTER below (A+ 3.0 ... F 0.5; the provisional "A/B", "B/C", "C/D" grades of pollsters with
    few polls are the midpoint of the pair); a pollster "Banned by 538" scores 0.5 (F).
  pollresults.org grades: the letter printed after the pollster on its Bluesky posts ("Big Data Poll (F)",
    "Quinnipiac University (B+)"), harvested from the feed into data/cache/pollster_grades_pollresults.csv (not committed).
    Only the DERIVED per-pollster score (538 star scale, the most recent grade) is kept in the repository:
    data/state/pollster_scores.csv (key, score). Its race pages carry an empty grade column.
  These ratings are RECORDED, not used: no quality weighting or exclusion is switched on (generic.QUALITY_GAMMA /
  QUALITY_EXCLUDE_BELOW are None; the 2026-09-29 backtests found no significant gain).
  No other free source: Silver Bulletin's ratings are paywalled (not scraped); 538's site is gone (archived files only).

Names: a 2026 poll's pollster is mapped through names.canon to a canonical key; the key is joined to 538's pollster_rating_id
via the pollresults.org API (it carries 538's rating id per poll), the 538 poll archives and the rating files themselves.
Live score = the pollresults.org grade where it has one (current), else the 538 Jan-2024 stars, else unrated.

    python -m midterms.pollster_quality        -> tables + 2026 coverage
"""
from __future__ import annotations
import re, json
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]; RAT = ROOT / "data" / "raw" / "ratings"; CACHE = ROOT / "data" / "cache"
PR_GRADES = CACHE / "pollster_grades_pollresults.csv"          # raw harvest (git-ignored)
PR_SCORES = ROOT / "data" / "state" / "pollster_scores.csv"      # derived: canonical key -> score (committed)

LETTER = {"A+": 3.0, "A": 2.8, "A-": 2.6, "B+": 2.4, "B": 2.2, "B-": 2.0, "C+": 1.8, "C": 1.6, "C-": 1.4,
          "D+": 1.2, "D": 1.0, "D-": 0.8, "F": 0.5}
VINTAGE_FILE = {"2016": "r2016.csv", "2018": "r2018.csv", "2019": "r2019.csv", "2020": "r2020.csv", "2021": "r2021.csv",
                "2023": "r2023.csv", "2024": "combined.csv"}
VINTAGE_FOR = {2016: "2016", 2018: "2018", 2020: "2020", 2022: "2021", 2024: "2024", 2026: "2024"}


def fetch_sources():
    """Re-download the rating sources into data/raw/ratings/ (gitignored): 538's archived vintages and the pollresults.org
    API's pollster -> 538 rating id table (CC BY 4.0, paged newest first)."""
    import urllib.request, urllib.parse, time
    ua = {"User-Agent": "scorvec.com midterm model (+https://scorvec.com/midterms/about.html)"}; (RAT / "538").mkdir(parents=True, exist_ok=True)
    base = "https://raw.githubusercontent.com/fivethirtyeight/data/master/pollster-ratings/"
    for v, f in VINTAGE_FILE.items():
        src = base + ("pollster-ratings-combined.csv" if v == "2024" else f"{v}/pollster-ratings.csv")
        (RAT / "538" / f).write_bytes(urllib.request.urlopen(urllib.request.Request(src, headers=ua), timeout=60).read())
    rows, cur = [], None
    for _ in range(200):
        q = {"limit": 100, **({"cursor": cur} if cur else {})}
        d = json.loads(urllib.request.urlopen(urllib.request.Request("https://api.pollresults.org/v1.0/politics/results/all?" + urllib.parse.urlencode(q), headers=ua), timeout=60).read())
        rows += [{k: r.get(k) for k in ("pollster", "displayName", "pollsterRatingId", "pollsterRatingName", "cycle", "endDate", "type")} for r in d["results"]]
        cur = d.get("cursor")
        if not cur or not d["results"]: break
        time.sleep(0.3)
    (RAT / "pollresults_api_pollsters.json").write_text(json.dumps(rows))


def letter_score(g) -> float:
    if g is None or (isinstance(g, float) and np.isnan(g)): return np.nan
    g = str(g).replace("°", "").strip()
    if "/" in g:
        a, b = g.split("/", 1); return (LETTER.get(a, np.nan) + LETTER.get(b, np.nan)) / 2
    return LETTER.get(g, np.nan)


def norm(s) -> str:
    s = str(s).lower().replace("&amp;", "&")
    s = re.sub(r"\(.*?\)", " ", s); s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(w for w in s.split() if w not in ("the", "inc", "llc", "co"))


_NAME2ID = None


def name_to_id() -> dict:
    """normalised pollster name -> 538 pollster_rating_id, from every 538 file that carries both, plus the pollresults API."""
    global _NAME2ID
    if _NAME2ID is not None: return _NAME2ID
    m = {}
    def add(name, rid):
        if pd.isna(rid) or not str(name).strip() or str(name) == "nan": return
        try: rid = int(float(rid))
        except ValueError: return
        m.setdefault(norm(name), rid)
    for f in sorted((RAT / "538").glob("r20*.csv")):
        d = pd.read_csv(f, encoding="utf-8-sig"); c = [x for x in d.columns if x.lower().replace(" ", "_") == "pollster_rating_id"]
        if c:
            for a, b in zip(d["Pollster"], d[c[0]]): add(a, b)
    d = pd.read_csv(RAT / "538" / "combined.csv")
    for a, b in zip(d["pollster"], d["pollster_rating_id"]): add(a, b)
    for f in ("generic_ballot_polls_historical", "house_polls_historical", "senate_polls_historical", "governor_polls_historical",
              "president_approval_polls_historical"):
        fp = ROOT / "data" / "raw" / "538" / f"{f}.csv"
        if not fp.exists(): continue
        d = pd.read_csv(fp, low_memory=False, usecols=lambda c: c in ("pollster", "display_name", "pollster_rating_id", "pollster_rating_name"))
        for col in ("pollster_rating_name", "display_name", "pollster"):
            if col in d:
                for a, b in d[[col, "pollster_rating_id"]].drop_duplicates().itertuples(index=False): add(a, b)
    r = ROOT / "data" / "raw" / "538repo" / "raw_polls.csv"
    if r.exists():
        d = pd.read_csv(r, low_memory=False, usecols=["pollster", "pollster_rating_id"]).drop_duplicates()
        for a, b in d.itertuples(index=False): add(a, b)
    api = RAT / "pollresults_api_pollsters.json"
    if api.exists():
        for x in json.loads(api.read_text()):
            for col in ("pollster", "displayName", "pollsterRatingName"): add(x.get(col), x.get("pollsterRatingId"))
    _NAME2ID = m; return m


_VINT = {}


def vintage(v: str) -> pd.DataFrame:
    """One 538 rating vintage -> DataFrame(rid, name, grade, score, banned, bias). bias = 538's mean-reverted bias, D positive."""
    if v in _VINT: return _VINT[v]
    d = pd.read_csv(RAT / "538" / VINTAGE_FILE[v], encoding="utf-8-sig"); n2i = name_to_id()
    if v == "2024":
        out = pd.DataFrame({"rid": d["pollster_rating_id"], "name": d["pollster"], "grade": d["numeric_grade"].astype(str),
                            "score": d["numeric_grade"].astype(float), "banned": False, "bias": -d["bias_ppm"].astype(float)})
    else:
        name = d["Pollster"]; rid = d["Pollster Rating ID"] if "Pollster Rating ID" in d else name.map(lambda x: n2i.get(norm(x)))
        ban = d[[c for c in d.columns if c.startswith("Banned")][0]].astype(str).str.lower().eq("yes")
        g = d["538 Grade"]; sc = g.map(letter_score).where(~ban, 0.5)
        b = d["Mean-Reverted Bias"]
        if b.dtype == object:                         # "R +1.5" / "D +0.3"
            b = b.astype(str).map(lambda s: (1 if s.startswith("D") else -1) * float(re.sub(r"[^\d.]", "", s) or 0) if s[:1] in "DR" else np.nan)
        out = pd.DataFrame({"rid": rid, "name": name, "grade": g.astype(str), "score": sc, "banned": ban, "bias": b.astype(float)})
    out["key"] = out["name"].map(norm); _VINT[v] = out
    return out


def score_538(v: str, rid=None, name=None) -> float:
    t = vintage(v)
    if rid is not None and not pd.isna(rid):
        h = t[t["rid"] == int(rid)]
        if len(h): return float(h["score"].iloc[0])
    if name is not None:
        k = norm(name); h = t[t["key"] == k]
        if len(h): return float(h["score"].iloc[0])
        rid2 = name_to_id().get(k)
        if rid2 is not None:
            h = t[t["rid"] == rid2]
            if len(h): return float(h["score"].iloc[0])
    return np.nan


# ---- pollresults.org grades ---------------------------------------------------------------------------------------------
_GRADE_LINE = re.compile(r"^(.+?)\s*\(([A-F][+-]?(?:/[A-F][+-]?)?)\)\s*$")


def update_pollresults_grades(days=800, max_pages=100):
    """Harvest '<Pollster> (<grade>)' lines from the pollresults.org Bluesky posts into PR_GRADES (appends; newest grade wins)."""
    from . import bluesky_polls as B, names as R
    posts = B.fetch(days, max_pages=max_pages, actor=B.PR_ACTOR); rows = []
    for p in posts:
        L = [l.strip() for l in p.get("text", "").split("\n")]
        for i, l in enumerate(L):
            m = _GRADE_LINE.match(l)
            if not m or re.search(r"\d%", l) or l.startswith(("Partisan pollster", "Sponsor:")): continue
            if i + 1 < len(L) and not re.match(r"\d[\d,]*\s*(LV|RV|A|V)\b", L[i + 1]): continue     # the pollster line precedes "n POP"
            rows.append({"pollster": m.group(1).strip(), "key": R.canon(m.group(1)), "grade": m.group(2), "posted": p["created"][:10]})
    N = pd.DataFrame(rows, columns=["pollster", "key", "grade", "posted"])
    N = N.groupby(["pollster", "grade"], as_index=False).agg(key=("key", "first"), first=("posted", "min"), last=("posted", "max"), posts=("posted", "size"))
    if PR_GRADES.exists():                       # the feed only reaches back so far: keep what earlier runs saw
        N = pd.concat([pd.read_csv(PR_GRADES, dtype=str), N.astype({"posts": str})], ignore_index=True)
        N = N.assign(posts=N["posts"].astype(int)).groupby(["pollster", "grade"], as_index=False).agg(key=("key", "first"), first=("first", "min"), last=("last", "max"), posts=("posts", "max"))
    if len(N): N.to_csv(PR_GRADES, index=False); write_scores()
    return N


def write_scores():
    """data/state/pollster_scores.csv: canonical key -> score of the most recently posted grade, merged into what is there."""
    from . import names as R
    cur = dict(pd.read_csv(PR_SCORES).itertuples(index=False, name=None)) if PR_SCORES.exists() else {}
    if PR_GRADES.exists():
        d = pd.read_csv(PR_GRADES).sort_values("last")
        for r in d.itertuples():
            for k in {r.key, R.canon(r.pollster)}: cur[k] = letter_score(r.grade)
    PR_SCORES.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sorted(cur.items()), columns=["key", "score"]).to_csv(PR_SCORES, index=False)


def pollresults_grades() -> dict:
    """canonical key -> (grade, score), the most recently posted grade per key: the committed scores, overlaid by the raw
    harvest when it is present (grade None where only the derived score is known)."""
    from . import names as R
    out = {}
    if PR_SCORES.exists():
        for k, v in pd.read_csv(PR_SCORES).itertuples(index=False, name=None): out[k] = (None, float(v))
    if PR_GRADES.exists():
        d = pd.read_csv(PR_GRADES).sort_values("last")
        for r in d.itertuples():
            for k in {r.key, R.canon(r.pollster)}: out[k] = (r.grade, letter_score(r.grade))
    return out


# ---- live (2026) ---------------------------------------------------------------------------------------------------------
_LIVE = None
# media sponsors that print their name in place of the pollster's (only unambiguous, long-standing partnerships)
SPONSOR_ALIAS = {"fox news": "beacon research/shaw & co. research", "nbc": "hart research associates/public opinion strategies",
                 "pew research": "pew research center", "yougov/cbs": "yougov", "yougov/yahoo": "yougov", "ft/yougov": "yougov"}


def live_rating(pollster: str) -> dict:
    """{'score', 'grade', 'source'} for a 2026 poll's pollster string (sponsor tags like ' (R)' ignored)."""
    global _LIVE
    from . import names as R
    if _LIVE is None: _LIVE = (pollresults_grades(), {})
    pr, memo = _LIVE
    s = re.sub(r"\s*\((?:R|D|I)\)\s*$", "", str(pollster)).strip()
    if s in memo: return memo[s]
    k = R.canon(s); out = {"score": np.nan, "grade": None, "source": None}
    cands = [k, norm(s), R.canon(norm(s))]
    cands += [SPONSOR_ALIAS[c] for c in cands if c in SPONSOR_ALIAS]
    cands += [R.canon(x) for x in re.split(r"\s*/\s*", k) if "/" in k and x]          # "ft/yougov" -> yougov
    for c in cands:
        if c in pr: out = {"score": pr[c][1], "grade": pr[c][0], "source": "pollresults"}; break
    if out["source"] is None:
        for c in [s] + cands:
            sc = score_538("2024", name=c)
            if not np.isnan(sc): out = {"score": sc, "grade": f"{sc:.1f}*", "source": "538-2024"}; break
    memo[s] = out
    return out


# ---- weighting ------------------------------------------------------------------------------------------------------------
def weight(score, gamma: float, unrated: float = 1.5, floor_score: float = 0.5):
    """Quality weight (score / 3) ** gamma; unrated pollsters take the score `unrated`. gamma 0 = no weighting."""
    s = np.where(np.isnan(np.asarray(score, float)), unrated, np.asarray(score, float))
    return (np.clip(s, floor_score, 3.0) / 3.0) ** gamma


def coverage() -> pd.DataFrame:
    """Rating of every pollster in today's generic, approval and race polls (2026) -> data/cache/pollster_quality_2026.csv."""
    from . import generic as G
    rows = []
    for kind in ("generic", "approval"):
        f = CACHE / ("generic_polls.csv" if kind == "generic" else "approval_polls_votehub.csv")
        if not f.exists(): continue
        d = pd.read_csv(f, parse_dates=["end_date"]); d = d[d["end_date"] >= "2026-01-01"]
        rows += [{"kind": kind, "pollster": p} for p in d["pollster"]]
    pj = ROOT / "web" / "data" / "polls.json"
    if pj.exists():
        for r in json.loads(pj.read_text())["races"]:
            rows += [{"kind": "race-" + r["office"], "pollster": x["pollster"]} for x in r["polls"] if "[" not in x["pollster"]]
    d = pd.DataFrame(rows)
    if d.empty: return d
    rt = {p: live_rating(p) for p in d["pollster"].unique()}
    d["score"] = d["pollster"].map(lambda p: rt[p]["score"]); d["grade"] = d["pollster"].map(lambda p: rt[p]["grade"]); d["source"] = d["pollster"].map(lambda p: rt[p]["source"])
    t = d.groupby(["pollster", "grade", "source", "score"], dropna=False).agg(polls=("kind", "size"), kinds=("kind", lambda x: ",".join(sorted(set(x))))).reset_index()
    t.sort_values(["score", "polls"], ascending=[True, False]).to_csv(CACHE / "pollster_quality_2026.csv", index=False)
    return d


def main():
    for v in VINTAGE_FILE:
        t = vintage(v); print(v, len(t), "rated;", int(t["rid"].notna().sum()), "with id; score quartiles", t["score"].quantile([.1, .25, .5, .75]).round(2).tolist(),
                             "| Big Data", t[t["key"].str.contains("big data")]["grade"].tolist(), "| Rasmussen", t[t["key"].str.contains("rasmussen")]["grade"].tolist())
    d = coverage()
    if len(d):
        cov = d.groupby("kind").agg(polls=("score", "size"), rated=("score", lambda x: int(x.notna().sum())))
        cov["share"] = (cov["rated"] / cov["polls"]).round(3); print(cov.to_string())
        print("by source:", d["source"].fillna("unrated").value_counts().to_dict())
        low = d[d["score"] <= 1.2].groupby(["pollster", "grade"]).size(); print("D/F-rated pollsters in 2026 polls:", low.to_dict())


if __name__ == "__main__":
    main()
