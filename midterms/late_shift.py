"""How far a House count moves after it first appears on Wikipedia, in the states that count most ballots after Election Day.

    python -m midterms.late_shift        # -> data/static/late_shift.json, and a table of the shifts

For each House election from 2018 to 2024 in the late-counting states (live_obs.LATE but Alaska, whose ranked-choice races
carry no count), the state's House page on Wikipedia is read as it stood every day for the first week after Election Day,
then every third day to the end of the year, and as it stood on 1 February (the certified count). Each district's general
election box is taken the first time it is filled in (every candidate's vote and the total): the margin then (Democratic
minus Republican, points of every ballot, the model's unit) against the final margin, and the share of the final total
counted then. The shift is final minus first; a race without both a Democrat and a Republican is left out. The fit is the
shift over the share still out, k in shift = k (1 - share counted): the late ballots' margin less the first count's. It is
fitted by state and by election, and tested across elections (see fit).
Revisions come from the MediaWiki API (serial requests with the project User-Agent).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.parse

import numpy as np

from . import fetch as F
from .live_obs import LATE
from .paths import STATIC
from .results_wiki import NAME
from .wiki_polls import API

OUT = STATIC / "late_shift.json"
ELECTION = {2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
STATES = sorted(LATE - {"AK"})
SAMPLE_DAYS = [
    d + 0.5 for d in (*range(1, 8), *range(10, 56, 3))
]  # noon UTC after Election Day: daily, then every 3 days
BOX = re.compile(r"\{\{\s*Election box begin(.*?)\{\{\s*Election box end", re.S | re.I)
CAND = re.compile(r"\{\{\s*Election box (?:winning )?candidate(?: with party link)?[^|]*\|(.*?)\}\}", re.S | re.I)
TOTAL = re.compile(r"\{\{\s*Election box total[^|]*\|(.*?)\}\}", re.S | re.I)
DISTRICT = re.compile(r"^=+\s*(?:\[\[[^|\]]*\|)?District\s+(\d+)(?:\]\])?\s*=+\s*$", re.M | re.I)  # may be a link


def _api(**q) -> dict:
    q |= {"action": "query", "format": "json", "formatversion": 2}
    d = json.loads(F.open_url(API + "?" + urllib.parse.urlencode(q), timeout=120))
    time.sleep(1.0)
    return d


def revisions(title: str, start: str, end: str) -> list[tuple[str, int]]:
    """[(timestamp, revision id)] of a page between two times, oldest first (redirects followed)."""
    out, cont = [], {}
    while True:
        d = _api(
            prop="revisions",
            titles=title,
            redirects=1,
            rvprop="ids|timestamp",
            rvlimit="max",
            rvdir="newer",
            rvstart=start,
            rvend=end,
            **cont,
        )
        for p in d["query"]["pages"]:
            out += [(r["timestamp"], r["revid"]) for r in p.get("revisions", [])]
        if "continue" not in d:
            return out
        cont = {"rvcontinue": d["continue"]["rvcontinue"]}


def contents(revids: list[int]) -> dict[int, str]:
    """revision id -> wikitext, a few revisions per request (large pages)."""
    out = {}
    for i in range(0, len(revids), 4):
        d = _api(prop="revisions", revids="|".join(map(str, revids[i : i + 4])), rvprop="ids|content", rvslots="main")
        for p in d["query"]["pages"]:
            for r in p.get("revisions", []):
                out[r["revid"]] = r["slots"]["main"].get("content", "")
    return out


def _param(body: str, name: str) -> str:
    m = re.search(rf"\|\s*{name}\s*=([^|]*)", "|" + body)
    return m[1].strip() if m else ""


def _votes(s: str):
    s = re.sub(r"<ref.*?(?:/>|</ref>)|'''|''|[,\s]", "", s, flags=re.S)
    m = re.match(r"\d+", s)
    return int(m[0]) if m else None


def districts(text: str) -> dict[int, tuple[float, int]]:
    """district -> (Democratic minus Republican margin, points of every ballot; total votes) from the last filled-in
    general election box under each District heading; a box titled as a primary is skipped."""
    heads = [(m.start(), int(m[1])) for m in DISTRICT.finditer(text)]
    out = {}
    for b in BOX.finditer(text):
        title = _param(b[1].split("}}")[0], "title")
        if re.search(r"primary|runoff|special|convention", title, re.I):
            continue
        dist = next((n for pos, n in reversed(heads) if pos < b.start()), None)
        if dist is None:
            continue
        rows = [(_param(c, "party"), _votes(_param(c, "votes"))) for c in CAND.findall(b[0])]
        tot = TOTAL.search(b[0])
        total = _votes(_param(tot[1], "votes")) if tot else None
        if not rows or not total or any(v is None for _p, v in rows):
            out.pop(dist, None)  # the district's latest box is not filled in yet
            continue
        d = sum(v for p, v in rows if p.startswith("Democratic"))
        r = sum(v for p, v in rows if p.startswith("Republican"))
        if d and r and d + r <= total:
            out[dist] = (100 * (d - r) / total, total)
        else:
            out.pop(dist, None)
    return out


def collect() -> list[dict]:
    rows = []
    for year, day in ELECTION.items():
        e = dt.datetime.fromisoformat(day + "T00:00:00+00:00")
        final_at = dt.datetime(year + 1, 2, 1, tzinfo=dt.UTC)
        for st in STATES:
            title = f"{year} United States House of Representatives elections in {NAME[st]}"
            revs = revisions(title, f"{e - dt.timedelta(days=30):%Y-%m-%dT%H:%M:%SZ}", f"{final_at:%Y-%m-%dT%H:%M:%SZ}")
            if not revs:
                print(f"  {title}: no revisions")
                continue
            times = [*(e + dt.timedelta(days=x) for x in SAMPLE_DAYS), final_at]
            picks = []
            for t in times:  # the page as it stood then: its last revision at or before t
                before = [rid for ts, rid in revs if dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) <= t]
                picks.append(before[-1] if before else None)
            text = contents(sorted({p for p in picks if p}))
            seen = [districts(text[p]) if p in text else {} for p in picks]
            final = seen[-1]
            for dist, (m_fin, t_fin) in final.items():
                first = next(((x, s[dist]) for x, s in zip(SAMPLE_DAYS, seen[:-1]) if dist in s), None)
                if first is None:
                    continue
                x, (m0, t0) = first
                rows.append(
                    {
                        "state": st,
                        "year": year,
                        "district": dist,
                        "days": x,
                        "share": round(t0 / t_fin, 4),
                        "margin_first": round(m0, 2),
                        "margin_final": round(m_fin, 2),
                        "total_final": t_fin,
                    }
                )
            print(f"  {year} {st}: {sum(r['state'] == st and r['year'] == year for r in rows)} districts")
    return rows


def _slope(g: list[dict]) -> dict:
    """k in shift = k (1 - share counted), least squares through the origin, with its standard error and t."""
    if len(g) < 5:
        return {"n": len(g)}
    x = np.array([1 - r["share"] for r in g])
    y = np.array([r["margin_final"] - r["margin_first"] for r in g])
    k = float(x @ y / (x @ x))
    se = float(np.sqrt(((y - k * x) ** 2).sum() / (len(g) - 1) / (x @ x)))
    return {
        "n": len(g),
        "k": round(k, 2),
        "se": round(se, 2),
        "t": round(k / se, 2),
        "mean_out": round(float(x.mean()), 3),
    }


def fit(rows: list[dict]) -> dict:
    """The slope by state, by election and pooled, from counts with at least 1 % of the final total still out. The shift
    moves with the election (who votes late changes), so the test is across elections: the mean of the yearly slopes over
    its standard error (as many degrees of freedom as elections less one)."""
    g = [r for r in rows if r["share"] <= 0.99]
    by_year = {y: _slope([r for r in g if r["year"] == y]) for y in ELECTION}
    ks = [f["k"] for f in by_year.values() if "k" in f]
    across = {"years": len(ks)}
    if len(ks) >= 2:
        se = float(np.std(ks, ddof=1) / np.sqrt(len(ks)))
        across |= {"k": round(float(np.mean(ks)), 2), "se": round(se, 2), "t": round(float(np.mean(ks)) / se, 2)}
    return {
        "by_state": {st: _slope([r for r in g if r["state"] == st]) for st in STATES},
        "by_year": by_year,
        "pooled": _slope(g),
        "across_years": across,
    }


def main():
    rows = collect()
    res = fit(rows)
    OUT.write_text(json.dumps({"fit": res, "races": rows}, indent=1) + "\n")
    print(f"{OUT.relative_to(STATIC.parent.parent)}: {len(rows)} district counts")
    for part in ("by_state", "by_year"):
        for key, f in res[part].items():
            line = f"{f['k']:7.2f} {f['se']:6.2f} {f['t']:6.2f} {f['mean_out']:6.3f}" if "k" in f else ""
            print(f"{key!s:6s} {f['n']:4d} {line}")
    print("pooled", res["pooled"])
    print("across elections", res["across_years"])


if __name__ == "__main__":
    main()
