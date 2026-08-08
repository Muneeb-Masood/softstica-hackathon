# Findings log

Running log of test points, coordinates, and design-review results discussed
while designing Phase 5 (combined ranking). Kept here (not just in chat) so
a later session doesn't have to reconstruct them from memory. Append new
entries under a dated heading; don't rewrite old ones.

---

## 2026-08-08 — Phase 5 weighting design review

### Proposed weights (pending implementation)

`final_score = 0.55*time_score + 0.15*distance_confidence + 0.15*hours_confidence + 0.15*trust_normalized`

- `time_score = exp(-walking_time_s / 300)` — 300s decay constant, documented assumption.
- `distance_confidence = 1 / n_sharing_node` — `n_sharing_node` = count of AEDs (including itself) that snap to the same nearest walking-graph node. Fixed per AED, independent of test point/time.
- `hours_confidence` — the *live* parse confidence from `hours_parser.parse_operating_hours()` at the test datetime, but **redefined for scoring purposes**: `status=="open"` uses the raw parse confidence (1.0/0.5); `status in {"closed", "unknown"}` both contribute `0.0` to the score. (Closed vs. unknown are still shown with different labels in the UI — the zeroing only affects the numeric score.)
- `trust_normalized = (trust_score.total_score + 3) / 6` — rescales the -3..3 rule-based trust score to 0-1.
- `closed`-status AEDs are **segregated into a separate bucket, excluded from the main ranked list entirely** (approved 2026-08-08) — not scored inline, regardless of how good their other sub-scores are. `reachable=False` AEDs are excluded the same way (already specified in CLAUDE.md Phase 5).

### Test point: Imbiah Road flagship example (straight-line vs. walking-time)

**Coordinates: lat=1.252982, lon=103.818190** (nearest OSM node: 603563263, tagged "Imbiah Road"). This is the point from the original Phase 4 session — record it here so it's never lost again.

At this point, Saturday 2026-08-08 14:00:
- Straight-line ranks **Le Meridien (098679-001)** closest (120m, rank 1); Beach Arrival Plaza (098604-001) is rank 6 (168m).
- Walking-time flips it: **Beach Arrival Plaza wins** (160s vs. 178s) — the flagship example demonstrating why walking-network distance beats straight-line.
- **Confirmed under the full combined score too**: Beach Arrival Plaza stays #1 (final_score=0.7726) ahead of Le Meridien (#2, 0.7541). Flower Terrace Station and Imbiah Station follow at #3/#4.
- Caveat: at this point all top candidates have identical (maxed) distance_confidence/hours_confidence/trust, so this example confirms the weights don't *break* the flagship result, but doesn't by itself prove the 0.15-weighted terms can reorder anything (see next section for that).

### Test case: snap-collision confidence actually reordering a near-tie

Searched all 657 walking-graph nodes as candidate test points, comparing every pair among each point's 6 closest-by-time AEDs, looking for cases where a slower-by-time AED overtakes a faster one once distance/hours/trust confidence are folded in (with closed AEDs excluded from the search, matching the segregation decision above).

**Test point: lat=1.25163, lon=103.81792** (node 11819634331). Saturday 2026-08-08 14:00:
- `098008-003` (unnamed building, floor 2, "First Aid Room"): walking time **246s**, `distance_confidence=0.33` (shares its nearest node, 6418085540, with `098008-001` and `098008-002` — 3 AEDs total on one node), `trust_normalized=1.00`. `final_score = 0.5921`.
- `098679-002` (Le Meridien): walking time **247s** — 1 second slower — `distance_confidence=1.00` (unique node), `trust_normalized=1.00`. `final_score = 0.6916`.
- **Le Meridien wins despite being 1 second slower**, purely on the distance-confidence term (both trust and hours are equal here, so this isolates the snap-collision penalty specifically). Margin: 0.0996.
- This was not a rare fluke: the same node-cluster search found **1256 total flip instances** across the 657 sampled test points / top-6 pairs, so confidence-driven reordering of near-ties happens broadly across the map, not just in one contrived case.
- Conclusion: at 0.15 weight, a full distance-confidence swing (1.00 → 0.33, i.e. losing 2 collision-partners) is worth roughly the same as ~60-120s of extra walking time in this dataset's speed/decay scale — meaningful given most Sentosa AEDs are within a few hundred meters of each other. Weights judged adequate; not raising them.

### Mock: closed-AED display (Palawan Beach area)

