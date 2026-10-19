"""Scraped names carry no markup: they are cleaned where they enter (Wikipedia pages, the VoteHub API, pollster names), and
the pages escape every data string they put into HTML."""

import json
import re
from pathlib import Path

from midterms import generic as G, names as N, votehub_races as VR, wiki_polls as W

HOSTILE = '<img src=x onerror=alert(1)>Acme "Polls"\x00\x1b\n'
WEB = Path(__file__).resolve().parents[1] / "web"
ESC = """const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));"""


def plain(s):
    return not re.search(r"[<>\x00-\x1f\x7f-\x9f]", s)


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def test_clean_text():
    assert N.clean_text(HOSTILE) == 'img src=x onerror=alert(1)Acme "Polls"   '
    assert N.clean_text("Research & Polling") == "Research & Polling"  # ordinary names are unchanged


def test_clean_tree_keeps_structure():
    rec = {"pollster": HOSTILE, "sample_size": 812, "internal": None, "answers": [{"choice": "Smith<b>", "pct": 47.5}]}
    out = N.clean_tree(rec)
    assert out["sample_size"] == 812 and out["internal"] is None and out["answers"][0]["pct"] == 47.5
    assert out["answers"][0]["choice"] == "Smithb"
    assert all(plain(s) for s in strings(out))


def test_canon():
    assert plain(N.canon(HOSTILE))
    assert N.canon("The New York Times/Siena College") == "siena"


TABLE = """<html><body><div class="infobox"><table class="infobox"><tr><th>Nominee</th><td>&lt;b&gt;Ann Lee&lt;/b&gt;</td>
<td>Bob Roe&#x3C;script&#62;</td></tr><tr><th>Party</th><td>Democratic</td><td>Republican</td></tr></table></div>
<table><tr><th>Poll source</th><th>Date(s) administered</th><th>Sample size</th><th>Ann Lee (D)</th><th>Bob Roe (R)</th></tr>
<tr><td>&lt;img src=x onerror=alert(1)&gt;Acme&#60;/b&gt;&#7;</td><td>October 1&ndash;3, 2026</td><td>600 (LV)</td><td>48%</td><td>&lt;1%</td></tr>
</table></body></html>"""


def test_wikipedia_page_through_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "CACHE", tmp_path)
    W._cache_file("Hostile page").write_text(TABLE)  # a fresh cached copy: fetch reads it without the network
    html = W.fetch("Hostile page")
    rows = W.poll_tables(html, 2026)
    assert len(rows) == 1 and rows[0]["pollster"] == "img src=x onerror=alert(1)Acme/b"
    assert rows[0]["cands"] == {"Ann Lee (D)": 48.0, "Bob Roe (R)": 1.0}  # "<1%" reads as before
    assert W.infobox_nominees(html) == [("bAnn Lee/b", "Democratic"), ("Bob Roescript", "Republican")]
    assert all(plain(s) for s in strings(W.infobox_nominees(html)))
    assert W._defuse("&#x3c0; &#600; &amp;lt;") == "&#x3c0; &#600; &amp;lt;"  # other characters stay as they are


def test_votehub_feeds(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "raw" / "votehub"
    raw.mkdir(parents=True)
    poll = {
        "id": "x1",
        "pollster": HOSTILE,
        "seat_name": "PA-7",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "sample_size": 600,
        "population": "lv",
        "answers": [{"choice": "Dem", "pct": 48}, {"choice": "Rep<script>", "pct": 45}],
    }
    (raw / "us-representative.json").write_text(json.dumps([poll]))
    (raw / "generic-ballot.json").write_text(
        json.dumps([dict(poll, answers=[{"choice": "Dem", "pct": 48}, {"choice": "Rep", "pct": 45}])])
    )
    monkeypatch.setattr(VR, "ROOT", tmp_path)
    monkeypatch.setattr(G, "RAW", raw)
    assert all(plain(s) for s in strings(VR.fetch(refresh=False)))
    assert all(plain(s) for s in G.load_votehub("generic-ballot")["pollster"])


def test_pages_escape_with_one_esc():
    for page in sorted(WEB.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        if "innerHTML" in html or "insertAdjacentHTML" in html:
            assert html.count("const esc=") == 1 and ESC in html, f"{page.name}: the shared esc() definition"


def test_wikipedia_numbers_keep_their_thousands():
    assert W._num("2,000 (LV)") == 2000 and W._num("1,021 (RV)") == 1021 and W._num("12,345") == 12345
    assert W._num("45%") == 45 and W._num("45.5%") == 45.5 and W._num("677 (LV)") == 677
    assert W._num("48%[a]") == 48 and W._num("–") != W._num("–")  # footnotes dropped; no number is NaN
