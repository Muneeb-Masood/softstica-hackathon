"""
Profiling script for the full national AED_LOCATIONS.geojson dataset.
Read-only: does not modify any existing pipeline files or outputs.
"""
import json
from collections import Counter

PATH = "../AED_LOCATIONS.geojson"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

features = data["features"]
total = len(features)
print(f"Total records: {total}")

props = [f["properties"] for f in features]

# Unique OPERATING_HOURS strings
hours_values = [p.get("OPERATING_HOURS") for p in props]
unique_hours = Counter(h if h is not None else "<<NULL>>" for h in hours_values)
print(f"\nUnique OPERATING_HOURS strings: {len(unique_hours)}")

# Blank/missing floor level and description
def is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")

floor_blank = sum(1 for p in props if is_blank(p.get("AED_LOCATION_FLOOR_LEVEL")))
desc_blank = sum(1 for p in props if is_blank(p.get("AED_LOCATION_DESCRIPTION")))
print(f"\nBlank/missing AED_LOCATION_FLOOR_LEVEL: {floor_blank} ({floor_blank/total:.1%})")
print(f"Blank/missing AED_LOCATION_DESCRIPTION: {desc_blank} ({desc_blank/total:.1%})")

# Geographic spread
lats, lons = [], []
bad_coord_count = 0
for f in features:
    geom = f.get("geometry")
    if not geom or geom.get("type") != "Point":
        bad_coord_count += 1
        continue
    lon, lat = geom["coordinates"][0], geom["coordinates"][1]
    lats.append(lat)
    lons.append(lon)

print(f"\nGeometry issues (non-Point / missing): {bad_coord_count}")
print(f"Latitude range:  {min(lats):.6f} to {max(lats):.6f}")
print(f"Longitude range: {min(lons):.6f} to {max(lons):.6f}")

# Flag outliers outside a generous Singapore bounding box
SG_LAT_MIN, SG_LAT_MAX = 1.13, 1.48
SG_LON_MIN, SG_LON_MAX = 103.55, 104.10
outliers = []
for f in features:
    geom = f.get("geometry")
    if not geom or geom.get("type") != "Point":
        continue
    lon, lat = geom["coordinates"][0], geom["coordinates"][1]
    if not (SG_LAT_MIN <= lat <= SG_LAT_MAX and SG_LON_MIN <= lon <= SG_LON_MAX):
        outliers.append((f["properties"].get("AED_ID"), lat, lon))

print(f"\nRecords outside generous Singapore bbox ({SG_LAT_MIN}-{SG_LAT_MAX} lat, {SG_LON_MIN}-{SG_LON_MAX} lon): {len(outliers)}")
for aed_id, lat, lon in outliers[:20]:
    print(f"  {aed_id}: lat={lat}, lon={lon}")

# Procedural access barrier keywords
keywords = ["security", "approach", "guard", "officer", "call ahead", "school hours"]
desc_fields = ["AED_LOCATION_DESCRIPTION", "OPERATING_HOURS"]

matches_by_keyword = Counter()
matched_records = 0
for p in props:
    combined = " ".join(str(p.get(field, "") or "") for field in desc_fields).lower()
    hit = False
    for kw in keywords:
        if kw in combined:
            matches_by_keyword[kw] += 1
            hit = True
    if hit:
        matched_records += 1

print(f"\nRecords matching procedural access barrier keywords (in description or hours): {matched_records} ({matched_records/total:.1%})")
for kw, count in matches_by_keyword.most_common():
    print(f"  '{kw}': {count}")

# Show all unique OPERATING_HOURS strings (sorted by frequency) for reference
print(f"\nTop 30 most common OPERATING_HOURS strings:")
for val, count in unique_hours.most_common(30):
    display = val if len(str(val)) < 80 else str(val)[:77] + "..."
    print(f"  [{count:5d}] {display}")
