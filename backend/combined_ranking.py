"""
Combined AED ranking (Phase 5).

Combines four signals into one final_score per AED for a given test
location, date, and time:
    walking time         (distance_ranking.rank_by_walking_time)
    walking-distance confidence  (distance_ranking.compute_snap_collision_confidence)
    operating-hours confidence   (hours_parser.parse_operating_hours, evaluated
                                   at the live test datetime -- not the fixed
                                   reference datetime trust_score.py uses for
                                   its own hours_score component)
    trust/findability score       (trust_score.score_aed_properties)

    final_score = 0.55*time_score + 0.15*distance_confidence
                  + 0.15*hours_confidence + 0.15*trust_normalized

Weighting rationale (see backend/findings.md for the design-review numbers
behind these choices): walking time is the primary decision axis the brief
is built around, so it gets the majority weight. The other three are framed
in the brief as uncertainty/quality signals, not primary ranking criteria,
so 0.15 each lets them reorder close-in-time candidates without letting a
much-closer AED lose to a farther one purely on trust or hours confidence.
Verified empirically (findings.md, 2026-08-08) that 0.15 is not too weak to
matter: a full distance_confidence swing (no collision -> 3-way collision)
outweighs roughly a minute of walking-time difference in this dataset's
speed/decay scale, and 1256 such reorderings were found across a sweep of
all walking-graph nodes as candidate test points. (That "roughly a minute"
figure was measured under the original TIME_DECAY_S=300; see the 2026-08-08
tuning note in findings.md for why the constant was later raised to 1200 --
the qualitative conclusion that 0.15 can reorder near-ties still holds, the
exact minute-figure was not re-measured under 1200.)

time_score = exp(-walking_time_s / TIME_DECAY_S). TIME_DECAY_S=1200s (20 min
half-scale) is a documented planning-level assumption, not a measured or
live value -- it sets the scale at which additional walking time stops
mattering much, not a claim about real response times. Raised from an
initial 300s (see findings.md, 2026-08-08 tuning note) after real-query
testing showed 300s let trust/hours/distance-confidence (0.15 weight each,
0.45 combined) dominate over walking time for any AED beyond ~10-15
minutes -- backwards for a tool meant to prioritize real accessibility.

hours_confidence is *not* the same number trust_score.py's hours_score is
built from -- that one scores the OPERATING_HOURS string's parse quality at
a fixed reference datetime (a data-quality signal). This one is evaluated
at the live test_dt and is redefined for ranking purposes: status=="open"
contributes the raw parse confidence (1.0 clean / 0.5 partial); status in
{"closed", "unknown"} both contribute 0.0 to the score. They are NOT the
same thing to a user (closed = confidently inaccessible right now; unknown
= we can't establish it), so they stay display-differentiated via
hours_status, but they score identically: neither is a confident "go here".

trust_normalized = (trust_score.total_score + 3) / 6, rescaling the -3..3
rule-based trust score to 0..1.

Segregation -- excluded from the main ranked list, not scored, not silently
dropped either (both returned in `excluded`):
    reachable == False   -- no path in the walking network (Phase 4).
    hours_status == "closed" at test_dt -- confidently not accessible right
        now. Decided 2026-08-08 (see findings.md) after comparing this
        against sorting closed AEDs inline with a badge: a plain
        hours_confidence=0.0 penalty was not steep enough to reliably sink
        a very close closed AED below farther open ones, and any penalty
        steep enough to fix that converges on the same ordering as
        segregation anyway -- so segregation is used directly instead of
        reverse-engineering an inline penalty to match it.
"unknown" hours status stays in the main list (with hours_confidence=0.0
pulling its score down) rather than being excluded -- "we don't know" is
not the same claim as "we know it's shut", and excluding it would guess.

Mobility profile (stairs / wheelchair): rank_combined and
rank_combined_timeline take a `mobility_profile` param ("walk" default,
or "wheelchair"), passed straight through to
distance_ranking.rank_by_walking_time -- see that module's docstring for
the OSM-tag-derived route-quality flags (uses_stairs,
crosses_unmarked_road, uses_permissive_access, uses_synthetic_entrance,
mobility_note). "walk"
still walks stairs (slower, flagged); "wheelchair" excludes stairs edges
from pathfinding entirely, so an AED reachable only via stairs moves from
`ranked` to `excluded` with reason="unreachable" and a mobility_note
explaining specifically that the walking route requires stairs, distinct
from an AED with no path in the network at all (mobility_note=None in
that case). This is a routing-input choice, not a trust/confidence score
-- it changes which physical path is searched, not how a fixed path is
scored, so it lives in distance_ranking.py's graph selection, not as
another sub-score weight here.
"""

