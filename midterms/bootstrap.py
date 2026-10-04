"""Download the fixed public inputs into data/raw/ (git-ignored) if they are missing. Run by the workflow before every
daily run (`actions/cache` keeps data/raw between runs, so normally nothing is downloaded); safe to rerun.

    python -m midterms.bootstrap            # fetch what is missing, verify checksums
    python -m midterms.bootstrap --force    # re-download everything

Sources (all historical, none changes):
  538 / ABC News poll archives (CC BY 4.0): projects.fivethirtyeight.com/polls/data/*_historical.csv, which no longer
      exist at the source, from the Internet Archive's copies of 2025-03-06
  538 data repository (CC BY 4.0): github.com/fivethirtyeight/data - raw_polls, pollster ratings, partisan lean 2018,
      urbanization index 2022
  FEC "Federal Elections" results workbooks 2018/2020/2022 (public domain)
  MIT Election Data and Science Lab, U.S. President 1976-2024 (Harvard Dataverse doi:10.7910/DVN/42MVDX, CC0)
  538 archived pollster-rating vintages + the pollresults.org API's pollster table (pollster_quality.fetch_sources)
"""
from __future__ import annotations

import hashlib
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "scorvec.com midterm model (+https://scorvec.com/midterms/about.html)"}
WB = "https://web.archive.org/web/{}id_/https://projects.fivethirtyeight.com/polls/data/{}.csv"
GH = "https://raw.githubusercontent.com/fivethirtyeight/data/master/"
FEC = "https://www.fec.gov/resources/cms-content/documents/federalelections{}.xlsx"
# (destination under data/raw, url, md5 of the copy the model was built on)
FILES = [
    ("538/generic_ballot_polls_historical.csv", WB.format("20250306062814", "generic_ballot_polls_historical"), "db6f32f8b691465fb5904d7d700b9b6f"),
    ("538/governor_polls_historical.csv", WB.format("20250306062821", "governor_polls_historical"), "3071c7faf166c3a23aac152c2aa82a10"),
    ("538/house_polls_historical.csv", WB.format("20250306062819", "house_polls_historical"), "14892143b52e494dfaaa271c2f13cb6c"),
    ("538/president_approval_polls_historical.csv", WB.format("20250306062815", "president_approval_polls_historical"), "180b14e59bf2beae1630c31f7af23a1f"),
    ("538/senate_polls_historical.csv", WB.format("20250306062822", "senate_polls_historical"), "56f43b321fe11ebb1cfc743d1e6edb23"),
    ("538repo/raw_polls.csv", GH + "pollster-ratings/raw_polls.csv", None),
    ("538repo/pollster-ratings-combined.csv", GH + "pollster-ratings/pollster-ratings-combined.csv", None),
    ("538repo/partisan_lean_DISTRICTS_2018.csv", GH + "partisan-lean/2018/fivethirtyeight_partisan_lean_DISTRICTS.csv", None),
    ("538repo/urbanization-index-2022.csv", GH + "district-urbanization-index-2022/urbanization-index-2022.csv", None),
    ("fec/federalelections2018.xlsx", FEC.format(2018), "dae569593e446d0b7d1e0aee2d1fc79b"),
    ("fec/federalelections2020.xlsx", FEC.format(2020), "ed533051916a6d12b1dbec5dee92eb84"),
    ("fec/federalelections2022.xlsx", FEC.format(2022), "c87371c7736e7f8f4136f96ce0b6327e"),
    ("mit/president_1976_2024.csv", "https://dataverse.harvard.edu/api/access/datafile/13887042", "405af83db7625cb35d8c19a5ebe029ff"),
]
# git blob ids of the 538 repository files the model was built on (checked, not enforced: the repository is archived)
GIT_SHA = {"538repo/raw_polls.csv": "08735ea503ad64c67cfa285fd75c0a270fdca245",
           "538repo/pollster-ratings-combined.csv": "6ca53b1aa5ac70b081a62f4e7430cf300d1b1743",
           "538repo/partisan_lean_DISTRICTS_2018.csv": "381f9525cdb5e9696a41d2b4a20b42d252a9fa51",
           "538repo/urbanization-index-2022.csv": "4de582ef4c21cd9b6f5b31e42c19ea048548b498"}


def _get(url: str) -> bytes:
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r: return r.read()
        except Exception as e:
            if k == 3: raise
            print(f"  retry {url[:80]} ({str(e)[:60]})"); time.sleep(15 * (k + 1))


def _git_blob(b: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def main(force=False) -> int:
    bad = 0
    for dest, url, md5 in FILES:
        f = RAW / dest
        if f.exists() and not force: continue
        b = _get(url); time.sleep(2)
        if md5 and hashlib.md5(b).hexdigest() != md5:
            print(f"!! {dest}: checksum differs from the copy the model was built on"); bad += 1
        if dest in GIT_SHA and _git_blob(b) != GIT_SHA[dest]:
            print(f"  note: {dest} changed upstream since the model was built")
        f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b); print(f"  {dest}: {len(b) / 1e6:.1f} MB")
    if force or not (RAW / "ratings" / "pollresults_api_pollsters.json").exists() or not (RAW / "ratings" / "538" / "combined.csv").exists():
        from . import pollster_quality as PQ
        PQ.fetch_sources(); print("  ratings: 538 vintages + pollresults.org pollster table")
    print("bootstrap:", "OK" if not bad else f"{bad} checksum mismatch(es)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))
