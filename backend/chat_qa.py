"""
Read-only chat/Q&A over an already-computed /rank response (presentation
layer only).

The frontend passes the exact ranked/excluded/explanations payload it
already has from POST /rank or /rank/timeline -- this module never fetches
AEDs, never touches the walking graph, and never calls combined_ranking.py
or trust_score.py. It only turns a natural-language question plus that
payload into one short, grounded answer via a single fresh Gemini call.
Deliberately kept separate from the ranking modules: it cannot invent a
new time estimate, cannot change any AED's score or rank, and must answer
strictly from the sub-scores/explanations it was given -- anything outside
that (live traffic, real-time AED battery/availability, anything not in
the payload) gets an honest "I don't have that information" rather than a
guess. Same live-vs-simulation boundary as CLAUDE.md Section 7.

Optionally, `context` may also carry `timeline`: the frontend's already-
fetched POST /rank/timeline `hours` array (one entry per hour of the
selected day, each with its own ranked/excluded lists) -- this is what
powers the Time Slider. When present, it lets a user who is dragging the
slider ask "why doesn't this AED ever show closed?" or "when does this one
close?" -- questions the single-hour ranked/excluded snapshot alone can't
answer. It is compressed into per-AED open/closed hour ranges (see
`_build_timeline_block`) rather than dumped raw, both to keep the prompt
small and because Sentosa AEDs are mostly 24/7 (see CLAUDE.md Section 10),
so most AEDs collapse to one line. Coverage is intentionally limited to
AEDs already present in the current ranked/excluded lists -- the same
"grounded in what's on screen" boundary as the rest of this module.

No response caching -- each question's phrasing differs, so cache hits
would be rare, and the point of this feature is a live back-and-forth.
Cost is bounded instead by (a) a short-answer instruction to the model and
(b) MAX_MESSAGES_PER_SESSION, an in-memory per-session cap enforced here.
The in-memory dict is process-lifetime only, which is fine for a single-
process demo backend; it is not meant to survive a restart or scale across
workers.
"""

import os
import threading
from typing import Dict, List, Optional, TypedDict

from dotenv import load_dotenv

load_dotenv()

import google.generativeai as genai

MODEL_NAME = "models/gemini-2.5-flash"

# Hard cap on questions per session, enforced regardless of whether Gemini
# calls succeed or error -- this bounds worst-case cost during a demo even
# if a client retries aggressively or a bug causes repeated failing calls.
MAX_MESSAGES_PER_SESSION = 20
MAX_QUESTION_CHARS = 500
MAX_HISTORY_TURNS = 6  # most recent prior messages included for context only

_model_cache: Optional["genai.GenerativeModel"] = None
_lock = threading.Lock()
_session_counts: Dict[str, int] = {}


class ChatMessage(TypedDict):
    role: str  # "user" or "assistant"
    text: str


class ChatResult(TypedDict):
    answer: str
    messages_used: int
    messages_remaining: int


class RateLimitError(Exception):
    """Raised when a session has used its MAX_MESSAGES_PER_SESSION questions."""


_SYSTEM_INSTRUCTION = """\
You answer short questions about an AED (defibrillator) ranking result \
already computed by a SIMULATION/PREPAREDNESS tool -- NOT a live \
emergency app. You will be given, as plain text, the exact ranked list, \
excluded list, and any generated explanations for one specific test \
location/date/time query. You must answer using ONLY the data given to \
you in this prompt -- you are not shown, and must not guess, anything \
else about the real world.

You may also be given an HOURS TIMELINE section: one line per AED showing \
its open/closed/unknown status across every hour of the selected day, as \
compressed hour ranges (e.g. "00:00-23:00 open" means open the entire day; \
several ranges means the status changes at those hour boundaries). This \
comes from the Time Slider feature and is the ONLY source you may use for \
questions about when an AED opens or closes, or why its status does or \
does not change through the day -- e.g. "why doesn't it show closed?" is \
answered by pointing out its timeline is a single open range (or, if it IS \
present, by naming the hour it switches). If no HOURS TIMELINE section is \
given, or the AED asked about isn't listed in it, say you don't have \
hourly timeline data for that AED/query and suggest opening the Time \
Slider for that location to see it.

Rules:
- Never invent, estimate, or guess any number, time, distance, or fact \
that is not present in the data you were given. If asked about something \
outside the given data -- e.g. real-time traffic, live AED battery or \
working condition, current/live availability, anything not present in \
the payload -- reply that you don't have that information in the current \
ranking data, and point to what you do have instead if it's relevant.
- You cannot change, recompute, or re-rank anything. If asked to "rerank", \
"recalculate", or "what if I were somewhere else", explain that you can \
only explain the ranking already shown, not produce a new one -- the user \
would need to re-run the query for a new location/time.
- Never state a low-confidence value (trust badge below High, hours \
confidence or distance confidence below 1.0) as if it were certain -- \
carry the same uncertainty language the underlying data implies.
- Never claim real-time AED availability or working condition -- the HOURS \
TIMELINE is parsed historical registry data for the selected day, not a \
live feed.
- Keep answers to 2-4 sentences, plain language, no markdown formatting, \
no bullet lists, no JSON -- just the answer text itself.
"""


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
            generation_config=genai.GenerationConfig(temperature=0.3),
        )
    return _model_cache


