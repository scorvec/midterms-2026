"""Canonical pollster names: one key per pollster across the national poll feeds."""
import re


# pollster name harmonisation across the poll feeds (VoteHub, Polling USA, pollresults.org)
ALIASES = {"angus reid global": "angus reid", "mclaughlin & associates": "mclaughlin", "economist/yougov": "yougov", "the economist/yougov": "yougov", "reuters/ipsos": "ipsos", "ny times/siena": "siena", "new york times": "siena", "nyt/siena": "siena", "big data poll": "big data poll",
           "rasmussen reports": "rasmussen", "fox news": "fox news", "harvard-harris": "harrisx", "harvard caps/harrisx": "harrisx", "2way/harrisx": "harrisx", "forbes/harrisx": "harrisx", "wash post/ipsos": "ipsos/wapo", "abc/wash post/ipsos": "ipsos/wapo",
           "i&i/tipp": "tipp", "tipp insights": "tipp", "npr/pbs/marist": "marist", "marist university": "marist", "marist college": "marist", "marist poll": "marist", "npr/pbs news/marist": "marist",
           "the new york times/siena college": "siena", "the new york times/siena university": "siena", "new york times/siena university": "siena", "siena university": "siena", "new york times/siena college": "siena", "siena college": "siena", "harrisx/harris": "harrisx", "harrisx/harris poll": "harrisx", "deseret news/hinckley": "morning consult/deseret", "deseret news/hinckley institute": "morning consult/deseret", "deseret news": "morning consult/deseret", "morning consult/deseret news": "morning consult/deseret", "impact research/national research inc.": "wall street journal", "impact research/national research": "wall street journal", "public policy polling": "ppp", "strength in numbers/verasight": "verasight", "g. elliott morris/verasight": "verasight", "marquette": "marquette", "marquette university law school": "marquette", "emerson": "emerson", "emerson college": "emerson", "cygnal": "cygnal",
           "quantus insights": "quantus", "rmg research": "rmg", "echelon insights": "echelon", "morning consult": "morning consult", "quinnipiac": "quinnipiac", "quinnipiac university": "quinnipiac", "cbs news": "yougov/cbs", "cbs news/yougov": "yougov/cbs",
           "atlas intel": "atlasintel", "atlasintel": "atlasintel", "daily mail": "jl partners", "j.l. partners": "jl partners", "financial times": "ft/yougov", "the bullfinch group": "bullfinch", "cnn": "cnn/ssrs", "cnn/ssrs": "cnn/ssrs", "nbc news": "nbc", "yahoo news": "yougov/yahoo", "yahoo news/yougov": "yougov/yahoo"}
def canon(name: str) -> str:
    k = re.sub(r"\s+", " ", str(name).lower().replace("*", "")).strip(); return ALIASES.get(k, k)
