"""Data preparation for the midterm model.

Sources (all free, all under data/raw; see README):
  FEC "Federal Elections YYYY" workbooks  -> official general-election results by district / state
  FiveThirtyEight poll archives (Wayback)  -> House, Senate, generic-ballot and approval polls 2018-2024
  FiveThirtyEight raw_polls.csv           -> every rated poll with the actual result (calibration)
  FiveThirtyEight urbanization index 2022
  Our district lean on the 2018, 2022 and 2026 maps          -> data/static (built by cd_lean_build.py)

Everything is expressed as a Democratic two-party margin in points (D% - R% of the D+R vote),
positive = Democratic. Louisiana / California / Washington top-two races with two candidates of
the same party get margin NaN (no two-party contest).
"""

from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
from .paths import STATIC

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "cache"
DEM = {"DEM", "D", "DFL", "DNL", "D/WF", "DEM/WF"}
REP = {"REP", "R", "R/CON", "REP/CON"}


def _party(p):
    """FEC party labels: DEM/REP plus fusion combos (D/IP/WF, R/CON), write-in winners W(D)/D, GOP, R*, D(UND)."""
    p = str(p).strip().upper().replace("*", "")
    if p in DEM or p.startswith(("DEM", "D/", "D(", "W(D)")):
        return "D"
    if p in REP or p.startswith(("REP", "R/", "R(", "W(R)")) or p == "GOP":
        return "R"
    return "O"


def fec_house(year: int) -> pd.DataFrame:
    """One row per district: dem_votes, rep_votes, margin (D two-party, pts), winner party,
    incumbent party running (I flag), uncontested flag."""
    fx = RAW / "fec" / f"federalelections{year}.xlsx"
    if not fx.exists():
        fx = fx.with_suffix(".xls")  # 2008-2014 workbooks are .xls
    x = pd.ExcelFile(fx)
    # 2018+: "... House Results ..."; 2014/2016: "YYYY US House Results by State"; 2012: "YYYY US House & Senate Resuts" (sic)
    sh = [s for s in x.sheet_names if "House Results" in s or "House & Senate Res" in s][0]
    df = x.parse(sh)
    df.columns = [str(c).strip() for c in df.columns]
    if "DISTRICT" not in df.columns and "D" in df.columns:
        df = df.rename(columns={"D": "DISTRICT"})  # 2012-2016 label
    if "House & Senate" in sh:
        df = df[df["DISTRICT"].astype(str).str.strip().str.upper() != "S"]
    st = df["STATE ABBREVIATION"].ffill()
    draw = df["DISTRICT"].astype(str).str.strip()
    # FEC codes: "01".."53" districts, "00" = at-large seat OR a per-state party-total row; labels such as
    # "01 - UNEXPIRED TERM" mark specials held the same day (dropped). The 2022 workbook also repeats
    # "DISTRICT n" as header rows in the name column; the DISTRICT column itself is still populated.
    special = draw.str.contains("UNEXPIRED|SPECIAL", case=False, na=False) | df["CANDIDATE NAME"].astype(
        str
    ).str.contains("UNEXPIRED", case=False)
    dist = pd.to_numeric(draw.str.extract(r"^(\d+)", expand=False), errors="coerce")
    votes = pd.to_numeric(df["GENERAL VOTES"] if "GENERAL VOTES" in df else df["GENERAL VOTES "], errors="coerce")
    comb_col = [c for c in df.columns if c.startswith("COMBINED GE PARTY TOTALS")]
    comb = pd.to_numeric(df[comb_col[0]], errors="coerce") if comb_col else pd.Series(np.nan, index=df.index)
    win = df["GE WINNER INDICATOR"].astype(str).str.strip().eq("W")
    inc_col = [c for c in df.columns if c.startswith("(I)")][0]
    inc = df[inc_col].astype(str).str.contains(r"\(I\)")
    party = df["PARTY"].map(_party)
    d = pd.DataFrame(
        {
            "state": st,
            "district": dist,
            "party": party,
            "votes": votes.fillna(0.0),
            "comb": comb,
            "win": win,
            "inc": inc,
            "name": df["CANDIDATE NAME"].astype(str),
            "raw_party": df["PARTY"].astype(str),
            "special": special,
        }
    )
    d = d[
        d["district"].notna() & ~d["special"] & ((d["votes"] > 0) | d["win"])
    ].copy()  # unopposed winners have no vote line (FL)
    d = d[~d["state"].isin(["AS", "DC", "GU", "MP", "PR", "VI"])]  # delegates are not House seats
    d["district"] = d["district"].astype(int)
    d.loc[d["district"] == 0, "district"] = (
        1  # at-large (the total rows have no votes and no W, so they are already gone)
    )
    out = []
    for (s, k), g in d.groupby(["state", "district"]):
        # Per candidate: fusion states (NY, CT) list one line per ballot party plus a "Combined Parties:" row
        # holding the candidate's true total in the COMBINED column; elsewhere the single line is the total.
        # Party = the D/R line if the candidate has one (the W flag can sit on the Conservative line).
        cands = []
        for nm, gg in g.groupby("name"):
            comb_v = gg["comb"].dropna().max() if gg["comb"].notna().any() else np.nan
            v = comb_v if not np.isnan(comb_v) else gg["votes"].sum()
            pr = "D" if (gg["party"] == "D").any() else ("R" if (gg["party"] == "R").any() else "O")
            cands.append(
                {"name": nm, "party": pr, "v": float(v), "win": bool(gg["win"].any()), "inc": bool(gg["inc"].any())}
            )
        gg = pd.DataFrame(cands)
        dv = gg.loc[gg["party"] == "D", "v"].max() if (gg["party"] == "D").any() else 0.0
        rv = gg.loc[gg["party"] == "R", "v"].max() if (gg["party"] == "R").any() else 0.0
        wrow = gg[gg["win"]]
        wp = (
            wrow.sort_values("v", ascending=False)["party"].iloc[0]
            if len(wrow)
            else (None if (dv == 0 and rv == 0) else ("D" if dv > rv else "R"))
        )
        ip = gg.loc[gg["inc"], "party"].iloc[0] if gg["inc"].any() else None
        tot = gg["v"].sum()
        margin = 100 * (dv - rv) / (dv + rv) if (dv > 0 and rv > 0) else np.nan
        out.append(
            {
                "year": year,
                "state": s,
                "cd": k,
                "seat": f"{s}-{k}",
                "dem_votes": dv,
                "rep_votes": rv,
                "total_votes": tot,
                "margin": margin,
                "winner": wp,
                "inc_party": ip,
                "uncontested": bool(dv == 0 or rv == 0),
                "dem_share_all": 100 * dv / tot if tot else np.nan,
            }
        )
    return pd.DataFrame(out).sort_values(["state", "cd"]).reset_index(drop=True)


