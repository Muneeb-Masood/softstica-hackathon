"""
FastAPI backend (Phase 7).

Wraps the already-tested Phase 1-6 modules behind three endpoints. This
file contains no ranking/scoring/explanation logic of its own -- it only
parses request params, calls combined_ranking / explanation / distance_ranking,
and shapes the results into JSON responses.

Startup: the walking graph (osmnx MultiDiGraph, loaded from the cached
sentosa_walk_graph.graphml) and the 66 Sentosa AED records are loaded once
in the FastAPI startup event via distance_ranking.load_walk_graph() /
load_aeds(), which populate module-level caches in distance_ranking.py.
Every request reuses those cached objects rather than reloading from disk.
"""

import time
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from combined_ranking import rank_combined, rank_combined_timeline
from crowd_simulation import run_crowd_simulation
from distance_ranking import load_aeds, load_walk_graph
from explanation import generate_explanation, generate_timeline_explanations
from trust_score import score_aed_properties

DISCLAIMER = (
    "This is a simulation/preparedness tool using historical registry "
    "data. It does not reflect live AED availability, working "
    "condition, or real-time emergency guidance."
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


def _parse_test_dt(date_str: str, time_str: str) -> datetime:
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must be YYYY-MM-DD and time must be HH:MM",
        )


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
            "building_name": props.get("BUILDING_NAME"),
            "latitude": props["LATITUDE"],
            "longitude": props["LONGITUDE"],
            "floor_level": props.get("AED_LOCATION_FLOOR_LEVEL"),
            "description": props.get("AED_LOCATION_DESCRIPTION"),
            "operating_hours": props.get("OPERATING_HOURS"),
            "trust_score": trust["total_score"],
            "trust_badge": trust["badge"],
        })
    return {"count": len(result), "aeds": result}


@app.post("/rank")
def rank(req: RankRequest):
    test_dt = _parse_test_dt(req.date, req.time)

    ranked, excluded = rank_combined(req.lat, req.lon, test_dt)

    explanations = None
    if ranked:
        exp = generate_explanation(req.lat, req.lon, test_dt, ranked)
        explanations = {
            "top_explanation": exp["top_explanation"],
            "runnerup_explanation": exp["runnerup_explanation"],
            "source": exp["source"],
        }

    return {
        "query": {
            "lat": req.lat,
            "lon": req.lon,
            "date": req.date,
            "time": req.time,
        },
        "ranked": ranked,
        "excluded": excluded,
        "explanations": explanations,
        "disclaimer": DISCLAIMER,
    }


class TimelineRequest(BaseModel):
    lat: float
    lon: float
    date: str  # YYYY-MM-DD


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

    hourly = rank_combined_timeline(req.lat, req.lon, test_date)
    explanations_by_hour, stats = generate_timeline_explanations(req.lat, req.lon, hourly)

    hours_payload = []
    for h in sorted(hourly.keys()):
        test_dt, ranked, excluded = hourly[h]
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
