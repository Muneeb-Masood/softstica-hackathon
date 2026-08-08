#!/usr/bin/env python3
"""
Phase 10 crowd simulation test.

Runs run_crowd_simulation against real Sentosa data for Universal Studios
Singapore (the brief's own example attraction) and checks:
  - the grid actually spans the attraction's AED footprint
  - every simulated point gets a winner (or the run fails loudly, not
    silently)
  - the tally sums to n_points
  - the "indoor snap degenerate" finding from distance_ranking.py's
    docstring (USS's interior has almost no mapped walking nodes, so grid
    points collapse onto far fewer distinct graph nodes than there are
    points) is visible in the result via distinct_snap_nodes /
    avg_points_per_snap_node / indoor_snap_degenerate, not hidden
  - an unmatched building_name returns None (caller 404s) rather than
    crashing or silently returning an empty result
"""
import sys
import time
from datetime import datetime

from crowd_simulation import attraction_bbox, generate_grid_points, run_crowd_simulation
from distance_ranking import load_aeds, load_walk_graph

TEST_DATE = datetime(2026, 8, 8, 14, 0)  # Saturday 14:00, matches findings.md sweep


def run():
    aeds = load_aeds()
    graph = load_walk_graph()

    failures = []

    print("=" * 90)
    print("PHASE 10 CROWD SIMULATION TEST -- Universal Studios Singapore")
    print("=" * 90)

    bbox = attraction_bbox("Universal Studios Singapore", aeds=aeds)
    if bbox is None:
        failures.append("attraction_bbox returned None for a building_name known to exist")
    else:
        print(f"bbox: {bbox}")
        if bbox["aed_count"] != 19:
            failures.append(f"expected 19 USS AEDs, got {bbox['aed_count']}")
        if not (bbox["min_lat"] < bbox["max_lat"] and bbox["min_lon"] < bbox["max_lon"]):
            failures.append("bbox is degenerate (min >= max)")

    grid = generate_grid_points(bbox, n_per_side=8) if bbox else []
    if len(grid) != 64:
        failures.append(f"expected 64 grid points (8x8), got {len(grid)}")

    start = time.perf_counter()
    result = run_crowd_simulation(
        "Universal Studios Singapore", TEST_DATE, n_per_side=8, aeds=aeds, graph=graph
    )
    elapsed = time.perf_counter() - start
    print(f"run_crowd_simulation elapsed: {elapsed:.2f}s for {result['n_points']} points")

    if result is None:
        print("FAILED -- run_crowd_simulation returned None for a valid attraction")
        sys.exit(1)

    if result["n_points"] != 64:
        failures.append(f"expected 64 n_points, got {result['n_points']}")

    if len(result["points"]) != result["n_points"]:
        failures.append("points list length != n_points")

    winners_missing = [p for p in result["points"] if p["winner_aed_id"] is None]
    if winners_missing:
        failures.append(f"{len(winners_missing)} simulated points got no winner (all AEDs excluded)")

    tally_sum = sum(t["win_count"] for t in result["tally"])
    if tally_sum != result["n_points"] - len(winners_missing):
        failures.append(f"tally sum {tally_sum} != points with a winner {result['n_points'] - len(winners_missing)}")

    if result["bottleneck"] is None:
        failures.append("bottleneck is None despite having winners")
    else:
        top_by_tally = result["tally"][0]
        if result["bottleneck"]["aed_id"] != top_by_tally["aed_id"]:
            failures.append("bottleneck does not match top of tally")

    print(f"distinct_snap_nodes: {result['distinct_snap_nodes']}")
    print(f"avg_points_per_snap_node: {result['avg_points_per_snap_node']}")
    print(f"indoor_snap_degenerate: {result['indoor_snap_degenerate']}")
    print(f"bottleneck: {result['bottleneck']}")
    print(f"tally (top 5): {result['tally'][:5]}")

    # Concentration check, not "collapses to one node" -- measured directly
    # (see distance_ranking.py docstring update): only 2 of 657 Sentosa
    # graph nodes fall inside USS's own AED bbox, so a 64-point grid over
    # its footprint should map onto meaningfully fewer than 64 distinct
    # nodes. Threshold matches indoor_snap_degenerate's own definition
    # (< half as many distinct nodes as points) so this asserts the flag
    # fires for the brief's own example attraction, not a specific count
    # that could drift if the OSM extract is refreshed.
    if result["distinct_snap_nodes"] >= result["n_points"] / 2:
        failures.append(
            f"expected USS's grid to concentrate onto fewer than half as many distinct "
            f"nodes as points (known OSM indoor-coverage gap), got "
            f"{result['distinct_snap_nodes']}/{result['n_points']}"
        )
    if not result["indoor_snap_degenerate"]:
        failures.append("indoor_snap_degenerate should be True for USS given the known coverage gap")

    print()
    print("-" * 90)
    print("Unmatched building_name should return None, not crash")
    print("-" * 90)
    none_result = run_crowd_simulation("Nonexistent Attraction Xyz", TEST_DATE, aeds=aeds, graph=graph)
    if none_result is not None:
        failures.append("run_crowd_simulation should return None for an unmatched building_name")
    else:
        print("OK -- returned None as expected")

    print()
    if failures:
        print(f"FAILED -- {len(failures)} issue(s):")
        for f in failures:
            print("  -", f)
        sys.exit(1)

    print(
        f"PASSED -- 64-point grid over USS, {result['distinct_snap_nodes']} distinct snap node(s), "
        f"indoor_snap_degenerate={result['indoor_snap_degenerate']}, "
        f"bottleneck={result['bottleneck']['building_name']} "
        f"({result['bottleneck']['win_count']}/{result['n_points']} points), "
        f"elapsed={elapsed:.2f}s"
    )


if __name__ == "__main__":
    run()
