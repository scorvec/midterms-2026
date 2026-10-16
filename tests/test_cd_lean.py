"""District lean builder (cd_lean_build.py): the join of block votes to a block equivalency file, the county-swing step that
carries block votes to an election with no precinct shapes, and the county-name keys that match Wikipedia's county tables to
FIPS codes."""

import numpy as np
import pandas as pd
import pytest

from midterms import cd_lean_build as C


def _blocks(rows):
    return pd.DataFrame(rows, columns=["block", "d", "r"])


def test_block_ids_are_15_digits():
    raw = pd.Series(["560019567001000", "1000000US560019567001001", " 560019567001002 ", "10010201001000"])
    assert C._block_id(raw).tolist() == ["560019567001000", "560019567001001", "560019567001002", "010010201001000"]


def test_equivalency_file_joins_the_block_votes():
    # a 118th-Congress-style table: a two-district state and an at-large one, padded codes and a water row
    bef = pd.DataFrame(
        {
            "block": C._block_id(
                pd.Series(["290010000000001", "290010000000002", "290030000000001", "560010000000001"])
            ),
            "cd": [" 01", "02 ", "02", "00"],
        }
    )
    mo = C.bef_blocks(bef, "MO")
    assert mo.set_index("block")["cd"].to_dict() == {
        "290010000000001": "1",
        "290010000000002": "2",
        "290030000000001": "2",
    }
    assert C.bef_blocks(bef, "WY")["cd"].tolist() == ["1"]  # at-large 00 -> seat 1
    bv = _blocks([("290010000000001", 60.0, 40.0), ("290010000000002", 30.0, 70.0), ("290030000000001", 50.0, 50.0)])
    g, lost = C.seat_sums(bv, mo)
    assert g.to_dict("index") == {"1": {"d": 60.0, "r": 40.0}, "2": {"d": 80.0, "r": 120.0}}
    assert lost == 0.0
    with pytest.raises(RuntimeError):
        C.bef_blocks(bef, "VA")  # no block of the state: the files would not join


def test_county_carry_moves_each_block_by_its_county():
    bv = _blocks([("290010000000001", 60.0, 40.0), ("290010000000002", 30.0, 70.0), ("290030000000001", 50.0, 50.0)])
    # county 29001: 90 D / 110 R in the block votes (margin -10) -> 120 D / 80 R (margin +20, turnout x1.0)
    ct = pd.DataFrame({"county": ["29001"], "d": [120.0], "r": [80.0]})
    out = C.county_carry(bv, ct, (-4.0, 1.1)).set_index("block")
    m = 100 * (out["d"] - out["r"]) / (out["d"] + out["r"])
    assert m["290010000000001"] == pytest.approx(20 + 30)  # +30 points of swing on top of each block's own margin
    assert m["290010000000002"] == pytest.approx(-40 + 30)
    assert (out["d"] + out["r"])["290010000000001"] == pytest.approx(100.0)
    # a county with no total takes the state's change
    assert m["290030000000001"] == pytest.approx(-4.0)
    assert (out["d"] + out["r"])["290030000000001"] == pytest.approx(110.0)
    assert out.attrs["state_change_share"] == pytest.approx(100 / 300, abs=1e-4)


def test_county_carry_rejects_implausible_turnout():
    bv = _blocks([("290010000000001", 50.0, 50.0)])
    ct = pd.DataFrame({"county": ["29001"], "d": [400.0], "r": [100.0]})  # 5x the votes: not the same county
    out = C.county_carry(bv, ct, (2.0, 1.0))
    assert (100 * (out["d"] - out["r"]) / (out["d"] + out["r"])).iat[0] == pytest.approx(2.0)


def test_county_carry_kansas_city_unit():
    # Kansas City reports apart from Jackson / Clay / Platte: the four form one swing unit
    bv = _blocks([("290950000000001", 50.0, 50.0), ("290470000000001", 50.0, 50.0)])
    ct = pd.DataFrame({"county": ["29095", "29047", "29KC"], "d": [20.0, 30.0, 70.0], "r": [30.0, 30.0, 20.0]})
    out = C.county_carry(bv, ct, (0.0, 1.0))
    m = 100 * (out["d"] - out["r"]) / (out["d"] + out["r"])
    assert np.allclose(m, 100 * (120 - 80) / 200)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("St. Louis City", "stlouiscity"),
        ("St. Louis city", "stlouiscity"),
        ("St. Louis County", "stlouis"),
        ("City of Alexandria", "alexandriacity"),
        ("Doña Ana County", "donaana"),
        ("Ste. Genevieve", "stegenevieve"),
        ("Sainte Genevieve County", "stegenevieve"),
        ("LaSalle Parish", "lasalle"),
        ("La Salle", "lasalle"),
        ("Shannon[a]", "shannon"),
    ],
)
def test_county_name_keys(name, key):
    assert C._cname(name) == key
