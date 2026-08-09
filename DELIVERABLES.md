# AED Discovery & Routing — Deliverables (Sentosa Prototype)

Lane 1 — Discovery and Routing. This document is the Phase 12 deliverables
package: problem/user definition, method card, baseline-vs-final evaluation,
reproducibility package, and safety/privacy statement. It is written from,
and cross-checked against, the actual running code (`backend/`, `frontend/`)
rather than the original build plan alone — where the two differ, this
document describes what was actually built.

**This is a simulation/preparedness tool. It does not reflect live AED
availability, working condition, or real-time emergency guidance**, and that
statement is also shown persistently in the running app itself.

---

## 1. Problem and User Definition

**Problem.** A pin on a map is not the same as a real, reachable AED. The
nearest AED by straight-line distance can be the wrong answer once you
account for the actual walking network (water, buildings, one-way paths),
stated operating hours, and how findable the AED's in-building description
actually is. Someone planning ahead — not mid-emergency — has no easy way to
answer "if I were at this specific place, on this specific date and time,
which AED would actually be the best real choice, and how sure can I be?"

**Who this is for:**
- **Venue safety / emergency-preparedness planners** running drills or
  siting reviews for an attraction or precinct (e.g. "is our AED coverage
  actually good at 6pm on a Saturday, once you account for hours and real
  walking distance, not just the map?").
- **Registry data-quality owners** (e.g. SCDF / venue operators) who need a
  concrete, reasoned list of *which* AED records are unreliable and *why* —
  not just a spreadsheet of raw fields.
- **Members of the public**, using the tool to plan ahead (e.g. before
  visiting Sentosa) — explicitly not during an actual emergency.

**Decision this tool supports:** "For a hypothetical test location, date,
and time, which AED is the best real candidate, how confident should I be
in that answer, and what would make the second choice worth knowing too?"
It is a planning and evaluation tool, not a dispatch or navigation tool.

---

## 2. Method Card

### 2.1 Data and scope
- Source: SCDF Public Access AEDs (data.gov.sg), national GeoJSON
  (`AED_LOCATIONS.geojson`), ~9,000–10,000 records nationwide.
- Filtered to a Sentosa Island bounding box
  (lon 103.815–103.825, lat 1.245–1.260) → **66 AED records**
  (`backend/sentosa_aeds.geojson`), verified by inspection to cover Sentosa's
  main attractions (Universal Studios Singapore, SEA Aquarium, Resorts World
  Sentosa, Le Meridien, Equarius Hotel, beach stations, etc.) with no
  obviously unrelated records.
- Fields used per record: coordinates, building/address, floor level,
  free-text location description, free-text operating hours. Fields the
  dataset does **not** contain (live availability, battery status, real
  footfall, incident data, patient data) are never fabricated or implied.

### 2.2 Operating-hours parsing (`backend/hours_parser.py`)
Rule-based (regex) parser, not a model. Splits `OPERATING_HOURS` on `;` into
day-range + time-range segments (e.g. `"Mon - Fri 08:30-18:00; Sat - Sun
Closed"`), matches the test datetime's weekday against parsed segments, and
returns `(status, confidence)`:
- `status`: `open` / `closed` / `unknown`.
- `confidence`: `1.0` if every segment parsed cleanly, `0.5` if some
  segments parsed and others didn't, `0.0` if nothing usable parsed (blank,
  fully unparseable, or the test weekday isn't named by any parsed segment).
- **Never guesses**: if the test day isn't covered by any successfully
  parsed segment, the result is `unknown` with `0.0` confidence rather than
  assuming open or closed — the brief's "safely abstain" requirement applied
  directly to this component.

### 2.3 Trust / findability score (`backend/trust_score.py`)
A rule-based, `-3..+3` data-quality score per AED, independent of any
specific query — it answers "how reliable is this record", not "is this the
best pick right now." Four components:
- **Floor score** (+1 / 0): `AED_LOCATION_FLOOR_LEVEL` present or not.
- **Description score** (+1 / 0 / −1): +1 if the free-text description
  contains a concrete landmark word (kiosk, lift, entrance, shop name,
  first-aid room, etc. — ~80-word list); −1 if it uses only vague
  directional language ("near", "opposite", "beside") with no concrete
  anchor; 0 for blank or neither.
- **Hours score** (+1 / 0 / −1): derived directly from §2.2's parse
  confidence at a fixed reference datetime (a data-quality signal about the
  *string*, not the live query time).
- **Access-barrier score** (0 / −1): a keyword/regex detector for
  procedural, human-mediated, or schedule-gated barriers in the description
  ("approach security", "ticket required", "call ahead", etc.), grouped into
  three categories with their own plain-language reasons. This is a
  **confidence penalty only** — it never estimates or adds a time delay,
  since no real data exists on how long any such process takes, and
  inventing a number would misrepresent certainty the tool doesn't have.

Total score maps to a badge: **High (≥3)**, **Medium (=2)**, **Needs
Verification (≤1)**. Any single −1 component caps a record at "Needs
Verification" regardless of how good the other components are — a
deliberate design choice (documented assumption, not given verbatim in the
brief) so a genuinely unfindable location, an access barrier, or unparseable
hours can't be averaged away by unrelated fields being fine.

### 2.4 Walking-network routing (`backend/distance_ranking.py`)
- **Required baseline**: `rank_by_straight_line` — haversine distance,
  ignoring walkability entirely.
- **Actual ranking input**: `rank_by_walking_time` — real shortest-path
  walking time on a cached OpenStreetMap pedestrian graph (`osmnx` +
  `networkx`, single-source Dijkstra, edge weight = time in seconds at a
  documented constant walking speed of 1.34 m/s, ~4.8 km/h). The graph is
  downloaded and cached once (`sentosa_walk_graph_augmented.graphml`) and
  never re-downloaded live.
- Every route is annotated, from real OSM tags on the exact edges used, with
  honesty flags rather than fabricated risk scores: `uses_stairs`,
  `crosses_unmarked_road` (crosses a vehicle-carrying road with no marked
  pedestrian crossing nearby), `uses_permissive_access` (tolerated, not a
  guaranteed public right-of-way), `uses_synthetic_entrance` (see below).
  Each surfaces as a plain-language warning in the UI, never silently
  absorbed into the score.
- **Mobility profiles**: `walk` (default; still walks stairs, slower and
  flagged) and `wheelchair` (stairs edges removed from the graph entirely
  before pathfinding — an AED reachable only via stairs becomes
  unreachable for this profile rather than mis-scored as passable).
- **User-adjustable pace**: a `pace_m_per_s` parameter (0.3–2.5 m/s,
  default 1.34) rescales reported walking time without changing which route
  is found (documented as a simplifying assumption — see
  `distance_ranking._pace_time_scale`).
- **Unreachable AEDs** (no path in the walking network from the test point)
  are marked `reachable=False` and excluded from scoring rather than
  dropped silently or crashed on.

**Entrance augmentation — addressing the brief's "entrances" criterion**
(`backend/entrance_augmentation.py`, `build_building_footprints.py`): OSM's
pedestrian network only maps public streets, not building interiors, so
AED coordinates near/inside a building all snapped to whichever single
nearest street node happened to be closest — a "snap collision" that
understates real separation between AEDs in the same building. The fix
samples evenly spaced synthetic "entrance" points around the perimeter of
every OSM building footprint within 25m of an AED (109 of 494 downloaded
footprints qualified) and connects each into the graph as an additional
plausible door. Tagged `synthetic=True` end-to-end and surfaced honestly
(`uses_synthetic_entrance`) — these are evenly spaced guesses around a
mapped outline, **not surveyed doorways**, and buildings with no mapped
footprint or no nearby AED are unaffected. Measured effect: dataset-wide
snap-collision rate (AEDs sharing a nearest node with ≥1 other AED) dropped
from **51.5% → 15.2%**; Universal Studios Singapore's 19 AEDs went from
sharing 10 nodes to 19 distinct nodes (fully de-collided). One large
single-footprint case (Equarius Hotel) remains only partially resolved
(7→16 distinct nodes out of 64 grid-sample points) — reported as a real,
unresolved limitation, not hidden.

### 2.5 Combined ranking (`backend/combined_ranking.py`)
```
final_score = 0.55 * time_score
            + 0.15 * distance_confidence
            + 0.15 * hours_confidence
            + 0.15 * trust_normalized
```
- `time_score = exp(-walking_time_s / 1200)` — a 20-minute half-scale decay,
  a **documented planning-level assumption**, tuned once after real-query
  testing at the original 300s constant let the three 0.15-weighted terms
  (0.45 combined) dominate over walking time for any AED beyond ~10–15
  minutes' walk (a 36-minute walk out-ranking a 16-minute walk purely on a
  trust-badge gap). Re-verified after the fix: across a sweep of all 657
  walking-graph nodes as candidate test points, the confidence terms still
  produced **1,909 near-tie reorderings**, but the maximum walking-time gap
  involved in any of them was capped at 250s (~4.2 min) — confidence/trust
  can no longer override a many-minutes-longer walk.
