"""
AED distance ranking (Phase 4).

Two ranking functions over the same 66 Sentosa AEDs:
    rank_by_straight_line -- the literal required baseline per the brief
                              ("A basic pin map or straight-line nearest-
                              neighbour lookup is the required baseline,
                              not a complete submission"). Ignores whether
                              the straight line is actually walkable.
    rank_by_walking_time  -- real walking-network time using the cached OSM
                              graph (networkx shortest path, weighted by
                              edge length in meters, converted to time via
                              an assumed constant walking speed).

Both are kept so they can be compared directly in the evaluation section;
neither accounts for operating hours or trust score yet (that's Phase 5).

Walking-time assumption: OSM pedestrian ways in this bbox don't carry
reliable per-edge walking-speed tags, so travel time is derived from a
single constant assumed walking speed (WALKING_SPEED_M_PER_S below)
applied to the shortest-path distance, not per-edge speed data. This is a
documented simplification, not a measured or live speed.

AEDs whose nearest graph node is not connected to the test point's node
(no path exists in the walking network -- e.g. isolated pedestrian islands
or roads not connected to the walk network in OSM) are marked
reachable=False with walking_distance_m/walking_time_s left as None,
rather than being silently dropped or crashing the ranking.

KNOWN LIMITATION -- indoor/private venue interiors: OSM's pedestrian
network only covers publicly mapped streets and paths. Large attraction
interiors have no internal walkway data, so AED coordinates inside such a
venue snap to whichever nearby perimeter street node happens to be
geometrically closest, not to a real internal path. Measured for Universal
Studios Singapore (backend/test_crowd_simulation.py): only 2 of 657
Sentosa graph nodes fall inside its own AED bounding box, and its 19 AEDs
collapse onto just 10 distinct nearest-graph-nodes -- not the same single
node for all of them (the venue is large enough to have multiple sides
near different perimeter roads), but far fewer distinct nodes than the 19
you'd expect if the walking network could see indoor structure. AEDs
sharing a snap node can degenerately report identical (or near-zero)
walking distance/time from a test point even though they are physically
tens of meters apart inside the building. This is a real data-coverage
gap, not a bug in the snapping or shortest-path logic -- it should be
called out explicitly wherever walking-time results are shown for indoor
venues, and is a legitimate finding for the method card (straight-line
ranking can appear more "precise" than walking-time ranking purely because
the walking graph can't see indoor structure, not because it's actually
more correct). Phase 10's crowd simulation surfaces this directly via
distinct_snap_nodes/indoor_snap_degenerate (see crowd_simulation.py).
"""

import json
import math
import os
from typing import List, Optional, TypedDict

import networkx as nx
import osmnx as ox

GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "sentosa_aeds.geojson")
GRAPH_PATH = os.path.join(os.path.dirname(__file__), "sentosa_walk_graph.graphml")

EARTH_RADIUS_M = 6_371_000.0

# Assumed constant adult walking speed. OSM walking edges in this bbox carry
# no reliable per-edge pedestrian speed tags, so this is a documented
# simplification applied uniformly to shortest-path distance, not measured
# or live data. ~4.8 km/h, a standard planning-level walking pace.
WALKING_SPEED_M_PER_S = 1.34


class AedDistance(TypedDict):
    aed_id: str
    building_name: Optional[str]
    latitude: float
    longitude: float
    distance_m: float


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def load_aeds() -> List[dict]:
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [feat["properties"] for feat in data["features"]]


def rank_by_straight_line(
    test_lat: float, test_lon: float, aeds: Optional[List[dict]] = None
) -> List[AedDistance]:
    """
    Rank all Sentosa AEDs by straight-line (haversine) distance from a test point.

    This is the required baseline only -- it ignores operating hours, trust
    score, and whether a straight line is actually walkable (water, fences,
    building footprints, one-way pedestrian routes, etc).
    """
    if aeds is None:
        aeds = load_aeds()

    ranked: List[AedDistance] = []
    for props in aeds:
        lat = props["LATITUDE"]
        lon = props["LONGITUDE"]
        dist = haversine_distance_m(test_lat, test_lon, lat, lon)
        ranked.append(
            AedDistance(
                aed_id=props["AED_ID"],
                building_name=props.get("BUILDING_NAME"),
                latitude=lat,
                longitude=lon,
                distance_m=dist,
            )
        )

    ranked.sort(key=lambda r: r["distance_m"])
    return ranked


