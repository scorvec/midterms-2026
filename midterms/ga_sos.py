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


if __name__ == "__main__":
    probe() if "probe" in sys.argv else None
