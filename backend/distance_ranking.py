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
constant assumed walking speed (WALKING_SPEED_M_PER_S below) applied per
edge, except edges tagged highway=steps, which use a separate slower
STAIRS_SPEED_M_PER_S constant (see "Route-quality flags" below). Both are
documented planning-level simplifications, not measured or live speeds.

Route-quality flags (mobility profile support): the cached graph already
carries OSM tags beyond "length" that were being downloaded and silently
discarded. Three are now read, per edge, along the actual shortest path
chosen for each AED:
    highway == "steps"            -- a staircase segment. Walked at
        STAIRS_SPEED_M_PER_S instead of WALKING_SPEED_M_PER_S in the
        default "walk" mobility profile (still passable, just slower and
        flagged via uses_stairs=True on the result). In the "wheelchair"
        profile these edges are removed from the graph entirely before
        pathfinding -- not slowed down, not scored as passable-but-risky,
        excluded outright -- because no wheelchair=yes/ramp tag exists on
        any of them to say otherwise, and guessing one is passable would
        misrepresent certainty we don't have.
    highway in ROAD_HIGHWAY_TYPES  -- a vehicle-carrying road segment
        (confirmed empirically against this bbox's own maxspeed tags --
        "unclassified" carries 40-50 km/h maxspeed on 112 of 124 such
        edges here, "secondary" and "residential" both carry 50;
        "footway"/"steps"/"path"/"service" are excluded from this set as
        pedestrian-only or low-traffic driveways). If the path crosses
        such an edge and neither endpoint node is OSM-tagged
        highway=="crossing" (a marked pedestrian crossing), the route is
        flagged crosses_unmarked_road=True -- a real map fact, not an
        assumption about actual traffic volume.
    access == "permissive"        -- the way is not a full public
        right-of-way, just tolerated. Flagged uses_permissive_access=True
        -- a confidence/trust-style caveat, same spirit as
        compute_snap_collision_confidence below.
None of these three invents a number (no fabricated time delay, no
fabricated danger score) -- they are pass/fail facts read directly from
tags already present in the downloaded graph, surfaced so the ranking
layer and UI can say plainly what kind of route each AED actually has,
matching the same "show uncertainty, never invent it" principle used for
access-barrier detection in trust_score.py.

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
Studios Singapore before entrance_augmentation.py existed
(backend/test_crowd_simulation.py): only 2 of 657 Sentosa graph nodes fell
inside its own AED bounding box, and its 19 AEDs collapsed onto just 10
distinct nearest-graph-nodes -- not the same single node for all of them
(the venue is large enough to have multiple sides near different perimeter
roads), but far fewer distinct nodes than the 19 you'd expect if the
walking network could see indoor structure. After entrance_augmentation.py
(below), those same 19 AEDs snap to 19 distinct nodes -- see MITIGATION.
The rest of this paragraph still describes the underlying gap for any
building with no mapped footprint or no AED nearby. AEDs
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

