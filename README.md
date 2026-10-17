# 2026 U.S. midterm forecast

[![CI](https://github.com/scorvec/midterms-2026/actions/workflows/ci.yml/badge.svg)](https://github.com/scorvec/midterms-2026/actions/workflows/ci.yml)
[![daily forecast](https://github.com/scorvec/midterms-2026/actions/workflows/daily.yml/badge.svg)](https://github.com/scorvec/midterms-2026/actions/workflows/daily.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Monte Carlo forecast of the 2026 U.S. House, Senate and governor elections, with an experimental forecast of 22 state
legislative chambers. It is published at [scorvec.com/midterms](https://scorvec.com/midterms/); the reader-facing
explanation is [How the forecast works](https://scorvec.com/midterms/about.html) (`web/about.html`).

Each day the model re-reads the polls, refits a national generic-ballot trend from individual polls, blends every race's
polls with a fundamentals prior and simulates the elections 20,000 times with correlated errors: a national miss, a
statewide miss, regional and demographic swings, and each race's own error. The whole run happens in GitHub Actions and
commits its outputs to `web/`; the pages are static, and every position of the "national mood" slider is precomputed.

| page | what it shows |
|---|---|
| `web/index.html` | the forecast: chamber and governor odds, seat distributions, every race and how each has moved |
| `web/about.html` | how the forecast works, the track record and every source |
| `web/polls.html` | every poll and the weight it received in its race |
| `web/whatif.html` + `whatif_engine.js` | re-runs the simulation in the browser under the reader's own assumptions |
| `web/legislatures.html` | state legislative chambers (experimental), with district maps |

## How the model works

**National environment** (`generic.py`, `national_mood.py`). The generic-ballot trend is fitted to individual polls, not to
other people's averages: a penalised piecewise-linear trend (knots every 14 days), one house effect per pollster
(ridge-shrunk, centred), likely / registered / adult offsets and a partisan-sponsor offset. Each poll sits at the
midpoint of its field period, and the versions of one survey (for example likely and registered voters) count as one
poll. The trend is taken as it stands: there is no correction for the direction of a polling miss.
The size of past misses, in either direction, sets the national error s(L), which grows with the days L to the election
(fitted on the 15 House elections 1996-2024; `data/static/national_mood_fit.json`). The part of that error that is late
opinion movement is scaled state by state by how much of the vote is already banked (`early_vote.py`).

**House** (`model.py`, `run2026.py`). Each seat's expected Democratic two-party margin starts from a fundamentals prior:
the district's partisan lean (our own, from precinct returns: 0.75 x its 2024 presidential margin against the nation's +
0.25 x the same for 2020, on the 2026 maps; `cd_lean_build.py`), incumbency and the nominees' fundraising through June
30 (`money.py`), fitted on the contested seats of 2018 and 2022 with the same lean on those elections' maps, plus the
national environment. Demographic terms carry the Hispanic and Asian swing since 2024, with Texas, Florida and Western
Hispanic factors, white non-college and urbanization factors; the district polls update those factors
(`model.factor_update`). Race polls are corrected before they are blended, with the corrections measured on 538's
archive of rated polls (`race_poll_calibration.py`): sponsored polls are shifted and down-weighted, each pollster's
measured lean is removed, polls with many undecided or third-party respondents and polls from inexperienced pollsters
get more variance, older polls fade (63-day half-life, 150-day window), and several versions of one survey count once.
The blend is a Student-t (robust) update, so a poll that disagrees with everything else counts for less until other
polls confirm it.

**Senate** (`senate2026.py`). The same pipeline with a prior refitted on every Democrat-v-Republican Senate race
1982-2024, with linear time trends in the lean and incumbency slopes (the Senate has nationalized). Independent
challengers (a wider prior; 12 points more for one with no Democrat on the ballot and at least a quarter of the Republican's
money, see [Independent challengers](#independent-challengers)), appointed incumbents and split anti-Republican fields have
their own rules. A common trend estimated across
all of this year's Senate polls brings older polls up to date. Control is counted the way it is settled: 50-50 goes to
the Republicans while the vice president is a Republican, and independent winners are assumed to abstain (the page also
shows the answer if they side with either party).

**Governors** (`gov2026.py`). A prior fitted on every governor race 1998-2024 - the lean slope with a time trend, elected
and successor incumbents, and the nominees' prior office (`gov_quality.py`) - with the Senate's poll corrections, poll
errors and blend (polls from VoteHub and the Wikipedia race pages).

**Ranked-choice races** (`rcv.py`). In Maine (Senate, both House seats) and Alaska (Senate, House, governor) a poll's own
final round is used where it reports one; a first-round-only poll is converted to an expected final-round margin with
transfer rates measured on the official round-by-round tabulations, and the spread of those rates adds to the race's
polling uncertainty.

**Simulation.** 20,000 draws with Student-t (5 df) errors: a national draw for the House, a statewide draw shared by the
Senate and governor races (correlated 0.3 with the national draw), a state draw, urbanization and demographic-group
factors, and each race's own error. A slider position is a shift of the same draws (House margins one for one, Senate
and governor races by their fitted national slopes), so moving the slider changes the answer only through the
assumption. A forward heating-oil adjustment (below) is added after the poll blend.

**State legislatures** (`stateleg.py`, experimental). Seat-by-seat simulations of 22 chambers on the same national draw:
the district's 2024 presidential lean, incumbency and the national environment, a state error shared by both chambers
and a seat error, with multi-member districts, held-over senate seats and tie rules handled per chamber.

**Diagnostics that do not feed the forecast.** Early and mail ballots by race, party or composition in North Carolina,
Florida, Georgia and Pennsylvania; Texas early-vote turnout (off by default); voter registration in North Carolina and
Pennsylvania; same-pollster poll-to-poll changes. Details below.

## Validation

Changes to the model are scored on leak-free, walk-forward harnesses that run the live code as of past dates with only
the information available then. [Model notes](#model-notes) records the scores, including what was tested and left off.

- **House and Senate** (the track record on the about page, `build_web.BACKTEST_NOTE`; computed before the Senate error
  re-split below, with a shared statewide miss of 4.2, and with the 538 / Cook district leans the House prior used
  before its own lean - on the live-path harness that switch moved the September 15 calls by about a seat, see [District
  lean](#district-lean-the-house-prior)). House, each year forecast from the other years' data: on September 15 the
  model gave Democrats 227 seats (10-90 % range 202-257) in 2018 against 235 won, and 213 (191-234) in 2022 against 213;
  over 12 dates from July to November the result fell inside the 10-90 % range every time. Senate, 2018-2024 at 6 dates
  each: the seat count fell inside the 10-90 % range at all 24; in all four cycles the Senate polls missed toward the
  Republicans - a direction the model deliberately does not assume.
- **Governors** (`gov_backtest.py`): every race 2006-2024 at up to six dates (234 races, 1,086 race-dates, 10 cycles);
  see [Governor model review](#governor-model-review) for the scores.
- **State legislatures** (`stateleg_backtest.py`): 2018 and 2022 on October 1; see [State legislatures](#state-legislatures-experimental).
- **Ranked-choice transfers** (`python -m midterms.rcv --backtest`): each count predicted from its official first round with
  rates fitted on the other counts (`web/about.html#rcv`).
- **Reconstructed history** (`backfill.py`): the history lines on the forecast page are the current model run as of
  every past date with only the polls released by then. `python -m midterms.backfill --check` reruns today's
  reconstruction and compares it with `web/data/model.json`.

Where cycles are compared, significance is judged per election cycle, since a cycle shares one national miss: an exact
sign-flip permutation of the cycle means and a race-cluster bootstrap stratified by cycle.

## The daily run

`.github/workflows/daily.yml` is scheduled for 02:30 UTC (22:30 New York time, after the day's poll releases; GitHub
can start scheduled runs late). It runs `python -m midterms.bootstrap`, then `python -m midterms.weekly --daily`:

1. Downloads the fixed public inputs into `data/raw/` if they are missing (`bootstrap.py`, checksums verified).
2. National polls: VoteHub's open API (generic ballot, approval and race-poll catalogues), Polling USA and Political Poll
   Bot posts on Bluesky, pollresults.org and hand-entered releases; `generic.py` refits the generic-ballot trend.
3. UF Election Lab early-vote counts (`early_vote.py`) and EIA's weekly residential heating-oil price (`heating_oil.py`).
4. Wikipedia: the House seat table (2026 maps, incumbents, nominees), race ratings, every Senate and governor race page and the
   50 state House-election pages; VoteHub and pollresults.org race polls; the Bluesky feeds (`refresh.py`).
5. `build_web.py`: the House, Senate, governor and state-legislature simulations -> `web/data/model.json`, `polls.json`,
   `whatif.json`, `stateleg_seats.json`.
6. The daily snapshot `web/data/history/model_<date>.json`; the reconstructed history (`backfill.py`, rebuilt only when the
   methodology files change), `web/data/race_history.json` and `web/data/summary.json` (the site's homepage panel).
7. Diagnostics that do not feed the forecast (Florida and Pennsylvania early / mail vote, North Carolina and Pennsylvania
   registration on Mondays, same-pollster deltas); they are kept in the Actions cache or uploaded as a workflow artifact,
   never committed.
8. On Mondays, `reports/weekly_<date>.md`: which races moved in the past week and why.

**Publish gate** (`weekly.publish_gate`). The outputs are held when a race that had polls suddenly has none, the race-poll
count falls by more than 10 %, or the generic-ballot poll count falls - usually a Wikipedia table that stopped parsing. The
workflow then commits only the poll records in `data/state/` and fails; `force_publish` on a manual run overrides it.

**Commit.** The outputs are snapshotted, then on each push attempt the job resyncs to the newest commit of the branch,
restores the snapshot and commits (no rebase, no merge of stale trees). `data/raw/` and `data/cache/` persist between runs
through `actions/cache`. A run takes a few minutes on a normal day and about 20 minutes when the reconstructed history is
rebuilt or the download cache is cold.

**Publishing.** The website copies the pages and `web/data/` from this repository at the commit SHA of `main`
(`site_integration/midterms-sync.yml` is a reference copy of that workflow) and refuses a forecast that is broken or
older than the one it has.

Other workflows:

| workflow | what it does |
|---|---|
| `early-diag.yml` | daily early-vote composition diagnostics for North Carolina, Florida and Georgia; commits `data/early/` only |
| `stateleg-geo.yml` | builds the district maps `web/data/stateleg_geo/` from Census TIGER/Line when the chamber list or the district leans change |
| `stateleg-data.yml` | one-time builds of the state-legislature tables in `data/static/stateleg/` (triggered from a feature branch) |
| `cd-lean.yml` | manual: the congressional-district leans (`cd_lean_build.py`) on the 2026 maps or on the 2018 and 2022 maps |

## Run it yourself

Python 3.11 and network access:

```
pip install -r requirements.txt
python -m midterms.bootstrap            # fixed public inputs into data/raw/ (once)
python -m midterms.weekly --daily       # the daily run: fetch, fit, simulate; writes web/data/
python -m http.server -d web            # serve the pages, then open the printed address
```

The first run downloads every source page and takes longer than a daily run. Individual pieces:

```
python -m midterms.generic                      # generic-ballot and approval trends -> data/cache/generic_model.json
python -m midterms.build_web                    # simulate from the cached inputs -> web/data/
python -m midterms.backtest 2022 2022-09-15     # House model as of a date in a past cycle, scored
python -m midterms.gov_backtest all             # governor harness: every variant, then the report
python -m midterms.stateleg_backtest            # state-legislature backtest
python -m midterms.backfill --check             # today's reconstruction against web/data/model.json
```

Tests (`tests/`) need only the committed files and no network, and take a few seconds; CI runs them with the ruff checks
on every code change (`.github/workflows/ci.yml`):

```
pip install pytest
pytest
```

Switches read from the environment: `MIDTERMS_STATELEG` (state legislatures, default `on`), `MIDTERMS_STATELEG_KAPPA`
(the tested-and-rejected state-poll signal, default `off`), `MIDTERMS_RCV` (ranked-choice handling, default `on`),
`TX_EARLY` (Texas tracker, default off) and `MIDTERMS_FORCE_PUBLISH`. Model switches that were tested and left off are
module constants, each documented where it is defined.

## Repository layout

| path | what |
|---|---|
| `midterms/` | the model (Python 3.11; `requirements.txt`); `paths.py` defines the data layout |
| `web/` | the five pages and `whatif_engine.js`; `web/data/` holds the published outputs |
| `tests/` | the test suite: every module imports, the early-vote profile and exposures, release dates, simulation pieces |
| `data/static/` | small inputs derived once from public sources (below); committed |
| `data/state/` | poll records the daily run accumulates (the feeds only reach back a few weeks); committed |
| `data/manual/` | hand-entered polls (releases not yet in any feed) and checked corrections to scraped polls, with source URLs |
| `data/early/` | early-vote diagnostics written by `early-diag.yml`; not used by the forecast or the website |
| `data/raw/`, `data/cache/` | downloads and intermediate files; git-ignored, kept between runs by `actions/cache` |
| `reports/` | weekly change reports |
| `site_integration/` | reference copy of the website's sync workflow |

## Data sources and licences

Committed inputs (`data/static/`):

| file | contents | source / licence |
|---|---|---|
| `district_groups_2026.csv`, `state_groups.csv`, `district_wnc_2026.csv` | citizen voting-age population by Hispanic / Asian origin group, white non-college share, per 2026 district and state | U.S. Census Bureau ACS 2020-2024 5-year tables + PUMS, 120th-Congress block equivalency (public domain); built by `demog.py` |
| `district_heat_oil_2026.csv`, `state_heat_oil.csv` | share of households heating with fuel oil / kerosene | ACS B25040 (public domain); `demog.heat_oil_shares` |
| `urbanization_2026.csv` | urbanization index on the 2026 maps | 538 urbanization index 2022 (CC BY 4.0) carried to the new maps with Census block-group populations; `urban.py` |
| `early_share_2022.json` | each state's share of 2022 ballots cast before Election Day | U.S. EAC 2022 EAVS (public domain); `early_vote.py` |
| `early_profile.json` | the timing profile of pre-Election-Day ballots (W, P of h(tau) = (1 - tau/W)^P) with the derived shares it was fitted to | UF Election Lab national early-vote tables (2024 via Wayback Machine captures; shares only, the tables are not redistributed); `python -m midterms.early_vote profile` |
| `fec_june30_{2018,2022,2026}.csv`, `fec_senate_june30_2026.csv`, `fec_reports_2026.csv`, `fec_nominees_2026.csv` | candidate fundraising through June 30 (House nominees; Senate nominees for the funded-independent rule) | FEC / OpenFEC (public domain); the House files are built by `fec_money.py` |
| `house_national_vote_1946.csv` | national House vote by party since 1946 | Wikipedia election pages (CC BY-SA 4.0) |
| `national_mood_fit.json` | fitted coefficients only: the national-vote error s(L) (and the unused directional and approval variants), the cycles used and the late-movement slope | fit of the generic-ballot average to the House vote, 15 cycles 1996-2024 (538 / ABC News averages and poll archives, HuffPost Pollster archives, Gallup approval). The underlying table is not distributed; `python -m midterms.national_mood --refit` rebuilds the fit from it |
| `gov_results_2024.csv`, `gov_nominees.csv`, `gov_candidate_quality.csv`, `gov_nominee_offices.csv` | 2024 governor results; every governor nominee 1998-2026 with the linked article; whether each had held statewide elected office or a seat in Congress, and the infobox offices read | Wikipedia yearly "United States gubernatorial elections" pages and the nominees' articles (CC BY-SA 4.0); `gov_quality.py` |
| `rcv_transfers.csv`, `rcv_rates.json`, `rcv_backtest_results.csv`, `rcv_backtest_polls.csv` | ranked-choice transfers per eliminated candidate, the fitted rates, the leave-one-race-out backtests | official round-by-round tabulations of the Maine Secretary of State (2nd district 2018, 2022) and the State of Alaska Division of Elections (August 2022 special general, 2022 and 2024 generals), public records; `python -m midterms.rcv --fetch` downloads them into `data/raw/rcv/` and rebuilds the tables |
| `cd_lean_2026.csv`, `qc_cd.json` | the House prior's district lean on the 2026 maps: 2024 and 2020 presidential margins for all 435 seats, with every check | MEDSL 2024 precinct returns (CC0), VEST 2020 (CC BY 4.0), Census block equivalency files (public domain); `cd_lean_build.py` |
| `cd_lean_hist.csv`, `qc_cd_hist.json` | the same lean on the maps of the 2018 election (2016 and 2012 president) and the 2022 election (2020 and 2016), on which the prior is fitted, with every check | VEST 2016 and 2020 (CC BY 4.0), Census TIGER/Line 116th-Congress districts, 118th-Congress block equivalency file and 2020 blocks (public domain), MIT state totals (CC0); 2012 county results from Wikipedia's state pages (CC BY-SA 4.0, so the 2012 columns are CC BY-SA 4.0); `cd_lean_build.py hist` |
| `stateleg/` | state-legislature leans, results, sitting legislators, fitted parameters, backtest and audits | see [State legislatures](#state-legislatures-experimental) |
| `nc_early_baseline.csv` (+ `.qc.json`) | North Carolina accepted mail / one-stop ballots of the 2022 and 2024 generals by days out and race / ethnicity group, with propensity counts and the registered voters of each group | NC State Board of Elections absentee files, voter-history file and registration statistics (public records); aggregates only, `python -m midterms.nc_early baseline --src DIR` |
| `fl_wayback/`, `fl_registration.csv` | Florida statistics files of the 2024 and 2022 generals; active registered voters by party at month ends | Florida Division of Elections (public records); the 2024 / 2022 files as captured by the Internet Archive's Wayback Machine, the registration figures from the monthly county-and-party reports |
| `ga_wayback_2022.json`, `ga_county_baselines.csv` | Georgia 2022 early-vote composition at each day of in-person voting; each county's 2022 governor and 2024 presidential margin | georgiavotes.com 2022 pages as captured by the Wayback Machine; MEDSL 2022 returns and 2024 county presidential results |
| `tx_early_sources.json`, `tx_county_pres2024.csv` | sources for the Texas tracker; 2024 presidential votes by Texas county | Texas Secretary of State (public records); MIT Election Data and Science Lab 2024 precinct returns (doi:10.7910/DVN/NYTPDU, CC0), summed by county |

`web/data/districts_2026.topo.json` (district shapes) is built by `district_shapes.py` from Census 2020 cartographic block
groups and the 120th-Congress block equivalency files (public domain).

`data/state/` holds poll toplines as posted by the feeds (Polling USA, pollresults.org - poll data from The New York Times,
CC BY 4.0), `race_poll_seen.csv` (the date each race poll first appeared on the forecast page), the Center Square generic
ballot series used to confirm held polls (`source_watch.py`) and `pollster_scores.csv` (a numeric score per pollster from
pollresults.org's letter grades; recorded only, not used to weight polls; the grades themselves are not committed).

Downloaded at run time (never committed): the 538 poll archives and data repository files (CC BY 4.0), FEC results
workbooks, MIT Election Lab presidential returns (CC0), VoteHub, Wikipedia (CC BY-SA 4.0), Bluesky posts, pollresults.org,
UF Election Lab early-vote counts (CC BY-NC-ND 4.0 - used only to compute each state's scaling, never republished), EIA
heating-oil prices (public domain), Florida Division of Elections and Pennsylvania Department of State files (and, when
switched on, the Texas Secretary of State's 2026 early-voting county files). Race ratings from Cook, Sabato and Inside
Elections are read from Wikipedia's ratings tables and shown for comparison only. Every poll belongs to its pollster.

### Fetching politely

`fetch.py` sends one identifying User-Agent, `scorvec.com midterm model (+https://scorvec.com/midterms/about.html)`, which
the bootstrap, registration and early-vote downloads also use. Pages are fetched one at a time with pauses, nothing already
held is downloaded again, and `fetch.py` prints its request and byte totals per host at the end of the run.

| source | per daily run |
|---|---|
| Wikipedia (~130 race and table pages) | the MediaWiki API's current revision ids, 50 pages per request; only pages edited since the cached copy are downloaded; historical results pages are fetched once |
| pollresults.org race pages (~220) | the sitemap (1 request); a page is downloaded only when its `lastmod` is on or after the cached copy |
| Bluesky (Polling USA, Political Poll Bot) | posts are cached; each feed is read only back to the first post already held |
| VoteHub API (5 poll types) | 5 requests, a few MB: the API has no date filter or ETag |
| EIA heating oil, UF early vote, Florida statistics files | conditional requests (ETag / Last-Modified): unchanged files come back as 304 |
| Texas SOS early-voting turnout | off unless `TX_EARLY=on`; then the election index and one county file per early-voting day, each at most once a day (conditional requests); per-voter rosters are never requested |
| Pennsylvania mail ballots (data.pa.gov) | 2026 aggregates only; 2022 and 2024 are queried once and kept |
| NC / PA registration (Mondays) | new weekly NC snapshots only; past years' files are kept |
| 538, FEC, MIT inputs (bootstrap) | once, then kept |
| OpenFEC | never in the daily run; a manual rebuild only (cached per candidate) |

The one-time builders (`demog.py`, `urban.py`, `district_shapes.py`, `cd_lean_build.py`, `stateleg_build.py`,
`fec_money.py`) need large downloads (ACS summary files and PUMS, Census geography, precinct returns; several GB) or an
API key and are not part of the daily run; their docstrings list the exact inputs. The geographic builders also need
GeoPandas (the state-legislature workflows install it), and `district_shapes.py` runs mapshaper through `npx`.

### Secrets

| secret | used by | needed for |
|---|---|---|
| `OPENFEC_API_KEY` | `fec_money.py`, only on a manual run with `fec_refresh` | re-pulling June 30 fundraising (an [api.data.gov](https://api.open.fec.gov/developers/) key) |

The daily run needs no secret; it commits with the workflow's own `GITHUB_TOKEN`.

## Model notes

The sections below record what was measured behind the main choices, including what was tested and left off. The
switches named here are module constants or the environment variables listed above; the comment at each definition
carries the same evidence.

### National environment and error

- **No directional correction** (`national_mood.DIRECTION = False`). Generic-ballot averages overstated Democrats in 13 of
  the 15 House elections 1996-2024, but most of that record comes from older averages built differently. Rebuilt with this
  estimator for the seven elections with poll-level data (2012-2024), the average overstated Democrats in 5 of 7 a month
  out, by anything from nothing to five points, so no direction is assumed. An approval-adjusted variant
  (`national_mood.APPROVAL`) is built and off.
- **House effects shrunk lightly** (`generic.LAMBDA_HOUSE = 1` rather than 4): predicting the next 24 days' polls on 538's
  generic archive 2018-2024 at 7 dates, weighted RMSE 2.623 -> 2.551 (23 of 28 cases, 4 of 4 years; not significant on its
  own, year-block t 1.95).
- **Gallup party identification** (tested, not adopted). Gallup's leaned party-ID gap (adults) was tested as a predictor of
  the national House vote alongside the generic-ballot average G, leave-one-election-out 1996-2024 (n 15). Gallup's
  quarterly series is not openly published, so the annual series is the proxy. V ~ G: MAE 1.91 / RMSE 2.29; + the
  election-year gap 1.69 / 2.14 (better in 11/15, permutation p 0.09); + the previous year's gap 1.60 / 2.08 (12/15,
  permutation p 0.06); + the two-year change 2.04 / 2.41 (worse). Midterms only (n 7): no variant helps. No significant
  out-of-sample gain. The per-year series is not distributed (Gallup does not allow republishing its tables); source:
  Gallup, "Party Affiliation" trend, retrieved October 2026.
- **Uncontested seats** (tested, not adopted). The raw national House vote that the national-mood fit targets is distorted
  by seats without a D-v-R contest (2018: 29 D-only and 2 R-only seats plus same-party top-two generals; D 6.0M votes there,
  R 0.6M) and by Florida's unopposed races, which record no vote. A one-off test (October 2026) imputed every such seat
  (23-97 a cycle) from its nearest contested result on the same lines, else a safe-seat model (masked contested seats:
  margin RMSE 11.0, safe-seat fallback 18.0), giving an adjusted vote 1.6 points either side of the raw one over 1996-2024
  (2018: D+8.7 -> D+7.7). The national error falls (4.27 -> 3.90 at 29 days), but scored against the same target the adjusted
  vote does not forecast better (log score -0.022 per cycle, 7/15 cycles better, permutation p 0.29). A seat harness's gain
  (p 0.008) comes entirely from the intercept moving 0.27 points toward the Republicans, the generic ballot's historical
  Democratic overstatement that the no-direction rule excludes; the live-path House backtest (2018-2022) is a wash. Sources:
  MIT Election Data and Science Lab, U.S. House 1976-2018 (CC0); FEC "Federal Elections" 2018-2022 and the House Clerk's
  2024 election statistics (public domain).

### Governor model review

Governor races are the least nationalized of the three offices, so the fundamentals prior matters most there. The harness
(`python -m midterms.gov_backtest all`) scores every even-year cycle 2006-2024 at September 1, September 15,
October 1, October 15, November 1 and the day before the election (2006-2016 from October 1 only: 538's raw_polls, the poll
source for those years, holds the last ~60 days; 2018-2024 use 538's full governor poll archive). It runs the live path -
`gov2026.Prior`, `prepare` (sponsor shifts and pollster leans fitted on earlier cycles only), `collapse_versions`, the robust
blend and `gov2026.simulate` on the shared national draw - with only what was known on the date: polls three days after
their last field day, the prior fitted on the governor races of earlier cycles (1998 onward), and the generic-ballot
average known at that lead (538's average to 2016, this estimator from 2018; no directional correction). The 2026-only
Hispanic / Asian loadings and the heating-oil term are left out. n = 234 races (every D-v-R race of each cycle, polled or
not), 1,086 race-dates, 10 cycles; 984 race-dates had polls. Scores: Brier and log loss of P(D wins), error of the race
mean, the CRPS of the margin, 80 % / 50 % interval coverage. Each entry reads difference (cycles better; permutation p /
bootstrap p), with an exact sign-flip permutation of the cycle means (2^10 assignments) and a race-cluster bootstrap
stratified by cycle (4,000 draws).

Baseline (the prior before these terms): log loss 0.1944, Brier 0.0573, margin MAE 6.11 and RMSE 8.59 points, CRPS 4.39,
80 % intervals cover 76.1 % and 50 % intervals 45.1 % (too narrow), PIT KS 0.121.

| variant | log loss | Brier | CRPS | margin MAE | 80 % cov. |
|---|---|---|---|---|---|
| baseline | 0.1944 | 0.0573 | 4.390 | 6.11 | 0.761 |
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

Adopted (`gov2026.PRIOR_SPEC = {"lean_t": True, "succ": True, "qual": "all"}`; `{}` restores the baseline prior):
- *The lean slope's time trend* (lean x (year - 2010) / 10). Governor races have nationalized: the per-cycle lean slope rose
  from 0.1-0.2 (1998-2006) to 0.43-0.54 (2018-2022); a pooled slope (0.36) is too flat for a current race and compresses
  every prior toward 50-50. Fitted 1998-2024: 0.277 + 0.110 per decade -> 0.45 in 2026 (one cycle beyond the data, the same
  reach the walk-forward test made every cycle).
- *Successor incumbents as their own term*. Governors who succeeded mid-term and then ran (Ivey 2018, Reynolds 2018, Hochul
  2022, McKee 2022, Parson 2020, Quinn 2010 ...) had been read as open seats by the history parser; they are neither open
  seats nor full incumbents. Fitted: 10.8 points for an
  elected incumbent, 6.9 for a successor. Simply recoding them as incumbents (`INC_FIX`) scored the same within noise and is
  not used.
- *Nominee experience*, D minus R (+1 / 0 / -1): a nominee is experienced if, before the race, he or she held a statewide
  elected office (governor, lieutenant governor, attorney general, secretary of state, treasurer, comptroller, auditor,
  superintendent, an elected state commission) or a seat in Congress, read from each nominee's Wikipedia infobox
  (`gov_quality.py`); a sitting governor counts as experienced, and a nominee without an article as not. Symmetric in party
  by construction. Fitted: 6.1 points.
- Together against the baseline: margin MAE 6.11 -> 5.76; 80 % coverage 76.1 -> 79.6 %, 50 % coverage 45.1 -> 47.2 %, PIT
  KS 0.121 -> 0.109; better at every date; unpolled race-dates' MAE 14.2 -> 11.5.
- 2024 is in the history (`gov2026.USE_2024`, `data/static/gov_results_2024.csv`) and is the harness's tenth test cycle.

About thirty variants were scored, and with ten cycles the smallest attainable permutation p is 0.002, so no single result
survives a strict multiple-comparison correction. The adopted terms are the ones that pass on both tests, help in most
cycles and have a reason (a measured trend, a coding error, a long-established predictor); everything else stays off.

Measured, not used:
- Governor poll errors, measured as the Senate's were (538 raw_polls, last 60 days, races with 3+ polls): within-race sd
  4.89 v 4.63 for the Senate, race-average systematic 5.73 v 5.32. Scaling the Senate's 4.2 / 5.0 by those ratios made every
  score worse (CRPS +0.06, p 0.016 / 0.001), so the governor blend keeps the Senate's poll errors.
- The shared miss: the cycle-mean governor polling miss (non-partisan polls, last 21 days, 1998-2022) has RMS 3.5 points
  against the Senate's 2.8, and the two offices' cycle means correlate 0.90. The governors' shared statewide shock
  (`senate2026.GOV_SHARED_MISS` 4.2, scaled by the governor national slope) is left as it is: x0.5 and x1.5 each lose on log
  loss or coverage.
- Undecideds do not split in proportion to the candidates' shares: on 538's governor polls with cycle fixed effects they
  lean to the trailing candidate (coefficient -0.19, se 0.04, in the last 21 days). Shifting polls accordingly is tested
  below and left off; undecideds stay a symmetric variance term.
- Results run more lopsided than the race means: after removing each cycle's shared miss the winner beats the mean by about
  2 points (both parties' favourites). The lean trend removes part of it; a symmetric pull of the polls toward the state's
  fundamentals was not significant.
- Not testable here: Alaska's ranked-choice count (the harness scores Alaska 2022 on its first round) and strong independents
  (too few governor cases since 2006).

### Senate prior and errors

- **Time-varying prior** (`senate2026.SEN_CONST`, `B_LEAN`, `C_INC`, `NAT_SLOPE`, `PRIOR_SD`). Per-cycle fits show the
  incumbency coefficient falling about 3.8 points per decade, the lean slope rising about 0.11 and the residual sd falling
  about 1.35. The prior has linear time trends in the lean and incumbency slopes, fitted on every D-v-R race 1982-2024, the
  national slope of the last 10 cycles and a trend-extrapolated residual sd; at 2026: constant 0.66, lean 0.837,
  incumbency 6.7, national 0.795, sd 8.8. Against the pooled 10-cycle fit on the walk-forward harness (Senate 2006-2024, 10
  cycles, 1,615 race-dates): log loss -0.0079 (7/10 cycles; sign-flip p 0.061, race bootstrap p < 0.001), Brier -0.0025,
  CRPS -0.23; 2014-2024 better in 6/6 cycles, worse in 2006, 2008 and 2012. The gain is largest in cycles whose polls
  overstated Democrats; unpolled races show the same time pattern.
- **Shared and race errors** (`STATE_SHARED_MISS` 3.1, `RACE_EXTRA` 2.83). The cycle-wide statewide miss is 3.1 over
  1998-2022 (RMS about zero, no direction), and a single 4.2 shared shock implied a correlation between race errors of
  0.34 against 0.13 measured. Each race keeps its total spread (sqrt(3.1^2 + 2.83^2) = 4.2); only the correlation falls.
  Race scores unchanged; seat-count PIT sd 0.265 -> 0.299 (uniform 0.289). Governors keep `GOV_SHARED_MISS` 4.2.
- **Midterm poll error** (`MIDTERM_POLL_SYS_K` 0.8): the shared per-race Senate polling miss is smaller in midterms (4.1
  v 4.7 in presidential years); scaling it by the measured ratio improved log loss in all five midterms 2006-2022 and is
  applied in midterm years only.
- **Common trend** (`SEN_COMMON_TREND`, on): how all of the year's Senate polls have moved together, estimated with each
  race's own level and each pollster's lean, shifts every older poll by the shared movement since it was taken.
  Walk-forward 2006-2024: log loss -0.0035 (8/10 cycles). The same adjustment taken from the generic-ballot average, or
  applied to governor races, made the forecasts worse, so it is used for the Senate only.

### Independent challengers

Four 2026 Senate races have an independent as the main challenger: Nebraska, Idaho and South Dakota with no Democrat on
the ballot, Montana with one (`senate2026.IND_SHIFT`, `IND_SHIFT_SD`, `IND_MIN_RATIO`, `SPLIT_PRIOR_MULT`). Measured on every
Senate race since 1988 and every governor race since 1998 with no Democrat on the ballot (MIT and Wikipedia results; Sanders
and Mississippi's 2008 special, Democrats in all but label, aside), against the D-v-R prior fitted without the race's cycle
(the Senate form on the cycles before it; the governor prior on the other cycles), at the actual national vote:

| race | June 30 money (x the Republican's) | independent minus Republican | prior | above the prior |
|---|---|---|---|---|
| Orman, Kansas 2014 | $0.63M (0.45x) | -11.1 | -38.5 | +27.4 |
| McMullin, Utah 2022 | $3.25M (0.85x) | -10.9 | -31.6 | +20.8 |
| Osborn, Nebraska 2024 | $1.58M (1.00x) | -6.7 | -31.0 | +24.3 |
| Walker, Alaska governor 2014 | (state filings) | +2.3 | -30.7 | +33.0 |
| Harrington, Arkansas 2020 | $0 | -33.1 | -31.7 | -1.4 |
| O'Hara, Mississippi 2002 | none | -69.2 | -32.3 | -36.8 |
| Spannaus, Virginia 1990 | none | -63.3 | -21.9 | -41.5 |
| Steve Osborn, Indiana 2006 | none | -74.8 | -25.7 | -49.1 |

- **Prior, no Democrat on the ballot.** Being an independent explains nothing (all eight: mean -2.9, sd 34); money does: the
  four with a campaign finished +26.4 above the prior (sd 5.2, 95 % 18 to 35), the four without -1 to -49. "Funded" is at
  least a quarter of the Republican's June 30 money, with no dollar floor: the earlier $1M floor would have filed Orman with
  the unfunded. The record places the cut only between no money and Orman's ($0.63M, over $0.8M in 2026 dollars; 0.45x):
  Nebraska ($5.5M, 0.74x) is well clear, South Dakota ($0.78M, 0.58x) and Idaho ($0.60M, 0.29x) fall in that untested range
  and now qualify. Early polling strength separates the groups too, but the polls already enter the blend, so the prior does
  not condition on them. On the live-path backtest (next point) dropping the floor touches one race, Kansas 2014, and scores worse there
  (log loss +0.11 two to four weeks out): Orman led his final polls by 3 and lost by 11, with Kansas's governor polls 6 points
  off the same way.
- **Size of the shift.** The prior moves by 12, not the measured +26. Live path (that prior at the generic-ballot average,
  each race's own polls, leave-one-race-out, the four funded races): shifts 0 / 12 / 26 score log loss 0.507 / 0.551 /
  0.617 two to four weeks out and 0.636 / 0.549 / 0.503 five to nine weeks out. Late in 2014 and 2024 these races' polls ran
  ahead of the results, as those cycles' D-v-R polls did, so a prior further below the polls scored better late; four races
  in three cycles cannot settle it. The shift sd 9 (prior sd 12.6 in all) is wider than the group's spread (5.2, 95 % 2.9 to 19).
- **Polls.** In the four funded races the non-partisan average of the final three weeks missed by +2.8 for the independent
  (Kansas +11.4, Alaska -1.2, Utah -2.0, Nebraska +2.9; se 3.1): +0.1 (se 3.0) net of the same cycle's D-v-R statewide polling
  miss and -1.2 (se 3.1, three races) net of the same state's D-v-R race that year (Kansas governor 2014 +6.4, Nebraska's other
  Senate race 2024 +6.7). Their spread (RMS 5.1 net of the cycle) is that of D-v-R races in those cycles (3.8-6.4). The
  independent's margin fell from the 22-60-day average to the final three weeks in all four (mean -4.2, se 1.7), -1.2 (se 0.7,
  three races) net of the cycle's D-v-R movement. Undecided and minor-party voters went 67 % to the Republican between the final
  polls and the result, against 71 % in D-v-R races in red states (lean below -15; 168 races). So there is no independent
  poll bias, extra variance or late fade to add: these misses follow the cycle and the state, which the shared statewide shock
  already carries.
- **Sponsors.** An independent's own polls ran +5.5 for the independent (se 2.1; 13 polls, 4 races) against the same race's
  non-partisan polls within 21 days, like the Democratic sponsor effect (+4.3) the model removes from them. Democratic-sponsored
  polls in independent races ran -3.0 (se 2.8; 12 polls, 6 races; race-level se 4.0) and keep the independent's-side tag: too
  few races to drop it.
- **A Democrat on the ballot too (Montana).** The margin comes from full-field polls only (independent minus Republican; the
  Democrat is in the poll's third-party share, which adds variance), and the D-v-R prior is kept, widened x2. Independents who
  outpolled a Democrat on the same ballot (King, Maine 2012, 2018, 2024; Crist, Florida 2010; Cutler, Maine 2010; Chafee, Rhode
  Island 2010; Ventura, Minnesota 1998; Pollina, Vermont 2008) finished -2.4 from that prior (sd 12.9, se 4.6): no shift, and the
  x2 sd (17.7) covers the spread. Late consolidation toward the stronger anti-Republican candidate is not measured: in the two
  such races in 538's archive (Maine 2018 and 2024) the trailing Democrat gained 3-5 points on the final polls, and third-place
  independents (8 races 2018-2022) lost 3.0 (se 1.5); the earlier three-way races have no poll file here.
- **Correlation.** No shared independent error in the simulation: the two funded independents of one cycle (2014) missed in
  opposite directions (+8.3 and -5.2 net of the cycle), and their misses track the same state's D-v-R misses, which the
  simulation's shared statewide shock already gives every race.

### Tested and left off

**Same-state Senate and governor errors** (`senate2026.JOINT_ON = False`). Within a cycle, a state's Senate and governor
polling errors correlate 0.58 (95 % 0.46-0.68; 149 state-years, 13 cycles). With the switch on, each race's own error is
split into c x a draw shared by the state's statewide races plus sqrt(sd^2 - c^2) x its own draw (c = 3.68 points on
1998-2022, capped at 0.9 sd), so only the covariance moves. Joint test: the governor harness paired with the Senate
harness, state by state, one joint simulation per pair on the shared national draw; n = 682 pair-dates, 142 state-years,
10 cycles:

| | off | on | difference (cycles better; permutation p / bootstrap p) |
|---|---|---|---|
| log loss, 4 outcomes | 0.3325 | 0.3347 | +0.0023 (5/10; 0.73 / 0.76) |
| Brier, 4 outcomes | 0.1793 | 0.1813 | +0.0020 (4/10; 0.47 / 0.40) |
| Brier of P(split ticket) | 0.0775 | 0.0792 | +0.0016 (5/10; 0.53 / 0.50) |
| mean P(split) (actual 24.9 %) | 27.4 % | 26.1 % | |
| simulated correlation of the pair | 0.31 | 0.56 | (backtest residuals: 0.32) |

No gain: the shared national draw already gives a state's two races a correlation of 0.31, and the backtest residuals
show 0.32. The simulated national shock is larger than the realized cycle-wide miss, so it already carries the state part.
Each office's seat distribution is a sum over one office's races, so a cross-office covariance cannot move the headlines.

**Undecideds toward the trailing candidate** (`model.UND_TRAIL_K = None`). On, each race poll's margin moves by
k x margin x (100 - D - R) / (D + R) before the sponsor lean is removed, with k from `model.und_trail_fit(before=cycle)`
(538 raw_polls, Senate and governor, non-partisan, last 60 days, cycle fixed effects; -0.22 when fitted on every cycle). Against the
model as it is:

| harness | n | log loss | Brier | CRPS | margin MAE | 80 % cov. |
|---|---|---|---|---|---|---|
| governors | 234 races, 1,086 race-dates | -0.0003 (2/10; 0.99 / 1.00) | +0.0001 (3/10; 0.51 / 0.72) | +0.18 (2/10; 0.008 / <0.001) | +0.24 (0.014 / <0.001) | 79.6 -> 76.0 % |
| Senate (walk-forward prior) | 326 races, 1,528 race-dates | +0.0031 (1/10; 0.006 / <0.001) | +0.0006 (2/10; 0.031 / 0.002) | +0.26 (0/10; 0.002 / <0.001) | +0.34 (0/10; 0.002 / <0.001) | 79.6 -> 77.1 % |

It makes the race means worse in both offices: they are already too close to 50-50, and the switch pulls them further
in. It is not a party lean (mean shift of the race means +0.03 points for governors, +0.05 for the Senate).

**Pollster shared error** (`model.POLLSTER_SHARED_ERROR = None`). A pollster's house error is shared by all of its polls
in a race, so four polls from one firm are not four independent reads. With tau set, each poll's error in the robust blend
splits into its own part and a pollster-race part common to that firm's polls, integrated out exactly; each poll's
marginal variance is unchanged. tau is fitted on 538's raw_polls (`race_poll_calibration.pollster_shared_sd`: same-pollster
v different-pollster pairs at the same time gap, lean-corrected, net of sampling): tau = 2.8 points. On the leak-free
backtests (Senate 2018-2024, governors 2018-2022, House 2018 and 2022; six dates from September 1 to November 1; tau from
earlier cycles) it changed nothing measurable: log loss Senate +0.0002 (95 % interval -0.0014 to +0.0016), governors
-0.0006 (-0.0024 to +0.0008), House +0.0001 (-0.0001 to +0.0003).

**Directional state corrections** (`model.STATE_DIRECTION = False`). State polling misses persist net of the national miss
(next-cycle slope 0.24, 538 raw_polls 1998-2022) and statewide polls ran too Democratic in states with more white
non-college voters (1.17 points per state sd). Both are measured and implemented; like the national rule, the live model
assumes no direction and widens the state-level error instead.

### Heating-oil adjustment

A judgment term, not backtested (no past cycle had a comparable price shock): the rise in retail heating oil over the past
year, times each district's (state's) share of oil-heated homes, times a typical household's 650 gallons, at 0.78
generic-ballot points per dollar of gasoline spread over 750 gallons, times the share not yet priced into the polls,
against the president's party (`model.heating_oil_shift`). The price is EIA's weekly Maine No. 2 heating oil residential
price; EIA's survey runs from October to March, and outside the season the last published week is held.

### State legislatures (experimental)

`stateleg.py` forecasts control of 22 chambers - both chambers in Michigan, Minnesota, Wisconsin, Arizona, Pennsylvania,
New Hampshire, North Carolina, California and Kentucky; the Georgia, Iowa and Texas Houses; and the Texas Senate - inside
the daily run (`MIDTERMS_STATELEG`, default on; workflow input `stateleg`). It writes `state_legislatures` into
`web/data/model.json` and the per-seat file `web/data/stateleg_seats.json`; `web/legislatures.html` shows them.

**Seat model.** Each seat's expected Democratic two-party margin is `a + beta * lean + c * inc + g * E`: the district's
2024 presidential margin minus the national one (`lean`), incumbency (+1 / -1 / 0) and the model's national environment E.
The simulation adds g x the House's national draw, a state error shared by both chambers (partly chamber-specific) and a
t5 seat error. Multi-member districts (AZ House, NH House) elect their top-k candidates (party share +- the candidate's
own deviation from its slate, sd fitted, plus an incumbency bonus); a party with fewer nominees than seats can win at most
that many. Seats with only one major party on the ballot are fixed. Staggered senates (PA and WI: odd districts up; CA,
KY and TX: the seats elected in 2024 are held over) add the held-over seats by their current holders. Ties: the MI and PA senates go to the party of the 2026 governor winner (the lieutenant governor), the
NC Senate to the Democrats (Lt Gov Hunt), elsewhere counted as shared.

**Fitted parameters** (`data/static/stateleg/params.json`; `python -m midterms.stateleg fit`): incumbency c = 6.0 points,
national slope g = 0.77, 2-year state swing error 4.8 (state error 3.4), between-chamber correlation 0.62 - Klarner returns
1972-2022, 42,851 consecutive same-map district pairs; lean slope beta = 0.91, intercept a = 2.9, seat error 5.8 - 2018
(2016 lean) and 2022 (2020 lean) results; slate sd 8.6 % of the party mean (11,276 multi-member slates).

**Backtest** (`python -m midterms.stateleg_backtest`, `data/static/stateleg/backtest.json`): 2018 and 2022 on October 1,
Klarner parameters from earlier elections only, the lean slope from the other cycle, E = the 538 generic-ballot average with
no correction, who is on the ballot from the candidate records. The original 14 chambers (24 chamber-elections with full
district data): control Brier 0.115 against 0.250 for "the current majority holds", called right in 20 of 24 (the
current-majority rule: 18), seat count inside the 80 % range 23 of 24. The Georgia, Iowa and Texas Houses (5 chamber-elections
with complete district data): all called right, as the current-majority rule also was; Brier 0.007; seat count inside the
80 % range 5/5. The California and Kentucky chambers and the Texas Senate (10 chamber-elections): all called right, as the
current-majority rule; seat count inside the 80 % range 8 of 10. The daily run publishes these per-group scores from
`backtest.json` (`stateleg_backtest.published`, `state_legislatures.backtest` in `model.json`), and `about.html` and
`legislatures.html` quote them from there. No published forecaster's chamber calls were scored (none available under a
usable licence).

**State-poll signal** (tested, not adopted; `MIDTERMS_STATELEG_KAPPA=on` restores it). The legislative state swing residual
tracks the state's statewide polling miss (slope -0.31, se 0.05; 360 state-cycles 1998-2020), and a fitted link kappa x
the state's Senate / governor surprise (kappa 0.074, se 0.024) changed seat log loss by +0.0005 (2018) and +0.0003 (2022)
and the original 14 chambers' control Brier 0.115 -> 0.119 (arm "state" of `backtest.json`): no measurable gain.

**Coverage.** `stateleg_scan.py` reads every 2026 chamber page (`data/static/stateleg/coverage_scan.csv`) and checks the
candidates (`stateleg_scan.check` -> `pending_check.csv`): a chamber is added only when the page has a general-election
slate for every seat up, the seats up match the 2024 rule, every district has a lean on the current map, and the
one-party seat count is plausible against 2024 (MEDSL) and 2022 (Klarner). Held back: CT Senate and House (2024 MEDSL party
coding under fusion voting gives no usable comparator), UT and WV Senates (one-party seats far below 2022 / 2024), IA Senate
(a district missing from the page), WA and OR Senates (lean check failed: rms 4.0 and 8.9).

**Approximations.** NH House: candidates are not listed on Wikipedia, so every seat is contested by full slates, floterial
districts are simulated as ordinary multi-member districts, and the 2018 backtest has no NH House. MI Senate: no 2024
Senate election, and the 2026 court-ordered redraw of the Detroit-area districts is not in the Census TIGER files yet;
leans are VEST 2020 precincts moved to 2024 by each House district's measured swing on the 2022 map (the same method
reproduces known 2024 Senate leans with rms 0.3-1.8 in PA, GA, IA, TX, NC). PA odd Senate seats use the same method on the
current map. MN Senate candidates are not on Wikipedia: incumbents are assumed to run unless listed as retiring. Georgia
House nominees come from the Georgia Secretary of State's official primary and runoff results (`ga_sos.py`), cross-checked
against Wikipedia and Open States (`ga_house_crosscheck.csv`).

**Data** (`stateleg_build.py`, run in GitHub Actions by `stateleg-data.yml`; downloads cached, never committed):

| file | contents | source |
|---|---|---|
| `stateleg/lean_2026.csv` | 2024 president by 2026 district (MEDSL precinct rows joined to the same precinct's legislative labels; MN / WI senates from nested House districts; AZ House = LDs; MI Senate / PA odd seats as above) + a spatial cross-check | MEDSL 2024 precinct returns (CC0), VEST 2020 (CC BY 4.0), TIGER (public domain) |
| `stateleg/lean_hist.csv` | 2016 president on the 2018 maps, 2020 president on the 2022 maps | VEST 2016 / 2020 x TIGER 2018 / 2022; NH House by town names x MEDSL 2022 labels |
| `stateleg/medsl_results.csv.gz` | candidate-level legislative results 2022, 2024 | MEDSL (CC0) |
| `stateleg/klarner.csv.gz` | Klarner returns aggregated to district-elections, all states 1972-2022 | Klarner, doi:10.7910/DVN/FJOGJB (CC0) |
| `stateleg/openstates_current.csv` | sitting legislators | Open States people (CC0) |
| `stateleg/qc.json`, `stateleg/geo_audit.json` | coverage, cross-checks, map-change audit; the district-map join audit (the map build fails on a gap) | - |
| `web/data/stateleg_geo/{ST}.topo.json`, `index.json` | district maps, one TopoJSON per state (`stateleg_geo.py`, `stateleg-geo.yml`) | U.S. Census Bureau TIGER/Line 2025 state legislative districts (public domain); large water cut with the Census 2024 cartographic state outline; NH floterials from MEDSL 2024 precinct labels |

Candidates and retirements are read each day from the Wikipedia 2026 chamber pages (`stateleg_wiki.py`, cached like every
page). On the maps, districts are shaded by the chance a Democrat wins the seat (multi-member: the expected Democratic
share), hatched where only one party is on the ballot and dotted where the seat is not up in 2026. Boundaries are the
TIGER/Line 2025 districts (the maps in force for 2026), except the Michigan Senate's court-ordered Detroit-area redraw,
which is flagged on its map; each New Hampshire floterial is drawn as the outline of the base districts whose towns vote
in it.

### District lean (the House prior)

`cd_lean_build.py` builds the House prior's district lean from public precinct returns: 0.75 x the most recent presidential
two-party margin minus the nation's + 0.25 x the same for the election before, using only elections held before the cycle
- the 2026 maps from 2024 and 2020 (`cd_lean_2026.csv`), the maps of the 2022 election from 2020 and 2016 and of the 2018
election from 2016 and 2012 (`cd_lean_hist.csv`, on which the prior is fitted). The national margins are the 50-state sums
of the same tables. Every state is calibrated to its official totals, each nominee's votes on every ballot line (MIT's
party code leaves out New York's fusion lines through 2016).

**2026 maps.** Redrawn states are found from the Census 119th v 120th Congress block equivalency files. Unchanged states
use MEDSL 2024 precinct rows by their U.S. House label, where the file matches the official state totals and the
presidential vote sits on labelled precinct rows. Redrawn states (and files that fail those checks): VEST 2020 precincts
-> 2020 blocks -> 120th-Congress districts, carried to 2024 by the county-by-district swing on the labels (`qc_cd.json`
lists every check and shift). The Census 120th-Congress block file does not follow court orders, so
`cd_lean_build.MAP_STATUS` records the map in force per state with a dated source; Missouri uses its 2022 lines (the U.S.
Supreme Court blocked the 2025 map on 2026-09-25). For comparison only, against 2 x Cook PVI: rms 1.09 points over all 435
(0.93 on unchanged maps, 1.29 on redrawn ones), mean +0.25, r 0.9994.

**2018 and 2022 maps.** VEST 2016 and 2020 precincts -> 2020 blocks -> the 118th-Congress block equivalency file (2022) or
the TIGER/Line 116th-Congress districts by each block's internal point (2018). VEST has no 2012 files and MEDSL's county
returns sit behind a Dataverse guestbook, so 2012 is the 2016 block votes moved by each county's 2012-16 swing and turnout
change, with the county results from Wikipedia's state pages (every state's table matches its official totals within 1 %).
Checked against The Downballot's published presidential results by district (a check only; their figures are not used or
redistributed): 2016 on the 2018 maps rms 0.16 points (largest 1.7, MO-5), 2020 on the 2022 maps rms 0.23 (3.4, LA-6),
2012 on the 2018 maps rms 2.5 (10, TX-7: the county step cannot see swings inside the large metro counties), so the 2018
lean is within rms 0.63 (2.5) of the same lean from their figures. The 2016 vote alone would be off by rms 2.45 (7.6). The
county step measured where both elections are known (2016 rebuilt from the 2020 blocks): rms 2.7 by district. 538's
published leans for the same maps differ by rms 3.3 (2018 partisan lean, which also weights state legislative results;
largest in West Virginia and Kentucky) and 2.4 (the PVI column of its 2022 urbanization index).

**Fit** (`model.fit_prior`, contested seats 2018 and 2022, n 795; the 538 / Cook leans it replaced in brackets): lean slope
0.918 (0.951), incumbency 4.47 (4.58), residual sd 7.35 (7.41); with fundraising (`money.coefficients`) 0.855 (0.889), 3.34
(3.51) and 1.31 (1.25) per unit of the log money ratio. `Params.k0`, the contested-margin intercept over the national House
margin, is 0.4 (2018 -0.79, 2022 +1.63); the 1.1 it replaced came from an earlier fit - the same definition gives 0.78 on
the old leans. Fundraising still helps out of sample on the new lean (all-seat RMSE fit on one cycle and scored on the
other: -5.6 % in 2018, -2.6 % in 2022).

**Backtest.** The live House path (`model.house_blend`, `factor_update`, `simulate`) on 2018 and 2022, each year forecast
from the other's fit (slopes, k0, fundraising), 538's House polls as released, the generic-ballot average at the lead, 7
dates from August 1 to the day before the election (5,565 seat-dates):

| lean | log loss | Brier | CRPS | margin MAE | 80 % cov. | Sep 15, 2018 (235 won) | Sep 15, 2022 (213 won) |
|---|---|---|---|---|---|---|---|
| 538 / Cook | 0.1153 | 0.0353 | 3.642 | 4.98 | 0.854 | 221 (197-247) | 212 (190-233) |
| ours | 0.1113 | 0.0338 | 3.593 | 4.93 | 0.866 | 222 (198-249) | 212 (189-233) |

Ours is better in both cycles on all three scores (log loss 2018 -0.0070, 2022 -0.0009; race bootstrap p 0.13, Brier 0.055,
CRPS 0.20): with two cycles not a significant gain, and not a loss. The seat count is inside the 10-90 % range at all 14
dates with either lean. On the inputs of 16 October 2026 the switch moved the House from 241.7 to 241.2 Democratic seats
(P(majority) 0.894 -> 0.888): +0.7 seats from the lean with k0 refitted on both leans, -1.1 from replacing the stale k0.

### Early-vote diagnostics

None of these feeds the forecast or the website. Votes cast before Election Day enter the model only through the
early-vote movement split described above.

**North Carolina** (`nc_early.py`, `early-diag.yml`; race and ethnicity are self-reported on the NC voter record): accepted
mail and one-stop ballots by days before Election Day, 2026 against 2022 and 2024, for white, Black, other, undesignated
and Hispanic voters - each group's share of ballots, that share against its share of registered voters on the same days
out, ballots per 1,000 registered, the share who skipped at least two of the three previous generals, and what the same
voters did in the previous general of the same kind. Inputs: the NCSBE absentee file of 2026 (one conditional request per
run), the committed 2022 / 2024 baseline, NCSBE weekly registration statistics, and the 2016-2024 generals reduced from
NCSBE's voter-history file (voter-level, so it lives only in the Actions cache, never committed). Output
`data/early/nc_early.json` and the day's table `data/early/nc/2026_<date>.csv`.

**Florida** (`fl_early.py`; party only): the Division of Elections' daily statistics files are snapshotted into
`data/early/fl_stats/` (Florida keeps no history, so a missed day is lost) and compared with the Wayback Machine's 2024
captures at the same days out, as the returns' R-D margin, that margin minus the registration R-D of the latest month end,
and minus the R-D of all mail ballots sent. Output `data/early/fl_early.json`. Hurricanes Helene (Sep 26, 2024) and Milton
(Oct 9, 2024) fall on the 2024 baseline dates.

**Georgia** (`ga_early.py`): georgiavotes.com's home page once a day (compiled from the Georgia Secretary of State's
absentee files; its pages are open to crawlers, its `/api/` is not and is never used): ballots so far against 2022 at the
same point, statewide and by county, the county mix weighted by each county's past margin, and the race, age and gender of
2026 voters against 2022 at the same day of in-person voting (Wayback captures, `data/static/ga_wayback_2022.json`). It
cannot be tested for skill - no past cycle has both a same-day baseline and an outcome to score - so it stays a diagnostic.

**Pennsylvania** (`pa_early.py`, daily run): mail-ballot applications and returned ballots by party at matched days out,
2026 against 2022 and 2024, from the Department of State's datasets on data.pa.gov; uploaded as a workflow artifact.

**Texas** (`tx_early.py`; off by default). Nothing runs or is fetched unless the repository variable `TX_EARLY` is `on`.
When on, it follows the Texas Secretary of State's daily county early-voting file for the 2026 general (county summary
only, one conditional request per file per day) and writes `web/data/tx_early.json` (workflow artifact): early votes as a
share of registered voters, statewide and for fixed county groups (big Democratic counties, Republican-leaning suburbs and
exurbs, the Rio Grande Valley and border, the swing suburbs Tarrant and Fort Bend; `data/static/tx_county_pres2024.csv`).
Texas has no party registration, so this is turnout, not vote choice. The 2022 comparison is not built: the SOS's 2022
day-by-day portal and its results site answered GitHub Actions with a Cloudflare browser challenge, which is not evaded.
The planned end point, the final 2022 early vote by county, is described in `tx_early._final_2022`.

## Credits

Model and code: Shawn Corvec. Poll data belong to their pollsters. Governor results and nominees' offices: Wikipedia
(CC BY-SA 4.0). Ranked-choice transfer rates are derived from the Maine Secretary of State's and the State of Alaska
Division of Elections' official tabulations. See `web/about.html` ("Sources and credits") for the full list. Not
affiliated with or endorsed by any of the sources.

## License

Code: [MIT](LICENSE). Data keep the terms of their sources, listed above. Race polls taken from Wikipedia's race pages are
used under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); `web/data/polls.json`, `web/data/whatif.json`
and the poll page (`web/polls.html`) republish them, as checked, corrected and weighted by the model, under the same licence.
