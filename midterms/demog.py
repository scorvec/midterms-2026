"""Citizen voting-age population (CVAP) by Hispanic-origin and Asian-origin group, per 2026 House district.

Why groups: Hispanic and Asian voters are not one bloc. Cuban/Venezuelan South Florida, Tejano south
Texas, Puerto Rican Orlando and Dominican New Jersey swing differently, as do Chinese, Indian,
Vietnamese and Filipino voters. The seat model needs each district's loading on each group.

Sources (all Census, all bulk files; the Census API now needs a key):
  ACS 2020-2024 5-year table-based summary file, tract level (data/raw/acs/acsdt5y2024-*.dat)
    B03001  Hispanic origin (all ages)          B02015  Asian alone by detailed group (all ages)
    B05003  total CVAP                          B05003I Hispanic CVAP     B05003D Asian-alone CVAP
    B05003H White non-Hispanic CVAP
  ACS 2020-2024 5-year PUMS persons (data/raw/acs/csv_pus.zip): CVAP rate (18+ and citizen) by group,
    per PUMA shrunk to the state. Needed because origin tables count everyone: Venezuelans in Doral are
    mostly not citizens, Cubans mostly are, and the tract tables do not cross origin with citizenship.
  120th Congress block equivalency (data/raw/geo/NationalCD120.txt): the 2026 maps, incl. the 2025-26
    redraws (AL CA FL LA NC OH TN TX UT). Census 2020 block-group population centres (CenPop2020_Mean_BG)
    weight each tract's block groups; the ~3 % of people in block groups split by a district line are
    shared by block count.

Group CVAP in a tract = (Hispanic or Asian CVAP of the tract) x the group's share of that CVAP, where the
share is pop_g x rate_g / sum_h pop_h x rate_h, so the groups always add up to the published CVAP.

Run: python -m midterms.demog [--validate]   -> data/cache/district_groups_2026.csv
"""
from __future__ import annotations
import sys, zipfile
import numpy as np, pandas as pd
from .data_prep import RAW, CACHE

ACS = RAW / "acs"; GEO = RAW / "geo"

HISP_GROUPS = {  # PUMS HISP codes -> group ; B03001 lines -> group
    "mexican": ([2], [4]), "puerto_rican": ([3], [5]), "cuban": ([4], [6]), "dominican": ([5], [7]),
    "central_american": (list(range(6, 13)), list(range(9, 16))),
    "colomb": ([16], [20]), "venezuelan": ([21], [25]),     # colomb = Colombian origin (ACS B03001)
    "other_south_american": ([13, 14, 15, 17, 18, 19, 20, 22], [17, 18, 19, 21, 22, 23, 24, 26]),
    "other_hispanic": ([23, 24], [28, 29, 30, 31]),
}
# Asian alone. PUMS RAC2P19 (2020-22 records) / RAC2P24 (2023-24 records); B02015 lines.
ASIAN_GROUPS = {
    "chinese": ([43, 44], [4000, 4005], [2, 8]),
    "indian": ([38], [4015], [21]),
    "filipino": ([45], [4008], [12]),
    "vietnamese": ([57], [4014], [19]),
    "korean": ([49], [4003], [5]),
    "japanese": ([48], [4002], [4]),
    "other_south_asian": ([39, 53, 54], [4016, 4018, 4019], [22, 23, 24, 25, 26, 27, 28]),
}
ASIAN_OTHER_LINES = [3, 6, 7, 9, 10, 11, 13, 14, 15, 16, 17, 18, 20, 29, 30, 31, 32, 33, 34, 35]
GROUPS = list(HISP_GROUPS) + list(ASIAN_GROUPS) + ["other_asian"]
FIPS = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
        "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
        "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
        "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
        "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
        "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY"}
K_SHRINK = 50  # unweighted PUMS records; PUMA rate = (n*puma + K*state)/(n+K)


# ---------------------------------------------------------------- PUMS: CVAP rate by group
def _pums_group(df):
    g = pd.Series(pd.NA, index=df.index, dtype="object")
    h = df["HISP"].astype(int)
    for name, (codes, _) in HISP_GROUPS.items():
        g[h.isin(codes)] = name
    asian = (df["RAC1P"] == 6) & (h == 1)  # non-Hispanic Asian for the rate; tract tables are Asian alone of any origin
    r19 = pd.to_numeric(df["RAC2P19"], errors="coerce"); r24 = pd.to_numeric(df["RAC2P24"], errors="coerce")
    ga = pd.Series("other_asian", index=df.index)
    for name, (c19, c24, _) in ASIAN_GROUPS.items():
        ga[r19.isin(c19) | r24.isin(c24)] = name
    g[asian] = ga[asian]
    return g


