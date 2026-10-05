"""Our own congressional-district presidential lean on the 2026 maps (all 435 seats), from public data only - a replacement
candidate for Cook PVI (proprietary). One-time build in GitHub Actions (.github/workflows/stateleg-data.yml, step `cd`);
NOT used by the House model unless MIDTERMS_CD_LEAN=ours.

Method
  * Which states changed their map for 2026: the Census 119th- vs 120th-Congress block equivalency files (block -> district),
    compared block by block (population-weighted).
  * Unchanged states: 2024 president aggregated by each precinct's 2024 U.S. House district label (MEDSL 2024 precinct returns,
    CC0) - split precincts divided by U.S. House ballots in each part; county-level absentee/early rows spread over the county's
    districts pro rata.
  * Redrawn states (and any state whose labels cover < 97 % of its presidential vote): VEST 2020 precinct shapes (CC BY 4.0) ->
    2020 Census blocks (block internal point inside the precinct; precinct votes split by block population, TIGER 2020 POP20) ->
    120th-Congress districts through the block equivalency file = 2020 president on the new map, EXACT up to precinct splits.
    2024 is then carried to each block by the swing and turnout change of its (county x 2024 district) cell, measured on the
    MEDSL 2024 labels against the same cell's 2020 block total. Validation: the same block route applied to the 2024 state
    legislative districts of the state-leg build (exact 2024 values known from labels) - see qc_cd.json.
  * 2020 president on unchanged maps: VEST 2020 shapes areally interpolated into TIGER 2024 CD119 districts.
Output: data/static/cd_lean_2026.csv (seat, pres24/pres20 votes and two-party margins, method, QC), data/static/qc_cd.json.
    python -m midterms.cd_lean_build
"""
from __future__ import annotations

import io, json, re, sys, zipfile
from pathlib import Path
import numpy as np, pandas as pd
from . import stateleg_build as B
from .fetch import open_url, report
from .paths import STATIC

ABBR = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "12": "FL", "13": "GA", "15": "HI",
        "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI",
        "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC",
        "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
        "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY"}
FIPS = {v: k for k, v in ABBR.items()}
QC: dict = {}
MAPF = "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/"


def _crawl(url, depth, pat, seen=None):
    seen = seen if seen is not None else set(); out = []
    if depth < 0 or url in seen: return out
    seen.add(url)
    try: h = open_url(url, timeout=60).decode("utf-8", "replace")
    except Exception: return out
    for href in re.findall(r'href="([^"?#]+)"', h):
        if href.startswith(("/", "http")) or href.startswith(".."): continue
        u = url + href
        if href.endswith("/"):
            if re.search(r"20(2[3-9])|congress|cd1|119|120", href, re.I): out += _crawl(u, depth - 1, pat, seen)
        elif re.search(pat, href, re.I): out.append(u)
    return out


def bef(n: int) -> pd.DataFrame:
    """Block -> district (CDFP) for the n-th Congress, from the Census block equivalency files."""
    pq = B.R / "bef" / f"cd{n}.parquet"
    if pq.exists(): return pd.read_parquet(pq)
    urls = _crawl(MAPF, 3, rf"cd_?{n}.*\.(zip|txt)$")
    print(f"  BEF {n}: {urls[:6]}")
    if not urls: raise RuntimeError(f"no CD{n} block equivalency file found under {MAPF}")
    nat = [u for u in urls if "national" in u.lower()] or urls
    frames = []
    for u in nat[:1] if [u for u in urls if "national" in u.lower()] else urls:
        p = B.url_get(u, Path(u).name)
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as z:
                for nm in z.namelist():
                    if nm.lower().endswith((".txt", ".csv")): frames.append(pd.read_csv(z.open(nm), dtype=str, sep=None, engine="python"))
        else: frames.append(pd.read_csv(p, dtype=str, sep=None, engine="python"))
    b = pd.concat(frames, ignore_index=True); b.columns = [c.upper() for c in b.columns]
    gcol = next(c for c in b.columns if c in ("GEOID", "BLOCKID", "GEOID20")); dcol = next(c for c in b.columns if c.startswith("CD") or c == "DISTRICT")
    b = b.rename(columns={gcol: "block", dcol: "cd"})[["block", "cd"]]
    b = b[~b["cd"].isin(["ZZ", "98", ""])]; pq.parent.mkdir(parents=True, exist_ok=True); b.to_parquet(pq)
    print(f"  BEF {n}: {len(b)} blocks, columns {gcol}/{dcol}")
    return b


