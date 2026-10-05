"""District maps for the state-legislature page (web/legislatures.html): Census TIGER/Line state legislative districts -> one
TopoJSON, web/data/stateleg_districts.topo.json, with every chamber in stateleg.CHAMBERS.

One-time build in GitHub Actions (.github/workflows/stateleg-geo.yml); downloads are cached in data/raw/stateleg (never committed).
  * TIGER/Line 2025 SLDU / SLDL (public domain). The 2025 files equal the 2024 ones for every modelled chamber (stateleg_build
    `maps` audit) and carry the maps used in 2026 - except the Michigan Senate, whose court-ordered 2026 redraw of the Detroit-area
    districts is not in the Census files (flagged on the map; the model's leans use the same 2025 lines).
  * TIGER/Line polygons run out into the Great Lakes and coastal water: they are clipped to the Census cartographic state outline
    (cb_2024_us_state_500k) so the maps show land.
  * Arizona House = the 30 legislative districts (two members each): the page draws the Senate object for it.
  * New Hampshire House floterial districts are not in TIGER (it holds the 164 base districts): each floterial is rebuilt from the
    towns and wards that vote in it (MEDSL 2024 precinct labels: a town votes in its base district and in a floterial) as the union
    of whole base districts when its towns fill them exactly, else of the towns' and wards' TIGER 2020 voting-district shapes.
  * Simplified with mapshaper (shared arcs, so neighbouring districts and the two chambers of a state stay seamless).
Join audit -> data/static/stateleg/geo_audit.json: every modelled district key (data/static/stateleg/lean_2026.csv, the same keys
the forecast uses) against the polygons. The build FAILS if any modelled seat lacks a polygon.

    python -m midterms.stateleg_geo            # builds only what is missing (no-op when every chamber already joins)
    python -m midterms.stateleg_geo --force
"""
from __future__ import annotations

import json, re, shutil, subprocess, sys, tempfile
from pathlib import Path
import pandas as pd
from .paths import ROOT, STATIC

OUT = ROOT / "web" / "data" / "stateleg_districts.topo.json"
AUDIT = STATIC / "stateleg" / "geo_audit.json"
VINTAGE = 2025
STATE_OUTLINE = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip"
NH_VTD = "https://www2.census.gov/geo/tiger/TIGER2020PL/LAYER/VTD/2020/tl_2020_33_vtd20.zip"
MAPSHAPER = "mapshaper@0.6.113"
SIMPLIFY = "interval=120"            # metres; mapshaper Visvalingam, keep-shapes
QUANT = "quantization=200000"
FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "FL": "12", "GA": "13", "HI": "15",
        "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26",
        "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37",
        "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
        "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56"}
ALIAS = {("AZ", "lower"): ("AZ", "upper")}                    # same districts, drawn from one object
NOTES = {
    ("MI", "upper"): {"flag": "Lines shown are the 2022 map from the Census files. A court-ordered redraw of the Detroit-area Senate "
                              "districts applies in 2026 and is not in the Census files yet, so districts in and around Detroit do not "
                              "match the 2026 lines (the forecast's district leans use the same 2022 lines)."},
    ("AZ", "lower"): {"note": "Each of the 30 legislative districts elects two House members; shading is the expected Democratic share of "
                              "the two seats."},
    ("NH", "lower"): {"note": "Filled: the 164 base districts. Outlined: the floterial districts, which lie on top of several base "
                              "districts and elect extra members from all of them together; hover or tap a district to see its "
                              "floterial too, or switch the shading to the floterials."},
}


def model_keys():
    """{(state, chamber): set of district keys} for every chamber in stateleg.CHAMBERS (lean_2026.csv, as the forecast reads it)."""
    from .stateleg import CHAMBERS
    L = pd.read_csv(STATIC / "stateleg" / "lean_2026.csv", dtype={"district": str}, keep_default_na=False)
    return {(st, ch): set(L.loc[(L["state"] == st) & (L["chamber"] == ch), "district"]) for st, ch, *_ in CHAMBERS}


def obj_name(st, ch):
    return f"{st}_{ch}"


def complete(need):
    """True when the committed TopoJSON already has a polygon for every modelled key of every chamber."""
    if not OUT.exists(): return False
    T = json.loads(OUT.read_text())
    for (st, ch), keys in need.items():
        o = T["objects"].get(obj_name(*ALIAS.get((st, ch), (st, ch))))
        if o is None: return False
        have = {g["properties"]["id"] for g in o["geometries"] if g.get("type")}
        if keys - have: return False
    return True


