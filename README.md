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
   Texas early-vote turnout (`tx_early.py`, a separate workflow step), voter registration in North Carolina and
   Pennsylvania on Mondays (`registration.py`), same-pollster poll deltas. They are uploaded as a workflow artifact, not
   committed.
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
| `tx_early_2022.csv`, `tx_early_sources.json` | 2022 general: cumulative early votes (in person, by mail) and registered voters by county and early-voting day (Oct 24 - Nov 4, 2022), with the source, retrieval time and checks | Texas Secretary of State, Early Voting Turnout portal (earlyvoting.texas-election.com; public records as reported by the counties; attribution, no endorsement implied); `python -m midterms.tx_early baseline`, once, in GitHub Actions |
| `tx_county_pres2024.csv` | 2024 presidential votes by Texas county (to check the tracker's county groups) | MIT Election Data and Science Lab, County Presidential Election Returns 2000-2024, doi:10.7910/DVN/VOQCHQ (CC0) |
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
prices, Florida Division of Elections and Pennsylvania Department of State files, the Texas Secretary of State's 2026
early-voting county files. Race ratings from Cook, Sabato and
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
| Texas SOS early-voting turnout (from Oct 19) | the election index and one county file per early-voting day so far, each at most once a day (conditional requests); the per-voter rosters are never requested |
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

`midterms/tx_early.py` (a step of the daily workflow; nothing feeds the forecast) follows the Texas Secretary of State's daily
county early-voting file for the 2026 general (early voting Mon Oct 19 - Fri Oct 30; the SOS "Early Voting Turnout" portal,
`goelect.txelections.civixapps.com`, county summary only) and writes `web/data/tx_early.json` (workflow artifact): for every
early-voting day, cumulative early votes (in person + mail ballots received) as a share of registered voters, against the 2022
general on the same early-voting day (Oct 24 - Nov 4, 2022, committed once in `data/static/tx_early_2022.csv`), statewide and for
fixed county groups - big Democratic counties (Harris, Dallas, Travis, Bexar, El Paso), Republican-leaning suburbs and exurbs
(Collin, Denton, Montgomery, Rockwall, Parker, Kaufman, Ellis, Johnson, Brazoria, Galveston, Comal, Guadalupe; each group's 2024
presidential margin is in the file), the Rio Grande Valley and border (Hidalgo, Cameron, Webb, Starr, Willacy, Maverick) and the
swing suburbs Tarrant and Fort Bend - plus each group's share of the statewide early vote against its 2022 share and the mail
share. Both early-voting periods start on the 17th day before Election Day moved to the next Monday and run 12 days, so day k is
the same weekday and the same number of days out in both years (SB 2753 of 2025, which would change the period, is not in effect
for November 2026). Caveats (also in the file's `notes`): Texas has no party registration and no party data are used, so this is
turnout, not vote choice; mail voting is limited to voters 65 and over, disabled voters and a few other groups; HB 1217 (2023)
extended weekend and last-week hours to small counties; counties' reports lag, and the file counts the counties that did not move
from the day before.

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

## Credits

Model and code: Shawn Corvec. Poll data belong to their pollsters. Ranked-choice transfer rates are derived from the Maine
Secretary of State's and the State of Alaska Division of Elections' official ranked-choice tabulations. See `web/about.html` ("Sources and credits") for the
full list. Not affiliated with or endorsed by any of the sources.
