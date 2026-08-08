"""
Plain-language explanation generation (Phase 6).

Turns the sub-scores already computed by combined_ranking.rank_combined
into two short pieces of text for a given (test location, date, time) query:
    top_explanation      -- 1-2 sentences on the #1-ranked AED
    runnerup_explanation -- 1 sentence comparing #2 against #1

This module does NOT do any ranking math. It only narrates numbers that
already exist on the RankedAed dicts (walking time, distance_confidence,
hours_confidence, trust). It must never invent a reason that isn't backed
by a real sub-score.

Tone contract (see backend/findings.md, 2026-08-08 explanation-design
review, for the four worked examples this prompt is built from):
    - State the recommendation confidently first ("X is the top pick"),
      not buried under hedging.
    - Name any specific uncertainty (shared walking-graph node, low trust
      badge, unparseable/partial hours) plainly, in the same breath, using
      the actual sub-score that causes it -- never a vague "may vary".
    - Never phrase a low-confidence result as if it were certain, and
      never claim real-time AED availability -- this is a simulation
      tool, not live emergency guidance (CLAUDE.md Section 7).
    - If nothing is uncertain, say so plainly rather than manufacturing a
      caveat -- honesty runs both directions.

Caching: one Gemini call per unique (test_lat, test_lon, date, time)
query, cached to backend/cache/explanations/<key>.json keyed on those four
values (rounded) plus the top/runner-up AED ids and their sub-scores, so a
re-ranking caused by a code change can't silently serve stale narrative
text for different numbers. Never called live in a demo loop -- the
frontend hits this cache, not the API, on repeat views of the same query.
"""

import hashlib
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple, TypedDict

from dotenv import load_dotenv

load_dotenv()

import google.generativeai as genai

from combined_ranking import ExcludedAed, RankedAed

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "explanations")
MODEL_NAME = "models/gemini-2.5-flash"

# Phase 9 (time slider): a full-day timeline query has up to 24 hourly
# rankings for one location. Re-running the top/runner-up explanation for
# every hour would burn tokens on narrating a ranking that hasn't actually
# changed -- Sentosa AEDs are almost all 24/7 (CLAUDE.md section 10), so in
# the common case the #1 pick never changes across the day and one call
# should cover all 24 hours. EXPLANATION_SEGMENT_CAP bounds the worst case
# (a location where the #1 pick flips every hour) at a fixed number of real
# Gemini calls per timeline query, regardless of how messy the underlying
# hours data turns out to be. Approved design, 2026-08-08 (see chat: "1 call
# typical, up to 6 worst case").
EXPLANATION_SEGMENT_CAP = 6

_model_cache: Optional["genai.GenerativeModel"] = None


class ExplanationResult(TypedDict):
    top_explanation: str
    runnerup_explanation: Optional[str]
    source: str  # "gemini" or "cache"


# The four hand-written patterns from the design review (findings.md),
# used as few-shot guidance so the model matches this tone instead of
# defaulting to generic hedge-everything AI copy.
_FEW_SHOT_EXAMPLES = """\
Example 1 -- clean top pick, no uncertainty anywhere:
Input: top_pick=Beach Arrival Plaza (098604-001), walking_time_s=160, distance_confidence=1.0, hours_status=open, hours_confidence=1.0, trust_badge=High
Output: {"top_explanation": "Beach Arrival Plaza is the top pick for this location and time \\u2014 about a 3-minute walk (160s) along the actual walking path, currently open on a fully verified hours pattern, and a High trust score (floor, description, and hours all check out). No uncertainty flags on this one."}

Example 2 -- runner-up losing purely on walking time:
Input: top_pick=Beach Arrival Plaza (098604-001) walking_time_s=160, runner_up=Le Meridien (098679-001) walking_time_s=178, both distance_confidence=1.0, hours_confidence=1.0, trust_badge=High
Output: {"runnerup_explanation": "Le Meridien is a close second, about 18 seconds slower to walk to than Beach Arrival Plaza \\u2014 that's the entire gap, since its hours and trust confidence are tied with the top pick."}

Example 3 -- runner-up losing on distance-confidence (routing/snap-collision uncertainty), not walking time:
Input: top_pick=Le Meridien (098679-002) walking_time_s=247, distance_confidence=1.0 (unique node); runner_up=098008-003 walking_time_s=246 (1s faster), distance_confidence=0.33 (shares its nearest node with 2 other AEDs); both hours_confidence=1.0, trust_badge=High
Output: {"runnerup_explanation": "098008-003 is actually 1 second faster to walk to than Le Meridien, but ranks lower: its nearest walking-path node is shared with two other AEDs, so the routing can't fully distinguish which of the three the walk actually leads to \\u2014 distance-confidence drops to 0.33. Its hours and trust are otherwise fully verified; the gap is specifically a routing-certainty issue, not distance or data quality."}

Example 4 -- top pick itself carrying uncertainty, stated plainly without burying the recommendation:
Input: top_pick=098008-003 ("31 Beach View", Level 2, First Aid Room), walking_time_s=20.2, distance_confidence=0.33 (shares its nearest node with 2 other AEDs in the same building), hours_status=open, hours_confidence=1.0, trust_badge=High
Output: {"top_explanation": "098008-003 is the top pick \\u2014 a 20-second walk, well ahead of anything else nearby \\u2014 though its walking distance is only approximate: it shares a mapped street-level access point with two other AEDs in the same building, so the routing can't fully distinguish between the three. Hours and location details are otherwise fully verified."}
"""

