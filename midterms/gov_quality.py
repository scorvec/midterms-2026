"""Governor nominees' prior office (2026-10-05; candidate-quality test of the governor review, midterms/gov_backtest.py).

A nominee is "experienced" when, before the election, he or she had held a STATEWIDE elected office (governor, lieutenant
governor, attorney general, secretary of state, treasurer, comptroller / controller, auditor, superintendent of public
instruction, an elected state commission) or a seat in CONGRESS (U.S. Senate or House), read from the office list of the
nominee's Wikipedia infobox (office / title / jr/sr fields and their term_start dates). A nominee without an article counts as
not experienced (has_page = 0). The term the prior can use is symmetric in party: experienced D minus experienced R.

    python -m midterms.gov_quality titles       # nominee page titles from the yearly results pages -> data/static/gov_nominees.csv
    python -m midterms.gov_quality fetch        # wikitext of those pages (MediaWiki API, 50 titles a request)
                                                #   -> data/static/gov_candidate_quality.csv (derived facts only)
    python -m midterms.gov_quality results2024  # the 2024 results table -> data/static/gov_results_2024.csv (gov2026.USE_2024)
    python -m midterms.gov_quality all          # all three (GitHub Actions: .github/workflows/gov-data.yml; the laptop downloads nothing)
Source: Wikipedia (CC BY-SA 4.0), the yearly "United States gubernatorial elections" pages and the nominees' articles.
"""
import json, re, sys, time, urllib.parse
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NOMINEES = ROOT / "data" / "static" / "gov_nominees.csv"
QUALITY = ROOT / "data" / "static" / "gov_candidate_quality.csv"
OFFICES = ROOT / "data" / "static" / "gov_nominee_offices.csv"      # the infobox offices read for each D / R nominee (audit)
YEARS = list(range(1998, 2025, 2)) + [2026]
_PARTY = re.compile(r"\s*\(([^)]+)\)\s*([\d.]+%)?")

STATEWIDE = re.compile(r"(?<!board of )governor|attorney general|secretary of (?:the )?state|treasurer|comptroller|controller|auditor|"
                       r"superintendent of public instruction|state superintendent|commissioner of (?:agriculture|insurance|labor|education|public lands)|"
                       r"(?:agriculture|insurance|labor|land|lands) commissioner|railroad commission|public service commission|corporation commission|"
                       r"public utilities commission", re.I)
CONGRESS = re.compile(r"(?:united states|u\.\s?s\.) senat|(?:united states|u\.\s?s\.) house of representatives|u\.\s?s\. representative|"
                      r"united states representative|member of congress|delegate to the (?:united states|u\.s\.) house", re.I)
NOT_STATEWIDE = re.compile(r"state senate|house of delegates|state house|assembly|county|city|mayor|municipal|district attorney|"
                           r"lieutenant governor of (?:the )?(?:federal|reserve)|board of governors|deputy|assistant|chief of staff|"
                           r"(?<!state )(?<!u\.s\. )(?<!united states )senate of|legislat|court|judge|justice", re.I)


