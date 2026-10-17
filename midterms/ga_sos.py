"""Georgia House 2026 general-election nominees from the Georgia Secretary of State (official public records).

python -m midterms.ga_sos build      # data/static/stateleg/ga_house_nominees_2026.csv + ga_house_crosscheck.csv
"""

from __future__ import annotations
import re
import sys
import time
import json
from .fetch import report
from .paths import RAW


GA_RAW = RAW / "stateleg" / "ga_sos"  # the SOS files, kept in the raw cache


def get_cached(url, name):
    """One polite GET per file, kept in the raw cache (Actions cache) so reruns do not download it again."""
    from .stateleg_build import stream

    p = GA_RAW / name
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        print("  download", url)
        stream(url, p)
        time.sleep(3)
    return p


def elections():
    j = json.loads(
        get_cached(
            "https://results.sos.ga.gov/results/public/api/jurisdictions/Georgia", "jurisdiction_Georgia.json"
        ).read_text()
    )
    print("jurisdiction keys", list(j)[:40])
    for k, v in j.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print(k, len(v), [{kk: v[i].get(kk) for kk in list(v[i])[:6]} for i in range(min(len(v), 25))])
    return j


def structure(eid):
    p = get_cached(f"https://results.sos.ga.gov/cdn/results/Georgia/export-{eid}.json", f"export-{eid}.json")
    j = json.loads(p.read_text())
    print(eid, "top keys", {k: (type(v).__name__, len(v) if hasattr(v, "__len__") else v) for k, v in j.items()})
    for k, v in j.items():
        if isinstance(v, list) and v:
            x = v[0]
            print(" first", k, json.dumps(x)[:1500])
            hs = [b for b in v if "house" in json.dumps(b.get("name", b.get("ballotItemName", "")))[:300].lower()][:2]
            for b in hs:
                print(
                    " house item",
                    json.dumps({kk: b[kk] for kk in b if kk not in ("precinctResults", "breakdownResults")})[:2500],
                )


HOUSE = re.compile(r"State\s*House.*?(?:District|Dist\.?|HD)\s*0*(\d+)\D*?-\s*(Rep|Dem)\b", re.I)


def _norm_item(n):
    """'X - District 9/ Para la ... - Distrito 9 - Dem' (bilingual county ballots) -> 'X - District 9 - Dem'."""
    if "/" in n:
        m = re.search(r"-\s*(Dem|Rep)\s*$", n)
        n = n.split("/")[0].strip() + (f" - {m.group(1)}" if m else "")
    return re.sub(r"\s+", " ", n).strip()


def house_contests(eid):
    """{(district, party): {candidate: votes}} for State House primary contests, summed over the statewide and county results."""
    j = json.loads(
        get_cached(
            f"https://results.sos.ga.gov/cdn/results/Georgia/export-{eid}.json", f"export-{eid}.json"
        ).read_text()
    )
    items = list(j["results"].get("ballotItems", []))
    names = set()
    out = {}
    seen_state = set()
    for b in items:
        m = HOUSE.search(_norm_item(b["name"]))
        if m:
            seen_state.add(_norm_item(b["name"]))
    for src, its in [("state", items)] + [(c["name"], c.get("ballotItems", [])) for c in j.get("localResults", [])]:
        for b in its:
            names.add(b["name"])
            nm = _norm_item(b["name"])
            m = HOUSE.search(nm)
            if not m:
                continue
            if src != "state" and nm in seen_state:
                continue  # the statewide result already totals it
            key = (int(m.group(1)), "R" if m.group(2).lower() == "rep" else "D")
            d = out.setdefault(key, {})
            for o in b.get("ballotOptions", []):
                d[o["name"]] = d.get(o["name"], 0) + (o.get("voteCount") or 0)
    hn = sorted(n for n in names if "house" in n.lower())
    print(
        f"{eid}: {len(names)} distinct ballot items, {len(hn)} with 'house'; samples {hn[:6]} ... {hn[-4:]}; parsed {len(out)} State House party contests"
    )
    return out


def elections_2026():
    j = json.loads(
        get_cached(
            "https://results.sos.ga.gov/results/public/api/jurisdictions/Georgia", "jurisdiction_Georgia.json"
        ).read_text()
    )
    return sorted(
        (e["electionDate"], e["publicElectionId"], e["name"][0]["text"])
        for e in j["elections"]
        if e["electionDate"].startswith("2026")
    )


def _clean(n):
    from .names import clean_text

    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)|\bincumbent\b", "", clean_text(n), flags=re.I)).strip()