MITIGATION -- entrance augmentation (entrance_augmentation.py): the walk
graph loaded here (load_walk_graph) is not the raw OSM download but that
graph plus synthetic "entrance" nodes spaced around the perimeter of every
building footprint that has an AED near it, each connected to the nearest
real street node by an estimated edge. This gives points near/inside such
a building several plausible doors to snap to instead of one, directly
addressing the brief's "entrances" criterion. It does not fabricate a real
indoor path or a verified entrance location -- the points are evenly
spaced guesses around the outline, not surveyed doors -- so any walking
result whose shortest path uses one of these edges is flagged
uses_synthetic_entrance=True (see _walk_path/_annotate_graph below), the
same honesty convention as uses_stairs/crosses_unmarked_road. Buildings
with no mapped footprint, or no AED nearby, are unaffected and still hit
the limitation described above.
"""

import json
import math
import os
from typing import List, Optional, Tuple, TypedDict

import networkx as nx
import osmnx as ox

from entrance_augmentation import load_augmented_graph

GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "sentosa_aeds.geojson")

EARTH_RADIUS_M = 6_371_000.0

# Assumed constant adult walking speed. OSM walking edges in this bbox carry
# no reliable per-edge pedestrian speed tags, so this is a documented
# simplification applied uniformly to shortest-path distance, not measured
# or live data. ~4.8 km/h, a standard planning-level walking pace.
WALKING_SPEED_M_PER_S = 1.34

# Assumed constant stair-climbing pace, applied only to edges tagged
# highway=steps. Roughly 1.8 km/h horizontal-equivalent -- a documented
# planning-level assumption (real pace varies hugely by step count/rise),
# deliberately much slower than flat WALKING_SPEED_M_PER_S rather than a
# measured value. Only used in the "walk" mobility profile; "wheelchair"
# removes these edges outright instead of slowing them down (see module
# docstring).
STAIRS_SPEED_M_PER_S = 0.5

# User-adjustable pace (see main.py's `pace_m_per_s` request field, exposed
# in the frontend as an m/s slider): bounds on the speed a user can select.
# WALKING_SPEED_M_PER_S (1.34, "average") is the default/center point.
# Bounds are sanity limits, not measured either -- below MIN_PACE_M_PER_S is
# slower than a typical assisted/wheelchair-push pace, above
# MAX_PACE_M_PER_S is a jog rather than a walk, so both ends stop being a
# meaningful "walking" pace to rank against.
MIN_PACE_M_PER_S = 0.3
MAX_PACE_M_PER_S = 2.5


def _pace_time_scale(pace_m_per_s: float) -> float:
    """
    Multiplier applied to a walking_time_s computed at WALKING_SPEED_M_PER_S
    to get the equivalent time at a user-selected pace.

    The walk graph's per-edge time_s (_annotate_graph) is baked in once at
    WALKING_SPEED_M_PER_S / STAIRS_SPEED_M_PER_S -- re-annotating the whole
    graph, or re-running Dijkstra, per pace change would be wasteful.
    That's unnecessary under one simplifying assumption: a user's stairs
    pace scales with their flat-ground pace by the same fixed ratio
    STAIRS_SPEED_M_PER_S/WALKING_SPEED_M_PER_S (~0.373x) that the "average"
    baseline uses. Under that assumption every edge's time_s -- flat or
    stairs -- scales by the same factor, WALKING_SPEED_M_PER_S /
    pace_m_per_s, regardless of how much of a given path is stairs, so a
    single post-hoc multiply on the aggregated time is equivalent to
    re-annotating and re-solving the graph at the new speed. It also means
    pace never changes *which* path is shortest -- only how long that same
    path is reported to take.
    """
    if not (MIN_PACE_M_PER_S <= pace_m_per_s <= MAX_PACE_M_PER_S):
        raise ValueError(
            f"pace_m_per_s must be between {MIN_PACE_M_PER_S} and {MAX_PACE_M_PER_S}, got {pace_m_per_s!r}"
        )
    return WALKING_SPEED_M_PER_S / pace_m_per_s


# highway tags treated as vehicle-carrying roads for the crosses_unmarked_road
# flag, chosen by inspecting this bbox's own maxspeed tags (see module
# docstring) rather than assumed from OSM's general road hierarchy --
# "unclassified" is Sentosa's internal road class and does carry real
# vehicle maxspeeds here, so it's included even though it's often low-traffic
# elsewhere. "service" is excluded: in this bbox it's almost entirely
# driveways/carpark aisles with no maxspeed tag.
ROAD_HIGHWAY_TYPES = {"secondary", "unclassified", "residential"}

MOBILITY_PROFILES = ("walk", "wheelchair")


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
    uses_stairs: Optional[bool]
    crosses_unmarked_road: Optional[bool]
    uses_permissive_access: Optional[bool]
    uses_synthetic_entrance: Optional[bool]
    mobility_note: Optional[str]


_graph_cache: Optional["ox.MultiDiGraph"] = None
_wheelchair_graph_cache: Optional["ox.MultiDiGraph"] = None


def _highway_has(value, tag: str) -> bool:
    """
    osmnx returns an edge's `highway` (and similarly `access`) tag as a
    single string, or as a list when graph simplification merged multiple
    OSM ways with different tag values into one edge. Handle both the same
    way rather than special-casing every call site.
    """
    if isinstance(value, (list, tuple)):
        return tag in value
    return value == tag


def _truthy(value) -> bool:
    """
    entrance_augmentation.py tags synthetic edges/nodes with synthetic=True,
    but a graphml round-trip (save then ox.load_graphml) stringifies custom
    boolean attributes, so a plain `is True` check would silently always be
    False after loading from the cached file. Handles both the in-memory
    bool (fresh build, no round-trip yet) and the "True"/"False" string
    (loaded from disk).
    """
    return value is True or value == "True"


def _annotate_graph(graph: "ox.MultiDiGraph") -> None:
    """
    Mutates edge data in place to add the derived route-quality fields
    described in the module docstring: time_s (per-edge walking time,
    slower on highway=steps), is_steps, is_road_crossing, is_permissive,
    is_synthetic (entrance_augmentation.py's estimated entrance edges).
    Idempotent (checked via the "time_s" key) so calling this more than
    once on the same graph object is a no-op.
    """
    for _u, _v, _k, data in graph.edges(keys=True, data=True):
        if "time_s" in data:
            continue
        highway = data.get("highway")
        is_steps = _highway_has(highway, "steps")
        speed = STAIRS_SPEED_M_PER_S if is_steps else WALKING_SPEED_M_PER_S
        data["time_s"] = data["length"] / speed
        data["is_steps"] = is_steps
        data["is_road_crossing"] = any(_highway_has(highway, t) for t in ROAD_HIGHWAY_TYPES)
        data["is_permissive"] = data.get("access") == "permissive"
        data["is_synthetic"] = _truthy(data.get("synthetic"))


def load_walk_graph() -> "ox.MultiDiGraph":
    """
    Load the cached Sentosa walking graph (never re-downloads/re-augments
    live), annotated once with the derived route-quality fields
    (_annotate_graph). This is the entrance-augmented graph
    (entrance_augmentation.load_augmented_graph), not the raw OSM download
    -- see this module's "MITIGATION -- entrance augmentation" docstring.
    """
    global _graph_cache
    if _graph_cache is None:
        _graph_cache = load_augmented_graph()
        _annotate_graph(_graph_cache)
    return _graph_cache


def load_wheelchair_graph(graph: Optional["ox.MultiDiGraph"] = None) -> "ox.MultiDiGraph":
    """
    A copy of the walking graph with every highway=steps edge removed
    entirely -- the "wheelchair" mobility profile pathfinds on this graph
    instead of the full one, so a shortest path can never silently route
    someone through a staircase. No wheelchair=yes/ramp tag exists on any
    Sentosa steps edge to say a staircase is actually passable, so it is
    excluded outright rather than scored as passable-but-slow (module
    docstring). Built once and cached at module level.
    """
    global _wheelchair_graph_cache
    if _wheelchair_graph_cache is None:
        base = graph if graph is not None else load_walk_graph()
        wheelchair_graph = base.copy()
        steps_edges = [
            (u, v, k)
            for u, v, k, data in wheelchair_graph.edges(keys=True, data=True)
            if data.get("is_steps")
        ]
        wheelchair_graph.remove_edges_from(steps_edges)
        _wheelchair_graph_cache = wheelchair_graph
    return _wheelchair_graph_cache


def _walk_path(graph: "ox.MultiDiGraph", path: List) -> Tuple[float, bool, bool, bool, bool]:
    """
    Walk a shortest-path node sequence, summing real distance and reading
    the OSM-tag-derived route-quality flags off the specific edges Dijkstra
    actually used -- the minimum-time_s edge between each consecutive node
    pair, since a MultiDiGraph can carry more than one edge between the
    same two nodes.

    Returns (distance_m, uses_stairs, crosses_unmarked_road,
    uses_permissive_access, uses_synthetic_entrance).
    """
    distance_m = 0.0
    uses_stairs = False
    crosses_unmarked_road = False
    uses_permissive_access = False
    uses_synthetic_entrance = False

    for u, v in zip(path, path[1:]):
        parallel = graph.get_edge_data(u, v)
        edge = min(parallel.values(), key=lambda d: d.get("time_s", float("inf")))
        distance_m += edge["length"]

        if edge.get("is_steps"):
            uses_stairs = True
        if edge.get("is_road_crossing"):
            u_is_crossing = graph.nodes[u].get("highway") == "crossing"
            v_is_crossing = graph.nodes[v].get("highway") == "crossing"
            if not (u_is_crossing or v_is_crossing):
                crosses_unmarked_road = True
        if edge.get("is_permissive"):
            uses_permissive_access = True
        if edge.get("is_synthetic"):
            uses_synthetic_entrance = True

    return distance_m, uses_stairs, crosses_unmarked_road, uses_permissive_access, uses_synthetic_entrance


def rank_by_walking_time(
    test_lat: float,
    test_lon: float,
    aeds: Optional[List[dict]] = None,
    graph: Optional["ox.MultiDiGraph"] = None,
    mobility_profile: str = "walk",
    pace_m_per_s: float = WALKING_SPEED_M_PER_S,
) -> List[AedWalkingResult]:
    """
    Rank all Sentosa AEDs by real walking-network time from a test point.

    Snaps the test point and every AED to their nearest graph node, then
    uses a networkx single-source Dijkstra shortest path -- weighted by
    time_s, a per-edge time that's slower on highway=steps edges than flat
    ground (module docstring) -- to compute real walking time, distance,
    and the route-quality flags read off the actual path taken. AEDs with
    no path from the test point in the walking network are returned with
    reachable=False rather than being dropped or raising.

    mobility_profile: "walk" (default) allows every pedestrian edge,
    including stairs (slower, and flagged via uses_stairs=True).
    "wheelchair" pathfinds on load_wheelchair_graph() instead, which has
    every stairs edge removed outright -- an AED only reachable via a
    staircase becomes reachable=False under this profile, with
    mobility_note explaining that a walking (non-wheelchair) route exists
    but requires stairs, so "no route at all" isn't confused with "no
    route without stairs".

    pace_m_per_s: user-selected walking speed in meters/second (default
    WALKING_SPEED_M_PER_S, see MIN_PACE_M_PER_S/MAX_PACE_M_PER_S for the
    valid range) -- rescales the reported walking_time_s only. The graph is
    still searched at the WALKING_SPEED_M_PER_S weighting regardless of
    pace (see _pace_time_scale for why that doesn't change which path is
    shortest), so pace never affects reachability or route choice, only how
    long the chosen route is reported to take.
    """
    if mobility_profile not in MOBILITY_PROFILES:
        raise ValueError(f"mobility_profile must be one of {MOBILITY_PROFILES}, got {mobility_profile!r}")
    pace_scale = _pace_time_scale(pace_m_per_s)

    if aeds is None:
        aeds = load_aeds()
    if graph is None:
        graph = load_walk_graph()

    active_graph = graph if mobility_profile == "walk" else load_wheelchair_graph(graph)

    test_node = ox.distance.nearest_nodes(active_graph, X=test_lon, Y=test_lat)

    # Single-source Dijkstra from the test node covers every reachable node
    # in one pass, rather than running shortest_path once per AED. Returns
    # both time-to-reach and the actual path (needed for the route flags).
    times_s, paths = nx.single_source_dijkstra(active_graph, test_node, weight="time_s")

    # Only needed for the wheelchair profile, to tell "no path at all"
    # apart from "no path without stairs" for mobility_note below. Cheap
    # relative to the profile's own search (657 nodes total).
    walk_reachable_nodes = None
    if mobility_profile == "wheelchair":
        walk_reachable_nodes = set(
            nx.single_source_dijkstra_path_length(graph, test_node, weight="time_s")
        )

    aed_lons = [props["LONGITUDE"] for props in aeds]
    aed_lats = [props["LATITUDE"] for props in aeds]
    aed_nodes = ox.distance.nearest_nodes(active_graph, X=aed_lons, Y=aed_lats)

    ranked: List[AedWalkingResult] = []
    for props, aed_node in zip(aeds, aed_nodes):
        lat = props["LATITUDE"]
        lon = props["LONGITUDE"]
        time_s = times_s.get(aed_node)

        if time_s is None:
            mobility_note = None
            if (
                mobility_profile == "wheelchair"
                and walk_reachable_nodes is not None
                and aed_node in walk_reachable_nodes
            ):
                mobility_note = (
                    "A walking route exists but requires stairs; no stairs-free "
                    "path was found in the mapped pedestrian network."
                )
            ranked.append(
                AedWalkingResult(
                    aed_id=props["AED_ID"],
                    building_name=props.get("BUILDING_NAME"),
                    latitude=lat,
                    longitude=lon,
                    reachable=False,
                    walking_distance_m=None,
                    walking_time_s=None,
                    uses_stairs=None,
                    crosses_unmarked_road=None,
                    uses_permissive_access=None,
                    uses_synthetic_entrance=None,
                    mobility_note=mobility_note,
                )
            )
        else:
            path = paths[aed_node]
            distance_m, uses_stairs, crosses_unmarked_road, uses_permissive_access, uses_synthetic_entrance = _walk_path(
                active_graph, path
            )
            ranked.append(
                AedWalkingResult(
                    aed_id=props["AED_ID"],
                    building_name=props.get("BUILDING_NAME"),
                    latitude=lat,
                    longitude=lon,
                    reachable=True,
                    walking_distance_m=distance_m,
                    walking_time_s=time_s * pace_scale,
                    uses_stairs=uses_stairs,
                    crosses_unmarked_road=crosses_unmarked_road,
                    uses_permissive_access=uses_permissive_access,
                    uses_synthetic_entrance=uses_synthetic_entrance,
                    mobility_note=None,
                )
            )

    # Reachable AEDs first, sorted by walking time; unreachable ones last.
    ranked.sort(
        key=lambda r: (not r["reachable"], r["walking_time_s"] if r["reachable"] else float("inf"))
    )
    return ranked


class RouteGeometry(TypedDict):
    aed_id: str
    reachable: bool
    walking_route: Optional[List[Tuple[float, float]]]
    walking_distance_m: Optional[float]
    walking_time_s: Optional[float]
    straight_line: List[Tuple[float, float]]
    straight_line_distance_m: float
    uses_stairs: Optional[bool]
    crosses_unmarked_road: Optional[bool]
    uses_permissive_access: Optional[bool]
    uses_synthetic_entrance: Optional[bool]
    mobility_note: Optional[str]


def get_route_geometry(
    test_lat: float,
    test_lon: float,
    aed_id: str,
    aeds: Optional[List[dict]] = None,
    graph: Optional["ox.MultiDiGraph"] = None,
    mobility_profile: str = "walk",
    pace_m_per_s: float = WALKING_SPEED_M_PER_S,
) -> Optional[RouteGeometry]:
    """
    Frontend map-drawing support: the real walking-network route (as an
    ordered list of (lat, lon) points to draw a polyline with) from a test
    point to one specific AED, alongside the straight-line baseline between
    the same two points for visual comparison. Everything here reuses
    rank_by_walking_time's own graph/shortest-path machinery so the drawn
    route is provably the same path that route's sub-scores were computed
    from, not a separately-approximated line.

    Returns None if aed_id doesn't match any AED in `aeds` (caller should
    404). If no path exists in the walking network, walking_route/
    walking_distance_m/walking_time_s are None (reachable=False) -- the
    straight-line baseline is still returned so the frontend can show *why*
    a straight line looks walkable but the real network disagrees.

    The walking_route always starts/ends at the exact test point/AED
    coordinate (not the snapped graph node) so the drawn line reads as a
    real point-to-point route, not one that visibly stops short at whatever
    street node it snapped to.
    """
    pace_scale = _pace_time_scale(pace_m_per_s)

    if aeds is None:
        aeds = load_aeds()
    props = next((p for p in aeds if p["AED_ID"] == aed_id), None)
    if props is None:
        return None

    if graph is None:
        graph = load_walk_graph()
    active_graph = graph if mobility_profile == "walk" else load_wheelchair_graph(graph)

    aed_lat = props["LATITUDE"]
    aed_lon = props["LONGITUDE"]
    straight_line = [(test_lat, test_lon), (aed_lat, aed_lon)]
    straight_line_distance_m = haversine_distance_m(test_lat, test_lon, aed_lat, aed_lon)

    test_node = ox.distance.nearest_nodes(active_graph, X=test_lon, Y=test_lat)
    aed_node = ox.distance.nearest_nodes(active_graph, X=aed_lon, Y=aed_lat)

    try:
        path = nx.shortest_path(active_graph, test_node, aed_node, weight="time_s")
    except nx.NetworkXNoPath:
        mobility_note = None
        if mobility_profile == "wheelchair":
            try:
                nx.shortest_path(graph, test_node, aed_node, weight="time_s")
                mobility_note = (
                    "A walking route exists but requires stairs; no stairs-free "
                    "path was found in the mapped pedestrian network."
                )
            except nx.NetworkXNoPath:
                pass
        return RouteGeometry(
            aed_id=aed_id,
            reachable=False,
            walking_route=None,
            walking_distance_m=None,
            walking_time_s=None,
            straight_line=straight_line,
            straight_line_distance_m=straight_line_distance_m,
            uses_stairs=None,
            crosses_unmarked_road=None,
            uses_permissive_access=None,
            uses_synthetic_entrance=None,
            mobility_note=mobility_note,
        )

    distance_m, uses_stairs, crosses_unmarked_road, uses_permissive_access, uses_synthetic_entrance = _walk_path(
        active_graph, path
    )
    time_s = pace_scale * sum(
        min(d.get("time_s", float("inf")) for d in active_graph.get_edge_data(u, v).values())
        for u, v in zip(path, path[1:])
    )

    node_coords = [(active_graph.nodes[n]["y"], active_graph.nodes[n]["x"]) for n in path]
    walking_route = [(test_lat, test_lon), *node_coords, (aed_lat, aed_lon)]

    return RouteGeometry(
        aed_id=aed_id,
        reachable=True,
        walking_route=walking_route,
        walking_distance_m=distance_m,
        walking_time_s=time_s,
        straight_line=straight_line,
        straight_line_distance_m=straight_line_distance_m,
        uses_stairs=uses_stairs,
        crosses_unmarked_road=crosses_unmarked_road,
        uses_permissive_access=uses_permissive_access,
        uses_synthetic_entrance=uses_synthetic_entrance,
        mobility_note=None,
    )


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
