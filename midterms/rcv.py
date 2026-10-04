"""Ranked-choice voting (2026-10-04): which 2026 races use it, how other candidates' voters transfer, and the expected
FINAL-ROUND margin of a race from first-round numbers.

Races decided by an instant-runoff count in November 2026 (RCV_RACES_2026):
  * Maine - U.S. Senate and both U.S. House seats. Maine's ranked-choice law (21-A M.R.S. sec. 1 (27-C), sec. 723-A; 2016
    citizen initiative, 2018 people's veto upheld) covers primaries and FEDERAL general elections only: the Maine Constitution
    requires a plurality for governor and the legislature in the general election (Opinion of the Justices, 2017 ME 100), so the
    governor's race is first-past-the-post.
  * Alaska - U.S. Senate, U.S. House (at large) and governor: top-four open primary and a ranked-choice general election
    (Ballot Measure 2, 2020; AS 15.15.350 and 15.25.010). The 2024 repeal (Ballot Measure 2, 2024) failed.

Transfer rates come from the official round-by-round tabulations (Maine Secretary of State; Alaska Division of Elections),
downloaded once into data/raw/rcv (git-ignored) by `python -m midterms.rcv --fetch`. Only the derived table is committed:
data/static/rcv_transfers.csv (one row per eliminated candidate: party type, first-round share, and where the ballots ended up -
to the Democratic finalist, the Republican finalist, or exhausted). Ballots passed to a candidate who is later eliminated are
followed to the final round with that candidate's own transfer proportions.

    python -m midterms.rcv --fetch      # one-time download (polite, ~25 files) + parse -> data/static/rcv_transfers.csv
    python -m midterms.rcv --backtest   # leave-one-race-out backtest (official first rounds and final pre-election polls)
The one-time builder reads the PDFs with PyMuPDF and Maine's 2018 .xls with xlrd (not needed by the daily run, which only reads
data/static/rcv_rates.json).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "rcv"
OUT = ROOT / "data" / "static" / "rcv_transfers.csv"
OUT_RATES = ROOT / "data" / "static" / "rcv_rates.json"

# office, seat (state for statewide races, "ST-n" for House seats) -> source of the RCV rule
RCV_RACES_2026 = {
    ("senate", "ME"): "Maine: 21-A M.R.S. sec. 723-A (ranked choice in federal general elections)",
    ("house", "ME-1"): "Maine: 21-A M.R.S. sec. 723-A",
    ("house", "ME-2"): "Maine: 21-A M.R.S. sec. 723-A",
    ("senate", "AK"): "Alaska: AS 15.15.350 (Ballot Measure 2, 2020)",
    ("house", "AK-1"): "Alaska: AS 15.15.350",
    ("governor", "AK"): "Alaska: AS 15.15.350",
}
NOT_RCV_NOTE = "Maine governor: plurality (Maine Constitution; Opinion of the Justices, 2017 ME 100)"

# Adoption switch (default on since 2026-10-04, decided by `python -m midterms.rcv --backtest`; numbers in web/about.html#rcv).
# MIDTERMS_RCV=off restores the old handling (round tables dropped or averaged with round one, Alaska governor party sums).
ENABLED = os.environ.get("MIDTERMS_RCV", "on").lower() != "off"

AK = "https://www.elections.alaska.gov/results/"
ME = "https://www.maine.gov/sos/sites/maine.gov.sos/files/"
# (race id, source url). Alaska: every RCV report linked from the Division of Elections' 2022 / 2024 results pages (the recount
# version where one exists) and the August 2022 special general; Maine: the Secretary of State's 2nd-district RCV summaries.
TABULATIONS = [
    ("AK 2022 special US House", AK + "22SSPG/RcvDetailedReport.pdf"),
    ("AK 2022 US House", AK + "22GENR/US%20REP.pdf"),
    ("AK 2022 US Senate", AK + "22GENR/US%20SEN.pdf"),
    ("AK 2024 US House", AK + "24GENR/RCV-USRep.pdf"),
    *[(f"AK 2022 State House {d}", AK + f"22GENR/{f}.pdf") for d, f in (("11", "11"), ("15", "15_recount_rcv"), ("18", "18"), ("28", "28"),
                                                                           ("30", "30"), ("31", "31"), ("34", "34"))],
    *[(f"AK 2022 State Senate {d}", AK + f"22GENR/{f}.pdf") for d, f in (("D", "D"), ("E", "E_recount_rcv"), ("N", "N"))],
    *[(f"AK 2024 State House {d}", AK + f"24GENR/{f}.pdf") for d, f in (("6", "RCV-HD6"), ("28", "RcvDetailedReport-HD28-Recount"),
                                                                           ("36", "RCV-HD36"), ("38", "RCV-HD38"), ("40", "RCV-HD40"))],
    *[(f"AK 2024 State Senate {d}", AK + f"24GENR/RCV-Sen{d}.pdf") for d in ("D", "F", "L")],
    ("ME 2018 US House 2", ME + "content/assets/SummaryReport-CongressionalDistrict2.xls"),
    ("ME 2022 US House 2", ME + "inline-files/Rep%20to%20Congress%20Dist%202%20RCV%20results.pdf"),
]
# candidate parties: the Division of Elections' official summary reports (the RCV reports carry no party)
AK_SUMMARY = {"2022": AK + "22GENR/ElectionSummaryReportRPT.pdf", "2024": AK + "24GENR/ElectionSummaryReport.pdf"}
PARTY_FIXED = {  # not in a downloaded summary: the August 2022 special general (all three on the 2022 general summary too) and Maine
    "Begich, Nick": "REP", "Palin, Sarah": "REP", "Peltola, Mary S.": "DEM",
    "Golden, Jared F.": "DEM", "Golden, Jared Forrest": "DEM", "Poliquin, Bruce": "REP",
    "Bond, Tiffany L.": "NON", "Bond, Tiffany": "NON", "Hoar, William R.S.": "NON", "Write-in": "NON"}
# party label -> transfer type. AIP (Alaskan Independence) and Constitution run to the right of the Republicans.
TYPE = {"REP": "R", "AIP": "R", "CON": "R", "DEM": "D", "GRN": "D"}


def _type(p): return TYPE.get(str(p), "O")


def _local(url): return RAW / re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("/results/")[-1].split("/files/")[-1])


def fetch_all():
    """One-time download of the tabulations and summaries (skips files already held; 2 s between requests)."""
    from . import fetch as F
    RAW.mkdir(parents=True, exist_ok=True)
    for url in [u for _, u in TABULATIONS] + list(AK_SUMMARY.values()):
        f = _local(url)
        if f.exists() and f.stat().st_size > 1000: continue
        F.get(url, f); print("  fetched", url); time.sleep(2.0)


def _pdf_text(f):
    import pymupdf
    return "".join(p.get_text() for p in pymupdf.open(f))


def _lines(f):
    """Non-empty stripped lines; a wrapped nickname line ('"Putuuqti"') is joined to the name above it."""
    out = []
    for l in _pdf_text(f).splitlines():
        l = re.sub(r"\s+", " ", l).strip()
        if not l: continue
        if l.startswith('"') and out and "," in out[-1]: out[-1] = out[-1] + " " + l
        else: out.append(l)
    return out


def _int(s): return int(str(s).replace(",", "").strip())


def ak_parties():
    out = {}
    for y, url in AK_SUMMARY.items():
        lines = _lines(_local(url))
        for a, b in zip(lines, lines[1:]):
            if re.fullmatch(r"[A-Z]{3}", b) and "," in a: out.setdefault(y, {})[a] = b
    return out


def parse_ak(f):
    """Alaska 'RCV Detailed Report' -> (round-1 votes {cand: n}, [(eliminated, {to: ballots})], final {cand: n})."""
    L = _lines(f)
    rounds, events, cur, i = [], [], None, 0
    while i < len(L):
        l = L[i]
        if re.fullmatch(r"Round \d+", l):
            cur = {}; rounds.append(cur); i += 4; continue                     # Round n | Candidate | Votes | Percentage
        if cur is not None and l in ("Continuing Ballots Total",): cur = None
        if cur is not None and i + 2 < len(L) and re.fullmatch(r"[\d,]+", L[i + 1]) and L[i + 2].endswith("%"):
            cur[l] = _int(L[i + 1]); i += 3; continue
        m = re.match(r"Elimination transfer for candidate (.+)\.$", l)
        if m:
            e = m.group(1); tr = {}; j = i + 1
            while j < len(L) and not re.fullmatch(r"Round \d+", L[j]):
                if L[j] == e and j + 2 < len(L) and re.fullmatch(r"\d+", L[j + 2]):
                    tr[L[j + 1]] = tr.get(L[j + 1], 0) + _int(L[j + 2]); j += 3
                    if j < len(L) and re.fullmatch(r"\d+", L[j]) and j + 1 < len(L) and re.fullmatch(r"[\d,]+", L[j + 1]): j += 2   # Fraction | Votes (2024)
                    elif j < len(L) and re.fullmatch(r"[\d,]+", L[j]): j += 1                                                   # Votes (2022)
                    continue
                j += 1
            events.append((e, tr)); i = j; continue
        i += 1
    return rounds[0], events, {k: v for k, v in rounds[-1].items() if v > 0}


def parse_me(f):
    """Maine SOS RCV summary (2018 .xls, 2022 .pdf): round 1 and the round-1 transfer column -> one batch elimination."""
    if f.suffix == ".xls":
        x = pd.read_excel(f, header=None)
        rows = {}
        for _, r in x.iterrows():
            n = str(r[0]).strip()
            if re.match(r"^(DEM |REP )?[A-Z][a-z]+, ", n) and pd.notna(r[1]):
                rows[re.sub(r"^(DEM|REP) ", "", n)] = (int(r[1]), int(r[3]), int(r[4]))
        r1 = {k: v[0] for k, v in rows.items()}; fin = {k: v[2] for k, v in rows.items() if v[2] > 0}
    else:
        L = _lines(f)
        r1, fin = {}, {}
        for i, l in enumerate(L):
            if (re.match(r"^[A-Z][a-z]+, ", l) or l == "Write-in") and i + 4 < len(L) and re.fullmatch(r"\d+", L[i + 1]):
                r1[l] = int(L[i + 1]); v2 = int(L[i + 4])
                if v2 > 0: fin[l] = v2
    elim = [k for k in r1 if k not in fin]
    pile = sum(r1[k] for k in elim)
    tr = {k: fin[k] - r1[k] for k in fin}; tr["Exhausted"] = pile - sum(tr.values())
    return r1, [(" + ".join(elim), tr)], fin, elim


def tabulate():
    """Every eliminated candidate's ballots followed to the final round -> one row per eliminated candidate."""
    parties = ak_parties(); rows = []
    for rid, url in TABULATIONS:
        f = _local(url); yr = rid.split()[1]
        pmap = {**PARTY_FIXED, **parties.get(yr, {})}
        if rid.startswith("ME"):
            r1, events, fin, elim = parse_me(f)
            party = {k: pmap.get(k, "NON") for k in r1}
            party[events[0][0]] = "NON"                      # Maine's batch elimination: independents (and write-ins)
        else:
            r1, events, fin = parse_ak(f); party = {k: pmap.get(k, "?") for k in r1}
        assert len(fin) == 2, (rid, fin)
        tot = sum(r1.values())
        # eventual destination of each eliminated pile: transfers to a later-eliminated candidate follow that candidate's split
        dest = {}
        for e, tr in reversed(events):
            n = sum(tr.values()); d = {"A": 0.0, "B": 0.0, "X": 0.0}; fa, fb = sorted(fin)
            for to, v in tr.items():
                if to == fa: d["A"] += v / n
                elif to == fb: d["B"] += v / n
                elif to in dest: d = {k: d[k] + v / n * dest[to][k] for k in d}
                else: d["X"] += v / n                        # Exhausted, Overvotes
            dest[e] = d
        fa, fb = sorted(fin); pa, pb = _type(party.get(fa)), _type(party.get(fb))
        kind = "DR" if {pa, pb} == {"D", "R"} else ("OR" if {pa, pb} == {"O", "R"} else pa + pb)
        for e, _ in events:
            share = (r1.get(e) or sum(r1[k] for k in e.split(" + "))) / tot * 100
            d = dest[e]
            to_r = (d["A"] if pa == "R" else d["B"]) if kind in ("DR", "OR") else np.nan
            to_c = d["A"] if (pa != "R" and pb == "R") else d["B"] if (pb != "R" and pa == "R") else np.nan   # the non-Republican finalist
            rows.append({"race": rid, "final": f"{fa} ({party.get(fa)}) v {fb} ({party.get(fb)})", "final_kind": kind,
                         "eliminated": e, "party": party.get(e, "NON"), "type": _type(party.get(e, "NON")) if " + " not in e else "O",
                         "r1_share": round(share, 2), "to_nonrep": round(to_c, 4) if to_c == to_c else np.nan,
                         "to_rep": round(to_r, 4) if to_r == to_r else np.nan, "exhausted": round(d["X"], 4),
                         "r1_rep": round(sum(v for k, v in r1.items() if k in fin and _type(party.get(k)) == "R") / tot * 100, 2),
                         "r1_nonrep": round(sum(v for k, v in r1.items() if k in fin and _type(party.get(k)) != "R") / tot * 100, 2),
                         "final_margin": round((sum(v for k, v in fin.items() if _type(party.get(k)) != "R") - sum(v for k, v in fin.items() if _type(party.get(k)) == "R")) / tot * 100, 2) if kind in ("DR", "OR") else np.nan})
    return pd.DataFrame(rows)


