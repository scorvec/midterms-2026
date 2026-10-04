"""Backtest: run the House model as of a date in a past cycle and score it.
    python -m midterms.backtest 2022 2022-09-15
"""
import sys, numpy as np, pandas as pd
from . import model as M, data_prep as D

def run(year, asof, P=None, n=20000):
    P = P or M.Params()
    seats = M.build_house(year, asof, P); mg = M.simulate(seats, P, n=n); S = M.summarize(mg, seats)
    actual = (seats["winner"] == "D").sum()
    ds = ((mg > 0).sum(1)); pit = float((ds <= actual).mean())
    p = S["p_dem_seat"]; y = (seats["winner"] == "D").values.astype(float)
    brier = float(np.mean((p - y) ** 2)); ll = float(-np.mean(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))))
    bins = pd.cut(p, [0, .05, .2, .4, .6, .8, .95, 1.0], include_lowest=True); cal = pd.DataFrame({"p": p, "y": y, "bin": bins}).groupby("bin", observed=True).agg(n=("y", "size"), pred=("p", "mean"), actual=("y", "mean"))
    contested = ~seats["uncontested"].values; res = (seats["margin"].values - seats["mu"].values)[contested & seats["margin"].notna().values]
    print(f"{year} as of {asof}: E (generic-implied national margin) {seats['E'].iloc[0]:+.2f}; polled seats {seats['n_eff'].notna().sum()}")
    print(f"  Dem seats: mean {S['dem_seats_mean']:.1f}  p10/p50/p90 {S['dem_seats_p10']:.0f}/{S['dem_seats_p50']:.0f}/{S['dem_seats_p90']:.0f}  sd {S['seat_sd']:.1f}  P(D majority) {S['p_dem_majority']:.2f}  | ACTUAL {actual}  PIT {pit:.2f}")
    print(f"  per-seat Brier {brier:.3f}  logloss {ll:.3f}  | mu error vs result: mean {res.mean():+.2f} sd {res.std():.2f} (contested)")
    print(cal.round(3).to_string())
    return seats, mg, S

if __name__ == "__main__":
    run(int(sys.argv[1]), sys.argv[2])
