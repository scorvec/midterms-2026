"""The state-legislature backtest scores the pages quote (stateleg_backtest.published), from the committed backtest.json."""

import json

import pytest

from midterms import stateleg as SLM
from midterms import stateleg_backtest as SLB


def test_groups_cover_every_chamber_once():
    keys = [k for g in SLB.GROUPS.values() for k in g]
    assert sorted(keys) == sorted((st, ch) for st, ch, *_ in SLM.CHAMBERS)


def test_published_groups_add_up_to_the_pooled_summary():
    pub = SLB.published()
    pooled = next(r for r in json.loads(SLB.BACKTEST.read_text()) if r.get("summary") == "base")
    n = sum(g["n"] for g in pub.values())
    assert n == pooled["n"]
    for k in ("calls", "naive_calls", "in80"):
        assert sum(g[k] for g in pub.values()) / n == pytest.approx(pooled[k], abs=5e-4)
    assert sum(g["brier"] * g["n"] for g in pub.values()) / n == pytest.approx(pooled["brier"], abs=1e-3)
