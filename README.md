# 2026 U.S. midterm forecast

Repository: [github.com/scorvec/midterms-2026](https://github.com/scorvec/midterms-2026)

A Monte Carlo forecast of the 2026 U.S. House, Senate and governor elections, published at
[scorvec.com/midterms](https://scorvec.com/midterms/) ("How the forecast works":
[about.html](https://scorvec.com/midterms/about.html)). Each day the model re-reads the polls, refits a national
generic-ballot trend from individual polls, blends every race's polls with a fundamentals prior and simulates the
elections 20,000 times with correlated errors (a national miss, a statewide miss, regional and demographic swings, each
race's own error). The pages are static: every position of the "national mood" slider is precomputed.

The whole daily run happens in GitHub Actions (`.github/workflows/daily.yml`); nothing depends on any particular
machine. The run commits its outputs to `web/`, and the website copies them from there.

## What runs each day

`python -m midterms.weekly --daily` (02:30 UTC, i.e. 22:30 New York time, after the day's poll releases):

1. `bootstrap` (separate step) - downloads the fixed public inputs into `data/raw/` if they are missing.
2. National polls: VoteHub's open API (generic ballot, approval and race-poll catalogues), Polling USA and Political Poll
   Bot posts on Bluesky, pollresults.org; `generic.py` fits the generic-ballot trend with pollster house effects and
   likely/registered/adult offsets.
3. UF Election Lab early-vote counts (`early_vote.py`) and EIA's weekly residential heating-oil price (`heating_oil.py`).
4. Wikipedia: the House seat table (2026 maps, incumbents, PVI), ratings, every Senate/governor race page and the 50
   state House pages (polls); VoteHub and pollresults.org race polls; the Bluesky feeds (`refresh.py`).
5. `build_web.py` - House, Senate and governor simulations -> `web/data/model.json`, `polls.json`, `whatif.json`.
6. Daily snapshot `web/data/history/model_<date>.json`; the reconstructed history (`backfill.py`: the current model run as
   of every past date with only the polls released by then - rebuilt only when the methodology files change) and
   `web/data/race_history.json`; `web/data/summary.json` (the homepage panel).
7. Diagnostics that do not feed the forecast: Florida and Pennsylvania early/mail vote (`fl_early.py`, `pa_early.py`),
   Texas early-vote turnout (`tx_early.py`, a separate workflow step, off until switched on), voter registration in North Carolina and
   Pennsylvania on Mondays (`registration.py`), same-pollster poll deltas. They are uploaded as a workflow artifact, not
   committed. North Carolina early ballots by race and the Florida 2024 comparison run in their own workflow
   (`early-diag.yml`, README "Early-vote composition").
8. Mondays: `reports/weekly_<date>.md`, which races moved in the past week and why.

A publish gate (`weekly.publish_gate`) holds the outputs when a polled race suddenly has no polls or the poll counts
fall (usually a Wikipedia table that stopped parsing); the workflow then commits only the poll records and fails, and
`force_publish` on a manual run overrides it.

Runtime: about 5-10 minutes on a normal day (the simulations take ~1 minute; the rest is fetching, mostly pages that
changed); add ~10 minutes on a day the methodology changed (the backfill is rebuilt) or the data cache was evicted (all
pages are fetched again).

## Repository layout

| path | what |
|---|---|
| `midterms/` | the model (Python 3.11; `requirements.txt`) |
| `web/` | the four pages (`index.html`, `about.html`, `polls.html`, `whatif.html` + `whatif_engine.js`) and `web/data/` outputs |
| `data/static/` | small one-time inputs derived from public sources (below); committed |
| `data/state/` | poll records the daily run accumulates (the feeds only reach back a few weeks); committed |
| `data/manual/` | hand-entered polls (releases not yet in any feed), with their source URLs |
| `data/raw/`, `data/cache/` | downloads and intermediate files; git-ignored, kept between runs by `actions/cache` |
| `reports/` | weekly change reports |
| `site_integration/` | the workflow the website (scorvec/scorvec.github.io) uses to pull the published files from this repository |

## Data

Committed inputs (`data/static/`) and their sources:

| file | contents | source / licence |
|---|---|---|
| `district_groups_2026.csv`, `state_groups.csv`, `district_wnc_2026.csv` | citizen voting-age population by Hispanic / Asian origin group, white non-college share, per 2026 district and state | U.S. Census Bureau ACS 2020-2024 5-year tables + PUMS, 120th-Congress block equivalency (public domain); built by `demog.py` |
| `district_heat_oil_2026.csv`, `state_heat_oil.csv` | share of households heating with fuel oil / kerosene | ACS B25040 (public domain); `demog.heat_oil_shares` |
| `urbanization_2026.csv` | urbanization index on the 2026 maps | 538 urbanization index 2022 (CC BY 4.0) carried to the new maps with Census block-group populations; `urban.py` |
| `early_share_2022.json` | each state's share of 2022 ballots cast before Election Day | U.S. EAC 2022 EAVS (public domain); `early_vote.py` |
| `fec_june30_{2018,2022,2026}.csv`, `fec_senate_june30_2026.csv`, `fec_reports_2026.csv`, `fec_nominees_2026.csv` | candidate fundraising through June 30 | FEC / OpenFEC (public domain); `fec_money.py` |
| `house_national_vote_1946.csv` | national House vote by party since 1946 | Wikipedia election pages (CC BY-SA 4.0) |
| `national_mood_fit.json` | fitted coefficients only: the national-vote error s(L) (and the unused directional and approval variants), the cycles used and the late-movement slope | our fit to the generic-ballot average vs the House vote, 15 cycles 1996-2024 (538 / ABC News averages and poll archives, HuffPost Pollster archives, Gallup approval). The underlying table is not distributed; `python -m midterms.national_mood --refit` rebuilds the fit from a local copy |
| `tx_early_sources.json` (`tx_early_2022_final.csv` planned, not built) | sources and retrieval times for the Texas tracker; the 2022 final early vote by county will be added when the tracker is switched on | Texas Secretary of State (public records as reported by the counties; attribution, no endorsement implied) |
| `tx_county_pres2024.csv` | 2024 presidential votes by Texas county (to check the tracker's county groups) | MIT Election Data and Science Lab 2024 precinct returns (doi:10.7910/DVN/NYTPDU, CC0), summed by county; `tx_early baseline` |
| `nc_early_baseline.csv` (+ `.qc.json`) | North Carolina accepted mail / one-stop ballots of the 2022 and 2024 generals, cumulative by days before Election Day (0-60), by self-reported race group and Hispanic ethnicity: counts, low-propensity and previous-general method counts, and the registered voters of each group on the same days out | NC State Board of Elections absentee files, voter-history file and weekly registration statistics (public records); aggregates only, `python -m midterms.nc_early baseline --src DIR` |
| `fl_wayback/Stats_{43888,26906}_*.txt` | Florida Division of Elections statistics files of the 2024 general (VbmVoted 10/04, 10/09, 10/28; VbmProvided 09/16, 10/10, 10/28; EarlyVoted 10/28; finals) and the 2022 general (final) | Florida Division of Elections (public records), as captured by the Internet Archive's Wayback Machine; fetched once |
| `fl_registration.csv` | Florida active registered voters by party at each month end (Jun-Nov 2022, Jun-Nov 2024, Jan 2026 on) | Florida Division of Elections monthly reports by county and party (public records); added by hand (dos.fl.gov needs a browser) |
| `gov_results_2024.csv`, `gov_nominees.csv`, `gov_candidate_quality.csv`, `gov_nominee_offices.csv` | the 2024 governor results table; every governor nominee 1998-2026 with the linked article; whether each had held statewide elected office or a seat in Congress before the race, and the infobox offices read | Wikipedia yearly "United States gubernatorial elections" pages and the nominees' articles (CC BY-SA 4.0); facts derived by `midterms/gov_quality.py` (README "Governor model review") |
| `rcv_transfers.csv`, `rcv_rates.json`, `rcv_backtest_results.csv`, `rcv_backtest_polls.csv` | ranked-choice transfers: one row per eliminated candidate (party type, first-round share, share of its ballots reaching each finalist or exhausted), the fitted rates, and the leave-one-race-out backtests | official round-by-round tabulations of the Maine Secretary of State (2nd district 2018, 2022) and the State of Alaska Division of Elections (August 2022 special general, 2022 and 2024 general elections), public records; `python -m midterms.rcv --fetch` downloads them once into `data/raw/rcv/` and rebuilds the tables, `--backtest` reruns the test |

`web/data/districts_2026.topo.json` (district shapes) is built by `district_shapes.py` from Census 2020 cartographic
block groups and the 120th-Congress block equivalency files (public domain).

`data/state/` holds poll toplines as they were posted by the feeds (Polling USA, pollresults.org - poll data from The New
York Times, CC BY 4.0), `race_poll_seen.csv` (the date each race poll first appeared on the forecast page) and
`pollster_scores.csv` (each pollster's numeric quality score derived from pollresults.org's letter grades; recorded only,
no quality weighting is switched on; the grades themselves are not committed).

Downloaded at run time (never committed): the 538 poll archives and data repository files (CC BY 4.0), FEC results
workbooks, MIT Election Lab presidential returns (CC0), VoteHub, Wikipedia, Bluesky posts, pollresults.org, UF Election
Lab early-vote counts (CC BY-NC-ND 4.0 - used only to compute each state's scaling, never republished), EIA heating-oil
prices, Florida Division of Elections and Pennsylvania Department of State files (and, once switched on, the Texas Secretary of
State's 2026 early-voting county files). Race ratings from Cook, Sabato and
Inside Elections are read from Wikipedia's ratings tables at run time and shown for comparison only.

### Fetching politely

Every request identifies itself as `scorvec.com midterm model (+https://scorvec.com/midterms/about.html)`, pages are
fetched one at a time with pauses, and nothing that is already held is downloaded again (`midterms/fetch.py` counts every
request and prints the totals per host at the end of the run). `data/raw` and `data/cache` persist between runs.

| source | per daily run |
|---|---|
| Wikipedia (~130 race and table pages) | the MediaWiki API's current revision ids, 50 pages per request (3 requests); only pages edited since the cached copy are downloaded; the historical results pages are fetched once |
| pollresults.org race pages (~220) | the sitemap (1 request) - a page is downloaded only when its `lastmod` is on or after the cached copy |
| Bluesky (Polling USA, Political Poll Bot) | posts are cached; the feed is read only until the first post already held (about one page per account) |
| VoteHub API (5 poll types) | 5 requests, ~2 MB: the API has no date filter or ETag |
| EIA heating oil, UF early vote, Florida statistics files | conditional requests (ETag / Last-Modified): unchanged files come back as 304 |
| Texas SOS early-voting turnout | OFF until switched on (`TX_EARLY`); when on, from Oct 19: the election index and one county file per early-voting day so far, each at most once a day (conditional requests); the per-voter rosters are never requested |
| Pennsylvania mail ballots (data.pa.gov) | 2026 aggregates only; 2022 and 2024 are queried once and kept |
| NC / PA registration (Mondays) | new weekly NC snapshots only; past years' date lists and PA PDFs are kept |
| 538, FEC, MIT inputs (bootstrap) | once, then kept (checksums verified) |
| OpenFEC | never in the daily run; a manual rebuild only (cached per candidate) |

The one-time builders (`demog.py`, `urban.py`, `district_shapes.py`, `fec_money.py`) need large downloads (ACS summary
files and PUMS, Census geography; several GB) or an API key and are not part of the daily run; their docstrings list the
exact inputs.

## Secrets

| secret | used by | needed for |
|---|---|---|
| `OPENFEC_API_KEY` | `fec_money.py`, only on a manual run with `fec_refresh` | re-pulling June 30 fundraising (an [api.data.gov](https://api.open.fec.gov/developers/) key) |

The daily run itself needs no secret; it commits with the workflow's own `GITHUB_TOKEN`.

## Run it yourself

```
pip install -r requirements.txt
python -m midterms.bootstrap
python -m midterms.weekly --daily      # ~10-15 min; writes web/data/
python -m http.server -d web           # then open the printed address
```

## Ranked-choice races

Maine (U.S. Senate and House; not governor, which the Maine Constitution keeps plurality) and Alaska (U.S. Senate, House and
governor) count ranked ballots. In those races (`midterms/rcv.py`, `RCV_RACES_2026`) a poll's own final round is used as the
poll, and a first-round-only poll is converted to an expected final-round margin with transfer rates measured on the official
tabulations above; the spread of those rates across past counts is added to the race's polling uncertainty. Backtest and
numbers: `web/about.html#rcv`. `MIDTERMS_RCV=off` restores the previous handling.

## Governor model review (2026-10-05)

Governor races are the least nationalized of the three, so the fundamentals prior matters more there, and the governor model had
only a crude backtest (a plain mean of the last three weeks' polls, end of campaign, polled races). The review built a leak-free
harness first and scored every change on it; only changes with a significant out-of-sample gain are on. Nothing in the review shifts
either party: no fitted lean toward a party, no persistent-state or white non-college correction (both stay off).

**Harness** (`python -m midterms.gov_backtest all`, then `report`; `midterms/gov_backtest.py`). Every even-year cycle 2006-2024 at
Sep 1, Sep 15, Oct 1, Oct 15, Nov 1 and the day before the election (2006-2016 from Oct 1 only: 538's raw_polls, the poll source
for those years, holds the last ~60 days; 2018-2024 use 538's full governor poll archive). It runs the live path - `gov2026.Prior`,
`prepare` (sponsor shifts and pollster leans fitted on earlier cycles only, undecided / third-candidate / new-pollster variance),
`collapse_versions`, the robust blend and `gov2026.simulate` on the shared national draw - with only what was known on the date:
polls three days after their last field day, the prior fitted walk-forward on the governor races of earlier cycles (1998 onward,
never a later cycle), and the generic-ballot average known at that lead (538's average to 2016, our estimator from 2018; no
directional correction; the table is the one `national_mood --refit` uses and is not distributed). The 2026-only Hispanic / Asian
loadings and the heating-oil term are left out. n = 234 races (every D-v-R race of each cycle, polled or not), 1,086 race-dates,
10 cycles; 984 race-dates had polls. Scores: Brier and log loss of P(D wins), error of the race mean, the CRPS of the margin
(a proper score of the whole distribution), 80 % / 50 % interval coverage. Significance, paired by race and date: an exact sign-flip
permutation of the cycle means (2^10 assignments; the honest unit, since a cycle shares one national miss) and a race-cluster
bootstrap stratified by cycle (4,000 draws). In the table each change reads difference (cycles better; permutation p / bootstrap p).

**Baseline** (the model before the review): log loss 0.1944, Brier 0.0573, margin MAE 6.11 and RMSE 8.59 points, CRPS 4.39,
80 % intervals cover 76.1 % and 50 % intervals 45.1 % (too narrow), PIT KS 0.121.

| variant | log loss | Brier | CRPS | margin MAE | 80 % cov. |
|---|---|---|---|---|---|
| base (model before the review) | 0.1944 | 0.0573 | 4.390 | 6.11 | 0.761 |
| successor incumbents coded as incumbents (parser fix) | -0.0044 (7/10; 0.129 / 0.059) | -0.0017 (7/10; 0.152 / 0.062) | +0.035 (3/10; 0.248 / 0.183) | +0.03 (0.469 / 0.372) | 0.758 |
| lean slope with a time trend | -0.0050 (6/10; 0.061 / 0.018) | -0.0015 (7/10; 0.070 / 0.070) | -0.230 (7/10; 0.025 / 0.000) | -0.32 (0.023 / 0.001) | 0.789 |
| recency weights, half-life 12 years | -0.0006 (6/10; 0.484 / 0.266) | -0.0001 (5/10; 0.676 / 0.578) | -0.039 (9/10; 0.064 / 0.017) | -0.06 (0.041 / 0.004) | 0.762 |
| recency weights, half-life 8 years | -0.0007 (5/10; 0.553 / 0.377) | -0.0001 (5/10; 0.871 / 0.757) | -0.053 (9/10; 0.072 / 0.028) | -0.08 (0.037 / 0.006) | 0.762 |
| fit without races with a 15 %+ third candidate | -0.0007 (3/10; 0.666 / 0.354) | -0.0003 (3/10; 0.484 / 0.249) | -0.021 (6/10; 0.184 / 0.103) | -0.02 (0.328 / 0.228) | 0.766 |
| previous governor race residual | +0.0007 (7/10; 0.965 / 0.569) | +0.0002 (7/10; 0.938 / 0.729) | -0.049 (8/10; 0.160 / 0.086) | -0.07 (0.096 / 0.092) | 0.770 |
| previous race residual, own v inherited | -0.0009 (7/10; 0.383 / 0.468) | -0.0004 (7/10; 0.312 / 0.467) | -0.033 (7/10; 0.441 / 0.338) | -0.04 (0.459 / 0.406) | 0.761 |
| incumbent's own previous residual | -0.0006 (7/10; 0.518 / 0.286) | -0.0003 (8/10; 0.314 / 0.196) | -0.023 (7/10; 0.562 / 0.469) | -0.03 (0.555 / 0.482) | 0.761 |
| prior sd: open v incumbent | +0.0007 (5/10; 0.268 / 0.059) | +0.0002 (6/10; 0.471 / 0.254) | +0.015 (1/10; 0.018 / 0.106) | +0.02 (0.021 / 0.152) | 0.748 |
| prior sd grows with distance from 0 | +0.0021 (3/10; 0.105 / 0.033) | +0.0005 (4/10; 0.439 / 0.221) | +0.055 (2/10; 0.029 / 0.005) | +0.07 (0.035 / 0.009) | 0.750 |
| prior sd x0.8 | -0.0063 (9/10; 0.014 / 0.005) | -0.0015 (9/10; 0.143 / 0.085) | +0.140 (0/10; 0.002 / 0.000) | +0.12 (0.006 / 0.007) | 0.728 |
| prior sd x1.25 | +0.0071 (1/10; 0.004 / 0.000) | +0.0020 (1/10; 0.027 / 0.003) | -0.060 (8/10; 0.031 / 0.031) | -0.05 (0.143 / 0.150) | 0.778 |
| shared statewide shock x0.5 | -0.0037 (7/10; 0.258 / 0.226) | -0.0008 (9/10; 0.139 / 0.239) | +0.024 (3/10; 0.119 / 0.018) | +0.00 (1.000 / 1.000) | 0.703 |
| shared statewide shock x1.5 | +0.0085 (2/10; 0.051 / 0.008) | +0.0017 (1/10; 0.037 / 0.021) | +0.004 (6/10; 0.867 / 0.775) | +0.00 (1.000 / 1.000) | 0.822 |
| race sd x1.15 | +0.0063 (3/10; 0.031 / 0.001) | +0.0013 (1/10; 0.016 / 0.002) | -0.019 (7/10; 0.148 / 0.076) | +0.00 (1.000 / 1.000) | 0.811 |
| race sd x0.9 | -0.0037 (7/10; 0.066 / 0.006) | -0.0008 (9/10; 0.023 / 0.006) | +0.024 (2/10; 0.016 / 0.001) | +0.00 (1.000 / 1.000) | 0.729 |
| undecided penalty x2 | +0.0002 (3/10; 0.578 / 0.639) | +0.0000 (4/10; 0.984 / 0.983) | +0.002 (5/10; 0.824 / 0.769) | +0.01 (0.518 / 0.581) | 0.758 |
| undecided penalty from 8 % | +0.0003 (2/10; 0.277 / 0.309) | +0.0000 (4/10; 0.662 / 0.792) | +0.002 (4/10; 0.607 / 0.558) | +0.01 (0.211 / 0.229) | 0.758 |
| no undecided penalty | +0.0001 (5/10; 0.891 / 0.931) | +0.0001 (6/10; 0.629 / 0.643) | +0.001 (5/10; 0.875 / 0.856) | -0.00 (0.758 / 0.838) | 0.757 |
| third-candidate penalty x1.5 | +0.0002 (1/10; 0.250 / 0.026) | +0.0001 (1/10; 0.375 / 0.050) | +0.004 (2/10; 0.875 / 0.513) | +0.00 (0.875 / 0.694) | 0.761 |
| poll staleness half-life 42 d | +0.0000 (3/10; 0.906 / 0.907) | -0.0001 (5/10; 0.646 / 0.750) | +0.012 (3/10; 0.178 / 0.308) | +0.02 (0.191 / 0.345) | 0.765 |
| poll staleness half-life 90 d | +0.0002 (5/10; 0.422 / 0.577) | +0.0001 (5/10; 0.346 / 0.491) | -0.005 (5/10; 0.424 / 0.537) | -0.01 (0.289 / 0.390) | 0.755 |
| governor poll errors from raw_polls (4.4 / 5.4) | +0.0015 (4/10; 0.555 / 0.467) | +0.0001 (2/10; 0.955 / 0.914) | +0.061 (2/10; 0.016 / 0.001) | +0.07 (0.035 / 0.025) | 0.773 |
| successor incumbents, own coefficient | -0.0049 (8/10; 0.020 / 0.005) | -0.0018 (8/10; 0.025 / 0.005) | +0.021 (4/10; 0.340 / 0.330) | +0.02 (0.580 / 0.533) | 0.760 |
| lean trend + parser fix | -0.0083 (9/10; 0.027 / 0.003) | -0.0028 (8/10; 0.018 / 0.009) | -0.167 (7/10; 0.041 / 0.003) | -0.22 (0.055 / 0.006) | 0.782 |
| lean trend + successor term | -0.0090 (9/10; 0.008 / 0.001) | -0.0030 (9/10; 0.004 / 0.003) | -0.185 (7/10; 0.035 / 0.001) | -0.25 (0.045 / 0.004) | 0.781 |
| polls pulled toward the state's fundamentals (k walk-forward) | -0.0046 (7/10; 0.074 / 0.150) | -0.0012 (6/10; 0.676 / 0.339) | -0.091 (7/10; 0.035 / 0.014) | -0.12 (0.066 / 0.037) | 0.786 |
| nominee experience D - R | -0.0049 (5/10; 0.242 / 0.139) | -0.0024 (6/10; 0.107 / 0.075) | -0.094 (8/10; 0.027 / 0.004) | -0.13 (0.025 / 0.004) | 0.771 |
| nominee experience, open seats only | -0.0045 (6/10; 0.107 / 0.106) | -0.0023 (6/10; 0.031 / 0.040) | -0.056 (8/10; 0.178 / 0.057) | -0.09 (0.129 / 0.019) | 0.770 |
| **lean trend + successor term + experience (adopted)** | -0.0111 (9/10; 0.037 / 0.011) | -0.0043 (8/10; 0.020 / 0.004) | -0.266 (8/10; 0.012 / 0.000) | -0.35 (0.016 / 0.000) | 0.796 |
| adopted + undecideds toward the trailing candidate (k walk-forward) | -0.0114 (8/10; 0.158 / 0.023) | -0.0042 (7/10; 0.045 / 0.011) | -0.084 (5/10; 0.461 / 0.266) | -0.12 (0.451 / 0.267) | 0.760 |
| lean trend + successor + experience (open seats) | -0.0110 (9/10; 0.018 / 0.003) | -0.0042 (8/10; 0.014 / 0.001) | -0.233 (7/10; 0.021 / 0.000) | -0.32 (0.021 / 0.000) | 0.791 |
| lean trend + successor, race sd x1.15 | -0.0029 (4/10; 0.363 / 0.402) | -0.0018 (5/10; 0.125 / 0.124) | -0.192 (6/10; 0.031 / 0.000) | -0.25 (0.045 / 0.004) | 0.824 |
| lean trend + successor, race sd x0.9 | -0.0125 (8/10; 0.016 / 0.000) | -0.0037 (9/10; 0.012 / 0.002) | -0.170 (7/10; 0.064 / 0.004) | -0.25 (0.045 / 0.004) | 0.755 |
| lean trend + successor, shared shock x1.5 | +0.0000 (2/10; 0.988 / 0.946) | -0.0012 (4/10; 0.266 / 0.429) | -0.175 (6/10; 0.041 / 0.002) | -0.25 (0.045 / 0.004) | 0.839 |
| lean trend + successor, prior sd x1.25 | -0.0010 (6/10; 0.805 / 0.707) | -0.0007 (7/10; 0.553 / 0.509) | -0.218 (8/10; 0.008 / 0.000) | -0.28 (0.016 / 0.003) | 0.785 |

**Adopted** (`gov2026.PRIOR_SPEC = {"lean_t": True, "succ": True, "qual": "all"}`; `{}` restores the old prior):
- *The lean slope's time trend* (lean x (year - 2010) / 10). Governor races have nationalized: the per-cycle lean slope rose from
  0.1-0.2 (1998-2006) to 0.43-0.54 (2018-2022); a pooled slope (0.36) is too flat for a current race, which compressed every prior
  toward 50-50. On its own: CRPS -0.23 (7/10, p 0.025 / <0.001), MAE -0.32 (p 0.023 / 0.001). Fitted 1998-2024: 0.277 + 0.110 per
  decade -> 0.45 in 2026 (one cycle beyond the data, the same reach the walk-forward test made every cycle).
- *Successor incumbents as their own term*. The history parser read only "re-elected / lost re-election / defeated", so 19 governors
  who had succeeded mid-term and then ran (Ivey 2018, Reynolds 2018, Hochul 2022, McKee 2022, Parson 2020, Quinn 2010 ...) were fitted
  as open seats - while the 2026 race table coded every incumbent running, including the successor Rhoden (SD), as a full incumbent.
  With their own coefficient (the Senate's appointee rule in spirit): log loss -0.0049 (8/10, p 0.020 / 0.005), Brier -0.0018 (p 0.025
  / 0.006); on top of the lean trend log loss -0.0040 (8/10, p 0.047 / 0.037). Simply recoding them as incumbents (`INC_FIX`) scored
  the same within noise and is not used. Fitted: 10.8 points for an elected incumbent, 6.9 for a successor.
- *Nominee experience*, D minus R (+1 / 0 / -1): a nominee is experienced if, before the race, he or she held a statewide elected
  office (governor, lieutenant governor, attorney general, secretary of state, treasurer, comptroller, auditor, superintendent, an
  elected state commission) or a seat in Congress - read from each nominee's Wikipedia infobox (`midterms/gov_quality.py`); a sitting
  governor counts as experienced. Symmetric in party by construction. On top of the lean trend and successor term: CRPS -0.081 (9/10,
  p 0.037 / 0.008), MAE -0.10 (p 0.049 / 0.021), log loss and Brier a little better (not significant). Fitted: 6.1 points.
- Together against the baseline: log loss -0.0111 (9/10 cycles, p 0.037 / 0.011), Brier -0.0043 (8/10, p 0.020 / 0.004), CRPS -0.27
  (8/10, p 0.012 / <0.001), margin MAE 6.11 -> 5.76 (p 0.016 / <0.001); 80 % coverage 76.1 -> 79.6 %, 50 % 45.1 -> 47.2 %, PIT KS
  0.121 -> 0.109. Better at every date (log loss Sep 1 0.196 -> 0.171, final 0.173 -> 0.169); unpolled race-dates' MAE 14.2 -> 11.5.
- 2024 added to the history (`gov2026.USE_2024`, `data/static/gov_results_2024.csv`): a data update, not a model change; 2024 is
  also the harness's tenth test cycle.

Caveat on n: about thirty variants were scored, and with ten cycles the smallest attainable permutation p is 0.002, so no single
result survives a strict multiple-comparison correction. The adopted terms are the ones that pass on both tests, help in most cycles
and have a reason (a measured trend, a coding error, a long-established predictor); everything else stays off.

**Tested, not adopted** (numbers in the table): recency weights instead of a trend (half-lives 8 and 12 years: CRPS better in 9/10
cycles but log loss and Brier flat); dropping races with a strong third candidate from the fit; the same state's previous governor
race (the incumbent's own "personal vote", or the party's) - no gain once polls are blended; heteroscedastic prior sd (by open seat,
or growing with the prior's distance from 0) - worse; a narrower or wider prior (x0.8 / x1.25) and narrower or wider race and shared
errors - each trades log loss against coverage and CRPS (sharper wins on log loss, wider on coverage), so none improves calibration
without a loss elsewhere; governor-specific undecided penalties (x2, from 8 %, off), a larger third-candidate penalty, poll staleness
half-lives of 42 or 90 days - all within noise; polls pulled toward the state's fundamentals by a walk-forward coefficient (symmetric
in party; CRPS better, log loss not significant).

**Measured, not used.**
- Governor poll errors, measured exactly as the Senate's were (538 raw_polls, last 60 days, races with 3+ polls): within-race sd 4.89
  v 4.63 for the Senate, race-average systematic 5.73 v 5.32. Scaling the Senate's 4.2 / 5.0 by those ratios (4.4 / 5.4, fitted
  walk-forward) made every score worse (CRPS +0.06, p 0.016 / 0.001): the governor blend keeps the Senate's poll errors.
- The shared miss: the cycle-mean governor polling miss (non-partisan polls, last 21 days, 1998-2022) has RMS 3.5 points against the
  Senate's 2.8, and the two offices' cycle means correlate 0.90. The simulation's governor shared shock (4.2 x b3 / 0.8: 2.9 points before the review, 3.2 with the
  refitted national slope b3 0.55 -> 0.62) is left as it is: x0.5 and x1.5 each lose on log loss or coverage.
- Same-state Senate and governor races: within a cycle, their polling errors correlate 0.58 (95 % 0.46-0.68; 149 state-years, 13
  cycles), a shared state part of about 3.7 points. The simulation draws the two races' own errors independently (they share only the
  national draw). See follow-up 1 below.
- Undecideds: allocating them in proportion to the candidates' shares is not supported - on 538's governor polls with cycle fixed
  effects the coefficient is -0.19 (se 0.04) in the last 21 days and -0.52 (0.06) at 21-62 days (they lean to the trailing
  candidate), and -0.34 / +0.02 / -0.37 on the 2018-22 archive by lead. Undecideds stay a symmetric variance term (follow-up 2).
- Results run more lopsided than the race means: after removing each cycle's shared miss the winner beats the mean by about 2 points
  (both parties' favourites). The lean trend removes part of it; the rest is not corrected (the symmetric poll pull above was not
  significant).
- Not testable here: Alaska's ranked-choice count (the harness scores Alaska 2022 on its first round; the live model converts polls
  with `rcv.py`), and strong independents (too few governor cases since 2006).

**2026 effect** (Oct 5 inputs, E +8.82, same seed; competitive races and every race that moved 3 points or more):

| state | D v R | polls | prior before -> after | mean (sd) before | mean (sd) after | P(D) before -> after |
|---|---|---|---|---|---|---|
| TN | Jerri Green v Marsha Blackburn | 4 | -7.7 -> -16.3 | -12.1 (6.1) | -13.7 (6.0) | 4 -> 3 % |
| NH | Cinde Warmington v Kelly Ayotte | 9 | -11.5 -> -12.6 | -10.4 (4.9) | -10.6 (4.8) | 4 -> 4 % |
| NE | Lynne Walz v Jim Pillen | 9 | -20.3 -> -23.8 | -8.8 (5.3) | -9.5 (5.2) | 7 -> 6 % |
| SC | Jermaine Johnson v Alan Wilson | 2 | -3.4 -> -10.9 | -9.6 (6.7) | -11.3 (6.6) | 8 -> 6 % |
| SD | Dan Ahlers v Larry Rhoden | 1 | -23.6 -> -23.9 | -10.0 (6.1) | -10.3 (6.0) | 7 -> 6 % |
| AR | Fredrick Love v Sarah Huckabee Sanders | 1 | -23.9 -> -28.4 | -9.4 (6.9) | -10.9 (6.9) | 9 -> 7 % |
| WY | Kenneth Casner v Eric Barlow | 0 | -14.3 -> -18.6 | -14.3 (14.1) | -18.6 (13.4) | 13 -> 8 % |
| AL | Doug Jones v Tommy Tuberville | 3 | -8.3 -> -11.0 | -8.5 (6.0) | -9.0 (5.9) | 8 -> 8 % |
| OK | Cyndi Munson v Mike Mazzei | 0 | -10.1 -> -13.3 | -10.1 (14.1) | -13.3 (13.4) | 20 -> 14 % |
| KS | Cindy Holscher v Ty Masterson | 6 | -3.3 -> -4.7 | -4.3 (4.8) | -4.5 (4.7) | 20 -> 20 % |
| TX | Gina Hinojosa v Greg Abbott | 40 | -14.8 -> -17.1 | -3.8 (4.5) | -4.1 (4.4) | 24 -> 23 % |
| NV | Aaron Ford v Joe Lombardo | 13 | -11.9 -> -13.3 | -3.7 (5.2) | -4.0 (5.1) | 24 -> 23 % |
| VT | Amanda Janoo v Phil Scott | 4 | -1.1 -> +0.7 | -4.2 (5.7) | -3.9 (5.6) | 24 -> 26 % |
| FL | David Jolly v Byron Donalds | 24 | -0.1 -> -0.7 | -1.5 (4.8) | -1.6 (4.7) | 38 -> 38 % |
| GA | Keisha Lance Bottoms v Rick Jackson | 13 | +1.8 -> +1.8 | +0.4 (4.7) | +0.4 (4.6) | 53 -> 53 % |
| OH | Amy Acton v Vivek Ramaswamy | 24 | -1.6 -> -2.5 | +2.1 (4.7) | +1.9 (4.6) | 66 -> 65 % |
| WI | David Crowley v Tom Tiffany | 7 | +2.0 -> -4.0 | +3.0 (4.9) | +2.2 (4.9) | 72 -> 66 % |
| OR | Tina Kotek v Christine Drazan | 5 | +23.3 -> +26.6 | +3.3 (5.3) | +4.1 (5.3) | 73 -> 77 % |
| CO | Phil Weiser v Victor Marx | 0 | +7.3 -> +14.8 | +7.3 (14.1) | +14.8 (13.4) | 72 -> 88 % |
| IA | Rob Sand v Zach Lahn | 17 | -1.9 -> +3.2 | +6.2 (4.6) | +6.8 (4.5) | 88 -> 89 % |
| NM | Deb Haaland v Gregg Hull | 4 | +7.5 -> +14.8 | +7.2 (5.2) | +8.2 (5.1) | 88 -> 90 % |
| AK | Jonathan Kreiss-Tomkins v Bernadette Wilson | 4 | -2.0 -> -3.1 | +7.6 (5.4) | +7.4 (5.4) | 91 -> 90 % |
| MI | Jocelyn Benson v John James | 23 | +2.2 -> +2.4 | +7.4 (4.6) | +7.4 (4.5) | 93 -> 92 % |
| AZ | Katie Hobbs v Andy Biggs | 21 | +17.6 -> +13.2 | +8.7 (4.9) | +8.2 (4.8) | 94 -> 93 % |
| MN | Amy Klobuchar v Lisa Demuth | 7 | +4.1 -> +10.9 | +9.3 (5.1) | +10.2 (5.0) | 95 -> 96 % |
| ME | Hannah Pingree v Robert B. Charles | 12 | +4.6 -> +5.5 | +10.8 (4.6) | +10.8 (4.5) | 97 -> 97 % |
| IL | JB Pritzker v Darren Bailey | 1 | +23.0 -> +26.2 | +23.0 (14.1) | +26.2 (13.4) | 95 -> 97 % |

Expected Democratic governors 26.8 before and after (80 % range 23-31), P(Democratic majority of governors) 67.2 % -> 67.1 %. The
2024 data update alone moves nothing measurable (67.2 % -> 66.9 %). Most polled races move a point or less; the prior moves the races
with few or no polls (Colorado has none in the feeds: Weiser, the attorney general, against Marx, who has held no office).

Data: `data/static/gov_results_2024.csv` (2024 results table), `gov_nominees.csv` (every nominee on the yearly results pages and the
linked article), `gov_candidate_quality.csv` (experienced or not, the qualifying office) and `gov_nominee_offices.csv` (the infobox
offices read, for audit) - facts derived from Wikipedia (CC BY-SA 4.0), built once in GitHub Actions by `gov_quality.py all` (583
articles in 13 MediaWiki API requests; the one-time workflow that ran it was removed after the build). A nominee without an article
counts as not experienced.

### Follow-ups (2026-10-05, evening)

Harness fix first: in 2010 the day before the election is Nov 1, so that date was scored twice. `gov_backtest.dates` now drops the
duplicate; every number above is from the corrected run (1,086 race-dates), and none of the conclusions changed.

**1. Same-state Senate and governor errors (implemented; switched OFF: `senate2026.JOINT_ON`).** Within a cycle, a state's Senate and
governor polling errors correlate 0.58. With the switch on (`joint_setup` before both statewide simulations), each race's own error is
split into c x a draw shared by the state's statewide races plus sqrt(sd^2 - c^2) x its own draw, with c capped at 0.9 sd. Each
race's variance is unchanged, so only the covariance moves. c = sqrt of the within-cycle covariance of the two offices' race-average
polling errors (`joint_state_sd`: 3.68 points on 1998-2022; walk-forward 3.1-3.7).
Joint test: the governor harness (adopted prior) paired with the private live Senate harness (prior refitted walk-forward, the same
E and dates), state by state, one joint simulation per pair with the shared national draw. n = 682 pair-dates, 142 state-years,
10 cycles; scores over the four outcomes (both D, D governor / R senator, R governor / D senator, both R):

| | off | on | difference (cycles better; permutation p / bootstrap p) |
|---|---|---|---|
| log loss, 4 outcomes | 0.3325 | 0.3347 | +0.0023 (5/10; 0.73 / 0.76) |
| Brier, 4 outcomes | 0.1793 | 0.1813 | +0.0020 (4/10; 0.47 / 0.40) |
| Brier of P(split ticket) | 0.0775 | 0.0792 | +0.0016 (5/10; 0.53 / 0.50) |
| mean P(split) (actual 24.9 %) | 27.4 % | 26.1 % | |
| simulated correlation of the pair | 0.31 | 0.56 | (backtest residuals: 0.32) |

Per-race numbers are unchanged to Monte Carlo noise (largest |dP| 0.014). No gain, so the switch stays off. The reason: the shared
national draw already gives a state's two races a correlation of 0.31, and the backtest residuals show 0.32. The simulated national
shock is larger than the realized cycle-wide miss, so it already carries the state part; adding the state term on top overstates
straight tickets. A re-split (smaller national shock plus a state term) would leave same-state pairs as they are and only move
cross-state correlations; it was not tested here.
2026 if switched on (Oct 5 inputs, E +8.79, the live statewide draw; states with both races and a split chance over 5 %; "D/I" = the
non-Republican Senate candidate, independents included):

| state | Senate | governor | P(Senate D/I) | P(governor D) | both D/I: off -> on | split: off -> on | both R: off -> on | corr off -> on |
|---|---|---|---|---|---|---|---|---|
| NH | Chris Pappas (D) v John E. Sununu | Cinde Warmington v Kelly Ayotte | 88 % | 4 % | 4 -> 4 % | 84 -> 84 % | 12 -> 12 % | 0.40 -> 0.75 |
| TX | James Talarico (D) v Ken Paxton | Gina Hinojosa v Greg Abbott | 72 % | 23 % | 20 -> 23 % | 55 -> 50 % | 25 -> 27 % | 0.38 -> 0.68 |
| GA | Jon Ossoff (D) v Mike Collins | Keisha Lance Bottoms v Rick Jackson | 97 % | 53 % | 52 -> 53 % | 45 -> 44 % | 3 -> 3 % | 0.41 -> 0.77 |
| IA | Josh Turek (D) v Ashley Hinson | Rob Sand v Zach Lahn | 61 % | 89 % | 57 -> 59 % | 35 -> 31 % | 7 -> 10 % | 0.41 -> 0.73 |
| KS | Adam Hamilton (D) v Roger Marshall | Cindy Holscher v Ty Masterson | 41 % | 20 % | 13 -> 18 % | 35 -> 26 % | 52 -> 56 % | 0.42 -> 0.78 |
| FL | Angie Nixon (D) v Ashley Moody | David Jolly v Byron Donalds | 23 % | 39 % | 14 -> 20 % | 34 -> 23 % | 52 -> 57 % | 0.40 -> 0.77 |
| NE | Dan Osborn (I) v Pete Ricketts | Lynne Walz v Jim Pillen | 36 % | 6 % | 4 -> 5 % | 34 -> 31 % | 62 -> 63 % | 0.37 -> 0.73 |
| AK | Mary Peltola (D) v Dan S. Sullivan | Jonathan Kreiss-Tomkins v Bernadette Wilson | 64 % | 90 % | 60 -> 62 % | 33 -> 28 % | 6 -> 10 % | 0.38 -> 0.72 |
| ME | Troy Jackson (D) v Susan Collins | Hannah Pingree v Robert B. Charles | 67 % | 97 % | 66 -> 66 % | 32 -> 31 % | 2 -> 3 % | 0.40 -> 0.72 |
| OH | Sherrod Brown (D) v Jon Husted | Amy Acton v Vivek Ramaswamy | 77 % | 64 % | 55 -> 60 % | 32 -> 22 % | 13 -> 18 % | 0.40 -> 0.75 |
| SC | Annie Andrews (D) v Darline Graham | Jermaine Johnson v Alan Wilson | 29 % | 6 % | 3 -> 4 % | 29 -> 26 % | 68 -> 70 % | 0.31 -> 0.61 |
| SD | Brian Bengs (I) v Mike Rounds | Dan Ahlers v Larry Rhoden | 26 % | 6 % | 3 -> 5 % | 26 -> 23 % | 71 -> 72 % | 0.30 -> 0.61 |
| OR | Jeff Merkley (D) v David Brock Smith | Tina Kotek v Christine Drazan | 98 % | 77 % | 76 -> 75 % | 24 -> 24 % | 1 -> 1 % | 0.15 -> 0.30 |
| MI | Abdul El-Sayed (D) v Mike Rogers | Jocelyn Benson v John James | 76 % | 92 % | 72 -> 75 % | 23 -> 18 % | 4 -> 7 % | 0.44 -> 0.78 |
| MN | Peggy Flanagan (D) v Michele Tafoya | Amy Klobuchar v Lisa Demuth | 83 % | 96 % | 81 -> 82 % | 17 -> 15 % | 2 -> 4 % | 0.37 -> 0.73 |
| OK | N'Kiyla Jasmine Thomas (D) v Kevin Hern | Cyndi Munson v Mike Mazzei | 1 % | 14 % | 0 -> 0 % | 14 -> 15 % | 85 -> 85 % | 0.09 -> 0.19 |
| CO | John Hickenlooper (D) v Mark Baisley | Phil Weiser v Victor Marx | 97 % | 89 % | 86 -> 86 % | 13 -> 13 % | 0 -> 0 % | 0.06 -> 0.13 |
| AR | Hallie Shoffner (D) v Tom Cotton | Fredrick Love v Sarah Huckabee Sanders | 7 % | 7 % | 1 -> 2 % | 12 -> 10 % | 87 -> 88 % | 0.25 -> 0.50 |
| NM | Ben Ray Luján (D) v Larry Marker | Deb Haaland v Gregg Hull | 98 % | 90 % | 89 -> 89 % | 11 -> 10 % | 1 -> 1 % | 0.24 -> 0.48 |
| IL | Juliana Stratton (D) v Don Tracy | JB Pritzker v Darren Bailey | 92 % | 97 % | 89 -> 89 % | 11 -> 10 % | 0 -> 0 % | 0.08 -> 0.15 |
| WY | James W. Byrd (D) v Harriet Hageman | Kenneth Casner v Eric Barlow | 2 % | 7 % | 0 -> 0 % | 9 -> 9 % | 91 -> 91 % | 0.05 -> 0.12 |
| AL | Everett Wess (D) v Barry Moore | Doug Jones v Tommy Tuberville | 1 % | 8 % | 0 -> 1 % | 8 -> 8 % | 91 -> 91 % | 0.27 -> 0.56 |
| ID | Todd Achilles (I) v Jim Risch | Terri Pickens v Brad Little | 7 % | 1 % | 0 -> 0 % | 8 -> 7 % | 92 -> 92 % | 0.24 -> 0.48 |

Headlines with it on are unchanged within noise: Senate 51.93 -> 51.92 Democratic seats, P(51+) 68.2 -> 68.1 %, P(Republicans lose
control) 70.1 -> 69.9 %; governors 26.8, P(majority) 66.9 -> 66.8 %. Each office's distribution is a sum over one office's races,
so a cross-office covariance cannot move it.
State legislatures (report only, unchanged). The legislative state swing residual (Klarner cells, `stateleg.fit_swing`) does track
the state's statewide polling miss: slope -0.31 (se 0.05) per point of poll - result error, r -0.31, 360 state-cycles 1998-2020
(-0.26 on the change from the previous cycle, n 224). But the model's existing link, kappa x the statewide surprise
(`MIDTERMS_STATELEG_KAPPA`), scores worse on the 2018 / 2022 backtest (39 chamber-elections). kappa 0: seat log loss 0.2166 / 0.1552,
control Brier 0.072. Fitted kappa (0.05 / 0.07): 0.2171 / 0.1555, Brier 0.074. kappa 0.31: 0.2290 / 0.1589, Brier 0.093. Not linked.

**2. Undecideds toward the trailing candidate (implemented; switched OFF: `model.UND_TRAIL_K = None`; the user's decision).** On, each
race poll's margin moves by k x margin x (100 - D - R) / (D + R) before the sponsor lean is removed. k < 0 moves it toward whichever
candidate trails. k comes from `model.und_trail_fit(before=cycle)`: 538 raw_polls, Senate and governor, non-partisan, last 60 days,
cycle fixed effects; walk-forward -0.96 (2006, from 1998-2004 only), -0.45 (2010), -0.25 (2014), -0.24 (2018, 2022), -0.22 (2024 and
live). Scored against the model as it is:

| harness | n | log loss | Brier | CRPS | margin MAE | 80 % cov. |
|---|---|---|---|---|---|---|
| governors (adopted prior) | 234 races, 1,086 race-dates | -0.0003 (2/10; 0.99 / 1.00) | +0.0001 (3/10; 0.51 / 0.72) | +0.18 (2/10; 0.008 / <0.001) | +0.24 (0.014 / <0.001) | 79.6 -> 76.0 % |
| Senate (private live harness, walk-forward prior) | 326 races, 1,528 race-dates | +0.0031 (1/10; 0.006 / <0.001) | +0.0006 (2/10; 0.031 / 0.002) | +0.26 (0/10; 0.002 / <0.001) | +0.34 (0/10; 0.002 / <0.001) | 79.6 -> 77.1 % |

It makes the race means worse in both offices. They are already too close to 50-50 (results run about 2 points more lopsided), and
the switch pulls them further in. Direction check: it is not a party lean. Mean shift of the race means is +0.03 points (governors)
and +0.05 (Senate); races the model had the Democrat leading move -0.66 / -0.51, races it had the Republican leading +0.65 / +0.65
(n 510 / 576 governor, 795 / 733 Senate race-dates). 2026 effect if switched on (live k -0.22): VT +0.5 points (25.5 -> 28.3 % for
Janoo), AK -0.3, NM -0.3, AL +0.3, NE +0.3, TN +0.6, ME -0.3; FL Jolly v Donalds +0.06 points (38.5 -> 38.9 %); Senate ID +1.0 points
(7.1 -> 8.5 %), all other Senate races within 0.25 points. Headlines unchanged (Senate P(51+) 68.2 -> 68.0 %, governors 66.9 -> 66.8 %).

**3. Poll entries checked.** Neither is a data error, so no override was added.
- Vermont, University of New Hampshire, Sept 17-21 2026: Janoo (D) 49, Scott (R) 43 (6 % undecided). Every source carries the same
  numbers and the same party labels: VoteHub (Janoo / Scott answers), Wikipedia's 2026 Vermont page (Scott (R) 43, Janoo (D) 49,
  829 LV), the New York Times feed via pollresults.org (D+6), and Polling USA on Bluesky. So the party and name mapping is right; it
  is a surprising published result, not a transcription error. It contrasts with Braun Research / Vermont Public in the same weeks
  (Scott 44-34 LV), and the robust blend weighs the two accordingly. One minor VoteHub slip: it gives UNH's June Scott-Janoo question
  n 511, where Wikipedia has 887 LV. Sample size is not used by the model.
- Illinois: the only general-election poll in any source (VoteHub, Wikipedia, pollresults.org) is Victory Research, Nov 20-24 2025,
  Pritzker 54-34. The other 2026 Illinois polls are Republican primary polls. It is past the 150-day window, so Illinois runs on its
  prior (Pritzker +26). That is the correct treatment of a race nobody has polled since November, not an error.

## Pollster shared error (implemented, switched off)

A pollster's house error is shared by all of its polls in a race, so four polls from one firm are not four independent reads
(Florida governor, October 2026: Change Research's four polls carried 44 % of the poll weight). `model.POLLSTER_SHARED_ERROR`
= tau splits each poll's error in the race blend (`robust_blend`) into its own part and a pollster-race part common to that
firm's polls, integrated out exactly; each poll's marginal variance is unchanged, so a firm with one poll in a race is treated
as before. tau is fitted on 538's raw_polls (`race_poll_calibration.pollster_shared_sd`: within-race pairs of polls at the same
time gap, different-pollster minus same-pollster squared error difference, lean-corrected, net of sampling): tau^2 = 7.7 +- 0.7,
tau = 2.8 points (2.77-2.83 on cycles before 2018-2024). On the leak-free backtests (Senate 2018-2024, governors 2018-2022, House
2018 and 2022; six dates from September 1 to November 1; tau from earlier cycles) it changed nothing measurable: log loss
Senate +0.0002 (95 % interval -0.0014 to +0.0016), governors -0.0006 (-0.0024 to +0.0008), House +0.0001 (-0.0001 to +0.0003),
Brier and the error of the race means likewise within noise. Not adopted: the switch stays off (`None`). Switched on, the
Florida governor race moves from 39 % to 34 % for Jolly; the national headlines move by less than half a point.

## Gallup party identification (tested, not adopted)

A one-off backtest (2026-10-05) asked whether Gallup's leaned party-ID gap (Democrats + Democratic leaners minus
Republicans + Republican leaners, adults) added to the generic-ballot average G at Oct 1 for the national House vote. Gallup's
quarterly series (the intended Q3 input) is not openly published (the trend page has annual averages 1991-2025; releases show
only a few recent quarters), so the annual series is the proxy: the election year's average (has Oct-Dec look-ahead), the
previous year's (leak-free) and the two-year change. Leave-one-election-out, 1996-2024 (n 15; no G for 1992/94 in the repository):
V ~ G MAE 1.91 / RMSE 2.29; + election-year gap 1.69 / 2.14 (coef 0.31 +- 0.20; better in 11/15, paired t on squared error p
0.31, permutation p 0.09, bootstrap 95 % of the MSE gain -0.6 to +1.8); + previous-year gap 1.60 / 2.08 (12/15, t p 0.37,
permutation 0.06, bootstrap -1.1 to +2.7); + two-year change 2.04 / 2.41 (worse). Midterms only (n 7): no variant helps (the
change is worse in 7/7). The House vote runs 4.4 points more Republican than Gallup's adult gap on average (sd 3.4, 1992-2024;
midterms -5.1, presidential years -3.8, difference not significant). No significant out-of-sample gain: not adopted. Today's Q3
2026 reading (D+10) would move the fitted national margin by +0.2 against the same fit without it.
The script and the per-year gap series are not distributed (Gallup does not allow republishing its tables); the figures
above are aggregates. Source: Gallup, "Party Affiliation" trend, retrieved 2026-10-05.

## Texas early-vote turnout (diagnostic)

**Off (user, 2026-10-05) until switched on.** `midterms/tx_early.py` is in the repository but makes no request and writes
nothing unless the repository variable `TX_EARLY` is set to `on` (the daily workflow's step is skipped otherwise, and the
script itself checks `TX_EARLY=on`). When on, it follows the Texas Secretary of State's daily county early-voting file for the
2026 general (early voting Mon Oct 19 - Fri Oct 30; the SOS "Early Voting Turnout" portal, `goelect.txelections.civixapps.com`,
county summary only, one conditional request per file per day) and writes `web/data/tx_early.json` (workflow artifact): early
votes (in person + mail ballots received) as a share of registered voters, statewide and for fixed county groups - big
Democratic counties (Harris, Dallas, Travis, Bexar, El Paso), Republican-leaning suburbs and exurbs (Collin and Denton, Trump +11
and +13 in 2024, Montgomery, and every other non-core county of the Dallas-Fort Worth, Houston and San Antonio metros with over
60,000 presidential votes that Trump carried by 15 points or more: Rockwall, Parker, Kaufman, Ellis, Johnson, Brazoria,
Galveston, Comal, Guadalupe; `data/static/tx_county_pres2024.csv`), the Rio Grande Valley and border (Hidalgo, Cameron, Webb,
Starr, Willacy, Maverick) and the swing suburbs Tarrant and Fort Bend - with each group's share of the statewide early vote and
the mail share.

The 2022 comparison is not built yet. The SOS's day-by-day 2022 portal (earlyvoting.texas-election.com) and its 2019-2024
results site (results.texas-election.com) answered GitHub Actions with a Cloudflare browser challenge (HTTP 403) on 2026-10-05,
which is not evaded. Planned (user's choice): the FINAL 2022 general early vote by county as the end point (on each 2026 day,
turnout against the 2022 final and the share of the 2022 final reached; after Oct 30 the like-for-like final comparison and the
composition shift), from the SOS's "Voter Registration and Unofficial Early Voting Figures by County" pages
(sos.state.tx.us/elections/historical/counties.shtml; early votes from unofficial election-night returns, no in-person / mail
split), checked against the 17,672,143 registered voters and the ~5.4 million early votes the SOS reported for 2022. Details
in the TODO in `tx_early._final_2022`.

Calendar: both early-voting periods start on the 17th day before Election Day moved to the next Monday and run 12 days, so day
k is the same weekday and the same number of days out in 2022 and 2026 (SB 2753 of 2025, which would change the period, is not
in effect for November 2026). Caveats (also in the file's `notes`): Texas has no party registration and no party data are used,
so this is turnout, not vote choice; mail voting is limited to voters 65 and over, disabled voters and a few other groups; HB
1217 (2023) extended weekend and last-week hours to small counties; counties' reports lag.

## Early-vote composition: North Carolina and Florida (diagnostic)

DIAGNOSTIC ONLY - nothing feeds the forecast or the website. `.github/workflows/early-diag.yml` runs daily at 01:50 UTC in its
own concurrency group, commits only `data/early/`, and every step is `continue-on-error` with a warning on failure. Runtime
about 35 s a day (North Carolina ~6 s, Florida ~2 s); the first run, which built the voter-history table, took ~3 minutes.

**North Carolina** (`midterms/nc_early.py`; race and ethnicity are self-reported on the NC voter record): accepted mail and
one-stop ballots cast by the same number of days before Election Day, 2026 against 2022 and 2024, for white, Black, other,
undesignated and Hispanic (ethnicity field, any race) voters - each group's share of ballots, its share of ballots divided by its
share of registered voters on the same days out (index), ballots per 1,000 registered, the share who skipped at least two of the
three previous generals (low propensity), and what the same voters did in the previous general of the same kind (by mail /
early in person / on Election Day / not at all; Election Day last time and early now is a mode shift, not new turnout). Inputs:
the NCSBE absentee file of 2026 (one conditional request per run), the committed 2022 / 2024 baseline, NCSBE weekly registration
statistics (cached), and the 2016-2024 generals reduced from NCSBE's voter-history file (voter-level, so it lives only in the
Actions cache under `nc-hist-generals-v1`, never committed or released; rebuilt from one ~350 MB download when that entry is
gone). Output `data/early/nc_early.json` and the day's table `data/early/nc/2026_<date>.csv` (later files show how far each day
filled in: ~1 % for the newest day). Traps handled in the code: 2024 codes one-stop as "EARLY VOTING", RegStat folded Asian /
multiracial / undesignated into "Other" until Dec 2023 (so those are one denominator group), 2022 mail ballots that arrived after
Election Day, keying errors in return dates. Calendar: mail went out 60 days out in 2022 and 2026 but ~42 days out in 2024;
one-stop opens 19 days out in all three years (Oct 15, 2026).

**Florida** (`midterms/fl_early.py`; party only - race is on the Florida voter record but only in the voter extract, which the
Division provides on request): the daily statistics files are snapshotted into `data/early/fl_stats/` (Florida keeps no
history, so a missed day is lost) and compared with the Wayback Machine's 2024 captures at the same days out, as the returns'
R-D margin, that margin minus the registration R-D of the latest month end, and minus the R-D of all mail ballots sent (with
each party's return rate). Output `data/early/fl_early.json`. Hurricanes Helene (Sep 26, 2024) and Milton (Oct 9, 2024) fall on
the 2024 baseline dates.

**Georgia**: not built. The free official per-voter absentee file has no race; the voter list with race is sold; the SOS site
and its turnout data hub (which shows turnout by race) answer automated requests with a browser challenge, which is not evaded.

## Heating-oil adjustment

A judgment term, not backtested: the rise in retail heating oil over the past year, times each district's (state's)
share of oil-heated homes, times a typical household's 650 gallons, at 0.78 generic-ballot points per dollar of gasoline
spread over 750 gallons, times the share not yet priced into the polls, against the president's party. The price is
EIA's weekly Maine No. 2 heating oil residential price. EIA's survey runs from October to March; outside the season the
last published week is held (the page says which week).

## State legislatures (experimental, on since 2026-10-05)

`midterms/stateleg.py` forecasts control of 22 chambers - Michigan, Minnesota, Wisconsin, Arizona, Pennsylvania, New Hampshire and
North Carolina (both chambers) the Georgia, Iowa and Texas Houses, and the California Senate and Assembly, Kentucky
Senate and House and Texas Senate (all added 2026-10-05) - inside the daily run (`MIDTERMS_STATELEG`, default on; workflow input `stateleg`). It
writes `state_legislatures` into `web/data/model.json`; `web/legislatures.html` shows it (not linked from the other pages yet).

**Seat model.** Each seat's expected Democratic two-party margin is `a + beta * lean + c * inc + g * E + kappa * shift_state`:
the district's 2024 presidential margin minus the national one (`lean`), incumbency (+1 / -1 / 0), the model's national environment
E (the same generic-ballot trend, no directional correction) and the state's own signal (how far its Senate / governor race polls pull
those races from their fundamentals priors, net of the national average pull). The simulation adds g x the House's national draw,
kappa x the same state's Senate/governor surprise in the same simulation, a state error shared by both chambers (partly chamber-specific)
and a t5 seat error. Multi-member districts (AZ House, NH House) elect their top-k candidates (party share +- the candidate's own
deviation from its slate, sd fitted, plus an incumbency bonus); a party with fewer nominees than seats can win at most that many. Seats
with only one major party on the ballot are fixed. PA and WI senates add the held-over seats (Open States holders). Ties: MI and PA
senates go to the party of the 2026 governor winner (the lieutenant governor), the NC Senate to the Democrats (Lt Gov Hunt), elsewhere
counted as shared.

**Fitted parameters** (`data/static/stateleg/params.json`; `python -m midterms.stateleg fit`): incumbency c = 6.0 pts, national slope
g = 0.77, 2-year state swing error 4.8 (state error 3.4), between-chamber correlation 0.62 - Klarner returns 1972-2022, 42,851
consecutive same-map district pairs; lean slope beta = 0.91, intercept a = 2.9, seat error 5.8 - 2018 (2016 lean) and 2022 (2020 lean)
results; kappa = 0.074 (se 0.024, 153 state-years: Klarner 4-year legislative swing residuals v the change in the same state's
governor-race residual); slate sd 8.6 % of the party mean (11,276 multi-member slates).

**Backtest** (`python -m midterms.stateleg_backtest`, `data/static/stateleg/backtest.json`): 2018 and 2022 on Oct 1, Klarner parameters
from earlier elections only, the lean slope from the other cycle, E = the 538 generic-ballot average with no correction, who is on
the ballot from the candidate records. 24 chamber-elections with full district data: control Brier 0.119 against 0.250 for "the current
majority holds" (calls right 75 % for both), seat count inside the 80 % range 22 of 24; seats (contested slots) Brier 0.049 (2018) and
0.046 (2022) against 0.103 / 0.069 for each seat staying with its last winner (2018) or the sign of its lean (2022). The state-signal arm
(kappa x governor-poll pull) changed seat log loss by +0.002 (2018) and -0.001 (2022) and control Brier 0.119 -> 0.125: no measurable
gain, so the state-poll signal is NOT used (tested, not adopted; `MIDTERMS_STATELEG_KAPPA=on` restores it). No published forecaster's chamber calls were scored (none available under a usable licence).

**Georgia, Iowa and Texas Houses** (same method and parameters): backtest reported separately - 5 chamber elections with complete
district data (GA 2018 missing two districts' leans): all called right (as the current-majority rule), Brier 0.006, seat count inside
the 80 % range 5/5. Adding them re-draws the simulation, so the 14 original chambers now read Brier 0.118 (naive 0.250), calls 79 %
(naive 75 %), 23/24 inside the 80 % range (Monte Carlo noise against the first run's 0.119 / 75 % / 22). Candidates: GA from the Georgia
Secretary of State's official results of the May 19 primary and June 16 runoff (`midterms/ga_sos.py` -> `data/static/stateleg/
ga_house_nominees_2026.csv`, derived table with source and retrieval date; 119 contested, 40 D only, 21 R only, against 90 / 43 / 47 in
2024; cross-check with Wikipedia and Open States in `ga_house_crosscheck.csv` - every difference is a retirement, a primary defeat, a
name spelling, or Wikipedia's district-3 box that belongs to district 4; the SOS table wins); TX from the district election boxes (a seat
is fixed only where the general-election listing lacks a party); IA from district prose (retirements
read; nominees mostly not, so seats count as contested unless the page says a party has none).

**Coverage scan and added chambers (2026-10-05).** `midterms/stateleg_scan.py` read every 2026 chamber page
(`data/static/stateleg/coverage_scan.csv`) and checked the candidates (`stateleg_scan.check` -> `pending_check.csv`): a chamber is added
only when the page has a general-election slate for every seat up (a missing party then really means no nominee), the seats up match
the 2024 rule, every district has a lean on the current map (TIGER 2022-2025 unchanged; for the 2026 Senate seats the House-cell method
reproduces known 2024 Senate leans to rms 0.9 CA, 1.6 KY, 0.9 TX), and the one-party seat count is plausible against 2024 (MEDSL) and
2022 (Klarner). Added: CA Senate (1 one-party seat v 4 / 5), CA Assembly (12 v 17 / 22), KY Senate (10 v 12 / 11), KY House (52 v 56 /
54), TX Senate (3 v 5 / 13 of 31). Backtest of the five (10 chamber elections): all called right, as the current-majority rule; seat
count inside the 80 % range 8 of 10 (2018 CA Assembly -6, 2018 KY House +12). Held back: CT Senate and House (2024 MEDSL party coding
under fusion voting gives no usable comparator), UT and WV Senates (one-party seats far below 2022/2024), IA Senate (district 31 missing
from the page), WA and OR Senates (lean check failed: rms 4.0 and 8.9).

**Approximations.** NH House: candidates are not listed on Wikipedia, so every seat is contested by full slates, floterial districts are
simulated as ordinary multi-member districts, and the 2018 backtest has no NH House (MEDSL's 2018 files cannot be downloaded); flagged
experimental. MI Senate: no 2024 Senate election and the 2026 court-ordered redraw of the Detroit-area districts is not in the Census
TIGER files yet; leans are VEST 2020 precincts moved to 2024 by each House district's measured swing on the 2022 map (the same method
reproduces known 2024 Senate leans with rms 0.3-1.8 in PA, GA, IA, TX, NC). PA odd Senate seats use the same method on the current map.
MN Senate candidates are not on Wikipedia: incumbents are assumed to run unless listed as retiring.

**Data** (`midterms/stateleg_build.py`, one-time, GitHub Actions `.github/workflows/stateleg-data.yml`; downloads cached, never committed):

| file | contents | source |
|---|---|---|
| `stateleg/lean_2026.csv` | 2024 president by 2026 district (MEDSL precinct rows joined to the same precinct's legislative labels; MN/WI senates from nested House districts; AZ House = LDs; MI Senate / PA odd seats as above) + a spatial cross-check | MEDSL 2024 precinct returns (CC0), VEST 2020 (CC BY 4.0), TIGER (public domain) |
| `stateleg/lean_hist.csv` | 2016 president on the 2018 maps, 2020 president on the 2022 maps | VEST 2016/2020 x TIGER 2018/2022; NH House by town names x MEDSL 2022 labels |
| `stateleg/medsl_results.csv.gz` | candidate-level legislative results 2022, 2024 | MEDSL (CC0) |
| `stateleg/klarner.csv.gz` | Klarner returns aggregated to district-elections, all states 1972-2022 | Klarner, doi:10.7910/DVN/FJOGJB (CC0) |
| `stateleg/openstates_current.csv` | sitting legislators | Open States people (CC0) |
| `stateleg/qc.json` | coverage, cross-checks, map-change audit | - |
| `stateleg/geo_audit.json` | district-map join audit: every modelled district key against the map polygons (the build fails on a gap) | - |
| `web/data/stateleg_geo/{ST}.topo.json`, `index.json` | district maps for `legislatures.html`, one TopoJSON per state (`midterms/stateleg_geo.py`, GitHub Actions `.github/workflows/stateleg-geo.yml`) | U.S. Census Bureau TIGER/Line Shapefiles 2025, state legislative districts (public domain); large water cut with the Census 2024 cartographic state outline; NH floterials from MEDSL 2024 precinct labels |

Candidates and retirements are read each day from the Wikipedia 2026 chamber pages (`midterms/stateleg_wiki.py`, cached like every page).

**District maps** (`legislatures.html`): each chamber's districts shaded by the chance a Democrat wins the seat (multi-member: the expected
Democratic share), hatched where only one party is on the ballot, dotted where the seat is not up in 2026 (PA and WI odd-year senate
seats, by the current holder). The daily run writes the per-seat file `web/data/stateleg_seats.json` (`midterms/stateleg_seats.py`:
P(D), expected margin, lean, fixed party, expected seats of k, members, nominees). Boundaries: Census TIGER/Line 2025 (= 2024 for every
chamber here, so the maps in force for 2026), except the Michigan Senate's court-ordered 2026 Detroit-area redraw, which is not in the
Census files and is flagged on its map. New Hampshire floterials are not in TIGER: each is the union of the base districts whose towns
vote in it (all 39 match whole base districts exactly) and is drawn as an outline over them. The geometry workflow reruns when
`stateleg.py` or `lean_2026.csv` changes and builds only when a chamber lacks polygons, so added chambers get maps automatically.

## Congressional-district lean from public data (built, not used)

`midterms/cd_lean_build.py` -> `data/static/cd_lean_2026.csv`: 2024 and 2020 presidential two-party margins for all 435 seats on the 2026
maps, and a 75/25 lean like Cook's, as a replacement candidate for Cook PVI (proprietary). Redrawn states are found from the Census 119th
v 120th Congress block equivalency files (AL CA FL LA MO NC OH TN TX UT). Unchanged states: MEDSL 2024 precinct rows by their U.S. House
label, where the file matches the official state totals (MIT) and the presidential vote sits on labelled precinct rows. Redrawn states
(and files that fail those checks): VEST 2020 precincts -> 2020 blocks (internal point, split by block population) -> 120th-Congress
districts, carried to 2024 by the (county x 2024 district) swing on the labels, else the county's, else the state's. Each state is then
calibrated to its official totals (`qc_cd.json` lists every check and shift). `MIDTERMS_CD_LEAN=ours` switches the whole House prior to it (off). The Census 120th-Congress block file does not
follow court orders: `cd_lean_build.MAP_STATUS` records the map in force per state with a dated source (`qc_cd.json` -> map_status).
Missouri uses its 2022 lines (the U.S. Supreme Court blocked the 2025 map on 2026-09-25); the other redrawn states use the 120th file
and are flagged `map_verified = False` (block-level match to the map in force not checked; Louisiana's map is unresolved). No House
seat lean is overridden.

Against the Cook PVI the House model uses (x2, margin units): seats on unchanged maps agree to rms 0.9 points (r 0.9996; Cook rounds to
whole PVI points); redrawn seats rms 3.8, almost all of it Missouri, where the seat table's Cook values look like the old map's (MO-5:
Cook D+12 v ours R+8); without Missouri rms 1.1 over all 435. The block-and-swing route reproduces known 2024 legislative-district
margins to rms 1.9 (PA House, WI Assembly; districts far smaller than a congressional district). Switched on offline (Oct 5 inputs):
Democratic House seats 241.1 -> 241.2, majority 88.5 % -> 88.0 %; MO-5 1.00 -> 0.48, FL-22 +0.17, AL-2 -0.14, MO-2 -0.15. A backtest of
the switch needs the same lean on the 2018 and 2022 maps (VEST 2016/2020 x TIGER CD116/CD118) and the House prior refitted on it in
place of 538's partisan lean.

## Deep review changes (user decision 2026-10-09)

A review on live-path, walk-forward harnesses (Senate 2006-2024, 10 cycles, 1,615 race-dates; House 2018/20/22; governors 2006-2024)
led to three changes, each behind its constant:

- **Senate prior with time-varying slopes** (`senate2026.SEN_CONST / B_LEAN / C_INC / NAT_SLOPE / PRIOR_SD`). The Senate has nationalized:
  per-cycle fits show incumbency falling ~3.8 points per decade, the lean slope rising ~0.11 and the residual sd falling ~1.35. The prior
  now has linear time trends in the lean and incumbency slopes (fitted on every D-v-R race 1982-2024), the national slope of the last 10
  cycles and a trend-extrapolated residual sd; at 2026: constant 0.70, lean 0.836, incumbency 6.7, national 0.794, sd 8.8 (was 2.22 /
  0.788 / 10.0 / 0.795 / 12.0). Against the pooled-10-cycle rule, walk-forward: log loss -0.0080 (7/10 cycles; sign-flip p 0.059, race
  bootstrap p < 0.001), Brier -0.0026, CRPS -0.23; 2014-2024 better in 6/6 cycles, worse in 2006/2008/2012. The gain is largest in cycles
  whose polls overstated Democrats; unpolled races show the same time pattern.
- **Senate shared / race error re-split** (`senate2026.STATE_SHARED_MISS` 4.2 -> 3.1, `RACE_EXTRA` 0 -> 2.83). The cycle-wide statewide
  miss is 3.1 over 1998-2022 (RMS about zero, no direction) and the model's implied correlation between race errors was 0.34 against 0.13
  measured. Each race keeps its total spread; only the correlation falls. Race scores unchanged; seat-count PIT sd 0.265 -> 0.299
  (uniform 0.289). Governors keep their shared miss (`GOV_SHARED_MISS` 4.2).
- **Generic-ballot house effects shrunk less** (`generic.LAMBDA_HOUSE` 4 -> 1): predicting the next 24 days' polls, weighted RMSE 2.623 ->
  2.551 (23/28 cases, 4/4 years; not significant on its own, year-block t 1.95).

Also: governor `n_polls` counts only polls inside the 150-day window, as the Senate and House do. Oct 9 inputs, before -> after: generic
trend 9.07 -> 9.29; House 242.1 -> 242.9 seats, majority 0.895 -> 0.901; Senate control 0.714 -> 0.727 (KS 0.45 -> 0.38, AK 0.64 -> 0.59,
OH special 0.76 -> 0.72, ME 0.72 -> 0.74); governors 26.9, majority 0.677 -> 0.681. Tested and not adopted in the same review: dropping the
midterm poll-error x0.8 (worse in 5/5 midterms), a larger race poll error, a larger House residual or state error, more House / governor
poll weight (not significant).

## Credits

Model and code: Shawn Corvec. Poll data belong to their pollsters. Governor results and nominees' offices: Wikipedia (CC BY-SA 4.0). Ranked-choice transfer rates are derived from the Maine
Secretary of State's and the State of Alaska Division of Elections' official ranked-choice tabulations. See `web/about.html` ("Sources and credits") for the
full list. Not affiliated with or endorsed by any of the sources.
