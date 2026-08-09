import { useState } from "react";
import { badgeColor } from "./trustBadge";
import { formatPercent, formatWalkingTime, HOURS_STATUS_LABEL } from "./format";
import { fetchAedDetail } from "./api";

function TrustPill({ badge }) {
  return (
    <span className="pill" style={{ background: badgeColor(badge), color: "#fff" }}>
      {badge}
    </span>
  );
}

// The trust badge is a rule-based data-quality signal (floor recorded?
// description has a concrete landmark? hours parsed cleanly? any access
// barrier?) -- CLAUDE.md requires the tool to "show uncertainty," so the
// badge is never shown without saying which of those checks it failed.
function TrustReasons({ badge, reasons }) {
  if (!reasons || reasons.length === 0) {
    return (
      <p className="trust-reasons trust-reasons-clean">
        {badge}: floor level recorded, location description has a concrete landmark,
        operating hours parsed cleanly, and no access barrier was flagged.
      </p>
    );
  }
  return (
    <div className="trust-reasons">
      <span className="trust-reasons-label">Why {badge}:</span>
      <ul>
        {reasons.map((reason, i) => (
          <li key={i}>{reason}</li>
        ))}
      </ul>
    </div>
  );
}

// Explanations for #1..#5 arrive pre-fetched in one batch call (see
// backend/explanation.py generate_explanation). Anything beyond that, or
// any excluded (closed/unreachable) card, has no pre-fetched text -- the
// "Why this rank?" button fetches it on demand via /aed/{id}/detail, which
// works for ANY AED regardless of rank position (see backend AedDetail
// work: 098269-010 never lands in a top-5 view, so this on-demand lookup
// is the only honest way to show its justification).
function preloadedExplanationFor(aed, index, explanations) {
  if (!explanations) return null;
  if (index === 0) return explanations.top_explanation ?? null;
  const match = explanations.comparisons?.find((c) => c.aed_id === aed.aed_id);
  return match?.explanation ?? null;
}

function WhyButton({ state, onClick }) {
  const label = state?.loading
    ? "Loading…"
    : state?.expanded
    ? "Hide justification"
    : "Why this rank?";
  return (
    <button
      type="button"
      className="why-button"
      onClick={onClick}
      disabled={state?.loading}
    >
      {label}
    </button>
  );
}

function ExplanationBlock({ state, preloadedExplanation }) {
  if (!state?.expanded) return null;
  if (state.loading) return <p className="result-explanation">Fetching justification…</p>;
  if (state.error) return <p className="result-explanation result-explanation-error">Couldn't load justification: {state.error}</p>;

  const text = preloadedExplanation ?? state.explanation;
  if (!text) return <p className="result-explanation result-explanation-error">No justification text available for this AED.</p>;

  return (
    <>
      <p className="result-explanation">{text}</p>
      {state.rank != null && state.totalRanked != null && (
        <p className="result-explanation-note">
          Looked up directly: ranked #{state.rank} of {state.totalRanked} for this location/time.
        </p>
      )}
    </>
  );
}

// Deterministic, always-shown (never gated behind "Why this rank?") -- the
// backend computes this straight from OSM-tag-derived route facts
// (uses_stairs, crosses_unmarked_road, uses_permissive_access), not from
// Gemini, precisely so a stairs warning can never be missed or paraphrased
// away. See backend/main.py _mobility_warning.
function MobilityWarning({ text }) {
  if (!text) return null;
  return <p className="mobility-warning">{text}</p>;
}

function RankedCard({ rank, aed, index, explanations, note, query, detailState, onToggle }) {
  const preloadedExplanation = preloadedExplanationFor(aed, index, explanations);
  const state = detailState[aed.aed_id];

  return (
    <div className="result-card" style={{ borderLeft: `4px solid ${badgeColor(aed.trust_badge)}` }}>
      <div className="result-card-head">
        <span className="rank-number">#{rank}</span>
        <span className="result-name">{aed.building_name || aed.aed_id}</span>
        <TrustPill badge={aed.trust_badge} />
      </div>
      <div className="result-subscores">
        <span>Walk: {formatWalkingTime(aed.walking_time_s)}</span>
        <span>Distance confidence: {formatPercent(aed.distance_confidence)}</span>
        <span>{HOURS_STATUS_LABEL[aed.hours_status] || aed.hours_status}</span>
        <span>Hours confidence: {formatPercent(aed.hours_confidence)}</span>
        <span>Score: {aed.final_score.toFixed(3)}</span>
        {aed.uses_stairs && <span className="route-flag route-flag-stairs">Includes stairs</span>}
        {aed.crosses_unmarked_road && <span className="route-flag route-flag-road">Unmarked road crossing</span>}
        {aed.uses_permissive_access && <span className="route-flag route-flag-permissive">Permissive-access path</span>}
      </div>
      <MobilityWarning text={aed.mobility_warning} />
      <TrustReasons badge={aed.trust_badge} reasons={aed.trust_badge_reasons} />
      {index === 0 && note && <p className="result-explanation-note">{note}</p>}
      <WhyButton state={state} onClick={() => onToggle(aed, preloadedExplanation, query)} />
      <ExplanationBlock state={state} preloadedExplanation={preloadedExplanation} />
    </div>
  );
}

