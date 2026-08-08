# AED Discovery & Routing — Build Plan for Claude Code

This document is written to be handed directly to Claude Code as the working brief for this project. It covers the challenge requirements, the data, the tech stack, and the exact build sequence to follow.

---

## 1. Project Summary

We are building a prototype for "Lane 1 — Discovery and Routing" of an AED (Automated External Defibrillator) hackathon challenge. The tool is a simulation/preparedness tool, not a live emergency app. Given a test location, date, and time, it ranks nearby AEDs by how good a real choice each one would be, explains its reasoning, and is explicit about uncertainty rather than guessing.

**This is not a live rescue tool.** It must never present its output as verified, real-time emergency guidance. Every screen must make clear this is a simulation.

---

## 2. The Official Brief (verbatim)

"Create a simulation or preparedness tool that ranks candidate AEDs for a specified test location, date, and time. The solution may account for walking-network distance, stated operating hours, entrances, floor information, or ambiguity in free-text location descriptions. Strong solutions could use natural-language processing to normalize operating hours and location descriptions, graph optimization to compare routes, or a learning-to-rank method when suitable labeled data are supplied. They should identify why an AED was ranked, show uncertainty, and safely abstain when accessibility cannot be established. A basic pin map or straight-line nearest-neighbour lookup is the required baseline, not a complete submission. The prototype must never present a simulated route or parsed operating status as live emergency guidance or verified availability."

Official data source: Public Access AEDs, SCDF, via data.gov.sg

---

## 3. The Data

- File: `AED_LOCATIONS.geojson`
- Format: GeoJSON FeatureCollection
- Size: approximately 7MB, approximately 217,300 lines, approximately 9,000-10,000 AED records nationwide (Singapore)

**Fields per AED record:**
- `AED_ID` — unique identifier
- `LATITUDE`, `LONGITUDE` — coordinates
- `BUILDING_NAME`, `HOUSE_NUMBER`, `ROAD_NAME`, `POSTAL_CODE` — address
- `AED_LOCATION_FLOOR_LEVEL` — floor level, sometimes blank
- `AED_LOCATION_DESCRIPTION` — free text describing exact placement inside the building
- `OPERATING_HOURS` — free text, mostly clean in the Sentosa sample seen so far ("Mon - Sun 00:00-23:59"), expected to be messier elsewhere in the full dataset

**Example records (Sentosa area):**

| AED_ID | Building | Floor | Description | Hours |
|---|---|---|---|---|
| 098136-004 | SEA Aquarium | B1M | Open Ocean Habitat (Highest Tier Of Viewing Gallery) | Mon-Sun 00:00-23:59 |
| 098138-005 | Resorts World Sentosa | B2 | Car Park Opposite Clinic (Pillar Beside Escalator) | Mon-Sun 00:00-23:59 |
| 098139-002 | Hard Rock Hotel | 2 | Pool Towel Kiosk | Mon-Sun 00:00-23:59 |
| 098140-001 | Universal Studios Singapore | 1 | Beside That's A Wrap Retail Shop (Inside Lockers Area) | Mon-Sun 00:00-23:59 |

**NOT in the dataset (must not be assumed or faked as real):**
- Live AED availability or battery status
- Real footfall or population data
- Real emergency incident data
- Any patient information

Anything using these concepts must be clearly simulated, and labeled as simulated in the UI and writeup.

**Demo scope:** Filter the full national file down to a bounding box around Sentosa island for the prototype, rather than working with the full ~9,000-record national dataset. Sentosa is self-contained, has a mix of attractions, and gives a good variety of buildings for the demo.

Approximate Sentosa bounding box: longitude 103.815 to 103.825, latitude 1.245 to 1.260 (verify visually once filtered, adjust if it misses known Sentosa AEDs or includes unrelated ones).

---

## 4. Tech Stack

**Backend: Python + FastAPI**
- `geopandas` / `pandas` — load and filter the AED GeoJSON
- `osmnx` — download and build the Sentosa walking-network graph from OpenStreetMap
- `networkx` — shortest-path / walking-time computation on the graph (osmnx depends on this)
- Plain Python (regex/string rules) — parse `OPERATING_HOURS` into open/closed-by-time logic
- Gemini API — used narrowly, only for (a) scoring how vague/clear a location description is, and (b) turning computed scores into a plain-language explanation sentence. Not used for core ranking math.
- No database needed. Load the filtered Sentosa GeoJSON into memory at startup. Precompute what can be precomputed (walking graph, trust scores) and cache to local files so the backend does not redo expensive work on every request or every demo run.