def fec_senate(year: int) -> pd.DataFrame:
    x = pd.ExcelFile(RAW / "fec" / f"federalelections{year}.xlsx")
    sh = [s for s in x.sheet_names if "Senate Results" in s][0]
    df = x.parse(sh)
    df.columns = [str(c).strip() for c in df.columns]
    st = df["STATE ABBREVIATION"].ffill()
    votes = pd.to_numeric(df["GENERAL VOTES"] if "GENERAL VOTES" in df else df["GENERAL VOTES "], errors="coerce")
    comb_col = [c for c in df.columns if c.startswith("COMBINED GE PARTY TOTALS")]
    comb = pd.to_numeric(df[comb_col[0]], errors="coerce") if comb_col else pd.Series(np.nan, index=df.index)
    win = df["GE WINNER INDICATOR"].astype(str).str.strip().eq("W")
    inc_col = [c for c in df.columns if c.startswith("(I)")][0]
    inc = df[inc_col].astype(str).str.contains(r"\(I\)")
    d = pd.DataFrame(
        {
            "state": st,
            "party": df["PARTY"].map(_party),
            "votes": votes,
            "comb": comb,
            "win": win,
            "inc": inc,
            "name": df["CANDIDATE NAME"].astype(str),
            "special": df["CANDIDATE NAME"].astype(str).str.contains("SPECIAL", case=False)
            | df.get("FOOTNOTES", pd.Series("", index=df.index)).astype(str).str.contains("special", case=False),
        }
    )
    d = d[d["votes"].notna() & (d["votes"] > 0)].copy()
    d["v"] = np.where(d["comb"].notna(), d["comb"], d["votes"])
    out = []
    for s, g in d.groupby("state"):
        gg = g.groupby(["name", "party"], as_index=False).agg(v=("v", "max"), win=("win", "any"), inc=("inc", "any"))
        dv = gg.loc[gg["party"] == "D", "v"].max() if (gg["party"] == "D").any() else 0.0
        rv = gg.loc[gg["party"] == "R", "v"].max() if (gg["party"] == "R").any() else 0.0
        wp = gg.loc[gg["win"], "party"].iloc[0] if gg["win"].any() else None
        ip = gg.loc[gg["inc"], "party"].iloc[0] if gg["inc"].any() else None
        out.append(
            {
                "year": year,
                "state": s,
                "dem_votes": dv,
                "rep_votes": rv,
                "margin": 100 * (dv - rv) / (dv + rv) if (dv > 0 and rv > 0) else np.nan,
                "winner": wp,
                "inc_party": ip,
                "n_races_in_state": len(gg[gg["win"]]),
            }
        )
    return pd.DataFrame(out)


