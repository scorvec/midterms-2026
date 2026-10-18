"""Clark County, Nevada: 2026 mail and in-person early ballots by party against 2022 and 2024. Diagnostic only.

Nevada's Secretary of State publishes the statewide counts by party, but its site turns away automated requests; Clark
County's Election Department (about 70 % of the state's voters) publishes its own files, open to crawlers:
  * the mail ballot return file, rebuilt each night: one row per returned ballot with the voter's registered party and the
    day it came back (VOTER_REG_PARTY, BALLOT_RECEIVE_DATE; RETURN_CODE "UND" = returned undeliverable, not a ballot;
    "SNP" = signature missing, counted but awaiting cure). The 2022 and 2024 general files keep every ballot's day, so the
    same days out are read from them;
  * the in-person early vote file, one per day of early voting (posted the next day), one row per voter. A general
    election's file carries no party, so it is joined on the voter id to the county's active voter list (PARTY_REG,
    rebuilt weekly). Voters the list does not hold yet (registered since it was written) count as unmatched;
  * 2024's in-person file, for the turnout by day of early voting (2024's own party at the time is not published, so it is
    compared on totals only).
  * all of it by congressional district, through the voter list's CONGRESS field: NV-1, NV-3 and Clark's part of NV-4, each
    against its own active voters.
NV mails a ballot to every active voter, so the mail read against registration is the party-intensity read (as Florida's
"vs ballots sent"). Only counts leave the runner: the voter-level files stay in data/raw/nv_clark (Actions cache), the
2022 / 2024 day-by-day counts are reduced once into data/static/nv_clark_baseline.csv.

    python -m midterms.nv_clark_early        # fetch (conditional), compare, write data/early/nv_clark_early.json
"""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "nv_clark"  # voter-level downloads (actions cache only, never committed)
EARLY = ROOT / "data" / "early"
OUT = EARLY / "nv_clark_early.json"
BASELINE = ROOT / "data" / "static" / "nv_clark_baseline.csv"
HOST = "https://elections.clarkcountynv.gov/"
MAIL = {
    2026: HOST + "VoterRequestsTV/EVMB/mbreq26GE.zip",
    2024: HOST + "VoterRequestsTV/EVMB/mbreq24G.zip",
    2022: HOST + "VoterRequests/EVMB/mbreq22G.zip",
}
EV_DAY = HOST + "VoterRequestsTV/EVMB/ev_{:%Y%m%d}.zip"
EV_2024 = HOST + "VoterRequestsTV/EVMB/ev_24G.zip"
VOTERS = HOST + "VoterRequestsTV/COUNTY_active_TV.zip"
ELECTION_DAY = {2026: dt.date(2026, 11, 3), 2024: dt.date(2024, 11, 5), 2022: dt.date(2022, 11, 8)}
EV_WINDOW = (dt.date(2026, 10, 17), dt.date(2026, 10, 30))
PARTIES = ("DEM", "REP", "NP", "OTH")
BATCH_SHARE = 0.05  # a day holding 5 % of the last month's returns opens the matched comparison
# the county's file layouts (TechDocs: evlayout.pdf, mblayout.pdf, voterlistfilelayout.pdf), used when a file has no header
EV_COLS = ["IDNUMBER", "NAME", "PRECINCT", "PARTY", "PARTY ABBR", "CONGRESS", "ASSEMBLY", "SENATE", "COMMISSION",
           "EDUCATION", "REGENT", "SCHOOL", "WARD", "TOWNSHIP", "STATUS", "EV SITE", "ELECTION CODE", "ACTIVITY DATE"]  # fmt: skip
MAIL_COLS = ["IDNUMBER", "VOTER_NAME", "STREETNUMBER", "STREETPREDIR", "STREETNAME", "STREETTYPE", "UNIT", "CITY", "STATE",
             "ZIP", "PRECINCT", "VOTER_REG_PARTY", "BALLOT_PARTY", "ELECTION_CODE", "REQUEST_SOURCE", "REQUEST_DATE",
             "BALLOT_MAIL_DATE", "BALLOT_RECEIVE_DATE", "RETURN_CODE"]  # fmt: skip