**Frontend: React**
- `react-leaflet` (Leaflet + OpenStreetMap tiles) — map rendering, free, no API key required
- Plain React components for the ranked list, explanation panel, time slider, and trust-score badges
- No charting library needed for this scope

**Hosting**
- Frontend: Vercel or Netlify
- Backend: Render or Railway free tier, or run locally for the live demo if that is more reliable

---

## 5. Required Deliverables (must all exist by the end)

- [ ] Working prototype
- [ ] Problem and user definition (who this is for, what decision it supports)
- [ ] Method card (what techniques were used and why)
- [ ] Baseline (straight-line nearest neighbor) plus evaluation against it
- [ ] Reproducibility package (filtered dataset, bounding box used, setup steps)
- [ ] Safety and privacy statement

**Metrics to report:**
- One effectiveness metric
- One safety/error metric
- One performance/usability metric
- Tail metrics (90th/95th percentile) alongside averages, not just averages

---

## 6. Novelty Features (why each one exists)

These are the features that take the project past the required baseline. Each maps directly to a specific line in the brief, they are not decoration.

1. **Time slider** — ranking changes as the viewer moves through hours of the day. Directly demonstrates the brief's requirement to account for operating hours and to show why a ranking depends on "test location, date, and time."

2. **Crowd simulation** — simulate many starting points across a busy attraction (e.g. Universal Studios Singapore) and tally which AED comes out on top most often, revealing bottlenecks a single-person ranking would not show. Simulated data only, must be labeled as such.

3. **Data trust score** — rule-based score per AED (floor present, description has a concrete landmark, hours field is clean or messy) shown as a High/Medium/Needs Verification badge. Directly demonstrates the brief's requirement to "show uncertainty" and "safely abstain when accessibility cannot be established." Also produces a bonus output: a list of AEDs that should be re-surveyed.

4. **Runner-up reasoning** — alongside the top-ranked AED, show one short line explaining why the second-ranked AED did not win (e.g. closer but lower hours confidence). Reuses scores already computed, no new logic, but strongly demonstrates "identify why an AED was ranked."

---

## 7. Safety Framing (mandatory, not optional)

- Every screen must read as a simulation/planning tool, never as live emergency guidance
- Every operating-status result is shown as a confidence estimate, never a guarantee
- A visible disclaimer stays on screen at all times
- Never claim real-time AED availability; the data is historical registry data only

---

## 8. Build Sequence (follow in this order)

Each phase should be working and testable before moving to the next. Do not start the frontend before Phase 3 is working from the command line.

### Phase 0 — Project setup
- Create project structure: `/backend` (FastAPI) and `/frontend` (React)
- Set up Python virtual environment, install `geopandas`, `osmnx`, `networkx`, `fastapi`, `uvicorn`
- Set up React app with `react-leaflet` installed

### Phase 1 — Data filtering
- Load the full `AED_LOCATIONS.geojson`
- Filter to the Sentosa bounding box
- Save the filtered subset as `sentosa_aeds.geojson`
- Print/verify the record count and spot-check a few entries look correct
- This filtered file is what every later phase reads, not the full national file

### Phase 2 — Operating hours parsing
- Write a function that takes the `OPERATING_HOURS` string and a test datetime, and returns: open / closed / unknown, plus a confidence score
- Handle the clean "Mon - Sun 00:00-23:59" pattern first, then handle edge cases (blank, unparseable, partial-week patterns) by returning "unknown" with low confidence rather than guessing
- Test this against every unique `OPERATING_HOURS` string found in the Sentosa subset

