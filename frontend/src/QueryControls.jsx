export default function QueryControls({
  testPoint,
  date,
  time,
  mobility,
  onDateChange,
  onTimeChange,
  onMobilityChange,
  onSubmit,
  submitting,
}) {
  return (
    <>
      <div className="control-row">
        <label>Test location</label>
        {testPoint ? (
          <span className="location-value">
            {testPoint.lat.toFixed(5)}, {testPoint.lon.toFixed(5)}
          </span>
        ) : (
          <span className="location-hint">Click anywhere on the map to set a test location</span>
        )}
      </div>

      <div className="control-row">
        <label htmlFor="test-date">Test date</label>
        <input
          id="test-date"
          type="date"
          value={date}
          onChange={(e) => onDateChange(e.target.value)}
        />
      </div>

      <div className="control-row">
        <label htmlFor="test-time">Test time</label>
        <input
          id="test-time"
          type="time"
          value={time}
          onChange={(e) => onTimeChange(e.target.value)}
        />
      </div>

      <div className="control-row">
        <label>Mobility</label>
        <div className="mobility-toggle" role="radiogroup" aria-label="Mobility profile">
          <button
            type="button"
            className={`mobility-option ${mobility === "walk" ? "active" : ""}`}
            aria-pressed={mobility === "walk"}
            onClick={() => onMobilityChange("walk")}
          >
            Walking
          </button>
          <button
            type="button"
            className={`mobility-option ${mobility === "wheelchair" ? "active" : ""}`}
            aria-pressed={mobility === "wheelchair"}
            onClick={() => onMobilityChange("wheelchair")}
          >
            Wheelchair (avoid stairs)
          </button>
        </div>
      </div>
      {mobility === "wheelchair" && (
        <p className="mobility-hint">
          Routes are computed with every OSM-tagged staircase excluded. AEDs only
          reachable via stairs are moved to the excluded list, not silently scored
          as reachable. This is based on OSM's own tags, not a verified
          accessibility survey.
        </p>
      )}

      <button
        id="rank-button"
        type="button"
        disabled={!testPoint || submitting}
        onClick={onSubmit}
      >
        {submitting ? "Ranking…" : "Find AEDs"}
      </button>
    </>
  );
}
