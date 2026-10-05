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


def _box_rows(tb):
    rows = []
    for tr in tb.xpath(".//tr"):
        cells = [re.sub(r"\s+", " ", " ".join(c.xpath(".//text()"))).strip() for c in tr.xpath("./td|./th")]
        cells = [c for c in cells if c != ""]
        if len(cells) < 2: continue
        i = next((j for j, c in enumerate(cells[:2]) if party_code(c) in ("D", "R") or re.match(r"^(Libertarian|Green|Independent|Constitution|Forward|Working)", c)), None)
        if i is None: continue
        p = party_code(cells[i]); name = cells[i + 1] if i + 1 < len(cells) else ""
        if re.search(r"^(total|turnout|majority|plurality|margin)", name, re.I) or re.search(r"\b(hold|gain)\b", " ".join(cells), re.I): continue
        rows.append((p, name, cells[i + 2] if i + 2 < len(cells) else ""))
    return rows


def election_boxes(chunk_html):
    """[(caption, [(party, name, votes_text)])] for every election-box table in the chunk (candidate names sit in <th> row headers
    in 'plainrowheaders' boxes, so both th and td cells are read)."""
    out = []
    try: root = LH.fromstring(f"<div>{chunk_html}</div>")
    except Exception: return out
    for tb in root.xpath(".//table[contains(@class,'wikitable')]"):
        cap = " ".join(tb.xpath("./caption//text()")).strip()
        rows = _box_rows(tb)
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
    # 1) election boxes, each assigned to the nearest preceding "District N" heading (or the district in its caption)
    heads = [(m.start(), m.group(1)) for m in re.finditer(r'<h[2345][^>]*id="District_(\d+[AB]?)(?:_\d+)?"', html)]
    by_d = {}
    for m in re.finditer(r'<table[^>]*class="[^"]*wikitable[^"]*"', html):
        end = html.find("</table>", m.start())
        if end < 0: continue
        block = html[m.start(): end + 8]
        cap = re.search(r"<caption[^>]*>(.*?)</caption>", block, re.S)
        capt = re.sub(r"<[^>]+>", " ", cap.group(1)) if cap else ""
        md = re.search(r"(\d+)(?:st|nd|rd|th)?\s*([AB])?\s*(?:district|District)", capt) or re.search(r"District\s+(\d+)([AB])?", capt)
        prev = [d for p, d in heads if p < m.start()]
        d = (f"{md.group(1)}{md.group(2) or ''}" if md else (prev[-1] if prev else None))
        if d is None: continue
        bx = election_boxes(block)
        if bx: by_d.setdefault(norm_dist(st, ch, d), []).extend([(capt or c, r) for c, r in bx])
    # 1b) districts with candidate LISTS instead of boxes: "Name (Republican)" items (MN House) or "<Party> Primary ... nominee" blocks (NH Senate)
    for i, (pos, d) in enumerate(heads):
        dist = norm_dist(st, ch, d)
        if dist in by_d: continue
        end = min([p for p, _ in heads[i + 1:]] + [len(html)]); chunk = re.sub(r"<!--.*?-->", " ", html[pos:end], flags=re.S)
        items = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip() for x in re.findall(r"<li[^>]*>(.*?)</li>", chunk, re.S)]
        c = []
        for it in items:
            m = re.match(r"^(.+?)\s*\((Republican|Democratic|DFL|Democratic[–-]Farmer[–-]Labor|Libertarian|Green|Independent)[^)]*\)", it)
            if m: c.append((party_code(m.group(2)) or "O", m.group(1)))
        if not c:
            for m in re.finditer(r"(Republican|Democratic)\s+Primary(.*?)(?=(?:Republican|Democratic)\s+Primary|$)", re.sub(r"<[^>]+>", " | ", chunk), re.S):
                seg = m.group(2); nm = re.search(r"(?:nominee|Nominee|Declared|Candidates?)\s*\|[\s|]*([A-Z][^|,\[]+)", seg)
                if nm: c.append(("D" if m.group(1) == "Democratic" else "R", nm.group(1).strip()))
        if c: recs[dist] = {"cands": c, "source": "candidate list", "inc_marks": []}
    for dist, boxes in by_d.items():
        cands, src = from_boxes(boxes)
        if dist and cands: recs[dist] = {"cands": cands, "source": src, "inc_marks": [clean_name(n) for p, n in cands if is_inc_mark(n)]}
    # 2) district-breakdown tables (District / Candidate / party columns)
    try: tabs = pd.read_html(StringIO(html))
    except Exception: tabs = []
    summ = {}
    for tb in tabs:
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tb.columns]; tb.columns = cols
        if "District" not in cols: continue
        if "Candidate" in cols and len(tb) >= 10:                       # district breakdown: overrides boxes (specials) for its districts
            ci = cols.index("Candidate"); pc = cols[ci - 1]; bt = {}
            for _, r in tb.iterrows():
                dist = norm_dist(st, ch, r["District"]); nm = str(r["Candidate"])
                if not dist or nm.lower() in ("nan", "no candidate filed"): continue
                bt.setdefault(dist, {"cands": [], "source": "breakdown table", "inc_marks": []})["cands"].append((party_code(r[pc]) or "O", nm))
            recs.update(bt)
        if "Incumbent" in cols:
            pcols = [c for c in cols if c.startswith("Party")]
            dcols = [c for c in cols if c.startswith("District")]
            for _, r in tb.iterrows():
                if st == "NH" and ch == "lower" and len(dcols) >= 2:
                    try: dist = f"{str(r[dcols[0]]).strip().title()} {int(float(r[dcols[1]]))}"
                    except Exception: continue
                elif st == "MN" and ch == "lower" and len(dcols) >= 2 and str(r[dcols[1]]).strip() in ("A", "B"):
                    dist = norm_dist(st, ch, f"{r[dcols[0]]}{str(r[dcols[1]]).strip()}")
                else: dist = norm_dist(st, ch, r[dcols[0]])
                nm = str(r["Incumbent"])
                if not dist or nm.lower() == "nan": continue
                pty = next((party_code(r[c]) for c in pcols if party_code(r[c])), None)
                pc24 = next((c for c in cols if c.lower().startswith("2024 pres")), None); p24 = None
                if pc24:
                    mm = re.match(r"\s*([DR])\+\s*([\d.]+)", str(r[pc24]))
                    if mm: p24 = float(mm.group(2)) * (1 if mm.group(1) == "D" else -1)
                summ.setdefault(dist, []).append({"name": clean_name(nm), "party": pty, "retiring": "†" in nm, "status": str(r.get("Status", "")), "p24": p24})
    # retirement / outgoing lists
    ret = []
    for m in re.finditer(r'<h[234][^>]*id="([^"]*(Retir|Outgoing|Term-limited|Term_limited|Seeking|Lost_renomination|defeated|Declined)[^"]*)"', html, re.I):
        nxt = re.search(r"<h[23]", html[m.end():]); seg = html[m.end(): m.end() + (nxt.start() if nxt else 20000)]
        ret += [clean_name(LH.fromstring(f"<li>{x}</li>").text_content()) for x in re.findall(r"<li>(.*?)</li>", seg, re.S)][:200]
    return recs, summ, ret


