"""
Fetch Water Resource Zone (WRZ) boundary datasets.

Sources:
  - Environment Agency WRZ boundaries (OGC API Features) - England + partial Wales
    https://environment.data.gov.uk/dataset/72a77500-d465-11e4-a8b3-f0def148f590
  - MOSL WRZ boundaries (128 zones, full England + Wales coverage)
    https://mosl.co.uk/market-insight/market-performance/environmental-impact/water-resource-zones-wrzs
  - Ofwat / Ordnance Survey water company "appointed area" boundaries
    (used only for QA - confirming which company covers the parts of Wales
    the EA dataset omits)

Output: data/raw/water_resource_zones.geojson
        data/raw/mosl_wrz_boundaries.geojson
        data/raw/water_company_boundaries.geojson
        data/raw/mosl/Postal_Sector_by_Water_Resource_Zone.xlsx
        data/raw/mosl/WRZ_Map_Data.xlsx
"""
import json
import os
import requests

RAW = "data/raw"
os.makedirs(RAW, exist_ok=True)
os.makedirs(f"{RAW}/mosl", exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_ea_wrz():
    url = (
        "https://environment.data.gov.uk/geoservices/datasets/"
        "72a77500-d465-11e4-a8b3-f0def148f590/ogc/features/v1/collections/"
        "Water_Resource_Zones/items?f=application%2Fjson&limit=500"
    )
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    d = r.json()
    assert d["numberReturned"] == d["numberMatched"], "paginate - EA dataset grew beyond one page"
    with open(f"{RAW}/water_resource_zones.geojson", "w", encoding="utf-8") as f:
        json.dump(d, f)
    print(f"EA WRZ: {len(d['features'])} zones")


def fetch_mosl_wrz():
    # discovered via the webmap embedded on the MOSL WRZ page
    url = (
        "https://services-eu1.arcgis.com/WtojzgQcT8v03gyt/arcgis/rest/services/"
        "WRZ_Supply_Demand_Balance_2025/FeatureServer/0/query"
        "?where=1%3D1&outFields=*&f=geojson&outSR=27700"
    )
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    with open(f"{RAW}/mosl_wrz_boundaries.geojson", "wb") as f:
        f.write(r.content)
    d = json.loads(r.content)
    print(f"MOSL WRZ: {len(d['features'])} zones")


def fetch_mosl_tables():
    files = {
        "postal-sector-by-water-resource-zone": "Postal_Sector_by_Water_Resource_Zone.xlsx",
        "wrz-map-data": "WRZ_Map_Data.xlsx",
    }
    # NOTE: MOSL's /doclink/ URLs are JWT-signed and expire - re-find the
    # current links from https://mosl.co.uk/market-insight/market-performance/
    # environmental-impact/water-resource-zones-wrzs ("Download WRZ by..."
    # buttons) if these have gone stale.
    print("MOSL table downloads: fetch current /doclink/ URLs manually from "
          "the MOSL WRZ page and save into data/raw/mosl/ (see file names above).")


def fetch_water_company_boundaries():
    # Ofwat/OS "appointed area" boundaries, hosted via the Stream Water Data portal
    url = (
        "https://services-eu1.arcgis.com/XxS6FebPX29TRGDJ/arcgis/rest/services/"
        "UC2/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson&outSR=27700"
    )
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(f"{RAW}/water_company_boundaries.geojson", "wb") as f:
        f.write(r.content)
    print("Water company appointed-area boundaries downloaded")


if __name__ == "__main__":
    fetch_ea_wrz()
    fetch_mosl_wrz()
    fetch_water_company_boundaries()
    fetch_mosl_tables()
