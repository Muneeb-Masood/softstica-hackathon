"""
FastAPI backend (Phase 7).

Wraps the already-tested Phase 1-6 modules behind three endpoints. This
file contains no ranking/scoring/explanation logic of its own -- it only
parses request params, calls combined_ranking / explanation / distance_ranking,
and shapes the results into JSON responses.

Startup: the walking graph (osmnx MultiDiGraph, loaded from the cached
sentosa_walk_graph_augmented.graphml -- the base OSM walk network plus
entrance_augmentation.py's synthetic building-entrance nodes) and the 66
Sentosa AED records are loaded once
in the FastAPI startup event via distance_ranking.load_walk_graph() /
load_aeds(), which populate module-level caches in distance_ranking.py.
Every request reuses those cached objects rather than reloading from disk.
"""

import time
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from chat_qa import MAX_MESSAGES_PER_SESSION, RateLimitError, answer_question
from combined_ranking import display_name, rank_combined, rank_combined_timeline
from crowd_simulation import run_crowd_simulation
from distance_ranking import (
    MAX_PACE_M_PER_S,
    MIN_PACE_M_PER_S,
    WALKING_SPEED_M_PER_S,
    get_route_geometry,
    load_aeds,
    load_walk_graph,
)
from explanation import (
    generate_aed_detail,
    generate_explanation,
    generate_timeline_explanations,
    generate_trust_explanation,
)
from trust_score import explain_trust_score, score_aed_properties

DISCLAIMER = (
    "Prototype for planning and simulation only—not for emergency use. "
    "In an emergency in Singapore, call 995 immediately and follow SCDF "
    "instructions. This tool uses historical registry data and does not "
    "reflect live AED availability, working condition, or real-time "
    "emergency guidance."
)

app = FastAPI(title="AED Discovery & Routing (Sentosa Prototype)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_startup_info = {"loaded": False, "startup_time_s": None}


@app.on_event("startup")
def load_data_and_graph() -> None:
    """Load the AED list and walking graph once at startup, not per-request."""
    start = time.perf_counter()
    load_aeds()
    load_walk_graph()
    _startup_info["startup_time_s"] = round(time.perf_counter() - start, 3)
    _startup_info["loaded"] = True


class RankRequest(BaseModel):
    lat: float
    lon: float
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    mobility: Literal["walk", "wheelchair"] = "walk"
    pace_m_per_s: float = Field(default=WALKING_SPEED_M_PER_S, ge=MIN_PACE_M_PER_S, le=MAX_PACE_M_PER_S)


def _parse_test_dt(date_str: str, time_str: str) -> datetime:
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must be YYYY-MM-DD and time must be HH:MM",
        )


def _mobility_warning(item: dict) -> Optional[str]:
    """
    Deterministic, always-shown route-quality warning -- NOT dependent on
    the Gemini explanation call, so it can never be missed or paraphrased
    away. Built directly from distance_ranking's OSM-tag-derived flags
    (uses_stairs, crosses_unmarked_road, uses_permissive_access,
    uses_synthetic_entrance; see that module's docstring). Every AED whose
    route has one of these flags set must surface this note wherever it's
    shown to a user.
    """
    notes = []
    if item.get("uses_stairs"):
        notes.append(
            "This route includes stairs. If you are using a wheelchair or "
            "cannot use stairs, do not use this route -- switch the mobility "
            "setting to \"Wheelchair\" for a stairs-free route, if one exists."
        )
    if item.get("crosses_unmarked_road"):
        notes.append(
            "This route crosses a vehicle road with no marked pedestrian "
            "crossing nearby in the map data -- cross with caution."
        )
    if item.get("uses_permissive_access"):
        notes.append(
            "This route relies on a path tagged as permissive access "
            "(tolerated, not a guaranteed public right-of-way), not a public street."
        )
    if item.get("uses_synthetic_entrance"):
        notes.append(
            "The final stretch of this route uses an estimated entrance point "
            "around the building's outline, not a surveyed doorway -- the "
            "real entrance may be a short walk from where this route ends."
        )
    return " ".join(notes) if notes else None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "data_loaded": _startup_info["loaded"],
        "startup_time_s": _startup_info["startup_time_s"],
    }


