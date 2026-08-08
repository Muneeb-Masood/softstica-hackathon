"""
Operating hours parser for AED records.

Parses OPERATING_HOURS strings (e.g. "Mon - Sun 00:00-23:59") and determines
whether an AED is open at a given test datetime.

Returns:
    (status, confidence):
        - status: "open", "closed", or "unknown"
        - confidence: float 0.0-1.0, where 1.0 means fully parseable and definitive

Assumptions:
    - Day abbreviations use standard English (Mon..Sun), case-insensitive
    - Multiple day/time segments are separated by semicolons, e.g.
      "Mon - Fri 08:30-18:00; Sat - Sun Closed", and are matched independently
      against the test day
    - A segment applies only to the days it names; if no successfully-parsed
      segment names the test day, the status is "unknown" rather than guessed
    - Time ranges are inclusive of both endpoints; a range where the end time
      is earlier than the start time is treated as crossing midnight
    - "Closed" in place of a time range means the AED is closed on those days
"""

import re
from collections import namedtuple
from datetime import datetime
from typing import List, Optional, Tuple

DAY_TO_WEEKDAY = {
    "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3,
    "Fri": 4, "Sat": 5, "Sun": 6,
}

Segment = namedtuple("Segment", ["days", "is_closed", "start_min", "end_min"])

_DAY_RANGE_RE = re.compile(
    r'^(?P<d1>[A-Za-z]{3})\s*-\s*(?P<d2>[A-Za-z]{3})\s+(?P<rest>.+)$'
)
_SINGLE_DAY_RE = re.compile(r'^(?P<d>[A-Za-z]{3})\s+(?P<rest>.+)$')
_TIME_RANGE_RE = re.compile(
    r'^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$'
)


def _expand_day_range(start_wd: int, end_wd: int) -> frozenset:
    """Expand a (start_weekday, end_weekday) pair into the set of covered weekdays."""
    if start_wd <= end_wd:
        return frozenset(range(start_wd, end_wd + 1))
    # Wrap-around range, e.g. Sat - Tue
    return frozenset(range(start_wd, 7)) | frozenset(range(0, end_wd + 1))


def _parse_single_segment(seg: str) -> Optional[Segment]:
    """
    Parse one day-range/time-range segment, e.g. "Mon - Fri 08:30-18:00" or
    "Sat - Sun Closed". Returns None if the segment can't be parsed.
    """
    seg = seg.strip()
    if not seg:
        return None

    if seg.upper() == "CLOSED":
        # Bare "Closed" with no day range given applies to every day.
        return Segment(days=frozenset(range(7)), is_closed=True, start_min=None, end_min=None)

    m = _DAY_RANGE_RE.match(seg)
    if m:
        d1, d2, rest = m.group("d1"), m.group("d2"), m.group("rest")
    else:
        m = _SINGLE_DAY_RE.match(seg)
        if not m:
            return None
        d1 = d2 = m.group("d")
        rest = m.group("rest")

    d1c, d2c = d1.capitalize(), d2.capitalize()
    if d1c not in DAY_TO_WEEKDAY or d2c not in DAY_TO_WEEKDAY:
        return None

    days = _expand_day_range(DAY_TO_WEEKDAY[d1c], DAY_TO_WEEKDAY[d2c])
    rest = rest.strip()

    if rest.upper() == "CLOSED":
        return Segment(days=days, is_closed=True, start_min=None, end_min=None)

    tm = _TIME_RANGE_RE.match(rest)
    if not tm:
        return None

    sh, sm, eh, em = (int(g) for g in tm.groups())
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
        return None

    return Segment(
        days=days, is_closed=False,
        start_min=sh * 60 + sm, end_min=eh * 60 + em,
    )


def _parse_segments(operating_hours_str: str) -> Tuple[List[Segment], int]:
    """
    Split OPERATING_HOURS into semicolon-separated segments and parse each.
    Returns (successfully_parsed_segments, failure_count).
    """
    cleaned = operating_hours_str.strip().rstrip(";").strip()
    raw_segments = [s.strip() for s in cleaned.split(";") if s.strip()]

    parsed = []
    failures = 0
    for raw in raw_segments:
        seg = _parse_single_segment(raw)
        if seg is None:
            failures += 1
        else:
            parsed.append(seg)
    return parsed, failures


def _is_time_in_segment(segment: Segment, test_dt: datetime) -> bool:
    """Check whether test_dt's time-of-day falls within an open segment's time range."""
    if segment.is_closed:
        return False

    test_min = test_dt.hour * 60 + test_dt.minute
    start_min, end_min = segment.start_min, segment.end_min

    if start_min <= end_min:
        return start_min <= test_min <= end_min
    # Crosses midnight, e.g. 22:00-02:00
    return test_min >= start_min or test_min <= end_min


def parse_operating_hours(operating_hours_str: Optional[str], test_dt: datetime) -> Tuple[str, float]:
    """
    Parse an OPERATING_HOURS string and determine open/closed status at test_dt.

    Args:
        operating_hours_str: The OPERATING_HOURS string from the AED record.
        test_dt: The datetime to test against.

    Returns:
        (status, confidence):
            - status: "open", "closed", or "unknown"
            - confidence: float 0.0-1.0
    """
    if operating_hours_str is None or not operating_hours_str.strip():
        return "unknown", 0.0

    parsed, failures = _parse_segments(operating_hours_str)

    if not parsed:
        # Nothing parsed at all (blank after cleaning, or fully unparseable).
        return "unknown", 0.0

    weekday = test_dt.weekday()
    matching = [seg for seg in parsed if weekday in seg.days]

    if not matching:
        # No successfully-parsed segment names this day (e.g. a partial-week
        # pattern that never mentions it). Don't guess.
        return "unknown", 0.0

    is_open = any(_is_time_in_segment(seg, test_dt) for seg in matching)
    status = "open" if is_open else "closed"

    # Full confidence only if every segment in the string parsed cleanly.
    confidence = 1.0 if failures == 0 else 0.5
    return status, confidence