import math
from datetime import date as date_type, datetime, time as time_type
from typing import Dict, List, Optional, Tuple, TypedDict

from distance_ranking import (
    MOBILITY_PROFILES,
    compute_snap_collision_confidence,
    load_aeds,
    load_walk_graph,
    rank_by_walking_time,
)
from hours_parser import parse_operating_hours
from trust_score import explain_trust_score, score_aed_properties

TIME_DECAY_S = 1200.0
W_TIME = 0.55
W_DIST_CONF = 0.15
W_HOURS_CONF = 0.15
W_TRUST = 0.15


class RankedAed(TypedDict):
    aed_id: str
    building_name: Optional[str]
    latitude: float
    longitude: float
    walking_time_s: float
    walking_distance_m: float
    time_score: float
    distance_confidence: float
    hours_status: str
    hours_confidence: float
    trust_score: int
    trust_badge: str
    trust_badge_reasons: List[str]
    trust_normalized: float
    final_score: float
    uses_stairs: bool
    crosses_unmarked_road: bool
    uses_permissive_access: bool
    uses_synthetic_entrance: bool


class ExcludedAed(TypedDict):
    aed_id: str
    building_name: Optional[str]
    reason: str  # "unreachable" or "closed"
    hours_status: Optional[str]
    walking_time_s: Optional[float]
    mobility_note: Optional[str]


def display_name(props: dict) -> Optional[str]:
    """
    BUILDING_NAME is null for some Sentosa AEDs (e.g. 098008-003, a first-aid
    room at 31 Beach View with no named building) -- falling straight
    through to the raw AED_ID in that case reads as a bug in the UI. Prefer
    the street address (HOUSE_NUMBER + ROAD_NAME) as a human-readable
    fallback; only the caller falls back further to AED_ID if even that is
    missing.
    """
    name = props.get("BUILDING_NAME")
    if name:
        return name
    road = props.get("ROAD_NAME")
    if road:
        house = props.get("HOUSE_NUMBER")
        return f"{house} {road}" if house else road
    return None


def rank_combined(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    aeds: Optional[List[dict]] = None,
    graph: Optional["object"] = None,
    mobility_profile: str = "walk",
) -> Tuple[List[RankedAed], List[ExcludedAed]]:
    """
    Rank Sentosa AEDs for a test location, date, and time.

    mobility_profile: "walk" (default) or "wheelchair" -- see module
    docstring "Mobility profile" section.

    Returns (ranked, excluded):
        ranked   -- AEDs with a full final_score, sorted best first, every
                    sub-score visible on each entry.
        excluded -- AEDs left out of the ranking (unreachable or closed at
                    test_dt), with the reason, not silently dropped.
    """
    if aeds is None:
        aeds = load_aeds()
    if graph is None:
        graph = load_walk_graph()

    walking_by_id, distance_confidence_by_id = _shared_geometry(
        test_lat, test_lon, aeds, graph, mobility_profile
    )
    return _combine_for_datetime(test_dt, aeds, walking_by_id, distance_confidence_by_id)


def _shared_geometry(
    test_lat: float,
    test_lon: float,
    aeds: List[dict],
    graph: "object",
    mobility_profile: str = "walk",
) -> Tuple[dict, dict]:
    """
    The two sub-scores that depend only on test location + AED coordinates,
    not on date/time: walking time/distance and snap-collision confidence.
    Computed once and reused across every hour in a Phase 9 timeline query,
    since re-running shortest-path search 24x for a fixed location would be
    pure waste -- only hours_confidence actually varies by time.
    """
    if mobility_profile not in MOBILITY_PROFILES:
        raise ValueError(f"mobility_profile must be one of {MOBILITY_PROFILES}, got {mobility_profile!r}")
    walking = rank_by_walking_time(test_lat, test_lon, aeds=aeds, graph=graph, mobility_profile=mobility_profile)
    walking_by_id = {r["aed_id"]: r for r in walking}
    distance_confidence_by_id = compute_snap_collision_confidence(aeds, graph)
    return walking_by_id, distance_confidence_by_id