def titles():
    """Every governor nominee (D, R and others) in the cached yearly results tables with the linked article title."""
    import lxml.html as LH
    from . import wiki_polls as W
    from .data_prep import _ST
    rows = []
    for y in YEARS:
        try: html = W.fetch(f"{y} United States gubernatorial elections", max_age_h=10 ** 6)    # cached copy; fetched once if missing
        except Exception as e: print("  not available:", y, str(e)[:60]); continue
        doc = LH.fromstring(html)
        for tb in doc.xpath("//table[contains(@class,'wikitable')]"):
            hdr = [th.text_content().strip() for th in tb.xpath(".//tr[1]/th")]
            if not (any(x.startswith("Candidates") for x in hdr) and any(x.startswith("State") for x in hdr)): continue
            for tr in tb.xpath(".//tr")[1:]:
                th = tr.xpath("./th"); tds = tr.xpath("./td")
                if not th or not tds: continue
                st = _ST.get(re.sub(r"\[.*?\]", "", th[0].text_content()).strip())
                if not st: continue
                cell = tds[-1]; s = cell.text_content()
                linked = {}
                for a in cell.xpath(".//a[@href]"):
                    nm = a.text_content().strip(); t = a.get("title") or ""
                    if nm and "/wiki/" in a.get("href", "") and ":" not in t and "redlink=1" not in a.get("href", ""): linked.setdefault(nm, t)
                # every "Name (Party) 12.3%" of the cell; a name without a link is a nominee without an article
                for nm, party, pct in re.findall(r"([^%▌()\]]+?)\s*\(([^)]+)\)\s*([\d.]+%)?", re.sub(r"\[[^\]]*\]", "", s)):
                    nm = re.sub(r"^Y\s+(?=[A-Z])", "", re.sub(r"^(?:Others|Others:)\s+", "", nm.strip()))   # "Y" = the winner's check mark
                    if not nm or len(nm) > 60 or not re.search(r"[A-Z]", nm): continue
                    rows.append({"year": y, "state": st, "name": nm, "party": party.strip(), "pct": (pct or "").rstrip("%"),
                                 "title": linked.get(nm, ""), "has_page": int(nm in linked)})
            break
    T = pd.DataFrame(rows).drop_duplicates(["year", "state", "name"])
    T.to_csv(NOMINEES, index=False); print(f"{len(T)} nominees ({T.has_page.sum()} with an article) -> {NOMINEES}")
    return T


def _strip(v):
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)        # [[target|text]] -> text
    v = re.sub(r"\{\{[^{}]*\}\}", " ", v); v = re.sub(r"<[^>]+>", " ", v); v = re.sub(r"'''?", "", v)
    return re.sub(r"\s+", " ", v).strip()


def _year_month(v):
    m = re.search(r"\{\{\s*(?:start date|start date and age|dts)\s*\|\s*(?:df=\w+\|\s*)?(\d{4})\s*(?:\|\s*(\d{1,2}))?", v, re.I)
    if m: return int(m.group(1)), int(m.group(2) or 1)
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)?\s*\d{0,2},?\s*((?:17|18|19|20)\d{2})", v)
    if m:
        mon = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
        return int(m.group(2)), (mon.index(m.group(1).lower()) + 1) if m.group(1) else 1
    return None


def _infobox_fields(wikitext):
    """{field: value} of the article's first {{Infobox ...}}, split on its TOP-LEVEL pipes (one-line infoboxes and values holding
    [[a|b]] links or nested templates are handled)."""
    i = wikitext.lower().find("{{infobox")
    if i < 0: return {}
    depth = 0; j = i; n = len(wikitext)
    while j < n:
        if wikitext.startswith("{{", j): depth += 1; j += 2; continue
        if wikitext.startswith("}}", j):
            depth -= 1; j += 2
            if depth == 0: break
            continue
        j += 1
    body = wikitext[i + 2:j - 2]; parts, cur, d_t, d_l, k = [], [], 0, 0, 0
    while k < len(body):
        two = body[k:k + 2]
        if two == "{{": d_t += 1; cur.append(two); k += 2; continue
        if two == "}}": d_t -= 1; cur.append(two); k += 2; continue
        if two == "[[": d_l += 1; cur.append(two); k += 2; continue
        if two == "]]": d_l -= 1; cur.append(two); k += 2; continue
        if body[k] == "|" and d_t == 0 and d_l == 0: parts.append("".join(cur)); cur = []; k += 1; continue
        cur.append(body[k]); k += 1
    parts.append("".join(cur))
    f = {}
    for p in parts[1:]:
        if "=" not in p: continue
        key, v = p.split("=", 1); f.setdefault(key.strip().lower().replace(" ", "_"), v.strip())
    return f


def offices(wikitext):
    """[(office text, (start year, month) or None)] from the infobox fields office/title/jr/sr N and term_start N."""
    f = _infobox_fields(wikitext or "")
    out = []
    for k, v in f.items():
        m = re.match(r"^(office|title|jr/sr)(\d*)$", k)
        if not m: continue
        n = m.group(2); txt = _strip(v)
        if m.group(1) == "jr/sr": txt = txt + " " + _strip(f.get("state" + n, ""))
        out.append((txt, _year_month(f.get("term_start" + n, "") or f.get("termstart" + n, ""))))
    return out


