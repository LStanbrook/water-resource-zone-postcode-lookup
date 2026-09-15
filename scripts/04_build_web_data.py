"""
Build the compact data files the WRZ Finder web tool (data/web/) fetches at
runtime: a postcode-sector-sharded lookup, small zone/LAD reference tables,
and two map layers for the "Map" tab.

Run after 03_build_master_table.py.

Output (all under data/web/):
  zones.json, lad_names.json         - small reference tables
  areas/<AREA>.txt                   - one file per postcode area, one line
                                        per postcode: rest|zoneId|ladId|matchCode|snapDist|qaCode
  map_zones.json                     - simplified per-zone boundaries (for hover/tooltip)
  map_fill.json                      - per-company dissolved shapes, with only
                                        sub-1km2 digitisation slivers closed
                                        (genuine larger gaps stay visible)
  basemap.jpg                        - see fetch_basemap() - static map image,
                                        pre-projected to Web Mercator so it lines
                                        up with Leaflet's default rendering
"""
import json
import math
import os
import pandas as pd
import geopandas as gpd
import requests
from shapely.validation import make_valid
from shapely.ops import unary_union
from shapely.geometry import Polygon, MultiPolygon

RAW = "data/raw"
PROC = "data/processed"
WEB = "data/web"
LAD_NAMES_CSV = f"{RAW}/onspd_docs/Documents/LAD Local Authority District names and codes UK as at 04_25.csv"


def clean(v):
    return None if pd.isna(v) else v


