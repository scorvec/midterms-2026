"""Polls from Wikipedia race pages (the 538 archive ends in Sept 2024).

Senate: "2026 United States Senate election in {State}" (special: "... special election in ...").
Each page has 'Polling' tables: columns Poll source | Date(s) administered | Sample size |
Margin of error | candidate columns (D, R, ...) | Other | Undecided. We keep general-election
tables that contain the two major nominees, and return one row per poll with dem, rep, margin.
Cached per page under data/cache/wiki/ (re-fetched when older than a day).
"""
from __future__ import annotations
import re, io, time, json, datetime as dt
from pathlib import Path
import pandas as pd, numpy as np, urllib.request, urllib.parse

ROOT = Path(__file__).resolve().parents[1]; CACHE = ROOT / "data" / "cache" / "wiki"; CACHE.mkdir(parents=True, exist_ok=True)
from .fetch import UA
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}


# A run that wants fresh race pages (the daily run, python -m midterms.refresh) sets FRESH_SINCE to its start time: every page
# with the default 24 h age is then CHECKED once in that run. Checking costs almost nothing: the MediaWiki API returns the
# current revision id of up to 50 pages per request (revisions()), and a page is downloaded again only when its revision
# changed since the cached copy (wgCurRevisionId in the cached HTML). Callers that pass their own max_age_h (the history
# pages: they never change) are unaffected.
FRESH_SINCE = None
API = "https://en.wikipedia.org/w/api.php"
_REV = {}            # title -> current revision id (filled in batches by revisions())


def _cache_file(title):
    return CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", title) + ".html")


def _cached_rev(html):
    m = re.search(r'"wgCurRevisionId":(\d+)', html[:200000]); return int(m.group(1)) if m else None


def revisions(titles):
    """Current revision ids of many pages at once (50 titles per API request; redirects followed) -> _REV."""
    from . import fetch as F
    titles = [t for t in dict.fromkeys(titles) if t not in _REV]
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        q = urllib.parse.urlencode({"action": "query", "prop": "info", "redirects": 1, "format": "json", "formatversion": 2,
                                    "titles": "|".join(chunk)})
        d = json.loads(F.open_url(API + "?" + q, timeout=60))["query"]
        norm = {x["from"]: x["to"] for x in d.get("normalized", [])}; red = {x["from"]: x["to"] for x in d.get("redirects", [])}
        rev = {p["title"]: p.get("lastrevid") for p in d.get("pages", [])}
        for t in chunk:
            n = norm.get(t, t); _REV[t] = rev.get(red.get(n, n))
        time.sleep(1.0)


def fetch(title: str, max_age_h=None) -> str:
    from . import fetch as F
    fp = _cache_file(title)
    check = False
    if max_age_h is None:
        max_age_h = 24
        if FRESH_SINCE is not None and fp.exists() and fp.stat().st_mtime < FRESH_SINCE: max_age_h = 0; check = True
    if fp.exists() and (time.time() - fp.stat().st_mtime) < max_age_h * 3600: return fp.read_text()
    if fp.exists():                                   # unchanged since the cached copy? (one batched API call per 50 pages)
        old = fp.read_text()
        try:
            if title not in _REV:                     # first check of the run: every page this cache has seen, in batches
                revisions([title] + [t for t in _known() if t != title])
            cur = _REV.get(title)
            if cur is not None and cur == _cached_rev(old):
                fp.touch(); _remember(title); return old
        except Exception as e:
            print("  wikipedia revision check failed:", str(e)[:80])
    url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
    html = F.open_url(url, timeout=60).decode("utf-8", "replace")
    fp.write_text(html); _remember(title); time.sleep(1.0); return html


_INDEX = CACHE / "titles.json"      # every title fetched into this cache (so the revision check can batch them)


def _known():
    try: return [t for t in json.loads(_INDEX.read_text()) if _cache_file(t).exists()]
    except Exception: return []


def _remember(title):
    k = _known()
    if title not in k: _INDEX.write_text(json.dumps(k + [title]))


def _parse_date(s, year):
    """'September 10–12, 2026' / 'Sep 8 – 10, 2026' / 'August 28 – September 2, 2026' -> end date."""
    s = re.sub(r"\[.*?\]", "", str(s)).replace("–", "-").replace("—", "-")
    m = re.findall(r"([A-Z][a-z]+)\.?\s+(\d{1,2})(?:\s*-\s*(?:([A-Z][a-z]+)\.?\s+)?(\d{1,2}))?,?\s*(\d{4})?", s)
    if not m: return None
    mon1, d1, mon2, d2, yr = m[-1]; mon = mon2 or mon1; day = d2 or d1; yr = yr or str(year)
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try: return pd.Timestamp(dt.datetime.strptime(f"{mon[:3] if fmt.startswith('%b') else mon} {day} {yr}", fmt))
        except ValueError: continue
    return None