def pums_rates(force=False) -> pd.DataFrame:
    out = CACHE / "pums_group_cvap.csv"
    if out.exists() and not force:
        return pd.read_csv(out, dtype={"ST": str, "PUMA": str})
    cols = ["STATE", "PUMA", "AGEP", "CIT", "HISP", "RAC1P", "RAC2P19", "RAC2P24", "PWGTP"]
    parts = []
    with zipfile.ZipFile(ACS / "csv_pus.zip") as z:
        for name in sorted(n for n in z.namelist() if n.endswith(".csv")):
            with z.open(name) as f:
                for ch in pd.read_csv(f, usecols=cols, dtype={"STATE": str, "PUMA": str, "RAC2P19": str, "RAC2P24": str},
                                      chunksize=2_000_000):
                    ch = ch.rename(columns={"STATE": "ST"}); ch = ch[(ch["HISP"] != 1) | (ch["RAC1P"] == 6)]
                    ch = ch.assign(group=_pums_group(ch)).dropna(subset=["group"])
                    ch["cvap"] = ((ch["AGEP"] >= 18) & (ch["CIT"] != 5)) * ch["PWGTP"]
                    parts.append(ch.groupby(["ST", "PUMA", "group"]).agg(pop=("PWGTP", "sum"), cvap=("cvap", "sum"),
                                                                        n=("PWGTP", "size")).reset_index())
            print(f"  pums {name} done", flush=True)
    r = pd.concat(parts).groupby(["ST", "PUMA", "group"], as_index=False).sum()
    r["ST"] = r["ST"].str.zfill(2); r["PUMA"] = r["PUMA"].str.zfill(5)
    r.to_csv(out, index=False)
    return r


def puma_group_rates() -> pd.DataFrame:
    """CVAP/pop per (state, PUMA, group), shrunk toward the state rate; state rate shrunk toward national."""
    r = pums_rates()
    nat = r.groupby("group")[["pop", "cvap"]].sum(); nat = (nat.cvap / nat["pop"]).rename("nat")
    st = r.groupby(["ST", "group"])[["pop", "cvap", "n"]].sum().reset_index()
    st = st.merge(nat, on="group")
    st["st_rate"] = (st.n * st.cvap / st["pop"] + K_SHRINK * st.nat) / (st.n + K_SHRINK)
    r = r.merge(st[["ST", "group", "st_rate"]], on=["ST", "group"])
    r["rate"] = (r.n * r.cvap / r["pop"] + K_SHRINK * r.st_rate) / (r.n + K_SHRINK)
    return r[["ST", "PUMA", "group", "rate"]], st[["ST", "group", "st_rate"]], nat


# ---------------------------------------------------------------- ACS summary-file tables
def _table(t, level="1400000", lines=None):
    tid = t.upper()
    df = pd.read_csv(ACS / f"acsdt5y2024-{t}.dat", sep="|", dtype={"GEO_ID": str}, low_memory=False)
    df = df[df.GEO_ID.str.startswith(level)].copy()
    df["geoid"] = df.GEO_ID.str.split("US").str[1]
    keep = {f"{tid}_E{int(l):03d}": l for l in (lines or [])}
    return df.set_index("geoid")[list(keep)].rename(columns=keep).astype(float)


def _cvap(t, level):
    x = _table(t, level, [9, 11, 20, 22]); return x.sum(axis=1)


def acs_frame(level="1400000") -> pd.DataFrame:
    """Per geography: total/Hispanic/Asian/White-NH CVAP and group populations (all ages)."""
    f = pd.DataFrame({"cvap": _cvap("b05003", level), "cvap_hisp": _cvap("b05003i", level),
                      "cvap_asian": _cvap("b05003d", level), "cvap_wnh": _cvap("b05003h", level)})
    h = _table("b03001", level, range(1, 32)); a = _table("b02015", level, range(1, 36))
    for name, (_, lines) in HISP_GROUPS.items():
        f["pop_" + name] = h[lines].sum(axis=1)
    for name, (_, _, lines) in ASIAN_GROUPS.items():
        f["pop_" + name] = a[lines].sum(axis=1)
    f["pop_other_asian"] = a[ASIAN_OTHER_LINES].sum(axis=1)
    return f.fillna(0)


