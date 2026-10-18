"""Published-source watch for polls held out until their pollster's own release confirms them.

The Center Square Voters' Voice dashboard (thecentersquare.com/polls) charts its generic-ballot series with Datawrapper, one
row per wave ("Aug 2026  42%  47%"). Polling USA and pollresults.org posted the Oct 5-7 wave as 47-42 on 2026-10-10 while the
dashboard still ended at the Aug 12-16 wave (also 47-42), so poll_corrections.csv holds that poll (action=hold,
value "centersquare:Oct 2026") until the dashboard carries an "Oct 2026" row. Each run reads the chart and writes the whole
series to data/state/centersquare_ballot.csv (label, dem, rep); a fetch failure keeps the last file, so the hold just stays.
    python -m midterms.source_watch
"""

from __future__ import annotations
import csv
import re
from pathlib import Path

from . import fetch

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "state"
_done = False


def _get(url: str) -> str:
    """GET with the project's User-Agent, counted in fetch.STATS like every other download."""
    return fetch.open_url(url, timeout=30).decode("utf-8", "replace")


def centersquare() -> list[tuple[str, float, float]] | None:
    """The dashboard's generic-ballot series as (label, dem, rep), or None when the page or chart can't be read."""
    page = _get("https://www.thecentersquare.com/polls/")
    for cid in dict.fromkeys(re.findall(r"datawrapper\.dwcdn\.net/([A-Za-z0-9]+/\d+)/", page)):
        try:
            rows = list(
                csv.reader(_get(f"https://datawrapper.dwcdn.net/{cid}/dataset.csv").splitlines(), delimiter="\t")
            )
        except Exception:
            continue
        if not rows or len(rows[0]) < 3:
            continue
        hdr = [h.lower() for h in rows[0]]
        di = next((i for i, h in enumerate(hdr) if "democratic candidate" in h), None)
        ri = next((i for i, h in enumerate(hdr) if "republican candidate" in h), None)
        if di is None or ri is None:
            continue

        def num(s):
            return float(s.strip().rstrip("%"))

        return [(r[0].strip(), num(r[di]), num(r[ri])) for r in rows[1:] if len(r) > max(di, ri) and r[di].strip()]
    return None


def run(quiet: bool = False) -> None:
    """Refresh every watched source once per process. Never raises: a failed read keeps the previous file."""
    global _done
    if _done:
        return
    _done = True
    f = STATE / "centersquare_ballot.csv"
    try:
        s = centersquare()
    except Exception as e:
        print(f"  source watch: Center Square dashboard unreadable ({str(e)[:80]}) - holds stay as they are")
        return
    if not s:
        print("  source watch: Center Square dashboard has no generic-ballot chart - holds stay as they are")
        return
    old = set()
    if f.exists():
        with open(f) as fh:
            old = {r["label"] for r in csv.DictReader(fh)}
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["label", "dem", "rep"])
        w.writerows(s)
    new = [r for r in s if r[0] not in old]
    if new and old:
        for lab, d, r in new:
            print(f"::warning::Center Square dashboard: new generic-ballot wave {lab} D {d:g} R {r:g}")
    if not quiet:
        print(
            f"  source watch: Center Square generic ballot, {len(s)} waves, newest {s[-1][0]} D {s[-1][1]:g} R {s[-1][2]:g}"
        )


def confirmed(key: str) -> tuple[float, float] | None:
    """(dem, rep) published for a hold key like "centersquare:Oct 2026", or None while it is unpublished."""
    src, _, label = key.partition(":")
    f = STATE / f"{src.strip()}_ballot.csv"
    if not f.exists():
        return None
    with open(f) as fh:
        for r in csv.DictReader(fh):
            if r["label"].strip().lower() == label.strip().lower():
                return float(r["dem"]), float(r["rep"])
    return None


if __name__ == "__main__":
    run()