def build():
    T = tabulate(); OUT.parent.mkdir(parents=True, exist_ok=True); T.to_csv(OUT, index=False)
    print(f"  {len(T)} eliminated candidates in {T.race.nunique()} tabulations -> {OUT.relative_to(ROOT)}")
    return T


# ---------------------------------------------------------------------------------------------------------------------------
# Transfer rates. A ballot of an eliminated candidate ends with the non-Republican finalist, the Republican finalist, or exhausted.
# What matters for the margin is the NET share, to_nonrep - to_rep, by the eliminated candidate's type:
#   partisan (R-leaning or D-leaning): one loyalty parameter LAMBDA, toward the eliminated candidate's own side (R-type: -LAMBDA,
#     D-type: +LAMBDA) - pooled because there are only two D-type eliminations in a D-v-R final;
#   independent / other: its own net share (sign free).
# Only finals with a Republican against a Democrat or an independent count (Republican-v-Republican finals say nothing about it).
# The race-to-race SPREAD of these net shares is the transfer uncertainty a race carries.

def _obs(T, exclude=None):
    T = T[T.final_kind.isin(["DR", "OR"])]
    if exclude is not None: T = T[T.race != exclude]
    T = T.assign(net=T.to_nonrep - T.to_rep)
    return T


def rates(T=None, exclude=None):
    T = pd.read_csv(OUT) if T is None else T
    o = _obs(T, exclude)
    part = o[o.type.isin(["R", "D"])]; loyal = np.where(part.type == "R", -part.net, part.net)
    oth = o[o.type == "O"]
    r = {"lambda": float(np.mean(loyal)), "lambda_sd": float(np.std(loyal, ddof=1)), "n_partisan": int(len(part)),
         "n_partisan_r": int((part.type == "R").sum()), "n_partisan_d": int((part.type == "D").sum()),
         "other": float(oth.net.mean()), "other_sd": float(oth.net.std(ddof=1)), "n_other": int(len(oth)),
         "races": int(o.race.nunique())}
    for t, g in o.groupby("type"):
        r[f"by_type_{t}"] = {"n": int(len(g)), "to_nonrep": round(float(g.to_nonrep.mean()), 3), "to_rep": round(float(g.to_rep.mean()), 3),
                             "exhausted": round(float(g.exhausted.mean()), 3), "net_sd": round(float(g.net.std(ddof=1)), 3) if len(g) > 1 else None}
    return r