class AedWalkingResult(TypedDict):
    aed_id: str
    building_name: Optional[str]
    latitude: float
    longitude: float
    reachable: bool
    walking_distance_m: Optional[float]
    walking_time_s: Optional[float]


_graph_cache: Optional["ox.MultiDiGraph"] = None


def load_walk_graph() -> "ox.MultiDiGraph":
    """Load the cached Sentosa walking graph from disk (never re-downloads)."""
    global _graph_cache
    if _graph_cache is None:
        if not os.path.exists(GRAPH_PATH):
            raise FileNotFoundError(
                f"{GRAPH_PATH} not found. Run build_walk_graph.py first to "
                "download and cache the walking network."
            )
        _graph_cache = ox.load_graphml(GRAPH_PATH)
    return _graph_cache


def rank_by_walking_time(
    test_lat: float,
    test_lon: float,
    aeds: Optional[List[dict]] = None,
    graph: Optional["ox.MultiDiGraph"] = None,
) -> List[AedWalkingResult]:
    """
    Rank all Sentosa AEDs by real walking-network time from a test point.

    Snaps the test point and every AED to their nearest graph node, then
    uses a networkx single-source Dijkstra shortest path (weighted by edge
    length in meters) to compute real walking distance, converted to time
    via WALKING_SPEED_M_PER_S. AEDs with no path from the test point in the
    walking network are returned with reachable=False rather than being
    dropped or raising.
    """
    if aeds is None:
        aeds = load_aeds()
    if graph is None:
        graph = load_walk_graph()

    test_node = ox.distance.nearest_nodes(graph, X=test_lon, Y=test_lat)

    # Single-source Dijkstra from the test node covers every reachable node
    # in one pass, rather than running shortest_path_length once per AED.
    lengths_m = nx.single_source_dijkstra_path_length(graph, test_node, weight="length")

    aed_lons = [props["LONGITUDE"] for props in aeds]
    aed_lats = [props["LATITUDE"] for props in aeds]
    aed_nodes = ox.distance.nearest_nodes(graph, X=aed_lons, Y=aed_lats)

    ranked: List[AedWalkingResult] = []
    for props, aed_node in zip(aeds, aed_nodes):
        lat = props["LATITUDE"]
        lon = props["LONGITUDE"]
        dist_m = lengths_m.get(aed_node)

        if dist_m is None:
            ranked.append(
                AedWalkingResult(
                    aed_id=props["AED_ID"],
                    building_name=props.get("BUILDING_NAME"),
                    latitude=lat,
                    longitude=lon,
                    reachable=False,
                    walking_distance_m=None,
                    walking_time_s=None,
                )
            )
        else:
            ranked.append(
                AedWalkingResult(
                    aed_id=props["AED_ID"],
                    building_name=props.get("BUILDING_NAME"),
                    latitude=lat,
                    longitude=lon,
                    reachable=True,
                    walking_distance_m=dist_m,
                    walking_time_s=dist_m / WALKING_SPEED_M_PER_S,
                )
            )

    # Reachable AEDs first, sorted by walking time; unreachable ones last.
    ranked.sort(
        key=lambda r: (not r["reachable"], r["walking_time_s"] if r["reachable"] else float("inf"))
    )
    return ranked


def compute_snap_collision_confidence(
    aeds: Optional[List[dict]] = None, graph: Optional["ox.MultiDiGraph"] = None
) -> dict:
    """
    Phase 5 input: for each AED, distance_confidence = 1 / n, where n is the
    number of AEDs (including itself) whose nearest walking-graph node is
    that same node. A unique node -> 1.0; sharing with 3 others -> 0.25.

    This is fixed per AED (depends only on AED coordinates and the graph,
    not on the test point or time), so it's cheap to compute once per
    ranking call rather than cached separately.
    """
    if aeds is None:
        aeds = load_aeds()
    if graph is None:
        graph = load_walk_graph()

    aed_lons = [props["LONGITUDE"] for props in aeds]
    aed_lats = [props["LATITUDE"] for props in aeds]
    aed_nodes = ox.distance.nearest_nodes(graph, X=aed_lons, Y=aed_lats)

    node_counts: dict = {}
    for node in aed_nodes:
        node_counts[node] = node_counts.get(node, 0) + 1

    return {
        props["AED_ID"]: 1.0 / node_counts[node]
        for props, node in zip(aeds, aed_nodes)
    }
