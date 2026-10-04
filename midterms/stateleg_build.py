"""One-time data build for the state-legislative chamber forecasts (runs in GitHub Actions; .github/workflows/stateleg-data.yml).

Downloads (cached in data/raw/stateleg, never committed) and the compact tables derived from them (data/static/stateleg,
committed):

  MIT Election Data and Science Lab precinct returns (Harvard Dataverse, CC0) - the per-state files of the same releases as
    State Precinct-Level Returns 2024 (doi:10.7910/DVN/DODOBJ) + U.S. President 2024 (XDJYKC) = "Precinct-Level Returns 2024 by
    Individual State" (NYTPDU); 2020 by state (NT66Z3, = OKL2K1 + president); 2022 state offices (OAARCY, one national file,
    filtered here); 2018 by state (NVQYMG, = ZFXEJU + federal offices; New Hampshire only).
  Klarner, State Legislative Election Returns 1967-2022 (Dataverse doi:10.7910/DVN/FJOGJB, CC0).
  VEST precinct results with shapes 2016 (doi:10.7910/DVN/NH5S2I) and 2020 (K7760H) (CC BY 4.0).
  Census TIGER/Line state legislative districts SLDU/SLDL 2018, 2022, 2024, 2025 and the 2020 cartographic county file
    (public domain).
  OpenStates people (github.com/openstates/people, CC0): the sitting legislators.

Outputs (data/static/stateleg):
  lean_2026.csv      district presidential votes on the CURRENT maps: 2024 president joined to the same precinct's 2024
                     state House / Senate district labels (MEDSL), with nesting (MN Senate = two House districts, WI Senate =
                     three Assembly districts) and, where no 2024 label exists (MI Senate, PA odd Senate seats), VEST 2020
                     precinct shapes areally interpolated into the TIGER 2025 districts + the 2020->2024 county swing.
  lean_hist.csv      previous-presidential votes on the maps used in 2018 (2016 president) and 2022 (2020 president):
                     VEST shapes x TIGER districts; New Hampshire by town name (MEDSL labels x VEST town votes).
  medsl_results.csv.gz   candidate-level state-legislative general results 2018 (NH), 2020, 2022, 2024 for the states.
  klarner.csv.gz     Klarner returns aggregated to district-elections, all states 1972-2022.
  openstates_current.csv  sitting legislators (state, chamber, district, name, party).
  qc.json            coverage and cross-check statistics.
    python -m midterms.stateleg_build
"""
from __future__ import annotations

import gzip, io, json, re, subprocess, sys, zipfile
from pathlib import Path
import numpy as np, pandas as pd
from .fetch import open_url, report
from .paths import RAW, STATIC

R = RAW / "stateleg"; OUT = STATIC / "stateleg"
STATES = ["MI", "MN", "WI", "AZ", "PA", "NH", "NC", "GA", "IA", "TX"]
FIPS = {"MI": "26", "MN": "27", "WI": "55", "AZ": "04", "PA": "42", "NH": "33", "NC": "37", "GA": "13", "IA": "19", "TX": "48"}
DV = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/{}"
SETS = {"m2024": "NYTPDU", "m2020": "NT66Z3", "m2018": "NVQYMG", "m2022": "OAARCY", "vest2016": "NH5S2I", "vest2020": "K7760H", "klarner": "FJOGJB"}
QC: dict = {}
_LIST: dict = {}


def dv_list(key):
    if key not in _LIST:
        f = R / "dv" / f"{key}_files.json"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(open_url(DV.format(SETS[key])))
        j = json.loads(f.read_text()); _LIST[key] = [(x["dataFile"]["id"], x["dataFile"]["filename"]) for x in j["data"]["latestVersion"]["files"]]
    return _LIST[key]


def dv_get(key, pattern):
    """Download (once) the first file of dataset `key` whose name matches the regex `pattern`."""
    hit = [(i, n) for i, n in dv_list(key) if re.fullmatch(pattern, n, re.I)]
    if not hit: return None
    fid, name = hit[0]; p = R / "dv" / key / name.replace(".tab", ".csv")
    if not p.exists() and not list(p.parent.glob(p.stem + ".*.slim.parquet")):
        p.parent.mkdir(parents=True, exist_ok=True); print(f"  download {key}/{name}")
        stream(f"https://dataverse.harvard.edu/api/access/datafile/{fid}?format=original", p)
    return p


