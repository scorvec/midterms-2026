"""2026 inputs from Wikipedia: House seat table, Senate race table, House ratings, generic aggregates.
    python -m midterms.wiki_inputs      # re-fetch and rebuild data/cache/*.csv
"""
import io, re, numpy as np, pandas as pd
from pathlib import Path
from . import wiki_polls as W
from .data_prep import _ST

ROOT = Path(__file__).resolve().parents[1]; RAW = ROOT / "data" / "raw" / "wiki"; CACHE = ROOT / "data" / "cache"
HOUSE_PAGE = "2026 United States House of Representatives elections"; SENATE_PAGE = "2026 United States Senate elections"; HOUSE_RATINGS = "2026 United States House of Representatives election ratings"
EXPECTED = {"CA": 52, "TX": 38, "FL": 28, "NY": 26, "PA": 17, "IL": 17, "OH": 15, "GA": 14, "NC": 14, "MI": 13, "NJ": 12, "VA": 11, "WA": 10, "AZ": 9, "IN": 9, "MA": 9, "TN": 9, "MD": 8, "MN": 8, "MO": 8, "WI": 8, "CO": 8, "AL": 7, "SC": 7, "KY": 6, "LA": 6, "OR": 6, "CT": 5, "OK": 5, "AR": 4, "IA": 4, "KS": 4, "MS": 4, "NV": 4, "UT": 4, "NE": 3, "NM": 3, "HI": 2, "ID": 2, "ME": 2, "MT": 2, "NH": 2, "RI": 2, "WV": 2}

def _cols(t): return [" ".join(str(x) for x in c) if isinstance(c, tuple) else str(c) for c in t.columns]
def _clean(s): return re.sub(r"\[.*?\]", "", str(s)).strip()
def _pvi(s):
    s = _clean(s)
    if s.upper().startswith("EVEN"): return 0.0
    m = re.match(r"([DR])\+(\d+(?:\.\d+)?)", s); return (1 if m.group(1) == "D" else -1) * float(m.group(2)) if m else np.nan
def _seat(loc):
    m = re.match(r"^(.*?)\s+(\d+|at-large)", str(loc).strip()); return None if not m or not _ST.get(m.group(1)) else f"{_ST[m.group(1)]}-{1 if m.group(2) == 'at-large' else int(m.group(2))}"


def house_seats(max_age_h=24):
    html = W.fetch(HOUSE_PAGE, max_age_h); (RAW / "house2026.html").write_text(html); tables = pd.read_html(io.StringIO(html))
    rows = []
    for i, t in enumerate(tables):
        cols = _cols(t)
        if not any(c.startswith("District Location") for c in cols) or not any("Status" in c for c in cols): continue
        t.columns = cols; pc = [c for c in cols if "PVI" in c][0]
        for _, r in t.iterrows():
            rows.append({"loc": str(r[cols[0]]), "pvi": str(r[pc]), "member": str(r[[c for c in cols if "Member" in c][0]]), "party": str(r[[c for c in cols if "Party" in c][0]]), "status": str(r[[c for c in cols if "Status" in c][0]]), "cands": str(r[[c for c in cols if "Candidates" in c][0]])[:300]})
    df = pd.DataFrame(rows); df = df[df["loc"].notna() & (df["loc"] != "nan")].copy(); df["seat"] = df["loc"].map(_seat); df = df[df["seat"].notna()]
    df["cook_pvi"] = df["pvi"].map(_pvi); st = df["status"].map(_clean)
    running = st.str.match(r"^Incumbent (renominated|advanced to general|running|re-elected)")
    party = df["party"].map(_clean).map(lambda p: "D" if (p.startswith("Democratic") or p.startswith("DFL")) else ("R" if p.startswith("Republican") else "O"))
    df["inc_running_party"] = party.where(running, None); df["redistricted_in"] = df["member"].str.contains("Redistricted from", na=False)
    def pick(g):
        r = g[g["inc_running_party"].notna()]
        if len(r) == 1: return r.iloc[0]
        if len(r) > 1: return r[~r["redistricted_in"]].iloc[0] if (~r["redistricted_in"]).any() else r.iloc[0]
        return g.iloc[0]
    seats = df.groupby("seat", sort=False).apply(pick, include_groups=False).reset_index()
    seats["open"] = seats["inc_running_party"].isna(); seats["inc"] = seats["inc_running_party"].map({"D": 1, "R": -1}).fillna(0).astype(int)
    seats["member"] = seats["member"].str.replace(r"\s*Redistricted from.*$", "", regex=True).str.replace(r"\[.*?\]", "", regex=True)
    bad = {k: v for k, v in seats.groupby(seats["seat"].str[:2]).size().items() if v != EXPECTED.get(k, 1)}
    assert len(seats) == 435 and not bad, f"seat table off: {len(seats)} seats, mismatches {bad}"
    seats["lean"] = 2.0 * seats["cook_pvi"]                                       # Cook share points -> margin units
    seats.to_csv(CACHE / "house2026_seats.csv", index=False); return seats


