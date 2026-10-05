"""Coverage scan for adding state-legislative chambers (Actions only): for every chamber with a 2026 election, how much of the
Wikipedia 2026 chamber page the candidate parser reads, by kind of evidence, against the chamber's district count and its 2022
one-party seats (Klarner). Writes data/static/stateleg/coverage_scan.csv.
    python -m midterms.stateleg_scan
"""
from __future__ import annotations

import pandas as pd
from . import wiki_polls as W, stateleg_wiki as SW, stateleg as SLM
from .fetch import report
from .paths import STATIC

ALL = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
       "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
       "KS": "Kansas", "KY": "Kentucky", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
       "MO": "Missouri", "MT": "Montana", "NV": "Nevada", "NH": "New Hampshire", "NM": "New Mexico", "NY": "New York",
       "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
       "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
       "VT": "Vermont", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}
# no 2026 legislative general election: LA, MS, NJ, VA (odd years); NE is nonpartisan; SC Senate (2024/2028), NM Senate (2024/2028),
# KS Senate (2024/2028), MN Senate up (2022 -> 2026: 4-year term) is included
NO_UPPER = {"SC", "NM", "KS"}
LOWER_NAME = {"CA": "State Assembly", "NV": "Assembly", "NY": "State Assembly", "WI": "State Assembly", "MD": "House of Delegates",
              "WV": "House of Delegates"}
TRUSTED = ("general box", "breakdown table")       # sources in which a missing party really means no nominee


def title(st, ch):
    if ch == "upper": return f"2026 {ALL[st]} Senate election" if st != "NY" else "2026 New York State Senate election"
    return f"2026 {ALL[st]} {LOWER_NAME.get(st, 'House of Representatives')} election"


def main():
    k = SLM.klarner(); k22 = k[k["year"] == 2022]
    have = {(c[0], c[1]) for c in SLM.CHAMBERS}
    rows = []
    SW.NAMES.update(ALL); SW.LOWER_NAME.update(LOWER_NAME)
    for st in ALL:
        for ch in ("upper", "lower"):
            if ch == "upper" and st in NO_UPPER: continue
            t = title(st, ch); rec = {"state": st, "chamber": ch, "page": t, "in_model": (st, ch) in have}
            q = k22[(k22["sab"] == st) & (k22["chamber"] == ch)]
            rec["klarner22_districts"] = int(len(q)); rec["klarner22_seats"] = int(q["eseats"].sum())
            rec["klarner22_one_party"] = int(((q["n_d"] == 0) | (q["n_r"] == 0)).sum())
            rec["multi_member_2022"] = bool((q["eseats"] > 1).any())
            try: html = W.fetch(t)
            except Exception as e: rec["page_ok"] = False; rec["error"] = str(e)[:60]; rows.append(rec); continue
            rec["page_ok"] = True
            recs, summ, ret = SW.parse_page(st, ch, html)
            src = pd.Series([v.get("source", "") for v in recs.values()]).value_counts().to_dict() if recs else {}
            rec["districts_parsed"] = len(recs); rec["districts_summary"] = len(summ); rec["sources"] = src
            tr = {d: v for d, v in recs.items() if v.get("source") in TRUSTED}
            rec["districts_trusted"] = len(tr)
            rec["trusted_one_party"] = sum(1 for v in tr.values() if not any(p == "D" for p, _ in v["cands"]) or not any(p == "R" for p, _ in v["cands"]))
            rows.append(rec)
            print(f"{st} {ch}: page ok, parsed {len(recs)} ({src}), trusted {len(tr)}, summary {len(summ)}; Klarner 2022 districts "
                  f"{rec['klarner22_districts']}, one-party {rec['klarner22_one_party']}; trusted one-party {rec['trusted_one_party']}")
    o = pd.DataFrame(rows); (STATIC / "stateleg").mkdir(parents=True, exist_ok=True)
    o.to_csv(STATIC / "stateleg" / "coverage_scan.csv", index=False)
    print(o.drop(columns=["page", "sources"]).to_string(index=False))
    report()