_SYSTEM_INSTRUCTION = """\
You write short plain-language explanations for a defibrillator (AED) \
discovery SIMULATION/PREPAREDNESS tool. This is NOT a live emergency app \
-- never phrase anything as guaranteed, real-time, or verified-right-now \
availability. You are given pre-computed sub-scores for a top-ranked AED \
and, if present, a runner-up AED for one specific test location, date, \
and time. You do not compute or alter any scores; you only narrate the \
numbers you are given.

Write:
- "top_explanation": 1-2 sentences on the top-ranked AED. State the \
recommendation confidently first (e.g. "X is the top pick"), then, in \
the same breath, name any specific uncertainty using the actual \
sub-score that causes it (shared walking-graph node -> distance_confidence \
< 1.0, low trust_badge, or partial/unparseable hours -> hours_confidence \
< 1.0). Do not bury the recommendation under hedging. If nothing is \
uncertain, say so plainly instead of inventing a caveat.
- "runnerup_explanation": 1 sentence, only if a runner-up is given, \
comparing it to the top pick and naming the SPECIFIC sub-score(s) that \
explain the gap (walking time difference, distance_confidence, \
hours_confidence, or trust_badge) -- never a vague "may vary" or generic \
hedge.

Never claim real-time AED availability or working condition. Never state \
a low-confidence result as if it were certain. Respond with ONLY a JSON \
object with keys "top_explanation" and "runnerup_explanation" (the second \
may be null if there is no runner-up). No markdown, no extra keys, no \
commentary outside the JSON.

""" + _FEW_SHOT_EXAMPLES


def _get_model() -> "genai.GenerativeModel":
    global _model_cache
    if _model_cache is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Add it to backend/.env (see .env.example)."
            )
        genai.configure(api_key=api_key)
        _model_cache = genai.GenerativeModel(
            MODEL_NAME,
            system_instruction=_SYSTEM_INSTRUCTION,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.4,
            ),
        )
    return _model_cache


