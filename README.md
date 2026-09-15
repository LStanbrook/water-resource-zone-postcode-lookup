# Water Resource Zone postcode lookup

Which [Water Resource Zone](https://mosl.co.uk/market-insight/market-performance/environmental-impact/water-resource-zones-wrzs)
(the area a water company plans and balances supply for as one system) and
which water company serves each postcode in England & Wales.

**Live tool:** https://claude.ai/artifact/Evq6G3SrL2uR47DJApAesn - type a
postcode, district or sector; see the boundaries on a map; download the full
dataset.

## What's in this repo

```
scripts/    the pipeline, in order (01 -> 04)
data/web/   source of the live tool (small, sharded lookup data + the page itself)
dist/       the finished dataset, zipped, for direct download
```

`data/raw/` and most of `data/processed/` are git-ignored - they're large
(the ONS Postcode Directory alone is 250MB+) and fully regenerable by running
the scripts. `dist/` holds the two files worth keeping in git: the finished
postcode-to-zone table and the boundary set it was built from, both zipped.

## Method

1. **Boundaries**: [MOSL](https://mosl.co.uk/market-insight/market-performance/environmental-impact/water-resource-zones-wrzs)
   publishes all 128 Water Resource Zones for England & Wales (the
   [Environment Agency's own WRZ dataset](https://environment.data.gov.uk/dataset/72a77500-d465-11e4-a8b3-f0def148f590)
   is the more commonly-linked source, but only covers England plus a
   fraction of Wales - the two agree closely, at a median boundary overlap of
   99.95%, where they overlap). MOSL's 128 don't include a distinct Isles of
   Scilly zone, so that one polygon is added back from the EA set - 129
   zones in total.
2. **Postcodes**: the [ONS Postcode Directory](https://geoportal.statistics.gov.uk/),
   filtered to live England & Wales postcodes with a grid reference.
3. **Join**: point-in-polygon, postcode grid reference against zone boundary.
   The ~0.08% of postcodes that fall outside every boundary (precision limits
   in the source polygons, plus genuinely isolated places like Lundy Island)
   are assigned to their nearest zone, with the distance recorded.
4. **QA**: every result is cross-checked against MOSL's own independently-
   published postcode-sector lookup table. 97.6% match exactly; the rest are
   flagged, not silently resolved.

## Output columns

| Column | Meaning |
|---|---|
| `postcode` | Full unit postcode |
| `lad_code` | ONS Local Authority District code |
| `water_company`, `wrz_name`, `wrz_code`, `wrz_region` | The assigned zone |
| `wrz_source` | `MOSL` or `EA (not in MOSL)` (Isles of Scilly only) |
| `match_method` | `within` (direct match), `nearest` (snapped - see `note`), `no_coordinates` |
| `snap_distance_m` | Distance for nearest-matches (0 for direct matches) |
| `note` | Explanation for anything not a direct match |
| `mosl_sector_qa` | Cross-check result against MOSL's sector table: `match` / `mismatch` / `not_checked` |

## Known limitations

- Scotland and Northern Ireland aren't covered - out of scope for both source
  datasets.
- A handful of genuine gaps exist in the source boundary data itself (e.g.
  ~103km² near the Wye Valley, two pockets inside Anglian Water's area) -
  these are disclosed, not papered over.
- Postcodes sitting within a few metres of a zone boundary line inherit
  whatever precision the original boundary was digitised at - that's a limit
  of the source data, not of this pipeline.

## Reproducing

```
pip install -r requirements.txt
python scripts/01_fetch_boundaries.py
python scripts/02_fetch_onspd.py
python scripts/03_build_master_table.py
python scripts/04_build_web_data.py
```

## Data sources & licensing

- Environment Agency WRZ boundaries - Open Government Licence
- MOSL WRZ boundaries & postal-sector lookup - published by MOSL for water
  market use
- Ofwat / Ordnance Survey water company boundaries - Open Government Licence
- ONS Postcode Directory - Open Government Licence; contains Royal Mail,
  Ordnance Survey and ONS intellectual property
