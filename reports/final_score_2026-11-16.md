# Final score, 16 November 2026

The forecast frozen at 2026-11-03T17:04Z (commit `6877451db9`) against the results as of 2026-11-16T17:50Z.
484 of 488 races are called; AK-1, CA-23, LA-5 and LA-6 are still out and are not scored.

## Races

| Office | Called | Brier | Log loss | Favourite won |
|---|---|---|---|---|
| House | 413 of 417 | 0.0449 | 0.1464 | 94.2 % |
| Senate | 35 of 35 | 0.0621 | 0.2077 | 94.3 % |
| Governor | 36 of 36 | 0.0635 | 0.2005 | 91.7 % |
| All | 484 of 488 | 0.0475 | 0.1549 | 94.0 % |

Against the race raters on the 144 House races they rated: Brier 0.1220 for the model, 0.1221 for the raters.

## Calibration, all races (probability of a Democratic win)

| Forecast band | Races | Mean forecast | Democrats won |
|---|---|---|---|
| 0.00-0.05 | 136 | 0.012 | 0.015 |
| 0.05-0.15 | 46 | 0.092 | 0.152 |
| 0.15-0.35 | 26 | 0.247 | 0.423 |
| 0.35-0.50 | 14 | 0.420 | 0.571 |
| 0.50-0.65 | 12 | 0.567 | 0.917 |
| 0.65-0.85 | 30 | 0.760 | 1.000 |
| 0.85-0.95 | 12 | 0.910 | 1.000 |
| 0.95-1.00 | 208 | 0.996 | 1.000 |

## Chambers (Democratic seats)

| Chamber | Forecast median | 10th to 90th | Result | Share of simulations at or below |
|---|---|---|---|---|
| House | 241 | 219 to 268 | 260 called, 4 out | |
| Senate | 53 | 49 to 56 | 56 | 0.924 |
| Governors | 27 | 24 to 31 | 30 | 0.810 |

Senate control: forecast probability 0.802 of a Democratic majority, which happened (Brier 0.0392).

## National House vote

Forecast Democratic margin +9.16 points (10th to 90th: +5.29 to +13.14); the count so far, not final, is +14.16 (civicAPI).