@app.get("/aeds")
def get_aeds():
    """All 66 Sentosa AEDs with trust badges, for map display."""
    aeds = load_aeds()
    result = []
    for props in aeds:
        trust = score_aed_properties(props)
        result.append({
            "aed_id": props["AED_ID"],
            "building_name": display_name(props),
            "latitude": props["LATITUDE"],
            "longitude": props["LONGITUDE"],
            "floor_level": props.get("AED_LOCATION_FLOOR_LEVEL"),
            "description": props.get("AED_LOCATION_DESCRIPTION"),
            "operating_hours": props.get("OPERATING_HOURS"),
            "trust_score": trust["total_score"],
            "trust_badge": trust["badge"],
            "trust_badge_reasons": explain_trust_score(props, trust),
        })
    return {"count": len(result), "aeds": result}


@app.get("/aeds/needs-verification")
def get_needs_verification():
    """
    Phase 11 (bonus output): AEDs whose Phase 3 trust score is below High,
    with plain-language reasons pulled straight from the sub-scores. This is
    a registry-quality view, not a ranking -- it exists so a survey team
    knows which records to re-check, independent of any particular test
    location/date/time.

    Two tiers, kept separate rather than merged into one list:
      - `items` (badge == "Needs Verification"): has a -1 sub-score, i.e. a
        genuinely unfindable description or unparseable hours -- a real
        accessibility risk per the Phase 3 badge design (see
        trust_score.py). This is the urgent list.
      - `medium_items` (badge == "Medium"): no -1, just short of a perfect
        score (e.g. floor level missing). Lower priority, but still
        surfaced with reasons since the brief's "needing re-verification"
        wording is broader than the "Needs Verification" badge name alone.
    """
    aeds = load_aeds()

    def _to_item(props: dict, trust) -> dict:
        return {
            "aed_id": props["AED_ID"],
            "building_name": display_name(props),
            "latitude": props["LATITUDE"],
            "longitude": props["LONGITUDE"],
            "floor_level": props.get("AED_LOCATION_FLOOR_LEVEL"),
            "description": props.get("AED_LOCATION_DESCRIPTION"),
            "operating_hours": props.get("OPERATING_HOURS"),
            "trust_score": trust["total_score"],
            "trust_badge": trust["badge"],
            "reasons": explain_trust_score(props, trust),
        }

    items = []
    medium_items = []
    badge_counts = {"High": 0, "Medium": 0, "Needs Verification": 0}
    for props in aeds:
        trust = score_aed_properties(props)
        badge_counts[trust["badge"]] = badge_counts.get(trust["badge"], 0) + 1
        if trust["badge"] == "Needs Verification":
            items.append(_to_item(props, trust))
        elif trust["badge"] == "Medium":
            medium_items.append(_to_item(props, trust))

    items.sort(key=lambda item: (item["trust_score"], item["aed_id"]))
    medium_items.sort(key=lambda item: (item["trust_score"], item["aed_id"]))

    return {
        "total_aeds": len(aeds),
        "badge_counts": badge_counts,
        "needs_verification_count": len(items),
        "medium_count": len(medium_items),
        "items": items,
        "medium_items": medium_items,
    }


@app.get("/aeds/{aed_id}/trust-explanation")
def aed_trust_explanation(aed_id: str):
    """
    Personalized "Why is this AED flagged {badge}?" text for the Phase 11
    registry-quality list -- grounded in this specific record's own
    description/hours wording, unlike explain_trust_score()'s generic
    per-component template strings (which read identically for any two
    records dinged for the same reason). Not location/time-scoped: the
    trust badge is a fixed registry-quality property (see trust_score.py),
    so this call is cached forever per AED_ID's current field values.
    """
    aeds = load_aeds()
    props = next((p for p in aeds if p["AED_ID"] == aed_id), None)
    if props is None:
        raise HTTPException(status_code=404, detail=f"AED_ID {aed_id!r} not found in the Sentosa dataset.")

    result = generate_trust_explanation(aed_id, props)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"AED_ID {aed_id!r} has a High trust badge -- nothing to explain.",
        )

    return {"aed_id": aed_id, "trust_explanation": result["trust_explanation"], "source": result["source"]}


