"""Washoe County, Nevada: 2026 mail and in-person early ballots by registered party. Diagnostic only.

The Registrar of Voters posts cumulative files every day on its 2026 Election Reports page (open to crawlers):
  * the mail ballot file: one row per ballot mailed - Nevada mails one to every active voter - with the voter's registered
    party, the ballot's status and the day it came back, so the same file gives the active rolls by party;
  * the in-person file: one row per early in-person voter with registered party and the day voted.
The county's site keeps no earlier general's daily files (its archive holds the statements of votes cast), so Washoe is read
against its own rolls, not against 2022 at the same point. Washoe holds about 17 % of the state's active voters, all of them
in NV-2. Only counts leave the runner: the voter-level files stay in data/raw/nv_washoe (Actions cache, never committed).

    python -m midterms.nv_washoe_early      # fetch (conditional), count, write data/early/nv_washoe_early.json
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import pandas as pd

from . import nv_clark_early as CL

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "nv_washoe"
OUT = ROOT / "data" / "early" / "nv_washoe_early.json"
PAGE = "https://www.washoecounty.gov/voters/results/2026data.php"
BASE = "https://www.washoecounty.gov/voters/results/"
FILES = {
    "mail": r"resultsfiles/Mail-Ballot-Detail---Public-Consumption(\d+)\.csv",
    "in_person": r"resultsfiles/In-Person-Turnout---Public-Consumption(\d+)\.csv",
}
# mail ballot statuses that are a returned ballot ("Surrendered and Voted in Person" counts in the in-person file instead)
RETURNED = {"ACCEPTED", "RETURNED", "SIGNATURE CURE NEEDED", "CONTACT COUNTY CLERK"}
CURE = "SIGNATURE CURE NEEDED"


def latest(html: str, kind: str) -> str | None:
    """The newest cumulative file of a kind linked from the 2026 page (the files are numbered by day)."""
    found = [(int(n), m.group(0)) for m in re.finditer(FILES[kind], html) for n in [m.group(1)]]
    return max(found)[1] if found else None


def get(url: str, name: str, min_age_h: float | None = None) -> tuple[bytes, Path]:
    from . import fetch as F

    RAW.mkdir(parents=True, exist_ok=True)
    return F.get(url, RAW / name, min_age_h=min_age_h, timeout=300)[0], RAW / (name + ".http.json")


def _day(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.fillna("").str.strip().str.split(" ").str[0], format="%m/%d/%Y", errors="coerce").dt.date


def mail(raw: bytes) -> tuple[pd.Series, pd.DataFrame]:
    """(registered party of every active voter mailed a ballot, one row per returned ballot: voter, party, day, cure flag)."""
    cols = ["VOTER_ID", "VOTER_REG_PARTY", "BALLOT_STATUS", "BALLOT_RETURNED_DATE"]
    d = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="latin-1", usecols=cols)
    st = d["BALLOT_STATUS"].fillna("").str.strip().str.upper()
    d = d.assign(id=CL.voter_id(d["VOTER_ID"]), party=CL.party(d["VOTER_REG_PARTY"]), status=st)
    rolls = d[st != "CANCELED"].drop_duplicates("id").set_index("id")["party"]
    r = d[st.isin(RETURNED)].assign(day=_day(d["BALLOT_RETURNED_DATE"]), cure=st.eq(CURE)).drop_duplicates("id")
    return rolls, r[["id", "party", "day", "cure"]]


def in_person(raw: bytes) -> pd.DataFrame:
    d = pd.read_csv(
        io.BytesIO(raw), dtype=str, encoding="latin-1", usecols=["VOTER_ID", "VOTER_REG_PARTY", "DATE_VOTED"]
    )
    d = d.assign(id=CL.voter_id(d["VOTER_ID"]), party=CL.party(d["VOTER_REG_PARTY"]), day=_day(d["DATE_VOTED"]))
    return d.drop_duplicates("id")[["id", "party", "day"]]


def _cut(d: pd.DataFrame, meta: Path) -> pd.DataFrame:
    """Drop a last day the file may hold only part of (nv_clark_early.complete_through)."""
    cut = CL.complete_through(meta)
    return d[d["day"] <= cut] if cut else d


def summary(rolls: pd.Series, m: pd.DataFrame, ip: pd.DataFrame | None, as_of: str) -> dict:
    """The counts by party, Rep - Dem shares and their gap to the rolls, as nv_clark_early writes them."""
    reg = CL.counts(rolls)
    reg |= {"R_minus_D_pct": CL.r_minus_d(reg), "as_of": as_of}
    mc, m_last = CL.counts(m["party"]), m["day"].max()
    res = {
        "registration": reg,
        "mail": {
            "returned": mc,
            "R_minus_D_pct": CL.r_minus_d(mc),
            "vs_registration_pts": round(CL.r_minus_d(mc) - reg["R_minus_D_pct"], 2),
            "signature_cure": int(m["cure"].sum()),
            "through": str(m_last),
            "days_out": (CL.ELECTION_DAY[2026] - m_last).days,
        },
    }
    through = [m_last]
    if ip is not None and len(ip):
        ip = ip[~ip["id"].isin(m["id"])]  # a voter counted once
        vc, i_last = CL.counts(ip["party"]), ip["day"].max()
        res["in_person"] = {
            "voted": vc,
            "days": int(ip["day"].nunique()),
            "R_minus_D_pct": CL.r_minus_d(vc),
            "vs_registration_pts": round(CL.r_minus_d(vc) - reg["R_minus_D_pct"], 2),
            "through": str(i_last),
            "days_out": (CL.ELECTION_DAY[2026] - i_last).days,
        }
        both = {k: mc[k] + vc[k] for k in (*CL.PARTIES, "total")}
        res["combined"] = {"total": both["total"], "R_minus_D_pct": CL.r_minus_d(both)}
        through.append(i_last)
    last = max(through)
    res["through"], res["days_out"] = str(last), (CL.ELECTION_DAY[2026] - last).days
    return res


def run() -> dict:
    page, _ = get(PAGE, "2026data.html", min_age_h=1)
    html = page.decode("utf-8", "replace")
    f_mail, f_ip = latest(html, "mail"), latest(html, "in_person")
    if not f_mail:
        raise RuntimeError("no general-election mail ballot file linked on the 2026 page")
    raw, meta = get(BASE + f_mail, "mail_latest.csv")
    rolls, m = mail(raw)
    w = CL.written(meta)
    m = _cut(m, meta)
    ip = None
    if f_ip:
        raw_ip, meta_ip = get(BASE + f_ip, "in_person_latest.csv")
        ip = _cut(in_person(raw_ip), meta_ip)
    res = {
        "note": (
            "Diagnostic only. Washoe County Registrar of Voters files: every mailed ballot with the voter's registered party and "
            "status (Nevada mails one to every active voter, so the file is also the active rolls) and every early in-person "
            "voter. No 2022 daily files are published, so Washoe is read against its own rolls."
        ),
        "source": PAGE,
        "files": {"mail": f_mail, "in_person": f_ip},
        **summary(rolls, m, ip, str(w.date()) if w else str(dt.date.today())),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, separators=(",", ":"), allow_nan=False, default=str))
    return res


def show(res: dict):
    r, m = res["registration"], res["mail"]
    print(
        f"\nWashoe County through {res['through']} ({res['days_out']} days out); active voters R-D {r['R_minus_D_pct']:+.2f}"
    )
    print(
        f"  mail returned {m['returned']['total']:,}: R-D {m['R_minus_D_pct']:+.2f} (vs rolls {m['vs_registration_pts']:+.2f})"
    )
    if ip := res.get("in_person"):
        print(
            f"  in person {ip['voted']['total']:,}: R-D {ip['R_minus_D_pct']:+.2f} (vs rolls {ip['vs_registration_pts']:+.2f})"
        )
        print(f"  together {res['combined']['total']:,}: R-D {res['combined']['R_minus_D_pct']:+.2f}")


if __name__ == "__main__":
    show(run())
    from . import fetch as _F

    _F.report()
    sys.exit(0)
