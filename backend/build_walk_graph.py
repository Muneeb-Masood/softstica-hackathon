#!/usr/bin/env python3
"""
Download and cache the Sentosa walking network (Phase 4).

Downloads the OSM pedestrian/walking street network for a bounding box
around Sentosa island using osmnx, and saves it to a local GraphML file so
the demo never re-downloads it live. Run this once; ranking code loads the
cached file instead of calling osmnx at request time.

Bounding box: slightly padded beyond the AED coordinate range found in
sentosa_aeds.geojson (lat 1.24886 to 1.25915, lon 103.81628 to 103.82492),
so that walking routes near the edge of the AED cluster aren't cut off by
the network boundary itself.
"""

import os

import osmnx as ox

GRAPH_PATH = os.path.join(os.path.dirname(__file__), "sentosa_walk_graph.graphml")

# Padded a bit beyond the observed AED lat/lon range so routes near the edge
# of the AED cluster aren't clipped by the network boundary.
NORTH, SOUTH = 1.2620, 1.2460
EAST, WEST = 103.8280, 103.8130


def build_and_cache_graph() -> "ox.MultiDiGraph":
    print(f"Downloading walking network for bbox N={NORTH} S={SOUTH} E={EAST} W={WEST} ...")
    graph = ox.graph_from_bbox(
        bbox=(WEST, SOUTH, EAST, NORTH),
        network_type="walk",
        simplify=True,
    )
    ox.save_graphml(graph, GRAPH_PATH)
    print(f"Saved graph to {GRAPH_PATH}")
    return graph


def load_cached_graph() -> "ox.MultiDiGraph":
    """Load the cached graph from disk, building and caching it first if missing."""
    if not os.path.exists(GRAPH_PATH):
        return build_and_cache_graph()
    return ox.load_graphml(GRAPH_PATH)


if __name__ == "__main__":
    if os.path.exists(GRAPH_PATH):
        print(f"Cache hit: loading existing graph from {GRAPH_PATH} (no download)")
        graph = ox.load_graphml(GRAPH_PATH)
    else:
        print("No cached graph found, downloading...")
        graph = build_and_cache_graph()
    print(f"Nodes: {graph.number_of_nodes()}")
    print(f"Edges: {graph.number_of_edges()}")
    print(f"Cached file exists: {os.path.exists(GRAPH_PATH)}")
    print(f"Cached file size: {os.path.getsize(GRAPH_PATH) / 1024:.1f} KB")
