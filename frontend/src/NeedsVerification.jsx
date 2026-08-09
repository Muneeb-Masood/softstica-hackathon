import { useEffect, useState } from "react";
import { fetchNeedsVerification, fetchTrustExplanation } from "./api";
import { badgeColor } from "./trustBadge";

// The per-component reasons (item.reasons) come from trust_score.py's
// fixed template strings -- honest, but identical wording for any two
// records dinged for the same reason (e.g. every vague-description AED
// reads the same). This button fetches a personalized explanation instead,
// grounded in THIS record's actual description/hours text (backend
// explanation.generate_trust_explanation), fetched on demand and cached
// per AED server-side.
function WhyBadgeButton({ item, explanationState, onToggle }) {
  const label = explanationState?.loading
    ? "Loading…"
    : explanationState?.expanded
    ? "Hide personalized reason"
    : `Why ${item.trust_badge}?`;
  return (
    <button
      type="button"
      className="why-button"
      onClick={() => onToggle(item)}
      disabled={explanationState?.loading}
    >
      {label}
    </button>
  );
}

function VerificationItem({ item, explanationState, onToggleExplanation }) {
  return (
    <li className="needs-verification-item">
      <div className="needs-verification-item-head">
        <span
          className="pill"
          style={{ background: badgeColor(item.trust_badge), color: "#fff" }}
        >
          {item.trust_badge}
        </span>
        <span className="needs-verification-name">{item.building_name || item.aed_id}</span>
        <span className="needs-verification-id">{item.aed_id}</span>
      </div>
      <div className="needs-verification-detail">
        Floor: {item.floor_level || "—"} · Description: {item.description || "(blank)"}
      </div>
      <ul className="needs-verification-reasons">
        {item.reasons.map((reason, i) => (
          <li key={i}>{reason}</li>
        ))}
      </ul>
      <WhyBadgeButton item={item} explanationState={explanationState} onToggle={onToggleExplanation} />
      {explanationState?.expanded && (
        explanationState.error ? (
          <p className="result-explanation result-explanation-error">
            Couldn't load explanation: {explanationState.error}
          </p>
        ) : explanationState.text ? (
          <p className="result-explanation">{explanationState.text}</p>
        ) : null
      )}
    </li>
  );
}

export default function NeedsVerification() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [openUrgent, setOpenUrgent] = useState(false);
  const [openMedium, setOpenMedium] = useState(false);
  const [explanations, setExplanations] = useState({});

  useEffect(() => {
    fetchNeedsVerification()
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  async function handleToggleExplanation(item) {
    const existing = explanations[item.aed_id];
    const expanded = !existing?.expanded;
    setExplanations((prev) => ({
      ...prev,
      [item.aed_id]: { ...prev[item.aed_id], expanded },
    }));

    if (existing?.text || existing?.loading) return;

    setExplanations((prev) => ({
      ...prev,
      [item.aed_id]: { ...prev[item.aed_id], loading: true, error: null },
    }));
    try {
      const res = await fetchTrustExplanation(item.aed_id);
      setExplanations((prev) => ({
        ...prev,
        [item.aed_id]: { ...prev[item.aed_id], loading: false, text: res.trust_explanation },
      }));
    } catch (err) {
      setExplanations((prev) => ({
        ...prev,
        [item.aed_id]: { ...prev[item.aed_id], loading: false, error: err.message },
      }));
    }
  }

  return (
    <>
      <p className="needs-verification-note">
        AEDs whose location description and/or operating-hours text scored too low on the
        Phase 3 trust check to rely on as-is. This is a data-quality list for re-survey, not a
        ranking -- it does not depend on the test location, date, or time above.
      </p>

      {error && <div className="results-status results-error">{error}</div>}

      {data && (
        <>
          <div className="needs-verification-summary">
            <strong>{data.needs_verification_count}</strong> of {data.total_aeds} Sentosa AEDs
            flagged "Needs Verification" (
            {data.badge_counts.High} High · {data.badge_counts.Medium} Medium ·{" "}
            {data.badge_counts["Needs Verification"]} Needs Verification)
          </div>

          {data.needs_verification_count > 0 && (
            <button
              type="button"
              id="needs-verification-toggle"
              onClick={() => setOpenUrgent((o) => !o)}
            >
              {openUrgent ? "Hide urgent list" : "Show urgent list"}
            </button>
          )}

          {openUrgent && (
            <ul className="needs-verification-list">
              {data.items.map((item) => (
                <VerificationItem
                  key={item.aed_id}
                  item={item}
                  explanationState={explanations[item.aed_id]}
                  onToggleExplanation={handleToggleExplanation}
                />
              ))}
            </ul>
          )}

          {data.medium_count > 0 && (
            <div className="needs-verification-secondary">
              <p className="needs-verification-note">
                Lower priority: these {data.medium_count} AEDs have no accessibility-risk
                component (no vague-only description or unparseable hours) -- just one field
                short of a perfect record, e.g. a missing floor level.
              </p>
              <button
                type="button"
                id="needs-verification-medium-toggle"
                onClick={() => setOpenMedium((o) => !o)}
              >
                {openMedium ? "Hide Medium list" : "Show Medium list"}
              </button>

              {openMedium && (
                <ul className="needs-verification-list needs-verification-list-secondary">
                  {data.medium_items.map((item) => (
                    <VerificationItem
                      key={item.aed_id}
                      item={item}
                      explanationState={explanations[item.aed_id]}
                      onToggleExplanation={handleToggleExplanation}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </>
  );
}
