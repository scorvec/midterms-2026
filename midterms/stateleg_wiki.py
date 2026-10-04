"""2026 state-legislative candidates from the Wikipedia chamber pages (CC BY-SA 4.0), read at run time through the
project's page cache (wiki_polls.fetch: only pages edited since the cached copy are downloaded).

Per district: the Democratic and Republican general-election nominees, whether the incumbent is on the ballot and, where a
page lists no candidates (Minnesota Senate, New Hampshire House), only the incumbents and the retirement lists. Three page
layouts are read:
  * a section per district (h2/h3/h4 "District N") holding election boxes: the last box whose caption is not a primary is the
    general election; with only primary boxes, each party's primary winner (most votes, or the only candidate) is its nominee;
  * a "district breakdown" table (Pennsylvania): District | Incumbent | Status | Party | Candidate rows;
  * a summary table (District | Incumbent | Party ...) where a dagger marks an incumbent not running.
Output: data/cache/stateleg_candidates.csv (state, chamber, district, n_dem, n_rep, n_oth, dem, rep, inc_names, retiring, source).
    python -m midterms.stateleg_wiki
"""
from __future__ import annotations

import re
from io import StringIO
import pandas as pd
from lxml import html as LH
from . import wiki_polls as W
from .paths import CACHE

NAMES = {"MI": "Michigan", "MN": "Minnesota", "WI": "Wisconsin", "AZ": "Arizona", "PA": "Pennsylvania", "NH": "New Hampshire",
         "NC": "North Carolina", "GA": "Georgia", "IA": "Iowa", "TX": "Texas"}
LOWER_NAME = {"WI": "State Assembly"}


def title(st, ch):
    return f"2026 {NAMES[st]} {'Senate' if ch == 'upper' else LOWER_NAME.get(st, 'House of Representatives')} election"


PARTY = [("D", r"^(Democratic|DFL|Democratic[–-]Farmer[–-]Labor|Democratic-NPL|Dem)\b"), ("R", r"^(Republican|Rep|GOP)\b")]


def party_code(s):
    s = str(s).strip()
    for c, p in PARTY:
        if re.match(p, s, re.I): return c
    return "O" if s and s.lower() not in ("nan", "none") else None


def clean_name(s):
    s = re.sub(r"\[[^\]]*\]", "", str(s)); s = re.sub(r"\((i|inc|incumbent)\)", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s.replace("†", "").replace("*", "")).strip()


def is_inc_mark(s):
    return bool(re.search(r"\((i|inc|incumbent)\)", str(s), re.I))


def surname(n):
    p = [x for x in re.sub(r"[^A-Za-z' -]", " ", clean_name(n)).split() if x.lower() not in ("jr", "sr", "ii", "iii", "iv")]
    return p[-1].lower() if p else ""


def norm_dist(st, ch, s):
    s = str(s)
    m = re.search(r"(\d+)\s*([AB])?\b", s)
    if not m: return None
    if st == "MN" and ch == "lower": return f"{int(m.group(1))}{m.group(2) or ''}"
    return str(int(m.group(1)))


def election_boxes(chunk_html):
    """[(caption, [(party, name, votes_text)])] for every election-box table in the chunk."""
    out = []
    try: root = LH.fromstring(f"<div>{chunk_html}</div>")
    except Exception: return out
    for tb in root.xpath(".//table[contains(@class,'wikitable')]"):
        cap = " ".join(tb.xpath("./caption//text()")).strip()
        rows = []
        for tr in tb.xpath(".//tr"):
            tds = [re.sub(r"\s+", " ", " ".join(td.xpath(".//text()"))).strip() for td in tr.xpath("./td")]
            tds = [t for t in tds if t != ""]
            if len(tds) < 2: continue
            p = party_code(tds[0])
            if p is None or re.search(r"total|turnout|majority|plurality|margin|hold|gain|write-in", " ".join(tds[:2]), re.I) and p == "O": continue
            rows.append((p, tds[1], tds[2] if len(tds) > 2 else ""))
        if rows: out.append((cap, rows))
    return out


def _votes(v):
    v = re.sub(r"[^\d]", "", str(v)); return int(v) if v else None


def from_boxes(boxes):
    gen = [b for b in boxes if not re.search(r"primary|convention|caucus", b[0], re.I)]
    if gen:
        rows = gen[-1][1]; src = "general box"
    else:
        rows, src = [], "primary winners"
        for p in ("D", "R"):
            for cap, rr in boxes:
                q = [r for r in rr if r[0] == p]
                if not q: continue
                best = max(q, key=lambda r: _votes(r[2]) or 0) if any(_votes(r[2]) for r in q) else (q[0] if len(q) == 1 else None)
                if best: rows.append(best); break
    return [(p, n) for p, n, _ in rows if not re.search(r"write-in|total|other", n, re.I)], src


