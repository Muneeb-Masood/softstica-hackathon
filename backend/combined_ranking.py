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
all walking-graph nodes as candidate test points.

time_score = exp(-walking_time_s / TIME_DECAY_S). TIME_DECAY_S=300s (5 min)
is a documented planning-level assumption, not a measured or live value --
it sets the scale at which additional walking time stops mattering much,
not a claim about real response times.

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
"""

import math
from datetime import datetime
from typing import List, Optional, Tuple, TypedDict

from distance_ranking import (
    compute_snap_collision_confidence,
    load_aeds,
    load_walk_graph,
    rank_by_walking_time,
)
from hours_parser import parse_operating_hours
from trust_score import score_aed_properties

TIME_DECAY_S = 300.0
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
    trust_normalized: float
    final_score: float


class ExcludedAed(TypedDict):
    aed_id: str
    building_name: Optional[str]
    reason: str  # "unreachable" or "closed"
    hours_status: Optional[str]
    walking_time_s: Optional[float]


def rank_combined(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    aeds: Optional[List[dict]] = None,
    graph: Optional["object"] = None,
) -> Tuple[List[RankedAed], List[ExcludedAed]]:
    """
    Rank Sentosa AEDs for a test location, date, and time.

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

    walking = rank_by_walking_time(test_lat, test_lon, aeds=aeds, graph=graph)
    walking_by_id = {r["aed_id"]: r for r in walking}
    distance_confidence_by_id = compute_snap_collision_confidence(aeds, graph)

    ranked: List[RankedAed] = []
    excluded: List[ExcludedAed] = []

    for props in aeds:
        aed_id = props["AED_ID"]
        w = walking_by_id[aed_id]

        if not w["reachable"]:
            excluded.append(ExcludedAed(
                aed_id=aed_id,
                building_name=props.get("BUILDING_NAME"),
                reason="unreachable",
                hours_status=None,
                walking_time_s=None,
            ))
            continue

        status, parse_confidence = parse_operating_hours(props.get("OPERATING_HOURS"), test_dt)

        if status == "closed":
            excluded.append(ExcludedAed(
                aed_id=aed_id,
                building_name=props.get("BUILDING_NAME"),
                reason="closed",
                hours_status=status,
                walking_time_s=w["walking_time_s"],
            ))
            continue

        hours_confidence = parse_confidence if status == "open" else 0.0
        trust = score_aed_properties(props)
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
            building_name=props.get("BUILDING_NAME"),
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
            trust_normalized=trust_normalized,
            final_score=final_score,
        ))

    ranked.sort(key=lambda r: -r["final_score"])
    return ranked, excluded