**Test point: lat=1.2495, lon=103.8215** (near Palawan Beach), **Saturday 2026-08-08 18:00** — a time when 5 real Sentosa AEDs are closed by their actual `OPERATING_HOURS`: `900021-001`/`900019-001` (Palawan Beach, "Mon-Fri 08:30-18:00; Sat-Sun Closed") and `098008-001`/`098008-002`/`098008-003` (unnamed building, "Mon-Sun 10:00-17:00").

Compared segregated-bucket vs. inline-with-badge:
- Inline with `hours_confidence` simply zeroed (no extra penalty) was **not enough** — Palawan Beach AEDs (115s walk, otherwise maxed dist_conf/trust) still landed at rank 4-5, ahead of several genuinely-open AEDs.
- Inline with an ad hoc ×0.1 penalty when closed reproduced the segregated ordering almost exactly, i.e. once you fix the "too weak" problem, inline and segregated converge on the same math — the only real difference left is UI framing.
- **Decision (approved 2026-08-08): segregated bucket.** Closed AEDs are excluded from the main ranked list and shown separately with a reopen-time hint, not scored inline.

### Phase 5 implemented and verified against this log (2026-08-08)

Implemented in `backend/combined_ranking.py` (`rank_combined`), using `distance_ranking.compute_snap_collision_confidence` for the distance-confidence term. Re-ran both test points above through the actual code (not the scratch scripts):

- Imbiah Road point (1.252982, 103.818190), Sat 14:00: Beach Arrival Plaza rank 1 (final_score=0.7726), Le Meridien 098679-001 rank 2 (0.7541) — matches the design-review numbers exactly.
- Snap-collision point (1.25163, 103.81792), Sat 14:00: 098008-003 (dist_conf=0.33) rank 9, final=0.5921; 098679-002 (dist_conf=1.00) rank 3, final=0.6916 — matches exactly, confirms the collision-confidence term is live and reordering as designed.
- Both runs correctly excluded `900021-001`/`900019-001` (Palawan Beach) with `reason="closed"` rather than scoring them — segregation confirmed working in code, not just in the design.

## 2026-08-08 — Phase 6 explanation generation: templates, then Gemini wiring

### Four hand-written tone examples (approved before any API call)

Written from real sub-scores before wiring the model, to fix the tone contract: state the pick confidently first, name any specific uncertainty in the same breath using the actual sub-score that causes it, never bury the recommendation under hedging, never manufacture a caveat when nothing is uncertain.

1. **Clean top pick, no uncertainty** — Beach Arrival Plaza (098604-001) at the Imbiah Road point.
2. **Runner-up losing purely on walking time** — Le Meridien 098679-001 vs. Beach Arrival Plaza, same point (18s gap, all other sub-scores tied).
3. **Runner-up losing on distance-confidence, not time** — 098008-003 (1s *faster* than 098679-002 but dist_conf 0.33 vs. 1.00) at the original snap-collision point.
4. **Top pick itself carrying uncertainty** — found by sweeping graph nodes for a case where the real #1-ranked AED (not just a runner-up) has dist_conf < 1.0: `098008-003` ("31 Beach View", Level 2, "First Aid Room") at **lat=1.2507821, lon=103.820316**, Sat 14:00 — walking_time_s=20.2, dist_conf=0.333 (shares its node with `098008-001`/`098008-002`, same building), trust High, hours_confidence=1.0, final_score=0.8641. Wins decisively on time (20s vs. next-closest ~167s+) despite the routing caveat, so the caveat is stated but doesn't get buried under it.

These four became the few-shot examples baked into `explanation.py`'s system instruction, verbatim.

### Gemini wiring

