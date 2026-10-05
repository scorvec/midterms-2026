"""District maps for the state-legislature page (web/legislatures.html): Census TIGER/Line state legislative districts -> one
TopoJSON per state, web/data/stateleg_geo/{ST}.topo.json (the page loads only the state it shows), and web/data/stateleg_geo/
index.json (chamber -> file / object, map notes, the file list), for every chamber in stateleg.CHAMBERS.

One-time build in GitHub Actions (.github/workflows/stateleg-geo.yml); downloads are cached in data/raw/stateleg (never committed).
  * TIGER/Line 2025 SLDU / SLDL (public domain). The 2025 files equal the 2024 ones for every modelled chamber (stateleg_build
    `maps` audit) and carry the maps used in 2026 - except the Michigan Senate, whose court-ordered 2026 redraw of the Detroit-area
    districts is not in the Census files (flagged on the map; the model's leans use the same 2025 lines).
  * TIGER/Line polygons run out into the Great Lakes and coastal water: the large water areas (district area outside the Census
    cartographic state outline cb_2024_us_state_500k, pieces over 25 km2) are cut away so the maps show land; land borders keep the
    TIGER lines. Islands under 2 km2 are dropped (a district's largest part is always kept).
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

OUTDIR = ROOT / "web" / "data" / "stateleg_geo"
INDEX = OUTDIR / "index.json"
AUDIT = STATIC / "stateleg" / "geo_audit.json"
VINTAGE = 2025
STATE_OUTLINE = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip"
NH_VTD = "https://www2.census.gov/geo/tiger/TIGER2020PL/LAYER/VTD/2020/tl_2020_33_vtd20.zip"
MAPSHAPER = "mapshaper@0.6.113"
BUILD = "v4"                                     # bump (or change SIMPLIFY / QUANT) to force a rebuild on the next push
SIMPLIFY = "interval=400"            # metres; mapshaper Visvalingam, keep-shapes
QUANT = "quantization=20000"
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


def signature():
    return f"{VINTAGE}|{SIMPLIFY}|{QUANT}|{MAPSHAPER}|{BUILD}"


def complete(need):
    """True when the committed per-state files already have a polygon for every modelled key of every chamber (same build settings)."""
    if not INDEX.exists(): return False
    I = json.loads(INDEX.read_text())
    if I.get("build") != signature(): return False
    for (st, ch), keys in need.items():
        m = I["chambers"].get(obj_name(st, ch))
        if m is None or not (OUTDIR / m["file"]).exists(): return False
        T = json.loads((OUTDIR / m["file"]).read_text())
        have = {g["properties"]["id"] for o in [m["object"]] + ([m["floterials"]] if m.get("floterials") else [])
                for g in T["objects"].get(o, {"geometries": []})["geometries"] if g.get("type")}
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


MIN_PART_KM2 = 2.0                   # islands / fragments smaller than this are dropped (a district's largest part is kept)
WATER_KM2 = 25.0                     # district area outside the shoreline outline in pieces at least this big = lake / sea


def km2(p):
    import math
    return p.area * 111.32 ** 2 * abs(math.cos(math.radians(p.centroid.y)))        # degrees^2 -> km^2 (small shapes)


def polys(geom):
    """Polygonal part of a geometry (clipping leaves slivers of lines / points), without parts under MIN_PART_KM2."""
    from shapely.geometry import MultiPolygon, Polygon
    if geom is None or geom.is_empty: return None
    parts = [geom] if isinstance(geom, Polygon) else [p for g in getattr(geom, "geoms", []) for p in (getattr(g, "geoms", None) or [g]) if isinstance(p, Polygon)]
    if not parts: return None
    big = max(parts, key=lambda p: p.area); parts = [p for p in parts if p is big or km2(p) >= MIN_PART_KM2]
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def land(g, st):
    """Cut the large water areas (Great Lakes, sea, big bays) out of the districts; land borders keep the TIGER lines."""
    from shapely.ops import unary_union
    w = unary_union(list(g.geometry)).difference(outline(st))
    parts = [p for p in (getattr(w, "geoms", None) or [w]) if p.geom_type == "Polygon" and km2(p) >= WATER_KM2]
    g = g.copy()
    if parts: g["geometry"] = g.geometry.difference(unary_union(parts))
    g["geometry"] = g.geometry.map(polys)
    return g[g.geometry.notna()]


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


def flot_of(audit):
    """{base district: [floterials it votes in]} for the page's tooltips."""
    o = {}
    for f, v in audit.get("nh_floterials", {}).items():
        if f.startswith("_"): continue
        for b in v["bases"]: o.setdefault(b, []).append(f)
    return o


