"""One-off probe (state-leg branch): list the files of the source datasets and print headers, so the builder can be
written against the real layouts. Runs in GitHub Actions only.   python -m midterms.stateleg_probe"""
import io, json, sys, zipfile, urllib.request
from pathlib import Path
import pandas as pd
from .fetch import open_url, report

DV = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/{}"
SETS = {"m2024": "NYTPDU", "m2022": "UYQIEP", "m2020": "NT66Z3", "m2018": "NVQYMG", "vest2016": "NH5S2I", "vest2020": "K7760H"}
RAW = Path("data/raw/stateleg"); RAW.mkdir(parents=True, exist_ok=True)


def files(doi):
    j = json.loads(open_url(DV.format(doi)))
    return [(f["dataFile"]["id"], f.get("directoryLabel", ""), f["dataFile"].get("filename"), f["dataFile"].get("filesize")) for f in j["data"]["latestVersion"]["files"]]


def dl(fid, name):
    p = RAW / "probe" / name
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(open_url(f"https://dataverse.harvard.edu/api/access/datafile/{fid}?format=original", timeout=600))
    return p


def show_state_file(p):
    if p.suffix == ".zip":
        z = zipfile.ZipFile(p); print("   zip:", z.namelist()[:10]); n = [x for x in z.namelist() if x.endswith(".csv")][0]; df = pd.read_csv(z.open(n), dtype=str, low_memory=False)
    else: df = pd.read_csv(p, dtype=str, low_memory=False)
    print("   cols:", list(df.columns)); print(df.head(3).to_string()[:1500])
    off = df["office"].value_counts(); print("   offices:", off.head(25).to_dict())
    for o in off.index:
        if any(k in o.upper() for k in ("STATE HOUSE", "STATE SENATE", "STATE REP", "ASSEMBLY", "LEGISL", "GENERAL COURT")):
            q = df[df.office == o]; print(f"   {o}: {len(q)} rows, districts {q['district'].nunique()}: {sorted(q['district'].unique())[:12]}; magnitude {q['magnitude'].value_counts().to_dict() if 'magnitude' in q else ''}")
            print("   modes:", q["mode"].value_counts().head(8).to_dict() if "mode" in q else "", "| party:", q["party_simplified"].value_counts().to_dict() if "party_simplified" in q else "")
    pr = df[df.office.str.upper().str.contains("PRESIDENT", na=False)]
    if len(pr): print("   president rows", len(pr), "district values", pr["district"].value_counts().head(5).to_dict(), "modes", pr["mode"].value_counts().head(6).to_dict())


def main():
    L = {}
    for k, doi in SETS.items():
        try:
            fs = files(doi); L[k] = fs; print(f"\n== {k} {doi}: {len(fs)} files")
            for f in fs[:120]: print("  ", f)
        except Exception as e: print(k, "FAILED", e)
    for k in ("m2024", "m2022"):
        for st in ("NH", "MI"):
            hit = [f for f in L.get(k, []) if (f[2] or "").upper().split("-")[0].split("_")[0].split(".")[0] == st or (f[1] or "").upper() == st]
            hit = hit or [f for f in L.get(k, []) if f"_{st.lower()}" in (f[2] or "").lower() or (f[2] or "").lower().startswith(st.lower())]
            print(f"\n== {k} {st}: candidates {hit[:4]}")
            if hit:
                try: show_state_file(dl(hit[0][0], f"{k}_{hit[0][2]}"))
                except Exception as e: print("   failed", e)
    # Klarner
    for fid, nm in ((10273089, "203slers_uoa_cham_year20230810.tab"), (10273085, "127_slers_1967to2022.tab")):
        try:
            p = dl(fid, nm); df = pd.read_csv(p, sep=None, engine="python", nrows=200000 if "127" in nm else None, dtype=str) if "127" not in nm else pd.read_csv(p, sep="\t", dtype=str, low_memory=False)
            print(f"\n== klarner {nm}: {len(df)} rows; cols {list(df.columns)}"); print(df.head(5).to_string()[:3000])
            if "127" in nm:
                for c in df.columns:
                    if df[c].nunique() < 40: print("   ", c, df[c].value_counts().head(15).to_dict())
                q = df[(df.iloc[:, :].astype(str).apply(lambda r: "2022" in r.values, axis=1))].head(3) if False else None
                for col in ("year", "sab"):
                    if col in df: print(col, df[col].value_counts().sort_index().tail(10).to_dict())
                if "sab" in df and "year" in df: print(df[(df["sab"] == "NH") & (df["year"] == "2022")].head(12).to_string()[:3000]); print(df[(df["sab"] == "AZ") & (df["year"] == "2022")].head(8).to_string()[:2000])
            else:
                print(df[df.iloc[:, 0].isin(["MI", "WI", "MN", "PA", "AZ", "NH", "NC"])].tail(20).to_string()[:3000] if len(df) else "")
        except Exception as e: print("klarner failed", nm, e)
    report()


if __name__ == "__main__":
    main()