- `python-dotenv` loads `backend/.env` (gitignored, confirmed not tracked by git; key is only ever read via `os.environ.get("GEMINI_API_KEY")` and passed straight to `genai.configure` — never printed, logged, or written to any cache file).
- `google-generativeai==0.8.6` (the SDK CLAUDE.md specifies). Note: this SDK prints a `FutureWarning` on import — Google has end-of-lifed `google.generativeai` in favor of a new `google.genai` package. Left as-is since CLAUDE.md names the SDK explicitly; worth a note in the method card and a possible future migration, not addressed now.
- Model: `models/gemini-2.5-flash` (selected via a live `list_models()` call against the real key — a small, fast model matches the brief's "used narrowly" scope for this call).
- `generation_config` requests `response_mime_type="application/json"` so the model returns `{"top_explanation": ..., "runnerup_explanation": ...}` directly, no markdown-fence parsing needed.
- Caching: one call per unique `(test_lat rounded to 5dp, test_lon rounded to 5dp, test_dt isoformat, top AED id + its sub-scores, runner-up AED id + its sub-scores)`, written to `backend/cache/explanations/<sha1>.json`. Including the sub-scores in the key (not just location/time) means a future ranking-logic change can't silently serve stale narrative text for different numbers.

### Live run against three real points (covers all four patterns)

Re-ran with the real `rank_combined` output (not synthetic pairs) so every explanation reflects an actual ranking, not a staged one:

1. **Imbiah Road point** (1.252982, 103.818190), Sat 14:00 — one call covers patterns 1+2 (top = Beach Arrival Plaza, runner-up = Le Meridien, real rank 1/2).
2. **New point found for a real adjacent-rank distance-confidence flip**: lat=1.2560496, lon=103.8207217, Sat 14:00 — rank 1 = Waterfront Station (098141-001, 44.2s, dist_conf 1.0), rank 2 = Universal Studios Singapore (098140-001, 30.1s — *faster* — dist_conf 0.5). Real #1/#2 pair, not a hand-picked isolated comparison like the original design-review case (which were rank 3/9, not adjacent) — pattern 3.
3. **Beach View point** (1.2507821, 103.820316), Sat 14:00 — real rank 1 = 098008-003 with dist_conf 0.33 — pattern 4.

Gemini output matched the hand-written tone on all three (confident pick stated first, specific sub-score named for the caveat, no generic hedging). Second call against the same query returned `source="cache"`, confirming no duplicate API call on repeat views.

### Confirmed Gemini output, verbatim (recorded here so a future session doesn't need chat history)

Pulled directly from `backend/cache/explanations/*.json`:

1. Beach Arrival Plaza (top): *"Beach Arrival Plaza is the top pick for this location and time — about a 3-minute walk (160s) along the actual walking path, currently open on a fully verified hours pattern, and a High trust score (floor, description, and hours all check out). No uncertainty flags on this one."*
2. Le Meridien (runner-up, same query): *"Le Meridien Singapore, Sentosa is a close second, about 18 seconds slower to walk to than Beach Arrival Plaza — that's the entire gap, since its hours and trust confidence are tied with the top pick."*
3. Waterfront Station / Universal Studios Singapore pair: top — *"Waterfront Station (098141-001) is the top pick for this location and time — about a 44-second walk, currently open on a fully verified hours pattern, and a High trust score. No uncertainty flags on this one."* runner-up — *"Universal Studios Singapore (098140-001) is actually faster to walk to (about 30 seconds) than Waterfront Station, but it ranks lower because its distance-confidence is 0.5, indicating some uncertainty in the exact walking path or access point. Its hours and trust are otherwise fully verified."*
4. 098008-003 (top, carries its own uncertainty) / 098008-002 (runner-up): top — *"098008-003 is the top pick — about a 20-second walk. However, its walking distance is an approximation because it shares a mapped street-level access point with other AEDs in the same building, leading to a distance-confidence of 0.33. Its hours and location details are otherwise fully verified."* runner-up — *"098008-002 is tied with the top pick on all metrics: identical walking time, distance confidence, hours confidence, and trust badge."*

All four confirm the tone contract held under a real model call, not just in the hand-written drafts: pick stated first, specific sub-score named for any caveat, no generic hedging language ("may vary", "please verify") anywhere in the output.

## 2026-08-08 — Phase 5 tuning correction: TIME_DECAY_S raised 300 -> 1200

### Problem found during real end-to-end frontend testing (not the synthetic sweeps above)

`time_score = exp(-walking_time_s / TIME_DECAY_S)` with the original `TIME_DECAY_S=300` (5 min) decays to near-zero by ~10-15 minutes of walking time. Past that point, the 0.55-weighted time term stops meaningfully discriminating between candidates, so the ranking is effectively decided by the remaining 0.45 combined weight of distance-confidence/hours-confidence/trust -- backwards for a tool meant to prioritize real accessibility, and outside the scope the original design-review sweeps tested (those sweeps only looked at *near-time-tied* pairs reordering on confidence, at data.gov.sg-adjacent short walks -- never a >2x-longer walk beating a much shorter one).

**Concrete failing example**, query lat=1.25653, lon=103.81503, 2026-08-08 17:15:
- Sentosa Boardwalk: walking_time_s=2204 (36m44s), trust_badge=High, dist_conf=1.0, hours_conf=1.0 -> ranked **above** Universal Studios Singapore: walking_time_s=982 (16m22s), trust_badge=Medium, dist_conf=1.0, hours_conf=1.0 -- a walk more than twice as long won purely on the trust-badge gap (High vs Medium = the full 0.15*trust_normalized swing).

### Fix

Raised `TIME_DECAY_S` from `300` to `1200` (20-minute half-scale) in `backend/combined_ranking.py`. No other change: same four components, same `0.55/0.15/0.15/0.15` weighting. This keeps walking time meaningfully influential across Sentosa's realistic 5-40 minute walk range instead of flattening out by minute 10-15.

### Re-verified against the exact failing query (same lat/lon/date/time, backend restarted to load the new constant)

Ranked list unchanged in size (61 ranked / 5 excluded -- this was a scoring change only, not a filtering change). The specific pair now orders correctly:

| AED | walking_time_s | trust_badge | final_score | rank |
|---|---|---|---|---|
| Universal Studios Singapore | 982 (16m22s) | Medium | 0.6676 | #26 |
| Sentosa Boardwalk | 2204 (36m44s) | High | 0.5376 | #53 |

Universal Studios now clearly outranks Sentosa Boardwalk. Top-5 for this query under the new constant: Imbiah Station (270s, 0.8891), Tiger Sky Tower (334s, 0.8664), Flower Terrace Station (485s, 0.8171), Imbiah Station (585s, 0.7879), Le Meridien Singapore Sentosa (617s, 0.7789) -- all high-trust, fully-open, monotonically decreasing by walking time, as expected now that time dominates within this range.

### Note for the method card

The "1256 reordering instances" / "~1 minute" figures from the original Phase 5 design-review section above (`### Test case: snap-collision confidence actually reordering a near-tie`) were measured under `TIME_DECAY_S=300`. **Superseded by the re-run below -- use the 2026-08-08 (post-tuning) numbers in the method card, not the original 1256 figure.**

### Snap-collision flip sweep re-run under TIME_DECAY_S=1200 (2026-08-08)

Re-ran the identical methodology from the original design-review sweep (same 657 walking-graph nodes as candidate test points, same Saturday 2026-08-08 14:00, same "6 closest-by-walking-time AEDs per node, all pairs checked, closed/unreachable AEDs excluded via `rank_combined`'s own segregation") with the code as it now stands (`TIME_DECAY_S=1200`), to check whether raising the time constant weakened the confidence terms' ability to reorder near-ties.

**Result: confidence-driven reordering is still very much alive -- and more frequent than before, not less:**

- **1909 total flip pairs** across **398 of 657 nodes** (up from 1256 under the old constant). This direction makes sense: a slower time-decay shrinks the *time_score* gap between walking times that are already close together (a few hundred seconds apart, which is what "6 closest at one point" mostly looks like), so the 0.15-weighted confidence terms have relatively more room to flip those near-ties than they did under the steeper 300s decay.
- Splitting by cause:
  - **Pure distance-confidence flips** (hours_confidence and trust tied, only `distance_confidence` differs): **1586 instances**. A full confidence swing outweighs **~120s (2 min) of walking-time difference on average**, up to **236.5s (~3.9 min) at the extreme** -- example: `098008-003` (110.2s walk, dist_conf=0.33, shares its node with 2 others) loses to Le Meridien `098679-002` (346.6s walk, dist_conf=1.0) by score_margin=0.0102, both High trust / hours_confidence=1.0.
  - **Pure trust-badge flips** (dist_conf and hours tied, only trust differs): **163 instances**, smaller effect -- ~60.6s average, ~101.8s max. Example: two Universal Studios Singapore AEDs, 798.7s (Medium trust) vs. 900.6s (High trust) -- the High-trust one wins despite being ~102s slower.
- **Across every one of the 1909 flips, the maximum walking-time gap involved was 250.1s (~4.2 min)** -- confirming the fix did not reopen the original bug's failure mode (a >20-minute gap being overridden by trust/confidence alone, as in the Sentosa Boardwalk vs. Universal Studios case above). The confidence terms only ever reorder candidates that are already close in walking time; they no longer have enough relative weight to flip a walk that's many minutes longer, which is exactly the intended effect of raising `TIME_DECAY_S`.

**Conclusion:** raising `TIME_DECAY_S` to 1200 fixed the far-apart pathological case (confidence/trust overriding a >2x-longer walk) while *preserving, and if anything strengthening*, the near-tie reordering behavior the 0.15 weights were designed for. No further weight changes indicated.
