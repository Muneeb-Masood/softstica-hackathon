#!/usr/bin/env python3
"""
Download and cache OSM building footprints for the Sentosa bbox.

Input for entrance_augmentation.py's Option A fix: OSM's pedestrian network
only maps public streets/paths, not building interiors, so a lat/lon point
near or inside a large attraction snaps to whichever single perimeter
street node happens to be geometrically closest (see distance_ranking.py's
"KNOWN LIMITATION" docstring and crowd_simulation.py's
indoor_snap_degenerate finding). Building footprint polygons -- which OSM
usually does have, even for buildings with no mapped interior paths -- let
entrance_augmentation.py spread several synthetic "entrance" points around
each building's perimeter instead of relying on the single nearest street
node, which is what the brief's "entrances" scoring criterion is about.

Downloads once and caches to sentosa_building_footprints.geojson so the
demo never re-queries Overpass live. Run this once; entrance_augmentation.py
reads the cached file instead.
"""

import os

import osmnx as ox

from build_walk_graph import EAST, NORTH, SOUTH, WEST

FOOTPRINTS_PATH = os.path.join(os.path.dirname(__file__), "sentosa_building_footprints.geojson")


def build_and_cache_footprints():
    """
    Download building footprints for the same bbox as the walk graph, keep
    only polygonal geometries (a handful of buildings are mapped as a bare
    node with building=yes and no outline -- not usable for perimeter
    sampling), and cache to disk.
    """
    print(f"Downloading building footprints for bbox N={NORTH} S={SOUTH} E={EAST} W={WEST} ...")
    gdf = ox.features_from_bbox(bbox=(WEST, SOUTH, EAST, NORTH), tags={"building": True})
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    keep_cols = [c for c in ("geometry", "name", "building") if c in gdf.columns]
    gdf = gdf[keep_cols].reset_index()
    gdf.to_file(FOOTPRINTS_PATH, driver="GeoJSON")
    print(f"Saved {len(gdf)} building footprints to {FOOTPRINTS_PATH}")
    return gdf


def load_cached_footprints():
    """Load the cached footprints from disk, building and caching them first if missing."""
    import geopandas as gpd

    if not os.path.exists(FOOTPRINTS_PATH):
        return build_and_cache_footprints()
    return gpd.read_file(FOOTPRINTS_PATH)


if __name__ == "__main__":
    if os.path.exists(FOOTPRINTS_PATH):
        print(f"Cache hit: loading existing footprints from {FOOTPRINTS_PATH} (no download)")
        gdf = load_cached_footprints()
    else:
        print("No cached footprints found, downloading...")
        gdf = build_and_cache_footprints()
    print(f"Footprints: {len(gdf)}")
    print(f"Cached file exists: {os.path.exists(FOOTPRINTS_PATH)}")
    print(f"Cached file size: {os.path.getsize(FOOTPRINTS_PATH) / 1024:.1f} KB")