### Phase 3 — Trust/findability score
- For each AED, compute a rule-based score:
  - +1 if `AED_LOCATION_FLOOR_LEVEL` is present
  - +1 if `AED_LOCATION_DESCRIPTION` contains a concrete landmark word (kiosk, entrance, lift, staircase, shop name, etc.)
  - -1 if the description only has vague directional words (near, opposite, beside) with no concrete anchor
  - `OPERATING_HOURS` parsing (from Phase 2's confidence score):
    - +1 if confidence = 1.0 (parsed cleanly)
    - 0 if confidence = 0.5 (partially parsed — some segments matched, others in the same string were unparseable)
    - -1 if confidence = 0.0 (fully unparseable)
- Map the total score to a badge: High / Medium / Needs Verification
- Store this score alongside each AED record

### Phase 4 — Walking-network graph and real walking-distance ranking
- Use `osmnx` to download the walking network for the Sentosa bounding box
- Cache the downloaded graph to a local file so it is never re-downloaded live during the demo
- Write a function: given a test lat/long, return all Sentosa AEDs sorted by real walking time (not straight-line distance)
- This is the required baseline (straight-line nearest neighbor) plus its improvement (real walking distance) — implement straight-line first as the literal baseline, then walking-distance as the actual submission, and keep both so they can be compared in the evaluation section

### Phase 5 — Combined ranking
- Combine the following into one final ranking score for a given test location, date, and time:
  - Walking time (Phase 4)
  - Walking-distance confidence — discounted when the AED's snapped graph node is shared with other AEDs (Phase 4 finding: ~51.5% of Sentosa AEDs share a nearest-node with at least one other AED; penalty should scale with how many AEDs share that node, e.g. sharing with 3 others is a bigger penalty than sharing with 1)
  - Operating-hours confidence (Phase 2)
  - Trust score (Phase 3)
- Decide and document the weighting logic, including how walking-distance confidence is folded into the final score (this becomes part of the method card)
- Output the ranked list, each with its individual sub-scores visible, not just the final number — including the walking-distance confidence/penalty, so it's clear when a ranking involves a snap-collision-affected AED
- Also carry forward the `reachable=False` case from Phase 4 step 5: an unreachable AED should not receive a walking-distance score at all and should be excluded or clearly separated from the ranked list, not silently scored as if reachable

### Phase 6 — Explanation and runner-up reasoning (Novelty 4)
- For the top-ranked AED, generate a short plain-language explanation from its sub-scores (walking time, distance_confidence, hours_confidence, trust score) — templated, or one Gemini API call, cached per unique (test location, date, time) query rather than called live on every request
- The explanation should surface the most notable sub-score honestly — e.g. if distance_confidence is low due to snap-collision, or hours_confidence is low due to unparseable hours, say so in plain language rather than only praising the winning factors
- For the second-ranked AED, generate a one-line comparison against the top pick, referencing which specific sub-score(s) explain the gap (e.g. "slightly slower to walk to, but has a fully verified location description")
- If the top-ranked AED involves any uncertainty (unreachable-adjacent, low trust, or shares a snap node), the explanation must state this plainly — never phrase a low-confidence result as if it were certain

### Phase 7 — Backend API
- Wrap Phases 1-6 in FastAPI endpoints, e.g. `POST /rank` taking lat, long, date, time and returning the ranked list with explanations
- Endpoint to serve the Sentosa AED list with trust badges for map display
- A basic GET /health endpoint for quick server-up checks during the demo
- The walking graph and AED data should load once at server startup, not reloaded per-request — confirm startup time is reasonable before moving to Phase 8

### Phase 8 — Frontend: core flow
- Map view with `react-leaflet`, showing Sentosa AEDs as pins
- Input for test location (click on map or address search), date, and time
- Call the backend `/rank` endpoint and display the ranked list with explanations and trust badges
- Persistent, visible disclaimer that this is a simulation, not live guidance
- Get this fully working end to end before adding any of the remaining novelty features

### Phase 9 — Time slider (Novelty 1)
- Precompute rankings for a set of times across the day for the selected test location
- Add a slider UI control that swaps between precomputed rankings instantly

### Phase 10 — Crowd simulation (Novelty 2 in this doc's numbering)
- Generate a grid/spread of simulated starting points across one chosen attraction (e.g. Universal Studios Singapore)
- Run the ranking function for each point (backend batch endpoint)
- Tally which AED wins most often, display as a highlighted "bottleneck" marker with a count on the map

### Phase 11 — Trust score summary view (bonus output)
- A simple table/list view: "AEDs in this area needing re-verification," pulled from the Phase 3 scores
- This is the secondary deliverable that goes beyond ranking into registry quality

### Phase 12 — Deliverables writeup
- Problem and user definition
- Method card (techniques used, weighting logic, assumptions made, e.g. any basement-floor hours assumption)
- Baseline vs. final comparison (straight-line vs. walking-distance-plus-scoring), with the required metrics (effectiveness, safety/error, performance/usability, tail percentiles)
- Reproducibility package: filtered dataset, bounding box, setup steps, requirements file
- Safety and privacy statement

---

## 9. Session Approach

Work through the phases in Section 8 one at a time, in order. At the end of each phase, stop, confirm the phase is fully working and tested, and clearly report that the phase is complete before moving on. Do not start the next phase in the same session; wait for a new session to begin it.

## 10. Notes and Assumptions to Carry Through the Build

- Sentosa AEDs sampled so far are almost all 24/7; if the time slider looks flat, either widen the demo area slightly (e.g. include Harbourfront) or apply and clearly document an assumption that basement car park AEDs (floor B1, B2) have reduced pedestrian access outside standard hours even when the building itself is 24/7
- Crowd simulation starting points are simulated, not real footfall, and must be labeled as such everywhere they appear
- Every "confidence" or "trust" number shown to the user must be clearly a model estimate, never presented as verified fact