def build():
    """Nominee table: one row per district with the Democratic and Republican nominee (primary winner with a majority, or the June 16
    runoff winner; a lone candidate is the nominee). A party with no State House primary contest in a district has no nominee."""
    import pandas as pd
    from .paths import STATIC, CACHE

    E = {d: (i, n) for d, i, n in elections_2026() if "General Primary" in n and "RECOUNT" not in n}
    prim_id = E["2026-05-19"][0]
    run_id = E["2026-06-16"][0]
    P, R = house_contests(prim_id), house_contests(run_id)
    rows, issues = [], []
    for d in range(1, 181):
        rec = {"district": str(d)}
        for party in ("D", "R"):
            c = P.get((d, party), {})
            tot = sum(c.values())
            nom, how = None, "no candidate in the primary"
            if len(c) == 1:
                nom, how = next(iter(c)), "unopposed in the primary"
            elif c:
                top = max(c, key=c.get)
                if tot and c[top] / tot > 0.5:
                    nom, how = top, f"won the primary {100 * c[top] / tot:.0f} %"
                else:
                    r = R.get((d, party), {})
                    if r:
                        nom = max(r, key=r.get)
                        how = f"won the June 16 runoff {100 * r[nom] / max(sum(r.values()), 1):.0f} %"
                    else:
                        how = "primary without a majority and no runoff found"
                        issues.append((d, party, how, c))
            rec[f"{party.lower()}_nominee"] = _clean(nom) if nom else ""
            rec[f"{party.lower()}_how"] = how
            rec[f"{party.lower()}_primary_candidates"] = len(c)
        rows.append(rec)
    T = pd.DataFrame(rows)
    T["source"] = f"Georgia Secretary of State official results, {prim_id} + {run_id} (results.sos.ga.gov)"
    T["retrieved"] = pd.Timestamp.today().strftime("%Y-%m-%d")
    both = ((T.d_nominee != "") & (T.r_nominee != "")).sum()
    donly = ((T.d_nominee != "") & (T.r_nominee == "")).sum()
    ronly = ((T.d_nominee == "") & (T.r_nominee != "")).sum()
    none = ((T.d_nominee == "") & (T.r_nominee == "")).sum()
    print(
        f"GA House 2026 (SOS): contested {both}, D only {donly}, R only {ronly}, neither {none}; runoff decided "
        f"{(T.d_how.str.contains('runoff') | T.r_how.str.contains('runoff')).sum()}; unresolved {len(issues)} {issues[:5]}"
    )
    # history: one-party seats in 2022 / 2024 (MEDSL general results)
    try:
        H = pd.read_csv(STATIC / "stateleg" / "medsl_results.csv.gz", dtype={"district": str})
        H = H[(H.state == "GA") & (H.chamber == "lower") & ~H.special]
        for y, q in H.groupby("year"):
            g = q.groupby("district")["party"].agg(lambda x: ("D" in set(x), "R" in set(x)))
            print(
                f"  GA House {y} general (MEDSL): contested {sum(a and b for a, b in g)}, D only {sum(a and not b for a, b in g)}, R only {sum(b and not a for a, b in g)}"
            )
    except Exception as e:
        print("history check failed", e)
    # cross-check with the Wikipedia parse and Open States
    try:
        W = pd.read_csv(CACHE / "stateleg_candidates.csv", dtype={"district": str})
        W = W[(W.state == "GA") & (W.chamber == "lower")].set_index("district")
        O = pd.read_csv(STATIC / "stateleg" / "openstates_current.csv", dtype={"district": str})
        O = O[(O.state == "GA") & (O.chamber == "lower")]
        O["district"] = O["district"].astype(str).str.lstrip("0")
        O = O.set_index("district")

        def sur(n):
            p = [
                x
                for x in re.sub(r"[^A-Za-z' -]", " ", str(n)).split()
                if x.lower() not in ("jr", "sr", "ii", "iii", "iv") and str(n) != "nan"
            ]
            return p[-1].lower() if p else ""

        diffs = []
        for r in T.itertuples():
            w = W.loc[r.district] if r.district in W.index else None
            for party, nom in (("D", r.d_nominee), ("R", r.r_nominee)):
                wn = (
                    ""
                    if w is None or not isinstance(w.get("dem" if party == "D" else "rep"), str)
                    else w["dem" if party == "D" else "rep"]
                )
                if wn and nom and sur(wn.split(";")[0]) != sur(nom):
                    diffs.append((r.district, party, "name", nom, wn))
                if wn and not nom:
                    diffs.append((r.district, party, "wiki has a nominee, SOS none", "", wn))
            if r.district in O.index:
                h = O.loc[[r.district]]
                for _, x in h.iterrows():
                    p = "D" if str(x.party).startswith("Dem") else "R"
                    nom = r.d_nominee if p == "D" else r.r_nominee
                    if nom and sur(x["name"]) != sur(nom):
                        diffs.append((r.district, p, "sitting member is not the nominee", nom, x["name"]))
        D = pd.DataFrame(diffs, columns=["district", "party", "kind", "sos", "other"])
        print(f"cross-check: {len(D)} differences; by kind {D.kind.value_counts().to_dict()}")
        print(D.to_string(index=False)[:8000])
        D.to_csv(STATIC / "stateleg" / "ga_house_crosscheck.csv", index=False)
    except Exception as e:
        print("cross-check failed", e)
    T.to_csv(STATIC / "stateleg" / "ga_house_nominees_2026.csv", index=False)
    report()


if __name__ == "__main__":
    if "build" in sys.argv:
        build()