def net_of(t, r):
    """(mean, race-level sd) of the net share an eliminated candidate of type t gives the non-Republican finalist."""
    if t == "R": return -r["lambda"], r["lambda_sd"]
    if t == "D": return r["lambda"], r["lambda_sd"]
    return r["other"], r["other_sd"]


def convert(nonrep, rep, others, r):
    """Expected final-round margin (non-Republican finalist minus Republican, points of first-round ballots) from first-round
    shares. others: [(share, type)]. sd: the transfer uncertainty - each type's share moves together within a race, at the
    spread of that type's net share across past tabulations plus the standard error of its mean."""
    m = nonrep - rep; by = {}
    for s_, t in others:
        if not (s_ > 0): continue
        mu, _ = net_of(t, r); m += s_ * mu; by[t] = by.get(t, 0.0) + s_
    var = 0.0
    for t, s_ in by.items():
        _, sd = net_of(t, r); n = r["n_partisan"] if t in "RD" else r["n_other"]
        var += (s_ * sd) ** 2 * (1 + 1 / n)
    return m, float(np.sqrt(var))


# --- poll handling ------------------------------------------------------------------------------------------------------------
ROUND_KEY = re.compile(r"^(RCV (round|count)|Round)\b", re.I)


def party_type_from_label(key, ballot_party=None):
    """'Dan J. Sullivan (R)' -> 'R'; ballot party ('Republican', 'Independent', ...) wins when given."""
    if ballot_party: return {"Republican": "R", "Democratic": "D", "DFL": "D", "Alaskan Independence": "R", "Constitution": "R",
                             "Green": "D"}.get(ballot_party, "O")
    m = re.search(r"\((R|D|AIP|C|G|I|L|NPA|Ind\w*|Lib\w*)\)", str(key))
    return {"R": "R", "AIP": "R", "C": "R", "D": "D", "G": "D"}.get(m.group(1), "O") if m else "O"


