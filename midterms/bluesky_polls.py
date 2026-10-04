"""Race polls from the Polling USA feed on Bluesky (@usapolling.bsky.social), 2026-09-23 (user suggestion).

The account posts new polls within hours - days before Wikipedia or VoteHub. Public AppView API, no login. Post template:
    "<State> - <Senate | Governor | Nth House> Polling: | 🔵 Name: 50% | 🔴 Name: 44% | ⚪️ Name: 3% | | Pollster / Sept 20, 2026 |
     (Republican Pollster) | (Current model result ...)"
Emoji = party (🔵 D, 🔴 R, ⚪️/🟢/🟡/🟣 independent / other). Notes: "(Republican Pollster)" / "(Democratic Pollster)" and
"(Pro-<name> ...)" become the sponsor tag the race-poll corrections use; "(Released Today)" marks an older poll released late.
No sample size or population is given (race polls do not need them). The posted date is taken as the field END date.
Only Senate, governor and House ballot posts are read; national generic-ballot posts are read separately
(national_generic below); state generic-ballot posts are skipped.
    python -m midterms.bluesky_polls        -> data/cache/bluesky_race_polls.csv
"""
import json, re, time, urllib.request, urllib.parse
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
ACTOR = "usapolling.bsky.social"
PARTY = {"🔵": "D", "🔴": "R", "⚪": "I", "⚪️": "I", "🟢": "O", "🟡": "O", "🟣": "O", "🟠": "O"}


# Polling USA name typos -> the pollster's name (2026-10-01: "DMH / Research" = DHM Research, WA-3 9/27; the typo read as a
# brand-new pollster, variance x1.75)
# and sponsor names for a known pollster pair ("AARP" = Fabrizio Ward (R) / Impact Research (D): the ME Senate 9/22 and NC Senate
# 9/20 polls were each counted twice, once as Wikipedia's "Fabrizio Ward (R)/ Impact Research (D)" and once as "AARP")
# "Shaw/Beacon" = the Fox News pair Beacon Research (D) / Shaw & Co (R); the short form read as a brand-new pollster (x1.75
# variance) on the 2026-10-01 Fox TX/IA polls
NAME_FIX = {"DMH / Research": "DHM Research", "DMH Research": "DHM Research", "AARP": "Fabrizio Ward/Impact Research",
            "Shaw/Beacon": "Beacon Research/Shaw & Company Research", "Beacon/Shaw": "Beacon Research/Shaw & Company Research",
            "Fox News": "Beacon Research/Shaw & Company Research",
            "Wedgewood": "Wedgewood Polls"}   # 2026-10-04: GA Senate/governor 10/3 filed apart from 8 races of "Wedgewood Polls"


KEEP_DAYS = 900            # posts kept in the local post cache (the grade harvest reads up to 800 days back)


def _post_cache(actor):
    return ROOT / "data" / "cache" / f"bluesky_posts_{re.sub(r'[^a-z0-9]+', '_', actor.lower())}.json"


def fetch(days=30, max_pages=12, actor=ACTOR):
    """The actor's posts of the last `days` days, newest first. Posts are immutable, so they are kept in a local cache
    (data/cache, carried between runs) and each run reads the feed only until it reaches a post it already has: normally
    one page (100 posts) per account per run instead of the whole window."""
    from . import fetch as F
    fp = _post_cache(actor)
    have = json.loads(fp.read_text()) if fp.exists() else []
    known = {p["uri"] for p in have}
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=days)
    oldest_cached = min((pd.Timestamp(p["created"]) for p in have), default=None)
    new, cursor = [], None
    for _ in range(max_pages):
        q = {"actor": actor, "limit": 100, "filter": "posts_no_replies"}
        if cursor: q["cursor"] = cursor
        d = json.loads(F.open_url(API + "?" + urllib.parse.urlencode(q), timeout=60))
        hit = False
        for it in d.get("feed", []):
            if it.get("reason"): continue                       # reposts
            if it["post"]["uri"] in known: hit = True; continue
            rec = it["post"]["record"]; new.append({"uri": it["post"]["uri"], "created": rec.get("createdAt"), "text": rec.get("text", "")})
        cursor = d.get("cursor")
        last = pd.Timestamp(d["feed"][-1]["post"]["record"].get("createdAt")) if d.get("feed") else None
        if not cursor or last is None or last < cutoff: break                      # the window is covered
        if hit and oldest_cached is not None and oldest_cached <= cutoff: break   # the cache covers the rest
        time.sleep(0.5)
    allp = {p["uri"]: p for p in have}; allp.update({p["uri"]: p for p in new})
    keep = pd.Timestamp.utcnow() - pd.Timedelta(days=KEEP_DAYS)
    posts = sorted((p for p in allp.values() if p.get("created") and pd.Timestamp(p["created"]) >= keep), key=lambda p: p["created"], reverse=True)
    fp.parent.mkdir(parents=True, exist_ok=True); fp.write_text(json.dumps(posts))
    return [p for p in posts if pd.Timestamp(p["created"]) >= cutoff]


