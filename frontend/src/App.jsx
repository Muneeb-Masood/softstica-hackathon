import { useEffect, useState } from "react";
import MapView from "./MapView";
import DisclaimerBanner from "./DisclaimerBanner";
import QueryControls from "./QueryControls";
import ResultsPanel from "./ResultsPanel";
import { fetchAeds, fetchRanking } from "./api";
import { BADGE_COLORS } from "./trustBadge";
import "./App.css";

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function nowTimeStr() {
  return new Date().toTimeString().slice(0, 5);
}

function App() {
  const [aeds, setAeds] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [testPoint, setTestPoint] = useState(null);
  const [date, setDate] = useState(todayStr());
  const [time, setTime] = useState(nowTimeStr());
  const [ranking, setRanking] = useState(null);
  const [rankLoading, setRankLoading] = useState(false);
  const [rankError, setRankError] = useState(null);

  useEffect(() => {
    fetchAeds()
      .then((data) => setAeds(data.aeds))
      .catch((err) => setLoadError(err.message));
  }, []);

  const handlePickLocation = (lat, lon) => {
    setTestPoint({ lat, lon });
    setRanking(null);
    setRankError(null);
  };

  const handleRank = async () => {
    if (!testPoint) return;
    setRankLoading(true);
    setRankError(null);
    try {
      const result = await fetchRanking({ lat: testPoint.lat, lon: testPoint.lon, date, time });
      setRanking(result);
    } catch (err) {
      setRankError(err.message);
    } finally {
      setRankLoading(false);
    }
  };

  return (
    <div id="app-shell">
      <header id="app-header">
        <h1>AED Discovery &amp; Routing — Sentosa Prototype</h1>
        <span className="aed-count">{aeds.length} AEDs loaded</span>
      </header>

      <DisclaimerBanner />

      <div id="main-body">
        <aside id="sidebar">
          <QueryControls
            testPoint={testPoint}
            date={date}
            time={time}
            onDateChange={setDate}
            onTimeChange={setTime}
            onSubmit={handleRank}
            submitting={rankLoading}
          />
          <ResultsPanel ranking={ranking} loading={rankLoading} error={rankError} />
        </aside>

        <div id="map-wrap">
          {loadError && <div className="error-banner">Failed to load AEDs: {loadError}</div>}
          <MapView aeds={aeds} testPoint={testPoint} onPickLocation={handlePickLocation} />
          <div className="legend">
            {Object.entries(BADGE_COLORS).map(([label, color]) => (
              <div className="legend-row" key={label}>
                <span className="legend-dot" style={{ background: color }} />
                {label}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
