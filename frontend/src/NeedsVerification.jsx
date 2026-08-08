import { useEffect, useState } from "react";
import { fetchNeedsVerification } from "./api";
import { badgeColor } from "./trustBadge";

function VerificationItem({ item }) {
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
    </li>
  );
}

export default function NeedsVerification() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [openUrgent, setOpenUrgent] = useState(false);
  const [openMedium, setOpenMedium] = useState(false);

  useEffect(() => {
    fetchNeedsVerification()
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div id="needs-verification">
      <h2>Registry quality (Phase 11)</h2>
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
                <VerificationItem key={item.aed_id} item={item} />
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
                    <VerificationItem key={item.aed_id} item={item} />
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