def stream(url, dest: Path):
    """Large download straight to disk (counted like fetch.open_url)."""
    import shutil, urllib.request
    from .fetch import UA, count
    tmp = dest.with_name(dest.name + ".part")
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=600) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 22)
            break
        except Exception as e:
            if "format=original" in url and "400" in str(e):         # files Dataverse never ingested have no "original" form
                url = url.replace("?format=original", ""); continue
            if k == 3: raise
            print(f"  retry {url[:90]} ({str(e)[:60]})"); import time; time.sleep(20 * (k + 1))
    tmp.rename(dest); count(url, dest.stat().st_size)


def url_get(url, name):
    p = R / "web" / name
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True); print(f"  download {url}"); stream(url, p)
    return p


# ---------------------------------------------------------------- MEDSL precinct files

def chamber_of(office: str):
    o = str(office).upper()
    if o.startswith(("US ", "U.S.", "UNITED STATES")) or "CONGRESS" in o: return None
    if re.search(r"STATE SENATE|^SENATE$|STATE SENATOR", o): return "upper"
    if re.search(r"STATE HOUSE|STATE REPRESENTATIVE|STATE ASSEMBLY|^ASSEMBLY|HOUSE OF REPRESENTATIVES$|GENERAL ASSEMBLY", o) and "SENATE" not in o: return "lower"
    return None


def norm_district(st, ch, d, county=""):
    s = str(d).strip().upper()
    if ch == "cd":
        m = re.search(r"(\d+)", s); return str(max(int(m.group(1)), 1)) if m else ("1" if "LARGE" in s else None)
    if s in ("", "NAN", "NONE", "STATEWIDE"): return None
    if st == "NH" and ch == "lower":
        m = re.search(r"(\d+)", s); cty = str(county).strip().upper()
        mm = re.match(r"([A-Z]+)\D*(\d+)", s)                        # labels like "BELKNAP 1" / "BELKNAP DISTRICT 1"
        if mm: return f"{mm.group(1).title()} {int(mm.group(2))}"
        return f"{cty.title()} {int(m.group(1))}" if m and cty else None
    if st == "MN" and ch == "lower":
        m = re.search(r"0*(\d+)\s*([AB])", s)
        return f"{int(m.group(1))}{m.group(2)}" if m else None
    m = re.search(r"(\d+)", s)
    return str(int(m.group(1))) if m else None


COLS = ["state_po", "stage", "office", "district", "county_fips", "county_name", "jurisdiction_fips", "precinct", "mode", "party_simplified",
        "candidate", "votes", "magnitude", "writein", "special"]


def _keep_office(o: pd.Series) -> pd.Series:
    u = o.astype(str).str.upper().str.strip()
    return (u.str.contains("PRESIDENT") & ~u.str.contains("VICE")) | u.str.match(r"^(US|U\.S\.) HOUSE") | u.map(_chamber_map(u)).notna()


def _chamber_map(u: pd.Series) -> dict:
    return {o: chamber_of(o) for o in u.unique()}


def office_chamber(df) -> pd.Series:
    """'upper' / 'lower' / 'cd' (U.S. House) / None per row."""
    m = _chamber_map(df["office"]); c = df["office"].map(m)
    return c.where(~df["office"].str.match(r"^(US|U\.S\.) HOUSE"), "cd")


def medsl_load(p: Path, st: str) -> pd.DataFrame:
    """MEDSL precinct file -> the rows this model uses (president, U.S. House, state legislature), one count per precinct x contest x
    candidate. The slim extract is kept as parquet next to the download (the cache keeps it; the large CSV can be deleted)."""
    slim = p.with_name(p.stem + f".{st}.slim.parquet")
    if slim.exists():
        df = pd.read_parquet(slim)
    else:
        parts = []
        with open(p, encoding="utf-8", errors="replace") as fh: first = fh.readline()
        sep = "\t" if first.count("\t") > first.count(",") else ","
        for ch in pd.read_csv(p, dtype=str, low_memory=False, chunksize=400_000, sep=sep, usecols=lambda c: c.lower().strip('"') in COLS):
            ch.columns = [c.lower().strip('"') for c in ch.columns]
            if "state_po" in ch: ch = ch[ch["state_po"] == st]
            parts.append(ch[_keep_office(ch["office"])])
        df = pd.concat(parts, ignore_index=True); df.to_parquet(slim)
    if "stage" in df: df = df[df["stage"].fillna("GEN").str.upper().str.startswith("GEN")]
    df["votes"] = pd.to_numeric(df["votes"], errors="coerce").fillna(0.0)
    for c in ("county_fips", "jurisdiction_fips", "precinct", "county_name", "district", "mode", "candidate", "party_simplified", "magnitude", "writein", "special"):
        if c not in df: df[c] = ""
        df[c] = df[c].fillna("").astype(str)
    df["office"] = df["office"].astype(str).str.upper().str.strip()
    df["key"] = df["county_fips"] + "|" + df["jurisdiction_fips"] + "|" + df["precinct"].str.upper().str.strip()
    df["party"] = df["party_simplified"].str.upper().map({"DEMOCRAT": "D", "REPUBLICAN": "R"}).fillna("O")
    # one count per precinct x contest x candidate: a TOTAL row where a state reports both TOTAL and the vote modes
    is_tot = df["mode"].str.upper() == "TOTAL"
    has_total = is_tot.groupby([df["key"], df["office"], df["district"]]).transform("any")
    df = df[~has_total | is_tot]
    return df