def build(states=("MI", "MN", "WI", "AZ", "PA", "NH", "NC"), debug=False):
    rows = []
    for st in states:
        for ch in ("upper", "lower"):
            t = title(st, ch)
            try: html = W.fetch(t)
            except Exception as e: print(f"  {t}: fetch failed ({str(e)[:60]})"); continue
            recs, summ, ret = parse_page(st, ch, html)
            if debug and (st, ch) in (("MN", "lower"), ("MN", "upper"), ("NH", "upper"), ("NH", "lower")):
                i = html.find('id="District_1A"') if st == "MN" else html.find('id="District_1"')
                i = i if i >= 0 else html.find("Candidates")
                txt = re.sub(r"\s+", " ", html[max(i, 0): max(i, 0) + 2500]); print(f"  DEBUG {st} {ch} excerpt: {txt}")
            for d in sorted(set(recs) | set(summ), key=lambda x: (len(x), x)):
                c = recs.get(d, {}).get("cands", [])
                inc = summ.get(d, [])
                src = recs.get(d, {}).get("source", "")
                nd_ = sum(p == "D" for p, _ in c) if c else None; nr_ = sum(p == "R" for p, _ in c) if c else None
                if src in ("primary winners", "candidate list"):          # a party with no contested primary has no box: absence is not evidence
                    nd_ = nd_ or None; nr_ = nr_ or None
                rows.append({"state": st, "chamber": ch, "district": d,
                             "n_dem": nd_, "n_rep": nr_,
                             "n_oth": sum(p == "O" for p, _ in c) if c else None,
                             "dem": "; ".join(clean_name(n) for p, n in c if p == "D"), "rep": "; ".join(clean_name(n) for p, n in c if p == "R"),
                             "inc_marks": "; ".join(recs.get(d, {}).get("inc_marks", [])),
                             "inc_names": "; ".join(f"{x['name']}|{x['party'] or ''}|{int(x['retiring'])}|{x['status'][:30]}" for x in inc),
                             "pres24_page": next((x["p24"] for x in inc if x.get("p24") is not None), None),
                             "source": recs.get(d, {}).get("source", "summary only" if inc else "")})
            print(f"  {t}: {len(recs)} districts with candidates, {len(summ)} with incumbents, {len(ret)} retirement-list items")
            rows.append({"state": st, "chamber": ch, "district": "_retirements", "dem": "; ".join(ret)[:20000]})
    out = pd.DataFrame(rows); (CACHE).mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE / "stateleg_candidates.csv", index=False)
    return out


def qc_leans(o):
    """Internal check only (never published): our 2024 presidential margins v the margins some chamber pages print."""
    from .paths import STATIC
    try: L = pd.read_csv(STATIC / "stateleg" / "lean_2026.csv", dtype={"district": str})
    except FileNotFoundError: return
    q = o.dropna(subset=["pres24_page"]).merge(L[["state", "chamber", "district", "lean24", "src"]], on=["state", "chamber", "district"], how="inner")
    for (st, ch), g in q.groupby(["state", "chamber"]):
        e = g["lean24"] - g["pres24_page"]
        print(f"  QC {st} {ch}: ours v page 2024 margin, n {len(g)}, rms {float((e ** 2).mean()) ** 0.5:.2f}, max {e.abs().max():.1f} "
              f"(worst {g.loc[e.abs().idxmax(), 'district']}: ours {g.loc[e.abs().idxmax(), 'lean24']:.1f} v {g.loc[e.abs().idxmax(), 'pres24_page']:.1f}; {g['src'].iat[0]})")


if __name__ == "__main__":
    import sys
    o = build(debug="debug" in sys.argv); qc_leans(o)
    print(o[o.district != "_retirements"].groupby(["state", "chamber"]).agg(n=("district", "size"), with_cands=("n_dem", lambda x: x.notna().sum()),
          no_dem=("n_dem", lambda x: (x == 0).sum()), no_rep=("n_rep", lambda x: (x == 0).sum())).to_string())
    print(o[o.district != "_retirements"].groupby(["state", "chamber"]).head(3).to_string(max_colwidth=50)[:6000])