def build(force=False):
    need = model_keys()
    if not force and complete(need):
        print("stateleg_geo: every chamber already has its polygons - nothing to build"); return 0
    tmp = Path(tempfile.mkdtemp()); layers, audit = {}, {"vintage": VINTAGE, "chambers": {}}
    for (st, ch), keys in sorted(need.items()):
        if (st, ch) in ALIAS: continue
        lay = "sldu" if ch == "upper" else "sldl"
        g = tiger_layer(st, lay)
        g["id"] = [_tiger_key(st, ch, r) for _, r in g.iterrows()]
        g = g[g["id"].notna()][["id", "geometry"]]
        g["geometry"] = g.geometry.make_valid()
        g = land(g.dissolve("id", as_index=False), st)
        name = obj_name(st, ch); g.to_file(tmp / f"{name}.geojson", driver="GeoJSON"); layers.setdefault(st, []).append(name)
        if st == "NH" and ch == "lower":
            fl, info = nh_floterials(g, keys)
            if fl is not None and len(fl):
                fl["geometry"] = fl.geometry.map(polys)
                fl.to_file(tmp / f"{name}_flot.geojson", driver="GeoJSON"); layers[st].append(f"{name}_flot")
                audit["nh_floterials"] = info
                g = pd.concat([g, fl], ignore_index=True)
        have = set(g["id"])
        audit["chambers"][name] = {"model": len(keys), "polygons": len(have), "missing_polygon": sorted(keys - have), "not_modelled": sorted(have - keys)}
        print(f"  {name}: {len(keys)} modelled, {len(have)} polygons, missing {sorted(keys - have)[:20]}, extra {sorted(have - keys)[:20]}")
    if OUTDIR.exists(): shutil.rmtree(OUTDIR)
    OUTDIR.mkdir(parents=True)
    files, T = {}, {}
    for st, names in sorted(layers.items()):
        f = OUTDIR / f"{st}.topo.json"
        cmd = ["npx", "-y", MAPSHAPER, "-i"] + [str(tmp / f"{n}.geojson") for n in names] + \
              ["combine-files", "-simplify", SIMPLIFY, "keep-shapes", "-o", "format=topojson", QUANT, str(f)]
        subprocess.run(cmd, check=True)
        T[st] = json.loads(f.read_text()); files[st] = f.name
        print(f"  {f.name}: {f.stat().st_size / 1e3:.0f} kB, {len(T[st]['arcs'])} arcs")
    # post-simplification audit (keep-shapes should keep every polygon)
    meta = {}
    for (st, ch), keys in sorted(need.items()):
        src = ALIAS.get((st, ch), (st, ch)); o = obj_name(*src); objs = T.get(src[0], {"objects": {}})["objects"]
        ids = {g_["properties"]["id"] for g_ in objs.get(o, {"geometries": []})["geometries"] if g_.get("type")}
        fl = f"{o}_flot" if f"{o}_flot" in objs else None
        if fl: ids |= {g_["properties"]["id"] for g_ in objs[fl]["geometries"] if g_.get("type")}
        rec = audit["chambers"].setdefault(obj_name(st, ch), {"model": len(keys), "alias_of": o} if (st, ch) in ALIAS else {})
        rec["missing_after_simplify"] = sorted(keys - ids)
        if (st, ch) in ALIAS: rec.update(polygons=len(ids), missing_polygon=sorted(keys - ids), not_modelled=sorted(ids - keys))
        meta[obj_name(st, ch)] = {"file": files.get(src[0]), "object": o, **({"floterials": fl, "floterial_of": flot_of(audit)} if fl else {}),
                                  **NOTES.get((st, ch), {})}
    I = {"source": f"U.S. Census Bureau TIGER/Line Shapefiles {VINTAGE}, state legislative districts (SLDU/SLDL); large water areas cut "
                   "away with the Census 2024 cartographic state outline; New Hampshire floterials rebuilt from the base districts that "
                   "vote in them (MEDSL 2024 precinct labels)",
         "vintage": VINTAGE, "build": signature(), "files": sorted(files.values()), "chambers": meta}
    INDEX.write_text(json.dumps(I, separators=(",", ":"), ensure_ascii=False))
    bad = {k: v["missing_after_simplify"] or v.get("missing_polygon") for k, v in audit["chambers"].items() if v.get("missing_after_simplify") or v.get("missing_polygon")}
    audit["ok"] = not bad; audit["bytes"] = {f.name: f.stat().st_size for f in sorted(OUTDIR.glob("*.json"))}
    audit["bytes_total"] = sum(audit["bytes"].values())
    AUDIT.write_text(json.dumps(audit, indent=1, sort_keys=True))
    print(f"wrote {OUTDIR.relative_to(ROOT)}: {audit['bytes_total'] / 1e3:.0f} kB in {len(files)} state files")
    shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        print("!! modelled seats without a polygon:", bad); return 1
    return 0


if __name__ == "__main__":
    sys.exit(build(force="--force" in sys.argv))