def pres_rows(df):
    return df[df["office"].str.contains("PRESIDENT") & ~df["office"].str.contains("VICE")]


def label_lean(df: pd.DataFrame, st: str, ch: str, tag: str):
    """District D/R presidential votes from the same precinct's legislative district labels. Split precincts (one precinct
    voting in two districts of the chamber) are divided by the legislative ballots cast in each part - except New Hampshire,
    where a town votes in its base district AND a floterial district, so it counts fully in each. Presidential votes on rows
    with no legislative label (county-level absentee / early / provisional rows) are spread over the county's districts in
    proportion to the labelled presidential votes."""
    leg = df[office_chamber(df) == ch].copy()
    if leg.empty: return None
    leg["dist"] = [norm_district(st, ch, d, c) for d, c in zip(leg["district"], leg["county_name"])]
    bad = leg["dist"].isna().mean(); leg = leg[leg["dist"].notna()]
    T = leg.groupby(["key", "dist"])["votes"].sum().reset_index()
    T["county"] = T["key"].str.split("|").str[0]
    pr = pres_rows(df); pr = pr[pr["party"].isin(["D", "R"])]
    P = pr.pivot_table(index="key", columns="party", values="votes", aggfunc="sum", fill_value=0.0)
    for c in "DR":
        if c not in P: P[c] = 0.0
    tot_pres = float(P["D"].sum() + P["R"].sum())
    if st == "NH":
        T["w"] = 1.0
    else:
        T["w"] = T["votes"] / T.groupby("key")["votes"].transform("sum")
        T.loc[T["w"].isna(), "w"] = 1.0 / T.groupby("key")["dist"].transform("count")
    J = T.merge(P, left_on="key", right_index=True, how="inner")
    J["d"] = J["D"] * J["w"]; J["r"] = J["R"] * J["w"]
    cells = J.groupby(["county", "dist"])[["d", "r"]].sum()
    # unlabelled presidential rows
    lab = set(T["key"]); un = P[~P.index.isin(lab)].copy(); un["county"] = un.index.str.split("|").str[0]
    un_share = float((un["D"].sum() + un["R"].sum()) / tot_pres) if tot_pres else float("nan")
    lost = 0.0
    if len(un):
        cw = cells.reset_index(); v = cw["d"] + cw["r"]; cw["share"] = v / v.groupby(cw["county"]).transform("sum")
        uc = un.groupby("county")[["D", "R"]].sum()
        add = cw.merge(uc, left_on="county", right_index=True, how="inner")
        add["d"] = add["D"] * add["share"]; add["r"] = add["R"] * add["share"]
        cells = cells.add(add.set_index(["county", "dist"])[["d", "r"]], fill_value=0.0)
        lost = float(uc.loc[~uc.index.isin(cw["county"]), ["D", "R"]].sum().sum() / tot_pres) if tot_pres else 0.0
    out = cells.groupby(level="dist")[["d", "r"]].sum()
    out = out.reset_index().rename(columns={"dist": "district"})
    QC[f"{tag}_{st}_{ch}"] = {"districts": int(len(out)), "unlabelled_pres_share": round(un_share, 4), "pres_lost_share": round(lost, 4),
                              "bad_district_labels": round(float(bad), 4), "assigned_vs_total": round(float((out["d"].sum() + out["r"].sum()) / tot_pres), 4) if st != "NH" else None}
    print(f"  {tag} {st} {ch}: {len(out)} districts, unlabelled pres {un_share:.3f}, lost {lost:.4f}")
    out.attrs["cells"] = cells.reset_index().rename(columns={"dist": "district"})
    out.attrs["qc"] = QC[f"{tag}_{st}_{ch}"]
    return out