def _combine_for_datetime(
    test_dt: datetime,
    aeds: List[dict],
    walking_by_id: dict,
    distance_confidence_by_id: dict,
) -> Tuple[List[RankedAed], List[ExcludedAed]]:
    """
    The time-dependent half of rank_combined: hours parsing at test_dt, plus
    combining with the (already-computed) walking/distance-confidence/trust
    sub-scores into a final_score. Factored out so a Phase 9 timeline query
    can call this once per hour without repeating the expensive geometry.
    """
    ranked: List[RankedAed] = []
    excluded: List[ExcludedAed] = []

    for props in aeds:
        aed_id = props["AED_ID"]
        w = walking_by_id[aed_id]

        if not w["reachable"]:
            excluded.append(ExcludedAed(
                aed_id=aed_id,
                building_name=display_name(props),
                reason="unreachable",
                hours_status=None,
                walking_time_s=None,
                mobility_note=w.get("mobility_note"),
            ))
            continue

        status, parse_confidence = parse_operating_hours(props.get("OPERATING_HOURS"), test_dt)

        if status == "closed":
            excluded.append(ExcludedAed(
                aed_id=aed_id,
                building_name=display_name(props),
                reason="closed",
                hours_status=status,
                walking_time_s=w["walking_time_s"],
                mobility_note=None,
            ))
            continue

        hours_confidence = parse_confidence if status == "open" else 0.0
        trust = score_aed_properties(props)
        trust_badge_reasons = explain_trust_score(props, trust)
        distance_confidence = distance_confidence_by_id[aed_id]

        time_score = math.exp(-w["walking_time_s"] / TIME_DECAY_S)
        trust_normalized = (trust["total_score"] + 3) / 6.0

        final_score = (
            W_TIME * time_score
            + W_DIST_CONF * distance_confidence
            + W_HOURS_CONF * hours_confidence
            + W_TRUST * trust_normalized
        )

        ranked.append(RankedAed(
            aed_id=aed_id,
            building_name=display_name(props),
            latitude=props["LATITUDE"],
            longitude=props["LONGITUDE"],
            walking_time_s=w["walking_time_s"],
            walking_distance_m=w["walking_distance_m"],
            time_score=time_score,
            distance_confidence=distance_confidence,
            hours_status=status,
            hours_confidence=hours_confidence,
            trust_score=trust["total_score"],
            trust_badge=trust["badge"],
            trust_badge_reasons=trust_badge_reasons,
            trust_normalized=trust_normalized,
            final_score=final_score,
            uses_stairs=bool(w["uses_stairs"]),
            crosses_unmarked_road=bool(w["crosses_unmarked_road"]),
            uses_permissive_access=bool(w["uses_permissive_access"]),
            uses_synthetic_entrance=bool(w["uses_synthetic_entrance"]),
        ))

    ranked.sort(key=lambda r: -r["final_score"])
    return ranked, excluded


def rank_combined_timeline(
    test_lat: float,
    test_lon: float,
    test_date: date_type,
    aeds: Optional[List[dict]] = None,
    graph: Optional["object"] = None,
    hours: Optional[List[int]] = None,
    mobility_profile: str = "walk",
) -> Dict[int, Tuple[datetime, List[RankedAed], List[ExcludedAed]]]:
    """
    Phase 9: rank_combined for every hour of one day at a fixed test
    location, without repeating the walking/distance-confidence geometry
    per hour -- only hours_confidence (and therefore the combined score and
    ranking order) actually depends on the hour.

    mobility_profile: "walk" (default) or "wheelchair" -- see module
    docstring "Mobility profile" section. Fixed for the whole timeline
    query, same as the test location.

    Returns {hour: (test_dt, ranked, excluded)} for hour in `hours`
    (default 0..23).
    """
    if aeds is None:
        aeds = load_aeds()
    if graph is None:
        graph = load_walk_graph()
    if hours is None:
        hours = list(range(24))

    walking_by_id, distance_confidence_by_id = _shared_geometry(
        test_lat, test_lon, aeds, graph, mobility_profile
    )

    result: Dict[int, Tuple[datetime, List[RankedAed], List[ExcludedAed]]] = {}
    for h in hours:
        test_dt = datetime.combine(test_date, time_type(hour=h))
        ranked, excluded = _combine_for_datetime(test_dt, aeds, walking_by_id, distance_confidence_by_id)
        result[h] = (test_dt, ranked, excluded)
    return result
