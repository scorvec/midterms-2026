"""TEST ONLY (2026-10-05, not wired into the live model): the national House vote with uncontested seats imputed.

placeholder - filled in below
"""
from __future__ import annotations

import io, json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

from . import data_prep as D
from .fetch import open_url, report

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "static" / "uncontested"
MEDSL = RAW / "mit" / "house_1976_2018.csv"
MEDSL_URL = "https://raw.githubusercontent.com/MEDSL/constituency-returns/master/1976-2018-house.csv"
CLERK24 = RAW / "clerk" / "statistics2024.pdf"
CLERK24_URL = "https://clerk.house.gov/member_info/electionInfo/2024/statistics2024.pdf"


def fetch():
    """Download what is missing (GitHub Actions only; cached between runs)."""
    from . import bootstrap as B
    for dest, url in ((MEDSL, MEDSL_URL), (CLERK24, CLERK24_URL)):
        if dest.exists(): continue
        b = open_url(url, timeout=300); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(b)
        print(f"  {dest.relative_to(ROOT)}: {len(b) / 1e6:.1f} MB")
    for dest, url, md5 in B.FILES:
        if dest.startswith("fec/") and not (RAW / dest).exists():
            b = B._get(url); (RAW / dest).parent.mkdir(parents=True, exist_ok=True); (RAW / dest).write_bytes(b); print(f"  {dest}")
    report()


def probe():
    m = pd.read_csv(MEDSL, low_memory=False, encoding="latin-1"); print("=====BEGIN")
    print(m.columns.tolist(), len(m)); print(m.head(3).to_string())
    for y in (1996, 1998, 2016):
        q = m[m.year == y]; print(y, len(q), q.party.value_counts().head(25).to_dict())
        print(q[q.state_po == "FL"].head(20).to_string())
        print(q[q.state_po == "NY"].head(12).to_string())
        print(q[q.state_po == "LA"].head(12).to_string())
    print(m.stage.value_counts(dropna=False).to_dict(), m.special.value_counts(dropna=False).to_dict(), m.writein.value_counts(dropna=False).to_dict(), m["mode"].value_counts(dropna=False).to_dict())
    print("runoff" in m, m.get("runoff", pd.Series(dtype=object)).value_counts(dropna=False).to_dict())
    import fitz
    doc = fitz.open(CLERK24); print("clerk pages", len(doc))
    for p in (0, 1, 2, 3, 4, 5, 10, 11):
        print(f"---- page {p}"); print(doc[p].get_text()[:3000])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    {"fetch": fetch, "probe": probe}[cmd]()