def _tiger_key(st, ch, row):
    from .stateleg_build import norm_district
    if st == "NH" and ch == "lower":                                   # NAMELSAD "State House District Belknap 1" style
        m = re.search(r"([A-Za-z]+)\s+(?:County\s+)?(?:District\s+)?0*(\d+)\s*$", str(row["NAMELSAD"]))
        return f"{m.group(1).title()} {int(m.group(2))}" if m else None
    return norm_district(st, ch, row["code"])


def tiger_layer(st, lay):
    import geopandas as gpd
    from .stateleg_build import url_get
    p = url_get(f"https://www2.census.gov/geo/tiger/TIGER{VINTAGE}/{lay.upper()}/tl_{VINTAGE}_{FIPS[st]}_{lay}.zip", f"tl_{VINTAGE}_{FIPS[st]}_{lay}.zip")
    g = gpd.read_file(f"zip://{p}")
    col = "SLDUST" if lay == "sldu" else "SLDLST"
    g = g[~g[col].astype(str).str.upper().str.startswith("ZZ")].copy()
    g["code"] = g[col].astype(str)
    return g.to_crs(4326)


_OUTLINE = None
def outline(st):
    global _OUTLINE
    import geopandas as gpd
    from .stateleg_build import url_get
    if _OUTLINE is None: _OUTLINE = gpd.read_file(f"zip://{url_get(STATE_OUTLINE, 'cb_2024_us_state_500k.zip')}").to_crs(4326)
    return _OUTLINE[_OUTLINE["STATEFP"] == FIPS[st]].geometry.union_all()


def polys(geom):
    """Polygonal part of a geometry (clipping can leave slivers of lines / points)."""
    from shapely.geometry import MultiPolygon, Polygon
    if geom is None or geom.is_empty: return None
    if isinstance(geom, (Polygon, MultiPolygon)): return geom
    parts = [p for g in getattr(geom, "geoms", []) for p in (getattr(g, "geoms", None) or [g]) if isinstance(p, Polygon)]
    return MultiPolygon(parts) if parts else None


def nh_floterials(base, keys):
    """NH House floterials: model keys without a TIGER base polygon, rebuilt from the towns / wards that vote in them."""
    import geopandas as gpd
    from shapely.ops import unary_union
    from . import stateleg_build as B
    flot = sorted(keys - set(base["id"]))
    if not flot: return None, {}
    df = B.medsl_load(B.dv_get("m2024", "2024-nh-precinct-general\\.(tab|csv)"), "NH")
    leg = df[B.office_chamber(df) == "lower"].copy()
    leg["dist"] = [B.norm_district("NH", "lower", d, c) for d, c in zip(leg["district"], leg["county_name"])]
    leg = leg[leg["dist"].notna()][["key", "precinct", "dist"]].drop_duplicates()
    bset = set(base["id"])
    pb = leg[leg["dist"].isin(bset)].groupby("key")["dist"].agg(lambda s: sorted(set(s)))
    rows, info, vtd = [], {}, None
    for f in flot:
        pk = set(leg.loc[leg["dist"] == f, "key"])
        bases = sorted({b for k in pk for b in pb.get(k, [])})
        full = all(set(pb[pb.map(lambda v, b=b: b in v)].index) <= pk for b in bases) and bases
        if full:
            geom = unary_union(list(base.loc[base["id"].isin(bases), "geometry"])); how = "base"
        else:
            if vtd is None:
                vtd = gpd.read_file(f"zip://{B.url_get(NH_VTD, 'tl_2020_33_vtd20.zip')}").to_crs(4326)
                vtd["nm"] = vtd["NAME20"].map(B._town)
                print("  NH VTD names:", vtd["nm"].head(12).tolist())
            names = {B._town(p) for p in leg.loc[leg["dist"] == f, "precinct"]}
            hit = vtd[vtd["nm"].isin(names)]
            geom = unary_union(list(hit.geometry)) if len(hit) else None; how = "vtd"
            if len(hit) < len(names): info.setdefault("_vtd_unmatched", {})[f] = sorted(names - set(vtd["nm"]))
        info[f] = {"bases": bases, "towns": int(len(pk)), "method": how}
        if geom is not None: rows.append({"id": f, "geometry": geom})
    return gpd.GeoDataFrame(rows, crs=4326), info


