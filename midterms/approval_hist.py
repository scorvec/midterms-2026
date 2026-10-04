"""Presidential approval for the national mood (2026-10-03, user: "any value in using a presidential approval metric for
midterms?" -> "Sure go for it").

The approval-adjusted national mood (national_mood.py, APPROVAL) is fitted on GALLUP net approval (approve - disapprove) in
the 30 days before each lead, 1996-2024, from the American Presidency Project's tables (data/raw/approval_hist/, one page per
president). Gallup's own readings stop in December 2025 (the second-term page lists other pollsters after that, and the
parser keeps only Gallup rows), so the LIVE value is our own approval trend (generic.py: every pollster, house effects
removed) moved onto Gallup's scale by GALLUP_GAP: Gallup minus the all-pollster average (one value per pollster per 30 days)
over 2017-2024 in 538's archive = -2.48 net (Trump 1st term -2.67 +/- 0.38, n 141; Biden -1.92 +/- 0.75, n 49). The 2025
overlap with our own trend read -11, but our 2025 trend rests on 1-4 polls a month and is not a usable calibration.
    x = net approval, signed toward the Democrats (+ net for a Democratic president, - net for a Republican one)
"""
import io, json
from functools import lru_cache
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PRES = [("harry-s-truman", "D", 1945, 1953), ("dwight-d-eisenhower", "R", 1953, 1961), ("john-f-kennedy", "D", 1961, 1963),
        ("lyndon-b-johnson", "D", 1963, 1969), ("richard-m-nixon", "R", 1969, 1974), ("gerald-r-ford", "R", 1974, 1977),
        ("jimmy-carter", "D", 1977, 1981), ("ronald-reagan", "R", 1981, 1989), ("george-bush", "R", 1989, 1993),
        ("william-j-clinton", "D", 1993, 2001), ("george-w-bush", "R", 2001, 2009), ("barack-obama", "D", 2009, 2017),
        ("donald-j-trump", "R", 2017, 2021), ("joseph-r-biden", "D", 2021, 2025), ("donald-j-trump-2nd-term", "R", 2025, 2029)]
PRESIDENT_2026 = "R"
GALLUP_GAP = -2.5


@lru_cache(maxsize=1)
def gallup():
    """Every Gallup approval reading: end, net, party of the president."""
    rows = []
    for slug, party, _, _ in PRES:
        f = ROOT / "data" / "raw" / "approval_hist" / f"{slug}.html"
        if not f.exists(): continue
        t = [x for x in pd.read_html(io.StringIO(f.read_text(encoding="utf-8", errors="replace"))) if any("Approv" in str(c) for c in x.columns)][0]
        t.columns = [str(c) for c in t.columns]
        if "Source" in t: t = t[t["Source"].astype(str).str.lower().str.strip() == "gallup"]       # the 2nd-term page mixes pollsters
        cs = {k: [c for c in t.columns if k in c][0] for k in ("End", "Approv", "Disapprov")}
        d = pd.DataFrame({"end": pd.to_datetime(t[cs["End"]], errors="coerce"),
                          "net": pd.to_numeric(t[cs["Approv"]], errors="coerce") - pd.to_numeric(t[cs["Disapprov"]], errors="coerce")})
        d["party"] = party; rows.append(d.dropna())
    return pd.concat(rows).sort_values("end").reset_index(drop=True)


def gallup_x(asof, days=30):
    """Gallup net approval in the `days` before asof (else the last three readings), signed toward the Democrats."""
    G = gallup(); asof = pd.Timestamp(asof)
    q = G[(G.end <= asof) & (G.end > asof - pd.Timedelta(days=days))]
    if len(q) < 1: q = G[G.end <= asof].tail(3)
    return float(q.net.mean()) * (1 if q.party.iloc[-1] == "D" else -1)


def _trend(trend=None):
    if trend is None: trend = json.loads((ROOT / "data" / "cache" / "generic_model.json").read_text())["approval"]["trend"]
    t = pd.DataFrame(trend, columns=["d", "v"]); t["d"] = pd.to_datetime(t["d"]); return t


def trend_at(t, when):
    x = (t.d - pd.Timestamp("2000-01-01")).dt.days.values
    return float(np.interp((pd.Timestamp(when) - pd.Timestamp("2000-01-01")).days, x, t.v.values))


def live_x(asof=None, trend=None, now=None):
    """Approval on Gallup's scale, signed toward the Democrats: (x, net on Gallup's scale, net on our trend).
    `now` = the trend's value directly (backfill passes its as-of fit); else read from `trend` (default: today's model)."""
    if now is None:
        t = _trend(trend); when = pd.Timestamp(asof) if asof is not None else t.d.max().normalize()
        now = trend_at(t, min(when, t.d.max()))
    net = now + GALLUP_GAP
    return net * (1 if PRESIDENT_2026 == "D" else -1), net, now


if __name__ == "__main__":
    G = gallup(); print("Gallup readings:", len(G), G.end.min().date(), "->", G.end.max().date())
    gm = json.loads((ROOT / "data" / "cache" / "generic_model.json").read_text())
    print("live (trend 'now' from generic_model):", live_x(now=gm["approval"]["diag"]["now"]))
