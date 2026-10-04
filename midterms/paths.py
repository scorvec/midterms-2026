"""Repository layout. Everything is relative to the repository root.

data/static  committed, read-only inputs (small tables derived once from public sources; see README "Data")
data/state   committed, accumulating poll records that the daily run appends to (feeds only reach back weeks)
data/manual  committed, hand-entered polls
data/raw     downloads (git-ignored; fetched by the workflow, see bootstrap.py)
data/cache   intermediate files (git-ignored; rebuilt by the run)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "data" / "static"
STATE = ROOT / "data" / "state"
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "cache"