@app.post("/rank")
def rank(req: RankRequest):
    test_dt = _parse_test_dt(req.date, req.time)

    ranked, excluded = rank_combined(
        req.lat, req.lon, test_dt, mobility_profile=req.mobility, pace_m_per_s=req.pace_m_per_s
    )
    for item in ranked:
        item["mobility_warning"] = _mobility_warning(item)

    explanations = None
    if ranked:
        exp = generate_explanation(req.lat, req.lon, test_dt, ranked)
        explanations = {
            "top_explanation": exp["top_explanation"],
            "comparisons": exp["comparisons"],
            "source": exp["source"],
        }

    return {
        "query": {
            "lat": req.lat,
            "lon": req.lon,
            "date": req.date,
            "time": req.time,
            "mobility": req.mobility,
            "pace_m_per_s": req.pace_m_per_s,
        },
        "ranked": ranked,
        "excluded": excluded,
        "explanations": explanations,
        "disclaimer": DISCLAIMER,
    }


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class ChatRequest(BaseModel):
    session_id: str
    question: str
    # The frontend's already-computed ranking payload for whatever is
    # currently on screen -- {query, ranked, excluded, explanations}, i.e.
    # exactly the shape returned by /rank (or one hour's slice of
    # /rank/timeline). This endpoint does no ranking of its own; see
    # chat_qa.py.
    context: dict
    history: List[ChatMessageIn] = []


@app.post("/chat")
def chat(req: ChatRequest):
    """
    Read-only Q&A over an already-computed ranking (chat_qa.py). Never
    reranks or recomputes -- answers are grounded strictly in `req.context`,
    the exact payload the frontend already has on screen. Rate-limited per
    session (chat_qa.MAX_MESSAGES_PER_SESSION) as a demo cost safeguard.
    """
    if not req.session_id:
        raise HTTPException(status_code=422, detail="session_id is required")
    try:
        result = answer_question(
            session_id=req.session_id,
            question=req.question,
            context=req.context,
            history=[{"role": m.role, "text": m.text} for m in req.history],
        )
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "answer": result["answer"],
        "messages_used": result["messages_used"],
        "messages_remaining": result["messages_remaining"],
        "max_messages": MAX_MESSAGES_PER_SESSION,
        "disclaimer": DISCLAIMER,
    }


@app.get("/aed/{aed_id}/detail")
def aed_detail(
    aed_id: str,
    lat: float = Query(...),
    lon: float = Query(...),
    date: str = Query(...),
    time: str = Query(...),
    mobility: Literal["walk", "wheelchair"] = Query("walk"),
    pace_m_per_s: float = Query(WALKING_SPEED_M_PER_S, ge=MIN_PACE_M_PER_S, le=MAX_PACE_M_PER_S),
):
    """
    Direct, honest lookup for ONE specific AED at a test location/date/time,
    regardless of where it actually ranks. Reports its real rank position
    (e.g. "#11 of 64"), its real sub-scores, and a real explanation of why
    it ranks where it does -- as opposed to /rank's top-5 view, which won't
    show an AED at all if it doesn't naturally place in the top 5.

    This is the honest way to demo an AED that never lands in a top-5 view
    for any Sentosa test location (e.g. the one access-barrier-flagged AED,
    098269-010, whose best rank across all 657 walking-graph nodes swept as
    test locations is #11 of 64) -- rather than re-slicing it into a top
    spot it hasn't earned.
    """
    test_dt = _parse_test_dt(date, time)
    ranked, excluded = rank_combined(lat, lon, test_dt, mobility_profile=mobility, pace_m_per_s=pace_m_per_s)
    detail = generate_aed_detail(lat, lon, test_dt, ranked, excluded, aed_id)
    if detail.get("sub_scores"):
        detail["mobility_warning"] = _mobility_warning(detail["sub_scores"])

    if detail["status"] == "not_found":
        raise HTTPException(status_code=404, detail=f"AED_ID {aed_id!r} not found in the Sentosa dataset.")

    return {
        "query": {"lat": lat, "lon": lon, "date": date, "time": time, "mobility": mobility, "pace_m_per_s": pace_m_per_s},
        "detail": detail,
        "disclaimer": DISCLAIMER,
    }