def survey(versions, type_of, r, nonrep=None, rep=None, other_type="O"):
    """One survey (all its posted versions) in a ranked-choice race -> dict(margin, sd, how, first_margin, mixed_margin).
    versions: [{"cands": {name: pct}, "other_col": pct or nan, "round": int or None}], names WITHOUT the round column.
    type_of(name) -> 'R' / 'D' / 'O'. nonrep / rep: the finalists (default: the top non-Republican and the top Republican of the
    full-field version). other_type: the type of the ballot candidates the poll did not name (its 'Other' share transfers like
    them); None = nobody else is on the ballot, the 'Other' share is not a candidate and does not transfer.
      * a FINAL-ROUND version - the last round of the poll's own RCV table, or a version naming only the two finalists with no
        'Other' share (a head-to-head) - is used as reported (its versions, e.g. LV / RV, averaged), never mixed with round one;
      * otherwise each full-field first-round version is converted with the transfer rates (convert()) and the versions averaged.
    how: 'final', 'transfer' (shares were transferred) or 'first' (nothing to transfer). first_margin = the first-round versions'
    top non-R minus top R (no transfers); mixed_margin = the mean over all versions (the pipeline before: rounds averaged)."""
    full = max(versions, key=lambda v: (len(v["cands"]), -(v.get("round") or 0)))
    fc = full["cands"]
    if nonrep is None:
        nr = [k for k in fc if type_of(k) != "R"]; rr = [k for k in fc if type_of(k) == "R"]
        if not nr or not rr: return None
        nonrep, rep = max(nr, key=fc.get), max(rr, key=fc.get)
    def two(v):
        c = v["cands"]; return c[nonrep] - c[rep] if nonrep in c and rep in c else np.nan
    maxround = max([v.get("round") or 0 for v in versions])
    finals, firsts = [], []
    for v in versions:
        c = v["cands"]
        if nonrep not in c or rep not in c: continue
        oc = v.get("other_col", np.nan); oc = oc if oc == oc else 0.0
        explicit = v.get("round") is not None and v["round"] == maxround and maxround > 1 and set(c) == {nonrep, rep}
        h2h = v.get("round") is None and set(c) == {nonrep, rep} and not (oc > 0 and other_type is not None)
        if explicit or h2h: finals.append(two(v))
        elif (v.get("round") or 1) == 1: firsts.append(v)
    ms = [two(v) for v in versions]; mixed = float(np.nanmean(ms)) if any(x == x for x in ms) else np.nan
    first = float(np.mean([two(v) for v in firsts])) if firsts else np.nan
    if finals:
        return {"margin": float(np.mean(finals)), "sd": 0.0, "how": "final", "first_margin": first, "mixed_margin": mixed}
    if not firsts: return None
    conv, moved = [], 0.0
    for v in firsts:
        c = v["cands"]; others = [(x, type_of(k)) for k, x in c.items() if k not in (nonrep, rep)]
        oc = v.get("other_col", np.nan)
        if other_type is not None and oc == oc and oc > 0: others.append((oc, other_type))
        conv.append(convert(c[nonrep], c[rep], others, r)); moved += sum(x for x, _ in others)
    return {"margin": float(np.mean([m for m, _ in conv])), "sd": float(np.mean([s_ for _, s_ in conv])),
            "how": "transfer" if moved > 0 else "first", "first_margin": first, "mixed_margin": mixed}