def group_cvap(f: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    """Split Hispanic/Asian CVAP of each row into groups; `rates` has one rate column per group (same index)."""
    out = f[["cvap", "cvap_hisp", "cvap_asian", "cvap_wnh"]].copy()
    for fam, names, tot in (("h", list(HISP_GROUPS), "cvap_hisp"), ("a", list(ASIAN_GROUPS) + ["other_asian"], "cvap_asian")):
        w = pd.DataFrame({n: f["pop_" + n] * rates[n] for n in names})
        s = w.sum(axis=1).replace(0, np.nan)
        for n in names:
            out["cvap_" + n] = (f[tot] * w[n] / s).fillna(0)
    return out


def tract_rates(tracts: pd.Index) -> pd.DataFrame:
    rel = pd.read_csv(GEO / "2020_Census_Tract_to_2020_PUMA.txt", dtype=str, encoding="utf-8-sig")
    rel["geoid"] = rel.STATEFP + rel.COUNTYFP + rel.TRACTCE
    rel = rel.set_index("geoid").reindex(tracts)
    rel["ST"] = tracts.str[:2]
    pr, sr, nat = puma_group_rates()
    pw = pr.pivot_table(index=["ST", "PUMA"], columns="group", values="rate")
    sw = sr.pivot_table(index="ST", columns="group", values="st_rate")
    R = pw.reindex(pd.MultiIndex.from_arrays([rel.ST, rel.PUMA5CE])).set_axis(tracts)
    S = sw.reindex(rel.ST).set_axis(tracts)
    R = R.reindex(columns=GROUPS).fillna(S.reindex(columns=GROUPS))
    return R.fillna(nat)


# ---------------------------------------------------------------- tract -> district weights
def tract_district_weights(bef="NationalCD120.txt") -> pd.DataFrame:
    """(tract, state, cd, w): share of the tract's 2020 population in each district."""
    b = pd.read_csv(GEO / bef, dtype=str, usecols=["GEOID", "CDFP"])
    b = b[~b.CDFP.isin(["ZZ", "98"])]
    b["bg"] = b.GEOID.str[:12]
    nb = b.groupby(["bg", "CDFP"]).size().rename("nblk").reset_index()
    nb["frac"] = nb.nblk / nb.groupby("bg").nblk.transform("sum")
    p = pd.read_csv(GEO / "CenPop2020_Mean_BG.txt", dtype=str, encoding="utf-8-sig")
    p["bg"] = p.STATEFP + p.COUNTYFP + p.TRACTCE + p.BLKGRPCE; p["bgpop"] = p.POPULATION.astype(float)
    nb = nb.merge(p[["bg", "bgpop"]], on="bg", how="left")
    nb["pop"] = nb.frac * nb.bgpop.fillna(0)
    nb["tract"] = nb.bg.str[:11]
    # Connecticut: ACS 2022+ uses planning regions as counties (09110..09190); same TRACTCE, new county code
    ct = pd.read_csv(GEO / "acs22_cousub22_blkgrp20_st09.txt", sep="|", dtype=str, encoding="utf-8-sig")
    ct["a"] = ct.AREALAND_PART.astype(float)
    ct = ct.sort_values("a").drop_duplicates("GEOID_BLKGRP_20", keep="last")
    c22 = dict(zip(ct.GEOID_BLKGRP_20, ct.GEOID_COUSUB_22.str[2:5]))
    isct = nb.bg.str[:2] == "09"
    nb.loc[isct, "tract"] = "09" + nb.loc[isct, "bg"].map(c22) + nb.loc[isct, "bg"].str[5:11]
    t = nb.groupby(["tract", "CDFP"])["pop"].sum().reset_index()
    tot = t.groupby("tract")["pop"].transform("sum")
    # empty tracts (pop 0): share by block count so their (tiny) ACS counts still land somewhere
    t["w"] = np.where(tot > 0, t["pop"] / tot.where(tot > 0, 1), 1.0 / t.groupby("tract")["pop"].transform("size"))
    t["seat"] = t.tract.str[:2].map(FIPS) + "-" + t.CDFP.astype(int).clip(lower=1).astype(str)
    return t[["tract", "seat", "w"]]


def fill_missing_tracts(f: pd.DataFrame, need: pd.Index) -> pd.DataFrame:
    """Tracts in the block file but absent from the ACS tract table (14 Brentwood/Central Islip tracts in
    Suffolk NY, 77.6k people, in the 2020-24 file): give them the county total minus the tracts present,
    shared by 2020 population. Verified: Suffolk county 1,530,146 vs tract sum 1,452,567."""
    miss = need.difference(f.index)
    if len(miss) == 0:
        return f
    cty = acs_frame("0500000")
    have = f.groupby(f.index.str[:5]).sum()
    resid = (cty - have.reindex(cty.index).fillna(0)).clip(lower=0)
    p = pd.read_csv(GEO / "CenPop2020_Mean_BG.txt", dtype=str, encoding="utf-8-sig")
    p["t"] = p.STATEFP + p.COUNTYFP + p.TRACTCE; tp = p.groupby("t").POPULATION.apply(lambda x: x.astype(float).sum())
    mp = tp.reindex(miss).fillna(0)
    share = mp / mp.groupby(mp.index.str[:5]).transform("sum").replace(0, np.nan)
    add = resid.reindex(miss.str[:5]).set_axis(miss).multiply(share.fillna(0), axis=0).fillna(0)
    print(f"  filled {len(miss)} missing tracts from county residuals: {add.cvap.sum():,.0f} CVAP "
          f"({', '.join(sorted(set(miss.str[:5])))})")
    return pd.concat([f, add])


def build(bef="NationalCD120.txt", out_name="district_groups_2026.csv") -> pd.DataFrame:
    w = tract_district_weights(bef)
    f = fill_missing_tracts(acs_frame("1400000"), pd.Index(w.tract.unique()))
    g = group_cvap(f, tract_rates(f.index))
    m = w.merge(g, left_on="tract", right_index=True, how="left").fillna(0)
    val = [c for c in g.columns]
    d = m[val].multiply(m.w, axis=0).assign(seat=m.seat).groupby("seat").sum()
    for c in val:
        if c != "cvap":
            d["sh_" + c.removeprefix("cvap_")] = d[c] / d.cvap
    d = d.reset_index()
    d.to_csv(CACHE / out_name, index=False, float_format="%.5g")
    return d


def validate():
    """Same method on the 119th-Congress map vs the Census's own CD119 tables (direct, not built from tracts)."""
    d = build("NationalCD119.txt", "district_groups_cd119_check.csv").set_index("seat")
    f = acs_frame("5001900")
    f = f[f.index.str[2:4].str.isdigit() & (f.index.str[2:4] != "98") & f.index.str[:2].isin(list(FIPS))]
    st = f.index.str[:2]; cd = pd.Series(f.index.str[2:4].astype(int)).clip(lower=1).astype(str).values
    f.index = st.map(FIPS) + "-" + cd
    f = f[~f.index.str.startswith("DC")]
    # direct group CVAP with the state-level rates (the direct table has no PUMA)
    _, sr, nat = puma_group_rates()
    sw = sr.pivot_table(index="ST", columns="group", values="st_rate").reindex(columns=GROUPS)
    S = sw.reindex([FIPS_R[s[:2]] for s in f.index]).set_axis(f.index).fillna(nat)
    g = group_cvap(f, S)
    rows = []
    for c in ["cvap", "cvap_hisp", "cvap_asian", "cvap_wnh", "cvap_mexican", "cvap_cuban", "cvap_puerto_rican",
              "cvap_venezuelan", "cvap_dominican", "cvap_chinese", "cvap_indian", "cvap_vietnamese", "cvap_filipino"]:
        a = d[c] / d.cvap * 100; b = (g[c] / g.cvap * 100).reindex(a.index)
        e = (a - b).dropna()
        rows.append((c, len(e), round(b.mean(), 2), round(e.abs().mean(), 3), round(e.abs().max(), 2), e.abs().idxmax()))
    print(pd.DataFrame(rows, columns=["share", "n", "mean_share_pct", "mae_pp", "max_pp", "worst"]).to_string(index=False))
    tot = (d.cvap / g.cvap.reindex(d.index) - 1) * 100
    print(f"total CVAP: built vs direct, median {tot.median():+.2f}%  worst {tot.abs().max():.2f}% ({tot.abs().idxmax()})")


FIPS_R = {v: k for k, v in FIPS.items()}


def build_states(out_name="state_groups.csv") -> pd.DataFrame:
    """Same shares per state (for the Senate), plus the national row US. Direct state tables with state CVAP rates."""
    f = pd.concat([acs_frame("0400000"), acs_frame("0100000").set_axis(["US"])])
    f = f[f.index.isin(list(FIPS) + ["US"])]
    _, sr, nat = puma_group_rates()
    sw = sr.pivot_table(index="ST", columns="group", values="st_rate").reindex(columns=GROUPS)
    g = group_cvap(f, sw.reindex(f.index).set_axis(f.index).fillna(nat))
    for c in [c for c in g.columns if c != "cvap"]:
        g["sh_" + c.removeprefix("cvap_")] = g[c] / g.cvap
    # white non-college share (of CVAP x adults 25+ without a BA): the one demographic factor in which statewide
    # polling misses have been correlated across states (research/senate_error_correlation.py)
    e = pd.concat([_table("b15003", "0400000", [1, 22, 23, 24, 25]), _table("b15003", "0100000", [1, 22, 23, 24, 25]).set_axis(["US"])])
    g["college"] = (e[[22, 23, 24, 25]].sum(axis=1) / e[1]).reindex(g.index)
    g["sh_wnc"] = g["sh_wnh"] * (1 - g["college"])
    g.index = [FIPS.get(i, i) for i in g.index]
    g = g.rename_axis("state").reset_index()
    g.to_csv(CACHE / out_name, index=False, float_format="%.5g")
    return g

if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate()
    else:
        d = build()
        print(len(d), "seats"); print(d.sort_values("sh_hisp", ascending=False)[["seat", "cvap", "sh_hisp", "sh_mexican", "sh_cuban", "sh_puerto_rican"]].head(12).to_string(index=False))


def district_wnc(bef="NationalCD120.txt", out_name="district_wnc_2026.csv") -> pd.DataFrame:
    """White non-college share per district (2026-09-22), the House twin of state_groups.sh_wnc: White non-Hispanic
    CVAP share x (1 - BA+ share of adults 25+), both aggregated from tracts through the block-equivalency weights."""
    w = tract_district_weights(bef)
    f = acs_frame("1400000")[["cvap", "cvap_wnh"]]
    e = _table("b15003", "1400000", [1, 22, 23, 24, 25])
    t = f.join(pd.DataFrame({"a25": e[1], "ba": e[[22, 23, 24, 25]].sum(axis=1)}), how="left").fillna(0)
    m = w.merge(t, left_on="tract", right_index=True, how="inner")
    for c in ("cvap", "cvap_wnh", "a25", "ba"): m[c] = m[c] * m["w"]
    g = m.groupby("seat")[["cvap", "cvap_wnh", "a25", "ba"]].sum()
    g["sh_wnh"] = g.cvap_wnh / g.cvap; g["college"] = g.ba / g.a25; g["sh_wnc"] = g.sh_wnh * (1 - g.college)
    g = g.reset_index()[["seat", "sh_wnh", "college", "sh_wnc"]]
    g.to_csv(CACHE / out_name, index=False, float_format="%.5g")
    return g


def district_youth(bef="NationalCD120.txt", out_name="district_youth_2026.csv") -> pd.DataFrame:
    """Share of adults (18+) aged 18-29 per district, from ACS B01001 tracts through the block-equivalency weights."""
    w = tract_district_weights(bef)
    a = _table("b01001", "1400000", range(1, 50))
    kids = a[[3, 4, 5, 6, 27, 28, 29, 30]].sum(axis=1); young = a[[7, 8, 9, 10, 11, 31, 32, 33, 34, 35]].sum(axis=1)
    t = pd.DataFrame({"adults": a[1] - kids, "young": young})
    m = w.merge(t, left_on="tract", right_index=True, how="inner")
    for c in ("adults", "young"): m[c] = m[c] * m["w"]
    g = m.groupby("seat")[["adults", "young"]].sum(); g["sh_young"] = g.young / g.adults
    g = g.reset_index()[["seat", "adults", "sh_young"]]; g.to_csv(CACHE / out_name, index=False, float_format="%.5g"); return g


def heat_oil_shares():
    """Share of occupied housing units heated mainly by fuel oil / kerosene (ACS B25040 line 5 of line 1), per 2026 seat
    (tracts through the block-equivalency weights) and per state (direct state rows) - 2026-09-22, for the heating-oil
    price adjustment (model.heating_oil_shift)."""
    w = tract_district_weights("NationalCD120.txt")
    t = _table("b25040", "1400000", [1, 5]).rename(columns={1: "hh", 5: "oil"})
    m = w.merge(t, left_on="tract", right_index=True, how="inner")
    for c in ("hh", "oil"): m[c] = m[c] * m["w"]
    g = m.groupby("seat")[["hh", "oil"]].sum(); g["sh_oil"] = g.oil / g.hh
    s = _table("b25040", "0400000", [1, 5]).rename(columns={1: "hh", 5: "oil"}); s["sh_oil"] = s.oil / s.hh
    s.index = [FIPS.get(i[-2:], i) for i in s.index]
    g.reset_index()[["seat", "hh", "sh_oil"]].to_csv((ROOT / "data" / "static" / "district_heat_oil_2026.csv"), index=False, float_format="%.5g")
    s.rename_axis("state").reset_index()[["state", "hh", "sh_oil"]].to_csv((ROOT / "data" / "static" / "state_heat_oil.csv"), index=False, float_format="%.5g")
    return g, s