def experienced(offs, year):
    """(statewide, congress, the qualifying office) held with a term that started before November of the election year."""
    sw = cg = False; what = ""
    for txt, ym in offs:
        if ym is None or ym >= (year, 11): continue
        if CONGRESS.search(txt): cg = True; what = what or txt
        elif STATEWIDE.search(txt) and not NOT_STATEWIDE.search(txt): sw = True; what = what or txt
    return sw, cg, what[:80]


def fetch():
    from . import fetch as F
    T = pd.read_csv(NOMINEES); want = sorted(set(T[T.has_page == 1].title))
    API = "https://en.wikipedia.org/w/api.php"; text, resolved = {}, {}
    WT = ROOT / "data" / "raw" / "gov_wikitext.json"         # downloaded once (Actions cache), never committed
    if WT.exists():
        c = json.loads(WT.read_text()); text, resolved = c["text"], {k: tuple(v) for k, v in c["resolved"].items()}
    want = [t for t in want if t not in text]
    for i in range(0, len(want), 50):
        chunk = want[i:i + 50]
        q = urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content|ids", "rvslots": "main", "redirects": 1,
                                    "format": "json", "formatversion": 2, "titles": "|".join(chunk)})
        d = json.loads(F.open_url(API + "?" + q, timeout=90))["query"]
        norm = {x["from"]: x["to"] for x in d.get("normalized", [])}; red = {x["from"]: x["to"] for x in d.get("redirects", [])}
        pages = {p["title"]: p for p in d.get("pages", [])}
        for t in chunk:
            n = norm.get(t, t); n = red.get(n, n); p = pages.get(n) or {}
            revs = p.get("revisions") or []
            if revs: text[t] = revs[0]["slots"]["main"]["content"]; resolved[t] = (n, revs[0].get("revid"))
        time.sleep(1.5)
        print(f"  {min(i + 50, len(want))}/{len(want)} pages", flush=True)
    if want: WT.parent.mkdir(parents=True, exist_ok=True); WT.write_text(json.dumps({"text": text, "resolved": resolved}))
    rows, orows = [], []
    for r in T.itertuples():
        wt = text.get(r.title) if r.has_page else None
        offs = offices(wt) if wt else []
        sw, cg, what = experienced(offs, int(r.year)) if wt else (False, False, "")
        if r.party.startswith(("Democratic", "DFL", "Republican")):
            orows += [{"year": r.year, "state": r.state, "name": r.name, "office": o[:100], "start": f"{ym[0]}-{ym[1]:02d}" if ym else ""} for o, ym in offs]
        rows.append({"year": r.year, "state": r.state, "name": r.name, "party": r.party, "title": r.title, "has_page": int(wt is not None),
                     "statewide": int(sw), "congress": int(cg), "experienced": int(sw or cg), "office": what,
                     "revid": (resolved.get(r.title) or (None, None))[1]})
    Q = pd.DataFrame(rows); Q.to_csv(QUALITY, index=False)
    pd.DataFrame(orows).to_csv(OFFICES, index=False)
    print(f"{len(Q)} nominees, {Q.has_page.sum()} articles read, experienced {Q.experienced.sum()} -> {QUALITY}")
    from . import fetch as F2
    F2.report()


def results2024():
    from . import wiki_polls as W, gov2026 as GV
    rows = GV.parse_results(2024, W.fetch("2024 United States gubernatorial elections", max_age_h=10 ** 6))
    T = pd.DataFrame(rows); T.to_csv(GV.RESULTS_2024, index=False); print(f"2024: {len(T)} D-v-R governor races -> {GV.RESULTS_2024}")
    print(T.to_string())


def all_steps():
    results2024(); titles(); fetch()


if __name__ == "__main__":
    {"titles": titles, "fetch": fetch, "results2024": results2024, "all": all_steps}[sys.argv[1] if len(sys.argv) > 1 else "titles"]()
