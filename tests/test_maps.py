"""The map in force for 2026 is recorded in two places that must agree: cd_lean_build.MAP_STATUS (the dated sources, used by
the lean build) and demog.MAP_FROM_CD119 (used by every other district builder: urbanization, shapes, demographics); and the
committed district tables must carry every 2026 seat once."""

import pandas as pd

from midterms import cd_lean_build as C
from midterms import demog
from midterms.paths import STATIC


def test_map_status_agrees_with_cd119_override():
    on_119 = {st for st, v in C.MAP_STATUS.items() if v["map"] == 119}
    assert {demog.FIPS[f] for f in demog.MAP_FROM_CD119} == on_119


def test_district_tables_cover_435_seats_once():
    lean = pd.read_csv(STATIC / "cd_lean_2026.csv")
    seats = set(lean["seat"])
    assert len(seats) == 435
    for name in (
        "district_groups_2026.csv",
        "district_wnc_2026.csv",
        "district_heat_oil_2026.csv",
        "urbanization_2026.csv",
    ):
        t = pd.read_csv(STATIC / name)
        assert t["seat"].is_unique, name
        assert set(t["seat"]) == seats, name


def test_missouri_lean_is_on_its_2022_lines():
    lean = pd.read_csv(STATIC / "cd_lean_2026.csv").set_index("seat")
    assert (
        not lean.loc["MO-5", "redrawn"] and lean.loc["MO-5", "lean_75_25"] > 15
    )  # the Kansas City seat, not the cracked one