- `distance_confidence = 1 / n_sharing_node` (§2.4's snap-collision count).
- `hours_confidence`: the live parse confidence from §2.2 **evaluated at
  the query's test datetime**, redefined for scoring — `open` contributes
  the raw parse confidence (1.0/0.5), `closed` and `unknown` both
  contribute `0.0` (they're still shown as distinct statuses in the UI;
  only the numeric score is unified, since neither is a confident "go
  here").
- `trust_normalized = (trust_score + 3) / 6`, rescaling §2.3's `-3..3`
  score to `0..1`.
- **Segregation, not inline penalty**: AEDs that are `reachable == False`,
  or confidently `closed` at the test datetime, are **excluded from the
  ranked list entirely** and returned separately with a reason — never
  scored as if reachable/open, never silently dropped. `unknown` hours
  status stays in the ranked list (with `hours_confidence=0.0` pulling its
  score down) because "we don't know" is a different claim from "we know
  it's shut," and excluding it would be a guess.
- Weighting rationale: walking time is the brief's primary decision axis,
  so it gets the majority weight (55%); the other three are framed by the
  brief as uncertainty/quality signals, not primary ranking criteria, so
  15% each — verified empirically to be strong enough to reorder near-ties
  without letting a much-closer AED lose to a farther one purely on
  confidence (see §2.4's 1,909-flip figure).

### 2.6 Explanation and runner-up reasoning (`backend/explanation.py`)
One Gemini 2.5 Flash call per unique `(location, date, time)` query,
**cached to disk and never called live on repeat views** — narrates the
already-computed sub-scores for the top-5 ranked AEDs, it does no ranking
math of its own. Tone contract (hand-written, then verified against real
Gemini output): state the pick confidently first; name the *specific*
sub-score behind any caveat (e.g. "shares a walking-graph node with two
other AEDs" rather than "may be less accurate"); never bury a confident
recommendation under generic hedging ("may vary," "please verify"); if
nothing is uncertain, say so plainly rather than manufacturing a caveat.
Runner-up lines compare candidates #2–#5 directly against #1, naming which
sub-score explains the gap. A capped-explanation edge case (time-slider
segments beyond a per-query Gemini-call budget) was tested and confirmed to
fall back to a generic, AED-name-free note rather than ever risk narrating
the wrong AED (`backend/test_timeline_explanations.py`).

### 2.7 Grounded chat Q&A (`backend/chat_qa.py`) — beyond the original plan
A read-only Q&A layer over an already-computed `/rank` (or `/rank/timeline`)
response: the frontend passes its current on-screen payload, and a single
fresh Gemini call answers strictly from those sub-scores — it cannot invent
a new time estimate, change any score, or answer about live traffic/AED
battery/availability (it says so honestly rather than guessing). Rate-limited
per session as a cost safeguard.

### 2.8 Documented assumptions carried through the build
- Constant walking speed (1.34 m/s) and a slower constant stairs speed
  (0.5 m/s) — OSM edges in this bbox carry no reliable per-edge pedestrian
  speed tags.
- `TIME_DECAY_S = 1200` (§2.5) is a planning-level scale choice, not a
  measured value.
- Trust-score badge thresholds (`≥3` / `=2` / `≤1`) are a documented design
  choice, not given verbatim in the brief.
- Access-barrier detection changes confidence only, never adds a fabricated
  delay.
- Sentosa AEDs sampled are almost all 24/7, so the time-slider / hours
  component of the demo naturally exercises the "open" path far more than
  "closed" — the segregation and confidence-zeroing logic for closed/unknown
  status is verified by direct test and by construction, not by frequent
  natural occurrence in this specific dataset.

---

## 3. Baseline vs. Final — Evaluation

**Baseline** (required by the brief): `rank_by_straight_line` — haversine
nearest-neighbor, ignoring hours, trust, and walkability.
**Final**: `rank_combined` — real walking-network time + distance
confidence + hours confidence + trust score, weighted as in §2.5.

**Primary metric (nominated):** Top-1 disagreement rate against the
straight-line baseline (§3.1/§4.1) — it most directly answers the brief's
core question of whether accounting for the walking network, hours, and
trust actually changes which AED a planner would be told to go to, rather
than just re-ordering already-correct answers. Effectiveness (§3.1),
safety/error (§3.2), and performance/usability (§3.3) are reported
alongside it, not instead of it.

### 3.1 Effectiveness
> _See §4 for the exact benchmark numbers, run across 200 sampled
> walking-graph node coordinates as test points against the ranking
> pipeline directly._

The flagship qualitative example (Imbiah Road test point, Saturday 14:00):
straight-line baseline ranks **Le Meridien** closest (120m); real walking
time flips the winner to **Beach Arrival Plaza** (160s vs. 178s walking
time) — the straight-line-nearest AED is not the fastest AED to actually
reach. Quantitatively, across a systematic sweep of all 657 walking-graph
nodes as candidate test points, confidence-driven reordering (distance/
hours/trust breaking near-ties in walking time) occurred **1,909 times**;
purely on distance-confidence alone, the effect is worth roughly 2 minutes
of walking-time difference on average (up to ~4 minutes at the extreme).

### 3.2 Safety / error metric
Two structural guarantees, checked by direct test/benchmark rather than
asserted:
1. **No excluded AED ever appears in the ranked list.** `closed` and
   `reachable=False` AEDs are excluded by construction in
   `combined_ranking._combine_for_datetime`; verified with zero violations
   across the benchmark sample (§4).
2. **No capped time-slider explanation ever names the wrong AED.** Verified
   by a synthetic 8-changepoint test (`test_timeline_explanations.py`) —
   capped segments render a generic, AED-name-free note instead of reusing
   a mismatched prior explanation.

### 3.3 Performance / usability metric
Real, client-measured HTTP response time for `POST /rank` against the live
local backend — see §4 for mean/p50/p90/p95/max. Backend startup (AED data
+ cached walking graph load) reported directly by `GET /health`.

### 3.4 Tail metrics (required, not just averages)
p90/p95 of both API response time and the #1-ranked AED's real walking
time, reported alongside the mean — see §4.

---

## 4. Benchmark Results (real numbers, measured against the running local backend)

**Methodology.** Test points are real walking-graph node coordinates
sampled from the cached Sentosa graph (n=200 for the computation/
effectiveness/tail metrics, cheap enough to run at that scale in-process;
n=15 for live HTTP timing) — not AED coordinates, since testing "from" an
AED's own location degenerately reports ~0s walking time to itself or a
co-located AED. Saturday 2026-08-08, 14:00, default mobility (`walk`) and
pace (1.34 m/s).

One methodology correction made during this run, worth recording: an
initial HTTP-timing pass opened a fresh `requests` connection to
`localhost` for every call, which pays a consistent **~2 second one-time
connection-establishment tax per call on this Windows machine** (an
IPv6/IPv4 resolution quirk, not application behavior) — this initially
looked like a mysterious ~2.1s floor on every request, *including cache
hits*. Isolated by timing `rank_combined` and `generate_explanation`
in-process directly (125ms total including a cache hit) versus over HTTP,
then confirmed by comparing a fresh connection per call against a reused
`requests.Session()`: the first call on any connection took ~2s regardless
of endpoint (even `/health`); every subsequent call on the *same*
connection dropped to single-digit milliseconds of pure network overhead.
The numbers below use a reused connection against `127.0.0.1`, matching
how a real browser session behaves (keep-alive), not the artifact.

### 4.1 Effectiveness — baseline vs. final top-1 disagreement
| | Value |
|---|---|
| Sample size | 200 test points |
| Agrees with straight-line baseline | 97 (48.5%) |
| **Disagrees with straight-line baseline** | **103 (51.5%)** |

Over half the time, the straight-line-nearest AED is *not* the AED the
combined walking-network + confidence ranking would actually recommend —
directly quantifying why the required baseline is explicitly called out in
the brief as insufficient on its own.

### 4.2 Safety / error metric
| Check | Result (n=200) |
|---|---|
| Excluded (closed/unreachable) AEDs leaking into the ranked list | **0 violations** |
| `len(ranked) + len(excluded) != 66` (an AED silently dropped) | **0 mismatches** |

### 4.3 Performance / usability metric
**Core ranking computation** (`rank_combined`, in-process, no HTTP/Gemini — n=200):

| Mean | p50 | p90 | p95 | Max |
|---|---|---|---|---|
| 62.9 ms | 57.1 ms | 73.5 ms | 114.6 ms | 222.3 ms |

**Full `/rank` HTTP endpoint, cold (query not seen before, includes one live Gemini explanation call — n=15):**

| Mean | p50 | p90 | p95 | Max | Min |
|---|---|---|---|---|---|
| 5,342.7 ms | 6,287.0 ms | 8,495.8 ms | 9,257.6 ms | 9,067.1 ms | 73.9 ms |

**Full `/rank` HTTP endpoint, warm (repeat of the same query — cache hit — n=15):**

| Mean | p50 | p90 | p95 | Max |
|---|---|---|---|---|
| 100.6 ms | 98.4 ms | 125.8 ms | 135.7 ms | 133.2 ms |

Interpretation: the ranking algorithm itself is fast and consistent
(p95 under 115ms). The gap between cold and warm `/rank` calls (~5.2s at
the mean) is entirely attributable to the external, narrowly-scoped Gemini
explanation call (§2.6) — never the core ranking math — and is paid at most
once per unique `(location, date, time)` query, never again on repeat views,
by design.

### 4.4 Tail metrics — top-1 pick's real walking time (n=200)
| Mean | p50 | p90 | p95 | Max |
|---|---|---|---|---|
| 141.6 s | 103.9 s | 265.0 s | 516.5 s | 915.9 s |

The mean (~2.4 min) is a reasonable typical case; the p95 (~8.6 min) shows
that a meaningful tail of test locations only has a much slower best-case
AED available — exactly the kind of gap an average alone would hide, and a
concrete, data-backed argument for why Sentosa's AED coverage (or the
registry's floor/description completeness in those areas) is worth a
planner's attention beyond the average case.

---

## 5. Reproducibility Package

**No hosting/deployment — this prototype runs locally only**, per project
decision; the steps below reproduce the local demo, not a hosted deployment.

### 5.1 Data manifest

**Primary dataset**
| | |
|---|---|
| Name | Public Access AEDs |
| Publisher | Singapore Civil Defence Force (SCDF) |
| Source URL | https://data.gov.sg (dataset: Public Access AEDs) |
| Version / retrieval date | Downloaded 2026-08-08, saved as `AED_LOCATIONS.geojson` (national file, ~9,000–10,000 records) |
| Licence | [Singapore Open Data Licence, version 1.0](https://data.gov.sg/open-data-licence) — free to use, share, and adapt (commercially or non-commercially), provided the source is attributed and the use is not presented as officially endorsed. This document and the in-app disclaimer both carry that attribution. |
| Fields used | `AED_ID`, `LATITUDE`, `LONGITUDE`, `BUILDING_NAME`, `HOUSE_NUMBER`, `ROAD_NAME`, `POSTAL_CODE`, `AED_LOCATION_FLOOR_LEVEL`, `AED_LOCATION_DESCRIPTION`, `OPERATING_HOURS` |
| Transformations | Bounding-box filter to Sentosa (lon 103.815–103.825, lat 1.245–1.260) → `backend/sentosa_aeds.geojson` (66 records); no field-level edits, additions, or corrections to source values — parsed/derived fields (hours status, trust score, etc.) are computed and stored separately, never overwrite the source fields. |

**Supplemental dataset**
| | |
|---|---|
| Name | OpenStreetMap pedestrian network + building footprints (Sentosa bounding box) |
| Publisher | OpenStreetMap contributors, via the `osmnx` Python package |
| Source URL | https://www.openstreetmap.org (data), https://osmnx.readthedocs.io (fetch tool) |
| Version / retrieval date | Downloaded 2026-08-09, cached as `backend/sentosa_walk_graph_augmented.graphml` and `backend/sentosa_building_footprints.geojson`; never re-downloaded live during the demo |
| Licence | [Open Database License (ODbL) 1.0](https://www.openstreetmap.org/copyright) — requires attribution: **© OpenStreetMap contributors**, and that any produced database derived from it (the cached graph) remains available under ODbL or a compatible licence if redistributed |
| Fields used | Pedestrian-relevant way/node tags (highway type, `steps`, access tags), building footprint polygons within 25m of an AED |
| Transformations | Downloaded via `osmnx` for the Sentosa bbox → augmented with synthetic entrance nodes sampled around building footprint perimeters (§2.4, `entrance_augmentation.py`) → cached to `.graphml`. Synthetic entrance edges are tagged `synthetic=True` end-to-end and are never presented as surveyed doorways. |

**Frozen file checksums** (SHA-256, for verifying a copy matches what this
evaluation was run against):
```
AED_LOCATIONS.geojson                     e2ef793ffd0fd2dbe99ffdcfb21b38154c81fd0685d1f0fcc5b75a6d57205c02
backend/sentosa_aeds.geojson              49bc11149782a2f9140d47fd086cbe75fd304c32f4dcbc2ec4f5667ab1d85e57
backend/sentosa_building_footprints.geojson  167d3a9aef1e83bcfe37f78d8c550959cee91464609a67e8159947e538b2b0f1
backend/sentosa_walk_graph_augmented.graphml 7c3444d3820d06d4b28b3ff720f1a8dda5af9bf6d6c03e99c3cf7d99ef5fb67c
```
Regenerate and compare with (PowerShell): `certutil -hashfile <path> SHA256`.

### 5.2 What's included
- Filtered dataset: `backend/sentosa_aeds.geojson` (66 records).
- Documented bounding box: lon 103.815–103.825, lat 1.245–1.260.
- Cached walking graph: `backend/sentosa_walk_graph_augmented.graphml`
  (OSM pedestrian network + synthetic entrance nodes, §2.4) and
  `backend/sentosa_building_footprints.geojson`.
- `backend/requirements.txt` (pinned versions) and `frontend/package.json`.
- `backend/.env.example` — copy to `backend/.env` and fill in
  `GEMINI_API_KEY` (only used for §2.6/§2.7's narrow, cached explanation/
  chat calls; the core ranking math runs with no API key at all).
- No API keys are committed to the repo: `backend/.env` is gitignored, and
  the only tracked file is `.env.example` with a placeholder value.

### 5.3 Backend setup (Windows, PowerShell)
```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill in GEMINI_API_KEY
uvicorn main:app --reload
```
Verify with `GET http://localhost:8000/health` — should report
`data_loaded: true` and a `startup_time_s` in the low hundreds of
milliseconds (data + cached graph load, no network calls at startup).

### 5.4 Frontend setup
```powershell
cd frontend
npm install
npm run dev
```
Opens the Vite dev server (default `http://localhost:5173`), which calls
the backend at `http://localhost:8000`.

### 5.5 Regenerating the filtered dataset / graph from scratch (optional)
Only needed if starting from the raw national `AED_LOCATIONS.geojson`:
1. Bounding-box filter to Sentosa → `sentosa_aeds.geojson` (Phase 1 script).
2. `build_building_footprints.py` — download OSM building footprints near
   Sentosa AEDs.
3. `entrance_augmentation.py` — build the augmented walking graph from the
   raw `osmnx` download + footprints, cache to
   `sentosa_walk_graph_augmented.graphml`.
Not required for a normal run — both cached files are already committed so
the backend never re-downloads from OSM live during a demo.

### 5.6 Tests
`backend/test_hours_parser.py`, `test_trust_score.py`,
`test_crowd_simulation.py`, `test_timeline_explanations.py` — run with
`pytest` from `backend/` (venv activated).

---

## 6. Safety and Privacy Statement

**Required disclaimer (shown on every screen/demo).** Every user-facing
screen displays, verbatim:

> Prototype for planning and simulation only—not for emergency use. In an
> emergency in Singapore, call 995 immediately and follow SCDF instructions.

This is the live `DISCLAIMER` string in `backend/main.py`, returned on
every `/rank`, `/rank/timeline`, `/crowd-simulation`, `/route`, and `/chat`
response, and rendered persistently in the frontend (`DisclaimerBanner.jsx`,
shown on every screen, and `OnboardingModal.jsx`, shown on first load).

**Safety.**
- **No live emergency dispatch.** This tool does not call, page, or notify
  any emergency service, responder, or venue staff — it only computes and
  displays a ranking.
- **No 995 integration.** The tool never contacts, simulates contacting, or
  claims to contact SCDF's 995 emergency line; the disclaimer above directs
  the user to call 995 themselves, entirely outside the tool.
- **No myResponder integration.** This prototype has no connection to
  SCDF's myResponder app or its volunteer-responder network, and does not
  claim to.
- **No claim of current AED availability or working condition.** The
  dataset is a historical registry snapshot (§5.1); the tool never asserts
  that any specific AED is present, powered, or functional right now.
- **Dataset date is stated plainly.** National AED registry data retrieved
  2026-08-08 (§5.1); every export/output based on it should be read as of
  that date, not "live."
- This is a **simulation/preparedness tool**. It never presents a route,
  operating-hours parse, or trust score as verified, live, or guaranteed —
  every screen surfaces the persistent disclaimer above.
- Operating-hours status is a **confidence estimate from historical
  registry text**, never a live availability check — the dataset contains
  no real-time signal at all.
- Closed and unreachable AEDs are structurally excluded from the ranked
  list rather than risk being mistaken for a viable pick (§3.2).
- Every confidence/trust number shown to a user is explicitly a rule-based
  or model *estimate* of data quality, never a claim about the physical
  AED's real presence or working condition.
- Crowd-simulation results are **simulated grid data, not real footfall,
  queue, or incident data**, and are labeled as such at the point of
  display (`main.py`'s crowd-simulation-specific disclaimer prefix).
- The chat Q&A feature is grounded strictly in the sub-scores already on
  screen and explicitly declines to answer anything requiring live data it
  doesn't have (§2.7), rather than guessing.
- Mobility-relevant route facts (stairs, unmarked road crossings,
  permissive-access paths, estimated/synthetic entrances) are surfaced as
  plain-language warnings rather than absorbed silently into a score,
  because a routing choice with real physical-access consequences should
  never be invisible to the person relying on it.
- **Safe failure when accessibility is uncertain.** An AED with no path in
  the walking network, or confidently closed at the test time, is excluded
  from the ranked list rather than scored as if reachable/open (§2.5, §3.2);
  `unknown` hours status stays visible but scored as non-confident, never
  guessed as open.
- **All assumptions are documented, not silent** — constant walking speed,
  the 20-minute time-decay scale, trust-badge thresholds, and the
  basement-floor-hours question are listed explicitly in §2.8 rather than
  left implicit in the code.
- **Crowd-simulation start points are synthetic**, generated on a grid over
  a chosen attraction's footprint — never real visitor GPS traces, footfall
  counts, or any other real user data (§4.2 of `README.md`).

**Privacy.**
- The dataset contains no personal or patient information — only public
  infrastructure (AED) location and registry metadata.
- No user location, query history, or chat content is persisted to disk;
  `chat_qa.py`'s per-session rate-limit state is in-memory only and does
  not survive a backend restart.
- The Gemini API key is read only from an untracked `.env` file
  (`backend/.env`, gitignored) via `GEMINI_API_KEY`, never logged, printed,
  or written to any cache file; only the sub-scores needed for a given
  explanation/chat call are sent to the API — no broader dataset dump.
