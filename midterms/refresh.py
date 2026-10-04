"""One-command refresh: re-fetch the Wikipedia inputs, re-scrape polls, rebuild the board.
    python -m midterms.refresh          (~5 min: 50 state House pages + 36 Senate pages, cached 24 h)
"""
import time
from . import wiki_inputs as I, wiki_polls as W, build_web as B
t0 = time.time()
if __name__ == "__main__": W.FRESH_SINCE = t0          # manual run: re-fetch every race page once (wiki_polls.FRESH_SINCE)
s = I.house_seats(0); print(f"house seats {len(s)}, open {int(s['open'].sum())}", flush=True)
print("generic", I.generic_aggregates(), flush=True)
print("ratings", len(I.house_ratings(0)), flush=True)
print("senate races", len(I.senate_races(0)), flush=True)
hp = W.all_house_polls(); print(f"house polls {len(hp)} rows, {hp['seat'].nunique() if len(hp) else 0} seats", flush=True)
from . import votehub_races as VR, bluesky_polls as BP, pollster_quality as PQ
try: PQ.update_pollresults_grades(days=45, max_pages=15)      # pollresults.org grades (recorded; not used in the fit, 2026-09-29)
except Exception as e: print('pollster grades failed:', str(e)[:80])
try: BP.build()
except Exception as e: print('bluesky polls failed:', str(e)[:80])
try: VR.build()
except Exception as e: print('votehub races failed:', str(e)[:80])
B.main()
try: PQ._LIVE = None; PQ.coverage()                         # data/cache/pollster_quality_2026.csv
except Exception as e: print('pollster quality table failed:', str(e)[:80])
print(f"done in {time.time() - t0:.0f} s")
