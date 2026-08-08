export default function QueryControls({
  testPoint,
  date,
  time,
  onDateChange,
  onTimeChange,
  onSubmit,
  submitting,
}) {
  return (
    <div id="query-controls">
      <h2>Test query</h2>

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

      <button
        id="rank-button"
        type="button"
        disabled={!testPoint || submitting}
        onClick={onSubmit}
      >
        {submitting ? "Ranking…" : "Find AEDs"}
      </button>
    </div>
  );
}
