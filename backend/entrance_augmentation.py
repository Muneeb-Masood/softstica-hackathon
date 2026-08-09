#!/usr/bin/env python3
"""
Entrance augmentation (Option A fix for the brief's "entrances" criterion).

PROBLEM this fixes: OSM's pedestrian network only maps public streets, not
building interiors, so every lat/lon point near or inside a large building
snaps to whichever single nearby street node happens to be geometrically
closest (distance_ranking.py's "KNOWN LIMITATION" docstring;
crowd_simulation.py's indoor_snap_degenerate finding -- e.g. 64 simulated
points at Equarius Hotel collapsing onto 7 street nodes). That makes the
walking graph blind to "entrances" specifically, one of the criteria the
brief names explicitly ("may account for ... entrances").

FIX: for every AED-relevant building with a mapped footprint polygon
(build_building_footprints.py), sample several points spaced around its
perimeter and add each as a synthetic graph node, connected by a short
estimated edge to the nearest real street node. This gives points near/
inside that building several plausible "doors" to snap to instead of one,
without inventing a real floor plan or claiming real accuracy.

WHAT THIS IS NOT: not a real indoor path, not a verified entrance location
-- these are evenly-spaced guesses around the building outline, not surveyed
doors. Edges/nodes are tagged synthetic=True end to end, and
distance_ranking.py surfaces uses_synthetic_entrance=True on any walking
result whose path uses one, exactly like uses_stairs/crosses_unmarked_road
-- an honesty flag, not a claim of precision.

MATCHING: an AED is treated as "belonging" to a building footprint if it
falls within AED_BUILDING_BUFFER_M of the polygon (covers both AEDs mapped
just outside the outline and the common case of the AED being inside it).
Buildings with no AED nearby are not augmented -- there is no ranking
benefit to adding synthetic entrances where nothing is being routed to.

Matching and perimeter sampling are done in EPSG:3414 (SVY21, Singapore's
projected/metric CRS) for correct meter-based distances and spacing, then
reprojected back to WGS84 (lon/lat) before touching the walk graph, which
stays in WGS84 throughout (osmnx convention).
"""

import os
from typing import List, Optional

import geopandas as gpd
import osmnx as ox
from shapely.geometry import Point, Polygon

from build_building_footprints import load_cached_footprints
from build_walk_graph import load_cached_graph

AUGMENTED_GRAPH_PATH = os.path.join(os.path.dirname(__file__), "sentosa_walk_graph_augmented.graphml")
AEDS_PATH = os.path.join(os.path.dirname(__file__), "sentosa_aeds.geojson")

METRIC_CRS = "EPSG:3414"  # SVY21, Singapore's local projected CRS (meters)

AED_BUILDING_BUFFER_M = 25.0  # how close an AED must be to a footprint to "belong" to it
TARGET_SPACING_M = 15.0  # aim for one synthetic entrance roughly every this many meters of perimeter
MIN_ENTRANCE_POINTS = 4
# Large single buildings (e.g. Equarius Hotel, ~500m perimeter) need more
# than 10 points to actually achieve ~15m spacing -- a cap of 10 there
# works out to one entrance every ~50m, which barely improved on the
# pre-augmentation single-node collapse (findings.md, 2026-08-09: 7 -> 12
# distinct snap nodes for Equarius Hotel's crowd-sim grid, still
# indoor_snap_degenerate=True). Raised so perimeter/TARGET_SPACING_M sets
# the actual point count for most buildings instead of hitting this cap.
MAX_ENTRANCE_POINTS = 24

_augmented_graph_cache: Optional["ox.MultiDiGraph"] = None


def _load_aed_points_metric() -> "gpd.GeoDataFrame":
    import json

    with open(AEDS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    lats = [feat["properties"]["LATITUDE"] for feat in data["features"]]
    lons = [feat["properties"]["LONGITUDE"] for feat in data["features"]]
    gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat) for lon, lat in zip(lons, lats)], crs="EPSG:4326")
    return gdf.to_crs(METRIC_CRS)