@app.get("/route")
def route(
    aed_id: str = Query(...),
    lat: float = Query(...),
    lon: float = Query(...),
    mobility: Literal["walk", "wheelchair"] = Query("walk"),
    pace_m_per_s: float = Query(WALKING_SPEED_M_PER_S, ge=MIN_PACE_M_PER_S, le=MAX_PACE_M_PER_S),
):
    """
    Map-drawing support: the real walking-network route from a test point to
    one AED, plus the straight-line baseline between the same two points, so
    the frontend can draw both on the map for a direct visual "baseline vs.
    actual walking route" comparison -- not scored or ranked here, this is
    the same route rank_combined already scored, just returned as drawable
    coordinates. Never a live turn-by-turn route -- see DISCLAIMER.
    """
    result = get_route_geometry(lat, lon, aed_id, mobility_profile=mobility, pace_m_per_s=pace_m_per_s)
    if result is None:
        raise HTTPException(status_code=404, detail=f"AED_ID {aed_id!r} not found in the Sentosa dataset.")

    result["mobility_warning"] = _mobility_warning(result)
    return {
        "query": {"lat": lat, "lon": lon, "aed_id": aed_id, "mobility": mobility, "pace_m_per_s": pace_m_per_s},
        "route": result,
        "disclaimer": DISCLAIMER,
    }


class TimelineRequest(BaseModel):
    lat: float
    lon: float
    date: str  # YYYY-MM-DD
    mobility: Literal["walk", "wheelchair"] = "walk"
    pace_m_per_s: float = Field(default=WALKING_SPEED_M_PER_S, ge=MIN_PACE_M_PER_S, le=MAX_PACE_M_PER_S)


def _parse_test_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")


@app.post("/rank/timeline")
def rank_timeline(req: TimelineRequest):
    """
    Phase 9 (time slider): rank_combined for every hour of one day at a
    fixed test location, precomputed in one call so the frontend slider can
    swap between hours instantly with no per-drag network round trip.

    Explanations are NOT regenerated for every hour -- see
    explanation.generate_timeline_explanations: one Gemini call per segment
    where the #1 AED actually changes, capped at EXPLANATION_SEGMENT_CAP
    real calls per query. `stats` reports exactly how many Gemini calls
    vs. cache hits this query made, for transparency on token cost.
    """
    test_date = _parse_test_date(req.date)

    hourly = rank_combined_timeline(
        req.lat, req.lon, test_date, mobility_profile=req.mobility, pace_m_per_s=req.pace_m_per_s
    )
    explanations_by_hour, stats = generate_timeline_explanations(req.lat, req.lon, hourly)

    hours_payload = []
    for h in sorted(hourly.keys()):
        test_dt, ranked, excluded = hourly[h]
        for item in ranked:
            item["mobility_warning"] = _mobility_warning(item)
        hours_payload.append({
            "hour": h,
            "time": test_dt.strftime("%H:%M"),
            "ranked": ranked,
            "excluded": excluded,
            "explanation": explanations_by_hour[h],
        })

    return {
        "query": {
            "lat": req.lat,
            "lon": req.lon,
            "date": req.date,
            "mobility": req.mobility,
            "pace_m_per_s": req.pace_m_per_s,
        },
        "hours": hours_payload,
        "stats": stats,
        "disclaimer": DISCLAIMER,
    }


class CrowdSimulationRequest(BaseModel):
    building_name: str = "Universal Studios Singapore"
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    n_per_side: int = 8  # grid resolution, clamped to [2, 16] in crowd_simulation.py


@app.post("/crowd-simulation")
def crowd_simulation(req: CrowdSimulationRequest):
    """
    Phase 10 (Novelty 2): sweep a grid of SIMULATED starting points across
    one attraction's footprint and tally which AED comes out #1 most often.
    Not real footfall data -- see crowd_simulation.py and the disclaimer
    below, which must stay attached to every result shown in the UI.
    """
    test_dt = _parse_test_dt(req.date, req.time)
    result = run_crowd_simulation(req.building_name, test_dt, n_per_side=req.n_per_side)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No AEDs found matching building_name={req.building_name!r}",
        )
    result["disclaimer"] = (
        "Crowd simulation uses SIMULATED starting points on a grid, not real "
        "footfall, queue, or incident data. " + DISCLAIMER
    )
    return result