def blocks(st: str) -> pd.DataFrame:
    """2020 Census blocks of a state: GEOID20, POP20, internal point (attributes only; the zip is deleted after extraction)."""
    pq = B.R / "blocks" / f"{st}.parquet"
    if pq.exists(): return pd.read_parquet(pq)
    import pyogrio
    p = B.url_get(f"https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{FIPS[st]}_tabblock20.zip", f"tl_2020_{FIPS[st]}_tabblock20.zip")
    df = pyogrio.read_dataframe(f"zip://{p}", read_geometry=False, columns=["GEOID20", "POP20", "INTPTLAT20", "INTPTLON20"])
    df = pd.DataFrame({"block": df["GEOID20"], "pop": pd.to_numeric(df["POP20"], errors="coerce").fillna(0),
                       "lat": pd.to_numeric(df["INTPTLAT20"]), "lon": pd.to_numeric(df["INTPTLON20"])})
    pq.parent.mkdir(parents=True, exist_ok=True); df.to_parquet(pq); p.unlink()
    return df


def block_votes(st: str, v) -> pd.DataFrame:
    """VEST 2020 precinct votes -> blocks (internal point in precinct, split by block population)."""
    import geopandas as gpd
    bl = blocks(st)
    pts = gpd.GeoDataFrame(bl, geometry=gpd.points_from_xy(bl["lon"], bl["lat"]), crs=4269).to_crs(5070)
    v = v.reset_index(drop=True).copy(); v["pid"] = np.arange(len(v))
    j = gpd.sjoin(pts, v[["pid", "geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated()]
    bl = bl.assign(pid=j["pid"].values)
    # precincts that received no block: give their votes to the block nearest their representative point
    have = set(bl["pid"].dropna().astype(int)); miss = v[~v["pid"].isin(have)]
    extra = []
    if len(miss):
        rp = gpd.GeoDataFrame(miss[["pid", "d", "r"]], geometry=miss.geometry.representative_point(), crs=v.crs)
        nn = gpd.sjoin_nearest(rp, pts[["block", "geometry"]], how="left")
        nn = nn[~nn.index.duplicated()]
        extra = pd.DataFrame({"block": nn["block"].values, "d": nn["d"].values, "r": nn["r"].values})
    bl = bl.dropna(subset=["pid"]); bl["pid"] = bl["pid"].astype(int)
    pp = bl.groupby("pid")["pop"].transform("sum"); cnt = bl.groupby("pid")["pop"].transform("size")
    w = np.where(pp > 0, bl["pop"] / pp.where(pp > 0, 1), 1.0 / cnt)
    vv = v.set_index("pid").loc[bl["pid"], ["d", "r"]].values
    out = pd.DataFrame({"block": bl["block"].values, "d": vv[:, 0] * w, "r": vv[:, 1] * w})
    if len(extra): out = pd.concat([out, extra], ignore_index=True)
    out = out.groupby("block")[["d", "r"]].sum().reset_index()
    tot_v = float(v["d"].sum() + v["r"].sum()); QC[f"blocks_{st}_vote_coverage"] = round(float((out["d"].sum() + out["r"].sum()) / tot_v), 5)
    return out


def carry_2024(bv: pd.DataFrame, cells24: pd.DataFrame, b119: pd.DataFrame, cm24, cm20) -> pd.DataFrame:
    """2024 per block: the 2020 block votes moved by the swing (two-party margin change) and turnout ratio of the block's
    (county x 2024 district) cell; cells without 2024 labels fall back to the county's change."""
    x = bv.merge(b119.rename(columns={"cd": "cd119"}), on="block", how="left")
    x["county"] = x["block"].str[:5]; x["cd119"] = x["cd119"].fillna("0").map(lambda c: str(max(int(c), 1)) if str(c).isdigit() else "1")
    c20 = x.groupby(["county", "cd119"])[["d", "r"]].sum()
    c24 = cells24.assign(county=cells24["county"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)).groupby(["county", "district"])[["d", "r"]].sum()
    c24.index.names = ["county", "cd119"]
    c = c20.join(c24, lsuffix="20", rsuffix="24", how="left")
    c["m20"] = 100 * (c["d20"] - c["r20"]) / (c["d20"] + c["r20"]); c["m24"] = 100 * (c["d24"] - c["r24"]) / (c["d24"] + c["r24"])
    c["swing"] = c["m24"] - c["m20"]; c["turn"] = (c["d24"] + c["r24"]) / (c["d20"] + c["r20"])
    z = lambda ser: ser.set_axis(ser.index.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5))
    if cm20 is None: cm20 = (pd.Series(dtype=float), pd.Series(dtype=float))
    cs = (z(cm24[0]) - z(cm20[0])).rename("cswing"); ct = (z(cm24[1]) / z(cm20[1])).rename("cturn")
    c = c.reset_index().merge(cs, left_on="county", right_index=True, how="left").merge(ct, left_on="county", right_index=True, how="left")
    fb = c["swing"].isna() | ~np.isfinite(c["turn"]) | (c["d20"] + c["r20"] < 50)
    QC.setdefault("cell_fallback_share", []).append(round(float((c.loc[fb, "d20"] + c.loc[fb, "r20"]).sum() / (c["d20"] + c["r20"]).sum()), 4))
    c.loc[fb, "swing"] = c.loc[fb, "cswing"]; c.loc[fb, "turn"] = c.loc[fb, "cturn"]
    c["swing"] = c["swing"].fillna(0.0); c["turn"] = c["turn"].replace([np.inf, -np.inf], np.nan).fillna(1.0)
    x = x.merge(c[["county", "cd119", "swing", "turn"]], on=["county", "cd119"], how="left")
    n20 = x["d"] + x["r"]; m20 = np.where(n20 > 0, (x["d"] - x["r"]) / n20.where(n20 > 0, 1), 0.0)
    m24 = np.clip(m20 + x["swing"].fillna(0) / 100, -1, 1); n24 = n20 * x["turn"].fillna(1)
    x["d24"] = n24 * (1 + m24) / 2; x["r24"] = n24 * (1 - m24) / 2
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
    redrawn = sorted(changed[changed > 0.001].index); QC["redrawn_states"] = {s: round(float(changed[s]), 4) for s in redrawn}
    print("redrawn for 2026 (share of blocks changing district):", QC["redrawn_states"])
    rows = []; nat = {"d24": 0.0, "r24": 0.0}
    cd119 = None
    for st in sorted(FIPS):
        try: df = medsl24(st)
        except Exception as e: print("!!", st, "2024 file", e); df = None
        n119 = b119.loc[b119["block"].str[:2] == FIPS[st], "cd"].nunique()
        if df is not None and n119 == 1:                                  # at-large: the whole state
            pr = B.pres_rows(df); d_, r_ = pr.loc[pr["party"] == "D", "votes"].sum(), pr.loc[pr["party"] == "R", "votes"].sum()
            lab = pd.DataFrame({"district": ["1"], "d": [d_], "r": [r_]}); lab.attrs["qc"] = {"assigned_vs_total": 1.0, "unlabelled_pres_share": 0.0}
            lab.attrs["cells"] = pd.DataFrame(columns=["county", "district", "d", "r"])
        else:
            lab = B.label_lean(df, st, "cd", "cd2024") if df is not None else None
        if df is not None:
            pr = B.pres_rows(df); nat["d24"] += pr.loc[pr["party"] == "D", "votes"].sum(); nat["r24"] += pr.loc[pr["party"] == "R", "votes"].sum()
        n_cd = b120.loc[b120["block"].str[:2] == FIPS[st], "cd"].nunique()
        q = lab.attrs["qc"] if lab is not None else {}
        good = lab is not None and (q.get("assigned_vs_total") or 0) >= 0.97 and len(lab) == b119.loc[b119["block"].str[:2] == FIPS[st], "cd"].nunique()
        use_blocks = st in redrawn or not good
        r20 = None
        if use_blocks:
            try:
                v = vest20(st); bv = block_votes(st, v)
                cm20 = block_county_margin(bv)
                cm24 = B.county_margin(df)
                cells = lab.attrs["cells"] if lab is not None else pd.DataFrame(columns=["county", "district", "d", "r"])
                x = carry_2024(bv, cells, b119[b119["block"].str[:2] == FIPS[st]], cm24, cm20)
                x = x.merge(b120.rename(columns={"cd": "cd120"}), on="block", how="left")
                x["cd120"] = x["cd120"].fillna("0").map(lambda c: str(max(int(c), 1)) if str(c).isdigit() else "1")
                g = x.groupby("cd120")[["d", "r", "d24", "r24"]].sum()
                for cd, r in g.iterrows():
                    rows.append({"seat": f"{st}-{cd}", "pres24_d": r["d24"], "pres24_r": r["r24"], "pres20_d": r["d"], "pres20_r": r["r"],
                                 "method24": "vest2020_blocks_cd120_cellswing", "method20": "vest2020_blocks_cd120", "redrawn": st in redrawn})
                # check of the cell-swing step on the UNCHANGED 2024 districts of the same state (blocks -> CD119 vs labels)
                if lab is not None:
                    chk = x.groupby("cd119")[["d24", "r24"]].sum(); chk["m_hat"] = 100 * (chk["d24"] - chk["r24"]) / (chk["d24"] + chk["r24"])
                    ex = lab.set_index("district"); ex["m"] = 100 * (ex["d"] - ex["r"]) / (ex["d"] + ex["r"])
                    e = (chk["m_hat"] - ex["m"]).dropna(); QC[f"cd119_selfcheck_{st}_rms"] = round(float(np.sqrt((e ** 2).mean())), 3)
                continue
            except Exception as e:
                print(f"!! {st}: block route failed ({e}); labels kept" if lab is not None else f"!! {st}: no route ({e})")
                if lab is None: continue
        for _, r in lab.iterrows():
            rows.append({"seat": f"{st}-{r['district']}", "pres24_d": r["d"], "pres24_r": r["r"], "method24": "medsl2024_cd_labels",
                         "unlabelled_share": q.get("unlabelled_pres_share"), "redrawn": False})
        # 2020 on the unchanged map: VEST 2020 areally into TIGER CD119
        try:
            if cd119 is None:
                import geopandas as gpd
                cd119 = True
            import geopandas as gpd
            p = B.url_get(f"https://www2.census.gov/geo/tiger/TIGER2024/CD/tl_2024_{FIPS[st]}_cd119.zip", f"tl_2024_{FIPS[st]}_cd119.zip")
            t = gpd.read_file(f"zip://{p}").to_crs(5070); t["geometry"] = t.geometry.make_valid()
            t = t.rename(columns={"CD119FP": "code"})[["code", "geometry"]]
            o, cov, _ = B.areal(vest20(st), t, st, "cd")
            QC[f"pres20_areal_{st}_coverage"] = round(cov, 4)
            for _, r in o.iterrows():
                for row in rows:
                    if row["seat"] == f"{st}-{r['district']}": row.update({"pres20_d": r["d"], "pres20_r": r["r"], "method20": "vest2020_areal_cd119"})
        except Exception as e: print("!!", st, "pres20", e)
    D = pd.DataFrame(rows)
    D["pres24_margin"] = 100 * (D["pres24_d"] - D["pres24_r"]) / (D["pres24_d"] + D["pres24_r"])
    D["pres20_margin"] = 100 * (D["pres20_d"] - D["pres20_r"]) / (D["pres20_d"] + D["pres20_r"])
    n24 = 100 * (nat["d24"] - nat["r24"]) / (nat["d24"] + nat["r24"])
    n20 = 100 * (D["pres20_d"].sum() - D["pres20_r"].sum()) / (D["pres20_d"].sum() + D["pres20_r"].sum())
    QC["national_two_party_margin"] = {"2024": round(float(n24), 3), "2020_from_seats": round(float(n20), 3)}
    D["lean_75_25"] = 0.75 * (D["pres24_margin"] - n24) + 0.25 * (D["pres20_margin"] - n20)
    QC["n_seats"] = int(len(D)); QC["missing_pres20"] = D.loc[D["pres20_margin"].isna(), "seat"].tolist()
    print(f"cd lean: {len(D)} seats (expect 435); national 2024 {n24:+.2f}, 2020 {n20:+.2f}")
    validate_sld()
    D.round(3).to_csv(STATIC / "cd_lean_2026.csv", index=False)
    (STATIC / "qc_cd.json").write_text(json.dumps(QC, indent=1, sort_keys=True, default=str))
    report()


def validate_sld(states=("PA", "MI", "WI", "NC")):
    """Out-of-sample check of the redrawn-state route: blocks + cell swing on 2024 STATE LEGISLATIVE districts (whose exact 2024
    presidential votes the labels give), where the districts do not nest in the (county x CD119) swing cells."""
    import geopandas as gpd
    b119 = bef(119)
    for st in states:
        try:
            df = medsl24(st); v = vest20(st); bv = block_votes(st, v)
            lab = B.label_lean(df, st, "cd", "cd2024_v")
            x = carry_2024(bv, lab.attrs["cells"], b119[b119["block"].str[:2] == FIPS[st]], B.county_margin(df), block_county_margin(bv))
            bl = blocks(st).merge(x[["block", "d24", "r24", "d", "r"]], on="block", how="inner")
            pts = gpd.GeoDataFrame(bl, geometry=gpd.points_from_xy(bl["lon"], bl["lat"]), crs=4269).to_crs(5070)
            for ch, lay in (("lower", "sldl"), ("upper", "sldu")):
                exact = B.label_lean(df, st, ch, "v_exact")
                if exact is None: continue
                t = B.tiger(2024, B.FIPS[st], lay); j = gpd.sjoin(pts, t, how="inner", predicate="within")
                j["district"] = [B.norm_district(st, ch, c) for c in j["code"]]
                g = j.groupby("district")[["d24", "r24", "d", "r"]].sum(); g["m_hat"] = 100 * (g["d24"] - g["r24"]) / (g["d24"] + g["r24"])
                g["m20"] = 100 * (g["d"] - g["r"]) / (g["d"] + g["r"])
                ex = exact.set_index("district"); ex["m"] = 100 * (ex["d"] - ex["r"]) / (ex["d"] + ex["r"])
                k = g.join(ex[["m"]], how="inner"); e = k["m_hat"] - k["m"]; e0 = k["m20"] - k["m"]
                QC[f"validate_{st}_{ch}"] = {"n": int(len(k)), "rms_cellswing": round(float(np.sqrt((e ** 2).mean())), 3), "max_abs": round(float(e.abs().max()), 2),
                                             "rms_no_swing_2020": round(float(np.sqrt((e0 ** 2).mean())), 3)}
                print(f"  validate {st} {ch}: n {len(k)}, rms {np.sqrt((e ** 2).mean()):.2f} (2020 unadjusted {np.sqrt((e0 ** 2).mean()):.2f})")
        except Exception as ex_: print("!! validate", st, ex_)


if __name__ == "__main__":
    main()