VOTER_COLS = [
    "STATUS",
    "PRECINCT_NUM",
    "CONGRESS",
    "ASSEMBLY",
    "SENATE",
    "COMMISSION",
    "EDUCATION",
    "REGENT",
    "SCHOOL",
    "CITY",
    "WARD",
    "TOWNSHIP",
    "FIRST_NAME",
    "MIDDLE_NAME",
    "LAST_NAME",
    "NAME_SUFFIX",
    "SEX",
    "PARTY_REG",
    "BIRTH_YEAR",
    "PHONE_NUM",
    "RES_STREET_NUM",
    "RES_DIRECTION",
    "RES_STREET_NAME",
    "RES_ADDRESS_TYPE",
    "RES_UNIT",
    "RES_CITY",
    "RES_STATE",
    "RES_ZIP_CODE",
    "MAIL_ADDRESS",
    "MAIL_CITY",
    "MAIL_STATE",
    "MAIL_ZIP_CODE",
    "ACTIVITY_DATE",
    "REGISTRATION_DATE",
    "REGISTRATION_NUM",
    "LANGUAGE_PREF",
    "POLLING_CODE",
    "CONFIDENTIAL_FLAG",
    "ID_NOT_REQD",
    "AFFIDAVIT",
]
VOTER_COLS += [f"{k}{i}" for i in range(1, 21) for k in ("ELECTION", "VOTE_TYPE")]  # fmt: skip


def party(s: pd.Series) -> pd.Series:
    """The county's party names or codes -> DEM / REP / NP (non-partisan) / OTH."""
    u = s.fillna("").astype(str).str.strip().str.upper()
    out = pd.Series("OTH", index=s.index)
    out[u.str.startswith("DEM") | (u == "D")] = "DEM"
    out[u.str.startswith("REP") | (u == "R")] = "REP"
    out[u.str.startswith("NON") | (u == "NP")] = "NP"
    return out


def table(raw: bytes, layout: list[str], cols: list[str]) -> pd.DataFrame:
    """The comma-delimited text inside one of the county's zip files, `cols` only (header row optional)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = max(z.namelist(), key=lambda n: z.getinfo(n).file_size)
        text = z.read(name)
    first = text[: text.find(b"\n")].decode("latin-1").upper()
    fields = [f.strip().strip('"').strip() for f in first.split(",")]
    head = sum(f in layout for f in fields) >= 2  # a header row names the layout's fields, a data row does not
    if not head and first.count(",") + 1 != len(layout):
        raise ValueError(f"{name}: {first.count(',') + 1} fields and no header, the layout has {len(layout)}")
    kw = {"header": 0} if head else {"header": None, "names": layout}
    want = set(cols)
    d = pd.read_csv(io.BytesIO(text), dtype=str, encoding="latin-1", usecols=lambda c: c.strip().upper() in want, **kw)
    d.columns = [c.strip().upper() for c in d.columns]
    return d


def voter_id(s: pd.Series) -> pd.Series:
    """The voter id as both files can be matched on (whitespace and leading zeros dropped)."""
    return s.fillna("").astype(str).str.strip().str.lstrip("0")


def get(url: str, name: str, min_age_h: float | None = None) -> bytes:
    from . import fetch as F

    RAW.mkdir(parents=True, exist_ok=True)
    return F.get(url, RAW / name, min_age_h=min_age_h, timeout=300)[0]


def mail_returns(raw: bytes) -> pd.DataFrame:
    """One row per returned ballot (first return per voter): voter id, party, day received, signature-missing flag."""
    d = table(raw, MAIL_COLS, ["IDNUMBER", "VOTER_REG_PARTY", "BALLOT_RECEIVE_DATE", "RETURN_CODE"])
    code = d["RETURN_CODE"].fillna("").str.strip().str.upper()
    d = d.assign(
        day=pd.to_datetime(d["BALLOT_RECEIVE_DATE"], errors="coerce", format="mixed").dt.date, snp=code == "SNP"
    )
    d = d[d["day"].notna() & (code != "UND")]
    d = d.sort_values("day").drop_duplicates("IDNUMBER")
    return pd.DataFrame(
        {
            "id": voter_id(d["IDNUMBER"]).values,
            "party": party(d["VOTER_REG_PARTY"]).values,
            "day": d["day"].values,
            "snp": d["snp"].values,
        }
    )


def district(s: pd.Series) -> pd.Series:
    """The voter list's congressional district -> "NV-1" ... (None where it carries no number)."""
    n = s.fillna("").astype(str).str.extract(r"(\d+)", expand=False)
    return n.map(lambda x: f"NV-{int(x)}" if isinstance(x, str) else None)