def parse_page(st, ch, html):
    recs = {}
    # 1) district sections
    heads = [(m.start(), m.group(1), int(m.group(2) or 1)) for m in re.finditer(r'<h[234][^>]*id="District_(\d+[AB]?)(?:_(\d+))?"', html)]
    stops = [m.start() for m in re.finditer(r"<h2", html)]
    for i, (pos, d, k) in enumerate(heads):
        end = min([p for p in [h[0] for h in heads[i + 1:]] + stops if p > pos] or [len(html)])
        boxes = election_boxes(html[pos:end])
        if not boxes: continue
        cands, src = from_boxes(boxes)
        dist = norm_dist(st, ch, d)
        if dist and cands: recs[dist] = {"cands": cands, "source": src, "inc_marks": [clean_name(n) for p, n in cands if is_inc_mark(n)]}
    # 2) district-breakdown tables (District / Candidate / party columns)
    try: tabs = pd.read_html(StringIO(html))
    except Exception: tabs = []
    summ = {}
    for tb in tabs:
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tb.columns]; tb.columns = cols
        if "District" not in cols: continue
        if "Candidate" in cols and not recs:
            ci = cols.index("Candidate"); pc = cols[ci - 1]
            for _, r in tb.iterrows():
                dist = norm_dist(st, ch, r["District"]); nm = str(r["Candidate"])
                if not dist or nm.lower() in ("nan", "no candidate filed"): continue
                recs.setdefault(dist, {"cands": [], "source": "breakdown table", "inc_marks": []})["cands"].append((party_code(r[pc]) or "O", nm))
        if "Incumbent" in cols:
            pcols = [c for c in cols if c.startswith("Party")]
            dcols = [c for c in cols if c.startswith("District")]
            for _, r in tb.iterrows():
                if st == "NH" and ch == "lower" and len(dcols) >= 2:
                    try: dist = f"{str(r[dcols[0]]).strip().title()} {int(float(r[dcols[1]]))}"
                    except Exception: continue
                else: dist = norm_dist(st, ch, r[dcols[0]])
                nm = str(r["Incumbent"])
                if not dist or nm.lower() == "nan": continue
                pty = next((party_code(r[c]) for c in pcols if party_code(r[c])), None)
                summ.setdefault(dist, []).append({"name": clean_name(nm), "party": pty, "retiring": "†" in nm, "status": str(r.get("Status", ""))})
    # retirement / outgoing lists
    ret = []
    for m in re.finditer(r'<h[234][^>]*id="([^"]*(Retir|Outgoing|Term-limited|Term_limited|Seeking|Lost_renomination|defeated|Declined)[^"]*)"', html, re.I):
        nxt = re.search(r"<h[23]", html[m.end():]); seg = html[m.end(): m.end() + (nxt.start() if nxt else 20000)]
        ret += [clean_name(LH.fromstring(f"<li>{x}</li>").text_content()) for x in re.findall(r"<li>(.*?)</li>", seg, re.S)][:200]
    return recs, summ, ret


def build(states=("MI", "MN", "WI", "AZ", "PA", "NH", "NC")):
    rows = []
    for st in states:
        for ch in ("upper", "lower"):
            t = title(st, ch)
            try: html = W.fetch(t)
            except Exception as e: print(f"  {t}: fetch failed ({str(e)[:60]})"); continue
            recs, summ, ret = parse_page(st, ch, html)
            for d in sorted(set(recs) | set(summ), key=lambda x: (len(x), x)):
                c = recs.get(d, {}).get("cands", [])
                inc = summ.get(d, [])
                rows.append({"state": st, "chamber": ch, "district": d,
                             "n_dem": sum(p == "D" for p, _ in c) if c else None, "n_rep": sum(p == "R" for p, _ in c) if c else None,
                             "n_oth": sum(p == "O" for p, _ in c) if c else None,
                             "dem": "; ".join(clean_name(n) for p, n in c if p == "D"), "rep": "; ".join(clean_name(n) for p, n in c if p == "R"),
                             "inc_marks": "; ".join(recs.get(d, {}).get("inc_marks", [])),
                             "inc_names": "; ".join(f"{x['name']}|{x['party'] or ''}|{int(x['retiring'])}|{x['status'][:30]}" for x in inc),
                             "source": recs.get(d, {}).get("source", "summary only" if inc else "")})
            print(f"  {t}: {len(recs)} districts with candidates, {len(summ)} with incumbents, {len(ret)} retirement-list items")
            rows.append({"state": st, "chamber": ch, "district": "_retirements", "dem": "; ".join(ret)[:20000]})
    out = pd.DataFrame(rows); (CACHE).mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE / "stateleg_candidates.csv", index=False)
    return out


if __name__ == "__main__":
    o = build()
    print(o[o.district != "_retirements"].groupby(["state", "chamber"]).agg(n=("district", "size"), with_cands=("n_dem", lambda x: x.notna().sum()),
          no_dem=("n_dem", lambda x: (x == 0).sum()), no_rep=("n_rep", lambda x: (x == 0).sum())).to_string())
    print(o[o.district != "_retirements"].groupby(["state", "chamber"]).head(3).to_string(max_colwidth=50)[:6000])