def results_from_medsl(df, st, year):
    oc = office_chamber(df); leg = df[oc.isin(["upper", "lower"])].copy()
    if leg.empty: return pd.DataFrame()
    leg["chamber"] = oc[leg.index]
    leg["dist"] = [norm_district(st, ch, d, c) for ch, d, c in zip(leg["chamber"], leg["district"], leg["county_name"])]
    leg = leg[leg["dist"].notna()]
    leg["cand"] = leg["candidate"].str.upper().str.strip()
    leg = leg[~leg["cand"].isin(["", "OVER VOTES", "UNDER VOTES", "OVERVOTES", "UNDERVOTES", "BLANK", "TOTAL VOTES CAST", "REGISTERED VOTERS", "BALLOTS CAST"])]
    leg = leg[~leg["cand"].str.contains("OVERVOTE|UNDERVOTE|BLANK VOTE|SPOILED", regex=True)]
    leg["is_wi"] = leg["writein"].str.upper().isin(["TRUE", "1"]) | leg["cand"].str.contains("WRITE", regex=False)
    leg["mag"] = pd.to_numeric(leg["magnitude"], errors="coerce").fillna(1).astype(int)
    leg["spec"] = leg["special"].str.upper().isin(["TRUE", "1"])
    g = leg.groupby(["chamber", "dist", "spec", "cand"]).agg(party=("party", lambda x: x.mode().iat[0]), votes=("votes", "sum"), mag=("mag", "max"), wi=("is_wi", "all")).reset_index()
    g.loc[g["wi"], "party"] = "W"
    g = g.sort_values(["chamber", "dist", "spec", "votes"], ascending=[True, True, True, False])
    g["rank"] = g.groupby(["chamber", "dist", "spec"]).cumcount() + 1
    g["winner"] = g["rank"] <= g["mag"]
    g.insert(0, "state", st); g.insert(0, "year", year)
    return g.rename(columns={"dist": "district", "cand": "candidate", "mag": "magnitude", "spec": "special"}).drop(columns=["rank", "wi"])


def county_margin(df):
    pr = pres_rows(df); pr = pr[pr["party"].isin(["D", "R"])]
    v = pr.groupby(["county_fips", "party"])["votes"].sum().unstack(fill_value=0.0)
    return (100 * (v["D"] - v["R"]) / (v["D"] + v["R"])).rename("m"), (v["D"] + v["R"]).rename("n")


# ---------------------------------------------------------------- shapes

def vest_votes(zp: Path, yy: str):
    import geopandas as gpd
    with zipfile.ZipFile(zp) as z:
        shp = [n for n in z.namelist() if n.lower().endswith(".shp")]
    g = gpd.read_file(f"zip://{zp}!{shp[0]}")
    dcol = [c for c in g.columns if re.fullmatch(f"G{yy}PRED\\w*", c, re.I)]; rcol = [c for c in g.columns if re.fullmatch(f"G{yy}PRER\\w*", c, re.I)]
    if not dcol or not rcol: raise ValueError(f"{zp.name}: no G{yy}PRED/PRER columns in {list(g.columns)[:40]}")
    g["d"] = g[dcol].apply(pd.to_numeric, errors="coerce").fillna(0).sum(1); g["r"] = g[rcol].apply(pd.to_numeric, errors="coerce").fillna(0).sum(1)
    namecol = next((c for c in g.columns if c.upper() in ("NAME", "PRECINCT", "PREC_NAME", "TOWN", "NAME20", "NAME16")), None)
    g["pname"] = g[namecol].astype(str) if namecol else ""
    g = g[["d", "r", "pname", "geometry"]].to_crs(5070)
    g["geometry"] = g.geometry.make_valid()
    return g


def tiger(year, fips, lay):
    import geopandas as gpd
    p = url_get(f"https://www2.census.gov/geo/tiger/TIGER{year}/{lay.upper()}/tl_{year}_{fips}_{lay.lower()}.zip", f"tl_{year}_{fips}_{lay.lower()}.zip")
    g = gpd.read_file(f"zip://{p}").to_crs(5070); col = "SLDUST" if lay.lower() == "sldu" else "SLDLST"
    g = g[~g[col].astype(str).str.upper().str.startswith("ZZ")]
    g["code"] = g[col].astype(str); g["geometry"] = g.geometry.make_valid()
    return g[["code", "NAMELSAD", "geometry"]]


_COUNTIES = None
def counties():
    global _COUNTIES
    if _COUNTIES is None:
        import geopandas as gpd
        p = url_get("https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_county_500k.zip", "cb_2020_us_county_500k.zip")
        _COUNTIES = gpd.read_file(f"zip://{p}").to_crs(5070)[["GEOID", "geometry"]]
    return _COUNTIES