def parse(text):
    """-> list of dict(state, office, seat, cands[(name, party, pct)], pollster, end_date, sponsor, released_late) or []"""
    from .data_prep import _ST
    lines = [l.strip() for l in text.split("\n")]
    head = lines[0] if lines else ""
    m = re.match(r"^(?P<geo>[A-Za-z .]+?)\s*-\s*(?P<race>.+?)\s+Poll(?:ing|s)?:?\s*$", head)
    if not m:
        # combined post: "Georgia Polling:" then "Senate:" / "Governor:" sections sharing one pollster line
        c = re.match(r"^(?P<geo>[A-Za-z .]+?)\s+Poll(?:ing|s)?:?\s*$", head)
        st = _ST.get(c.group("geo").strip()) if c else None
        if not st: return []
        body = "\n".join(lines[1:]); out = []
        # ANY "<label>:" line opens a section (2026-09-24: an unrecognised "ME-2:" header merged the House matchup into the
        # governor block - Pingree/Charles/Dunlap/LePage as one race - and both polls were lost); unknown labels parse to [].
        secs = re.split(r"^\s*([^\n:🔵🔴⚪🟢🟡🟣🟠|]{2,40}?)\s*:\s*$", body, flags=re.M)
        tail = "\n".join(l for l in body.split("\n") if not re.search(r"(🔵|🔴|⚪|🟢|🟡|🟣|🟠)", l))
        for race, chunk in zip(secs[1::2], secs[2::2]):
            # keep the blank lines: they separate VERSIONS of the poll (full field / head-to-head, several matchups)
            cand = "\n".join(l for l in chunk.split("\n") if re.search(r"(🔵|🔴|⚪|🟢|🟡|🟣|🟠)", l) or not l.strip())
            out += parse(f"{c.group('geo').strip()} - {race} Polling:\n{cand}\n{tail}")
        return out
    st = _ST.get(m.group("geo").strip())
    if not st: return []
    race = m.group("race").strip().lower()
    if re.search(r"generic|approval|favor", race): return []
    body = "\n".join(lines[1:])
    src = re.search(r"^(?P<p>[^\n|]+?)\s*/\s*(?P<d>[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})", body, re.M)
    if not src: return []
    pollster = src.group("p").strip(); end = pd.to_datetime(src.group("d").replace("Sept", "Sep"), errors="coerce")
    pollster = NAME_FIX.get(pollster, pollster)
    notes = " ".join(re.findall(r"\(([^)]*)\)", body)).lower()
    sponsor = "R" if "republican pollster" in notes or "gop pollster" in notes else ("D" if "democratic pollster" in notes else "")
    tag_src = "label" if sponsor else ""     # 2026-09-30: "Republican Pollster" is a pollster-lean LABEL, "(X Internal)" a sponsor
    # 2026-10-01: "(Democratic Commissioner)" / "(Republican Commissioner)" = commissioned by that side (KS governor GSG 9/16)
    com = re.search(r"\((democratic|republican|gop) commissioner\)", body.lower())
    if com: sponsor, tag_src = ("D" if com.group(1) == "democratic" else "R"), "internal"
    pro = re.search(r"\(pro-([a-z .'-]+?)[ )]", body.lower()) or re.search(r"\(([a-z .'-]+?) (?:campaign )?internal\)", body.lower())
    # 2026-10-03: a post often carries several VERSIONS of a race - full field and head-to-head, two matchups, or district
    # subsamples - as blank-line-separated blocks. They were read as ONE candidate list: the shares summed to 170-300 % ('other'
    # 184 % on TSU's Texas post) and the D and R came from different versions (UNH NH Senate: Pappas's 45 from the Brown matchup
    # against Sununu's 45 from the Pappas matchup; TSU's "statewide" 53-42 was the TX-34 district subsample). Each block is now
    # its own version; a block under a district label ("TX-15 - (Trump +18)") in a statewide post is a sub-state sample and is
    # not a statewide poll; build() averages the versions of one matchup (wiki_polls rule 3).
    rx = r"(🔵|🔴|⚪️|⚪|🟢|🟡|🟣|🟠)\s*([^:|\n]+?):\s*([\d.]+)%"
    versions, substate = [], 0
    for chunk in re.split(r"\n\s*\n", body):
        vc = [(n.strip(), PARTY.get(e, "O"), float(v)) for e, n, v in re.findall(rx, chunk)]
        if len(vc) < 2: continue
        if re.search(r"^\s*(?:[A-Z]{2}-(?:\d+|AL)\b|(?:CD|District)\s*\d+\b)", chunk, re.M) and not re.search(r"house|district|cd", race):
            substate += 1; continue
        versions.append(vc)
    if substate and not versions:
        print(f"  !! Polling USA: {m.group('geo').strip()} {race}: only district subsamples ({substate}) - not a statewide poll, skipped")
    cands = versions[0] if versions else []
    if pro:
        who = pro.group(1).strip()        # first four letters: the feed misspells names ("Davis Internal" for Davies)
        hit = [c for c in cands if who.split()[-1][:4] in c[0].lower()]
        if hit: sponsor = {"D": "D", "I": "I", "R": "R"}.get(hit[0][1], sponsor); tag_src = "internal"
    blocks = []
    if race.startswith("senate"): office, seat = "senate", st
    elif race.startswith("gov"): office, seat = "governor", st                 # "Govenor" typos included
    else:
        h = (re.match(r"(?:(\d+)(?:st|nd|rd|th)|at-large)\s+(?:house|district|cd)", race)
             or re.match(r"(?:[a-z]{2}-|cd-?|district\s+)(\d+|al)\b", race))
        if not h: return []
        k = h.group(1) if h.lastindex else None
        office, seat = "house", f"{st}-{int(k) if k and k.isdigit() else 1}"
    if len(cands) < 2: return []
    for vc in versions:
        blocks.append({"state": st, "office": office, "seat": seat, "cands": vc, "pollster": pollster, "end_date": end,
                       "sponsor": sponsor, "tag_src": tag_src, "released_late": "released today" in notes})
    return blocks