def _check_and_increment(session_id: str) -> int:
    with _lock:
        count = _session_counts.get(session_id, 0)
        if count >= MAX_MESSAGES_PER_SESSION:
            raise RateLimitError(
                f"This session has reached its {MAX_MESSAGES_PER_SESSION}-question "
                "limit for the chat feature (a demo cost safeguard). Reload or start "
                "a new query to reset it."
            )
        _session_counts[session_id] = count + 1
        return _session_counts[session_id]


def _fmt(label: str, value) -> Optional[str]:
    return f"{label}={value}" if value is not None else None


def _format_ranked_line(aed: dict, rank: int) -> str:
    parts = [
        f"#{rank} {aed.get('building_name') or aed.get('aed_id')} ({aed.get('aed_id')})",
        _fmt("walking_time_s", round(aed["walking_time_s"], 1)) if aed.get("walking_time_s") is not None else None,
        _fmt("distance_confidence", aed.get("distance_confidence")),
        _fmt("hours_status", aed.get("hours_status")),
        _fmt("hours_confidence", aed.get("hours_confidence")),
        _fmt("trust_badge", aed.get("trust_badge")),
        _fmt("final_score", round(aed["final_score"], 3)) if aed.get("final_score") is not None else None,
    ]
    if aed.get("uses_stairs"):
        parts.append("uses_stairs=true")
    if aed.get("crosses_unmarked_road"):
        parts.append("crosses_unmarked_road=true")
    if aed.get("uses_permissive_access"):
        parts.append("uses_permissive_access=true")
    return ", ".join(p for p in parts if p)


def _format_excluded_line(aed: dict) -> str:
    parts = [
        f"{aed.get('building_name') or aed.get('aed_id')} ({aed.get('aed_id')})",
        _fmt("excluded_reason", aed.get("reason")),
        _fmt("hours_status", aed.get("hours_status")),
        _fmt("walking_time_s", round(aed["walking_time_s"], 1)) if aed.get("walking_time_s") is not None else None,
        _fmt("mobility_note", aed.get("mobility_note")),
    ]
    return ", ".join(p for p in parts if p)


def _compress_hour_ranges(hour_status: List[tuple]) -> str:
    """
    hour_status: [(hour_int, status_str), ...] sorted by hour, one entry per
    hour 0-23 (gaps allowed). Collapses consecutive equal-status hours into
    "HH:00-HH:00 status" ranges so a 24/7 AED renders as one segment instead
    of 24 near-duplicate lines.
    """
    if not hour_status:
        return "no timeline data"
    segments = []
    start_hour, cur_status = hour_status[0]
    prev_hour = start_hour
    for hour, status in hour_status[1:]:
        if status == cur_status and hour == prev_hour + 1:
            prev_hour = hour
            continue
        segments.append((start_hour, prev_hour, cur_status))
        start_hour, cur_status, prev_hour = hour, status, hour
    segments.append((start_hour, prev_hour, cur_status))

    parts = []
    for start, end, status in segments:
        if start == end:
            parts.append(f"{start:02d}:00 {status}")
        else:
            parts.append(f"{start:02d}:00-{end:02d}:00 {status}")
    return ", ".join(parts)