def split_rows(rows):
    """poll_tables rows -> {(pollster, end_date): [version dicts with 'round']}"""
    out = {}
    for p in rows:
        c = dict(p["cands"]); rnd = None
        for k in list(c):
            if ROUND_KEY.match(k): rnd = int(c.pop(k))
        out.setdefault((p["pollster"], p["end_date"]), []).append({"cands": c, "round": rnd, "other_col": p.get("other_col", np.nan),
                                                                    "n": p.get("n"), "table": p.get("table")})
    return out


# --- live use (wiki_polls.senate_polls, gov2026.race_polls, senate2026 / gov2026 run) ---------------------------------------------
_RATES = None


def current_rates():
    """The committed rates (data/static/rcv_rates.json, written by --build from all tabulations)."""
    global _RATES
    if _RATES is None: _RATES = json.loads(OUT_RATES.read_text())
    return _RATES


def applies(office, seat):
    return ENABLED and (office, seat) in RCV_RACES_2026


NOTE = "final-round estimate, ranked-choice"


def house_note(seat_row):
    """The House model is Democrat v Republican: with only those two on the ballot the head-to-head IS the final round; with more,
    say so (AK-1 2026: Begich (R), Hill (I), Hafner (D), McDermott (L) - the likely final round is Republican v independent)."""
    n = len(re.findall(r"\u258c", str(seat_row.get("cands", ""))))
    return NOTE if n <= 2 else "ranked-choice count with more than two candidates; the House model scores the Democrat v the Republican only"