def _num(s):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", re.sub(r"\[.*?\]", "", str(s))); return float(m.group(1)) if m else np.nan


_COMMITTEE_D = re.compile(r"\b(DCCC|DSCC|DGA|DLCC|House Majority PAC|Senate Majority PAC|Majority Forward)\b")
_COMMITTEE_R = re.compile(r"\b(NRCC|NRSC|RGA|RSLC|Congressional Leadership Fund|Senate Leadership Fund|One Nation)\b")


def _committee_tag(pol: str) -> str:
    """A party committee's / party super PAC's own poll without Wikipedia's "(D)"/"(R)" mark gets it (2026-09-30: "DCCC
    Analytics Department" on the Ohio and Wisconsin House pages, OH-9 / WI-1, entered untagged - no sponsor shift, full
    weight). The Bluesky feeds already tag these names (bluesky_polls.build / pollresults_build)."""
    if re.search(r"\((?:D|R|I)\)", pol): return pol
    if _COMMITTEE_D.search(pol): return pol.strip() + " (D)"
    if _COMMITTEE_R.search(pol): return pol.strip() + " (R)"
    return pol


def borrow_feed_tags(df, office, seat_of):
    """Wikipedia marks a campaign's own poll with a footnote letter ("PPP [F]" -> "Poll sponsored by Gutierrez's campaign"),
    which the parse drops, so the row enters untagged; the Wikipedia copy then wins over the feeds' tagged copy of the same
    poll (2026-09-30: TX-9 PPP 9/19 lost its (D) - the pollresults post says "Partisan pollster (D)", Polling USA "(Gutierrez
    Internal)"). An untagged row borrows the (D)/(R)/(I) tag of the same poll's feed row when that tag is sponsor evidence: same race,
    same pollster (model._same_pollster), end within 3 days, and only when every such feed copy agrees on the tag."""
    f = ROOT / "data" / "state" / "bluesky_race_polls.csv"
    if df is None or df.empty or not f.exists(): return df
    from .model import _same_pollster
    # ONLY sponsor evidence (feed column tag_src): NYT's per-poll "Partisan pollster" flag on the pollresults.org bot, Polling
    # USA's "(X Internal)" note, a committee name. Polling USA's "Republican Pollster" is a pollster-lean LABEL (Quantus,
    # Rasmussen, Big Data) and pollresults_site's tag a pollster majority - borrowing those would bring back the pollster-level
    # lean tags retired on 2026-09-29.
    fb = pd.read_csv(f, parse_dates=["end_date"])
    if "tag_src" not in fb: return df
    fb = fb[(fb["office"] == office) & fb["tag_src"].isin(["nyt", "internal", "committee"])]
    tag = lambda s: (re.findall(r"\((D|R|I)\)", str(s)) or [None])[-1]
    out = df.copy()
    for i, r in out.iterrows():
        if tag(r["pollster"]): continue
        c = fb[(fb["seat"].astype(str) == str(seat_of(r))) & ((fb["end_date"] - pd.Timestamp(r["end_date"])).abs() <= pd.Timedelta(days=3))]
        if c.empty: continue
        c = c[c["pollster"].map(lambda o: _same_pollster(r["pollster"], o)).astype(bool).values]
        tags = {tag(o) for o in c["pollster"]}
        if len(c) and len(tags) == 1 and None not in tags:
            t = tags.pop(); out.at[i, "pollster"] = f"{str(r['pollster']).strip()} ({t})"
            print(f"  wiki poll tag borrowed from the feeds: {office} {seat_of(r)} {r['pollster']} {pd.Timestamp(r['end_date']).date()} -> ({t})")
    return out