def build_lookup_shards():
    os.makedirs(f"{WEB}/areas", exist_ok=True)
    master = pd.read_csv(f"{PROC}/postcode_to_wrz_master.csv", dtype=str)
    master["snap_distance_m"] = pd.to_numeric(master["snap_distance_m"], errors="coerce")

    lad = pd.read_csv(LAD_NAMES_CSV, dtype=str)
    lad_map = dict(zip(lad["LAD25CD"], lad["LAD25NM"]))

    zone_cols = ["wrz_name", "water_company", "wrz_region", "wrz_source", "wrz_code"]
    zones_df = master[zone_cols].drop_duplicates().reset_index(drop=True)
    zones_df["zid"] = zones_df.index
    zone_key_to_id = {tuple(r[c] if pd.notna(r[c]) else None for c in zone_cols): r["zid"]
                       for _, r in zones_df.iterrows()}
    zones_list = [{"n": clean(r["wrz_name"]), "c": clean(r["water_company"]), "r": clean(r["wrz_region"]),
                   "s": clean(r["wrz_source"]), "code": clean(r["wrz_code"])} for _, r in zones_df.iterrows()]
    with open(f"{WEB}/zones.json", "w", encoding="utf-8") as f:
        json.dump(zones_list, f, separators=(",", ":"))

    used_lads = sorted(master["lad_code"].dropna().unique())
    lad_id_map = {code: i for i, code in enumerate(used_lads)}
    lad_list = [lad_map.get(code, code) for code in used_lads]
    with open(f"{WEB}/lad_names.json", "w", encoding="utf-8") as f:
        json.dump(lad_list, f, separators=(",", ":"))

    def get_zid(row):
        key = tuple(row[c] if pd.notna(row[c]) else None for c in zone_cols)
        return zone_key_to_id[key]

    master["zid"] = master.apply(get_zid, axis=1)
    master["ladid"] = master["lad_code"].map(lad_id_map)

    mcode_map = {"within": "", "nearest": "1", "no_coordinates": "2"}
    master["mcode"] = master["match_method"].map(mcode_map)
    qcode_map = {"match": "", "mismatch": "1", "not_checked": "2"}
    master["qcode"] = master["mosl_sector_qa"].map(qcode_map).fillna("2")
    master["area"] = master["postcode"].str.extract(r"^([A-Z]+)")[0]

    for area, grp in master.groupby("area"):
        lines = []
        for r in grp.itertuples(index=False):
            rest = r.postcode[len(area):].strip()
            ladid = "" if pd.isna(r.ladid) else str(int(r.ladid))
            dist = "" if r.mcode != "1" else str(int(round(r.snap_distance_m)))
            lines.append(f"{rest}|{r.zid}|{ladid}|{r.mcode}|{dist}|{r.qcode}")
        with open(f"{WEB}/areas/{area}.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    print(f"{len(used_lads)} LADs, {master['area'].nunique()} postcode areas")


def round_coords(geom_dict, nd=5):
    def rc(coords):
        if isinstance(coords[0], (float, int)):
            return [round(c, nd) for c in coords]
        return [rc(c) for c in coords]
    geom_dict["coordinates"] = rc(geom_dict["coordinates"])
    return geom_dict


def fill_small_holes(geom, threshold_m2=1e6):
    """Close interior gaps under threshold_m2 (digitisation noise); keep larger
    ones (genuine gaps in the source data, e.g. the ~103km2 Wye Valley hole)."""
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    out = [Polygon(p.exterior, [i for i in p.interiors if Polygon(i).area >= threshold_m2]) for p in polys]
    return MultiPolygon(out) if len(out) > 1 else out[0]


def load_combined_zones():
    mosl = gpd.read_file(f"{RAW}/mosl_wrz_boundaries.geojson")
    mosl["geometry"] = mosl.geometry.apply(make_valid)
    mosl["Water_Company"] = mosl["Water_Company"].str.strip()

    ea = gpd.read_file(f"{PROC}/wrz_valid_bng.gpkg")
    scilly = ea[ea["wrz_name"].str.contains("Scilly", case=False)][
        ["company", "wrz_name", "rzcode", "reg_group", "geometry"]
    ].copy()
    scilly = scilly.rename(columns={"company": "Water_Company", "wrz_name": "WRZ_Name_1",
                                     "rzcode": "WRZ_Code", "reg_group": "Region"})

    combined = pd.concat([mosl[["Water_Company", "WRZ_Name_1", "WRZ_Code", "Region", "geometry"]],
                           scilly[["Water_Company", "WRZ_Name_1", "WRZ_Code", "Region", "geometry"]]],
                          ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=27700)


def build_map_zones(combined):
    zones_simplified = combined.copy()
    zones_simplified["geometry"] = zones_simplified.geometry.simplify(30, preserve_topology=True)
    zones_ll = zones_simplified.to_crs(4326)

    features = []
    for _, row in zones_ll.iterrows():
        geom = round_coords(json.loads(gpd.GeoSeries([row.geometry]).to_json())["features"][0]["geometry"])
        features.append({
            "type": "Feature",
            "properties": {"n": clean(row["WRZ_Name_1"]), "c": clean(row["Water_Company"]),
                            "code": clean(row["WRZ_Code"]), "r": clean(row["Region"])},
            "geometry": geom,
        })
    out = json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"), allow_nan=False)
    with open(f"{WEB}/map_zones.json", "w", encoding="utf-8") as f:
        f.write(out)


def build_map_fill(combined):
    features = []
    for company, grp in combined.groupby("Water_Company"):
        u = unary_union(grp.geometry.values)
        filled = fill_small_holes(u, threshold_m2=1e6)
        filled = filled.simplify(30, preserve_topology=True)
        gser = gpd.GeoSeries([filled], crs=27700).to_crs(4326)
        geom = round_coords(json.loads(gser.to_json())["features"][0]["geometry"])
        features.append({"type": "Feature", "properties": {"c": clean(company)}, "geometry": geom})
    out = json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"), allow_nan=False)
    with open(f"{WEB}/map_fill.json", "w", encoding="utf-8") as f:
        f.write(out)


# Bounding box the basemap image is exported at (EPSG:4326) - the web tool's
# Leaflet map uses this exact box for its L.imageOverlay, so keep the two in sync.
BASEMAP_BBOX_4326 = (-6.446, 49.86, 1.77, 55.82)  # west, south, east, north


def fetch_basemap():
    """Static Esri World Street Map export, pre-projected to Web Mercator
    (EPSG:3857) so its pixels line up with Leaflet's default projection -
    a plain lat/lon export looks fine but drifts north-south when overlaid,
    since Leaflet renders in Mercator, not equirectangular."""
    def lonlat_to_mercator(lon, lat):
        x = lon * 20037508.34 / 180
        y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180) * 20037508.34 / 180
        return x, y

    west, south, east, north = BASEMAP_BBOX_4326
    xmin, ymin = lonlat_to_mercator(west, south)
    xmax, ymax = lonlat_to_mercator(east, north)
    width = 1200
    height = round(width / ((xmax - xmin) / (ymax - ymin)))

    url = (
        "https://services.arcgisonline.com/arcgis/rest/services/World_Street_Map/MapServer/export"
        f"?bbox={xmin},{ymin},{xmax},{ymax}&bboxSR=3857&imageSR=3857"
        f"&size={width},{height}&format=png32&transparent=false&f=image"
    )
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    tmp_png = f"{WEB}/_basemap_tmp.png"
    with open(tmp_png, "wb") as f:
        f.write(r.content)

    from PIL import Image
    im = Image.open(tmp_png).convert("RGB")
    im.save(f"{WEB}/basemap.jpg", "JPEG", quality=82, optimize=True)
    os.remove(tmp_png)


if __name__ == "__main__":
    build_lookup_shards()
    combined = load_combined_zones()
    build_map_zones(combined)
    build_map_fill(combined)
    fetch_basemap()