def generic_aggregates(html=None):
    html = html or (RAW / "house2026.html").read_text(); out = []
    for t in pd.read_html(io.StringIO(html)):
        cols = _cols(t)
        if any("aggregation" in c for c in cols):
            t.columns = cols
            for _, r in t.iterrows():
                m = re.search(r"(Democrats|Republicans)\s*\+\s*([\d.]+)", str(r[[c for c in cols if "Margin" in c][0]]))
                src = _clean(r[cols[0]])
                if re.search(r"realclear", src, re.I): continue      # RealClearPolling is not used anywhere in this model
                if m: out.append({"source": src, "margin": (1 if m.group(1).startswith("D") else -1) * float(m.group(2)), "updated": _clean(r[[c for c in cols if "updated" in c][0]])})
    return out


def house_ratings(max_age_h=24):
    html = W.fetch(HOUSE_RATINGS, max_age_h); (RAW / "house2026_ratings.html").write_text(html)
    r = [t for t in pd.read_html(io.StringIO(html)) if len(t) > 30][0]; r.columns = _cols(r); r["seat"] = r[r.columns[0]].map(_seat)
    rc = {c: c.split(" ")[1] for c in r.columns if c.startswith("Ratings ")}
    def score(v):
        v = str(v).lower()
        if "toss" in v: return 0.5
        d = 1 if re.search(r"\bd\b|dem", v) else (-1 if re.search(r"\br\b|rep", v) else 0)
        return 0.5 + d * (0.47 if ("safe" in v or "solid" in v) else 0.35 if "likely" in v else 0.2 if "lean" in v else 0.1 if "tilt" in v else 0.0)
    for c, nm in rc.items(): r[nm] = r[c].map(score)
    r["rating_mean"] = r[list(rc.values())].mean(1); out = r[["seat", "rating_mean"] + list(rc.values())].dropna(subset=["seat"])
    out.to_csv(CACHE / "house2026_ratings.csv", index=False); return out


def senate_races(max_age_h=24):
    html = W.fetch(SENATE_PAGE, max_age_h); (RAW / "senate2026.html").write_text(html); tables = pd.read_html(io.StringIO(html))
    rat = [t for t in tables if 25 <= len(t) <= 45 and any("Incumbent" in c for c in _cols(t)) and sum("Ratings" in c for c in _cols(t)) >= 5][0]; rat.columns = _cols(rat)
    rac = [t for t in tables if 25 <= len(t) <= 45 and any("Candidates" in c for c in _cols(t))][0]; rac.columns = _cols(rac)
    rat.to_csv(RAW / "senate2026_ratings_table.csv", index=False); rac.to_csv(RAW / "senate2026_races.csv", index=False)
    rows = []
    for _, r in rat.iterrows():
        stn = re.sub(r"\s*\(.*?\)", "", str(r[rat.columns[0]])).strip(); st = _ST.get(stn)
        if not st: continue
        inc = _clean(r[rat.columns[2]]); pv = _pvi(r[rat.columns[1]])
        rows.append({"state": st, "special": "special" in str(r[rat.columns[0]]).lower(), "incumbent": inc, "inc_retiring": bool(re.search(r"retiring|lost renomination|resign|appointed", inc, re.I)), "cook_pvi": pv, "lean": 2 * pv if pv == pv else np.nan,
                     **{f"rat_{c.split(' ')[1]}": str(r[c]) for c in rat.columns if c.startswith("Ratings")}})
    S = pd.DataFrame(rows); S.to_csv(CACHE / "senate2026_races.csv", index=False); return S


if __name__ == "__main__":
    s = house_seats(0); print("house seats", len(s), "open", int(s["open"].sum()))
    print("generic aggregates", generic_aggregates())
    r = house_ratings(0); print("ratings", len(r))
    S = senate_races(0); print("senate races", len(S))