def poll_tables(html: str, year: int, dem_names=(), rep_names=()):
    """All poll tables on a page -> rows (pollster, end_date, n, dem, rep, margin, dem_name, rep_name, table_idx)."""
    out = []
    # editors' quote typos (rowspan='2" on the FL special page, 2026-09-19) crash read_html -> normalise span values
    html = re.sub(r"""(row|col)span=['"](\d+)['"]""", r'\1span="\2"', html)
    try: tables = pd.read_html(io.StringIO(html))
    except ValueError as e:
        print(f"  !! poll_tables: page did not parse ({str(e)[:80]}) - zero polls from it"); return pd.DataFrame()
    for ti, t in enumerate(tables):
        cols = [" ".join(str(x) for x in c) if isinstance(c, tuple) else str(c) for c in t.columns]
        if not any("Poll source" in c or "Pollster" in c for c in cols): continue
        dcol = [c for c in cols if "Date" in c]; scol = [c for c in cols if "Sample" in c]
        if not dcol: continue
        # candidate columns: those that are not meta; a candidate column header repeats the name in both levels
        meta = {c for c in cols if any(k in c for k in ("Poll source", "Pollster", "Date", "Sample", "Margin of error", "Other", "Undecided", "Lead", "Sponsor"))}
        cands = [c for c in cols if c not in meta]
        if len(cands) < 2: continue
        t.columns = cols
        for _, r in t.iterrows():
            vals = {c: _num(r[c]) for c in cands}; vals = {k: v for k, v in vals.items() if not np.isnan(v)}
            if len(vals) < 2: continue
            end = _parse_date(r[dcol[0]], year)
            if end is None: continue
            pol = re.sub(r"\[.*?\]", "", str(r[[c for c in cols if "Poll source" in c or "Pollster" in c][0]]))
            pol = _committee_tag(pol)
            out.append({"table": ti, "pollster": pol[:60], "end_date": end, "n": _num(r[scol[0]]) if scol else np.nan, "cands": vals})
    return out


def infobox_nominees(html: str):
    """[(name, party)] from the race infobox's Nominee and Party rows (used when the race table lists no
    nominees, as for the 2026 specials).  Reads the table cells, not the page text: the table of contents
    also has a 'Nominee' heading."""
    k = html.find('class="infobox')
    if k < 0: return []
    box = html[k:k + 60000]
    def row(label):
        m = re.search(r"<t[hd][^>]*>\s*(?:<[^>]+>\s*)*" + label + r"\s*(?:<[^>]+>\s*)*</t[hd]>(.*?)</tr>", box, re.S)
        if not m: return []
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\[[^\]]*\]", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]
        return [c for c in cells if c]
    names, parties = row("Nominee"), row("Party")
    return list(zip(names, parties)) if names and len(names) == len(parties) else []


def _party_of(key):
    m = re.search(r"\((D|R|I|L|G|DFL|Ind\w*|Lib\w*)\)", key)
    return {"DFL": "D"}.get(m.group(1), m.group(1)[0]) if m else ""