def by_district(p: pd.Series, cd: pd.Series) -> dict:
    """{district: party counts} of a party Series indexed by voter id, through the voter list's districts."""
    d = pd.DataFrame({"party": p.values, "cd": p.index.map(cd)}).dropna(subset=["cd"])
    return {k: counts(g["party"]) for k, g in d.groupby("cd")}


def written(meta: Path) -> dt.datetime | None:
    """When the county wrote a downloaded file (its Last-Modified, in Nevada time)."""
    from email.utils import parsedate_to_datetime
    from zoneinfo import ZoneInfo

    try:
        lm = parsedate_to_datetime(json.loads(meta.read_text())["last_modified"])
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return lm.astimezone(ZoneInfo("America/Los_Angeles"))


def complete_through(meta: Path) -> dt.date | None:
    """The last whole day a nightly file holds: written() less the 20 hours before a night's rebuild (a file written at
    23:30 holds that day; one written in the morning holds only the day before)."""
    w = written(meta)
    return (w - dt.timedelta(hours=20)).date() if w else None


def counts(p: pd.Series) -> dict:
    c = p.value_counts()
    out = {k: int(c.get(k, 0)) for k in PARTIES}
    out["total"] = sum(out.values())
    return out


def r_minus_d(c: dict) -> float | None:
    return round(100 * (c["REP"] - c["DEM"]) / c["total"], 2) if c.get("total") else None


def baseline() -> pd.DataFrame:
    """2022 / 2024 counts by day: mail returns by party, 2024 in-person voters (total). Reduced once, then read."""
    if BASELINE.exists():
        return pd.read_csv(BASELINE, parse_dates=["day"])
    rows = []
    for y in (2022, 2024):
        m = mail_returns(get(MAIL[y], f"mbreq{y}.zip"))
        for (day, pt), n in m.groupby(["day", "party"]).size().items():
            rows.append({"year": y, "kind": "mail", "day": day, "party": pt, "n": int(n)})
    ev = table(get(EV_2024, "ev_24G.zip"), EV_COLS, ["IDNUMBER", "ACTIVITY DATE"])
    ev["day"] = pd.to_datetime(ev["ACTIVITY DATE"], errors="coerce", format="mixed").dt.date
    for day, n in ev.dropna(subset=["day"]).drop_duplicates("IDNUMBER").groupby("day").size().items():
        rows.append({"year": 2024, "kind": "in_person", "day": day, "party": "ALL", "n": int(n)})
    B = pd.DataFrame(rows).sort_values(["year", "kind", "day", "party"])
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    B.to_csv(BASELINE, index=False)
    return pd.read_csv(BASELINE, parse_dates=["day"])


def at_days_out(B: pd.DataFrame, year: int, kind: str, days_out: int) -> dict | None:
    """Cumulative counts of an earlier year through the same number of days before its Election Day."""
    cut = pd.Timestamp(ELECTION_DAY[year] - dt.timedelta(days=days_out))
    b = B[(B.year == year) & (B.kind == kind) & (B.day <= cut)]
    if not len(b):
        return None
    g = b.groupby("party")["n"].sum()
    if kind == "in_person":
        return {"total": int(g.sum())}
    out = {k: int(g.get(k, 0)) for k in PARTIES}
    out["total"] = sum(out.values())
    return out