def _largest_polygon(geom) -> Optional[Polygon]:
    """MultiPolygon -> its largest member by area; Polygon passes through unchanged."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type == "MultiPolygon":
        return max(geom.geoms, key=lambda p: p.area)
    return None


def _buildings_near_any_aed(buildings_metric: "gpd.GeoDataFrame", aed_points_metric: "gpd.GeoDataFrame") -> List[Polygon]:
    """Footprints (as plain Polygons, metric CRS) within AED_BUILDING_BUFFER_M of at least one AED."""
    matched: List[Polygon] = []
    aed_geoms = list(aed_points_metric.geometry)
    for geom in buildings_metric.geometry:
        poly = _largest_polygon(geom)
        if poly is None:
            continue
        if any(poly.distance(pt) <= AED_BUILDING_BUFFER_M for pt in aed_geoms):
            matched.append(poly)
    return matched


def _sample_perimeter_points(polygon: Polygon) -> List[Point]:
    """
    Evenly-spaced points around a polygon's exterior ring, in the same
    (metric) CRS the polygon is already in. Point count scales with
    perimeter length so a small kiosk doesn't get as many synthetic
    entrances as a hotel block, clipped to a sane range per building.
    """
    perimeter_m = polygon.exterior.length
    n = round(perimeter_m / TARGET_SPACING_M)
    n = max(MIN_ENTRANCE_POINTS, min(MAX_ENTRANCE_POINTS, n))
    return [polygon.exterior.interpolate(i / n, normalized=True) for i in range(n)]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_and_cache_augmented_graph() -> "ox.MultiDiGraph":
    """
    Load the base walk graph + building footprints + AEDs, add synthetic
    entrance nodes/edges around every AED-relevant building footprint, and
    cache the result. Idempotent to re-run (re-derives from the base graph
    each time rather than augmenting an already-augmented file).
    """
    print("Building entrance-augmented walk graph...")
    base_graph = load_cached_graph()
    graph = base_graph.copy()

    footprints = load_cached_footprints()
    footprints_metric = footprints.to_crs(METRIC_CRS)
    aed_points_metric = _load_aed_points_metric()

    matched_buildings = _buildings_near_any_aed(footprints_metric, aed_points_metric)
    print(f"{len(matched_buildings)} of {len(footprints_metric)} footprints are within "
          f"{AED_BUILDING_BUFFER_M}m of at least one AED -- augmenting only those.")

    next_synthetic_id = -1
    total_points = 0

    for poly in matched_buildings:
        perimeter_points_metric = _sample_perimeter_points(poly)
        perimeter_points_wgs84 = gpd.GeoSeries(perimeter_points_metric, crs=METRIC_CRS).to_crs("EPSG:4326")

        lons = [pt.x for pt in perimeter_points_wgs84]
        lats = [pt.y for pt in perimeter_points_wgs84]
        nearest_nodes = ox.distance.nearest_nodes(base_graph, X=lons, Y=lats)

        for lon, lat, nearest_node in zip(lons, lats, nearest_nodes):
            node_data = base_graph.nodes[nearest_node]
            dist_m = _haversine_m(lat, lon, node_data["y"], node_data["x"])

            synthetic_id = next_synthetic_id
            next_synthetic_id -= 1

            graph.add_node(synthetic_id, x=lon, y=lat, street_count=1, synthetic=True)
            graph.add_edge(synthetic_id, nearest_node, key=0, length=dist_m, synthetic=True)
            graph.add_edge(nearest_node, synthetic_id, key=0, length=dist_m, synthetic=True)
            total_points += 1

    print(f"Added {total_points} synthetic entrance nodes across {len(matched_buildings)} buildings.")
    ox.save_graphml(graph, AUGMENTED_GRAPH_PATH)
    print(f"Saved augmented graph to {AUGMENTED_GRAPH_PATH}")
    return graph


def load_augmented_graph() -> "ox.MultiDiGraph":
    """Load the cached augmented graph from disk, building and caching it first if missing."""
    global _augmented_graph_cache
    if _augmented_graph_cache is None:
        if not os.path.exists(AUGMENTED_GRAPH_PATH):
            _augmented_graph_cache = build_and_cache_augmented_graph()
        else:
            _augmented_graph_cache = ox.load_graphml(AUGMENTED_GRAPH_PATH)
    return _augmented_graph_cache


if __name__ == "__main__":
    if os.path.exists(AUGMENTED_GRAPH_PATH):
        print(f"Cache hit: loading existing augmented graph from {AUGMENTED_GRAPH_PATH} (rebuilding fresh instead, since this is the build script)")
    graph = build_and_cache_augmented_graph()
    n_synthetic = sum(1 for _, d in graph.nodes(data=True) if d.get("synthetic"))
    print(f"Total nodes: {graph.number_of_nodes()} ({n_synthetic} synthetic)")
    print(f"Total edges: {graph.number_of_edges()}")
    print(f"Cached file size: {os.path.getsize(AUGMENTED_GRAPH_PATH) / 1024:.1f} KB")
