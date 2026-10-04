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
   voter registration in North Carolina and Pennsylvania on Mondays (`registration.py`), same-pollster poll deltas.
   They are uploaded as a workflow artifact, not committed.
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
prices, Florida Division of Elections and Pennsylvania Department of State files. Race ratings from Cook, Sabato and
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

## Heating-oil adjustment

A judgment term, not backtested: the rise in retail heating oil over the past year, times each district's (state's)
share of oil-heated homes, times a typical household's 650 gallons, at 0.78 generic-ballot points per dollar of gasoline
spread over 750 gallons, times the share not yet priced into the polls, against the president's party. The price is
EIA's weekly Maine No. 2 heating oil residential price. EIA's survey runs from October to March; outside the season the
last published week is held (the page says which week).

## Credits

Model and code: Shawn Corvec. Poll data belong to their pollsters. Ranked-choice transfer rates are derived from the Maine
Secretary of State's and the State of Alaska Division of Elections' official ranked-choice tabulations. See `web/about.html` ("Sources and credits") for the
full list. Not affiliated with or endorsed by any of the sources.