def wiki_surveys(keep, ch, rep_n, party, state, special, meta):
    """wiki_polls.senate_polls rows of one ranked-choice race -> one row per survey (pollster, end date): the final-round version when
    the poll reports one (its last RCV round, or a head-to-head of the two finalists), else the full-field first round converted with
    the transfer rates. Columns as senate_polls plus rcv ('final' / 'transfer') and rcv_sd (transfer uncertainty, points)."""
    r = current_rates(); groups = {}
    for p, c in keep: groups.setdefault((p["pollster"], p["end_date"]), []).append((p, c))
    ty = lambda n: party_type_from_label(n, party.get(n))
    out = []
    for (pol, end), g in groups.items():
        if not any(ch in c and rep_n in c for _, c in g): meta["dropped_partial"] += 1; continue
        vs = [{"cands": c, "round": p.get("round"), "other_col": p.get("other_col", np.nan)} for p, c in g]
        named = set().union(*[set(v["cands"]) for v in vs])
        unnamed = {ty(n) for n in party if n not in named}
        sv = survey(vs, ty, r, nonrep=ch, rep=rep_n, other_type=(unnamed.pop() if len(unnamed) == 1 else "O") if unnamed else None)
        if sv is None: continue
        ocv = lambda v: v["other_col"] if v["other_col"] == v["other_col"] else 0.0
        if sv["how"] == "final":
            use = [v for v in vs if set(v["cands"]) == {ch, rep_n} and (v.get("round") or 0) != 1] or [v for v in vs if set(v["cands"]) == {ch, rep_n}]
            sh = 0.0
        else:
            use = [v for v in vs if (v.get("round") or 1) == 1 and ch in v["cands"] and rep_n in v["cands"]]
            sh = (sv["margin"] - sv["first_margin"]) / 2
        d_ = float(np.mean([v["cands"][ch] for v in use])) + sh; r_ = float(np.mean([v["cands"][rep_n] for v in use])) - sh
        oth = float(np.mean([sum(x for n, x in v["cands"].items() if n not in (ch, rep_n)) + ocv(v) for v in use]))
        und = float(np.mean([max(0.0, 100 - sum(v["cands"].values()) - ocv(v)) for v in use]))
        out.append({"state": state, "special": special, "pollster": pol, "end_date": end, "n": g[0][0]["n"], "dem": d_, "rep": r_,
                    "margin": sv["margin"], "dem_name": f"{ch} ({party[ch][0]})", "rep_name": f"{rep_n} (R)", "table": g[0][0]["table"],
                    "und": und, "other": oth if sv["how"] != "final" else 0.0, "rcv": sv["how"], "rcv_sd": sv["sd"]})
    return out


def votehub_answers(answers, ptype):
    """A VoteHub poll entry of a ranked-choice race -> (dem, rep, margin, dem_name, rep_name, other, how, sd) or None.
    answers: [(choice, pct)]; ptype(choice) -> 'D' / 'R' / 'O' (None = not on the ballot). An entry naming only one D and one R is
    a final-round (head-to-head) version; a full field is converted with the transfer rates."""
    a = [(c, float(p or 0), ptype(c)) for c, p in answers]
    if any(t is None for _, _, t in a): return None
    ds = [x for x in a if x[2] != "R"]; rs = [x for x in a if x[2] == "R"]
    if not ds or not rs: return None
    d, rp = max(ds, key=lambda x: x[1]), max(rs, key=lambda x: x[1])
    others = [(p, t) for c, p, t in a if c not in (d[0], rp[0])]
    if not others: return d[1], rp[1], d[1] - rp[1], d[0], rp[0], 0.0, "final", 0.0
    m, sd = convert(d[1], rp[1], others, current_rates()); sh = (m - (d[1] - rp[1])) / 2
    return d[1] + sh, rp[1] - sh, m, d[0], rp[0], sum(p for p, _ in others), "transfer", sd


def other_type_of(ballot, finalists):
    """Type of the ballot's non-finalist candidates when they share one (Alaska governor 2026: Bronson and Taylor, both R), else
    'O'; None when nobody else is on the ballot. ballot: [(name, party)]."""
    fs = {re.sub(r"[^a-z]", "", str(f).split()[-1].lower()) for f in finalists if f}
    t = {party_type_from_label(n, p) for n, p in ballot if n and re.sub(r"[^a-z]", "", n.split()[-1].lower()) not in fs}
    return (t.pop() if len(t) == 1 else "O") if t else None


