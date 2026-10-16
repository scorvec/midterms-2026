"""Our own congressional-district presidential lean on the 2026 maps (all 435 seats) and on the maps of the 2018 and 2022
elections, from public data only - a replacement candidate for Cook PVI (proprietary). One-time builds in GitHub Actions
(.github/workflows/cd-lean.yml); not used by the House model unless MIDTERMS_CD_LEAN=ours.

Method (2026 maps)
  * Which states changed their map for 2026: the Census 119th- vs 120th-Congress block equivalency files (block -> district),
    compared block by block (population-weighted).
  * Unchanged states: 2024 president aggregated by each precinct's 2024 U.S. House district label (MEDSL 2024 precinct returns,
    CC0) - split precincts divided by U.S. House ballots in each part; county-level absentee/early rows spread over the county's
    districts pro rata.
  * Redrawn states (and any state whose labels cover < 97 % of its presidential vote): VEST 2020 precinct shapes (CC BY 4.0) ->
    2020 Census blocks (block internal point inside the precinct; precinct votes split by block population, TIGER 2020 POP20) ->
    120th-Congress districts through the block equivalency file = 2020 president on the new map, exact up to precinct splits.
    2024 is then carried to each block by the swing and turnout change of its (county x 2024 district) cell, measured on the
    MEDSL 2024 labels against the same cell's 2020 block total. Validation: the same block route applied to the 2024 state
    legislative districts of the state-leg build (exact 2024 values known from labels) - see qc_cd.json.
  * 2020 president on unchanged maps: VEST 2020 shapes areally interpolated into TIGER 2024 CD119 districts.
Output: data/static/cd_lean_2026.csv (seat, pres24/pres20 votes and two-party margins, method, QC), data/static/qc_cd.json.
    python -m midterms.cd_lean_build

Method (2018 and 2022 maps, `hist`): the same lean from the elections before each cycle - 2018: 0.75 x 2016 + 0.25 x 2012,
2022: 0.75 x 2020 + 0.25 x 2016, each against the national margin. VEST 2016 / 2020 precincts -> 2020 blocks as above ->
the 118th-Congress block equivalency file (2022) or the TIGER/Line 116th-Congress districts by block internal point (2018).
VEST has no 2012 files: 2012 is the 2016 block votes moved by each county's 2012-16 swing and turnout change, with the county
results from Wikipedia's state pages (checked against the official state totals); the same county step, applied where both
years are known (2016 rebuilt from 2020 blocks), measures its error. States calibrated to their official totals (MIT).
Output: data/static/cd_lean_hist.csv (cycle, seat, votes, margins, method, lean_75_25), data/static/qc_cd_hist.json.
    python -m midterms.cd_lean_build hist [ST ...]
"""

from __future__ import annotations

import io
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from . import stateleg_build as B
from .fetch import open_url, report
from .paths import STATIC

ABBR = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
}
FIPS = {v: k for k, v in ABBR.items()}
STATE_CODES = {*ABBR, "11", "72"}  # with DC and Puerto Rico, which the block files also carry
QC: dict = {}
# Which congressional map is in force for November 2026, per a dated source (sources checked 2026-10-05). The Census 120th-Congress
# block equivalency file records the maps states adopted and does not follow later court orders. "map": 119 = the 2024 (119th-Congress)
# lines, 120 = the Census 120th-Congress file. "bef120": whether that file is known to hold the map in force (block level):
# "n/a" when the 119th lines are used, else "unverified" - no state's plan file was compared block by block.
MAP_STATUS = {
    "MO": {
        "map": 119,
        "in_force": "2022 map (6-2): U.S. Supreme Court blocked the 2025 map and ordered the 2022 map for 2026, 2026-09-25",
        "source": "PBS / Missouri Independent / The Hill, 2026-09-25; Wikipedia '2025 Missouri redistricting'",
        "bef120": "n/a",
    },
    "TX": {
        "map": 120,
        "in_force": "2025 map: three-judge panel ruling stayed by the U.S. Supreme Court",
        "source": "Ballotpedia / Wikipedia '2025-2026 United States redistricting' (checked 2026-10-05)",
        "bef120": "unverified",
    },
    "CA": {
        "map": 120,
        "in_force": "Prop 50 map: three-judge panel rejected the challenge, Supreme Court denied review",
        "source": "same, checked 2026-10-05",
        "bef120": "unverified",
    },
    "OH": {
        "map": 120,
        "in_force": "2025 map, no live challenge",
        "source": "same, checked 2026-10-05",
        "bef120": "unverified",
    },
    "NC": {
        "map": 120,
        "in_force": "2025 map, litigation on the merits pending",
        "source": "same, checked 2026-10-05",
        "bef120": "unverified",
    },
    "FL": {
        "map": 120,
        "in_force": "2026 map signed 2026-05-04, litigation pending",
        "source": "same, checked 2026-10-05",
        "bef120": "unverified",
    },
    "TN": {
        "map": 120,
        "in_force": "2026 map passed 2026-05-07",
        "source": "Wikipedia '2026 Tennessee redistricting' (checked 2026-10-05)",
        "bef120": "unverified",
    },
    "UT": {
        "map": 120,
        "in_force": "court-ordered plaintiffs' Map 1 (Judge Gibson; federal court cleared it)",
        "source": "KSL / NBC News (checked 2026-10-05)",
        "bef120": "unverified",
    },
    "AL": {
        "map": 120,
        "in_force": "2023 legislature map, Supreme Court lifted the injunction (May 11 / June 2, 2026)",
        "source": "Bloomberg Law 'What we know about the race to redraw 2026 lines' (checked 2026-10-05)",
        "bef120": "unverified - the seat table and our lean disagree on AL-2 (Cook R+7, ours R+10)",
    },
    "LA": {
        "map": 120,
        "in_force": "UNRESOLVED: Callais voided the 2024 map; new map / suspended House primaries",
        "source": "Bloomberg Law (checked 2026-10-05)",
        "bef120": "unverified",
    },
}
MAPF = "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/"


def _crawl(url, depth, pat, seen=None):
    seen = seen if seen is not None else set()
    out = []
    if depth < 0 or url in seen:
        return out
    seen.add(url)
    try:
        h = open_url(url, timeout=60).decode("utf-8", "replace")
    except Exception:
        return out
    for href in re.findall(r'href="([^"?#]+)"', h):
        if href.startswith(("/", "http", "..")):
            continue
        u = url + href
        if href.endswith("/"):
            if re.search(r"20(2[3-9])|congress|cd1|119|120", href, re.I):
                out += _crawl(u, depth - 1, pat, seen)
        elif re.search(pat, href, re.I):
            out.append(u)
    return out


