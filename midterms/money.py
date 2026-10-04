"""Fundraising in the House prior (2026-09-18).

The prior becomes  mu = k0 + b*lean + c*inc + m*(lr - lr0) + E,  where lr = log((D + $10k) / (R + $10k)) of the two
nominees' individual contributions PLUS self-funding (contributions + loans) through June 30 of the election year (what is public by mid-September).

Evidence (research/money_backtest.py; fit on one cycle, scored on the other, contested districts, the test year's
national environment known): competitive-seat RMSE 2018 6.38 -> 6.03, 2022 9.24 -> 8.89; all seats 7.32 -> 7.04,
7.52 -> 7.46. End-of-cycle totals (which include late money that follows likely winners) would claim 5.94 / 8.65 —
June 30 keeps about two thirds of that. Money absorbs part of incumbency: c 4.6 -> 3.3.

Inputs: data/cache/fec_june30_{2018,2022}.csv (fit), data/cache/fec_june30_2026.csv (live), both from
research/fec_point_in_time.py; the district results from data_prep.
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FLOOR = 1e4


def _lr(dem, rep):
    return np.log((dem + FLOOR) / (rep + FLOOR))


def seat_money(year: int):
    """seat -> lr for seats where BOTH major-party nominees have a known June 30 figure; None if the file is missing.
    A nominee who could not be matched to an FEC record is missing, not $0: that seat gets no money term (the
    reversed FEC name 'NICK, LALOTA' once made a sitting member a $0 candidate and moved NY-1 from 24 to 70 %)."""
    f = ROOT / "data" / "static" / f"fec_june30_{year}.csv"
    if not f.exists(): return None
    j = pd.read_csv(f)
    # individual contributions + the candidate's own money (contributions and loans). Backtest: the same skill as
    # individual-only (competitive RMSE 6.09 / 8.87 v 6.05 / 8.91) but it does not read a $5M self-funder as broke
    j["money"] = j["indiv_jun30"] + j["self_jun30"].fillna(0.0)
    p = j.pivot_table(index="seat", columns="party", values="money", aggfunc=lambda v: v.sum(min_count=1), dropna=False)
    if "D" not in p or "R" not in p: return None
    p = p.dropna(subset=["D", "R"])
    return pd.DataFrame({"seat": p.index, "lr": _lr(p["D"].values, p["R"].values), "money_d": p["D"].values, "money_r": p["R"].values})


@lru_cache(maxsize=4)
def coefficients(years=(2018, 2022)):
    """(b, c, m, lr0, delta) from the given cycles' contested districts with year intercepts. delta = mean change of
    the fitted value relative to the lean+inc prior, so the model's k0 still applies. The backtest passes the OTHER
    cycle only (leave-one-year-out)."""
    from . import data_prep as D
    rows = []
    for y in years:
        h = D.fec_house(y).merge(D.partisan_lean(y), on="seat"); h = h[~h["uncontested"] & h["margin"].notna()].copy()
        h["inc"] = h["inc_party"].map({"D": 1, "R": -1}).fillna(0); h["year"] = y
        mo = seat_money(y)
        rows.append(h.merge(mo, on="seat", how="inner"))
    A = pd.concat(rows, ignore_index=True)
    yrs = sorted(A["year"].unique())
    X = np.column_stack([A["lean"], A["inc"], A["lr"]] + [(A["year"] == y).astype(float) for y in yrs])
    b, c, m = np.linalg.lstsq(X, A["margin"], rcond=None)[0][:3]
    X0 = np.column_stack([A["lean"], A["inc"]] + [(A["year"] == y).astype(float) for y in yrs])
    b0, c0 = np.linalg.lstsq(X0, A["margin"], rcond=None)[0][:2]
    lr0 = float(A["lr"].mean())
    delta = float(np.mean(b * A["lean"] + c * A["inc"] + m * (A["lr"] - lr0) - (b0 * A["lean"] + c0 * A["inc"])))
    return float(b), float(c), float(m), lr0, delta
