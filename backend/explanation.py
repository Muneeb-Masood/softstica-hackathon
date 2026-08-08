"""
Plain-language explanation generation (Phase 6).

Turns the sub-scores already computed by combined_ranking.rank_combined
into narrative text for the top TOP_N_EXPLAIN (5) ranked AEDs of a given
(test location, date, time) query:
    top_explanation -- 1-2 sentences on the #1-ranked AED
    comparisons     -- one short sentence per AED at positions #2-#5 (however
                        many are actually present, up to 4), each comparing
                        that AED against #1 specifically -- not chained
                        against its immediate predecessor, and not a full
                        standalone paragraph. This matches the brief's
                        "identify why an AED was ranked" framing: a user
                        scanning the list wants to know why each entry
                        didn't beat the winner, not five independent essays.

This module does NOT do any ranking math. It only narrates numbers that
already exist on the RankedAed dicts (walking time, distance_confidence,
hours_confidence, trust). It must never invent a reason that isn't backed
by a real sub-score.

One Gemini call covers the whole query regardless of how many of the 5
positions are present -- the single prompt lists top_pick plus every
candidate_2..candidate_5 available, and the single JSON response carries
top_explanation plus one comparison string per candidate, in order. This is
unchanged from the original top+runner-up design (which was already 1 call,
not 2); extending to 5 positions grows the prompt/response size, not the
call count.

Tone contract (see backend/findings.md, 2026-08-08 explanation-design
review, for the four worked examples this prompt is built from):
    - State the recommendation confidently first ("X is the top pick"),
      not buried under hedging.
    - Name any specific uncertainty (shared walking-graph node, low trust
      badge, unparseable/partial hours, access barrier) plainly, in the
      same breath, using the actual sub-score that causes it -- never a
      vague "may vary".
    - Never phrase a low-confidence result as if it were certain, and
      never claim real-time AED availability -- this is a simulation
      tool, not live emergency guidance (CLAUDE.md Section 7).
    - If nothing is uncertain, say so plainly rather than manufacturing a
      caveat -- honesty runs both directions.
    - If an AED's location description flags an access barrier
      (trust_score.describe_access_barrier -- e.g. "approach security",
      "ticket required", "call ahead"), state the specific barrier type
      plainly as an added source of uncertainty, but never invent or
      estimate how much time it might add -- that number does not exist
      in the data, so making one up would misrepresent certainty we don't
      have. Say the effective time is uncertain because of the barrier,
      not a fabricated adjusted time.

Caching: one Gemini call per unique (test_lat, test_lon, date, time)
query, cached to backend/cache/explanations/<key>.json keyed on those four
values (rounded) plus every explained AED's id and sub-scores (top plus
each candidate), so a re-ranking caused by a code change can't silently
serve stale narrative text for different numbers. Never called live in a
demo loop -- the frontend hits this cache, not the API, on repeat views of
the same query.

Phase 9 (time slider) note: generate_timeline_explanations groups hours
into segments by the #1 AED's identity only (unchanged by this extension
to 5 positions -- see that function's docstring). Positions #2-#5 can in
principle shuffle within a segment where #1 stays fixed, in which case a
cached segment's comparison text for those positions may not reflect the
exact hour being viewed. This limitation already existed for position #2
before this change; it now applies to up to 4 positions instead of 1. Not
fixed here because re-keying segments on all 5 identities would multiply
the segment (and Gemini-call) count and defeat EXPLANATION_SEGMENT_CAP's
purpose of bounding cost -- the sub-scores shown alongside the narrative
are always freshly computed per hour regardless, only the prose can lag.
"""

import hashlib
import json
import os
from datetime import datetime
from functools import lru_cache
from typing import Dict, List, Optional, Tuple, TypedDict

from dotenv import load_dotenv

load_dotenv()

import google.generativeai as genai

from combined_ranking import ExcludedAed, RankedAed
from distance_ranking import load_aeds
from trust_score import AccessBarrierInfo, describe_access_barrier

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "explanations")
DETAIL_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "aed_detail")
MODEL_NAME = "models/gemini-2.5-flash"

