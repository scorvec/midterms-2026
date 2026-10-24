"""Governor races decided only by a majority of the vote, and the chance that nobody gets one (2026).

  * Vermont: with no majority the General Assembly elects the governor in January from the top three (Vermont Constitution
    ch. II sec. 47). By custom it elects the leader: Shumlin in 2014 with 46.4 % and in 2010 with 49.5 %, Douglas in 2002 with
    44.9 %. Five names are on the 2026 ballot.
  * Georgia: a general election needs a majority, otherwise the top two meet in a runoff four weeks later (O.C.G.A.
    21-2-501). Neither 2026 Georgia race has a third name on the ballot (the Libertarian, Chase Oliver, withdrew from the
    governor's race; the Senate ballot is Ossoff v Collins), so only write-ins could force a runoff: not modelled.

P(no majority) = P(|D - R margin| < minor share): the share of the vote going to neither nominee is drawn per simulation,
independently of the margin, from a lognormal fitted to the state's last RECENT governor races with both a Democrat and a
Republican on the ballot (Vermont 2010-2022: 2.9-8.5 %, mean 4.8; the 2020 race, with a Progressive as the Democrats'
nominee, has no Democrat and is not among them). A race with no third name on the 2026 ballot gets none. The win
probabilities do not change: the forecast's winner is the leader, as the legislature's custom makes it."""

from __future__ import annotations

import numpy as np

RULES = {
    "VT": "the legislature then elects the governor in January; by custom it picks the leader",
    "GA": "a runoff between the top two on December 1",
}
RECENT = 6


def minor_history(H, state):
    """The state's last RECENT governor races' share for neither nominee (gov2026.history rows with a 'minor' value)."""
    h = H[(H["state"] == state) & H["minor"].notna()].sort_values("year")
    return h["minor"].tail(RECENT).to_numpy(float)


def minor_draws(hist, n, seed=17):
    """Lognormal draws of the minor share (points of the vote) fitted to the past values (log mean and log sd)."""
    x = np.log(np.clip(hist, 0.1, None))
    return np.exp(np.random.default_rng(seed).normal(x.mean(), x.std(ddof=1), n))


def p_no_majority(margins, minor):
    """Share of simulations in which neither nominee clears 50 %: |margin| below the minor share (both in points)."""
    return float((np.abs(margins) < minor).mean())


def races(GS, H, n):
    """{state: (rule text, minor-share draws)} for the governor races that need a majority and have a third name on the ballot."""
    out = {}
    for st in GS["state"]:
        if st not in RULES or not int(GS.loc[GS["state"] == st, "n_minor"].iloc[0]):
            continue
        hist = minor_history(H, st)
        if len(hist) >= 3:
            out[st] = (RULES[st], minor_draws(hist, n))
    return out