def logged_from(B: pd.DataFrame, year: int) -> int | None:
    """Days out from which an earlier year's mail returns can be matched: the county logs the ballots of the first weeks
    in one batch (2022: 38,493 on 23 Oct after a few dozen a day; 2024: 79,418 on 19 Oct), so before that day "the same
    days out" compares with almost nothing. The first day holding a BATCH_SHARE of the month before Election Day."""
    m = B[(B.year == year) & (B.kind == "mail")].groupby("day")["n"].sum()
    ed = pd.Timestamp(ELECTION_DAY[year])
    window = m[(m.index > ed - pd.Timedelta(days=31)) & (m.index < ed)]
    big = window[window >= BATCH_SHARE * window.sum()]
    return int((ed - big.index.min()).days) if len(big) else None


def run(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    voters = table(
        get(VOTERS, "voters_active.zip", min_age_h=20), VOTER_COLS, ["REGISTRATION_NUM", "PARTY_REG", "CONGRESS"]
    )
    voters = voters.dropna(subset=["REGISTRATION_NUM"]).drop_duplicates("REGISTRATION_NUM")
    reg_party = pd.Series(party(voters["PARTY_REG"]).values, index=voter_id(voters["REGISTRATION_NUM"]))
    reg_party = reg_party[~reg_party.index.duplicated()]
    reg_cd = pd.Series(district(voters["CONGRESS"]).values, index=voter_id(voters["REGISTRATION_NUM"]))
    reg_cd = reg_cd[~reg_cd.index.duplicated()]
    print("  Clark voter list: districts", voters["CONGRESS"].value_counts().head(6).to_dict(), flush=True)
    print("  Clark voter list: party values", voters["PARTY_REG"].value_counts().head(8).to_dict(), flush=True)
    reg = counts(reg_party)
    w = written(RAW / "voters_active.zip.http.json")
    reg |= {"R_minus_D_pct": r_minus_d(reg), "as_of": str(w.date() if w else today)}
    cds = by_district(reg_party, reg_cd)
    del voters

    m = mail_returns(get(MAIL[2026], "mbreq2026.zip"))
    if cut := complete_through(RAW / "mbreq2026.zip.http.json"):  # the day the file was written may be partial
        print(
            f"  Clark mail 2026: {int((m['day'] > cut).sum())} returns after {cut} (incomplete day) left out",
            flush=True,
        )
        m = m[m["day"] <= cut]
    print("  Clark mail 2026: returned", len(m), "through", m["day"].max(), flush=True)
    ev = []
    for d in pd.date_range(EV_WINDOW[0], min(EV_WINDOW[1], today - dt.timedelta(days=1))).date:
        try:  # each day's file is written once, the day after
            e = table(get(EV_DAY.format(d), f"ev_{d:%Y%m%d}.zip", min_age_h=24 * 365), EV_COLS, ["IDNUMBER"])
        except Exception as exc:
            print(f"  Clark in-person {d}: not available ({str(exc)[:60]})", flush=True)
            continue
        ev.append(e.assign(day=d))
    through = max(x for x in [m["day"].max(), *(e["day"].iloc[0] for e in ev)] if pd.notna(x))
    B = baseline()

    # each kind against the earlier years at its own last day: the mail file and the in-person files are written apart
    mail, m_last = counts(m["party"]), m["day"].max()
    days_out = (ELECTION_DAY[2026] - m_last).days
    res_mail = {"returned": mail, "R_minus_D_pct": r_minus_d(mail), "signature_missing": int(m["snp"].sum()),
                "through": str(m_last), "days_out": days_out}  # fmt: skip
    res_mail["vs_registration_pts"] = round(res_mail["R_minus_D_pct"] - reg["R_minus_D_pct"], 2)
    for y in (2024, 2022):
        start = logged_from(B, y)
        if start is not None and days_out > start:
            res_mail[f"vs_{y}_from"] = start  # matched once that year's first batch is in
            continue
        b = at_days_out(B, y, "mail", days_out)
        if b and b["total"]:
            res_mail[f"vs_{y}"] = {"returned": b, "R_minus_D_pct": r_minus_d(b),
                                   "returned_change_pct": round(100 * (mail["total"] / b["total"] - 1), 1)}  # fmt: skip
    res = {
        "note": (
            "Diagnostic only. Clark County Election Department files: mail ballots returned by registered party (2022 and "
            "2024 from their final files, by the day each ballot came back) and in-person early voters joined to the active "
            "voter list for party. NV mails a ballot to every active voter."
        ),
        "source": "https://www.clarkcountynv.gov/government/departments/elections/reports_data_maps/",
        "through": str(through),
        "days_out": (ELECTION_DAY[2026] - through).days,
        "registration": reg,
        "mail": res_mail,
    }
    if ev:
        E = pd.concat(ev, ignore_index=True)
        E["IDNUMBER"] = voter_id(E["IDNUMBER"])
        E = E.drop_duplicates("IDNUMBER")
        pt = E["IDNUMBER"].map(reg_party)
        voted = counts(pt.dropna())
        last = max(e["day"].iloc[0] for e in ev)
        ip = {"voted": voted, "unmatched": int(pt.isna().sum()), "days": len(ev), "R_minus_D_pct": r_minus_d(voted),
              "through": str(last), "days_out": (ELECTION_DAY[2026] - last).days}  # fmt: skip
        if ip["R_minus_D_pct"] is not None:
            ip["vs_registration_pts"] = round(ip["R_minus_D_pct"] - reg["R_minus_D_pct"], 2)
        b = at_days_out(B, 2024, "in_person", ip["days_out"])
        if b and b["total"]:
            n = len(E)
            ip["vs_2024"] = {
                "total_2024": b["total"],
                "total_2026": n,
                "change_pct": round(100 * (n / b["total"] - 1), 1),
            }
        both = {k: voted[k] + mail[k] for k in (*PARTIES, "total")}
        res["in_person"] = ip
        res["combined"] = {"total": both["total"], "R_minus_D_pct": r_minus_d(both)}
        print(
            f"  Clark in-person: {len(E)} voters over {len(ev)} days, {ip['unmatched']} not on the voter list",
            flush=True,
        )
    # by congressional district (NV-1 and NV-3 lie inside Clark, NV-4 only partly): each against its own rolls
    mail_cd = by_district(pd.Series(m["party"].values, index=m["id"].values), reg_cd)
    ip_cd = by_district(pd.Series(pt.values, index=E["IDNUMBER"].values).dropna(), reg_cd) if ev else {}
    res["districts"] = {}
    for k in sorted(cds):
        rg, ml, ip_k = cds[k], mail_cd.get(k), ip_cd.get(k)
        row = {"registration": rg | {"R_minus_D_pct": r_minus_d(rg)}}
        for name, c in (("mail", ml), ("in_person", ip_k)):
            if c and c["total"]:
                row[name] = c | {
                    "R_minus_D_pct": r_minus_d(c),
                    "vs_registration_pts": round(r_minus_d(c) - r_minus_d(rg), 2),
                }
        both = {q: (ml or {}).get(q, 0) + (ip_k or {}).get(q, 0) for q in (*PARTIES, "total")}
        if both["total"]:
            row["combined"] = {"total": both["total"], "R_minus_D_pct": r_minus_d(both)}
        res["districts"][k] = row
    EARLY.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, separators=(",", ":"), allow_nan=False, default=str))
    return res


def show(res: dict):
    r, m = res["registration"], res["mail"]
    print(
        f"\nClark County through {res['through']} ({res['days_out']} days out); active voters R-D {r['R_minus_D_pct']:+.2f}"
    )
    print(
        f"  mail returned {m['returned']['total']:,}: R-D {m['R_minus_D_pct']:+.2f} (vs registration {m['vs_registration_pts']:+.2f})"
    )
    for y in (2024, 2022):
        if v := m.get(f"vs_{y}"):
            print(f"    {y} same days out: {v['returned']['total']:,} returned, R-D {v['R_minus_D_pct']:+.2f}")
    if ip := res.get("in_person"):
        print(
            f"  in person {ip['voted']['total']:,} matched (+{ip['unmatched']:,} unmatched): R-D {ip['R_minus_D_pct']:+.2f}"
        )
        print(f"  mail + in person {res['combined']['total']:,}: R-D {res['combined']['R_minus_D_pct']:+.2f}")


if __name__ == "__main__":
    show(run())
    from . import fetch as _F

    _F.report()
    sys.exit(0)
