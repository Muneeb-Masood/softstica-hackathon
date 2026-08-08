import { badgeColor } from "./trustBadge";
import { formatPercent, formatWalkingTime, HOURS_STATUS_LABEL } from "./format";

function TrustPill({ badge }) {
  return (
    <span className="pill" style={{ background: badgeColor(badge), color: "#fff" }}>
      {badge}
    </span>
  );
}

function RankedCard({ rank, aed, explanation, note }) {
  return (
    <div className="result-card">
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
      </div>
      {explanation && <p className="result-explanation">{explanation}</p>}
      {note && <p className="result-explanation-note">{note}</p>}
    </div>
  );
}

function ExcludedCard({ aed }) {
  return (
    <div className="result-card excluded-card">
      <div className="result-card-head">
        <span className="pill pill-excluded">{aed.reason === "closed" ? "Closed now" : "Unreachable"}</span>
        <span className="result-name">{aed.building_name || aed.aed_id}</span>
      </div>
      <div className="result-subscores">
        {aed.reason === "closed" && (
          <span>Walk: {formatWalkingTime(aed.walking_time_s)}</span>
        )}
        {aed.reason === "unreachable" && <span>No path found on the walking network</span>}
      </div>
    </div>
  );
}

export default function ResultsPanel({ ranking, loading, error }) {
  if (loading) return <div className="results-status">Ranking AEDs…</div>;
  if (error) return <div className="results-status results-error">{error}</div>;
  if (!ranking) return null;

  const { ranked, excluded, explanations } = ranking;

  return (
    <div id="results-panel">
      <h2>Ranked AEDs ({ranked.length})</h2>
      {ranked.length === 0 && (
        <div className="results-status">
          No AEDs are open and reachable for this location/date/time. See the excluded list below.
        </div>
      )}
      {ranked.map((aed, i) => (
        <RankedCard
          key={aed.aed_id}
          rank={i + 1}
          aed={aed}
          explanation={
            i === 0
              ? explanations?.top_explanation
              : i === 1
              ? explanations?.runnerup_explanation
              : null
          }
          note={i === 0 ? explanations?.note : null}
        />
      ))}

      <h2 className="excluded-heading">
        Excluded ({excluded.length}) — closed or unreachable, not silently dropped
      </h2>
      {excluded.length === 0 ? (
        <div className="results-status">None excluded for this query.</div>
      ) : (
        excluded.map((aed) => <ExcludedCard key={aed.aed_id} aed={aed} />)
      )}
    </div>
  );
}
