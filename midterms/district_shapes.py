"""2026 congressional district shapes for the forecast page's House map.

The 2025-26 mid-decade redraws (TX CA NC OH UT FL AL TN LA...) are not in any published cartographic file, so the districts
are rebuilt from the Census block equivalency file of the map in force (demog.cd_blocks: the 120th-Congress file, Missouri on
its 2022 lines): each 2020 block group goes to the district holding most of its blocks, block groups are dissolved into
districts (cartographic 1:500k block-group shapes, water-clipped), then simplified with mapshaper to web TopoJSON. Block groups
split between districts land in one of them, which is invisible at national scale.
    python -m midterms.district_shapes      -> web/data/districts_2026.topo.json
"""

import subprocess
import tempfile
from pathlib import Path
import pandas as pd
import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "data" / "raw" / "geo"


def main():
    from .demog import FIPS, cd_blocks

    b = cd_blocks(120)
    b["bg"] = b.GEOID.str[:12]
    cnt = (
        b.groupby(["bg", "CDFP"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values("n", ascending=False)
        .drop_duplicates("bg")
    )
    cnt["seat"] = cnt.bg.str[:2].map(FIPS) + "-" + pd.to_numeric(cnt.CDFP).clip(lower=1).astype(int).astype(str)
    shp = GEO / "shapes" / "cb_2020_us_bg_500k.shp"
    src = str(shp) if shp.exists() else f"zip://{shp.with_suffix('.zip')}"  # demog.fetch_geo keeps the zip
    g = gpd.read_file(src, columns=["GEOID", "geometry"])
    g = g.merge(cnt[["bg", "seat"]], left_on="GEOID", right_on="bg", how="inner")
    d = g.dissolve(by="seat", as_index=False)[["seat", "geometry"]]
    d["geometry"] = d.geometry.buffer(0)
    print(f"{len(d)} districts from {len(g)} block groups")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "d.geojson"
        d.to_crs(4326).to_file(src, driver="GeoJSON")
        out = ROOT / "web" / "data" / "districts_2026.topo.json"
        subprocess.run(
            [
                "npx",
                "--yes",
                "mapshaper",
                "-i",
                str(src),
                "-simplify",
                "3%",
                "keep-shapes",
                "-filter-slivers",
                "-o",
                "format=topojson",
                "quantization=100000",
                str(out),
            ],
            check=True,
        )
    print(out, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