# Phase 9 (time slider): a full-day timeline query has up to 24 hourly
# rankings for one location. Re-running the top/comparisons explanation for
# every hour would burn tokens on narrating a ranking that hasn't actually
# changed -- Sentosa AEDs are almost all 24/7 (CLAUDE.md section 10), so in
# the common case the #1 pick never changes across the day and one call
# should cover all 24 hours. EXPLANATION_SEGMENT_CAP bounds the worst case
# (a location where the #1 pick flips every hour) at a fixed number of real
# Gemini calls per timeline query, regardless of how messy the underlying
# hours data turns out to be. Approved design, 2026-08-08 (see chat: "1 call
# typical, up to 6 worst case"). Still 1 call per segment after extending
# to 5 explained positions -- the cap counts segments/calls, not positions,
# so it does not need to change.
EXPLANATION_SEGMENT_CAP = 6

# How many top-ranked AEDs get narrative text: #1 gets a full explanation,
# #2..#TOP_N_EXPLAIN each get a short comparison line against #1. A ranked
# list shorter than this just produces fewer comparisons, not an error.
TOP_N_EXPLAIN = 5

_model_cache: Optional["genai.GenerativeModel"] = None


@lru_cache(maxsize=1)
def _aed_description_by_id() -> Dict[str, Optional[str]]:
    """
    RankedAed carries sub-scores, not the raw AED_LOCATION_DESCRIPTION text
    -- reuse distance_ranking.load_aeds() (already loads the small Sentosa
    geojson) to look up a specific AED's description so it can be passed
    through trust_score.describe_access_barrier(). Cached for the process
    lifetime; the Sentosa AED list is static within a running backend.
    """
    return {props["AED_ID"]: props.get("AED_LOCATION_DESCRIPTION") for props in load_aeds()}


def _access_barrier_for(aed_id: str) -> AccessBarrierInfo:
    return describe_access_barrier(_aed_description_by_id().get(aed_id))


class PositionExplanation(TypedDict):
    aed_id: str
    building_name: Optional[str]
    explanation: Optional[str]  # None only if Gemini's response omitted this
    # candidate rather than the count mismatching -- never fabricated text


class ExplanationResult(TypedDict):
    top_explanation: str
    comparisons: List[PositionExplanation]  # positions #2..#TOP_N_EXPLAIN, rank order
    source: str  # "gemini" or "cache"


class AedDetailResult(TypedDict):
    aed_id: str
    building_name: Optional[str]
    status: str  # "ranked", "excluded_unreachable", "excluded_closed", "not_found"
    rank: Optional[int]  # 1-indexed position within the ranked list, if status=="ranked"
    total_ranked: int
    sub_scores: Optional[dict]  # the RankedAed's own fields, if status=="ranked"
    access_barrier: Optional[AccessBarrierInfo]
    explanation: Optional[str]
    source: Optional[str]  # "gemini", "cache", or None (excluded/not_found need no call)


