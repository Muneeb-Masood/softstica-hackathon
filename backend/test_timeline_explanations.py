#!/usr/bin/env python3
"""
Synthetic 8-changepoint test for explanation.generate_timeline_explanations
(Phase 9 capped-explanation logic).

Real Sentosa data rarely produces more than a couple of #1-pick changes in
one day (CLAUDE.md section 10: most AEDs are 24/7), so EXPLANATION_SEGMENT_CAP
(6) is never actually exercised by a real query -- this test builds a
synthetic `hourly` dict where the #1 AED changes 8 times across 24 hours (9
segments) using 9 distinct real Sentosa AED records, to force the cap.

Specifically guards against the capped-segment name-mismatch bug: a capped
hour must never show explanation text (which would name a specific AED) or
a note that mentions any AED name/id, since the note sits next to a ranked
card whose actual #1 may be a different AED than whatever segment last had
a real explanation generated.
"""
import sys

from datetime import datetime

from distance_ranking import load_aeds
from explanation import EXPLANATION_SEGMENT_CAP, generate_timeline_explanations

TEST_LAT, TEST_LON = 1.2525, 103.8200
TEST_DATE = datetime(2026, 8, 8)


def make_ranked(top_props, runner_props, walking_time_s):
    def entry(props, wt):
        return {
            "aed_id": props["AED_ID"],
            "building_name": props.get("BUILDING_NAME"),
            "latitude": props["LATITUDE"],
            "longitude": props["LONGITUDE"],
            "walking_time_s": wt,
            "walking_distance_m": wt * 1.34,
            "time_score": 0.9,
            "distance_confidence": 1.0,
            "hours_status": "open",
            "hours_confidence": 1.0,
            "trust_score": 2,
            "trust_badge": "High",
            "trust_normalized": 1.0,
            "final_score": 0.9,
        }

    return [entry(top_props, walking_time_s), entry(runner_props, walking_time_s + 20)]


def build_synthetic_hourly(chosen):
    """9 distinct #1 AEDs across 24 hours -> 8 changepoints, 9 segments."""
    hourly = {}
    for h in range(24):
        idx = min(h, 8)
        top = chosen[idx]
        runner = chosen[(idx + 1) % 9]
        test_dt = TEST_DATE.replace(hour=h)
        hourly[h] = (test_dt, make_ranked(top, runner, walking_time_s=100 + idx * 10), [])
    return hourly


def run():
    aeds = load_aeds()
    chosen = aeds[:9]
    all_names = [a.get("BUILDING_NAME") or a["AED_ID"] for a in chosen]
    all_ids = [a["AED_ID"] for a in chosen]

    hourly = build_synthetic_hourly(chosen)
    explanations_by_hour, stats = generate_timeline_explanations(TEST_LAT, TEST_LON, hourly)

    print("=" * 90)
    print("SYNTHETIC 8-CHANGEPOINT TIMELINE EXPLANATION TEST")
    print("=" * 90)
    print("stats:", stats)

    failures = []

    if stats["segments"] != 9:
        failures.append(f"expected 9 segments, got {stats['segments']}")
    if stats["capped_segments"] != 3:
        failures.append(f"expected 3 capped segments, got {stats['capped_segments']}")
    real_calls = stats["gemini_calls"] + stats["cache_hits"]
    if real_calls != EXPLANATION_SEGMENT_CAP:
        failures.append(
            f"expected {EXPLANATION_SEGMENT_CAP} real explanation calls, got {real_calls}"
        )

    print()
    for h in range(24):
        exp = explanations_by_hour[h]
        true_top_id = hourly[h][1][0]["aed_id"]
        true_top_name = hourly[h][1][0]["building_name"]
        status = "CAPPED" if exp["capped"] else "explained"
        print(
            f"  hour {h:>2} | true top: {true_top_name or true_top_id:<30} | {status:<9} | "
            f"top_explanation={'<set>' if exp['top_explanation'] else 'None':<7} | "
            f"note={'<set>' if exp['note'] else 'None'}"
        )

        if exp["top_aed_id"] != true_top_id:
            failures.append(f"hour {h}: top_aed_id mismatch ({exp['top_aed_id']} != {true_top_id})")

        if exp["capped"]:
            if exp["top_explanation"] is not None or exp["comparisons"]:
                failures.append(f"hour {h}: capped but explanation text is non-null (name-mismatch risk)")
            if not exp["note"]:
                failures.append(f"hour {h}: capped but no note shown to the user")
            else:
                for name in all_names:
                    if name and name in exp["note"]:
                        failures.append(f"hour {h}: capped note names an AED ('{name}')")
                for aed_id in all_ids:
                    if aed_id in exp["note"]:
                        failures.append(f"hour {h}: capped note contains an AED id ({aed_id})")

    print()
    if failures:
        print(f"FAILED -- {len(failures)} issue(s):")
        for f in failures:
            print("  -", f)
        sys.exit(1)

    print(
        "PASSED -- 9 segments / 8 changepoints, "
        f"{stats['capped_segments']} capped, {real_calls} real explanations generated; "
        "no capped segment shows explanation text or a note naming a mismatched AED."
    )


if __name__ == "__main__":
    run()
