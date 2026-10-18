"""Canonical pollster names: one key per pollster across the national poll feeds; scraped text made plain."""

import re

# Scraped names (Wikipedia tables, the VoteHub API, poll posts) reach the published JSON and from there the pages' markup:
# no angle brackets, and no control characters (each becomes a space).
_UNSAFE = re.compile(r"[<>\x00-\x1f\x7f-\x9f]")


def clean_text(s) -> str:
    return _UNSAFE.sub(lambda m: "" if m.group() in "<>" else " ", str(s))


def clean_tree(x):
    """clean_text on every string of a parsed JSON feed (keys too); other values unchanged."""
    if isinstance(x, str):
        return clean_text(x)
    if isinstance(x, dict):
        return {clean_tree(k): clean_tree(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean_tree(v) for v in x]
    return x


# pollster name harmonisation across the poll feeds (VoteHub, Polling USA, pollresults.org)
ALIASES = {
    "angus reid global": "angus reid",
    "mclaughlin & associates": "mclaughlin",
    "economist/yougov": "yougov",
    "the economist/yougov": "yougov",
    "reuters/ipsos": "ipsos",
    "ny times/siena": "siena",
    "new york times": "siena",
    "nyt/siena": "siena",
    "big data poll": "big data poll",
    "rasmussen reports": "rasmussen",
    "fox news": "fox news",
    "harvard-harris": "harrisx",
    "harvard caps/harrisx": "harrisx",
    "2way/harrisx": "harrisx",
    "forbes/harrisx": "harrisx",
    # Ipsos fields the ABC News / Washington Post polls on its KnowledgePanel as it does Reuters'; the feeds file them under
    # several names (VoteHub: "ipsos" with the sponsors apart, or "washington post/abc news/ipsos"), and one firm under
    # three keys split its house effect and escaped the per-pollster share cap
    "wash post/ipsos": "ipsos",
    "washington post/ipsos": "ipsos",
    "abc/wash post/ipsos": "ipsos",
    "abc news/washington post/ipsos": "ipsos",
    "washington post/abc news/ipsos": "ipsos",
    "abc news/the washington post/ipsos": "ipsos",
    "the washington post/abc news/ipsos": "ipsos",
    "i&i/tipp": "tipp",
    "tipp insights": "tipp",
    "npr/pbs/marist": "marist",
    "marist university": "marist",
    "marist college": "marist",
    "marist poll": "marist",
    "npr/pbs news/marist": "marist",
    "the new york times/siena college": "siena",
    "the new york times/siena university": "siena",
    "new york times/siena university": "siena",
    "siena university": "siena",
    "new york times/siena college": "siena",
    "siena college": "siena",
    "harrisx/harris": "harrisx",
    "harrisx/harris poll": "harrisx",
    "deseret news/hinckley": "morning consult/deseret",
    "deseret news/hinckley institute": "morning consult/deseret",
    "deseret news": "morning consult/deseret",
    "morning consult/deseret news": "morning consult/deseret",
    "impact research/national research inc.": "wall street journal",
    "impact research/national research": "wall street journal",
    "public policy polling": "ppp",
    "strength in numbers/verasight": "verasight",
    "g. elliott morris/verasight": "verasight",
    "marquette": "marquette",
    "marquette university law school": "marquette",
    "emerson": "emerson",
    "emerson college": "emerson",
    "cygnal": "cygnal",
    "quantus insights": "quantus",
    "rmg research": "rmg",
    "echelon insights": "echelon",
    "morning consult": "morning consult",
    "quinnipiac": "quinnipiac",
    "quinnipiac university": "quinnipiac",
    "cbs news": "yougov/cbs",
    "cbs news/yougov": "yougov/cbs",
    "atlas intel": "atlasintel",
    "atlasintel": "atlasintel",
    "daily mail": "jl partners",
    "j.l. partners": "jl partners",
    "financial times": "ft/yougov",
    "the bullfinch group": "bullfinch",
    "cnn": "cnn/ssrs",
    "cnn/ssrs": "cnn/ssrs",
    "nbc news": "nbc",
    "yahoo news": "yougov/yahoo",
    "yahoo news/yougov": "yougov/yahoo",
    # Polling USA writes "Honest Polling", pollresults.org "The Honest Poll" - one pollster; otherwise the Oct 4-6
    # survey enters twice (pollresults RV 52-41 and Polling USA 54-42 = its LV version mislabelled RV), each at full weight.
    "honest polling": "the honest poll",
    "wsj": "wall street journal",
    "centerline research & strategy": "centerline research",
    # VoteHub writes "truedot.ai", pollresults.org "TrueDot" (the Decision Desk HQ/NewsNation pollster) - one
    # pollster, so its house effect pools and a later VoteHub copy of the Oct 5-9 poll cannot enter a second time.
    "truedot.ai": "truedot",
    # pollresults.org wrote Fox News's Oct 9-12 national poll as "Beacon Research/Shaw & Company Research";
    # the Jan and Apr polls (and every Fox state poll) are "... Shaw & Co. Research" - one pollster, one house effect.
    "beacon research/shaw & company research": "beacon research/shaw & co. research",
    # Short forms in the Polling USA posts: one pollster each, so the poll takes its pollster's house effect and rating
    # (Big Data Poll F, Clarity Campaign Labs B/C; under the short name it was unrated).
    "big data": "big data poll",
    "clarity campaign": "clarity campaign labs",
}


def canon(name: str) -> str:
    k = re.sub(r"\s+", " ", clean_text(name).lower().replace("*", "")).strip()
    if k in ALIASES:
        return ALIASES[k]
    # "Pollster - Sponsor" (a feed wrote "YouGov - CBS News") is the pollster; the sponsor travels in its own field.
    # Without this the CBS/YouGov 9/30-10/2 LV poll would enter twice ("yougov" and "yougov - cbs news"), each at full weight.
    if " - " in k:
        base = k.split(" - ", 1)[0].strip()
        if base:
            return ALIASES.get(base, base)
    return k
