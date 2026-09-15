"""
Download the latest ONS Postcode Directory (ONSPD) and extract the columns
needed for the WRZ join: live England & Wales postcodes with a National
Grid reference.

ONSPD is published quarterly on the ONS Open Geography Portal (ArcGIS Online,
owner "ONSGeography_data"). This script finds the current release
automatically rather than hardcoding a dated URL.

Output: data/processed/onspd_ew_live.csv
        (columns: pcds, doterm, ctry26cd, lad26cd, east1m, north1m, gridind)
        data/raw/onspd/lad_names.csv
        (LAD code -> name, for display in the web tool)
"""
import io
import os
import zipfile
import requests
import pandas as pd

RAW = "data/raw/onspd"
OUT = "data/processed/onspd_ew_live.csv"
os.makedirs(RAW, exist_ok=True)
os.makedirs("data/processed", exist_ok=True)

KEEP_COLS = ["pcds", "doterm", "ctry26cd", "lad26cd", "east1m", "north1m", "gridind"]
LIVE_COUNTRIES = ["E92000001", "W92000004"]  # England, Wales


def find_latest_onspd_item():
    r = requests.get(
        "https://www.arcgis.com/sharing/rest/search",
        params={
            "q": 'title:"ONS Postcode Directory" AND owner:ONSGeography_data',
            "f": "json", "num": 20, "sortField": "created", "sortOrder": "desc",
        },
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()["results"]
    zips = [x for x in results if x["type"] == "CSV Collection" and "User Guide" not in x["title"]]
    return zips[0]  # newest


def download_and_filter():
    item = find_latest_onspd_item()
    print(f"Using {item['title']} ({item['id']})")
    zip_path = f"{RAW}/{item['name']}"
    if not os.path.exists(zip_path):
        url = f"https://www.arcgis.com/sharing/rest/content/items/{item['id']}/data"
        r = requests.get(url, timeout=300, stream=True)
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        print(f"Downloaded {zip_path}")

    z = zipfile.ZipFile(zip_path)
    combined_name = next(n for n in z.namelist() if n.startswith("Data/ONSPD_") and n.endswith("_UK.csv"))

    chunks = []
    with z.open(combined_name) as f:
        for chunk in pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"), usecols=KEEP_COLS, dtype=str, chunksize=300_000):
            chunk = chunk[chunk["doterm"].isna()]
            chunk = chunk[chunk["ctry26cd"].isin(LIVE_COUNTRIES)]
            chunks.append(chunk)

    df = pd.concat(chunks, ignore_index=True)
    df.to_csv(OUT, index=False)
    print(f"Live England & Wales postcodes: {len(df)} -> {OUT}")

    lad_name = next(n for n in z.namelist() if n.startswith("Documents/LAD Local Authority District names and codes"))
    with z.open(lad_name) as src, open(f"{RAW}/lad_names.csv", "wb") as dst:
        dst.write(src.read())
    print(f"LAD names -> {RAW}/lad_names.csv")


if __name__ == "__main__":
    download_and_filter()
