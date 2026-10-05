"""Georgia House 2026 general-election nominees from the Georgia Secretary of State (official public records).
    python -m midterms.ga_sos probe      # Actions only: locate the SOS candidate / results files
"""
from __future__ import annotations
import re, sys, time, json
from .fetch import open_url, report

PROBE = [
    "https://sos.ga.gov/page/elections-candidates",
    "https://sos.ga.gov/index.php/elections/qualifying_candidate_information",
    "https://sos.ga.gov/elections",
    "https://results.sos.ga.gov/results/public/Georgia",
    "https://results.sos.ga.gov/results/public/api/elections/Georgia",
    "https://results.sos.ga.gov/cdn/results/Georgia/elections.json",
    "https://mvp.sos.ga.gov/s/qualifying-candidate-information",
]


def probe():
    for u in PROBE:
        try:
            b = open_url(u, timeout=60); t = b.decode("utf-8", "replace")
            print(f"\n### {u}: {len(b)} bytes, starts {t[:200]!r}")
            links = sorted(set(re.findall(r'(?:href|src)="([^"]+)"', t)))
            for l in links:
                if re.search(r"candidat|qualif|2026|result|export|csv|xlsx|json|runoff|primary", l, re.I): print("   ", l)
            for m in re.findall(r'"([^"]*(?:2026|Primary|Runoff|General)[^"]{0,80})"', t)[:60]: print("   str:", m)
        except Exception as e: print(f"\n### {u}: FAILED {e}")
        time.sleep(3)
    report()


def probe2():
    base = "https://results.sos.ga.gov/results/public/"
    h = open_url(base + "Georgia").decode("utf-8", "replace")
    js = re.findall(r'src="([^"]+\.js)"', h); print("scripts:", js)
    for j in js:
        time.sleep(2)
        try: t = open_url(base + j if not j.startswith("http") else j).decode("utf-8", "replace")
        except Exception as e: print(j, e); continue
        hits = sorted(set(re.findall(r'["`\']([^"`\'\s]*(?:api|cdn|export|elections|\.json)[^"`\'\s]{0,80})["`\']', t)))
        print(f"\n## {j}: {len(t)} chars"); [print("   ", x) for x in hits[:150]]
    report()


def probe3():
    t = open_url("https://results.sos.ga.gov/cdn/results/v4/Georgia.json").decode("utf-8", "replace")
    print(len(t), t[:1500])
    try:
        j = json.loads(t)
        items = j if isinstance(j, list) else next((v for v in j.values() if isinstance(v, list)), [])
        for e in items[:80]: print("  ", {k: e[k] for k in list(e)[:8]} if isinstance(e, dict) else e)
    except Exception as e: print("json", e)
    report()


def probe4():
    base = "https://results.sos.ga.gov/results/public/"
    t = open_url(base + "main-E6MCELDW.js").decode("utf-8", "replace")
    for key in ("/v4/", "mediaExportPath", "baseUrl=", "/cdn/results", "/results/public/api", "elections/${a}/${p}`", "authority"):
        for m in list(re.finditer(re.escape(key), t))[:4]:
            print(f"## {key} @{m.start()}: {t[max(0, m.start() - 350): m.start() + 250]}\n")
    report()


def probe5():
    for u in ("https://www.newtoncountyga.gov/174/Election-Results", "https://floydcountyga.gov/elections/election_results.php"):
        try:
            t = open_url(u).decode("utf-8", "replace")
            print(u, sorted(set(re.findall(r"results\.sos\.ga\.gov[^\"'<> ]+", t)))[:40])
        except Exception as e: print(u, e)
        time.sleep(3)
    report()


API = "https://results.sos.ga.gov/results/public/api/elections/Georgia/"
CDN = "https://results.sos.ga.gov/cdn/results/"


def probe6():
    for eid in ("GeneralPrimary51926", "GeneralPrimaryRunoff61626", "GeneralRunoff61626", "PrimaryRunoff61626", "Runoff61626"):
        try:
            t = open_url(API + eid).decode("utf-8", "replace"); j = json.loads(t)
            print(f"## {eid}: keys {list(j)[:30]}")
            print("   ", {k: (v if not isinstance(v, (list, dict)) else type(v).__name__ + str(len(v))) for k, v in j.items()})
        except Exception as e: print(f"## {eid}: {e}")
        time.sleep(3)
    report()


def probe7():
    t = open_url("https://results.sos.ga.gov/results/public/main-E6MCELDW.js").decode("utf-8", "replace")
    for m in sorted(set(re.findall(r"http\.(?:get|post)\(`([^`]+)`", t))): print("EP", m)
    for m in list(re.finditer(r"mediaExportPath|ballotItems|ballot-items|contests", t))[:12]:
        print("CTX", t[max(0, m.start() - 200): m.start() + 150].replace("\n", " "))
    time.sleep(2)
    j = json.loads(open_url(API + "GeneralPrimary51926"))
    for k in ("publicReportCategories", "map", "countGroups", "parties", "groupReportingStatus"): print(k, json.dumps(j.get(k))[:800])
    report()


def probe8():
    t = open_url("https://results.sos.ga.gov/results/public/main-E6MCELDW.js").decode("utf-8", "replace")
    for key in ("blobName", "environment.cdn}", "imageCdn", ".json`"):
        for m in list(re.finditer(re.escape(key), t))[:6]:
            print("CTX", key, t[max(0, m.start() - 300): m.start() + 200].replace("\n", " "))
    report()


if __name__ == "__main__":
    if "probe8" in sys.argv: probe8()
    if "probe7" in sys.argv: probe7()
    if "probe6" in sys.argv: probe6()
    if "probe5" in sys.argv: probe5()
    if "probe4" in sys.argv: probe4()
    if "probe3" in sys.argv: probe3()
    if "probe" in sys.argv: probe()
    if "probe2" in sys.argv: probe2()