def convert_rows(q, other_type):
    """Feed / hand-entered rows (no candidate split, only an 'other' share) of a ranked-choice race: a row with an 'other' share is a
    first round and is converted, assuming the others transfer like the ballot's other candidates (other_type); one without is the
    final round (head-to-head) as published."""
    if q is None or not len(q): return q
    q = q.copy()
    if "rcv" not in q: q["rcv"] = None
    if "rcv_sd" not in q: q["rcv_sd"] = np.nan
    for i, r_ in q.iterrows():
        if isinstance(r_["rcv"], str) and r_["rcv"]: continue
        o = r_.get("other", np.nan); o = 0.0 if o != o else float(o)
        if o > 0 and other_type is not None:
            m, sd = convert(r_["dem"], r_["rep"], [(o, other_type)], current_rates()); sh = (m - (r_["dem"] - r_["rep"])) / 2
            q.at[i, "dem"] = r_["dem"] + sh; q.at[i, "rep"] = r_["rep"] - sh; q.at[i, "margin"] = m; q.at[i, "rcv"] = "transfer"; q.at[i, "rcv_sd"] = sd
        else:
            q.at[i, "rcv"] = "final"; q.at[i, "rcv_sd"] = 0.0
    return q


def prefer_final(df, keys):
    """Within one survey (keys), a final-round version replaces the converted first-round versions (never averaged with them)."""
    if "rcv" not in df or not len(df): return df
    fin = df.groupby(keys, dropna=False)["rcv"].transform(lambda x: (x == "final").any())
    drop = fin & (df["rcv"] == "transfer")
    if drop.any(): print(f"  ranked-choice: {int(drop.sum())} first-round version(s) dropped for the same survey's final round")
    return df[~drop]


def race_sd(p, asof=None, window=None):
    """The race's transfer uncertainty: the mean rcv_sd of its polls in the averaging window (final-round polls count 0)."""
    if p is None or not len(p) or "rcv_sd" not in p: return 0.0
    from . import model as M
    q = p
    if asof is not None:
        a = pd.Timestamp(asof); w = M.POLL_WINDOW if window is None else window
        q = p[(p["end_date"] <= a) & (p["end_date"] > a - pd.Timedelta(days=w))]
    return float(q["rcv_sd"].fillna(0.0).mean()) if len(q) else 0.0


# --- backtest -----------------------------------------------------------------------------------------------------------------
# Past ranked-choice races with a Republican-v-non-Republican final round that had public polls: Wikipedia race pages (the
# district section for Maine), the polls of the final 30 days. Target: the official final-round margin, points of first-round ballots.
POLL_RACES = [
    ("ME 2018 US House 2", "2018 United States House of Representatives elections in Maine", 2, "2018-11-06", ("Jared Golden", "Bruce Poliquin")),
    ("ME 2022 US House 2", "2022 United States House of Representatives elections in Maine", 2, "2022-11-08", ("Jared Golden", "Bruce Poliquin")),
    ("AK 2022 special US House", "2022 Alaska's at-large congressional district special election", None, "2022-08-16", ("Mary Peltola", "Sarah Palin")),
    ("AK 2022 US House", "2022 United States House of Representatives election in Alaska", None, "2022-11-08", ("Mary Peltola", "Sarah Palin")),
    ("AK 2024 US House", "2024 United States House of Representatives election in Alaska", None, "2024-11-05", ("Mary Peltola", "Nick Begich")),
]


def _strip(k): return re.sub(r"\s*\(.*?\)|\[.*?\]", "", str(k)).strip()


def backtest_results(T=None):
    """Official first rounds -> predicted final-round margin, leave-one-race-out rates. (a1) top two only, (a2) party sums
    (all D-type minus all R-type, independents dropped - the Alaska governor rule), (b) transfers."""
    T = pd.read_csv(OUT) if T is None else T; out = []
    for race, g in _obs(T).groupby("race", sort=False):
        r = rates(T, exclude=race); g0 = g.iloc[0]
        a1 = g0.r1_nonrep - g0.r1_rep
        a2 = a1 + g[g.type == "D"].r1_share.sum() - g[g.type == "R"].r1_share.sum()
        b, sd = convert(g0.r1_nonrep, g0.r1_rep, list(zip(g.r1_share, g.type)), r)
        out.append({"race": race, "kind": g0.final_kind, "eliminated_share": round(g.r1_share.sum(), 1), "actual": g0.final_margin,
                    "a1_top_two": round(a1, 2), "a2_party_sum": round(a2, 2), "b_transfer": round(b, 2), "b_sd": round(sd, 2)})
    return pd.DataFrame(out)


