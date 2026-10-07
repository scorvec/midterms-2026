"""Known corrections to scraped race polls (2026-10-07, user: South Dakota "Were there really polls showing the democrat ahead").

data/manual/poll_corrections.csv, one row per correction, applied to a race's assembled polls (every source, after the
hand-entered and feed rows join, before versions collapse):
  office, seat, pollster (case-insensitive substring of the pollster name, sponsor tags ignored), end_date (YYYY-MM-DD, or
  empty = any date), action, value, note
  action = drop     the row is not a poll of its own (e.g. a feed copy dated by its RELEASE: Lake Research's Bengs poll,
                    fielded Aug 26-30, appeared again from the Bluesky feed as "9/30" with no sponsor tag and full weight)
  action = sponsor  value D / R / I: the poll was paid for by that side - written as the pollster's "(X)" tag, which
                    model.sponsor_of turns into the measured sponsor shift and weight (no new adjustment of any kind)
  action = end_date value YYYY-MM-DD: the true end of fieldwork
  action = name     value "Feed>Ballot": a candidate name the feed misspells, fixed BEFORE polls are matched to the race's
                    nominees by surname (Polling USA's NC-1 "Davies" for Rep. Don Davis, 2026-10-07); pollster/end_date
                    may be empty (= every poll of that race)
Every applied row is printed, so the run log shows what changed.
"""
import re
from pathlib import Path

import pandas as pd

F = Path(__file__).resolve().parents[1] / "data" / "manual" / "poll_corrections.csv"


def _bare(s): return re.sub(r"\s*\((D|R|I)\)", "", str(s)).strip().lower()


def _rows(office, seat):
    if not F.exists(): return pd.DataFrame()
    d = pd.read_csv(F, dtype=str, keep_default_na=False)
    return d[(d.office == office) & (d.seat == seat)]


def apply(polls: pd.DataFrame, seat, office) -> pd.DataFrame:
    if polls is None or not len(polls): return polls
    C = _rows(office, seat)
    if not len(C): return polls
    p = polls.copy(); p["end_date"] = pd.to_datetime(p["end_date"])
    keep = pd.Series(True, index=p.index)
    for c in C.itertuples():
        hit = p.pollster.map(_bare).str.contains(c.pollster.strip().lower(), regex=False)
        if c.end_date: hit &= p.end_date.dt.strftime("%Y-%m-%d") == c.end_date
        hit &= keep
        if not hit.any(): continue
        if c.action == "drop":
            keep &= ~hit
        elif c.action == "sponsor":
            tag = f" ({c.value.strip().upper()})"
            p.loc[hit, "pollster"] = p.loc[hit, "pollster"].map(lambda s: re.sub(r"\s*\((D|R|I)\)", "", str(s)).strip() + tag)
        elif c.action == "end_date":
            p.loc[hit, "end_date"] = pd.Timestamp(c.value)
        else:
            continue
        print(f"  poll correction {office} {seat}: {c.action} {c.value} on {int(hit.sum())} row(s) [{c.pollster} {c.end_date or 'any date'}] - {c.note}")
    return p[keep]


def fix_names(df: pd.DataFrame, office) -> pd.DataFrame:
    """Apply action=name rows (by office and seat) to dem_name / rep_name of feed rows, before nominee matching."""
    if df is None or not len(df) or not F.exists() or "seat" not in df: return df
    d = pd.read_csv(F, dtype=str, keep_default_na=False)
    d = d[(d.office == office) & (d.action == "name")]
    if not len(d): return df
    df = df.copy()
    for c in d.itertuples():
        old, new = [x.strip() for x in c.value.split(">", 1)]
        for col in ("dem_name", "rep_name"):
            hit = (df.seat == c.seat) & (df[col].astype(str).str.strip().str.lower() == old.lower())
            if c.pollster: hit &= df.pollster.map(_bare).str.contains(c.pollster.strip().lower(), regex=False)
            if hit.any():
                df.loc[hit, col] = new
                print(f"  poll correction {office} {c.seat}: name {old} -> {new} on {int(hit.sum())} row(s) - {c.note}")
    return df