function ExcludedCard({ aed, query, detailState, onToggle }) {
  const state = detailState[aed.aed_id];
  return (
    <div className="result-card excluded-card">
      <div className="result-card-head">
        <span className="pill pill-excluded">
          {aed.reason === "closed"
            ? "Closed now"
            : aed.mobility_note
            ? "No stairs-free route"
            : "Unreachable"}
        </span>
        <span className="result-name">{aed.building_name || aed.aed_id}</span>
      </div>
      <div className="result-subscores">
        {aed.reason === "closed" && (
          <span>Walk: {formatWalkingTime(aed.walking_time_s)}</span>
        )}
        {aed.reason === "unreachable" && !aed.mobility_note && <span>No path found on the walking network</span>}
        {aed.mobility_note && <span>{aed.mobility_note}</span>}
      </div>
      <WhyButton state={state} onClick={() => onToggle(aed, null, query)} />
      <ExplanationBlock state={state} preloadedExplanation={null} />
    </div>
  );
}

export default function ResultsPanel({ ranking, loading, error }) {
  const [detailState, setDetailState] = useState({});
  // Collapsed by default so a long ranked/excluded list doesn't force
  // scrolling past every card to reach the Time Slider / Crowd Simulation /
  // Registry Quality sections below in the sidebar -- opt-in expand instead.
  const [rankedOpen, setRankedOpen] = useState(false);
  const [excludedOpen, setExcludedOpen] = useState(false);

  if (loading) return <div className="results-status">Ranking AEDs…</div>;
  if (error) return <div className="results-status results-error">{error}</div>;
  if (!ranking) return null;

  const { ranked, excluded, explanations, query } = ranking;

  async function handleToggle(aed, preloadedExplanation, q) {
    const existing = detailState[aed.aed_id];
    const expanded = !existing?.expanded;
    setDetailState((prev) => ({
      ...prev,
      [aed.aed_id]: { ...prev[aed.aed_id], expanded },
    }));

    // Already have the text (preloaded from the batch call, or fetched
    // earlier this session) -- nothing more to do, just toggled visibility.
    if (preloadedExplanation != null) return;
    if (existing?.explanation || existing?.loading) return;
    if (!q) return;

    setDetailState((prev) => ({
      ...prev,
      [aed.aed_id]: { ...prev[aed.aed_id], loading: true, error: null },
    }));
    try {
      const data = await fetchAedDetail({
        aedId: aed.aed_id,
        lat: q.lat,
        lon: q.lon,
        date: q.date,
        time: q.time,
        mobility: q.mobility,
      });
      setDetailState((prev) => ({
        ...prev,
        [aed.aed_id]: {
          ...prev[aed.aed_id],
          loading: false,
          explanation: data.detail.explanation,
          rank: data.detail.rank,
          totalRanked: data.detail.total_ranked,
        },
      }));
    } catch (err) {
      setDetailState((prev) => ({
        ...prev,
        [aed.aed_id]: { ...prev[aed.aed_id], loading: false, error: err.message },
      }));
    }
  }

  return (
    <div className="results-subpanel">
      <button
        type="button"
        className="results-collapse-toggle"
        onClick={() => setRankedOpen((o) => !o)}
        aria-expanded={rankedOpen}
      >
        <span className="subheading">Ranked AEDs ({ranked.length})</span>
        <span className="app-section-chevron">{rankedOpen ? "▾" : "▸"}</span>
      </button>
      {rankedOpen && (
        <div className="results-list">
          {ranked.length === 0 && (
            <div className="results-status">
              No AEDs are open and reachable for this location/date/time. See the excluded list below.
            </div>
          )}
          {ranked.map((aed, i) => (
            <RankedCard
              key={aed.aed_id}
              rank={i + 1}
              index={i}
              aed={aed}
              explanations={explanations}
              note={explanations?.note}
              query={query}
              detailState={detailState}
              onToggle={handleToggle}
            />
          ))}
        </div>
      )}

      <button
        type="button"
        className="results-collapse-toggle excluded-heading"
        onClick={() => setExcludedOpen((o) => !o)}
        aria-expanded={excludedOpen}
      >
        <span className="subheading">
          Excluded ({excluded.length}) — closed or unreachable, not silently dropped
        </span>
        <span className="app-section-chevron">{excludedOpen ? "▾" : "▸"}</span>
      </button>
      {excludedOpen && (
        <div className="results-list">
          {excluded.length === 0 ? (
            <div className="results-status">None excluded for this query.</div>
          ) : (
            excluded.map((aed) => (
              <ExcludedCard
                key={aed.aed_id}
                aed={aed}
                query={query}
                detailState={detailState}
                onToggle={handleToggle}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}
