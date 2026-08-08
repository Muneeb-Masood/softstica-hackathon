import { useMemo, useState } from "react";
import { formatPercent } from "./format";

const DEFAULT_ATTRACTION = "Universal Studios Singapore";

function attractionOptions(aeds) {
  const counts = new Map();
  for (const aed of aeds) {
    const name = aed.building_name;
    if (!name) continue;
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return [...counts.entries()]
    .filter(([, count]) => count >= 2)
    .sort((a, b) => b[1] - a[1]);
}

export default function CrowdSimulation({ aeds, date, time, onRun, result, loading, error }) {
  const options = useMemo(() => attractionOptions(aeds), [aeds]);
  const [attraction, setAttraction] = useState(DEFAULT_ATTRACTION);

  const hasAttraction = options.some(([name]) => name === attraction);

  return (
    <div id="crowd-simulation">
      <h2>Crowd simulation (Phase 10)</h2>
      <p className="crowd-sim-note">
        Runs the ranking pipeline for a grid of SIMULATED starting points spread across
        one attraction's footprint and tallies which AED comes out #1 most often. Not
        real footfall, queue, or incident data.
      </p>

      <div className="control-row">
        <label htmlFor="crowd-attraction">Attraction</label>
        <select
          id="crowd-attraction"
          value={hasAttraction ? attraction : DEFAULT_ATTRACTION}
          onChange={(e) => setAttraction(e.target.value)}
        >
          {options.map(([name, count]) => (
            <option key={name} value={name}>
              {name} ({count} AEDs)
            </option>
          ))}
        </select>
      </div>

      <button
        id="crowd-sim-button"
        type="button"
        disabled={loading || options.length === 0}
        onClick={() => onRun({ buildingName: attraction, date, time })}
      >
        {loading ? "Simulating…" : "Run crowd simulation"}
      </button>

      {error && <div className="results-status results-error">{error}</div>}

      {result && (
        <div className="crowd-sim-result">
          <div className="crowd-sim-headline">
            <strong>{result.n_points} simulated points</strong> across {result.attraction}
          </div>

          {result.bottleneck ? (
            <div className="crowd-sim-bottleneck">
              Most frequent #1 pick:{" "}
              <strong>{result.bottleneck.building_name || result.bottleneck.aed_id}</strong>{" "}
              — won {result.bottleneck.win_count}/{result.n_points} points (
              {formatPercent(result.bottleneck.win_fraction)})
            </div>
          ) : (
            <div className="crowd-sim-bottleneck">No AED won any simulated point.</div>
          )}

          {result.indoor_snap_degenerate && (
            <p className="crowd-sim-warning">
              Data-coverage note: this attraction's interior has little to no mapped
              pedestrian pathway data in OpenStreetMap, so many of the {result.n_points}{" "}
              simulated points snapped to the same handful of street nodes ({result.distinct_snap_nodes}{" "}
              distinct nodes, ~{result.avg_points_per_snap_node} points/node). The bottleneck
              above reflects that coverage gap as much as real geography — treat it as a
              registry/mapping-quality finding, not a precise crowd result.
            </p>
          )}

          <ul className="crowd-sim-tally">
            {result.tally.slice(0, 5).map((t) => (
              <li key={t.aed_id}>
                {t.building_name ? `${t.building_name} (${t.aed_id})` : t.aed_id} — {t.win_count} (
                {formatPercent(t.win_fraction)})
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
