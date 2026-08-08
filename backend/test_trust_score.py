#!/usr/bin/env python3
"""Runs the trust score (Phase 3) against every AED in sentosa_aeds.geojson."""

import json
from collections import Counter

from trust_score import explain_trust_score, score_aed_properties

GEOJSON_PATH = "sentosa_aeds.geojson"


def load_features():
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["features"]


def run():
    features = load_features()
    results = []
    for feat in features:
        props = feat["properties"]
        score = score_aed_properties(props)
        results.append((props, score))

    badge_counts = Counter(score["badge"] for _, score in results)

    print("=" * 90)
    print(f"TRUST SCORE DISTRIBUTION  (n={len(results)})")
    print("=" * 90)
    for badge in ["High", "Medium", "Needs Verification"]:
        count = badge_counts.get(badge, 0)
        pct = 100 * count / len(results) if results else 0
        print(f"  {badge:22s}: {count:3d}  ({pct:5.1f}%)")
    print()

    # Pick example AEDs: a High, a Medium if present, and at least one
    # Needs Verification (fall back to lowest-scoring record if none).
    by_badge = {"High": [], "Medium": [], "Needs Verification": []}
    for props, score in results:
        by_badge[score["badge"]].append((props, score))

    examples = []
    if by_badge["High"]:
        examples.append(by_badge["High"][0])
    if by_badge["Medium"]:
        examples.append(by_badge["Medium"][0])
    if by_badge["Needs Verification"]:
        examples.append(by_badge["Needs Verification"][0])
    else:
        lowest = min(results, key=lambda pair: pair[1]["total_score"])
        examples.append(lowest)
    # One more High example for variety, if available.
    if len(by_badge["High"]) > 1:
        examples.append(by_badge["High"][1])

    print("=" * 90)
    print("EXAMPLE SCORE BREAKDOWNS")
    print("=" * 90)
    for props, score in examples:
        print(f"AED_ID: {props['AED_ID']}  ({props.get('BUILDING_NAME')})")
        print(f"  Floor level        : {props.get('AED_LOCATION_FLOOR_LEVEL')!r}")
        print(f"  Description        : {props.get('AED_LOCATION_DESCRIPTION')!r}")
        print(f"  Operating hours    : {props.get('OPERATING_HOURS')!r}")
        print(f"  floor_score        : {score['floor_score']:+d}")
        print(f"  description_score  : {score['description_score']:+d}")
        print(f"  hours_score        : {score['hours_score']:+d}  "
              f"(status={score['hours_status']}, confidence={score['hours_confidence']})")
        print(f"  total_score        : {score['total_score']:+d}")
        print(f"  badge              : {score['badge']}")
        reasons = explain_trust_score(props, score)
        if reasons:
            print("  reasons            :")
            for reason in reasons:
                print(f"    - {reason}")
        print("-" * 90)

    # Phase 11: sanity-check the "needs re-verification" view -- every
    # Needs Verification AED must have at least one plain-language reason.
    print("=" * 90)
    print("PHASE 11: NEEDS-VERIFICATION REASON COVERAGE")
    print("=" * 90)
    missing_reasons = []
    for props, score in by_badge["Needs Verification"]:
        reasons = explain_trust_score(props, score)
        if not reasons:
            missing_reasons.append(props["AED_ID"])
    if missing_reasons:
        print(f"  FAIL: {len(missing_reasons)} Needs Verification AED(s) with no reason: {missing_reasons}")
    else:
        print(f"  OK: all {len(by_badge['Needs Verification'])} Needs Verification AED(s) have >=1 reason")


if __name__ == "__main__":
    run()