def partisan_lean(map_year: int) -> pd.DataFrame:
    """Our district lean on the map of the 2018, 2022 or 2026 election: 0.75 x (the previous presidential two-party margin minus
    the national one) + 0.25 x the same for the election before, from public precinct returns (cd_lean_build.py). Margin
    points, positive = D. Only elections held before `map_year` enter."""
    if map_year == 2026:
        t = pd.read_csv(STATIC / "cd_lean_2026.csv")
    else:
        t = pd.read_csv(STATIC / "cd_lean_hist.csv")
        t = t[t["cycle"] == map_year]
    if len(t) != 435 or t["lean_75_25"].isna().any():
        raise ValueError(f"no complete district lean for the {map_year} map ({len(t)} seats)")
    return pd.DataFrame({"seat": t["seat"].values, "lean": t["lean_75_25"].astype(float).values})


def urbanization() -> pd.DataFrame:
    u = pd.read_csv(RAW / "538repo" / "urbanization-index-2022.csv")
    u["seat"] = u["state"] + "-" + u["cd"].astype(int).astype(str)
    return u[["seat", "urbanindex", "rural", "exurban", "suburban", "urban", "grouping"]]


def polls(kind: str) -> pd.DataFrame:
    """538 archive -> one row per (poll, race) with D and R pct and margin. kind: house | senate | generic | approval."""
    f = {
        "house": "house_polls_historical",
        "senate": "senate_polls_historical",
        "generic": "generic_ballot_polls_historical",
        "approval": "president_approval_polls_historical",
    }[kind]
    df = pd.read_csv(RAW / "538" / f"{f}.csv", low_memory=False)
    df["end_date"] = pd.to_datetime(df["end_date"], format="%m/%d/%y", errors="coerce")
    if kind == "approval":
        out = df[["poll_id", "politician", "end_date", "sample_size", "numeric_grade", "yes", "no"]].copy()
        out["net"] = out["yes"] - out["no"]
        return out
    if kind == "generic":  # the generic file carries dem / rep columns directly
        out = (
            df[
                [
                    "poll_id",
                    "question_id",
                    "cycle",
                    "end_date",
                    "sample_size",
                    "numeric_grade",
                    "pollster",
                    "partisan",
                    "internal",
                    "population",
                    "dem",
                    "rep",
                ]
            ]
            .rename(columns={"numeric_grade": "grade"})
            .copy()
        )
        out["margin"] = out["dem"] - out["rep"]
        return out.dropna(subset=["margin"])
    df["p"] = df["party"].map(_party)
    key = ["poll_id", "question_id"] + (
        ["state", "seat_number", "cycle", "election_date", "stage"] if kind != "generic" else ["cycle"]
    )
    dem = df[df["p"] == "D"].groupby(key)["pct"].max().rename("dem")
    rep = df[df["p"] == "R"].groupby(key)["pct"].max().rename("rep")
    meta = (
        df.groupby(key)
        .agg(
            end_date=("end_date", "max"),
            sample_size=("sample_size", "max"),
            grade=("numeric_grade", "max"),
            pollster=("pollster", "first"),
            partisan=("partisan", "first"),
            internal=("internal", "first"),
            population=("population", "first"),
        )
        .reset_index()
    )
    out = meta.merge(dem.reset_index(), on=key, how="left").merge(rep.reset_index(), on=key, how="left")
    out["margin"] = out["dem"] - out["rep"]
    if kind == "house":
        out["seat"] = out["state"].map(_abbr) + "-" + out["seat_number"].fillna(0).astype(int).astype(str)
    if kind == "senate":
        out["seat"] = out["state"].map(_abbr)
    return out.dropna(subset=["margin"])


_ST = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
    "District of Columbia": "DC",
}


def _abbr(s):
    return _ST.get(s, s)


if __name__ == "__main__":
    for y in (2018, 2022):
        h = fec_house(y)
        print(
            y,
            "House:",
            len(h),
            "seats; D wins",
            (h["winner"] == "D").sum(),
            "R wins",
            (h["winner"] == "R").sum(),
            "uncontested",
            h["uncontested"].sum(),
            f"| median D margin {h['margin'].median():.1f}",
        )
        s = fec_senate(y)
        print(y, "Senate:", len(s), "states; D", (s["winner"] == "D").sum(), "R", (s["winner"] == "R").sum())
    print({y: partisan_lean(y).head(3).to_dict("records") for y in (2018, 2022, 2026)})
    hp = polls("house")
    print("house polls", len(hp), hp["cycle"].value_counts().to_dict())
    print(hp[hp["cycle"] == 2022][["seat", "end_date", "dem", "rep", "margin", "grade"]].head(3).to_string())
    gp = polls("generic")
    print("generic polls", len(gp), gp.groupby("cycle").size().to_dict())
