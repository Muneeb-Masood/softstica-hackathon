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

from combined_ranking import rank_combined
from distance_ranking import load_aeds, load_walk_graph
from explanation import generate_explanation
from trust_score import score_aed_properties

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
        "disclaimer": (
            "This is a simulation/preparedness tool using historical registry "
            "data. It does not reflect live AED availability, working "
            "condition, or real-time emergency guidance."
        ),
    }