def senate_polls(state_abbr: str, year=2026, special=False, dem=None, rep=None, ballot=None, return_meta=False, title=None):
    """General-election polls of the race that is actually on the ballot (2026-09-18 rewrite).

    ballot = [(name, party)] from the race table / infobox.  With a ballot:
      * a poll row naming anyone NOT on the ballot is a hypothetical matchup and is dropped (Idaho's
        Risch v Roth (D), South Dakota's Rounds v Beaudion (D), Nebraska's Ricketts v Burbank/Forbes (D));
      * 'majors' = on-ballot candidates averaging >= 10 % where polled; a row that leaves a major out is a
        hypothetical head-to-head and is dropped (Montana: Alme v Bankhead with Bodnar missing);
      * the CHALLENGER is the Democrat when one is on the ballot and no independent outpolls them,
        otherwise the leading independent (Osborn NE, Achilles ID, Bengs SD, Bodnar MT);
      * margin = challenger - Republican.
    Without a ballot it falls back to the first (D) / (R) columns, as before.
    Rows are then collapsed to one per (pollster, end date) and rows with the challenger or the Republican
    under 15 % are dropped (primary-table debris, ranked-choice round tables)."""
    title = title or f"{year} United States Senate {'special ' if special else ''}election in {STATES[state_abbr]}"
    html = fetch(title); rows = poll_tables(html, year)
    meta = {"challenger": dem, "challenger_party": "D", "majors": [], "dropped_hypothetical": 0, "dropped_partial": 0}
    empty = (pd.DataFrame(), meta) if return_meta else pd.DataFrame()
    if not isinstance(rows, list) or not rows: return empty
    ballot = [(n, p) for n, p in (ballot or []) if n]
    full = {_norm(n): (n, p) for n, p in ballot}
    by_sur = {}
    for n, p in ballot: by_sur.setdefault(_surname(n), []).append((n, p))
    def who(key):
        """exact full name first; the surname only when it is unique on the ballot (Alaska lists a second
        'Dan Sullivan' — Dan J., a namesake — next to the incumbent Dan S. Sullivan)"""
        k = re.sub(r"\(.*?\)", "", key).strip()
        if _norm(k) in full: return full[_norm(k)]
        c = by_sur.get(_surname(k), []); return c[0] if len(c) == 1 else None
    res = []
    if ballot:
        keep = []
        for p in rows:
            c = {k: v for k, v in p["cands"].items() if "Generic" not in k}
            if not c or any(who(k) is None for k in c): meta["dropped_hypothetical"] += 1; continue
            keep.append((p, {who(k)[0]: v for k, v in c.items()}))
        shares = {}
        for p, c in keep:
            for n, v in c.items(): shares.setdefault(n, []).append(v)
        majors = [n for n, v in shares.items() if np.mean(v) >= 10]
        meta["majors"] = majors
        party = dict(ballot)
        opp = [n for n in majors if not party[n].startswith("Rep")]
        reps = [n for n in majors if party[n].startswith("Rep")]
        if not opp or not reps: return empty
        rep_n = max(reps, key=lambda n: np.mean(shares[n]))          # the Republican the polls are about
        # the challenger: the non-Republican major with the highest share across full-field rows
        full = [c for p, c in keep if all(n in c for n in majors)]
        score = {n: np.mean([c[n] for c in full]) if full else np.mean(shares[n]) for n in opp}
        ch = max(opp, key=lambda n: score[n])
        meta.update(challenger=ch, challenger_party=party[ch][0], republican=rep_n)
        for p, c in keep:
            if not all(n in c for n in majors): meta["dropped_partial"] += 1; continue
            res.append({"state": state_abbr, "special": special, "pollster": p["pollster"], "end_date": p["end_date"], "n": p["n"], "dem": c[ch], "rep": c[rep_n], "margin": c[ch] - c[rep_n],
                        "dem_name": f"{ch} ({party[ch][0]})", "rep_name": f"{rep_n} (R)", "table": p["table"],
                        "und": max(0.0, 100 - sum(c.values())), "other": sum(v for n, v in c.items() if n not in (ch, rep_n))})
    else:
        for p in rows:
            c = p["cands"]; keys = list(c)
            dk = [k for k in keys if re.search(r"\(D\)|\(DFL\)|Democratic", k) or (dem and dem.split()[-1] in k)]
            rk = [k for k in keys if re.search(r"\(R\)|Republican", k) or (rep and rep.split()[-1] in k)]
            if not dk or not rk: continue
            d, r = c[dk[0]], c[rk[0]]
            res.append({"state": state_abbr, "special": special, "pollster": p["pollster"], "end_date": p["end_date"], "n": p["n"], "dem": d, "rep": r, "margin": d - r, "dem_name": dk[0][:40], "rep_name": rk[0][:40], "table": p["table"]})
    df = pd.DataFrame(res)
    if not df.empty:
        # one poll, one row: RCV round tables and with/without-leaner variants were entering 5-7 times (AK)
        df = df[df[["dem", "rep"]].min(axis=1) >= 15]
        agg = {c: "first" for c in df.columns if c not in ("pollster", "end_date", "dem_name", "rep_name")}; agg.update({"dem": "mean", "rep": "mean", "margin": "mean", "n": "max"})
        for k in ("und", "other"):
            if k in df: agg[k] = "mean"
        df = df.groupby(["pollster", "end_date", "dem_name", "rep_name"], as_index=False, sort=False).agg(agg)
        df = borrow_feed_tags(df, "governor" if "gubernatorial" in title else "senate", lambda r: state_abbr)
    return (df, meta) if return_meta else df


if __name__ == "__main__":
    import sys
    for st in (sys.argv[1:] or ["GA", "NC", "ME", "MI", "OH"]):
        df = senate_polls(st); print(st, len(df), "polls;", "" if df.empty else f"newest {df['end_date'].max().date()} | last 5 margins {df.sort_values('end_date')['margin'].tail(5).round(1).tolist()} | names {df['dem_name'].iloc[-1]} vs {df['rep_name'].iloc[-1]}")


def _district_chunks(html: str):
    """Split a state House page into (district_number, html_chunk) by the 'District N' headings."""
    parts = re.split(r'(<h[23][^>]*id="District_(\d+)[^"]*"[^>]*>)', html)
    # re.split with two groups yields [pre, tag, num, body, tag, num, body, ...]
    out = []; i = 1
    while i + 2 < len(parts) + 1 and i < len(parts):
        tag, num, body = parts[i], parts[i + 1], parts[i + 2] if i + 2 < len(parts) else ""
        out.append((int(num), body)); i += 3
    if not out:                                          # at-large pages have no district headings
        out = [(1, html)]
    return out


