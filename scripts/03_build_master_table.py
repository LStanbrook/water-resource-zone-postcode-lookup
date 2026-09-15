"""
Join every live England & Wales postcode to its Water Resource Zone.

Method:
  1. Use MOSL's 128-zone boundary set as the primary source (it agrees with
     the EA's own boundaries at a median IoU of 0.9995 on the 112 zones both
     datasets share, and additionally covers the ~16,000 km2 of Wales the EA
     dataset omits).
  2. Add back the EA's "Isles of Scilly" zone, which MOSL's 128-zone set
     doesn't carry as its own polygon.
  3. Point-in-polygon join every postcode against these 129 zones.
  4. Anything that falls outside every zone (boundary-precision edge cases,
     plus genuinely isolated places like Lundy Island) is assigned to its
     nearest zone, with the snap distance recorded.
  5. Cross-check the result against MOSL's own postal-sector-level lookup
     table as an independent QA pass.

Output: data/processed/postcode_to_wrz_master.csv
"""
import os
import pandas as pd
import geopandas as gpd
from shapely.validation import make_valid

RAW = "data/raw"
PROC = "data/processed"


def load_zones():
    mosl = gpd.read_file(f"{RAW}/mosl_wrz_boundaries.geojson")
    mosl["geometry"] = mosl.geometry.apply(make_valid)
    mosl["Water_Company"] = mosl["Water_Company"].str.strip()
    mosl = mosl[["Water_Company", "WRZ_Name_1", "WRZ_Code", "Region", "geometry"]].copy()
    mosl["source"] = "MOSL"

    ea = gpd.read_file(f"{PROC}/wrz_valid_bng.gpkg")
    scilly = ea[ea["wrz_name"].str.contains("Scilly", case=False)][
        ["company", "wrz_name", "rzcode", "reg_group", "geometry"]
    ].copy()
    scilly = scilly.rename(columns={
        "company": "Water_Company", "wrz_name": "WRZ_Name_1",
        "rzcode": "WRZ_Code", "reg_group": "Region",
    })
    scilly["source"] = "EA (not in MOSL)"

    zones = pd.concat([mosl, scilly], ignore_index=True)
    return gpd.GeoDataFrame(zones, geometry="geometry", crs=27700)


def load_postcode_points():
    df = pd.read_csv(f"{PROC}/onspd_ew_live.csv", dtype=str)
    full = df.copy()
    df = df[df["gridind"] != "9"].copy()  # rows with no grid reference at all
    df["east1m"] = df["east1m"].astype(float)
    df["north1m"] = df["north1m"].astype(float)
    geom = gpd.points_from_xy(df["east1m"], df["north1m"])
    pts = gpd.GeoDataFrame(df[["pcds", "lad26cd"]], geometry=geom, crs=27700)
    return full, pts


def build():
    zones = load_zones()
    full, pts = load_postcode_points()

    joined = gpd.sjoin(pts, zones, how="left", predicate="within")
    within = joined[joined["WRZ_Code"].notna()].copy()
    within["match_method"] = "within"
    within["snap_dist_m"] = 0.0

    unmatched_pcds = set(joined.loc[joined["WRZ_Code"].isna(), "pcds"])
    unmatched_pts = pts[pts["pcds"].isin(unmatched_pcds)]
    nearest = gpd.sjoin_nearest(unmatched_pts, zones, how="left", distance_col="snap_dist_m")
    nearest = nearest.sort_values("snap_dist_m").drop_duplicates(subset="pcds", keep="first")
    nearest["match_method"] = "nearest"
    print(f"within: {len(within)}  nearest-snapped: {len(nearest)}  "
          f"no-coordinates: {(full['gridind'] == '9').sum()}")

    cols = ["pcds", "lad26cd", "Water_Company", "WRZ_Name_1", "WRZ_Code", "Region", "source",
            "match_method", "snap_dist_m"]
    no_coords = full[full["gridind"] == "9"][["pcds", "lad26cd"]].copy()
    for c in ["Water_Company", "WRZ_Name_1", "WRZ_Code", "Region", "source"]:
        no_coords[c] = None
    no_coords["match_method"] = "no_coordinates"
    no_coords["snap_dist_m"] = None

    master = pd.concat([within[cols], nearest[cols], no_coords[cols]], ignore_index=True)
    master = master.rename(columns={
        "pcds": "postcode", "lad26cd": "lad_code", "Water_Company": "water_company",
        "WRZ_Name_1": "wrz_name", "WRZ_Code": "wrz_code", "Region": "wrz_region",
        "source": "wrz_source", "snap_dist_m": "snap_distance_m",
    })

    def make_note(row):
        if row["match_method"] == "nearest":
            d = row["snap_distance_m"]
            dist_str = f"{d:.0f} m" if d < 1000 else f"{d/1000:.1f} km"
            return (f"Postcode fell outside all zone boundaries in the source data; "
                     f"assigned to nearest zone, {dist_str} away.")
        if row["match_method"] == "no_coordinates":
            return "No grid reference available for this postcode in ONSPD; zone could not be determined."
        return ""

    master["note"] = master.apply(make_note, axis=1)
    qa_check(master)

    master.to_csv(f"{PROC}/postcode_to_wrz_master.csv", index=False)
    print(f"Wrote {len(master)} rows -> {PROC}/postcode_to_wrz_master.csv")


def qa_check(master):
    """Cross-check against MOSL's own postal-sector-level lookup table."""
    sector_path = f"{RAW}/mosl/Postal_Sector_by_Water_Resource_Zone.xlsx"
    if not os.path.exists(sector_path):
        print("MOSL sector table not found locally - skipping QA cross-check "
              "(see scripts/01_fetch_boundaries.py for where to get it)")
        master["mosl_sector_qa"] = "not_checked"
        return

    mosl_sector = pd.read_excel(sector_path, sheet_name="INT_PostalSector_WRZ25.shp")

    def to_sector(pc):
        parts = pc.strip().split(" ")
        return f"{parts[0]} {parts[1][0]}" if len(parts) == 2 else None

    master["sector"] = master["postcode"].apply(to_sector)
    sector_zones = mosl_sector.groupby("RMSect")["WRZ_2025"].apply(set).to_dict()

    def check(row):
        zs = sector_zones.get(row["sector"])
        if zs is None:
            return "not_checked"
        if pd.isna(row["wrz_name"]):
            return "not_checked"
        our = row["wrz_name"].strip().lower()
        match = any(our == z.strip().lower() or our in z.strip().lower() or z.strip().lower().endswith(our) for z in zs)
        return "match" if match else "mismatch"

    master["mosl_sector_qa"] = master.apply(check, axis=1)
    master.drop(columns=["sector"], inplace=True)
    print(master["mosl_sector_qa"].value_counts())


if __name__ == "__main__":
    build()
