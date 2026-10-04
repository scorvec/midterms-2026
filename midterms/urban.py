"""Urbanization index on ANY congressional map (2026-09-22).

538's index is the natural log of the average number of people living within 5 miles of each resident of a district
(tract-level). The model used 538's 2022-map file keyed by district NUMBER, so every seat in a state redrawn since
(TX, CA, MO, NC, OH, UT, FL, AL, TN, LA...) carried the loading of an unrelated old district. This rebuilds it from the
2020 block-group population centroids (CenPop2020_Mean_BG) on a 1 km grid: population convolved with a 5-mile disk,
read back at each block group, log, population-weighted per district through the block equivalency file.
Validated on the CD119 map against 538's CD118 values in states whose map did not change; the result is linearly
mapped onto 538's scale so the model's loading keeps its meaning.
    python -m midterms.urban        -> data/cache/urbanization_2026.csv
"""
import numpy as np, pandas as pd
from pathlib import Path
from scipy.signal import fftconvolve
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]; GEO = ROOT / "data" / "raw" / "geo"
R_M, CELL = 8046.7, 1000.0          # 5 miles, 1 km grid


def bg_density():
    p = pd.read_csv(GEO / "CenPop2020_Mean_BG.txt", dtype=str, encoding="utf-8-sig")
    p["bg"] = p.STATEFP + p.COUNTYFP + p.TRACTCE + p.BLKGRPCE; p["pop"] = p.POPULATION.astype(float)
    p["lat"] = p.LATITUDE.astype(float); p["lon"] = p.LONGITUDE.astype(float)
    p["near"] = np.nan
    regions = {"conus": ~p.STATEFP.isin(["02", "15", "72"]), "ak": p.STATEFP == "02", "hi": p.STATEFP == "15"}
    crs = {"conus": "EPSG:5070", "ak": "EPSG:3338", "hi": "ESRI:102007"}
    for reg, m in regions.items():
        q = p[m]; x, y = Transformer.from_crs("EPSG:4326", crs[reg], always_xy=True).transform(q.lon.values, q.lat.values)
        x0, y0 = x.min() - 2 * R_M, y.min() - 2 * R_M
        ix = ((x - x0) // CELL).astype(int); iy = ((y - y0) // CELL).astype(int)
        G = np.zeros((iy.max() + 3, ix.max() + 3)); np.add.at(G, (iy, ix), q["pop"].values)
        k = int(np.ceil(R_M / CELL)); yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
        disk = ((xx * CELL) ** 2 + (yy * CELL) ** 2 <= R_M ** 2).astype(float)
        C = fftconvolve(G, disk, mode="same")
        p.loc[m, "near"] = np.maximum(C[iy, ix], q["pop"].values)
    return p.set_index("bg")[["pop", "near"]]


def district_index(d: pd.DataFrame, bef: str) -> pd.Series:
    b = pd.read_csv(GEO / bef, dtype=str, usecols=["GEOID", "CDFP"]); b = b[~b.CDFP.isin(["ZZ", "98"])]
    b["bg"] = b.GEOID.str[:12]
    nb = b.groupby(["bg", "CDFP"]).size().rename("nblk").reset_index()
    nb["frac"] = nb.nblk / nb.groupby("bg").nblk.transform("sum")
    nb = nb.join(d, on="bg", how="inner"); nb["w"] = nb.frac * nb["pop"]
    from .demog import FIPS
    nb["seat"] = nb.bg.str[:2].map(FIPS) + "-" + nb.CDFP.astype(int).clip(lower=1).astype(str)
    nb = nb[nb.w > 0]
    return nb.groupby("seat").apply(lambda g: np.average(np.log(g["near"]), weights=g["w"]), include_groups=False)


def main():
    d = bg_density()
    i119, i120 = district_index(d, "NationalCD119.txt"), district_index(d, "NationalCD120.txt")
    u = pd.read_csv(ROOT / "data" / "raw" / "538repo" / "urbanization-index-2022.csv"); u["seat"] = u["state"] + "-" + u["cd"].astype(int).astype(str)
    v = u.set_index("seat")["urbanindex"]
    # states whose map is the same on 538's 2022 file, CD119 and CD120 (2024 redraws: AL GA LA NC NY; 2025-26: see CD120)
    changed = {s[:2] for s in i120.index if s in i119.index and abs(i120[s] - i119[s]) > 1e-6} | {"AL", "GA", "LA", "NC", "NY"}
    same = [s for s in v.index if s in i119.index and s[:2] not in changed]
    a, b = np.polyfit(i119[same], v[same], 1); r = np.corrcoef(i119[same], v[same])[0, 1]
    out = pd.DataFrame({"seat": i120.index, "urbanindex": a * i120.values + b, "raw": i120.values})
    out.to_csv(ROOT / "data" / "static" / "urbanization_2026.csv", index=False)
    print(f"validation on {len(same)} unchanged seats: r {r:.3f}, 538 = {a:.3f} x ours + {b:.2f}; changed states {sorted(changed)}")
    old = out.set_index("seat")["urbanindex"]; moved = (old - v.reindex(old.index)).abs().sort_values(ascending=False)
    print("largest changes vs the district-number lookup:", moved.head(8).round(2).to_dict())
    return out


if __name__ == "__main__":
    main()