def _block_id(s: pd.Series) -> pd.Series:
    """Census block GEOIDs as 15-digit strings, whatever a release wraps them in: a summary-level prefix ('1000000US...'),
    separators, spaces or dropped leading zeros."""
    d = s.astype(str).str.strip().str.replace(r"\D", "", regex=True)
    return d.str[-15:].str.zfill(15)


def bef(n: int) -> pd.DataFrame:
    """Block -> district (CDFP) for the n-th Congress, from the Census block equivalency files. Fails when the block ids do
    not start with state codes (they would join nothing)."""
    pq = B.R / "bef" / f"cd{n}_v3.parquet"
    b = pd.read_parquet(pq) if pq.exists() else _bef_build(n, pq)
    ok = b["block"].str[:2].isin(STATE_CODES)
    if ok.mean() < 0.99:
        raise RuntimeError(f"CD{n} block file: {ok.mean():.1%} of block ids start with a state code, e.g. {b.head(3)}")
    return b


def _bef_build(n, pq):
    urls = _crawl(MAPF, 3, rf"cd_?{n}.*\.(zip|txt)$")
    print(f"  BEF {n}: {urls[:6]}")
    if not urls:
        raise RuntimeError(f"no CD{n} block equivalency file found under {MAPF}")
    nat = [u for u in urls if "national" in u.lower()] or urls
    frames = []
    for u in nat[:1] if [u for u in urls if "national" in u.lower()] else urls:
        p = B.url_get(u, Path(u).name)
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as z:
                frames.extend(
                    pd.read_csv(z.open(nm), dtype=str, sep=None, engine="python")
                    for nm in z.namelist()
                    if nm.lower().endswith((".txt", ".csv"))
                )
        else:
            frames.append(pd.read_csv(p, dtype=str, sep=None, engine="python"))
    b = pd.concat(frames, ignore_index=True)
    b.columns = [c.upper() for c in b.columns]
    gcol = next(c for c in b.columns if c in ("GEOID", "BLOCKID", "GEOID20"))
    dcol = next(c for c in b.columns if c.startswith("CD") or c == "DISTRICT")
    b = b.rename(columns={gcol: "block", dcol: "cd"})[["block", "cd"]]
    print(f"  BEF {n}: raw rows like {b.head(2).to_dict('records')}")
    b["block"] = _block_id(b["block"])
    b["cd"] = b["cd"].astype(str).str.strip()
    n0 = len(b)
    b = b[~b["cd"].isin(["ZZ", "98", ""])].drop_duplicates("block")  # the zip holds overlapping files
    QC[f"bef{n}_rows_raw_vs_unique"] = [n0, len(b)]
    pq.parent.mkdir(parents=True, exist_ok=True)
    b.to_parquet(pq)
    print(f"  BEF {n}: {len(b)} blocks, columns {gcol}/{dcol}")
    return b


_MIT = None


def mit():
    """State presidential two-party totals 2012-2024 (MIT Election Lab, CC0 - the file the model's bootstrap already uses)."""
    global _MIT
    if _MIT is None:
        from .bootstrap import FILES, RAW as BR

        dest, url, _ = next(f for f in FILES if f[0] == "mit/president_1976_2024.csv")
        f = BR / dest
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            B.stream(url, f)
        p = pd.read_csv(f)
        p = p[p.year.isin([2012, 2016, 2020, 2024]) & p.party_simplified.isin(["DEMOCRAT", "REPUBLICAN"])]
        _MIT = p.pivot_table(
            index=["year", "state_po"], columns="party_simplified", values="candidatevotes", aggfunc="sum"
        )
    return _MIT


def mit_state(st, y):
    v = mit().loc[(y, st)]
    return (
        100 * (v.DEMOCRAT - v.REPUBLICAN) / (v.DEMOCRAT + v.REPUBLICAN),
        float(v.DEMOCRAT + v.REPUBLICAN),
        float(v.DEMOCRAT),
        float(v.REPUBLICAN),
    )


def blocks(st: str) -> pd.DataFrame:
    """2020 Census blocks of a state: GEOID20, POP20, internal point (attributes only; the zip is deleted after extraction)."""
    pq = B.R / "blocks" / f"{st}.parquet"
    if pq.exists():
        return pd.read_parquet(pq)
    import pyogrio

    p = B.url_get(
        f"https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{FIPS[st]}_tabblock20.zip",
        f"tl_2020_{FIPS[st]}_tabblock20.zip",
    )
    df = pyogrio.read_dataframe(
        f"zip://{p}", read_geometry=False, columns=["GEOID20", "POP20", "INTPTLAT20", "INTPTLON20"]
    )
    df = pd.DataFrame(
        {
            "block": df["GEOID20"],
            "pop": pd.to_numeric(df["POP20"], errors="coerce").fillna(0),
            "lat": pd.to_numeric(df["INTPTLAT20"]),
            "lon": pd.to_numeric(df["INTPTLON20"]),
        }
    )
    pq.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq)
    p.unlink()
    return df