def _build_timeline_block(timeline_hours: List[dict], known_aed_ids: set) -> Optional[str]:
    """
    Compress the frontend's already-fetched /rank/timeline `hours` array
    into one open/closed/unknown line per AED, covering the full day. Only
    covers `known_aed_ids` (the AEDs already visible in the current ranked/
    excluded lists) -- see module docstring for why coverage is scoped that
    way, and `_compress_hour_ranges` for why it's ranges, not 24 raw rows.
    """
    if not timeline_hours or not known_aed_ids:
        return None

    by_aed: Dict[str, list] = {aed_id: [] for aed_id in known_aed_ids}
    names: Dict[str, str] = {}
    for entry in timeline_hours:
        hour = entry.get("hour")
        if hour is None:
            continue
        seen_this_hour = set()
        for aed in (entry.get("ranked") or []) + (entry.get("excluded") or []):
            aed_id = aed.get("aed_id")
            if aed_id not in by_aed or aed_id in seen_this_hour:
                continue
            seen_this_hour.add(aed_id)
            status = aed.get("hours_status") or ("unreachable" if aed.get("reason") == "unreachable" else "unknown")
            by_aed[aed_id].append((hour, status))
            if aed.get("building_name"):
                names[aed_id] = aed["building_name"]

    lines = []
    for aed_id, hour_status in by_aed.items():
        if not hour_status:
            continue
        hour_status.sort(key=lambda hs: hs[0])
        label = names.get(aed_id) or aed_id
        lines.append(f"{label} ({aed_id}): {_compress_hour_ranges(hour_status)}")

    return "\n".join(lines) if lines else None


def _build_context_block(context: dict) -> str:
    """
    Render the frontend's already-computed ranking payload (query, ranked,
    excluded, explanations) as plain text for the prompt. Does no math and
    reads no field not already present on the payload -- this function's
    only job is formatting, so a change here can never alter what the
    model is told.
    """
    query = context.get("query") or {}
    ranked = context.get("ranked") or []
    excluded = context.get("excluded") or []
    explanations = context.get("explanations") or {}

    lines = [
        "QUERY: "
        f"lat={query.get('lat')}, lon={query.get('lon')}, date={query.get('date')}, "
        f"time={query.get('time')}, mobility={query.get('mobility')}",
        "",
        f"RANKED LIST ({len(ranked)} AEDs, best first, exact order the user sees):",
    ]
    for i, aed in enumerate(ranked, start=1):
        lines.append(_format_ranked_line(aed, i))

    if explanations:
        lines.append("")
        lines.append("ALREADY-GENERATED EXPLANATIONS (reuse this reasoning, do not contradict it):")
        if explanations.get("top_explanation"):
            lines.append(f"top_pick_explanation: {explanations['top_explanation']}")
        for c in explanations.get("comparisons") or []:
            if c.get("explanation"):
                lines.append(f"comparison ({c.get('building_name') or c.get('aed_id')}): {c['explanation']}")

    lines.append("")
    lines.append(f"EXCLUDED ({len(excluded)} AEDs, closed or unreachable at this test time -- not ranked at all):")
    if not excluded:
        lines.append("(none)")
    for aed in excluded:
        lines.append(_format_excluded_line(aed))

    timeline_hours = context.get("timeline")
    if timeline_hours:
        known_aed_ids = {aed.get("aed_id") for aed in ranked + excluded if aed.get("aed_id")}
        timeline_block = _build_timeline_block(timeline_hours, known_aed_ids)
        if timeline_block:
            lines.append("")
            lines.append(
                "HOURS TIMELINE FOR TODAY (from the Time Slider, one line per AED "
                "listed above, showing its open/closed/unknown status across the "
                "full day as compressed hour ranges -- use this for any question "
                "about when an AED opens, closes, or why it stays open/closed "
                "across the day; it is NOT live status, just the same historical "
                "registry hours already parsed for the query time above):"
            )
            lines.append(timeline_block)

    return "\n".join(lines)


def answer_question(
    session_id: str,
    question: str,
    context: dict,
    history: Optional[List[ChatMessage]] = None,
) -> ChatResult:
    """
    Answer one question grounded strictly in `context` (the frontend's
    already-computed /rank or /rank/timeline payload for the ranking
    currently on screen). Raises RateLimitError once the session has used
    MAX_MESSAGES_PER_SESSION questions, ValueError on an empty question.
    Does no ranking math and keeps no per-question cache -- see module
    docstring.
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("question must not be empty")
    question = question[:MAX_QUESTION_CHARS]

    used = _check_and_increment(session_id)

    prompt_parts = [_build_context_block(context), ""]
    if history:
        prompt_parts.append("PRIOR CONVERSATION (for context only -- do not re-answer these):")
        for m in history[-MAX_HISTORY_TURNS:]:
            prompt_parts.append(f"{m['role']}: {m['text']}")
        prompt_parts.append("")
    prompt_parts.append(f"USER QUESTION: {question}")

    model = _get_model()
    response = model.generate_content("\n".join(prompt_parts))
    answer = (response.text or "").strip()

    return ChatResult(
        answer=answer,
        messages_used=used,
        messages_remaining=max(0, MAX_MESSAGES_PER_SESSION - used),
    )
