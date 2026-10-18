"""Release dates of the reconstructed history (backfill.py): race polls through the (office, seat) index, generic polls
through the per-pollster index - both built with groupby comprehensions that dict() cannot replace."""

import json

import pandas as pd
import pytest

from midterms import backfill as B


@pytest.fixture
def race_records(monkeypatch):
    rows = [
        ("house", "AZ-1", "Siena College", "2026-10-01", "2026-10-03", "votehub"),
        ("governor", "MI", "Emerson College", "2026-09-20", "2026-09-21", "ledger:first"),
        ("senate", "NC", "Cygnal", "2026-09-01", "2026-09-25", "votehub"),  # 24 days on: catalogue backfill
    ]
    R = pd.DataFrame(rows, columns=["office", "seat", "pollster", "end", "rec", "kind"])
    R["end"], R["rec"] = pd.to_datetime(R["end"]), pd.to_datetime(R["rec"])
    monkeypatch.setattr(B, "race_records", lambda: R)
    B._race_index.cache_clear()
    B._REL.clear()
    yield
    B._race_index.cache_clear()
    B._REL.clear()


@pytest.mark.usefixtures("race_records")
@pytest.mark.parametrize(
    ("office", "seat", "pollster", "end", "release"),
    [
        ("house", "AZ-1", "Siena", "2026-10-01", "2026-10-03"),  # recorded: the record's date
        ("senate", "GA", "Quinnipiac", "2026-10-01", "2026-10-04"),  # no record: end + LAG_DAYS
        ("governor", "MI", "Emerson", "2026-09-20", "2026-09-21"),  # first seen before the estimate: capped
        ("senate", "NC", "Cygnal", "2026-09-01", "2026-09-04"),  # a record past RECORD_MAX_LAG is not the release
    ],
)
def test_race_release(office, seat, pollster, end, release):
    assert B.release(office, seat, pollster, end) == pd.Timestamp(release)


def test_generic_release(monkeypatch, tmp_path):
    (tmp_path / "data" / "cache").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "votehub").mkdir(parents=True)
    (tmp_path / "data" / "state").mkdir(parents=True)
    pd.DataFrame(
        {
            "pollster": ["siena", "quinnipiac", "mclaughlin"],
            "end_date": ["2026-10-01", "2026-10-05", "2026-10-10"],
            "start_date": ["2026-09-28", "2026-10-02", "2026-10-07"],
            "partisan": [None, None, "REP"],
        }
    ).to_csv(tmp_path / "data" / "cache" / "generic_polls.csv", index=False)
    (tmp_path / "data" / "raw" / "votehub" / "generic-ballot.json").write_text(
        json.dumps([{"pollster": "Siena College", "end_date": "2026-10-01", "created_at": "2026-10-02"}])
    )
    pd.DataFrame({"pollster": ["McLaughlin"], "date": ["2026-10-10"], "posted": ["2026-10-11 15:19:26"]}).to_csv(
        tmp_path / "data" / "state" / "bluesky_generic.csv", index=False
    )
    pd.DataFrame({"pollster": [], "end_date": [], "posted": []}).to_csv(
        tmp_path / "data" / "state" / "pollresults_generic.csv", index=False
    )
    monkeypatch.setattr(B, "ROOT", tmp_path)
    B.generic_frame.cache_clear()
    try:
        G = B.generic_frame()
    finally:
        B.generic_frame.cache_clear()
    assert dict(zip(G["pollster"], G["release"].dt.strftime("%Y-%m-%d"))) == {
        "siena": "2026-10-02",  # VoteHub's record
        "quinnipiac": "2026-10-08",  # no record: end + LAG_DAYS
        "mclaughlin": "2026-10-11",  # the Bluesky post
    }