def block_votes(st: str, v, tag="") -> pd.DataFrame:
    """VEST precinct votes -> 2020 blocks (internal point in precinct, split by block population)."""
    import geopandas as gpd

    bl = blocks(st)
    pts = gpd.GeoDataFrame(bl, geometry=gpd.points_from_xy(bl["lon"], bl["lat"]), crs=4269).to_crs(5070)
    v = v.reset_index(drop=True).copy()
    v["pid"] = np.arange(len(v))
    j = gpd.sjoin(pts, v[["pid", "geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    bl = bl.assign(pid=j["pid"].values)
    # precincts that received no block: give their votes to the block nearest their representative point
    have = set(bl["pid"].dropna().astype(int))
    miss = v[~v["pid"].isin(have)]
    extra = []
    if len(miss):
        rp = gpd.GeoDataFrame(miss[["pid", "d", "r"]], geometry=miss.geometry.representative_point(), crs=v.crs)
        nn = gpd.sjoin_nearest(rp, pts[["block", "geometry"]], how="left")
        nn = nn[~nn.index.duplicated()]
        extra = pd.DataFrame({"block": nn["block"].values, "d": nn["d"].values, "r": nn["r"].values})
    bl = bl.dropna(subset=["pid"])
    bl["pid"] = bl["pid"].astype(int)
    pp = bl.groupby("pid")["pop"].transform("sum")
    cnt = bl.groupby("pid")["pop"].transform("size")
    w = np.where(pp > 0, bl["pop"] / pp.where(pp > 0, 1), 1.0 / cnt)
    vv = v.set_index("pid").loc[bl["pid"], ["d", "r"]].values
    out = pd.DataFrame({"block": bl["block"].values, "d": vv[:, 0] * w, "r": vv[:, 1] * w})
    if len(extra):
        out = pd.concat([out, extra], ignore_index=True)
    out = out.groupby("block")[["d", "r"]].sum().reset_index()
    tot_v = float(v["d"].sum() + v["r"].sum())
    out.attrs["coverage"] = QC[f"blocks_{st}{tag}_vote_coverage"] = round(
        float((out["d"].sum() + out["r"].sum()) / tot_v), 5
    )
    return out


def carry_2024(
    bv: pd.DataFrame, cells24: pd.DataFrame, b119: pd.DataFrame, cm24, cm20, state_swing=None
) -> pd.DataFrame:
    """2024 per block: the 2020 block votes moved by the swing (two-party margin change) and turnout ratio of the block's
    (county x 2024 district) cell; cells without 2024 labels fall back to the county's change."""
    x = bv.merge(b119.rename(columns={"cd": "cd119"}), on="block", how="left")
    x["county"] = x["block"].str[:5]
    x["cd119"] = x["cd119"].fillna("0").map(lambda c: str(max(int(c), 1)) if str(c).isdigit() else "1")
    c20 = x.groupby(["county", "cd119"])[["d", "r"]].sum()
    c24 = (
        cells24.assign(county=cells24["county"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5))
        .groupby(["county", "district"])[["d", "r"]]
        .sum()
    )
    c24.index.names = ["county", "cd119"]
    c = c20.join(c24, lsuffix="20", rsuffix="24", how="left").astype(float)
    c["m20"] = 100 * (c["d20"] - c["r20"]) / (c["d20"] + c["r20"])
    c["m24"] = 100 * (c["d24"] - c["r24"]) / (c["d24"] + c["r24"])
    c["swing"] = c["m24"] - c["m20"]
    c["turn"] = (c["d24"] + c["r24"]) / (c["d20"] + c["r20"])

    def z(ser):
        return ser.set_axis(ser.index.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5))

    if cm20 is None:
        cm20 = (pd.Series(dtype=float), pd.Series(dtype=float))
    cs = (z(cm24[0]) - z(cm20[0])).rename("cswing")
    ct = (z(cm24[1]) / z(cm20[1])).rename("cturn")
    c = (
        c.reset_index()
        .merge(cs, left_on="county", right_index=True, how="left")
        .merge(ct, left_on="county", right_index=True, how="left")
    )
    # fallback 1: the 2024 district's own swing (cells whose county code differs between the files, e.g. Kansas City, MO)
    dd = (
        c.groupby("cd119")[["d20", "r20"]]
        .sum()
        .join(cells24.groupby("district")[["d", "r"]].sum().rename(columns={"d": "d24", "r": "r24"}), how="left")
    )
    dd["dswing"] = 100 * (dd["d24"] - dd["r24"]) / (dd["d24"] + dd["r24"]) - 100 * (dd["d20"] - dd["r20"]) / (
        dd["d20"] + dd["r20"]
    )
    dd["dturn"] = (dd["d24"] + dd["r24"]) / (dd["d20"] + dd["r20"])
    c = c.merge(dd[["dswing", "dturn"]], left_on="cd119", right_index=True, how="left")
    bad = c["swing"].isna() | ~np.isfinite(c["turn"]) | (c["d20"] + c["r20"] < 50) | ~c["turn"].between(0.6, 1.6)
    QC.setdefault("cell_invalid_share", []).append(
        round(float((c.loc[bad, "d20"] + c.loc[bad, "r20"]).sum() / (c["d20"] + c["r20"]).sum()), 4)
    )
    fb1 = bad & c["dswing"].notna() & c["dturn"].between(0.6, 1.6)
    c.loc[fb1, "swing"] = c.loc[fb1, "dswing"]
    c.loc[fb1, "turn"] = c.loc[fb1, "dturn"]
    fb = bad & ~fb1
    QC.setdefault("cell_fallback_share", []).append(
        round(float((c.loc[fb, "d20"] + c.loc[fb, "r20"]).sum() / (c["d20"] + c["r20"]).sum()), 4)
    )
    fbc = fb & c["cswing"].notna() & c["cturn"].between(0.75, 1.35)
    c.loc[fbc, "swing"] = c.loc[fbc, "cswing"]
    c.loc[fbc, "turn"] = c.loc[fbc, "cturn"]
    fbs = fb & ~fbc  # last resort: the state's own 2020->24 change (MIT)
    QC.setdefault("cell_state_fallback_share", []).append(
        round(float((c.loc[fbs, "d20"] + c.loc[fbs, "r20"]).sum() / (c["d20"] + c["r20"]).sum()), 4)
    )
    if state_swing is not None:
        c.loc[fbs, "swing"] = state_swing[0]
        c.loc[fbs, "turn"] = state_swing[1]
    c["swing"] = c["swing"].fillna(0.0)
    c["turn"] = c["turn"].replace([np.inf, -np.inf], np.nan).fillna(1.0)
    x = x.merge(c[["county", "cd119", "swing", "turn"]], on=["county", "cd119"], how="left")
    n20 = x["d"] + x["r"]
    m20 = np.where(n20 > 0, (x["d"] - x["r"]) / n20.where(n20 > 0, 1), 0.0)
    m24 = np.clip(m20 + x["swing"].fillna(0) / 100, -1, 1)
    n24 = n20 * x["turn"].fillna(1)
    x["d24"] = n24 * (1 + m24) / 2
    x["r24"] = n24 * (1 - m24) / 2
    return x


def medsl24(st):
    p = B.dv_get("m2024", f"2024-{st.lower()}-precinct-general\\.(tab|csv)")
    return B.medsl_load(p, st) if p is not None else None


def block_county_margin(bv):
    """2020 margin and two-party votes per county from the block votes (block GEOID -> county)."""
    g = bv.assign(county=bv["block"].str[:5]).groupby("county")[["d", "r"]].sum()
    return (100 * (g["d"] - g["r"]) / (g["d"] + g["r"])).rename("m"), (g["d"] + g["r"]).rename("n")


def vest20(st):
    zp = B.dv_get("vest2020", f"{st.lower()}_2020\\.zip")
    return B.vest_votes(zp, "20") if zp is not None else None


def main():
    b119, b120 = bef(119), bef(120)
    m = b119.merge(b120, on="block", how="outer", suffixes=("119", "120"))
    m["st"] = m["block"].str[:2].map(ABBR)
    changed = m.groupby("st").apply(lambda q: float((q["cd119"] != q["cd120"]).mean()))
    redrawn_bef = sorted(changed[changed > 0.001].index)
    QC["redrawn_states_in_bef120"] = {s: round(float(changed[s]), 4) for s in redrawn_bef}
    QC["map_status"] = MAP_STATUS
    # states whose map in force is the 119th-Congress map get the 119th lines (b120 rows replaced by b119)
    keep119 = [st for st, v in MAP_STATUS.items() if v["map"] == 119]
    f119 = [FIPS[st] for st in keep119]
    b120 = pd.concat(
        [b120[~b120["block"].str[:2].isin(f119)], b119[b119["block"].str[:2].isin(f119)]], ignore_index=True
    )
    redrawn = [st for st in redrawn_bef if st not in keep119]
    QC["redrawn_states"] = redrawn
    print("redrawn for 2026 (share of blocks changing district):", QC["redrawn_states"])
    rows = []
    nat = {"d24": 0.0, "r24": 0.0}
    for st in sorted(FIPS):
        try:
            df = medsl24(st)
        except Exception as e:
            print("!!", st, "2024 file", e)
            df = None
        if df is not None and not B.pres_rows(df).shape[0]:
            print(
                f"  !! {st}: no president rows; offices {df['office'].value_counts().head(15).to_dict()}; rows {len(df)}"
            )
        n119 = b119.loc[b119["block"].str[:2] == FIPS[st], "cd"].nunique()
        m24s, t24s, d24s, r24s = mit_state(st, 2024)
        m20s, t20s, _, _ = mit_state(st, 2020)
        if n119 == 1:  # at-large: the state's official totals (MIT)
            rows.append(
                {
                    "seat": f"{st}-1",
                    "pres24_d": d24s,
                    "pres24_r": r24s,
                    "method24": "mit_state",
                    "redrawn": False,
                    "map_verified": True,
                }
            )
            _, _, d20s, r20s = mit_state(st, 2020)
            rows[-1].update({"pres20_d": d20s, "pres20_r": r20s, "method20": "mit_state"})
            continue
        lab = B.label_lean(df, st, "cd", "cd2024") if df is not None else None
        # completeness of the precinct file against the official state totals
        if df is not None:
            pr = B.pres_rows(df)
            fd, fr = pr.loc[pr["party"] == "D", "votes"].sum(), pr.loc[pr["party"] == "R", "votes"].sum()
            fm = 100 * (fd - fr) / max(fd + fr, 1)
            ratio = (fd + fr) / t24s
            QC[f"medsl_vs_mit_{st}"] = {"ratio": round(float(ratio), 4), "margin_diff": round(float(fm - m24s), 3)}
            file_ok = abs(fm - m24s) <= 0.6 and 0.97 <= ratio <= 1.03
        else:
            file_ok = False
        if df is not None:
            pr = B.pres_rows(df)
            nat["d24"] += pr.loc[pr["party"] == "D", "votes"].sum()
            nat["r24"] += pr.loc[pr["party"] == "R", "votes"].sum()
        q = lab.attrs["qc"] if lab is not None else {}
        # county-level tallies spread over a county's districts (WA's King County president rows) give every district the county's
        # margin: labels are trusted only when almost all presidential votes sit on labelled precinct rows
        good = (
            lab is not None
            and (q.get("assigned_vs_total") or 0) >= 0.97
            and (q.get("unlabelled_pres_share") or 0) <= 0.05
            and (q.get("split_precinct_pres_share") or 0) <= 0.05
            and len(lab) == b119.loc[b119["block"].str[:2] == FIPS[st], "cd"].nunique()
        )
        use_blocks = st in redrawn or not good or not file_ok
        if use_blocks:
            try:
                v = vest20(st)
                bv = block_votes(st, v)
                cm20 = block_county_margin(bv)
                cm24 = B.county_margin(df) if df is not None else (pd.Series(dtype=float), pd.Series(dtype=float))
                cells = (
                    lab.attrs["cells"] if lab is not None else pd.DataFrame(columns=["county", "district", "d", "r"])
                )
                cells_ok = (
                    file_ok
                    and (q.get("unlabelled_pres_share") or 0) <= 0.05
                    and (q.get("split_precinct_pres_share") or 0) <= 0.05
                )
                if (
                    not cells_ok
                ):  # labels unreliable: no (county x district) or district swing; county swing where the county's
                    cells = pd.DataFrame(
                        columns=["county", "district", "d", "r"]
                    )  # turnout ratio is plausible, else the state's
                x = carry_2024(
                    bv,
                    cells,
                    b119[b119["block"].str[:2] == FIPS[st]],
                    cm24,
                    cm20,
                    state_swing=(m24s - m20s, t24s / t20s),
                )
                x = x.merge(b120.rename(columns={"cd": "cd120"}), on="block", how="left")
                x["cd120"] = x["cd120"].fillna("0").map(lambda c: str(max(int(c), 1)) if str(c).isdigit() else "1")
                g = x.groupby("cd120")[["d", "r", "d24", "r24"]].sum()
                for cd, r in g.iterrows():
                    rows.append(
                        {
                            "seat": f"{st}-{cd}",
                            "pres24_d": r["d24"],
                            "pres24_r": r["r24"],
                            "pres20_d": r["d"],
                            "pres20_r": r["r"],
                            "method24": "vest2020_blocks_cd120_cellswing"
                            if cells_ok
                            else "vest2020_blocks_cd120_countyswing",
                            "method20": "vest2020_blocks_cd120",
                            "redrawn": st in redrawn,
                            "map_verified": st not in redrawn,
                        }
                    )
                # check of the cell-swing step on the unchanged 2024 districts of the same state (blocks -> CD119 vs labels)
                if lab is not None:
                    chk = x.groupby("cd119")[["d24", "r24"]].sum()
                    chk["m_hat"] = 100 * (chk["d24"] - chk["r24"]) / (chk["d24"] + chk["r24"])
                    ex = lab.set_index("district")
                    ex["m"] = 100 * (ex["d"] - ex["r"]) / (ex["d"] + ex["r"])
                    e = (chk["m_hat"] - ex["m"]).dropna()
                    QC[f"cd119_selfcheck_{st}_rms"] = round(float(np.sqrt((e**2).mean())), 3)
                continue
            except Exception as e:
                import traceback

                traceback.print_exc()
                print(
                    f"!! {st}: block route failed ({e}); labels kept" if lab is not None else f"!! {st}: no route ({e})"
                )
                if lab is None:
                    continue
        for _, r in lab.iterrows():
            rows.append(
                {
                    "seat": f"{st}-{r['district']}",
                    "pres24_d": r["d"],
                    "pres24_r": r["r"],
                    "method24": "medsl2024_cd_labels",
                    "map_verified": True,
                    "unlabelled_share": q.get("unlabelled_pres_share"),
                    "redrawn": False,
                }
            )
        # 2020 on the unchanged map: VEST 2020 areally into TIGER CD119
        try:
            import geopandas as gpd

            p = B.url_get(
                f"https://www2.census.gov/geo/tiger/TIGER2024/CD/tl_2024_{FIPS[st]}_cd119.zip",
                f"tl_2024_{FIPS[st]}_cd119.zip",
            )
            t = gpd.read_file(f"zip://{p}").to_crs(5070)
            t["geometry"] = t.geometry.make_valid()
            t = t.rename(columns={"CD119FP": "code"})[["code", "geometry"]]
            o, cov, _ = B.areal(vest20(st), t, st, "cd")
            QC[f"pres20_areal_{st}_coverage"] = round(cov, 4)
            for _, r in o.iterrows():
                for row in rows:
                    if row["seat"] == f"{st}-{r['district']}":
                        row.update({"pres20_d": r["d"], "pres20_r": r["r"], "method20": "vest2020_areal_cd119"})
        except Exception as e:
            print("!!", st, "pres20", e)
    D = pd.DataFrame(rows)
    # calibrate each state to its official totals (MIT): whatever the route left as a statewide offset (FL +1.3 with the cell
    # swing, VEST 2020 files short of the certified count) is removed by a uniform shift of every seat's margin in the state
    D["st"] = D["seat"].str[:2]
    QC["state_calibration_shift"] = {}
    for y in (2024, 2020):
        dc, rc = f"pres{y % 100}_d", f"pres{y % 100}_r"
        for st, q in D.groupby("st"):
            if q[dc].isna().any():
                continue
            m_off = mit_state(st, y)[0]
            m_ours = 100 * (q[dc].sum() - q[rc].sum()) / (q[dc].sum() + q[rc].sum())
            sh = m_off - m_ours
            if abs(sh) > 0.05:
                n = q[dc] + q[rc]
                m = (q[dc] - q[rc]) / n + sh / 100
                D.loc[q.index, dc] = n * (1 + m) / 2
                D.loc[q.index, rc] = n * (1 - m) / 2
                QC["state_calibration_shift"][f"{st}{y}"] = round(float(sh), 3)
    D = D.drop(columns="st")
    D["pres24_margin"] = 100 * (D["pres24_d"] - D["pres24_r"]) / (D["pres24_d"] + D["pres24_r"])
    D["pres20_margin"] = 100 * (D["pres20_d"] - D["pres20_r"]) / (D["pres20_d"] + D["pres20_r"])
    n24_medsl = 100 * (nat["d24"] - nat["r24"]) / (nat["d24"] + nat["r24"])
    n24 = 100 * (D["pres24_d"].sum() - D["pres24_r"].sum()) / (D["pres24_d"].sum() + D["pres24_r"].sum())
    QC["national_2024_from_medsl_rows"] = round(float(n24_medsl), 3)
    n20 = 100 * (D["pres20_d"].sum() - D["pres20_r"].sum()) / (D["pres20_d"].sum() + D["pres20_r"].sum())
    QC["national_two_party_margin"] = {"2024": round(float(n24), 3), "2020_from_seats": round(float(n20), 3)}
    D["lean_75_25"] = 0.75 * (D["pres24_margin"] - n24) + 0.25 * (D["pres20_margin"] - n20)
    QC["n_seats"] = len(D)
    QC["missing_pres20"] = D.loc[D["pres20_margin"].isna(), "seat"].tolist()
    print(f"cd lean: {len(D)} seats (expect 435); national 2024 {n24:+.2f}, 2020 {n20:+.2f}")
    validate_sld()
    D.round(3).to_csv(STATIC / "cd_lean_2026.csv", index=False)
    (STATIC / "qc_cd.json").write_text(json.dumps(QC, indent=1, sort_keys=True, default=str))
    report()


def validate_sld(states=("PA", "MI", "WI", "NC")):
    """Out-of-sample check of the redrawn-state route: blocks + cell swing on 2024 state legislative districts (whose exact 2024
    presidential votes the labels give), where the districts do not nest in the (county x CD119) swing cells."""
    import geopandas as gpd

    b119 = bef(119)
    for st in states:
        try:
            df = medsl24(st)
            v = vest20(st)
            bv = block_votes(st, v)
            lab = B.label_lean(df, st, "cd", "cd2024_v")
            x = carry_2024(
                bv,
                lab.attrs["cells"],
                b119[b119["block"].str[:2] == FIPS[st]],
                B.county_margin(df),
                block_county_margin(bv),
            )
            bl = blocks(st).merge(x[["block", "d24", "r24", "d", "r"]], on="block", how="inner")
            pts = gpd.GeoDataFrame(bl, geometry=gpd.points_from_xy(bl["lon"], bl["lat"]), crs=4269).to_crs(5070)
            for ch, lay in (("lower", "sldl"), ("upper", "sldu")):
                exact = B.label_lean(df, st, ch, "v_exact")
                if exact is None:
                    continue
                t = B.tiger(2024, B.FIPS[st], lay)
                j = gpd.sjoin(pts, t, how="inner", predicate="within")
                j["district"] = [B.norm_district(st, ch, c) for c in j["code"]]
                g = j.groupby("district")[["d24", "r24", "d", "r"]].sum()
                g["m_hat"] = 100 * (g["d24"] - g["r24"]) / (g["d24"] + g["r24"])
                g["m20"] = 100 * (g["d"] - g["r"]) / (g["d"] + g["r"])
                ex = exact.set_index("district")
                ex["m"] = 100 * (ex["d"] - ex["r"]) / (ex["d"] + ex["r"])
                k = g.join(ex[["m"]], how="inner")
                e = k["m_hat"] - k["m"]
                e0 = k["m20"] - k["m"]
                QC[f"validate_{st}_{ch}"] = {
                    "n": len(k),
                    "rms_cellswing": round(float(np.sqrt((e**2).mean())), 3),
                    "max_abs": round(float(e.abs().max()), 2),
                    "rms_no_swing_2020": round(float(np.sqrt((e0**2).mean())), 3),
                }
                print(
                    f"  validate {st} {ch}: n {len(k)}, rms {np.sqrt((e**2).mean()):.2f} (2020 unadjusted {np.sqrt((e0**2).mean()):.2f})"
                )
        except Exception as ex_:
            print("!! validate", st, ex_)


# ---------------------------------------------------------------- the 2018 and 2022 maps (the cycles the House prior is fitted on)

# cycle -> (recent, previous) presidential election: the 75/25 lean of 2026, from elections held before the cycle
HIST = {2018: (16, 12), 2022: (20, 16)}
VEST_YEARS = (16, 20)
TIGER20 = "https://www2.census.gov/geo/tiger/TIGER2020/CD"
WIKI_PRES = "{} United States presidential election in {}"
NOMINEES = {12: ("Obama", "Romney"), 16: ("Clinton", "Trump"), 20: ("Biden", "Trump")}
# Kansas City reports apart from its counties (Jackson, Clay, Platte) in Missouri's county returns: one swing unit with them.
# Names changed since 2012: Bedford city joined Bedford County, VA (2013); Shannon County, SD, is Oglala Lakota (2015).
KC_UNIT = {"29095": "29KC", "29047": "29KC", "29165": "29KC"}
COUNTY_ALIAS = {("VA", "bedfordcity"): "51019", ("SD", "shannon"): "46102", ("MO", "kansascity"): "29KC"}


def _cd(c):
    c = str(c).strip()
    return str(max(int(c), 1)) if c.isdigit() else None


def bef_blocks(b, st):
    """One state's block -> district rows of a block equivalency table (at-large '00' -> '1'); fails when none has a district."""
    q = b[b["block"].str.startswith(FIPS[st])]
    out = pd.DataFrame({"block": q["block"].values, "cd": q["cd"].map(_cd).values})
    if out["cd"].isna().all():
        raise RuntimeError(f"{st}: no block of the equivalency table has a district ({len(q)} rows, e.g. {q.head(3)})")
    return out


def vest_blocks(st, yy):
    """VEST 20yy precinct votes on the state's 2020 blocks (block_votes), kept as parquet; the precinct file is deleted once read.
    None when VEST has no file for the state."""
    pq = B.R / "blockvotes" / f"{st}_{yy}.parquet"
    if pq.exists():
        bv = pd.read_parquet(pq)
        QC[f"blocks_{st}_{yy}_vote_coverage"] = bv.attrs.get("coverage")
        return bv
    zp = B.dv_get(f"vest20{yy}", f"{st.lower()}(_20{yy})?\\.zip")
    if zp is None:
        return None
    bv = block_votes(st, B.vest_votes(zp, yy), f"_{yy}")
    pq.parent.mkdir(parents=True, exist_ok=True)
    bv.to_parquet(pq)
    zp.unlink()
    return bv


_CD116: dict = {}


def cd116(st):
    """The 116th-Congress districts (the 2018 election's map) of a state, TIGER/Line 2020 (the state file, else the national)."""
    import urllib.error

    import geopandas as gpd

    if st not in _CD116:
        try:
            p = B.url_get(f"{TIGER20}/tl_2020_{FIPS[st]}_cd116.zip", f"tl_2020_{FIPS[st]}_cd116.zip")
            t = gpd.read_file(f"zip://{p}")
        except urllib.error.HTTPError:
            p = B.url_get(f"{TIGER20}/tl_2020_us_cd116.zip", "tl_2020_us_cd116.zip")
            t = gpd.read_file(f"zip://{p}")
            t = t[t["STATEFP"] == FIPS[st]]
        t = t.to_crs(5070)
        _CD116[st] = t[t["CD116FP"].map(_cd).notna()][["CD116FP", "geometry"]]
    return _CD116[st]


def cd116_blocks(st, t):
    """2020 block -> 116th-Congress district (drawn on 2010 blocks) by each block's internal point; the nearest district for the
    few points in none (water)."""
    import geopandas as gpd

    bl = blocks(st)
    pts = gpd.GeoDataFrame(bl[["block"]], geometry=gpd.points_from_xy(bl["lon"], bl["lat"]), crs=4269).to_crs(5070)
    j = gpd.sjoin(pts, t, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    miss = j["CD116FP"].isna().values
    if miss.any():
        nn = gpd.sjoin_nearest(pts[miss], t, how="left")
        j.loc[miss, "CD116FP"] = nn[~nn.index.duplicated()]["CD116FP"].values
    QC[f"cd116_{st}_blocks_outside_districts"] = int(miss.sum())
    return pd.DataFrame({"block": j["block"].values, "cd": j["CD116FP"].map(_cd).values})


def _cname(s):
    """County name -> comparison key ('St. Louis City' and 'St. Louis city' -> 'stlouiscity', 'Doña Ana County' -> 'donaana')."""
    s = unicodedata.normalize("NFKD", re.sub(r"\[.*?\]", "", str(s))).encode("ascii", "ignore").decode().lower().strip()
    s = re.sub(r"^city of (.+)$", r"\1 city", s)
    s = re.sub(r"\b(county|parish|borough|census area|city and borough|municipality)\b", " ", s)
    return re.sub(r"[^a-z]", "", s.replace("saint", "st"))


_NAMES: dict = {}


def county_fips(st, name):
    """FIPS of a county named as on a Wikipedia results page (Census 2020 county list, public domain); None if unknown."""
    if not _NAMES:
        b = B.url_get(
            "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt", "national_county2020.txt"
        ).read_bytes()
        try:
            txt = b.decode("utf-8")
        except UnicodeDecodeError:
            txt = b.decode("latin-1")
        c = pd.read_csv(io.StringIO(txt), sep="|", dtype=str)
        _NAMES.update(
            {(s, _cname(n)): f + g for s, f, g, n in zip(c["STATE"], c["STATEFP"], c["COUNTYFP"], c["COUNTYNAME"])}
        )
    k = _cname(name)
    return COUNTY_ALIAS.get((st, k)) or _NAMES.get((st, k)) or _NAMES.get((st, k + "city"))


def wiki_county(st, yy):
    """County D and R presidential votes of 20yy from the Wikipedia state results page (CC BY-SA 4.0; the county tables give the
    certified results), checked against the official state totals (MIT): -> county, d, r, or None when the page has no usable
    table, a county name is unknown or the matched counties miss the state's D or R total by more than 1 %."""
    f = B.R / "wiki" / f"pres20{yy}_{st}.csv"
    if f.exists():
        out = pd.read_csv(f, dtype={"county": str})
    else:
        from . import wiki_polls as W

        name = "Washington (state)" if st == "WA" else W.STATES[st]
        try:
            html = W.fetch(WIKI_PRES.format(2000 + int(yy), name), max_age_h=24 * 365)
        except Exception as e:
            print(f"  !! wiki {st} 20{yy}: {e}")
            return None
        dn, rn = NOMINEES[int(yy)]
        best = None
        for t in pd.read_html(io.StringIO(html)):
            cols = [" ".join(str(x) for x in c) if isinstance(c, tuple) else str(c) for c in t.columns]
            if not re.search(r"county|parish|borough", cols[0], re.I):  # not the by-municipality tables
                continue
            dc = [i for i, c in enumerate(cols) if dn in c and re.search(r"#|votes", c, re.I)]
            rc = [i for i, c in enumerate(cols) if rn in c and re.search(r"#|votes", c, re.I)]
            if dc and rc and (best is None or len(t) > len(best)):
                best = pd.DataFrame(
                    {"name": t.iloc[:, 0].astype(str), "d": _num(t.iloc[:, dc[0]]), "r": _num(t.iloc[:, rc[0]])}
                )
        if best is None:
            print(f"  !! wiki {st} 20{yy}: no county table")
            return None
        out = best[~best["name"].str.contains("total", case=False) & best["d"].notna() & best["r"].notna()].copy()
        out["county"] = [county_fips(st, n) for n in out["name"]]
        f.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(f, index=False)
    _, _, d_off, r_off = mit_state(st, 2000 + int(yy))
    known = out["county"].notna()
    q = {
        "rows": len(out),
        "unknown_names": out.loc[~known, "name"].tolist(),
        "duplicate_fips": sorted(out.loc[known & out["county"].duplicated(keep=False), "county"].unique()),
        "d_vs_official": round(float(out.loc[known, "d"].sum() / d_off), 4),
        "r_vs_official": round(float(out.loc[known, "r"].sum() / r_off), 4),
    }
    q["used"] = bool(
        not q["unknown_names"]
        and not q["duplicate_fips"]
        and abs(q["d_vs_official"] - 1) <= 0.01
        and abs(q["r_vs_official"] - 1) <= 0.01
    )
    QC[f"wiki_county_{st}_20{yy}"] = q
    return out.loc[known, ["county", "d", "r"]] if q["used"] else None


def _num(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[,\s]", "", regex=True), errors="coerce")


def county_carry(bv, ct, state_change):
    """Another election on the blocks: each block's two-party margin moved by its county's margin change from the block votes `bv`
    to the county totals `ct` (county, d, r), its votes scaled by the county's turnout ratio. Counties without a total, or with a
    turnout ratio outside 0.75-1.35, take the state's change state_change = (swing in points, turnout ratio). -> block, d, r"""
    unit = bv["block"].str[:5]
    if ct is not None and "29KC" in set(ct["county"]):
        unit = unit.replace(KC_UNIT)
        ct = ct.assign(county=ct["county"].replace(KC_UNIT))
    g0 = bv.groupby(unit.values)[["d", "r"]].sum()
    g1 = (ct if ct is not None else pd.DataFrame(columns=["county", "d", "r"])).groupby("county")[["d", "r"]].sum()
    g1 = g1.reindex(g0.index).astype(float)
    sw = 100 * ((g1["d"] - g1["r"]) / (g1["d"] + g1["r"]) - (g0["d"] - g0["r"]) / (g0["d"] + g0["r"]))
    tu = (g1["d"] + g1["r"]) / (g0["d"] + g0["r"])
    ok = sw.notna() & tu.between(0.75, 1.35)
    sw = sw.where(ok, state_change[0])
    tu = tu.where(ok, state_change[1])
    n = bv["d"] + bv["r"]
    m = np.where(n > 0, (bv["d"] - bv["r"]) / n.where(n > 0, 1), 0.0)
    m1 = np.clip(m + unit.map(sw).values / 100, -1, 1)
    n1 = n.values * unit.map(tu).values
    out = pd.DataFrame({"block": bv["block"].values, "d": n1 * (1 + m1) / 2, "r": n1 * (1 - m1) / 2})
    out.attrs["state_change_share"] = round(float((g0.loc[~ok, "d"] + g0.loc[~ok, "r"]).sum() / n.sum()), 4)
    return out


def seat_sums(bv, bd):
    """Block votes -> (district totals, votes on blocks the map assigns to no district)."""
    x = bv.merge(bd, on="block", how="left")
    lost = x["cd"].isna()
    return x[~lost].groupby("cd")[["d", "r"]].sum(), round(float((x.loc[lost, "d"] + x.loc[lost, "r"]).sum()), 1)


def state_change(st, y_from, y_to):
    """(swing, turnout ratio) of the state's official two-party presidential vote between two elections (MIT)."""
    a, b = mit_state(st, 2000 + y_from), mit_state(st, 2000 + y_to)
    return b[0] - a[0], b[1] / a[1]


def build_hist(states=None):
    """The 75/25 lean on the maps of the 2018 and 2022 elections -> data/static/cd_lean_hist.csv, qc_cd_hist.json.
    Recent and previous presidential votes on the cycle's map: VEST precincts -> 2020 blocks -> districts; 2012, which VEST does
    not cover, is the 2016 block votes carried to 2012 by each county's 2012-16 change (Wikipedia county results). A state with no
    VEST file for a year gets the other VEST year carried the same way. Each state is calibrated to its official totals (MIT)."""
    QC.clear()
    b118 = bef(118)
    rows = []
    for cycle, (yn, yo) in HIST.items():
        tag = "cd116" if cycle == 2018 else "cd118"
        for st in states or sorted(FIPS):
            if cycle == 2022:  # the 118th-Congress block equivalency file
                bd = bef_blocks(b118, st)
                n_dist = bd["cd"].nunique()
            else:
                t = cd116(st)
                n_dist = len(t)
            if n_dist == 1:  # at-large: the state's official totals
                row = {"cycle": cycle, "seat": f"{st}-1"}
                for yy in (yn, yo):
                    _, _, d, r = mit_state(st, 2000 + yy)
                    row.update({f"pres{yy}_d": d, f"pres{yy}_r": r, f"method{yy}": "mit_state"})
                rows.append(row)
                continue
            if cycle == 2018:
                bd = cd116_blocks(st, t)
            got, how = {}, {}
            for yy in (yn, yo):
                if yy in VEST_YEARS and (bv := vest_blocks(st, yy)) is not None:
                    got[yy], how[yy] = bv, f"vest20{yy}_blocks_{tag}"
            for yy in (yn, yo):  # no VEST file: the nearest VEST year carried by the county change (2016 before 2012)
                if yy in got:
                    continue
                base = {12: 16, 16: 20, 20: 16}[yy]
                bv = got[base] if base in got else vest_blocks(st, base)
                if bv is None:
                    print(f"!! {cycle} {st}: no precinct votes for 20{yy}")
                    continue
                got[yy] = county_carry(bv, wiki_county(st, yy), state_change(st, base, yy))
                how[yy] = f"vest20{base}_blocks_wiki20{yy}_countyswing_{tag}"
                QC[f"countyswing_{st}_{base}_to_{yy}_state_change_share"] = got[yy].attrs["state_change_share"]
            seats = {}
            for yy, bv in got.items():
                g, lost = seat_sums(bv, bd)
                if len(g) != n_dist or not (g["d"] + g["r"]).gt(0).all():  # the block votes and the map do not join
                    raise RuntimeError(
                        f"{cycle} {st} 20{yy}: votes reach {len(g)} of {n_dist} districts ({lost:.0f} unassigned)"
                    )
                QC[f"unassigned_votes_{cycle}_{st}_{yy}"] = lost
                d_, r_ = g["d"].sum(), g["r"].sum()
                m_off, n_off, _, _ = mit_state(st, 2000 + yy)
                QC[f"blocks_vs_mit_{cycle}_{st}_{yy}"] = {
                    "ratio": round(float((d_ + r_) / n_off), 4),
                    "margin_diff": round(float(100 * (d_ - r_) / (d_ + r_) - m_off), 3),
                }
                for cd, r in g.iterrows():
                    seats.setdefault(cd, {"cycle": cycle, "seat": f"{st}-{cd}"}).update(
                        {f"pres{yy}_d": r["d"], f"pres{yy}_r": r["r"], f"method{yy}": how[yy]}
                    )
            rows.extend(seats.values())
            if cycle == 2018 and how.get(16, "").startswith("vest2016") and (b20 := vest_blocks(st, 20)) is not None:
                # the county-swing step tested where both years are known: 2016 rebuilt from the 2020 blocks and 2016 county
                # totals, against the 2016 precinct votes, on the same districts
                cty = got[16].assign(county=got[16]["block"].str[:5]).groupby("county")[["d", "r"]].sum().reset_index()
                g, _ = seat_sums(county_carry(b20, cty, state_change(st, 20, 16)), bd)
                ex, _ = seat_sums(got[16], bd)
                e = 100 * (g["d"] - g["r"]) / (g["d"] + g["r"]) - 100 * (ex["d"] - ex["r"]) / (ex["d"] + ex["r"])
                QC.setdefault("countyswing_check_2016_from_2020_cd116", {})[st] = [round(float(v), 2) for v in e]
            print(f"  {cycle} {st}: {len(seats)} districts ({', '.join(f'20{y} {how[y]}' for y in sorted(how))})")
    votes = [f"pres{yy}{s}" for yy in (12, 16, 20) for s in ("_d", "_r")]
    D = pd.DataFrame(rows).reindex(columns=["cycle", "seat", *votes, *(f"method{yy}" for yy in (12, 16, 20))])
    D["st"] = D["seat"].str[:2]
    QC["state_calibration_shift"] = {}
    for (cycle, st), q in D.groupby(["cycle", "st"]):
        for yy in HIST[cycle]:
            dc, rc = f"pres{yy}_d", f"pres{yy}_r"
            if q[dc].isna().any():
                continue
            sh = mit_state(st, 2000 + yy)[0] - 100 * (q[dc].sum() - q[rc].sum()) / (q[dc].sum() + q[rc].sum())
            if abs(sh) > 0.05:
                n = q[dc] + q[rc]
                m = (q[dc] - q[rc]) / n + sh / 100
                D.loc[q.index, dc] = n * (1 + m) / 2
                D.loc[q.index, rc] = n * (1 - m) / 2
                QC["state_calibration_shift"][f"{cycle}_{st}_20{yy}"] = round(float(sh), 3)
    D = D.drop(columns="st")
    QC["national_two_party_margin"] = {}
    for cycle, (yn, yo) in HIST.items():
        k = D["cycle"] == cycle
        nat = {}
        for yy in (yn, yo):
            d_, r_ = D.loc[k, f"pres{yy}_d"], D.loc[k, f"pres{yy}_r"]
            D.loc[k, f"pres{yy}_margin"] = 100 * (d_ - r_) / (d_ + r_)
            nat[yy] = 100 * (d_.sum() - r_.sum()) / (d_.sum() + r_.sum())
        D.loc[k, "lean_75_25"] = 0.75 * (D.loc[k, f"pres{yn}_margin"] - nat[yn]) + 0.25 * (
            D.loc[k, f"pres{yo}_margin"] - nat[yo]
        )
        QC["national_two_party_margin"][str(cycle)] = {f"20{y}": round(float(v), 3) for y, v in nat.items()}
        QC[f"n_seats_{cycle}"] = int(k.sum())
        QC[f"missing_{cycle}"] = D.loc[k & D["lean_75_25"].isna(), "seat"].tolist()
    e = [v for s in QC.get("countyswing_check_2016_from_2020_cd116", {}).values() for v in s]
    if e:
        QC["countyswing_check_rms"] = {"n": len(e), "rms": round(float(np.sqrt(np.mean(np.square(e)))), 3)}
    compare_538(D)
    cols = ["cycle", "seat", *votes, *(f"pres{yy}_margin" for yy in (12, 16, 20))]
    cols += [*(f"method{yy}" for yy in (12, 16, 20)), "lean_75_25"]
    D = D.reindex(columns=cols).sort_values(
        ["cycle", "seat"], key=lambda s: s.map(_seat_key) if s.name == "seat" else s
    )
    print(f"cd lean hist: {QC['n_seats_2018']} / {QC['n_seats_2022']} seats (expect 435 / 435)")
    D.round(3).to_csv(STATIC / "cd_lean_hist.csv", index=False)
    (STATIC / "qc_cd_hist.json").write_text(json.dumps(QC, indent=1, sort_keys=True, default=str))
    report()


def _seat_key(seat):
    st, n = seat.split("-")
    return f"{st}{int(n):03d}"


def compare_538(D):
    """Agreement with the leans 538 published for the same maps (CC BY 4.0): its 2018 partisan lean (which also weights state
    legislative results) and the PVI column of its 2022 urbanization index, both in margin points."""
    from .bootstrap import GH

    p = pd.read_csv(io.BytesIO(open_url(GH + "partisan-lean/2018/fivethirtyeight_partisan_lean_DISTRICTS.csv")))
    v = p.iloc[:, 1].astype(str)
    sign = np.where(v.str.startswith("D"), 1, -1)
    lean18 = pd.Series(
        sign * pd.to_numeric(v.str.extract(r"([\d.]+)", expand=False)).values,
        index=p.iloc[:, 0].str.replace(
            r"^([A-Z]{2})-0*(\d+)$", lambda m: f"{m.group(1)}-{int(m.group(2))}", regex=True
        ),
    )
    u = pd.read_csv(io.BytesIO(open_url(GH + "district-urbanization-index-2022/urbanization-index-2022.csv")))
    lean22 = pd.Series(u["pvi_22"].astype(float).values, index=u["state"] + "-" + u["cd"].astype(int).astype(str))
    for cycle, ref, name in ((2018, lean18, "538_partisan_lean_2018"), (2022, lean22, "538_pvi_22")):
        ours = D[D["cycle"] == cycle].set_index("seat")["lean_75_25"]
        k = ours.index.intersection(ref.index)
        e = ours[k] - ref[k]
        QC[f"vs_{name}"] = {
            "n": len(k),
            "rms": round(float(np.sqrt((e**2).mean())), 3),
            "mean": round(float(e.mean()), 3),
            "r": round(float(np.corrcoef(ours[k], ref[k])[0, 1]), 5),
            "largest": {s: round(float(x), 1) for s, x in e.abs().sort_values().tail(8).items()},
        }


if __name__ == "__main__":
    if sys.argv[1:2] == ["hist"]:
        build_hist(sys.argv[2:] or None)
    else:
        main()
