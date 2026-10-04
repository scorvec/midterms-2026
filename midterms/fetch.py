"""Polite HTTP: one identifying User-Agent, conditional requests, and a log of what each run downloaded.

`get(url, dest)` keeps the response in `dest` (data/raw or data/cache, carried between runs by actions/cache) with its
ETag / Last-Modified in a sidecar `<dest>.http.json`; the next request sends If-None-Match / If-Modified-Since and a 304
reuses the file without a transfer. `min_age_h` skips the request entirely while the stored copy is younger than that.
Every request is counted in STATS (printed at the end of the daily run: requests and bytes per host).
"""
from __future__ import annotations

import collections
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "scorvec.com midterm model (+https://scorvec.com/midterms/about.html)"
STATS = collections.defaultdict(lambda: [0, 0, 0])      # host -> [requests, bytes, not-modified]


def count(url: str, nbytes: int, not_modified: bool = False):
    s = STATS[urllib.parse.urlparse(url).netloc]; s[0] += 1; s[1] += nbytes; s[2] += int(not_modified)


def open_url(url: str, headers: dict | None = None, timeout: int = 120) -> bytes:
    """Plain GET with the project User-Agent (counted)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r: b = r.read()
    count(url, len(b)); return b


def get(url: str, dest: Path, min_age_h: float | None = None, timeout: int = 120) -> tuple[bytes, bool]:
    """(content, changed). Conditional GET against the stored copy; raises if there is neither a response nor a copy."""
    dest = Path(dest); meta_f = dest.with_name(dest.name + ".http.json")
    meta = json.loads(meta_f.read_text()) if meta_f.exists() and dest.exists() else {}
    if dest.exists() and min_age_h is not None and time.time() - dest.stat().st_mtime < min_age_h * 3600:
        return dest.read_bytes(), False
    h = {"User-Agent": UA}
    if meta.get("etag"): h["If-None-Match"] = meta["etag"]
    if meta.get("last_modified"): h["If-Modified-Since"] = meta["last_modified"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            b = r.read(); et, lm = r.headers.get("ETag"), r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304 and dest.exists():
            count(url, 0, True); dest.touch(); return dest.read_bytes(), False
        raise
    count(url, len(b))
    dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(b)
    meta_f.write_text(json.dumps({"etag": et, "last_modified": lm, "url": url}))
    return b, True


def report():
    if not STATS: return
    tot = [sum(v[i] for v in STATS.values()) for i in range(3)]
    print("downloads this run: " + "; ".join(f"{h} {v[0]} req ({v[2]} not modified) {v[1] / 1e6:.2f} MB"
                                            for h, v in sorted(STATS.items(), key=lambda x: -x[1][1]))
          + f" | total {tot[0]} requests, {tot[1] / 1e6:.1f} MB")