def house_polls(state_abbr: str, year=2026):
    """General-election polls per district from '{year} United States House of Representatives elections in {State}'."""
    title = f"{year} United States House of Representatives elections in {STATES[state_abbr]}"
    try: html = fetch(title)
    except Exception:
        try: html = fetch(title.replace(" elections in ", " election in "))          # at-large states: the page title is singular
        except Exception as e:
            print(f"  {state_abbr}: fetch failed ({str(e)[:60]})"); return pd.DataFrame()
    res = []
    for cd, chunk in _district_chunks(html):
        for p in poll_tables("<table" + chunk.split("<table", 1)[1] if "<table" in chunk else "", year):
            c = p["cands"]; keys = list(c)
            dk = [k for k in keys if re.search(r"\(D\)|\(DFL\)|Democratic", k) and "Generic" not in k]; rk = [k for k in keys if re.search(r"\(R\)|Republican", k) and "Generic" not in k]
            if not dk or not rk: continue
            res.append({"seat": f"{state_abbr}-{cd}", "pollster": p["pollster"], "end_date": p["end_date"], "n": p["n"], "dem": c[dk[0]], "rep": c[rk[0]], "margin": c[dk[0]] - c[rk[0]], "dem_name": dk[0][:40], "rep_name": rk[0][:40],
                        "und": max(0.0, 100 - sum(c.values())), "other": sum(v for k, v in c.items() if k not in (dk[0], rk[0]))})
    return pd.DataFrame(res)


def all_house_polls(year=2026):
    frames = []
    for st in STATES:
        df = house_polls(st, year)
        if not df.empty: frames.append(df)
    out = pd.concat(frames) if frames else pd.DataFrame()
    if len(out): out = screen_house_polls(out, year)
    if len(out): out = borrow_feed_tags(out, "house", lambda r: r["seat"])
    out.to_csv(ROOT / "data" / "cache" / f"house{year}_polls.csv", index=False); return out


def _norm(n):
    import unicodedata
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", str(n)).encode("ascii", "ignore").decode().lower().replace(".", "")).strip()


def _surname(n):
    import unicodedata
    n = unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", "", str(n))).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b(jr\.?|sr\.?|iii|ii|iv)\b", "", n).split(); return n[-1] if n else ""


def screen_house_polls(out, year=2026):
    """Keep only polls of the race that is actually on the ballot (2026-09-18).
    1. Both named candidates must be in the seat's Candidates column (house{year}_seats.csv) when that column
       lists a nominee of the party: 75 of 259 rows were hypothetical matchups (FL-2 four Democrats vs one
       Republican, ME-2 Golden after he retired, CA-48 Issa after he retired), each entering as if it were the race.
    2. min(dem, rep) >= 15: below that the D-vs-R margin is not the contest (AK-1: ranked-choice rounds with the
       Democrat at 9-11 % while an independent is the real challenger).
    3. One row per (seat, pollster, end date): variants of one poll (RCV rounds, with/without leaners) are averaged.
    Rejected rows go to house{year}_polls_rejected.csv with the reason."""
    import unicodedata
    seats = pd.read_csv(ROOT / "data" / "cache" / f"house{year}_seats.csv")
    cand = {k: unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode().lower() for k, v in zip(seats["seat"], seats["cands"].fillna(""))}
    def why(r):
        c = cand.get(r["seat"], "")
        for nm, party in ((r["dem_name"], "democratic"), (r["rep_name"], "republican")):
            if party in c and _surname(nm) not in c: return f"not on the ballot: {nm}"
        if min(r["dem"], r["rep"]) < 15: return "not a two-party contest (a candidate under 15)"
        return ""
    out = out.copy(); out["reject"] = out.apply(why, axis=1)
    out[out["reject"] != ""].to_csv(ROOT / "data" / "cache" / f"house{year}_polls_rejected.csv", index=False)
    keep = out[out["reject"] == ""].drop(columns="reject")
    agg = {c: "first" for c in keep.columns if c not in ("seat", "pollster", "end_date")}; agg.update({"dem": "mean", "rep": "mean", "margin": "mean"})
    for k in ("und", "other"):
        if k in keep: agg[k] = "mean"
    keep = keep.groupby(["seat", "pollster", "end_date"], as_index=False, sort=False).agg(agg)
    print(f"  house polls screened: {len(out)} rows -> {len(keep)} kept ({(out['reject'] != '').sum()} rejected, {keep['seat'].nunique()} seats)")
    return keep



def governor_polls(state_abbr: str, ballot, year=2026, return_meta=False):
    """The Senate rules (on-ballot candidates only, full field, challenger v Republican) applied to the governor race page."""
    return senate_polls(state_abbr, year, ballot=ballot, return_meta=return_meta, title=f"{year} {STATES[state_abbr]} gubernatorial election")