def _cache_key(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    top: RankedAed,
    runner_up: Optional[RankedAed],
) -> str:
    payload = {
        "test_lat": round(test_lat, 5),
        "test_lon": round(test_lon, 5),
        "test_dt": test_dt.isoformat(),
        "top_aed_id": top["aed_id"],
        "top_walking_time_s": round(top["walking_time_s"], 1),
        "top_distance_confidence": top["distance_confidence"],
        "top_hours_confidence": top["hours_confidence"],
        "top_trust_badge": top["trust_badge"],
        "runner_up_aed_id": runner_up["aed_id"] if runner_up else None,
        "runner_up_walking_time_s": round(runner_up["walking_time_s"], 1) if runner_up else None,
        "runner_up_distance_confidence": runner_up["distance_confidence"] if runner_up else None,
        "runner_up_hours_confidence": runner_up["hours_confidence"] if runner_up else None,
        "runner_up_trust_badge": runner_up["trust_badge"] if runner_up else None,
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def _format_aed_input(label: str, r: RankedAed) -> str:
    return (
        f"{label}={r['building_name'] or r['aed_id']} ({r['aed_id']}), "
        f"walking_time_s={round(r['walking_time_s'], 1)}, "
        f"distance_confidence={round(r['distance_confidence'], 2)}, "
        f"hours_status={r['hours_status']}, "
        f"hours_confidence={r['hours_confidence']}, "
        f"trust_badge={r['trust_badge']}"
    )


def generate_explanation(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    ranked: List[RankedAed],
) -> ExplanationResult:
    """
    Generate (or fetch cached) top-pick + runner-up explanation text for a
    ranked list already produced by combined_ranking.rank_combined.

    ranked must be non-empty (callers should handle the fully-excluded /
    no-candidates case before calling this -- there is nothing to explain
    if the list is empty).
    """
    if not ranked:
        raise ValueError("generate_explanation requires a non-empty ranked list")

    top = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None

    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(test_lat, test_lon, test_dt, top, runner_up)
    path = _cache_path(key)

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        return ExplanationResult(
            top_explanation=cached["top_explanation"],
            runnerup_explanation=cached.get("runnerup_explanation"),
            source="cache",
        )

    prompt_lines = [_format_aed_input("top_pick", top)]
    if runner_up:
        prompt_lines.append(_format_aed_input("runner_up", runner_up))
    prompt = "\n".join(prompt_lines)

    model = _get_model()
    response = model.generate_content(prompt)
    parsed = json.loads(response.text)

    result = ExplanationResult(
        top_explanation=parsed["top_explanation"],
        runnerup_explanation=parsed.get("runnerup_explanation"),
        source="gemini",
    )

    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


class HourExplanation(TypedDict):
    top_explanation: Optional[str]
    runnerup_explanation: Optional[str]
    source: Optional[str]  # "gemini", "cache", or None if nothing to explain
    top_aed_id: Optional[str]
    segment_start_hour: int
    segment_end_hour: int
    capped: bool
    note: Optional[str]


class TimelineExplanationStats(TypedDict):
    segments: int
    gemini_calls: int
    cache_hits: int
    capped_segments: int


_CAPPED_NOTE = (
    "Ranking changed again in this window, beyond this tool's per-query "
    "explanation cap. The AEDs and sub-scores above are accurate for this "
    "hour -- only the narrative explanation text is unavailable here; open "
    "a fresh query at this specific time for a full explanation."
)


def generate_timeline_explanations(
    test_lat: float,
    test_lon: float,
    hourly: Dict[int, Tuple[datetime, List[RankedAed], List[ExcludedAed]]],
    cap: int = EXPLANATION_SEGMENT_CAP,
) -> Tuple[Dict[int, HourExplanation], TimelineExplanationStats]:
    """
    Phase 9: resolve one explanation per hour of a rank_combined_timeline()
    result, without calling Gemini once per hour.

    Groups consecutive hours that share the same #1 AED into segments (a
    segment boundary is exactly a point where the top pick actually
    changed). Calls generate_explanation() once per segment, in
    chronological order, for at most `cap` segments -- each such call
    already hits explanation.py's own on-disk cache keyed on
    (lat, lon, test_dt, top/runner-up ids + sub-scores), so a segment whose
    exact (hour, sub-scores) combination was explained in a prior query
    costs zero new Gemini tokens.

    Segments beyond the cap do NOT reuse a prior segment's explanation text
    -- that text names a specific AED (e.g. "Beach Arrival Plaza is the top
    pick..."), and reusing it verbatim for a capped hour whose actual #1 is
    a *different* AED would have the narrative contradict the ranked card
    it sits next to. Instead capped hours get top_explanation/
    runnerup_explanation=None and a generic note (no AED name) explaining
    why no narrative text is shown for this hour; the sub-scores (which are
    always freshly computed per hour, capping only affects Gemini text)
    stay fully accurate and visible regardless.
    """
    hours_sorted = sorted(hourly.keys())

    segments: List[dict] = []
    for h in hours_sorted:
        _, ranked, _ = hourly[h]
        top_id = ranked[0]["aed_id"] if ranked else None
        if segments and segments[-1]["top_id"] == top_id:
            segments[-1]["end"] = h
        else:
            segments.append({"start": h, "end": h, "top_id": top_id})

    explanations_by_hour: Dict[int, HourExplanation] = {}
    stats: TimelineExplanationStats = {
        "segments": len(segments),
        "gemini_calls": 0,
        "cache_hits": 0,
        "capped_segments": 0,
    }

    for i, seg in enumerate(segments):
        start_h = seg["start"]
        test_dt, ranked, _excluded = hourly[start_h]

        if seg["top_id"] is None:
            # No candidates this segment (e.g. everything closed/unreachable)
            # -- nothing to narrate, don't fabricate an explanation for it.
            exp = None
            capped = False
        elif i < cap:
            exp = generate_explanation(test_lat, test_lon, test_dt, ranked)
            if exp["source"] == "gemini":
                stats["gemini_calls"] += 1
            else:
                stats["cache_hits"] += 1
            capped = False
        else:
            exp = None
            capped = True
            stats["capped_segments"] += 1

        for h in range(seg["start"], seg["end"] + 1):
            explanations_by_hour[h] = HourExplanation(
                top_explanation=exp["top_explanation"] if exp else None,
                runnerup_explanation=exp["runnerup_explanation"] if exp else None,
                source=exp["source"] if exp else ("capped" if capped else None),
                top_aed_id=seg["top_id"],
                segment_start_hour=seg["start"],
                segment_end_hour=seg["end"],
                capped=capped,
                note=_CAPPED_NOTE if capped else None,
            )

    return explanations_by_hour, stats
