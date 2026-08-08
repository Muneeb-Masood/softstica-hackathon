"""
Data trust / findability score for AED records (Phase 3).

Produces a rule-based confidence signal for how easy a given AED will be to
actually find and use in the field, and how much to trust its operating-hours
data. This is a proxy for registry data quality, not a measurement of the
physical AED itself -- it never claims the AED is present, working, or
currently accessible.

Score components (see CLAUDE.md Phase 3 for the spec this implements):
    floor_score        : +1 if AED_LOCATION_FLOOR_LEVEL is present, else 0
    description_score  : +1 if AED_LOCATION_DESCRIPTION names a concrete
                          landmark (kiosk, lift, entrance, shop name, etc.)
                          -1 if it relies only on vague directional words
                          (near, opposite, beside, ...) with no concrete
                          anchor to attach them to
                           0 otherwise (e.g. blank, or neither vague nor
                          concrete language)
    hours_score         : derived from Phase 2's parse confidence for
                          OPERATING_HOURS:
                              +1 if confidence == 1.0 (parsed cleanly)
                               0 if confidence == 0.5 (partially parsed)
                              -1 if confidence == 0.0 (fully unparseable)

total_score = floor_score + description_score + hours_score, range -3..3

Badge thresholds (documented assumption, not given verbatim in the brief):
    High               : total_score >= 3   (every component maxed out)
    Medium             : total_score == 2   (one component short of perfect)
    Needs Verification : total_score <= 1

Rationale for the cutoff between Medium and Needs Verification: any record
with a -1 in a single component (a vague-only description with no concrete
anchor, or fully unparseable hours) is capped at total_score == 1 even if
its other two components are maxed out (1 + 1 - 1 = 1). That is treated as
"Needs Verification" rather than "Medium" on purpose -- a genuinely
unfindable location or an unknown open/closed status is a real accessibility
risk on its own and shouldn't be averaged away by two unrelated fields being
fine. This mirrors the brief's requirement to "safely abstain when
accessibility cannot be established."

Assumption: hours_score is computed against a fixed reference datetime
(REFERENCE_TEST_DT below), not the live test datetime a user picks in the
ranking UI. This is a data-quality signal about the OPERATING_HOURS *string*
and should not change just because someone drags the time slider. It reuses
parse_operating_hours from hours_parser.py rather than duplicating parsing
logic; see that module's docstring for why confidence can still land at 0.0
for a clean string whose day-of-week isn't covered (a "partial week" pattern)
-- none of the Sentosa OPERATING_HOURS strings observed so far are affected
by this, since every pattern found covers all seven days between its
segments.
"""

import re
from datetime import datetime
from typing import List, Optional, TypedDict

from hours_parser import parse_operating_hours

REFERENCE_TEST_DT = datetime(2026, 8, 3, 12, 0)  # a Monday, used only to score OPERATING_HOURS quality

_CONCRETE_LANDMARK_WORDS = [
    "kiosk", "entrance", "exit", "lift", "elevator", "escalator",
    "staircase", "stairs", "stair", "counter", "reception", "lobby",
    "shop", "store", "retail", "office", "gantry", "restaurant", "cafe",
    "café", "pool", "gym", "clinic", "carpark", "car park", "pillar",
    "atm", "toilet", "restroom", "locker", "cinema", "theatre", "theater",
    "ride", "station", "tower", "museum", "aquarium", "waterpark",
    "casino", "hotel", "ballroom", "hall", "garden", "deck", "court",
    "gate", "bridge", "jetty", "pier", "walkway", "corridor", "block",
    "wing", "plaza", "market", "mall", "centre", "center", "headquarters",
    "concierge", "checkpoint", "barrier", "crossing", "building",
    "lounge", "bar", "spa", "cable car", "board walk", "boardwalk",
    "first aid", "security", "kindergarten", "control room",
    "control station", "control counter", "front desk", "frontdesk",
    "ticket",
]

_VAGUE_ONLY_WORDS = [
    "near", "opposite", "beside", "next to", "close to", "along",
    "outside", "behind", "in front of", "around", "by the",
]


class TrustScore(TypedDict):
    floor_score: int
    description_score: int
    hours_score: int
    hours_status: str
    hours_confidence: float
    total_score: int
    badge: str


def _score_floor(floor_level: Optional[str]) -> int:
    return 1 if (floor_level or "").strip() else 0


def _score_description(description: Optional[str]) -> int:
    text = (description or "").lower()
    if not text.strip():
        return 0

    has_concrete = any(
        re.search(r"\b" + re.escape(word) + r"\b", text)
        for word in _CONCRETE_LANDMARK_WORDS
    )
    if has_concrete:
        return 1

    has_vague = any(word in text for word in _VAGUE_ONLY_WORDS)
    if has_vague:
        return -1

    return 0


def _score_hours(operating_hours: Optional[str]) -> tuple[int, str, float]:
    status, confidence = parse_operating_hours(operating_hours, REFERENCE_TEST_DT)
    if confidence == 1.0:
        return 1, status, confidence
    if confidence == 0.5:
        return 0, status, confidence
    return -1, status, confidence


def _badge_for(total_score: int) -> str:
    if total_score >= 3:
        return "High"
    if total_score == 2:
        return "Medium"
    return "Needs Verification"


def compute_trust_score(
    floor_level: Optional[str],
    description: Optional[str],
    operating_hours: Optional[str],
) -> TrustScore:
    """Compute the rule-based trust/findability score for one AED record."""
    floor_score = _score_floor(floor_level)
    description_score = _score_description(description)
    hours_score, hours_status, hours_confidence = _score_hours(operating_hours)

    total_score = floor_score + description_score + hours_score

    return TrustScore(
        floor_score=floor_score,
        description_score=description_score,
        hours_score=hours_score,
        hours_status=hours_status,
        hours_confidence=hours_confidence,
        total_score=total_score,
        badge=_badge_for(total_score),
    )


def score_aed_properties(properties: dict) -> TrustScore:
    """Convenience wrapper: compute a TrustScore from a raw AED GeoJSON `properties` dict."""
    return compute_trust_score(
        floor_level=properties.get("AED_LOCATION_FLOOR_LEVEL"),
        description=properties.get("AED_LOCATION_DESCRIPTION"),
        operating_hours=properties.get("OPERATING_HOURS"),
    )


def explain_trust_score(properties: dict, trust: TrustScore) -> List[str]:
    """
    Plain-language reasons for whichever sub-scores are dragging total_score
    down (Phase 11: the "needs re-verification" list needs to say *why*, not
    just show a badge). Only components that are 0 or negative produce a
    reason -- a maxed-out +1 component isn't a data-quality issue worth
    surfacing here.
    """
    reasons: List[str] = []

    if trust["floor_score"] == 0:
        reasons.append("Floor level is not recorded in the registry.")

    description = (properties.get("AED_LOCATION_DESCRIPTION") or "").strip()
    if trust["description_score"] == -1:
        reasons.append(
            "Location description relies only on vague directional wording "
            "(e.g. 'near', 'opposite', 'beside') with no concrete landmark to anchor it."
        )
    elif trust["description_score"] == 0:
        if not description:
            reasons.append("Location description is blank.")
        else:
            reasons.append(
                "Location description has no recognizable concrete landmark "
                "or directional wording -- unclear as written."
            )

    if trust["hours_score"] == -1:
        reasons.append(
            "Operating hours could not be parsed at all; open/closed status would show as 'unknown'."
        )
    elif trust["hours_score"] == 0:
        reasons.append(
            "Operating hours only partially parsed -- part of the string didn't match a recognized pattern."
        )

    return reasons