def areal(v, t, st, ch, with_county=False):
    """Areal interpolation of precinct votes into districts. -> district, d, r (+ county weights)."""
    import geopandas as gpd
    v = v.copy(); v["pid"] = np.arange(len(v)); v["parea"] = v.geometry.area
    x = gpd.overlay(v[["pid", "d", "r", "parea", "geometry"]], t[["code", "geometry"]], how="intersection", keep_geom_type=True)
    x["f"] = x.geometry.area / x["parea"].where(x["parea"] > 0)
    x["d"] = x["d"] * x["f"]; x["r"] = x["r"] * x["f"]
    x["district"] = [norm_district(st, ch, c) for c in x["code"]]
    out = x.groupby("district")[["d", "r"]].sum().reset_index()
    cov = float((out["d"].sum() + out["r"].sum()) / (v["d"].sum() + v["r"].sum()))
    cw = None
    if with_county:
        pts = x.copy(); pts["geometry"] = pts.geometry.representative_point()
        pts = gpd.sjoin(pts, counties(), how="left", predicate="within")
        pts["n"] = pts["d"] + pts["r"]
        cw = pts.groupby(["district", "GEOID"])["n"].sum().reset_index().rename(columns={"GEOID": "county_fips"})
    return out, cov, cw


def map_changes(fips, lay, y0, y1):
    """Districts whose shape changed between two TIGER vintages (symmetric difference > 1 % of the area)."""
    a = tiger(y0, fips, lay).set_index("code"); b = tiger(y1, fips, lay).set_index("code"); ch = []
    for c in a.index.intersection(b.index):
        ga, gb = a.at[c, "geometry"], b.at[c, "geometry"]
        if ga.symmetric_difference(gb).area > 0.01 * ga.area: ch.append(c)
    return sorted(ch), len(a), len(b)


# ---------------------------------------------------------------- builders

