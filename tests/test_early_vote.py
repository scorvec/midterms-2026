"""Banked early votes (early_vote.py): the timing profile, exposures and the per-state factors."""

import json

import numpy as np
import pytest

from midterms import early_vote as EV


def test_profile_file():
    rec = json.loads(EV.PROFILE_JSON.read_text())
    assert rec["W"] == EV.W and rec["P"] == EV.P_SHAPE  # the module reads the fitted profile
    assert rec["W"] > 0 and rec["P"] > 0
    pts = rec["fit_2024"]["points"]
    assert pts and dict(pts)[0] == 1.0  # shares of the Election-Day total
    assert all(0.0 <= s <= 1.0 for _, s in pts + rec["check_2026"]["points"])


def test_profile_shape():
    tau = np.arange(0, 61)
    h = EV.h(tau)
    assert h[0] == 1.0
    assert EV.h(EV.W) == 0.0 and EV.h(EV.W + 10) == 0.0
    assert np.all(np.diff(h) <= 0)  # fewer ballots in, the further out


@pytest.mark.parametrize("L", [5, 18, 30])
def test_banked_start_follows_the_profile(L):
    """A path that starts from F h(L) at L days out is F h(tau): the history's exposure is sum (1 - F h(tau))^2."""
    F = 0.5
    expect = float(np.sum((1 - F * EV.h(np.arange(L))) ** 2))
    assert EV.exposure(F, L, F * float(EV.h(L))) == pytest.approx(expect)


def test_history_counts_ballots_already_banked():
    years = json.loads(EV.FIT_JSON.read_text())["years"]

    def zero_start(L):
        return float(np.mean([EV.exposure(EV.HIST_PRE[y], L) for y in years]))

    for L in (10, 18, 30):
        assert EV.hist_exposure(L) < zero_start(L)
    L = int(EV.W) + 5  # nothing banked yet that far out: both paths agree
    assert EV.hist_exposure(L) == pytest.approx(zero_start(L))
    assert EV.exposure(0.5, 0) == 0.0


def test_k_factors_without_snapshots(monkeypatch, tmp_path):
    """With nothing banked yet, a state that votes early more (Washington, all mail) is less exposed than one that hardly
    does (Mississippi)."""
    monkeypatch.setattr(EV, "RAW", tmp_path)
    k = EV.k_factors("2026-10-16")
    meta = k.pop("_meta")
    assert meta["snapshot"] is None and meta["banked_states"] == 0 and meta["lead"] == 18
    assert all(np.isfinite(v) and v > 0 for v in k.values())
    share = EV.share_2022()
    hi, lo = max(share, key=share.get), min(share, key=share.get)
    assert k[hi] < k[lo]