def build(force=False):
    need = model_keys()
    if not force and complete(need):
        print("stateleg_geo: every chamber already has its polygons - nothing to build"); return 0
    import geopandas as gpd
    tmp = Path(tempfile.mkdtemp()); layers, audit = [], {"vintage": VINTAGE, "chambers": {}}
    for (st, ch), keys in sorted(need.items()):
        if (st, ch) in ALIAS: continue
        lay = "sldu" if ch == "upper" else "sldl"
        g = tiger_layer(st, lay)
        if st == "NH" and ch == "lower":
            print("  NH sldl columns:", list(g.columns), g[["code", "NAMELSAD"]].head(8).values.tolist())
        g["id"] = [_tiger_key(st, ch, r) for _, r in g.iterrows()]
        g = g[g["id"].notna()][["id", "geometry"]]
        g["geometry"] = g.geometry.make_valid().intersection(outline(st)).map(polys)
        g = g[g.geometry.notna()]
        g = g.dissolve("id", as_index=False)
        name = obj_name(st, ch); g.to_file(tmp / f"{name}.geojson", driver="GeoJSON"); layers.append(name)
        if st == "NH" and ch == "lower":
            fl, info = nh_floterials(g, keys)
            if fl is not None and len(fl):
                fl["geometry"] = fl.geometry.make_valid().intersection(outline(st)).map(polys)
                fl.to_file(tmp / f"{name}_flot.geojson", driver="GeoJSON"); layers.append(f"{name}_flot")
                audit["nh_floterials"] = info
                g = pd.concat([g, fl], ignore_index=True)
        have = set(g["id"])
        audit["chambers"][name] = {"model": len(keys), "polygons": len(have), "missing_polygon": sorted(keys - have), "not_modelled": sorted(have - keys)}
        print(f"  {name}: {len(keys)} modelled, {len(have)} polygons, missing {sorted(keys - have)[:20]}, extra {sorted(have - keys)[:20]}")
    out_tmp = tmp / "out.topo.json"
    cmd = ["npx", "-y", MAPSHAPER, "-i"] + [str(tmp / f"{n}.geojson") for n in layers] + \
          ["combine-files", "-simplify", SIMPLIFY, "keep-shapes", "-o", "format=topojson", QUANT, str(out_tmp)]
    print(" ".join(cmd[3:6]), "...", " ".join(cmd[-6:])); subprocess.run(cmd, check=True)
    T = json.loads(out_tmp.read_text())
    # post-simplification audit (keep-shapes should keep every polygon)
    for (st, ch), keys in sorted(need.items()):
        o = T["objects"].get(obj_name(*ALIAS.get((st, ch), (st, ch))), {"geometries": []})
        ids = {g_["properties"]["id"] for g_ in o["geometries"] if g_.get("type")}
        if (st, ch) == ("NH", "lower"): ids |= {g_["properties"]["id"] for g_ in T["objects"].get("NH_lower_flot", {"geometries": []})["geometries"] if g_.get("type")}
        rec = audit["chambers"].setdefault(obj_name(st, ch), {"model": len(keys), "alias_of": obj_name(*ALIAS[(st, ch)])} if (st, ch) in ALIAS else {})
        rec["missing_after_simplify"] = sorted(keys - ids)
        if (st, ch) in ALIAS: rec.update(polygons=len(ids), missing_polygon=sorted(keys - ids), not_modelled=sorted(ids - keys))
    T["meta"] = {"source": f"U.S. Census Bureau TIGER/Line Shapefiles {VINTAGE}, state legislative districts (SLDU/SLDL), clipped to the "
                           "Census 2024 cartographic state outline; New Hampshire floterials rebuilt from base districts / TIGER 2020 voting "
                           "districts by MEDSL 2024 precinct labels",
                 "vintage": VINTAGE,
                 "chambers": {obj_name(st, ch): {"object": obj_name(*ALIAS.get((st, ch), (st, ch))),
                                                 **({"floterials": "NH_lower_flot"} if (st, ch) == ("NH", "lower") and "NH_lower_flot" in T["objects"] else {}),
                                                 **NOTES.get((st, ch), {})} for (st, ch) in sorted(need)}}
    OUT.write_text(json.dumps(T, separators=(",", ":")))
    bad = {k: v["missing_after_simplify"] for k, v in audit["chambers"].items() if v.get("missing_after_simplify") or v.get("missing_polygon")}
    audit["ok"] = not bad; audit["bytes"] = OUT.stat().st_size
    AUDIT.write_text(json.dumps(audit, indent=1, sort_keys=True))
    print(f"wrote {OUT.relative_to(ROOT)}: {OUT.stat().st_size / 1e3:.0f} kB, {len(T['objects'])} objects")
    shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        print("!! modelled seats without a polygon:", bad); return 1
    return 0


if __name__ == "__main__":
    sys.exit(build(force="--force" in sys.argv))