# The hand-written patterns from the design review (findings.md), used as
# few-shot guidance so the model matches this tone instead of defaulting to
# generic hedge-everything AI copy. "comparisons" is always a JSON array,
# one string per candidate given, in the same order they were listed --
# empty if no candidates were given at all.
_FEW_SHOT_EXAMPLES = """\
Example 1 -- clean top pick, no candidates given, no uncertainty anywhere:
Input: top_pick=Beach Arrival Plaza (098604-001), walking_time_s=160, distance_confidence=1.0, hours_status=open, hours_confidence=1.0, trust_badge=High, access_barrier=none
Output: {"top_explanation": "Beach Arrival Plaza is the top pick for this location and time \\u2014 about a 3-minute walk (160s) along the actual walking path, currently open on a fully verified hours pattern, and a High trust score (floor, description, and hours all check out). No uncertainty flags on this one.", "comparisons": []}

Example 2 -- one candidate losing purely on walking time:
Input: top_pick=Beach Arrival Plaza (098604-001) walking_time_s=160, candidate_2=Le Meridien (098679-001) walking_time_s=178, both distance_confidence=1.0, hours_confidence=1.0, trust_badge=High, access_barrier=none
Output: {"top_explanation": "Beach Arrival Plaza is the top pick \\u2014 about a 3-minute walk (160s), fully verified hours, and a High trust score. No uncertainty flags on this one.", "comparisons": ["Le Meridien is a close second, about 18 seconds slower to walk to than Beach Arrival Plaza \\u2014 that's the entire gap, since its hours and trust confidence are tied with the top pick."]}

Example 3 -- a candidate losing on distance-confidence (routing/snap-collision uncertainty), not walking time:
Input: top_pick=Le Meridien (098679-002) walking_time_s=247, distance_confidence=1.0 (unique node); candidate_2=098008-003 walking_time_s=246 (1s faster), distance_confidence=0.33 (shares its nearest node with 2 other AEDs); both hours_confidence=1.0, trust_badge=High
Output: {"top_explanation": "Le Meridien is the top pick, about a 4-minute walk (247s) on a uniquely mapped route, with fully verified hours and a High trust score.", "comparisons": ["098008-003 is actually 1 second faster to walk to than Le Meridien, but ranks lower: its nearest walking-path node is shared with two other AEDs, so the routing can't fully distinguish which of the three the walk actually leads to \\u2014 distance-confidence drops to 0.33. Its hours and trust are otherwise fully verified; the gap is specifically a routing-certainty issue, not distance or data quality."]}

Example 4 -- top pick itself carrying uncertainty, stated plainly without burying the recommendation, no candidates given:
Input: top_pick=098008-003 ("31 Beach View", Level 2, First Aid Room), walking_time_s=20.2, distance_confidence=0.33 (shares its nearest node with 2 other AEDs in the same building), hours_status=open, hours_confidence=1.0, trust_badge=High
Output: {"top_explanation": "098008-003 is the top pick \\u2014 a 20-second walk, well ahead of anything else nearby \\u2014 though its walking distance is only approximate: it shares a mapped street-level access point with two other AEDs in the same building, so the routing can't fully distinguish between the three. Hours and location details are otherwise fully verified.", "comparisons": []}

Example 5 -- top pick flagged with an access barrier, one plain caveat clause naming the cause, not a stacked list of every off-nominal number, no candidates given:
Input: top_pick=Casino (098269-010), walking_time_s=90.0, distance_confidence=1.0, hours_status=open, hours_confidence=1.0, trust_badge=Medium (the Medium badge here IS the access-barrier penalty -- don't also call out "Medium trust" as a second, separate concern), access_barrier=requires locating security/staff for access
Output: {"top_explanation": "The Casino AED is the top pick \\u2014 about a 90-second walk with fully verified hours \\u2014 though its description says to approach RWS staff for access, an extra step this ranking can't put a time figure on, so treat the effective time as uncertain rather than the 90-second walk alone.", "comparisons": []}

Example 6 -- a candidate losing partly on an access barrier the top pick doesn't have:
Input: top_pick=Beach Arrival Plaza (098604-001) walking_time_s=160, access_barrier=none; candidate_2=Casino (098269-010) walking_time_s=155 (faster), access_barrier=requires locating security/staff for access; both hours_confidence=1.0, trust_badge=High vs Medium
Output: {"top_explanation": "Beach Arrival Plaza is the top pick \\u2014 about a 3-minute walk (160s), fully verified hours, and a High trust score. No uncertainty flags on this one.", "comparisons": ["The Casino AED is actually 5 seconds faster to walk to than Beach Arrival Plaza, but its description requires locating security staff for access \\u2014 an extra step Beach Arrival Plaza doesn't have and this ranking can't put a time figure on, so it ranks second despite the shorter walk."]}

Example 7 -- four candidates at once (positions #2-#5): each comparison stays independent and against the top pick, never chained against the previous candidate, and each stays to ONE flowing sentence naming its own single most decision-relevant gap -- no cross-referencing between candidates:
Input: top_pick=Beach Arrival Plaza (098604-001) walking_time_s=160, distance_confidence=1.0, hours_confidence=1.0, trust_badge=High, access_barrier=none; candidate_2=Le Meridien walking_time_s=178, distance_confidence=1.0, hours_confidence=1.0, trust_badge=High, access_barrier=none; candidate_3=098008-003 walking_time_s=165, distance_confidence=0.33 (shared node), hours_confidence=1.0, trust_badge=High, access_barrier=none; candidate_4=Waterfront Station walking_time_s=210, distance_confidence=1.0, hours_confidence=0.0 (hours unparseable), trust_badge=Medium, access_barrier=none; candidate_5=Casino walking_time_s=300, distance_confidence=1.0, hours_confidence=1.0, trust_badge=Medium, access_barrier=requires locating security/staff for access
Output: {"top_explanation": "Beach Arrival Plaza is the top pick \\u2014 about a 3-minute walk (160s), fully verified hours, and a High trust score. No uncertainty flags on this one.", "comparisons": [
  "Le Meridien is about 18 seconds slower to walk to than Beach Arrival Plaza \\u2014 the entire gap, since everything else ties.",
  "098008-003 is only 5 seconds slower, but shares its nearest walking-graph node with other AEDs, so its distance is only approximate rather than confidently the shortest.",
  "Waterfront Station is about 50 seconds slower and its operating hours couldn't be parsed at all, so its open/closed status can't be confirmed the way Beach Arrival Plaza's can.",
  "Casino is the slowest of the five (about 2.3 minutes further) and its description requires locating security staff for access, an extra step on top of the longer walk."
]}
"""

