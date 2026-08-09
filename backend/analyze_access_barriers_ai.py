"""
One-time OFFLINE research script -- NOT wired into the live /rank pipeline.

Uses Gemini to review every AED_LOCATION_DESCRIPTION in the Sentosa subset
against the current hard-coded access-barrier keyword list in trust_score.py
(_ACCESS_BARRIER_CATEGORIES), for two purposes:

  1. Flag descriptions that might imply an access barrier the current regex
     patterns don't catch (coverage gap discovery).
  2. Independently confirm that descriptions currently matched by the
     keyword list are true positives, not false positives.

This is a discovery/QA aid only. It does not change trust_score.py, does
not touch the live scoring path, and produces no cached artifact the
backend reads at runtime -- the actual trust badge stays fixed
keyword-matching at query time, same as before this script exists. Output
is a human-reviewable report; a person decides which (if any) suggestions
get folded into the real keyword list by hand.

Run: python analyze_access_barriers_ai.py
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

import google.generativeai as genai

from trust_score import _ACCESS_BARRIER_CATEGORIES, describe_access_barrier

MODEL_NAME = "models/gemini-2.5-pro"

_CURRENT_PATTERNS_DESC = "\n".join(
    f'- {category} ("{reason}"): ' + ", ".join(patterns)
    for category, patterns, reason in _ACCESS_BARRIER_CATEGORIES
)

_SYSTEM_INSTRUCTION = f"""\
You are doing OFFLINE data-quality research for an AED (defibrillator) \
discovery tool. You are reviewing AED_LOCATION_DESCRIPTION strings from a \
public Singapore government registry (SCDF, via data.gov.sg), for the \
Sentosa island subset only. This is NOT a live decision -- your output is \
a reviewable research report for a human to manually approve changes to a \
fixed keyword list; you are not scoring anything that reaches an end user.

The existing tool already flags a description as an "access barrier" \
(distinct from just a landmark/location description) if it matches one of \
these three regex categories:

{_CURRENT_PATTERNS_DESC}

An access barrier means: the wording implies some process, gate, or person \
stands between arriving at the building/area and physically reaching the \
AED -- beyond simply walking there. A landmark word (kiosk, counter, \
reception, lift, ticket counter, control counter, etc.) naming WHERE the \
AED sits is NOT itself a barrier -- most of these are open, walk-up \
locations. Only flag wording that plausibly implies you'd need to ask \
someone, wait, present something, or be let through, to reach the AED \
itself.

For EACH description in the input list, decide:
- "flagged_by_keywords": true/false (given, not for you to compute)
- "ai_assessment": one of "confirmed_barrier" (agrees a real barrier is \
implied), "false_positive" (keyword matched but text doesn't actually \
imply a barrier), "not_a_barrier" (keywords didn't match and you agree), \
or "possible_gap" (keywords didn't match, but you think the wording DOES \
plausibly imply a barrier the current patterns miss)
- "matched_phrase": the exact substring (if any) that drives your \
assessment, else null
- "barrier_type": one of "staffing_queue", "ticketing", "scheduling", \
"verification", "physical_gate", or null if not_a_barrier
- "why_not_caught": for possible_gap only -- explain specifically why the \
existing regex patterns above don't match this text (e.g. wrong word, \
wrong phrase structure)
- "confidence": "high", "medium", or "low" -- how confident you are in \
ai_assessment
- "reasoning": 1-2 sentences justifying the call, explicit about which way \
you lean if it's ambiguous (e.g. "Ticketing Counter" could be a genuine \
barrier or just a landmark name -- say which way you lean and why)

Be conservative: a plain landmark name (kiosk, counter, front desk, \
reception, ticket counter, control counter, gantry, checkpoint) is \
"not_a_barrier" by default unless the surrounding wording adds an actual \
action requirement (e.g. "present ticket for entry", "queue at counter \
for access", "AED behind locked gate").

Respond with ONLY a JSON array, one object per input description, in the \
same order given, with keys: aed_id, description, flagged_by_keywords, \
ai_assessment, matched_phrase, barrier_type, why_not_caught, confidence, \
reasoning. No markdown, no commentary outside the JSON.
"""


def _get_model():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set. Add it to backend/.env.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        MODEL_NAME,
        system_instruction=_SYSTEM_INSTRUCTION,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )


def load_sentosa_descriptions(path="sentosa_aeds.geojson"):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = []
    for feat in data["features"]:
        p = feat["properties"]
        desc = p.get("AED_LOCATION_DESCRIPTION") or ""
        barrier = describe_access_barrier(desc)
        items.append({
            "aed_id": p.get("AED_ID"),
            "building_name": p.get("BUILDING_NAME"),
            "description": desc,
            "flagged_by_keywords": barrier["flagged"],
        })
    return items


def main():
    items = load_sentosa_descriptions()
    print(f"Loaded {len(items)} Sentosa AED descriptions.")

    prompt_lines = [
        f'{it["aed_id"]}: "{it["description"]}" (flagged_by_keywords={it["flagged_by_keywords"]})'
        for it in items
    ]
    prompt = "Review these descriptions:\n" + "\n".join(prompt_lines)

    model = _get_model()
    response = model.generate_content(prompt)
    results = json.loads(response.text)

    out_path = "cache/access_barrier_ai_review.json"
    os.makedirs("cache", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {len(results)} assessments to {out_path}")


if __name__ == "__main__":
    main()
