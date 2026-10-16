"""District lean builder (cd_lean_build.py): the join of block votes to a block equivalency file, the county-swing step that
carries block votes to an election with no precinct shapes, and the county-name keys that match Wikipedia's county tables to
FIPS codes."""

import zipfile

import numpy as np
import pandas as pd
import pytest

from midterms import cd_lean_build as C


def _blocks(rows):
    return pd.DataFrame(rows, columns=["block", "d", "r"])


def test_block_ids_are_15_digits():
    raw = pd.Series(["560019567001000", "1000000US560019567001001", " 560019567001002 ", "10010201001000"])
    assert C._block_id(raw).tolist() == ["560019567001000", "560019567001001", "560019567001002", "010010201001000"]


@pytest.mark.parametrize(
    "raw",
    [
        b"GEOID,CDFP\r\n290019501001000,01\r\n290019501001001,02\r\n",  # 119th / 120th style
        b"\xef\xbb\xbfGEOID|CD118FP\n290019501001000|01\n290019501001001|02\n",  # pipes, a byte-order mark, another header
        b'"BLOCKID","DISTRICT"\n"290019501001000","01"\n"290019501001001","02"\n',
    ],
)
def test_equivalency_table_releases(raw):
    t = C._bef_table(raw, "member.txt", 118)
    assert t.to_dict("list") == {"block": ["290019501001000", "290019501001001"], "cd": ["01", "02"]}


def test_equivalency_table_skips_other_members():
    assert C._bef_table(b"Block Equivalency File layout\nField 1: GEOID\n", "readme.txt", 118) is None


def test_equivalency_zip_with_one_file_per_state(tmp_path, monkeypatch):
    zp = tmp_path / "cd118.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("readme.txt", "Congressional district block equivalency files, one per state\n")
        z.writestr("CD118_MO.txt", "GEOID|CDFP\n290019501001000|01\n290019501001001|ZZ\n")
        z.writestr("CD118_WY.txt", "GEOID,CD118FP\n560019567001000,00\n")
    monkeypatch.setattr(C, "_crawl", lambda *a: ["https://example.invalid/cd118.zip"])
    monkeypatch.setattr(C.B, "url_get", lambda url, name: zp)
    monkeypatch.setattr(
        pd.DataFrame, "to_parquet", lambda self, *a, **k: None
    )  # the cache write (pyarrow is not needed here)
    b = C._bef_build(118, tmp_path / "cd118.parquet")
    assert b.to_dict("list") == {"block": ["290019501001000", "560019567001000"], "cd": ["01", "00"]}


def test_equivalency_file_check(tmp_path, monkeypatch):
    monkeypatch.setattr(C.B, "R", tmp_path)
    good = pd.DataFrame({"block": ["290019501001000"], "cd": ["01"]})
    monkeypatch.setattr(C, "_bef_build", lambda n, pq: good)
    assert C.bef(118).equals(good)
    monkeypatch.setattr(C, "_bef_build", lambda n, pq: good.assign(cd="nan"))  # the district column read as empty
    with pytest.raises(RuntimeError):
        C.bef(118)


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


def test_state_totals_count_every_ballot_line(tmp_path, monkeypatch):
    from midterms import bootstrap

    f = tmp_path / "mit" / "president_1976_2024.csv"
    f.parent.mkdir()
    f.write_text(
        "year,state_po,candidate,party_detailed,party_simplified,candidatevotes\n"
        "2016,NY,CLINTON,DEMOCRAT,DEMOCRAT,100\n"
        "2016,NY,CLINTON,WORKING FAMILIES,OTHER,10\n"
        "2016,NY,TRUMP,REPUBLICAN,REPUBLICAN,60\n"
        "2016,NY,TRUMP,CONSERVATIVE,OTHER,20\n"
        "2016,NY,STEIN,GREEN,OTHER,5\n"
        "2016,NY,,DEMOCRAT,DEMOCRAT,1\n"  # an unnamed write-in row
    )
    monkeypatch.setattr(bootstrap, "RAW", tmp_path)
    monkeypatch.setattr(C, "_MIT", None)
    m, n, d, r = C.mit_state("NY", 2016)
    assert (d, r, n) == (110.0, 80.0, 190.0)
    assert m == pytest.approx(100 * 30 / 190)


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


@pytest.mark.parametrize("year", [2018, 2022, 2026])
def test_every_seat_of_the_map_has_a_lean(year):
    from midterms import data_prep as D
    from midterms.wiki_inputs import EXPECTED

    lean = D.partisan_lean(year)
    assert len(lean) == 435 and lean["seat"].is_unique and np.isfinite(lean["lean"]).all()
    if year >= 2022:  # the 2020 apportionment
        n = lean["seat"].str[:2].value_counts()
        assert all(n[st] == EXPECTED.get(st, 1) for st in n.index)


@pytest.mark.parametrize("cycle", sorted(C.HIST))
def test_hist_lean_uses_only_earlier_elections(cycle):
    yn, yo = C.HIST[cycle]
    assert yo < yn < cycle % 100
    h = pd.read_csv(C.STATIC / "cd_lean_hist.csv")
    h = h[h["cycle"] == cycle]
    later = [y for y in (12, 16, 20) if y not in (yn, yo)]
    assert h[[f"pres{y}{s}" for y in later for s in ("_d", "_r")]].isna().all().all()

    def nat(y):
        return 100 * (h[f"pres{y}_d"].sum() - h[f"pres{y}_r"].sum()) / (h[f"pres{y}_d"].sum() + h[f"pres{y}_r"].sum())

    want = 0.75 * (h[f"pres{yn}_margin"] - nat(yn)) + 0.25 * (h[f"pres{yo}_margin"] - nat(yo))
    assert np.allclose(h["lean_75_25"], want, atol=0.01)


def test_no_cook_pvi_is_published():
    from pathlib import Path

    web = Path(C.STATIC).parents[1] / "web" / "data"
    for f in [web / "model.json", *sorted((web / "history").glob("model_*.json"))]:
        assert '"cook_pvi"' not in f.read_text(), f.name
