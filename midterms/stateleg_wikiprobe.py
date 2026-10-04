"""One-off: print the structure of the 2026 state-legislative chamber pages (headings, table headers, sample rows) so the
candidate parser can be written against them. Actions only.   python -m midterms.stateleg_wikiprobe"""
import re
from io import StringIO
import pandas as pd
from . import wiki_polls as W
from .fetch import report

STATES = {"MI": "Michigan", "MN": "Minnesota", "WI": "Wisconsin", "AZ": "Arizona", "PA": "Pennsylvania", "NH": "New Hampshire", "NC": "North Carolina"}
LOWER = {"WI": "State Assembly"}


def titles():
    for st, name in STATES.items():
        yield f"2026 {name} Senate election"
        yield f"2026 {name} {LOWER.get(st, 'House of Representatives')} election"


def main():
    for t in titles():
        try: html = W.fetch(t)
        except Exception as e: print(f"\n#### {t}: FAILED {e}"); continue
        print(f"\n#### {t}: {len(html)} chars")
        heads = re.findall(r'<h([234])[^>]*id="([^"]+)"', html)
        print("  headings:", [f"{l}:{h}" for l, h in heads][:80])
        try: tabs = pd.read_html(StringIO(html))
        except Exception as e: tabs = []; print("  read_html failed", e)
        for i, tb in enumerate(tabs):
            cols = [" | ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in tb.columns]
            print(f"  table {i}: {tb.shape} cols {cols[:12]}")
            if tb.shape[0] >= 5 or i < 6: print("    " + tb.head(4).to_string(max_colwidth=60).replace("\n", "\n    ")[:1600])
    report()


if __name__ == "__main__":
    main()
