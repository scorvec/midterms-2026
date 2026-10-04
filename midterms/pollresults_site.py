"""pollresults.org race pages as a race-poll source (2026-09-26, user: "This site will be quite useful for the midterms forecast
poll updates" -> "Yes go ahead").

The same people run the Political Poll Bot on Bluesky (bluesky_polls.pollresults_build), which covers only the posts of the
last 30 days and rounds to whole points. The website carries every poll of every race with the published decimals, the sample
size and population, and the primary/general stage, in a server-rendered table per race:

    /races/senate/<state>/   /races/governor/<state>/   /races/house/<state>-<district>/
    Pollster | Grade | Field dates | Stage | Sample ("n=619 LV") | Results ("Collins (Susan M.) (R): 48.66% ...") | Source

Data credit: the site attributes its poll data to The New York Times under CC BY 4.0 (and is not affiliated with the Times);
the about page says so. robots.txt allows everything; a page is fetched again only when the sitemap's lastmod is on or after
the day of the cached copy (else at most once per ~20 h), one at a time.

Rows come out in bluesky_polls' schema, candidates matched to each race's ballot by surname exactly as the bot feed is
(a poll naming someone who is not on the ballot is dropped), versions of one survey (LV/RV, with and without leaners)
averaged into one row.
"""
import re, time, html as H
import datetime as dt
import urllib.request
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://pollresults.org"
CACHE = ROOT / "data" / "raw" / "pollresults_site"
MAX_AGE_H = 20
_SKIP = re.compile(r"^(don'?t know|undecided|someone else|other|refused|would not vote|none|not sure|no answer|skipped)", re.I)


_LASTMOD = None