if __name__ == "__main__" and "check" not in __import__("sys").argv:
    main()


def check():
    """Checks for the PENDING chambers: seats up (2024 rule) = the districts the page lists; every up district has a lean and
    complete trusted slates; one-party seats against 2024 (MEDSL) and 2022 (Klarner); sitting members for held seats."""
    import sys
    from .paths import CACHE
    from .cd_lean_build import mit
    mit()                                                         # the MIT presidential file (national margin), once
    W.FRESH_SINCE = None
    SW.build()                                                    # candidates for CHAMBERS + PENDING -> data/cache
    C = pd.read_csv(CACHE / "stateleg_candidates.csv", dtype={"district": str})
    S, H = SLM.seats_2026(SLM.PENDING)
    R = pd.read_csv(STATIC / "stateleg" / "medsl_results.csv.gz", dtype={"district": str}); R = R[(R.year == 2024) & ~R.special]
    k = SLM.klarner(); k22 = k[k["year"] == 2022]
    rows = []
    for st, ch, name, n, up, tie in SLM.PENDING:
        s = S[(S.state == st) & (S.chamber == ch)]; c = C[(C.state == st) & (C.chamber == ch) & (C.district != "_retirements")]
        ct = c[c.source.isin(TRUSTED)]
        up_set, page_set = set(s.district), set(c.district)
        r24 = R[(R.state == st) & (R.chamber == ch)]
        g24 = r24.groupby("district")["party"].agg(lambda x: ("D" in set(x)) and ("R" in set(x)))
        q22 = k22[(k22["sab"] == st) & (k22["chamber"] == ch)]
        hq = H[(H.state == st) & (H.chamber == ch)] if len(H) else H
        rec = {"chamber": name, "seats": n, "up_model": len(up_set), "page_districts": len(page_set), "page_trusted": len(ct),
               "up_not_on_page": sorted(up_set - page_set)[:10], "page_not_up": sorted(page_set - up_set)[:10],
               "lean_missing": int(s["lean"].isna().sum()), "one_party_2026": int(((s.n_d == 0) | (s.n_r == 0)).sum()),
               "one_party_2024": int((~g24).sum()), "contested_2024": int(g24.sum()),
               "one_party_2022": int(((q22["n_d"] == 0) | (q22["n_r"] == 0)).sum()), "districts_2022": int(len(q22)),
               "held": int(len(hq)), "held_unknown_party": int(hq["party"].isna().sum()) if len(hq) else 0,
               "fixed_D": int(((s.n_r == 0) & (s.n_d > 0)).sum()), "fixed_R": int(((s.n_d == 0) & (s.n_r > 0)).sum()),
               "neither": int(((s.n_d == 0) & (s.n_r == 0)).sum())}
        newly = [d for d in s.district if d in g24.index and not g24[d] and d in set(c.district)
                 and (c.set_index("district").loc[d, "n_dem"] or 0) > 0 and (c.set_index("district").loc[d, "n_rep"] or 0) > 0]
        if newly:
            cc = c.set_index("district")
            print(f"  {name}: {len(newly)} seats one-party in 2024 but two-party on the page, e.g. " +
                  "; ".join(f"{d}: D {cc.loc[d, 'dem']} / R {cc.loc[d, 'rep']}" for d in newly[:8]))
        rec["pass_slates"] = rec["page_trusted"] >= rec["up_model"] and not rec["up_not_on_page"] and not rec["page_not_up"]
        rows.append(rec)
    o = pd.DataFrame(rows); pd.set_option("display.width", 250)
    print(o.to_string(index=False))
    o.to_csv(STATIC / "stateleg" / "pending_check.csv", index=False)
    report()


if __name__ == "__main__" and "check" in __import__("sys").argv:
    check()
