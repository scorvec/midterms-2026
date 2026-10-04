"""First-seen ledger of race polls: data/state/race_poll_seen.csv (committed).

The reconstructed history (backfill.py) needs to know WHEN each race poll could first have been known. Two records come
from this repository itself: the day a poll first appeared in the published poll list (web/data/polls.json) and the day a
hand-entered poll was added to data/manual/*.csv. Each daily run appends the polls it sees for the first time.

Columns: office, seat, pollster, end, rec (first-seen date), kind:
  polls.json        first seen in the published poll list on `rec`
  polls.json:first  already in the FIRST published poll list (2026-09-25): proves only "by then" (an upper bound)
  manual            hand-entered row added on `rec`
The rows before 2026-10-04 were seeded from the history of the original working copy of this model.
"""
from __future__ import annotations

import json
import datetime as dt
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "state" / "race_poll_seen.csv"
COLS = ["office", "seat", "pollster", "end", "rec", "kind"]


def load() -> pd.DataFrame:
    if not LEDGER.exists(): return pd.DataFrame(columns=COLS)
    return pd.read_csv(LEDGER, dtype=str, keep_default_na=False)


def update(day: str | None = None) -> int:
    """Append polls first seen today (polls.json asof, else today). Returns the number of new rows."""
    L = load(); seen = set(zip(L.office, L.seat, L.pollster, L.end, L.kind.str.replace(":first", "", regex=False)))
    pj = ROOT / "web" / "data" / "polls.json"; new = []
    if pj.exists():
        d = json.loads(pj.read_text()); day = day or d.get("asof")
        for r in d.get("races", []):
            for q in r.get("polls", []):
                k = (r["office"], str(r["seat"]), str(q["pollster"]), str(q["end"])[:10], "polls.json")
                if k not in seen: seen.add(k); new.append(k[:4] + (day, "polls.json"))
    day = day or dt.date.today().isoformat()
    for f in ("race_polls.csv", "substate_polls.csv"):
        fp = ROOT / "data" / "manual" / f
        if not fp.exists(): continue
        for r in pd.read_csv(fp).itertuples():
            k = (str(r.office), str(r.state), str(r.pollster), str(pd.Timestamp(r.end_date).date()), "manual")
            if k not in seen: seen.add(k); new.append(k[:4] + (day, "manual"))
    if new:
        out = pd.concat([L, pd.DataFrame(new, columns=COLS)], ignore_index=True)
        LEDGER.parent.mkdir(parents=True, exist_ok=True); out.to_csv(LEDGER, index=False)
    print(f"poll ledger: {len(new)} new rows ({len(L) + len(new)} in all)")
    return len(new)


if __name__ == "__main__":
    update()