def lastmod() -> dict:
    """{path: last-modified date} from the site's sitemap (one request per run): a race page is downloaded again only when the
    sitemap says it changed on or after the day of the cached copy."""
    global _LASTMOD
    if _LASTMOD is None:
        from . import fetch as F
        try:
            x = F.open_url(SITE + "/sitemap.xml", timeout=60).decode("utf-8", "replace")
            _LASTMOD = {u.replace(SITE, ""): d for u, d in re.findall(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>", x)}
        except Exception as e:
            print("  pollresults sitemap failed:", str(e)[:80]); _LASTMOD = {}
    return _LASTMOD


def _get(path: str, max_age_h: float = MAX_AGE_H) -> str:
    from . import fetch as F
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (re.sub(r"[^a-z0-9]+", "_", path.strip("/").lower()) or "index")
    f = f.with_suffix(".html")
    if f.exists():
        age_h = (time.time() - f.stat().st_mtime) / 3600
        lm = lastmod().get(path)
        cached_day = dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
        if age_h < max_age_h or (lm is not None and lm[:10] < cached_day):
            return f.read_text()
    txt = F.open_url(SITE + path, timeout=60).decode("utf-8", "replace")
    f.write_text(txt); time.sleep(1.0)
    return txt


def races():
    """[(office, seat, path)] for every 2026 Senate, governor and House race on the index."""
    from .data_prep import _ST
    slug2st = {k.lower().replace(" ", "-"): v for k, v in _ST.items()}
    out = []
    for path, off, slug, text in re.findall(r'<a href="(/races/(senate|governor|house)/([^/"]+)/)">([^<]+)</a>', _get("/races/")):
        if not text.strip().startswith("2026"):
            continue
        if off in ("senate", "governor"):
            st = slug2st.get(slug)
            if st: out.append((off, st, path))
        else:
            m = re.match(r"(.+)-(\d+|at-large)$", slug)
            st = slug2st.get(m.group(1)) if m else None
            if st: out.append(("house", f"{st}-{1 if m.group(2) == 'at-large' else int(m.group(2))}", path))
    return out


def _cands(results: str):
    """'Collins (Susan M.) (R): 48.66% Jackson (Troy) (D): 45.92% Don't know: 4.91%' ->
    [('Susan M. Collins', 'R', 48.66), ('Troy Jackson', 'D', 45.92)] (undecided and the like dropped)."""
    out = []
    for label, v in re.findall(r"\s*([^%]+?):\s*([\d.]+)%", results):
        label = label.strip()
        if _SKIP.match(label):
            continue
        pm = re.search(r"\s*\(([A-Z]{1,4})\)\s*$", label)
        party = pm.group(1) if pm else None
        name = label[:pm.start()] if pm else label
        g = re.match(r"^(.+?)\s*\(([^)]*)\)\s*$", name)                 # "Surname (Given)" -> "Given Surname"
        name = f"{g.group(2)} {g.group(1)}".strip() if g else name.strip()
        out.append((name, party, float(v)))
    return out


def _end(dates: str):
    """'Sep 15 - 22, 2026' / 'Aug 28 - Sep 14, 2026' / 'Sep 21, 2026' -> the end date."""
    m = re.match(r"([A-Z][a-z]{2})\.? (\d{1,2})(?:\s*-\s*(?:([A-Z][a-z]{2})\.? )?(\d{1,2}))?, (\d{4})", dates.strip())
    if not m:
        return pd.NaT
    return pd.to_datetime(f"{m.group(3) or m.group(1)} {m.group(4) or m.group(2)} {m.group(5)}", errors="coerce")


def race_rows(path: str):
    """Every poll row on one race page: (pollster, end, stage, n, pop, results, source url)."""
    s = _get(path)
    i = s.find('<table id="polls-table"')
    if i < 0:
        return []
    t = s[i:s.index("</table>", i)]
    rows = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(cells) < 6:
            continue
        txt = [" ".join(H.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in cells]
        link = re.search(r'href="([^"]+)"', cells[-1])
        n = re.search(r"n\s*=\s*([\d,]+)\s*(LV|RV|A|V)?", txt[4])
        rows.append({"pollster": txt[0], "end_date": _end(txt[2]), "stage": txt[3],
                     "n": int(n.group(1).replace(",", "")) if n else None, "pop": (n.group(2) or "").lower() if n else None,
                     "cands": _cands(txt[5]), "url": link.group(1) if link else None})
    return rows


def _sponsor_map():
    """{pollster name: 'D' | 'R' | ''} from every race poll the model already carries (web/data/polls.json, whose names come
    tagged from Wikipedia / Polling USA). pollresults.org prints no sponsor tag ('Tulchin Research', 'DCCC Targeting Team'
    where Wikipedia has '... (D)'), and an untagged partisan poll would skip the model's +-4 point sponsor correction and
    count at full weight. A pollster gets a tag when most of its known polls carry it."""
    import json
    f = ROOT / "web" / "data" / "polls.json"
    if not f.exists():
        return {}
    votes = {}
    for r in json.loads(f.read_text()).get("races", []):
        for q in r.get("polls", []):
            base = re.sub(r"\s*\((?:D|R)\)\s*$", "", q.get("pollster", "")).strip()
            votes.setdefault(base, []).append(q.get("sponsor") or "")
    out = {}
    for base, v in votes.items():
        top = max(("D", "R"), key=v.count)
        out[base] = top if v.count(top) * 2 >= len(v) else ""
    return out


def _tag(pollster: str, smap: dict) -> str:
    from .model import _same_pollster
    if re.search(r"\((?:D|R)\)\s*$", pollster):
        return pollster
    if re.search(r"\b(DCCC|DSCC|DGA|DLCC|Democratic)\b", pollster):
        return pollster + " (D)"
    if re.search(r"\b(NRCC|NRSC|RGA|RSLC|Republican)\b", pollster):
        return pollster + " (R)"
    hits = [t for base, t in smap.items() if _same_pollster(pollster, base)]
    if hits:
        top = max(("D", "R", ""), key=hits.count)
        if top and hits.count(top) * 2 >= len(hits):
            return f"{pollster} ({top})"
    return pollster


def build(days: int = 150):
    """Race polls ending in the last `days` days, in bluesky_polls' schema (src='pollresults_site')."""
    from .bluesky_polls import _ballots
    from .model import _sur5
    ballots = _ballots(); smap = _sponsor_map()
    since = pd.Timestamp(dt.date.today() - dt.timedelta(days=days))
    out, pages, dropped = [], 0, 0
    for office, seat, path in races():
        ballot = {_sur5(n): pt for n, pt in ballots.get((office, seat), [])}
        if not ballot:
            continue
        try:
            rows = race_rows(path); pages += 1
        except Exception as e:                                           # noqa: BLE001  one page must not sink the rest
            print(f"  pollresults.org {path}: {str(e)[:80]}"); continue
        for p in rows:
            if not p["stage"].lower().startswith("general") or pd.isna(p["end_date"]) or p["end_date"] < since:
                continue
            named = [(n, ballot.get(_sur5(n)), v) for n, _, v in p["cands"]]
            d = [c for c in named if c[1] == "D"]; r = [c for c in named if c[1] == "R"]; ind = [c for c in named if c[1] == "I"]
            if not r or not (d or ind):
                dropped += 1; continue
            rep = max(r, key=lambda c: c[2])
            chal = (max(d, key=lambda c: c[2]) if d and (not ind or max(d, key=lambda c: c[2])[2] >= max(ind, key=lambda c: c[2])[2])
                    else max(ind, key=lambda c: c[2]))
            tot = sum(c[2] for c in p["cands"])
            out.append({"state": seat.split("-")[0], "office": office, "seat": seat,
                        "pollster": _tag(p["pollster"], smap), "end_date": p["end_date"],
                        "dem_name": chal[0], "rep_name": rep[0], "challenger_party": chal[1], "dem": chal[2], "rep": rep[2],
                        "other": round(tot - chal[2] - rep[2], 2), "und": round(max(0.0, 100 - tot), 2), "n": p["n"], "pop": p["pop"],
                        "grade": 1.5, "posted": p["end_date"].strftime("%Y-%m-%d"), "released_late": False, "uri": p["url"],
                        "src": "pollresults_site"})
    df = pd.DataFrame(out)
    if len(df):
        key = ["office", "seat", "pollster", "end_date", "dem_name", "rep_name"]
        num = {c: "mean" for c in ("dem", "rep", "other", "und")}
        first = {c: "first" for c in df.columns if c not in key and c not in num}
        df = df.groupby(key, as_index=False).agg({**num, **first})
        df[["dem", "rep", "other", "und"]] = df[["dem", "rep", "other", "und"]].round(2)
        df["margin"] = df.dem - df.rep
    print(f"pollresults.org site: {pages} race pages, {len(df)} general-election polls in {days} days"
          f" ({dropped} dropped: a candidate not on the ballot)" + (f" - {df.office.value_counts().to_dict()}" if len(df) else ""))
    return df


if __name__ == "__main__":
    d = build()
    print(d.sort_values("end_date").tail(20)[["office", "seat", "pollster", "end_date", "dem_name", "dem", "rep_name", "rep", "n", "pop"]].to_string(index=False))