def get_posts(uris):
    """Posts by URI (app.bsky.feed.getPosts, 25 per call) -> [{uri, created, text}]."""
    out = []
    uris = list(dict.fromkeys(u for u in uris if isinstance(u, str) and u.startswith("at://")))
    cached = {}                                       # posts already in the local post caches need no request
    for fp in (ROOT / "data" / "cache").glob("bluesky_posts_*.json"):
        for p in json.loads(fp.read_text()): cached[p["uri"]] = p
    out += [cached[u] for u in uris if u in cached]; uris = [u for u in uris if u not in cached]
    for i in range(0, len(uris), 25):
        q = urllib.parse.urlencode([("uris", u) for u in uris[i:i + 25]])
        from . import fetch as F
        d = json.loads(F.open_url("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts?" + q, timeout=60))
        out += [{"uri": x["uri"], "created": x["record"].get("createdAt"), "text": x["record"].get("text", "")} for x in d.get("posts", [])]
        time.sleep(0.3)
    return out


def build(days=30):
    return _build(fetch(days), days)


def _build(posts, days=30, carry=True):
    rows = []
    for p in posts:
        for b in parse(p["text"]):
            d = [c for c in b["cands"] if c[1] == "D"]; r = [c for c in b["cands"] if c[1] == "R"]; i = [c for c in b["cands"] if c[1] == "I"]
            if not r or not (d or i): continue
            rep = max(r, key=lambda c: c[2])
            chal = max(d, key=lambda c: c[2]) if d and (not i or max(d, key=lambda c: c[2])[2] >= max(i, key=lambda c: c[2])[2]) else max(i, key=lambda c: c[2])
            tot = sum(c[2] for c in b["cands"])
            if tot > 105:                         # still more than one ballot in the block: never emit a mixed row
                print(f"  !! Polling USA: {b['state']} {b['office']} {b['pollster']} {b['end_date'].date() if hasattr(b['end_date'], 'date') else b['end_date']}: "
                      f"candidate shares sum to {tot:.0f} % - skipped"); continue
            sp = b["sponsor"] or ("D" if re.search(r"\b(DCCC|DSCC|DGA|DLCC|Democratic)\b", b["pollster"]) else ("R" if re.search(r"\b(NRCC|NRSC|RGA|RSLC|Republican)\b", b["pollster"]) else ""))
            ts = b.get("tag_src") or ("committee" if sp else "")
            tag = {"D": " (D)", "R": " (R)", "I": " (I)"}.get(sp, "")
            rows.append({"state": b["state"], "office": b["office"], "seat": b["seat"], "pollster": b["pollster"] + tag, "end_date": b["end_date"],
                         "dem_name": chal[0], "rep_name": rep[0], "challenger_party": chal[1], "dem": chal[2], "rep": rep[2],
                         "other": round(tot - chal[2] - rep[2], 1), "und": round(max(0.0, 100 - tot), 1), "n": None, "grade": 1.5,
                         "posted": p["created"][:10], "released_late": b["released_late"], "uri": p["uri"], "tag_src": ts})
    df = pd.DataFrame(rows)
    if len(df): df = df[df.end_date.notna()]
    if len(df):
        df["margin"] = df.dem - df.rep
        df = df.sort_values("posted").drop_duplicates(["office", "seat", "pollster", "end_date", "dem", "rep"], keep="first")
        # versions of one poll and matchup (full field / head-to-head, LV / RV) -> one row, averaged (wiki_polls rule 3)
        key = ["office", "seat", "pollster", "end_date", "dem_name", "rep_name"]
        agg = {c: "first" for c in df.columns if c not in key}; agg.update({k: "mean" for k in ("dem", "rep", "margin", "other", "und")})
        df = df.groupby(key, as_index=False, sort=False).agg(agg); df[["dem", "rep", "other", "und"]] = df[["dem", "rep", "other", "und"]].round(1)
    print(f"Polling USA (Bluesky): {len(df)} race polls in the last {days} days - " + (df.office.value_counts().to_dict().__str__() if len(df) else ""))
    if len(df): df["src"] = "usapolling"
    if not carry: return df                    # re-parse of carried Polling USA posts only (no second / third feed)
    try: pr = pollresults_build(days)
    except Exception as e: print("pollresults.org feed failed:", str(e)[:100]); pr = pd.DataFrame()
    if len(pr):
        # Polling USA rows stay (they carry the partisan-sponsor notes); pollresults.org adds the polls it does not have
        from . import model as M
        keep = []
        for r in pr.itertuples():
            race = df[(df.office == r.office) & (df.seat == r.seat)] if len(df) else None
            if race is not None and M._same_poll(r.pollster, r.end_date, r.margin, race): keep.append(False); continue
            j = _identical_copy(r, race)
            if j is not None:                       # a copy under another name: keep the Polling USA row, borrow the n
                if pd.isna(df.at[j, "n"]) and pd.notna(r.n): df.at[j, "n"] = r.n
                keep.append(False); continue
            keep.append(True)
        new = pr[keep] if len(df) else pr
        print(f"  pollresults.org: {len(pr)} matched race polls, {len(new)} not in the Polling USA feed")
        df = pd.concat([df, new], ignore_index=True)
    # third: the pollresults.org race pages themselves (2026-09-26) - every poll of every race with the published decimals,
    # sample size and population; only the polls neither Bluesky feed carried are added (pollresults_site.py)
    try:
        from . import pollresults_site as PS
        site = PS.build()
    except Exception as e: print("pollresults.org site failed:", str(e)[:100]); site = pd.DataFrame()
    if len(site):
        from . import model as M
        keep = []
        for r in site.itertuples():
            race = df[(df.office == r.office) & (df.seat == r.seat)] if len(df) else None
            if race is not None and M._same_poll(r.pollster, r.end_date, r.margin, race): keep.append(False); continue
            j = _identical_copy(r, race)
            if j is not None:
                if pd.isna(df.at[j, "n"]) and pd.notna(getattr(r, "n", None)): df.at[j, "n"] = r.n
                keep.append(False); continue
            keep.append(True)
        new = site[keep] if len(df) else site
        print(f"  pollresults.org site: {len(site)} race polls, {len(new)} in neither Bluesky feed")
        df = pd.concat([df, new], ignore_index=True)
    # Forced "(R)" tag for known Republican-leaning pollsters (added 2026-09-29 for Rasmussen / Big Data Poll) - REMOVED the same
    # day: on 538's raw_polls (2016-22, the 13 races with Rasmussen polls) the tag was a wash (6 better / 7 worse, p 0.41); each
    # pollster's own record already enters through race_poll_calibration.lean_for. FORCE_R_TAG = True restores it.
    if FORCE_R_TAG and len(df):
        lean = df["pollster"].astype(str).str.contains(r"^(?:rasmussen|big data)", case=False, regex=True) & ~df["pollster"].astype(str).str.contains(r"\((?:R|D)\)")
        df.loc[lean, "pollster"] = df.loc[lean, "pollster"].astype(str).str.strip() + " (R)"
    # 2026-10-01: keep the feed polls that have scrolled out of the fetch window. The Bluesky feeds are read `days` back, so a
    # poll known only from them (CA governor Wolfson (R) 8/16, posted 8/24) dropped out of the model once its post was older
    # than the window. Rows posted inside the window are always re-parsed (fixes apply); older rows are carried forward
    # unless the same poll is already present.
    fp = ROOT / "data" / "state" / "bluesky_race_polls.csv"
    if fp.exists() and len(df):
        old = pd.read_csv(fp)
        cut = (pd.Timestamp.today().normalize() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        old = old[old["src"].isin(["usapolling", "pollresults"]) & (old["posted"].astype(str) < cut)]
        if len(old):
            # 2026-10-03: carried rows are RE-PARSED from their posts (fetched by URI) so parser fixes reach them too - the mixed-
            # version rows (TSU's district subsample as "Texas 53-42", UNH NH's two matchups as one 45-45) had been carried as stored
            us = old[old["src"] == "usapolling"]
            if len(us):
                try:
                    fresh = _build(get_posts(us["uri"].tolist()), days, carry=False)
                    fresh = fresh[fresh["src"] == "usapolling"] if len(fresh) else fresh
                    if len(fresh): fresh = fresh.assign(posted=fresh["posted"].astype(str))
                    old = pd.concat([old[old["src"] != "usapolling"], fresh], ignore_index=True) if len(fresh) else old[old["src"] != "usapolling"]
                except Exception as e:
                    print(f"  carried posts not re-parsed ({str(e)[:60]}): mixed rows dropped instead")
                    old = old[(old["dem"] + old["rep"] + old["other"].fillna(0)) <= 105]
            from . import model as M
            cur = df.copy(); cur["end_date"] = pd.to_datetime(cur["end_date"]); old["end_date"] = pd.to_datetime(old["end_date"])
            keep = []
            for r in old.itertuples():
                race = cur[(cur.office == r.office) & (cur.seat == r.seat)]
                keep.append(not (len(race) and (M._same_poll(r.pollster, r.end_date, r.margin, race) or _identical_copy(r, race) is not None)))
            old = old[keep]
            if len(old):
                print(f"  carried forward: {len(old)} feed polls older than the {days}-day window")
                df = pd.concat([df, old], ignore_index=True)
    df.to_csv(fp, index=False)
    return df


FORCE_R_TAG = False


# ---- second feed: Political Poll Bot, @pollresults.org (2026-09-24, user suggestion) ------------------------------------
# Template (one race per post, several posts per release):
#     "[2026 ]<State | ST> <US Senate | Governor | House (Second Congressional District)>" / "Sep 17 - 21, 2026" / blank /
#     "Troy Jackson (D) 51% (+4.0%)" ... "Don't know 1%" / blank / "University of New Hampshire (B-)" / "1301 LV" / "View Poll"
# Party tags only appear from late Sep 2026; before that the names are bare, so parties come from each race's ballot
# (Wikipedia race tables) by surname. A candidate who is not on the ballot makes the row unusable, as in the other feeds.
PR_ACTOR = "pollresults.org"
_ORD = {w: i for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
        "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split(), 1)}
_SKIP = re.compile(r"^(don'?t know|undecided|someone else|other|refused|would not vote|none|not sure|no answer|skipped)", re.I)


def pr_parse(text):
    from .data_prep import _ST
    L = [l.strip() for l in text.split("\n")]
    if len(L) < 5: return None
    head = re.sub(r"^\d{4}\s+", "", L[0])
    abbr = {v: v for v in _ST.values()}
    st = race = None
    for name in sorted(list(_ST) + list(abbr), key=len, reverse=True):
        if head.startswith(name + " "): st = _ST.get(name) or abbr[name]; race = head[len(name):].strip().lower(); break
    if not st: return None
    if race.startswith("us senate"): office, seat = "senate", st
    elif race.startswith("governor"): office, seat = "governor", st
    elif race.startswith("house"):
        m = re.search(r"\((\w+)[- ]congressional district\)|district (\d+)|\((\d+)", race)
        if "at-large" in race or "at large" in race: k = 1
        elif m: k = _ORD.get((m.group(1) or "").lower()) or int(m.group(2) or m.group(3) or 0)
        else: return None
        if not k: return None
        office, seat = "house", f"{st}-{k}"
    else: return None
    dm = re.match(r"([A-Z][a-z]{2})\.? (\d{1,2})(?:\s*-\s*(?:([A-Z][a-z]{2})\.? )?(\d{1,2}))?, (\d{4})", L[1])
    if not dm: return None
    mon = dm.group(3) or dm.group(1); day = dm.group(4) or dm.group(2)
    end = pd.to_datetime(f"{mon} {day} {dm.group(5)}", errors="coerce")
    cands, i = [], 3
    while i < len(L) and L[i]:
        c = re.match(r"^(.+?)(?:\s+\(([A-Z]{1,4})\))?\s+([\d.]+)%", L[i]); i += 1
        if c and not _SKIP.match(c.group(1)): cands.append((c.group(1).strip(), c.group(2), float(c.group(3))))
    rest = [l for l in L[i:] if l and l != "View Poll"]
    if not rest or len(cands) < 2: return None
    pollster = re.sub(r"\s*\((?:[A-F][+-]?(?:/[A-F][+-]?)?)\)\s*$", "", rest[0]).strip()
    n = re.match(r"(\d[\d,]*)\s*(LV|RV|A|V)\b", rest[1]) if len(rest) > 1 else None
    # 2026-09-30: the post's own "Partisan pollster (D|R|I)" line (NYT's flag) was ignored, so Impact Research (D) MT-1,
    # PPP (D) TX-9 and Zenith (I) SD entered untagged - no sponsor shift, full weight
    pt = next((m.group(1) for m in (re.match(r"Partisan pollster \((D|R|I)\)", l) for l in rest) if m), "")
    # 2026-10-01: NYT also marks a partisan SPONSOR on the sponsor line ("Sponsor: House Majority PAC (D)", TX-15 NPA 9/28;
    # "Sponsor: Alabama Policy Action, Napolitan News Service (R)") without a "Partisan pollster" line
    if not pt: pt = next((m.group(1) for m in (re.match(r"Sponsor:.*\((D|R)\)\s*$", l) for l in rest) if m), "")
    return {"state": st, "office": office, "seat": seat, "cands": cands, "pollster": pollster, "end_date": end,
            "n": int(n.group(1).replace(",", "")) if n else None, "pop": n.group(2).lower() if n else None, "partisan": pt}



def _identical_copy(r, others):
    """The same survey under a sponsor's name in one feed and the pollster's in the other (AARP = Fabrizio/Impact Research,
    TSU = its Barbara Jordan centre, TPOR = Slingshot, 'Research & Polling' / '... Inc.'): the name check misses these, so
    a row in the same race ending within a day with IDENTICAL Dem and Rep shares is a copy. 10 such pairs were counted
    twice until 2026-09-29. Returns the index of the matching row in `others`, or None."""
    if others is None or not len(others): return None
    near = (pd.to_datetime(others["end_date"]) - pd.Timestamp(r.end_date)).abs() <= pd.Timedelta(days=1)
    hit = others[near & (others["dem"] == r.dem) & (others["rep"] == r.rep)]
    return hit.index[0] if len(hit) else None


def _ballots():
    """{(office, seat): [(name, 'D'|'R'|'I'|'O'), ...]} from the race tables the models use."""
    from . import senate2026 as S, gov2026 as G
    code = lambda p: "D" if str(p).startswith(("Democratic", "DFL")) else "R" if str(p).startswith("Republican") else "I" if str(p).startswith("Independent") else "O"
    out = {}
    for r in S.races().itertuples():
        out.setdefault(("senate", r.state), []).extend((n, code(p)) for n, p in (r.ballot or []))
    for r in G.races().itertuples(): out[("governor", r.state)] = [(n, code(p)) for n, p in r.cands]
    h = pd.read_csv(ROOT / "data" / "cache" / "house2026_seats.csv")
    for r in h.itertuples(): out[("house", r.seat)] = [(n.strip(), code(p)) for n, p in re.findall(r"▌([^▌\[]+?)\s*\(([^)]+)\)", str(r.cands))]
    return out


def pollresults_build(days=30):
    from .model import _sur5
    ballots = _ballots(); rows = []; unmatched = 0
    for p in fetch(days, actor=PR_ACTOR):
        b = pr_parse(p["text"])
        if not b: continue
        ballot = {_sur5(n): pt for n, pt in ballots.get((b["office"], b["seat"]), [])}
        if not ballot: continue
        named = [(n, ballot.get(_sur5(n)), v) for n, tag, v in b["cands"]]
        d = [c for c in named if c[1] == "D"]; r = [c for c in named if c[1] == "R"]; i = [c for c in named if c[1] == "I"]
        if not r or not (d or i): unmatched += 1; continue
        rep = max(r, key=lambda c: c[2])
        chal = max(d, key=lambda c: c[2]) if d and (not i or max(d, key=lambda c: c[2])[2] >= max(i, key=lambda c: c[2])[2]) else max(i, key=lambda c: c[2])
        tot = sum(c[2] for c in b["cands"])
        sp = b.get("partisan") or ("D" if re.search(r"\b(DCCC|DSCC|DGA|DLCC|Democratic)\b", b["pollster"]) else ("R" if re.search(r"\b(NRCC|NRSC|RGA|RSLC|Republican)\b", b["pollster"]) else ""))
        rows.append({"state": b["state"], "office": b["office"], "seat": b["seat"], "pollster": b["pollster"] + {"D": " (D)", "R": " (R)", "I": " (I)"}.get(sp, ""), "pop": b["pop"],
                     "tag_src": "nyt" if b.get("partisan") else ("committee" if sp else ""),
                     "end_date": b["end_date"], "dem_name": chal[0], "rep_name": rep[0], "challenger_party": chal[1], "dem": chal[2], "rep": rep[2],
                     "other": round(tot - chal[2] - rep[2], 1), "und": round(max(0.0, 100 - tot), 1), "n": b["n"], "grade": 1.5,
                     "posted": p["created"][:10], "released_late": False, "uri": p["uri"], "src": "pollresults"})
    df = pd.DataFrame(rows)
    if len(df):
        df = df[df.end_date.notna()]
        # one survey, several posted versions (likely / registered voters, with and without leaners): Siena's Maine
        # governor poll arrived twice on 2026-09-24. One survey is one poll: the versions are averaged into one row.
        key = ["office", "seat", "pollster", "end_date", "dem_name", "rep_name"]
        num = {c: "mean" for c in ("dem", "rep", "other", "und")}
        first = {c: "first" for c in df.columns if c not in key and c not in num}
        df = df.sort_values("posted").groupby(key, as_index=False).agg({**num, **first})
        df[["dem", "rep", "other", "und"]] = df[["dem", "rep", "other", "und"]].round(1)
        df["margin"] = df.dem - df.rep
    return df


if __name__ == "__main__":
    d = build(); print(d.sort_values("end_date").tail(25)[["office", "seat", "pollster", "end_date", "dem_name", "dem", "rep_name", "rep", "other"]].to_string(index=False))


# ---- National generic ballot from Polling USA (2026-09-26, user: "make sure our model updates daily (including the polling
# aggregators)"). VoteHub's generic feed lags weeks, so without a second feed the national trend had no fresh polls. Only posts headed exactly "Generic Ballot Polling:" / "Generic Congressional Polling:" are read: state
# ("Iowa - Generic ..."), subgroup ("... Among:"), average and "Certain To Vote" posts are skipped. The post names one date; it
# is matched against VoteHub end dates in generic.merged (calibration there).
# 2026-10-04 (NBC/Telemundo, 800 Latino RV, D 59-35): a SUBGROUP sample is not a national generic-ballot poll and must
# never enter the trend, whatever heading a feed gives it
_SUBGROUP = re.compile(r"\b(latin[oa]s?|latinx|hispanics?|black (?:voters|americans)|african[- ]americans?|asian[- ]americans?|"
                       r"gen[- ]?z|young (?:voters|americans)|youth|under 30|women voters|men voters|catholics?|evangelicals?|"
                       r"veterans|union households?)\b|telemundo|univision", re.I)
_GEN_HEAD = re.compile(r"^\s*Generic (?:Ballot|Congressional) Polling\s*:", re.I)
_GEN_D = re.compile(r"(?:DEM|Democrat(?:ic|s)?)\s*:?\s*(\d{1,2}(?:\.\d)?)%", re.I)
_GEN_R = re.compile(r"(?:GOP|Republican(?:s)?)\s*:?\s*(\d{1,2}(?:\.\d)?)%", re.I)
_GEN_SRC = re.compile(r"\n\s*([^\n/]+?)\s*/\s*([A-Z][a-z]+)\.?\s+(\d{1,2}),\s*(\d{4})")


NATIONAL_2024_HOUSE = -2.7                 # D minus R, 2024 national House popular vote


def national_generic(days=60):
    rows = []
    for p in fetch(days, max_pages=20):
        t = p.get("text", "")
        if not _GEN_HEAD.search(t) or _SUBGROUP.search(t): continue
        d, r, s = _GEN_D.search(t), _GEN_R.search(t), _GEN_SRC.search(t)
        if not (d and r and s): continue
        mon = s.group(2)[:3].title().replace("Sep", "Sep")
        try: date = pd.to_datetime(f"{mon} {s.group(3)} {s.group(4)}", format="%b %d %Y")
        except Exception: continue
        # A post with a "% change with 2024" note must reconcile with the NATIONAL 2024 House vote (R+2.7). 2026-09-26:
        # "Marist / Sept 20, DEM 49 (+9), GOP 46 (-12)" carried no state label but implies 2024 at D 40 / R 58 -- Iowa's
        # result (Marist's Iowa poll ended the same day); it had pulled the generic trend down 0.2.
        cd = re.search(r"(?:DEM|Democrat\w*)\s*:?\s*[\d.]+%\s*\(([+-]?\d+(?:\.\d)?)\)", t, re.I)
        cr = re.search(r"(?:GOP|Republican\w*)\s*:?\s*[\d.]+%\s*\(([+-]?\d+(?:\.\d)?)\)", t, re.I)
        if cd and cr:
            m24 = (float(d.group(1)) - float(cd.group(1))) - (float(r.group(1)) - float(cr.group(1)))
            if abs(m24 - NATIONAL_2024_HOUSE) > 5.0:
                continue
        rows.append({"pollster": s.group(1).strip(), "date": date, "dem": float(d.group(1)), "rep": float(r.group(1)),
                     "partisan": "REP" if re.search(r"Republican Pollster", t, re.I) else ("DEM" if re.search(r"Democratic Pollster", t, re.I) else None),
                     "posted": pd.Timestamp(p["created"]).tz_convert("America/New_York").tz_localize(None), "uri": p.get("uri")})
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["pollster", "date", "dem", "rep"]) if len(df) else df


# ---- pollresults.org national posts (2026-09-29, user: "political poll bot on bluesky has them" - Angus Reid and YouGov
# generic-ballot polls that neither VoteHub nor Polling USA carried). Layout, one field per line:
#   National House | Sep 25 - 28, 2026 | (blank) | Generic Democrat (D) 53% (+15.0%) | Generic Republican (R) 38% |
#   Don't know 6% ... | (blank) | YouGov (B) | 1003 LV | Sponsor: Economist | Partisan pollster (D) | View Poll
#   Donald Trump Approval | Sep 25 - 28, 2026 | | Disapprove 62% | Approve 36% | | YouGov (B) | 1007 LV | ...
# Unlike Polling USA these carry the sample size and population, and each population is its own post.
def _pr_date(line):
    m = re.match(r"([A-Z][a-z]{2})\.? (\d{1,2})(?:\s*-\s*(?:([A-Z][a-z]{2})\.? )?(\d{1,2}))?, (\d{4})", line or "")
    if not m: return None, None
    y = m.group(5); end = pd.to_datetime(f"{m.group(3) or m.group(1)} {m.group(4) or m.group(2)} {y}", errors="coerce")
    start = pd.to_datetime(f"{m.group(1)} {m.group(2)} {y}", errors="coerce")
    if pd.notna(start) and pd.notna(end) and start > end: start = start - pd.DateOffset(years=1)
    return start, end


def pr_national(days=60, kind="generic"):
    """pollresults.org national generic-ballot ("National House") or Trump approval posts -> DataFrame(pollster, sponsor,
    start_date, end_date, n, pop, dem, rep, partisan, posted, uri); dem/rep = approve/disapprove for approval."""
    head = "National House" if kind == "generic" else "Donald Trump Approval"
    rows = []
    for p in fetch(days, max_pages=20, actor=PR_ACTOR):
        L = [l.strip() for l in p.get("text", "").split("\n")]
        if not L or L[0] != head or _SUBGROUP.search(p.get("text", "")): continue
        start, end = _pr_date(L[1] if len(L) > 1 else "")
        if end is None or pd.isna(end): continue
        vals = {}
        for l in L[2:]:
            m = re.match(r"^(Generic Democrat|Generic Republican|Approve|Disapprove)\b.*?([\d.]+)%", l)
            if m: vals[m.group(1)] = float(m.group(2))
        a, b = ("Generic Democrat", "Generic Republican") if kind == "generic" else ("Approve", "Disapprove")
        if a not in vals or b not in vals: continue
        pollster = n = pop = sponsor = partisan = None
        for i, l in enumerate(L):
            m = re.match(r"(\d[\d,]*)\s*(LV|RV|A|V)\b", l)
            if m and i > 2:
                n, pop = int(m.group(1).replace(",", "")), m.group(2).lower()
                pollster = re.sub(r"\s*\((?:[A-F][+-]?(?:/[A-F][+-]?)?)\)\s*$", "", L[i - 1]).strip()
            s = re.match(r"Sponsor:\s*(.+)", l)
            if s: sponsor = s.group(1).strip()
            q = re.match(r"Partisan pollster \((D|R)\)", l)
            if q: partisan = "DEM" if q.group(1) == "D" else "REP"
        if not pollster: continue
        rows.append({"pollster": pollster, "sponsor": sponsor, "start_date": start, "end_date": end, "n": n, "pop": pop,
                     "dem": vals[a], "rep": vals[b], "partisan": partisan,
                     "posted": pd.Timestamp(p["created"]).tz_convert("America/New_York").tz_localize(None), "uri": p.get("uri")})
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["pollster", "end_date", "pop", "dem", "rep"]) if len(df) else df
