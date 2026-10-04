"""Retail heating-oil price for the heating-oil adjustment (model.heat_retail / model.heating_oil_shift).

Source: U.S. Energy Information Administration, State Heating Oil and Propane Program (SHOPP), "Weekly Maine No. 2 Heating
Oil Residential Price" (dollars per gallon, series W_EPD2F_PRS_SME_DPG; public domain). Fetched without an API key from
EIA's dnav history workbook. Maine is the state where the adjustment matters most (the largest fuel-oil household share)
and where the polls it is measured against were fielded; New England (PADD 1A, W_EPD2F_PRS_R1X_DPG) tracks it within about
5 %.

SEASON: the residential survey runs October through March (first price of a season is published in the first full week
of October). Between April and September there is no new price: the last published value is held (`series()` keeps
every week; `price_asof` returns the newest value on or before a date) and the page says which week the price is from.

    python -m midterms.heating_oil          # fetch + print the newest values
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SERIES = "W_EPD2F_PRS_SME_DPG"
LABEL = "EIA weekly Maine No. 2 heating oil residential price"
URL = f"https://www.eia.gov/dnav/pet/hist_xls/{SERIES}w.xls"
CSV = ROOT / "data" / "cache" / "eia_heating_oil_me.csv"


def fetch() -> pd.DataFrame:
    """Download the full weekly history (~70 KB) and write data/cache/eia_heating_oil_me.csv (date, price)."""
    from . import fetch as F                             # conditional request: EIA answers 304 while the workbook is unchanged
    raw = F.get(URL, ROOT / "data" / "raw" / "eia" / f"{SERIES}w.xls", timeout=60)[0]
    d = pd.read_excel(io.BytesIO(raw), sheet_name="Data 1", skiprows=2, engine="xlrd")
    d = d.iloc[:, :2]; d.columns = ["date", "price"]
    d = d.dropna(); d["date"] = pd.to_datetime(d["date"]).dt.normalize(); d["price"] = d["price"].astype(float)
    if len(d) < 500: raise ValueError(f"EIA heating-oil history too short ({len(d)} rows)")
    CSV.parent.mkdir(parents=True, exist_ok=True); d.to_csv(CSV, index=False)
    return d


def series() -> pd.DataFrame | None:
    """The weekly history (fetched if the cache is missing); None when neither works."""
    if CSV.exists():
        return pd.read_csv(CSV, parse_dates=["date"])
    try:
        return fetch()
    except Exception as e:
        print("  heating oil: EIA fetch failed -", str(e)[:100]); return None


def price_asof(s: pd.DataFrame, d) -> tuple[pd.Timestamp, float] | None:
    """(week, price) of the newest observation on or before d (the last value is held outside the Oct-Mar season)."""
    q = s[s["date"] <= pd.Timestamp(d)]
    if q.empty: return None
    r = q.iloc[-1]; return pd.Timestamp(r["date"]), float(r["price"])


if __name__ == "__main__":
    s = fetch(); print(LABEL); print(s.tail(6).to_string(index=False))
