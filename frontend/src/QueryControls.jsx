import { useEffect, useState } from "react";

const PACE_MIN = 0.3;
const PACE_MAX = 2.5;
const PACE_STEP = 0.01;

export default function QueryControls({
  testPoint,
  date,
  time,
  mobility,
  paceMPerS,
  onDateChange,
  onTimeChange,
  onMobilityChange,
  onPaceChange,
  onManualLocation,
  onSubmit,
  submitting,
}) {
  const [latInput, setLatInput] = useState(testPoint ? testPoint.lat.toFixed(6) : "");
  const [lonInput, setLonInput] = useState(testPoint ? testPoint.lon.toFixed(6) : "");

  // Keep the boxes in sync when the pin moves for any other reason (a map
  // click) so they never show a stale value next to the live pin.
  useEffect(() => {
    if (testPoint) {
      setLatInput(testPoint.lat.toFixed(6));
      setLonInput(testPoint.lon.toFixed(6));
    }
  }, [testPoint]);

  const applyManualLocation = () => {
    const lat = parseFloat(latInput);
    const lon = parseFloat(lonInput);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
    onManualLocation(lat, lon);
  };

  return (
    <>
      <div className="control-row">
        <label>Test location</label>
        <div className="location-inputs">
          <input
            type="number"
            step="0.000001"
            placeholder="Latitude"
            value={latInput}
            onChange={(e) => setLatInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyManualLocation()}
          />
          <input
            type="number"
            step="0.000001"
            placeholder="Longitude"
            value={lonInput}
            onChange={(e) => setLonInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyManualLocation()}
          />
          <button type="button" className="location-go" onClick={applyManualLocation}>
            Go
          </button>
        </div>
        <span className="location-hint">Type coordinates and press Go, or click anywhere on the map</span>
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

      <div className="control-row">
        <label htmlFor="pace-slider">Walking pace</label>
        <div className="pace-slider-row">
          <input
            id="pace-slider"
            type="range"
            min={PACE_MIN}
            max={PACE_MAX}
            step={PACE_STEP}
            value={paceMPerS}
            onChange={(e) => onPaceChange(parseFloat(e.target.value))}
          />
          <span className="pace-value">{paceMPerS.toFixed(2)} m/s</span>
        </div>
      </div>
      <p className="mobility-hint">
        Rescales walking time only (route choice doesn't change). 1.34 m/s
        (~4.8 km/h) is the default &mdash; the same range mainstream
        pedestrian-routing engines default to. Not a personal or measured
        pace &mdash; a documented planning-level assumption you can adjust,
        same honesty standard as everywhere else this tool shows a number.
      </p>

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