NEST = {"MN": lambda d: str(int(re.match(r"(\d+)", d).group(1))), "WI": lambda d: str((int(d) + 2) // 3)}


def build_2026():
    rows, res, cm = [], [], {}
    for st in STATES:
        p24 = dv_get("m2024", f"2024-{st.lower()}-precinct-general\\.(tab|csv)")
        if p24 is None: print("!! no 2024 file", st); continue
        df = medsl_load(p24, st)
        offs = sorted({o for o in df["office"].unique() if chamber_of(o)}); print(f"{st} 2024 legislative offices: {offs}")
        cm[(st, 2024)] = county_margin(df)
        res.append(results_from_medsl(df, st, 2024))
        for ch in ("upper", "lower"):
            o = label_lean(df, st, ch, "m2024")
            if o is not None: o["state"], o["chamber"], o["src"] = st, ch, "medsl2024_labels"; rows.append(o)
        if st in NEST:                                            # Senate from nested House / Assembly districts
            lo = rows[-1] if rows and rows[-1]["chamber"].iat[0] == "lower" and rows[-1]["state"].iat[0] == st else None
            if lo is not None:
                n = lo.copy(); n["district"] = n["district"].map(NEST[st]); n = n.groupby("district")[["d", "r"]].sum().reset_index()
                n["state"], n["chamber"], n["src"] = st, "upper", "medsl2024_nested"; rows.append(n)
        p20 = dv_get("m2020", f"2020-{st.lower()}-precinct-general\\.(tab|csv)")
        if p20 is not None:
            d20 = medsl_load(p20, st); cm[(st, 2020)] = county_margin(d20); res.append(results_from_medsl(d20, st, 2020))
            for ch in ("upper", "lower"):
                o = label_lean(d20, st, ch, "m2020")
                if o is not None: o["state"], o["chamber"], o["src"] = st, ch, "medsl2020_labels"; o["map"] = 2020; HIST.append(o.assign(pres_year=2020, cycle=2020))
    L = pd.concat(rows, ignore_index=True)
    # spatial cross-check / fallback on the 2025 TIGER maps: VEST 2020 + county swing 2020->2024
    sp = []
    for st in STATES:
        if st == "NH": continue
        zp = dv_get("vest2020", f"{st.lower()}_2020\\.zip")
        if zp is None: print("!! no VEST 2020", st); continue
        try: v = vest_votes(zp, "20")
        except Exception as e: print("!! VEST 2020", st, e); continue
        sw = None
        if (st, 2024) in cm and (st, 2020) in cm: sw = (cm[(st, 2024)][0] - cm[(st, 2020)][0]).rename("swing")
        for ch, lay in (("upper", "sldu"), ("lower", "sldl")):
            if st == "AZ" and ch == "lower": lay = "sldu"          # AZ House districts = the 30 legislative districts
            try:
                t = tiger(2025, FIPS[st], lay); o, cov, cw = areal(v, t, st, ch, with_county=True)
            except Exception as e: print("!! spatial", st, ch, e); continue
            o["lean20"] = 100 * (o["d"] - o["r"]) / (o["d"] + o["r"])
            if sw is not None and cw is not None:
                cw = cw.merge(sw, left_on="county_fips", right_index=True, how="left"); cw["swing"] = cw["swing"].fillna(sw.mean())
                s = cw.groupby("district").apply(lambda q: np.average(q["swing"], weights=q["n"]) if q["n"].sum() > 0 else np.nan).rename("swing")
                o = o.merge(s, left_on="district", right_index=True, how="left")
            else: o["swing"] = np.nan
            o["state"], o["chamber"] = st, ch; QC[f"spatial2025_{st}_{ch}_coverage"] = round(cov, 4); sp.append(o)
            HIST.append(o[["district", "d", "r", "state", "chamber"]].assign(src="vest2020_tiger2025", pres_year=2020, cycle=2026))
    S = pd.concat(sp, ignore_index=True) if sp else pd.DataFrame(columns=["state", "chamber", "district", "lean20", "swing"])
    S["lean24_hat"] = S["lean20"] + S["swing"]
    # fallback rows: MI Senate (no 2024 Senate election; map partly redrawn for 2026) and PA odd Senate seats
    have = set(zip(L["state"], L["chamber"], L["district"]))
    fb = S[[(s, c, d) not in have for s, c, d in zip(S["state"], S["chamber"], S["district"])]].copy()
    if len(fb):
        tot = fb["d"] + fb["r"]; m = fb["lean24_hat"] / 100
        fb["d"], fb["r"] = tot * (1 + m) / 2, tot * (1 - m) / 2; fb["src"] = "vest2020_tiger2025_countyswing"
        L = pd.concat([L, fb[["district", "d", "r", "state", "chamber", "src"]]], ignore_index=True)
    L["lean24"] = 100 * (L["d"] - L["r"]) / (L["d"] + L["r"])
    X = L.merge(S[["state", "chamber", "district", "lean20", "lean24_hat"]], on=["state", "chamber", "district"], how="left")
    for (st, ch), q in X[X["src"].str.startswith("medsl")].groupby(["state", "chamber"]):
        ok = q["lean24_hat"].notna()
        if ok.sum() > 3:
            e = q.loc[ok, "lean24"] - q.loc[ok, "lean24_hat"]
            QC[f"crosscheck_{st}_{ch}"] = {"n": int(ok.sum()), "of": int(len(q)), "rms_labels_vs_spatial": round(float(np.sqrt((e ** 2).mean())), 2), "mean": round(float(e.mean()), 2)}
            print(f"  cross-check {st} {ch}: labels v spatial+swing rms {np.sqrt((e ** 2).mean()):.2f} (n {ok.sum()}/{len(q)})")
    OUT.mkdir(parents=True, exist_ok=True)
    X.round(3).to_csv(OUT / "lean_2026.csv", index=False)
    R_ = pd.concat(res, ignore_index=True)
    return R_


HIST: list = []


def build_hist():
    """Previous-presidential lean on the maps of the 2018 and 2022 elections (leak-free backtest inputs)."""
    for cyc, vy, yy, ty in ((2018, "vest2016", "16", 2018), (2022, "vest2020", "20", 2022)):
        for st in STATES:
            if st == "NH": continue
            pat = f"{st.lower()}(_{2000 + int(yy)})?\\.zip"
            zp = dv_get(vy, pat)
            if zp is None: print("!! no", vy, st); continue
            try: v = vest_votes(zp, yy)
            except Exception as e: print("!!", vy, st, e); continue
            for ch, lay in (("upper", "sldu"), ("lower", "sldl")):
                if st == "AZ" and ch == "lower": lay = "sldu"
                try: t = tiger(ty, FIPS[st], lay); o, cov, _ = areal(v, t, st, ch)
                except Exception as e: print("!! spatial", cyc, st, ch, e); continue
                QC[f"hist{cyc}_{st}_{ch}_coverage"] = round(cov, 4)
                HIST.append(o.assign(state=st, chamber=ch, src=f"{vy}_tiger{ty}", pres_year=2000 + int(yy), cycle=cyc))
                print(f"  {cyc} {st} {ch}: {len(o)} districts, coverage {cov:.3f}")
    # New Hampshire by town name: MEDSL district labels of the cycle x VEST town presidential votes
    for cyc, lab_key, lab_pat, vy, yy in ((2018, "m2018", "2018-nh-precinct-general\\.(tab|csv)", "vest2016", "16"), (2022, "m2022", None, "vest2020", "20")):
        try:
            if lab_key == "m2022": df = m2022_state("NH")
            else: df = medsl_load(dv_get(lab_key, lab_pat), "NH")
            v = vest_votes(dv_get(vy, f"nh_{2000 + int(yy)}\\.zip"), yy)
            tv = pd.DataFrame({"name": v["pname"].map(_town), "D": v["d"], "R": v["r"]}).groupby("name")[["D", "R"]].sum()
            for ch in ("upper", "lower"):
                leg = df[office_chamber(df) == ch].copy()
                leg["dist"] = [norm_district("NH", ch, d, c) for d, c in zip(leg["district"], leg["county_name"])]
                leg["name"] = leg["precinct"].map(_town)
                m = leg[["name", "dist"]].dropna().drop_duplicates().merge(tv, left_on="name", right_index=True, how="left")
                miss = m["D"].isna().mean(); QC[f"hist{cyc}_NH_{ch}_town_unmatched"] = round(float(miss), 4)
                if miss > 0: print(f"  NH {cyc} {ch}: unmatched towns {sorted(m.loc[m['D'].isna(), 'name'].unique())[:30]}")
                o = m.dropna().groupby("dist")[["D", "R"]].sum().reset_index().rename(columns={"dist": "district", "D": "d", "R": "r"})
                HIST.append(o.assign(state="NH", chamber=ch, src=f"{lab_key}_labels_{vy}_towns", pres_year=2000 + int(yy), cycle=cyc))
        except Exception as e: print("!! NH hist", cyc, e)
    H = pd.concat(HIST, ignore_index=True)
    H["lean"] = 100 * (H["d"] - H["r"]) / (H["d"] + H["r"])
    H.round(3).to_csv(OUT / "lean_hist.csv", index=False)


def _town(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(s).upper()); s = re.sub(r"\bWD\b", "WARD", s); s = re.sub(r"\bTWP\b|\bTOWNSHIP\b", "", s)
    s = re.sub(r"\bWARD\s*0*(\d+)", r"WARD \1", s)
    return re.sub(r"\s+", " ", s).strip()


def m2022_state(st):
    """The 2022 state-offices file is one national CSV (863 MB): one pass writes every state's slim extract."""
    src = dv_get("m2022", "STATE_precinct_general\\.(tab|csv)")
    slim = lambda s_: src.with_name(src.stem + f".{s_}.slim.parquet")
    if not slim(st).exists():
        parts = {s_: [] for s_ in STATES}
        for ch in pd.read_csv(src, dtype=str, chunksize=500_000, low_memory=False, usecols=lambda c: c.lower() in COLS):
            ch.columns = [c.lower() for c in ch.columns]
            ch = ch[ch["state_po"].isin(STATES)]; ch = ch[_keep_office(ch["office"])]
            for s_, q in ch.groupby("state_po"): parts[s_].append(q)
        for s_, q in parts.items():
            (pd.concat(q, ignore_index=True) if q else pd.DataFrame(columns=COLS)).to_parquet(slim(s_))
    return medsl_load(src, st)


def build_results(R24):
    res = [R24]
    for st in STATES:
        try: res.append(results_from_medsl(m2022_state(st), st, 2022))
        except Exception as e: print("!! 2022 results", st, e)
    try: res.append(results_from_medsl(medsl_load(dv_get("m2018", "2018-nh-precinct-general\\.(tab|csv)"), "NH"), "NH", 2018))
    except Exception as e: print("!! NH 2018", e)
    X = pd.concat(res, ignore_index=True)
    X.to_csv(OUT / "medsl_results.csv.gz", index=False, compression="gzip")
    s = X[X["winner"] & ~X["special"]].groupby(["year", "state", "chamber", "party"]).size().unstack(fill_value=0)
    print("winners by chamber (MEDSL):\n" + s.to_string())


def build_klarner():
    src = dv_get("klarner", "127_slers_1967to2022.*\\.tab")
    k = pd.read_csv(src, dtype=str, low_memory=False)
    for c in ("etype", "partyz", "exper", "outcome", "dtype", "flot", "nest"):
        if c in k: print(f"  klarner {c}: {k[c].value_counts().head(12).to_dict()}")
    k["year"] = pd.to_numeric(k["year"], errors="coerce"); k = k[(k["year"] >= 1972) & (k["etype"].astype(str) == "g")]
    k["year"] = k["year"].astype(int).astype(str)
    k["vote"] = pd.to_numeric(k["vote"], errors="coerce").fillna(0)
    k["pz"] = k["partyz"].str.lower().map({"d": "D", "r": "R"}).fillna("O")
    k["inc"] = k["exper"].astype(str).str.lower().str.startswith("inc")
    k["won"] = k["outcome"].astype(str).str.lower().str.startswith("w")
    keys = ["year", "sab", "sen", "dno", "dname", "ddez", "geopost", "mmdpost", "specpost", "dtype", "dseats", "eseats", "etype", "redist", "regime", "flot", "nest", "month"]
    for c in keys:
        if c not in k: k[c] = ""
        k[c] = k[c].fillna("").astype(str)
    agg = k.groupby(keys)[["pz", "vote", "won", "inc", "cand"]].apply(lambda q: pd.Series({
        "d_votes": q.loc[q.pz == "D", "vote"].sum(), "r_votes": q.loc[q.pz == "R", "vote"].sum(), "o_votes": q.loc[q.pz == "O", "vote"].sum(),
        "n_d": int((q.pz == "D").sum()), "n_r": int((q.pz == "R").sum()), "n_o": int((q.pz == "O").sum()),
        "w_d": int((q.won & (q.pz == "D")).sum()), "w_r": int((q.won & (q.pz == "R")).sum()), "w_o": int((q.won & (q.pz == "O")).sum()),
        "inc_d": int((q.inc & (q.pz == "D")).sum()), "inc_r": int((q.inc & (q.pz == "R")).sum()), "inc_o": int((q.inc & (q.pz == "O")).sum()),
        "top_d": q.loc[q.pz == "D", "vote"].max() if (q.pz == "D").any() else 0.0, "top_r": q.loc[q.pz == "R", "vote"].max() if (q.pz == "R").any() else 0.0,
        "names_inc": ";".join(q.loc[q.inc, "cand"].astype(str).str[:40])})).reset_index()
    print(f"  klarner aggregated: {len(agg)} district-elections; target-state 2018/2022 sample:")
    print(agg[agg["sab"].isin(["MN", "NH", "AZ"]) & agg["year"].isin(["2022"])].head(15).to_string()[:3000])
    agg.to_csv(OUT / "klarner.csv.gz", index=False, compression="gzip")


def build_openstates():
    d = R / "openstates"
    if not d.exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "https://github.com/openstates/people", str(d)], check=True)
        subprocess.run(["git", "-C", str(d), "sparse-checkout", "set"] + [f"data/{s.lower()}/legislature" for s in STATES], check=True)
    import yaml
    rows = []
    for st in STATES:
        for f in sorted((d / "data" / st.lower() / "legislature").glob("*.yml")):
            y = yaml.safe_load(f.read_text())
            party = (y.get("party") or [{}])[-1].get("name", "")
            for r in y.get("roles", []):
                if r.get("end_date"): continue
                rows.append({"state": st, "chamber": r.get("type"), "district": r.get("district"), "name": y.get("name"), "party": party,
                             "id": y.get("id")})
    o = pd.DataFrame(rows); o.to_csv(OUT / "openstates_current.csv", index=False)
    print("  openstates:", o.groupby(["state", "chamber", "party"]).size().unstack(fill_value=0).to_string())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    steps = sys.argv[1:] or ["openstates", "klarner", "2026", "hist"]
    R24 = None
    if "openstates" in steps:
        try: build_openstates()
        except Exception as e: print("!! openstates", e)
    if "klarner" in steps: build_klarner()
    if "2026" in steps:
        R24 = build_2026(); build_results(R24)
    if "hist" in steps: build_hist()
    if "maps" in steps or "2026" in steps:
        for st in STATES:
            for lay in ("sldu", "sldl"):
                try:
                    for y0, y1 in ((2022, 2024), (2024, 2025)):
                        ch, a, b = map_changes(FIPS[st], lay, y0, y1); QC[f"tiger_change_{st}_{lay}_{y0}_{y1}"] = {"changed": ch, "n0": a, "n1": b}
                        print(f"  TIGER {st} {lay} {y0}->{y1}: {len(ch)} of {a} changed {ch[:40]}")
                except Exception as e: print("!! map change", st, lay, e)
    (OUT / "qc.json").write_text(json.dumps(QC, indent=1, sort_keys=True))
    report()


if __name__ == "__main__":
    main()
