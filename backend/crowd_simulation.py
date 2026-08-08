"""
Crowd simulation (Phase 10 / Novelty 2).

Generates a grid of SIMULATED starting points spread across one chosen
attraction's footprint and runs the full rank_combined pipeline (Phase 5)
for each point, tallying which AED comes out #1 most often. This is not
real footfall, queue, or incident data -- every starting point is synthetic
(a lat/lon grid), and every caller of run_crowd_simulation() must present
the result as labeled simulated data (see main.py's /crowd-simulation
disclaimer).

Why this can reveal something a single-person /rank query cannot: one
/rank call only answers "best AED for one person standing at one spot."
Sweeping many plausible starting points across a whole attraction and
tallying the #1 AED surfaces which AED is the consistent best answer for
the *area* -- useful for preparedness planning (where to prioritize
signage/maintenance), not for pinpointing real crowd density.

KNOWN LIMITATION this simulation surfaces empirically, not just in a
docstring (see distance_ranking.py's indoor/private-venue-interior note):
OSM's pedestrian network only covers publicly mapped streets, so lat/lon
points inside a large attraction interior (measured for Universal Studios
Singapore: 64 grid points collapse onto 17 distinct nearest-graph-nodes,
not 64) snap to whichever nearby perimeter street node happens to be
closest, not a real internal path. That is meaningfully fewer distinct
nodes than the number of simulated points, so nearby points can share an
identical walking-time profile to every AED even though they are
physically apart inside the building -- not because the AED they agree on
is a true walking bottleneck for that whole area, but because the walking
graph cannot see indoor structure. `distinct_snap_nodes` and
`avg_points_per_snap_node` in the result report this directly, and
`indoor_snap_degenerate` flags when the grid has fewer than half as many
distinct snap nodes as simulated points, so this is shown to the user
rather than silently presented as a precise finding.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple, TypedDict

import osmnx as ox

from combined_ranking import rank_combined
from distance_ranking import load_aeds, load_walk_graph

LAT_DEG_M = 110_574.0  # meters per degree of latitude, ~constant at Sentosa's latitude


def _lon_deg_m(at_lat_deg: float) -> float:
    """Meters per degree of longitude at a given latitude."""
    import math

    return 111_320.0 * math.cos(math.radians(at_lat_deg))


class AttractionBbox(TypedDict):
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    aed_count: int


def attraction_bbox(
    building_name: str,
    aeds: Optional[List[dict]] = None,
    buffer_m: float = 20.0,
) -> Optional[AttractionBbox]:
    """
    Bounding box around every AED whose BUILDING_NAME contains
    `building_name` (case-insensitive substring match), expanded by
    buffer_m in each direction so the simulated grid isn't limited to
    exactly the AED coordinates themselves. Returns None if no AED matches.
    """
    if aeds is None:
        aeds = load_aeds()

    needle = building_name.strip().lower()
    matches = [p for p in aeds if needle in (p.get("BUILDING_NAME") or "").lower()]
    if not matches:
        return None

    lats = [p["LATITUDE"] for p in matches]
    lons = [p["LONGITUDE"] for p in matches]
    lat_buf = buffer_m / LAT_DEG_M
    lon_buf = buffer_m / _lon_deg_m(sum(lats) / len(lats))

    return AttractionBbox(
        min_lat=min(lats) - lat_buf,
        max_lat=max(lats) + lat_buf,
        min_lon=min(lons) - lon_buf,
        max_lon=max(lons) + lon_buf,
        aed_count=len(matches),
    )


def generate_grid_points(bbox: AttractionBbox, n_per_side: int = 8) -> List[Tuple[float, float]]:
    """n_per_side x n_per_side grid of simulated starting points spanning bbox."""
    n_per_side = max(2, min(n_per_side, 16))
    lat_step = (bbox["max_lat"] - bbox["min_lat"]) / (n_per_side - 1)
    lon_step = (bbox["max_lon"] - bbox["min_lon"]) / (n_per_side - 1)
    return [
        (bbox["min_lat"] + i * lat_step, bbox["min_lon"] + j * lon_step)
        for i in range(n_per_side)
        for j in range(n_per_side)
    ]


class CrowdPoint(TypedDict):
    lat: float
    lon: float
    winner_aed_id: Optional[str]
    winner_building_name: Optional[str]


class CrowdTally(TypedDict):
    aed_id: str
    building_name: Optional[str]
    win_count: int
    win_fraction: float


class Bottleneck(TypedDict):
    aed_id: str
    building_name: Optional[str]
    win_count: int
    win_fraction: float


def run_crowd_simulation(
    building_name: str,
    test_dt: datetime,
    n_per_side: int = 8,
    aeds: Optional[List[dict]] = None,
    graph: Optional["object"] = None,
) -> Optional[dict]:
    """
    Run rank_combined for every point on a simulated grid spread across
    `building_name`'s footprint, tally the #1 AED per point.

    Returns None if building_name matches no AED (caller should 404).
    """
    if aeds is None:
        aeds = load_aeds()
    if graph is None:
        graph = load_walk_graph()

    bbox = attraction_bbox(building_name, aeds=aeds)
    if bbox is None:
        return None

    grid = generate_grid_points(bbox, n_per_side=n_per_side)

    point_lats = [lat for lat, _lon in grid]
    point_lons = [lon for _lat, lon in grid]
    snap_nodes = set(ox.distance.nearest_nodes(graph, X=point_lons, Y=point_lats))

    points: List[CrowdPoint] = []
    tally: Dict[str, int] = {}
    building_by_id: Dict[str, Optional[str]] = {}

    for lat, lon in grid:
        ranked, _excluded = rank_combined(lat, lon, test_dt, aeds=aeds, graph=graph)
        winner_id = None
        if ranked:
            winner = ranked[0]
            winner_id = winner["aed_id"]
            tally[winner_id] = tally.get(winner_id, 0) + 1
            building_by_id[winner_id] = winner["building_name"]
        points.append(CrowdPoint(
            lat=lat,
            lon=lon,
            winner_aed_id=winner_id,
            winner_building_name=building_by_id.get(winner_id) if winner_id else None,
        ))

    n_points = len(grid)
    tally_sorted = sorted(tally.items(), key=lambda kv: -kv[1])
    tally_payload: List[CrowdTally] = [
        CrowdTally(
            aed_id=aid,
            building_name=building_by_id.get(aid),
            win_count=count,
            win_fraction=count / n_points,
        )
        for aid, count in tally_sorted
    ]

    bottleneck: Optional[Bottleneck] = None
    if tally_payload:
        top = tally_payload[0]
        bottleneck = Bottleneck(
            aed_id=top["aed_id"],
            building_name=top["building_name"],
            win_count=top["win_count"],
            win_fraction=top["win_fraction"],
        )

    distinct_snap_nodes = len(snap_nodes)
    # Concentration heuristic, not a claim of total collapse to one node:
    # true when the grid maps onto fewer than half as many distinct graph
    # nodes as simulated points, i.e. an OSM indoor-coverage gap (see
    # module docstring) is measurably compressing walking-time diversity
    # across the grid rather than every point resolving to its own node.
    indoor_snap_degenerate = n_points > 4 and distinct_snap_nodes < (n_points / 2)

    return {
        "attraction": building_name,
        "bbox": bbox,
        "n_points": n_points,
        "points": points,
        "tally": tally_payload,
        "bottleneck": bottleneck,
        "distinct_snap_nodes": distinct_snap_nodes,
        "avg_points_per_snap_node": round(n_points / distinct_snap_nodes, 1) if distinct_snap_nodes else None,
        "indoor_snap_degenerate": indoor_snap_degenerate,
        "simulated": True,
    }
