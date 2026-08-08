#!/usr/bin/env python3
"""Tests for the operating hours parser (Phase 2)."""

import sys
from datetime import datetime

from hours_parser import parse_operating_hours

# (label, operating_hours_str, test_dt, expected_status, expected_confidence)
CASES = [
    # --- Clean 24/7 pattern (Sentosa's dominant pattern) ---
    ("24/7, Monday noon", "Mon - Sun 00:00-23:59", datetime(2026, 8, 3, 12, 0), "open", 1.0),
    ("24/7, Monday 22:00", "Mon - Sun 00:00-23:59", datetime(2026, 8, 3, 22, 0), "open", 1.0),
    ("24/7, trailing semicolon", "Mon - Sun 00:00-23:59;", datetime(2026, 8, 3, 12, 0), "open", 1.0),

    # --- Clean daily-with-limited-hours patterns ---
    ("Daily 9-21:30, open at 09:00 Monday", "Mon - Sun 09:00-21:30", datetime(2026, 8, 3, 9, 0), "open", 1.0),
    ("Daily 9-21:30, closed at 22:00 Monday", "Mon - Sun 09:00-21:30", datetime(2026, 8, 3, 22, 0), "closed", 1.0),
    ("Daily 10-17, open at 10:00 Monday", "Mon - Sun 10:00-17:00", datetime(2026, 8, 3, 10, 0), "open", 1.0),
    ("Daily 10-17, closed at 18:00 Monday", "Mon - Sun 10:00-17:00", datetime(2026, 8, 3, 18, 0), "closed", 1.0),

    # --- REQUIRED: multi-segment pattern, semicolon-separated, different days ---
    # "Mon - Fri 08:30-18:00; Sat - Sun Closed" tested on a Monday.
    ("Multi-segment, Monday within hours (08:30-18:00)",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 3, 10, 0), "open", 1.0),
    ("Multi-segment, Monday before opening (08:00)",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 3, 8, 0), "closed", 1.0),
    ("Multi-segment, Monday after closing (20:00)",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 3, 20, 0), "closed", 1.0),
    ("Multi-segment, Monday exactly at open boundary (08:30)",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 3, 8, 30), "open", 1.0),
    ("Multi-segment, Monday exactly at close boundary (18:00)",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 3, 18, 0), "open", 1.0),
    # Saturday/Sunday fall in the explicit "Closed" segment.
    ("Multi-segment, Saturday (explicitly closed segment)",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 8, 10, 0), "closed", 1.0),
    ("Multi-segment, Sunday (explicitly closed segment)",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 9, 10, 0), "closed", 1.0),
    # Friday is the boundary day of the weekday segment.
    ("Multi-segment, Friday within hours",
     "Mon - Fri 08:30-18:00; Sat - Sun Closed", datetime(2026, 8, 7, 12, 0), "open", 1.0),

    # --- Partial-week pattern: weekend never mentioned at all -> unknown, not guessed ---
    ("Partial week (weekdays only), Saturday not covered",
     "Mon - Fri 08:30-18:00", datetime(2026, 8, 8, 10, 0), "unknown", 0.0),
    ("Partial week (weekdays only), Monday still resolves",
     "Mon - Fri 08:30-18:00", datetime(2026, 8, 3, 10, 0), "open", 1.0),

    # --- Standalone "Closed" for the whole string ---
    ("Standalone Closed", "Closed", datetime(2026, 8, 3, 10, 0), "closed", 1.0),

    # --- Blank / missing ---
    ("Empty string", "", datetime(2026, 8, 3, 12, 0), "unknown", 0.0),
    ("Whitespace only", "   ", datetime(2026, 8, 3, 12, 0), "unknown", 0.0),
    ("None", None, datetime(2026, 8, 3, 12, 0), "unknown", 0.0),

    # --- Unparseable garbage ---
    ("Garbage text", "Please call ahead", datetime(2026, 8, 3, 12, 0), "unknown", 0.0),
    ("Malformed time range", "Mon - Sun 25:99-30:00", datetime(2026, 8, 3, 12, 0), "unknown", 0.0),

    # --- Partially parseable: one good segment, one garbage segment ---
    # The garbage segment doesn't cover Monday, but its presence should drop
    # confidence even though the Monday answer is still derivable.
    ("Partial parse, garbage segment doesn't affect Monday match",
     "Mon - Fri 08:30-18:00; !!! garbage !!!", datetime(2026, 8, 3, 10, 0), "open", 0.5),

    # --- Midnight-crossing time range ---
    ("Midnight-crossing hours, open just after midnight",
     "Mon - Sun 22:00-02:00", datetime(2026, 8, 4, 1, 0), "open", 1.0),
    ("Midnight-crossing hours, closed at noon",
     "Mon - Sun 22:00-02:00", datetime(2026, 8, 3, 12, 0), "closed", 1.0),
]


def run():
    passed = 0
    failed = 0

    print("=" * 90)
    print("OPERATING HOURS PARSER TEST RESULTS")
    print("=" * 90)

    for label, hours_str, test_dt, expected_status, expected_conf in CASES:
        status, confidence = parse_operating_hours(hours_str, test_dt)
        ok = (status == expected_status) and (confidence == expected_conf)
        marker = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        weekday_name = test_dt.strftime("%A") if test_dt else "-"
        print(f"[{marker}] {label}")
        print(f"       input={hours_str!r}  test={test_dt} ({weekday_name})")
        print(f"       expected=({expected_status}, {expected_conf})  got=({status}, {confidence})")

    print("=" * 90)
    print(f"{passed} passed, {failed} failed out of {len(CASES)}")
    print("=" * 90)

    return failed == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