def backtest_polls(T=None, window=30):
    from . import wiki_polls as W
    T = pd.read_csv(OUT) if T is None else T; out = []
    act = T.groupby("race").final_margin.first()
    for race, title, district, eday, (nr, rp) in POLL_RACES:
        r = rates(T, exclude=race); yr = int(eday[:4])
        h = W.fetch(title, max_age_h=10 ** 6)
        if district: h = dict(W._district_chunks(h))[district]; h = "<table" + h.split("<table", 1)[1]
        rows = W.poll_tables(h, yr)
        e = pd.Timestamp(eday)
        rows = [p for p in rows if e - pd.Timedelta(days=window) <= p["end_date"] <= e]
        for (pol, end), vs in split_rows(rows).items():
            vs = [{**v, "cands": {_strip(k): x for k, x in v["cands"].items()}} for v in vs]
            names = set().union(*[set(v["cands"]) for v in vs])
            if nr not in names or not any(rp in v["cands"] for v in vs): continue      # a different race (primary) on the page
            ty = {}
            for v, raw in zip(vs, split_rows([p for p in rows if p["pollster"] == pol and p["end_date"] == end]).get((pol, end), [])):
                for k in raw["cands"]: ty[_strip(k)] = party_type_from_label(k)
            # the finalists: the actual non-Republican finalist and the poll's own top Republican (in the full-field version)
            full = max(vs, key=lambda v: len(v["cands"]))["cands"]
            rr = [k for k in full if ty.get(k) == "R"]
            if not rr: continue
            s = survey(vs, lambda k: ty.get(k, "O"), r, nonrep=nr, rep=max(rr, key=full.get))
            if s is None: continue
            full_ty = {k: ty.get(k, "O") for k in full}
            a2 = sum(v for k, v in full.items() if full_ty[k] != "R" and (full_ty[k] == "D" or k == nr)) - sum(v for k, v in full.items() if full_ty[k] == "R")
            out.append({"race": race, "pollster": pol, "end": end.date(), "how": s["how"], "actual": act[race],
                        "a_mixed": s["mixed_margin"], "a1_first": s["first_margin"], "a2_party_sum": a2, "b": s["margin"], "b_sd": s["sd"]})
    return pd.DataFrame(out)


def _paired(d, a, b):
    """mean |error| of a and b, mean difference of |error| (a - b) and its t statistic / exact sign-test p."""
    from math import comb
    ea, eb = (d[a] - d.actual).abs(), (d[b] - d.actual).abs(); diff = ea - eb
    t = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 1 and diff.std(ddof=1) > 0 else np.nan
    k, n = int((diff > 0).sum()), int((diff != 0).sum())
    p = sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else np.nan
    return {"n": len(d), "mae_" + a: round(ea.mean(), 2), "mae_" + b: round(eb.mean(), 2), "rmse_" + a: round(float(np.sqrt((ea ** 2).mean())), 2),
            "rmse_" + b: round(float(np.sqrt((eb ** 2).mean())), 2), "mean_gain": round(diff.mean(), 2), "t": round(float(t), 2),
            "b_better": f"{k}/{n}", "sign_p_one_sided": round(p, 3)}


def backtest():
    T = pd.read_csv(OUT)
    R = backtest_results(T)
    print("official first round -> final round (points of first-round ballots), leave-one-race-out transfer rates:")
    print(R.to_string(index=False))
    for a in ("a1_top_two", "a2_party_sum"): print("  b_transfer vs", a, _paired(R, a, "b_transfer"))
    z = (R.b_transfer - R.actual) / R.b_sd
    print(f"  transfer sd calibration: rms z {np.sqrt((z ** 2).mean()):.2f} (1 = right), |z| > 1 in {(z.abs() > 1).sum()}/{len(z)} races")
    R.to_csv(ROOT / "data" / "static" / "rcv_backtest_results.csv", index=False)
    P = backtest_polls(T)
    print("\nfinal 30 days of polls (Wikipedia), leave-one-race-out rates:")
    print(P.round(2).to_string(index=False))
    pr = P.groupby("race", sort=False).agg(actual=("actual", "first"), n=("b", "size"), a_mixed=("a_mixed", "mean"), a1_first=("a1_first", "mean"),
                                           a2_party_sum=("a2_party_sum", "mean"), b=("b", "mean")).reset_index()
    print("\nper race (poll means):"); print(pr.round(2).to_string(index=False))
    P.round(2).to_csv(ROOT / "data" / "static" / "rcv_backtest_polls.csv", index=False)
    for a in ("a_mixed", "a1_first", "a2_party_sum"):
        print("  polls, b vs", a, "| per poll", _paired(P.dropna(subset=[a]), a, "b"), "| per race", _paired(pr.dropna(subset=[a]), a, "b"))
    return R, P, pr


def write_rates():
    r = rates(); r["source"] = ("Maine Secretary of State and Alaska Division of Elections ranked-choice tabulations 2018-2024 "
                                "(midterms/rcv.py; data/static/rcv_transfers.csv)")
    OUT_RATES.write_text(json.dumps(r, indent=1)); return r


if __name__ == "__main__":
    if "--fetch" in sys.argv: fetch_all(); build(); print(write_rates())
    if "--build" in sys.argv: print(build().to_string()); print(write_rates())
    if "--backtest" in sys.argv: backtest()