_SYSTEM_INSTRUCTION = """\
You write short plain-language explanations for a defibrillator (AED) \
discovery SIMULATION/PREPAREDNESS tool. This is NOT a live emergency app \
-- never phrase anything as guaranteed, real-time, or verified-right-now \
availability. You are given pre-computed sub-scores for a top-ranked AED \
and, if present, up to four more candidates (candidate_2, candidate_3, \
candidate_4, candidate_5, in rank order) for one specific test location, \
date, and time. You do not compute or alter any scores; you only narrate \
the numbers you are given.

Write:
- "top_explanation": 1-2 sentences on the top-ranked AED. State the \
recommendation confidently first (e.g. "X is the top pick"), then, in \
the same breath, name any specific uncertainty using the actual \
sub-score that causes it (shared walking-graph node -> distance_confidence \
< 1.0, low trust_badge, partial/unparseable hours -> hours_confidence \
< 1.0, or access_barrier not "none"). Do not bury the recommendation under \
hedging. If nothing is uncertain, say so plainly instead of inventing a \
caveat.
- "comparisons": a JSON array with exactly one string per candidate given \
(empty array if none were given), in the same order as candidate_2, \
candidate_3, etc. Each string is ONE sentence comparing that candidate to \
the top pick specifically (never to another candidate), naming the \
SPECIFIC sub-score(s) that explain the gap (walking time difference, \
distance_confidence, hours_confidence, trust_badge, or access_barrier) -- \
never a vague "may vary" or generic hedge. Treat each candidate \
independently: candidate_4's sentence should not reference candidate_2 or \
candidate_3, even though all are being compared to the same top pick in \
the same response.

access_barrier is given to you pre-classified as one of: "none", \
"requires locating security/staff for access", "requires ticket/\
registration/verification for access", or "access gated by schedule \
beyond stated operating hours". If it is not "none", state that exact \
fact plainly as an added source of uncertainty -- do NOT invent, \
estimate, or imply any specific extra time cost for it (no "add a few \
minutes", no guessed delay). Say the effective time-to-AED is uncertain \
because of the barrier, not a number. Do not try to infer the barrier \
type yourself from anything else -- use exactly the category you were \
given.

Match the cadence of the examples: a confident lead clause naming the \
pick and its walking time, then ONE flowing "though/but" clause naming \
the single most decision-relevant caveat. Do NOT enumerate every \
off-nominal sub-score as a stacked list of qualifiers (avoid constructions \
like "However, X is only 0.5, it has a Medium trust score, and its \
description indicates Y") -- that reads as generic hedge-everything AI \
copy, not the tone these examples model. If a trust_badge is Medium or \
Needs Verification purely *because* of the access_barrier penalty (i.e. \
there's no other reason given for a lower badge), don't call out the \
badge separately as if it were a second, unrelated concern -- it's the \
same fact stated twice. Only name distance_confidence separately if it \
reflects a real, distinct issue (a shared/collided walking-graph node), \
not merely because the number isn't 1.0 for some other reason.

Never claim real-time AED availability or working condition. Never state \
a low-confidence result as if it were certain. Respond with ONLY a JSON \
object with keys "top_explanation" (string) and "comparisons" (array of \
strings, one per candidate given, possibly empty). No markdown, no extra \
keys, no commentary outside the JSON.

""" + _FEW_SHOT_EXAMPLES

