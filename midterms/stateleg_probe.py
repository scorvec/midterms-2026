"""One-off probe (state-leg branch): list the files of the source datasets and print headers, so the builder can be
written against the real layouts. Runs in GitHub Actions only.   python -m midterms.stateleg_probe"""
import io, json, sys, zipfile, gzip, urllib.request, urllib.parse
from .fetch import open_url, report

DV = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/{}"
SETS = {"medsl2024_state": "DODOBJ", "medsl2024_pres": "XDJYKC", "medsl2022_state": "OAARCY", "medsl2020": "OKL2K1",
        "medsl2018": "ZFXEJU", "klarner": "FJOGJB"}


def files(doi):
    j = json.loads(open_url(DV.format(doi)))
    out = []
    for f in j["data"]["latestVersion"]["files"]:
        d = f["dataFile"]; out.append((d["id"], f.get("directoryLabel", ""), d.get("filename"), d.get("filesize"), d.get("originalFileFormat", d.get("contentType"))))
    return j["data"]["latestVersion"].get("versionNumber"), out


def head(fid, n=4000, tab=False):
    url = f"https://dataverse.harvard.edu/api/access/datafile/{fid}" + ("?format=original" if tab else "")
    req = urllib.request.Request(url, headers={"User-Agent": "scorvec.com midterm model (+https://scorvec.com/midterms/about.html)", "Range": f"bytes=0-{n}"})
    with urllib.request.urlopen(req, timeout=120) as r: return r.read(n + 1), r.headers.get("Content-Type"), r.status


def main():
    allf = {}
    for k, doi in SETS.items():
        try:
            v, fs = files(doi); allf[k] = fs
            print(f"\n== {k} {doi} v{v}: {len(fs)} files")
            for f in fs[:400]: print("  ", f)
        except Exception as e: print(k, "FAILED", e)
    # search VEST datasets
    for q in ("VEST 2024 precinct", "Voting and Election Science Team 2024", "VEST 2020 precinct", "VEST 2016 precinct", "2024 Precinct-Level Election Results"):
        try:
            j = json.loads(open_url("https://dataverse.harvard.edu/api/search?type=dataset&per_page=15&q=" + urllib.parse.quote(q)))
            print(f"\n== search {q!r}")
            for it in j["data"]["items"]: print("  ", it.get("global_id"), "|", it.get("name"), "|", it.get("published_at"))
        except Exception as e: print("search failed", e)
    # headers of a few files
    want = [("medsl2024_state", "nh"), ("medsl2024_pres", "nh"), ("medsl2024_state", "mi"), ("medsl2022_state", "mi"), ("medsl2020", "nh"), ("medsl2018", "nh"), ("klarner", "")]
    for k, st in want:
        for fid, d, name, size, fmt in allf.get(k, []):
            nm = (name or "").lower()
            if st and not (nm.startswith(st + "_") or f"_{st}_" in nm or f"_{st}." in nm or nm.startswith(st + ".") or f"/{st}" in nm or nm.startswith(st)): continue
            try:
                b, ct, status = head(fid, 6000, tab=str(fmt).startswith("text/") is False)
                print(f"\n== head {k} {name} ({size} B, {fmt}, {ct}, http {status})")
                if b[:2] == b"PK": print("  (zip; range read not decodable)")
                elif b[:2] == b"\x1f\x8b": print("  (gzip)"); 
                else: print(b[:3000].decode("utf-8", "replace"))
            except Exception as e: print("head failed", name, e)
            break
    # TIGER listings
    for y in (2018, 2022, 2024, 2025):
        for lay in ("SLDU", "SLDL"):
            try:
                h = open_url(f"https://www2.census.gov/geo/tiger/TIGER{y}/{lay}/").decode()
                import re
                fs = re.findall(r'href="(tl_[^"]+_(26|27|55|04|42|33|37)_[^"]+\.zip)"', h)
                print(f"TIGER{y} {lay}:", sorted(set(f for f, _ in fs)))
            except Exception as e: print("tiger", y, lay, e)
    report()


if __name__ == "__main__":
    main()
