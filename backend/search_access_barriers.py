"""
Search AED_LOCATION_DESCRIPTION for procedural-access-barrier / human-mediated-
access language, run against both the Sentosa subset and the full national
dataset. Read-only profiling script -- does not modify any pipeline files.
"""
import json
import re

PATTERNS = [
    r"approach security",
    r"approach staff",
    r"approach officer",
    r"call ahead",
    r"during school hours",
    r"requires? assistance",
    r"seek assistance",
    r"please approach",
    r"request assistance",
    r"assistance from",
    r"approach.{0,15}(security|staff|officer|counter|receptionist|concierge)",
    r"contact security",
    r"inform security",
    r"notify security",
    r"accompanied by",
    r"with escort",
]
COMPILED = [re.compile(p, re.IGNORECASE) for p in PATTERNS]


def matches(description: str):
    hits = []
    for pat in COMPILED:
        if pat.search(description):
            hits.append(pat.pattern)
    return hits


def search_file(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    results = []
    for feat in data["features"]:
        p = feat["properties"]
        desc = p.get("AED_LOCATION_DESCRIPTION") or ""
        hits = matches(desc)
        if hits:
            results.append({
                "AED_ID": p.get("AED_ID"),
                "BUILDING_NAME": p.get("BUILDING_NAME"),
                "AED_LOCATION_DESCRIPTION": desc,
                "matched_patterns": hits,
            })
    return results, len(data["features"])


if __name__ == "__main__":
    sentosa_results, sentosa_total = search_file("sentosa_aeds.geojson")
    print(f"=== SENTOSA ({sentosa_total} records) ===")
    print(f"Matches: {len(sentosa_results)}\n")
    for r in sentosa_results:
        print(f"AED_ID: {r['AED_ID']}")
        print(f"Building: {r['BUILDING_NAME']}")
        print(f"Description: {r['AED_LOCATION_DESCRIPTION']}")
        print(f"Matched: {r['matched_patterns']}")
        print()

    print("\n" + "=" * 60 + "\n")

    national_results, national_total = search_file("../AED_LOCATIONS.geojson")
    print(f"=== NATIONAL ({national_total} records) ===")
    print(f"Matches: {len(national_results)} ({len(national_results)/national_total:.1%})\n")
    print("Sample of 20:\n")
    for r in national_results[:20]:
        print(f"AED_ID: {r['AED_ID']} | Building: {r['BUILDING_NAME']}")
        print(f"Description: {r['AED_LOCATION_DESCRIPTION']}")
        print()