# AED-detail lookup (direct, honest, out-of-top-N): a user can ask "explain
# AED X" for any specific AED regardless of where it actually ranks. Unlike
# generate_explanation (which only ever narrates the top TOP_N_EXPLAIN
# positions), this must be able to say plainly "this AED ranks #11 of 64"
# and explain *why* it isn't higher -- this is the honest way to demo the
# access-barrier feature: 098269-010 never naturally lands in the Sentosa
# top 5 (verified by sweeping all 657 walking-graph nodes as candidate test
# locations -- see the session notes), so showing it via a re-sliced top-5
# would misrepresent it as a plausible top pick. A direct rank-#11 lookup
# doesn't have that problem.
_DETAIL_FEW_SHOT_EXAMPLES = """\
Example -- a real, low-ranked candidate with an access barrier, looked up directly:
Input: reference_top=Hotel Michael (098269-003), walking_time_s=57.3, distance_confidence=1.0, hours_confidence=1.0, trust_badge=High, access_barrier=none; detail_target=Resorts World Casino (098269-010), rank_position=11 of 64, walking_time_s=0.0, distance_confidence=0.5, hours_confidence=1.0, trust_badge=Medium, access_barrier=requires locating security/staff for access
Output: {"detail_explanation": "Resorts World Casino currently ranks #11 of 64 candidates for this location and time \\u2014 despite being a 0-second walk (right at the test point), it loses ground on two fronts: its distance-confidence is only 0.5 because its nearest walking-graph node is shared with other AEDs, and its description requires locating security staff for access, an extra step this ranking can't put a time figure on. Hotel Michael, the top pick, has full distance-confidence and no stated barrier, which is why it ranks well ahead despite a much longer 57-second walk."}

Example -- a low-ranked candidate with no special issues, just genuinely farther and slightly less certain:
Input: reference_top=Beach Arrival Plaza (098604-001), walking_time_s=160, distance_confidence=1.0, hours_confidence=1.0, trust_badge=High, access_barrier=none; detail_target=098973-001 (Flower Terrace Station), rank_position=23 of 58, walking_time_s=310, distance_confidence=1.0, hours_confidence=1.0, trust_badge=Medium, access_barrier=none
Output: {"detail_explanation": "Flower Terrace Station currently ranks #23 of 58 candidates for this location and time -- its walking distance and hours are fully confident, but at about 5 minutes it's considerably slower than Beach Arrival Plaza's 160-second walk, and its Medium trust badge (versus Beach Arrival Plaza's High) reflects a registry-quality gap, e.g. a missing floor level or thinner location description, not an access barrier."}
"""

_DETAIL_SYSTEM_INSTRUCTION = """\
You write a short plain-language explanation for ONE specific AED that a \
user has looked up directly by ID, for a defibrillator (AED) discovery \
SIMULATION/PREPAREDNESS tool. This is NOT a live emergency app -- never \
phrase anything as guaranteed, real-time, or verified-right-now \
availability. Unlike a top-5 ranked list, this AED may be ranked far down \
the list -- your job is to explain plainly where it actually stands and \
why, not to make it sound better or worse than the numbers say.

You are given "reference_top" (the #1-ranked AED for this location/time, \
for comparison) and "detail_target" (the AED being looked up), including \
detail_target's real rank_position out of the total ranked candidates. \
You do not compute or alter any scores; you only narrate the numbers you \
are given.

Write "detail_explanation": 1-3 sentences that:
1. State detail_target's actual rank position plainly and matter-of-factly \
first (e.g. "X currently ranks #11 of 64 candidates for this location and \
time") -- do not bury, soften, or omit this number.
2. Narrate its own walking time, distance_confidence, hours, and \
trust_badge in plain terms.
3. Name the SPECIFIC sub-score(s), compared against reference_top, that \
explain why it isn't ranked higher (walking time difference, \
distance_confidence, hours_confidence, trust_badge, or access_barrier) -- \
never a vague "may vary" or generic hedge. If access_barrier is not \
"none", state that exact fact plainly as part of the explanation -- do \
NOT invent, estimate, or imply any specific extra time cost for it. Say \
the effective time-to-AED is uncertain because of the barrier, not a \
number.

Match the cadence of the examples: matter-of-fact rank statement, then \
plain narration, then ONE flowing clause naming the most decision-relevant \
gap versus the top pick -- not a stacked list of every off-nominal number. \
If a trust_badge is lower purely *because* of the access_barrier penalty, \
don't call out the badge separately as a second, unrelated concern.

Never claim real-time AED availability or working condition. Never state \
a low-confidence result as if it were certain. Respond with ONLY a JSON \
object with key "detail_explanation" (string). No markdown, no extra \
keys, no commentary outside the JSON.

""" + _DETAIL_FEW_SHOT_EXAMPLES

_detail_model_cache: Optional["genai.GenerativeModel"] = None


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


def _get_detail_model() -> "genai.GenerativeModel":
    global _detail_model_cache
    if _detail_model_cache is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Add it to backend/.env (see .env.example)."
            )
        genai.configure(api_key=api_key)
        _detail_model_cache = genai.GenerativeModel(
            MODEL_NAME,
            system_instruction=_DETAIL_SYSTEM_INSTRUCTION,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.4,
            ),
        )
    return _detail_model_cache


def _cache_key(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    top: RankedAed,
    candidates: List[RankedAed],
    top_barrier: AccessBarrierInfo,
    candidate_barriers: List[AccessBarrierInfo],
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
        "top_access_barrier": sorted(top_barrier["types"]),
        "candidates": [
            {
                "aed_id": r["aed_id"],
                "walking_time_s": round(r["walking_time_s"], 1),
                "distance_confidence": r["distance_confidence"],
                "hours_confidence": r["hours_confidence"],
                "trust_badge": r["trust_badge"],
                "access_barrier": sorted(b["types"]),
            }
            for r, b in zip(candidates, candidate_barriers)
        ],
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def _format_aed_input(label: str, r: RankedAed, barrier: AccessBarrierInfo) -> str:
    barrier_str = "; ".join(barrier["reasons"]) if barrier["flagged"] else "none"
    return (
        f"{label}={r['building_name'] or r['aed_id']} ({r['aed_id']}), "
        f"walking_time_s={round(r['walking_time_s'], 1)}, "
        f"distance_confidence={round(r['distance_confidence'], 2)}, "
        f"hours_status={r['hours_status']}, "
        f"hours_confidence={r['hours_confidence']}, "
        f"trust_badge={r['trust_badge']}, "
        f"access_barrier={barrier_str}"
    )


def generate_explanation(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    ranked: List[RankedAed],
) -> ExplanationResult:
    """
    Generate (or fetch cached) explanation text for the top TOP_N_EXPLAIN
    positions of a ranked list already produced by combined_ranking.rank_combined:
    a full explanation for #1, plus one comparison-against-#1 sentence for
    each of #2..#5 that's actually present. One Gemini call per query
    regardless of how many of the 5 positions are present.

    ranked must be non-empty (callers should handle the fully-excluded /
    no-candidates case before calling this -- there is nothing to explain
    if the list is empty).
    """
    if not ranked:
        raise ValueError("generate_explanation requires a non-empty ranked list")

    subset = ranked[:TOP_N_EXPLAIN]
    top = subset[0]
    candidates = subset[1:]

    top_barrier = _access_barrier_for(top["aed_id"])
    candidate_barriers = [_access_barrier_for(r["aed_id"]) for r in candidates]

    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(test_lat, test_lon, test_dt, top, candidates, top_barrier, candidate_barriers)
    path = _cache_path(key)

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        return ExplanationResult(
            top_explanation=cached["top_explanation"],
            comparisons=cached["comparisons"],
            source="cache",
        )

    prompt_lines = [_format_aed_input("top_pick", top, top_barrier)]
    for i, (r, barrier) in enumerate(zip(candidates, candidate_barriers), start=2):
        prompt_lines.append(_format_aed_input(f"candidate_{i}", r, barrier))
    prompt_lines.append(
        f'Produce exactly {len(candidates)} entries in "comparisons", one per '
        "candidate listed above, in the same order (empty array if none were listed)."
    )
    prompt = "\n".join(prompt_lines)

    model = _get_model()
    response = model.generate_content(prompt)
    parsed = json.loads(response.text)

    comparison_texts = parsed.get("comparisons") or []
    comparisons: List[PositionExplanation] = []
    for i, r in enumerate(candidates):
        text = comparison_texts[i] if i < len(comparison_texts) else None
        comparisons.append(PositionExplanation(
            aed_id=r["aed_id"],
            building_name=r["building_name"],
            explanation=text,
        ))

    result = ExplanationResult(
        top_explanation=parsed["top_explanation"],
        comparisons=comparisons,
        source="gemini",
    )

    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


def _detail_cache_key(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    top: RankedAed,
    top_barrier: AccessBarrierInfo,
    target: RankedAed,
    target_barrier: AccessBarrierInfo,
    rank: int,
    total_ranked: int,
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
        "top_access_barrier": sorted(top_barrier["types"]),
        "target_aed_id": target["aed_id"],
        "target_walking_time_s": round(target["walking_time_s"], 1),
        "target_distance_confidence": target["distance_confidence"],
        "target_hours_confidence": target["hours_confidence"],
        "target_trust_badge": target["trust_badge"],
        "target_access_barrier": sorted(target_barrier["types"]),
        "rank": rank,
        "total_ranked": total_ranked,
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _detail_cache_path(key: str) -> str:
    return os.path.join(DETAIL_CACHE_DIR, f"{key}.json")


def generate_aed_detail(
    test_lat: float,
    test_lon: float,
    test_dt: datetime,
    ranked: List[RankedAed],
    excluded: List[ExcludedAed],
    aed_id: str,
) -> AedDetailResult:
    """
    Look up and explain ONE specific AED by id, regardless of where it
    actually ranks -- the honest counterpart to generate_explanation's
    top-TOP_N_EXPLAIN view. Used to demo AEDs (like an access-barrier-
    flagged one) that never naturally land in a top-5 view: this reports
    their real rank_position and generates a real explanation for exactly
    that position, instead of re-slicing them into a top spot they didn't
    earn.

    Three outcomes, all without fabricating anything:
        "ranked"             -- aed_id is in `ranked`; a real rank_position
                                 is reported and a Gemini call explains it
                                 relative to the actual #1.
        "excluded_unreachable" / "excluded_closed"
                              -- aed_id is in `excluded` (Phase 5
                                 segregation); no walking-time-based
                                 ranking exists for it at this test_dt, so
                                 no Gemini call is made -- the reason is a
                                 plain fact already known, not something to
                                 narrate.
        "not_found"           -- aed_id isn't in either list (bad id, or
                                 outside the loaded Sentosa set); no call.
    """
    for i, r in enumerate(ranked):
        if r["aed_id"] == aed_id:
            rank = i + 1
            total_ranked = len(ranked)
            target = r
            target_barrier = _access_barrier_for(aed_id)

            if rank == 1:
                # It IS the top pick -- no separate "reference" to compare
                # against; reuse the ordinary top-explanation path so the
                # narration style matches (no self-comparison needed).
                exp = generate_explanation(test_lat, test_lon, test_dt, ranked)
                return AedDetailResult(
                    aed_id=aed_id,
                    building_name=target["building_name"],
                    status="ranked",
                    rank=1,
                    total_ranked=total_ranked,
                    sub_scores=dict(target),
                    access_barrier=target_barrier,
                    explanation=exp["top_explanation"],
                    source=exp["source"],
                )

            top = ranked[0]
            top_barrier = _access_barrier_for(top["aed_id"])

            os.makedirs(DETAIL_CACHE_DIR, exist_ok=True)
            key = _detail_cache_key(
                test_lat, test_lon, test_dt, top, top_barrier, target, target_barrier, rank, total_ranked
            )
            path = _detail_cache_path(key)

            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    cached = json.load(f)
                explanation = cached["detail_explanation"]
                source = "cache"
            else:
                prompt = "\n".join([
                    _format_aed_input("reference_top", top, top_barrier),
                    _format_aed_input(f"detail_target (rank_position={rank} of {total_ranked})", target, target_barrier),
                ])
                model = _get_detail_model()
                response = model.generate_content(prompt)
                parsed = json.loads(response.text)
                explanation = parsed["detail_explanation"]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"detail_explanation": explanation}, f, indent=2)
                source = "gemini"

            return AedDetailResult(
                aed_id=aed_id,
                building_name=target["building_name"],
                status="ranked",
                rank=rank,
                total_ranked=total_ranked,
                sub_scores=dict(target),
                access_barrier=target_barrier,
                explanation=explanation,
                source=source,
            )

    for e in excluded:
        if e["aed_id"] == aed_id:
            if e["reason"] == "unreachable":
                explanation = (
                    "No path exists between the test location and this AED in the "
                    "walking network, so no walking-time-based ranking can be "
                    "computed for it at this location."
                )
                status = "excluded_unreachable"
            else:
                explanation = (
                    f"This AED's stated operating hours mean it is {e['hours_status']} "
                    "at this test date and time, so it is excluded from the ranked "
                    "list rather than scored as if accessible."
                )
                status = "excluded_closed"
            return AedDetailResult(
                aed_id=aed_id,
                building_name=e["building_name"],
                status=status,
                rank=None,
                total_ranked=len(ranked),
                sub_scores=None,
                access_barrier=_access_barrier_for(aed_id),
                explanation=explanation,
                source=None,
            )

    return AedDetailResult(
        aed_id=aed_id,
        building_name=None,
        status="not_found",
        rank=None,
        total_ranked=len(ranked),
        sub_scores=None,
        access_barrier=None,
        explanation=None,
        source=None,
    )


class HourExplanation(TypedDict):
    top_explanation: Optional[str]
    comparisons: List[PositionExplanation]
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
    it sits next to. Instead capped hours get top_explanation=None,
    comparisons=[], and a generic note (no AED name) explaining why no
    narrative text is shown for this hour; the sub-scores (which are always
    freshly computed per hour, capping only affects Gemini text) stay fully
    accurate and visible regardless.
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
                comparisons=exp["comparisons"] if exp else [],
                source=exp["source"] if exp else ("capped" if capped else None),
                top_aed_id=seg["top_id"],
                segment_start_hour=seg["start"],
                segment_end_hour=seg["end"],
                capped=capped,
                note=_CAPPED_NOTE if capped else None,
            )

    return explanations_by_hour, stats
