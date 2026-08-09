import { useEffect, useState } from "react";
import MapView from "./MapView";
import DisclaimerBanner from "./DisclaimerBanner";
import QueryControls from "./QueryControls";
import ResultsPanel from "./ResultsPanel";
import TimeSlider from "./TimeSlider";
import CrowdSimulation from "./CrowdSimulation";
import NeedsVerification from "./NeedsVerification";
import OnboardingModal from "./OnboardingModal";
import Section from "./Section";
import ChatPanel from "./ChatPanel";
import { fetchAeds, fetchCrowdSimulation, fetchRanking, fetchRoute, fetchTimeline } from "./api";
import { BADGE_COLORS } from "./trustBadge";
import "./App.css";

const ONBOARDING_SEEN_KEY = "aed_onboarding_seen";

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function nowTimeStr() {
  return new Date().toTimeString().slice(0, 5);
}

function App() {
  const [showOnboarding, setShowOnboarding] = useState(
    () => typeof window !== "undefined" && !window.localStorage.getItem(ONBOARDING_SEEN_KEY)
  );
  const [aeds, setAeds] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [testPoint, setTestPoint] = useState(null);
  const [date, setDate] = useState(todayStr());
  const [time, setTime] = useState(nowTimeStr());
  const [mobility, setMobility] = useState("walk");
  const [paceMPerS, setPaceMPerS] = useState(1.34);
  const [ranking, setRanking] = useState(null);
  const [rankLoading, setRankLoading] = useState(false);
  const [rankError, setRankError] = useState(null);

  const [timeline, setTimeline] = useState(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState(null);
  const [sliderHour, setSliderHour] = useState(() => Number(nowTimeStr().slice(0, 2)));

  const [crowdResult, setCrowdResult] = useState(null);
  const [crowdLoading, setCrowdLoading] = useState(false);
  const [crowdError, setCrowdError] = useState(null);

  const [routeData, setRouteData] = useState(null);
  // Which ranked AED's route is drawn on the map -- null means "defer to
  // the #1 pick" (see activeAedId below). Reset to null whenever a new
  // ranking arrives so a fresh query starts back on its own #1 pick
  // instead of carrying over a selection from the previous query.
  const [selectedAedId, setSelectedAedId] = useState(null);

  // One id per page load, used only to key the backend's per-session chat
  // message cap (chat_qa.py) -- not an auth token, not persisted.
  const [chatSessionId] = useState(() =>
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `session-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );

  useEffect(() => {
    fetchAeds()
      .then((data) => setAeds(data.aeds))
      .catch((err) => setLoadError(err.message));
  }, []);

  // Phase 9: precompute all 24 hourly rankings for the current location/date
  // the moment either changes -- this is cheap local computation, capped
  // Gemini explanation cost (see backend/explanation.py), so it's safe to
  // run automatically rather than waiting for the user to touch the slider.
  useEffect(() => {
    if (!testPoint) return;
    let cancelled = false;

    async function run() {
      if (cancelled) return;
      setTimelineLoading(true);
      setTimelineError(null);
      try {
        const data = await fetchTimeline({ lat: testPoint.lat, lon: testPoint.lon, date, mobility, paceMPerS });
        if (cancelled) return;
        setTimeline(data);
        setSliderHour(Number(time.slice(0, 2)));
      } catch (err) {
        if (!cancelled) setTimelineError(err.message);
      } finally {
        if (!cancelled) setTimelineLoading(false);
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [testPoint, date, time, mobility, paceMPerS]);

  // A fresh /rank result starts back on its own #1 pick, not whatever card
  // happened to be selected for the previous query.
  useEffect(() => {
    setSelectedAedId(null);
  }, [ranking]);

  const activeAedId = selectedAedId ?? ranking?.ranked?.[0]?.aed_id ?? null;

  // Draws the currently-active AED's real walking route (plus the
  // straight-line baseline) on the map -- the #1 pick by default, or
  // whichever ranked card the user clicked (see selectedAedId). Tied to the
  // main "Find AEDs" result only, not every time-slider drag, so dragging
  // the slider doesn't fire a route request per tick.
  useEffect(() => {
    if (!testPoint || !activeAedId) {
      setRouteData(null);
      return;
    }
    let cancelled = false;
    fetchRoute({ aedId: activeAedId, lat: testPoint.lat, lon: testPoint.lon, mobility, paceMPerS })
      .then((data) => {
        if (!cancelled) setRouteData(data.route);
      })
      .catch(() => {
        if (!cancelled) setRouteData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [activeAedId, testPoint, mobility, paceMPerS]);

  const sliderHourData = timeline?.hours.find((h) => h.hour === sliderHour) ?? null;
  const sliderRanking = sliderHourData && {
    ranked: sliderHourData.ranked,
    excluded: sliderHourData.excluded,
    query: testPoint && { lat: testPoint.lat, lon: testPoint.lon, date, time: sliderHourData.time, mobility, pace_m_per_s: paceMPerS },
    explanations: sliderHourData.explanation && {
      top_explanation: sliderHourData.explanation.top_explanation,
      comparisons: sliderHourData.explanation.comparisons,
      note: sliderHourData.explanation.note,
    },
  };

  const handlePickLocation = (lat, lon) => {
    setTestPoint({ lat, lon });
    setRanking(null);
    setRankError(null);
    setTimeline(null);
    setTimelineError(null);
  };

  const handleMobilityChange = (value) => {
    setMobility(value);
    // Previous results were ranked under the old profile -- clear them
    // rather than leave a stale walk-mode ranking on screen labeled as if
    // it reflects the newly selected mobility setting.
    setRanking(null);
    setRankError(null);
  };

  const handlePaceChange = (value) => {
    setPaceMPerS(value);
    // Same reasoning as handleMobilityChange -- a stale ranking computed at
    // the old pace shouldn't stay on screen labeled as if it reflects the
    // newly selected one.
    setRanking(null);
    setRankError(null);
  };

  const handleRank = async () => {
    if (!testPoint) return;
    setRankLoading(true);
    setRankError(null);
    try {
      const result = await fetchRanking({ lat: testPoint.lat, lon: testPoint.lon, date, time, mobility, paceMPerS });
      setRanking(result);
    } catch (err) {
      setRankError(err.message);
    } finally {
      setRankLoading(false);
    }
  };

  const handleRunCrowdSimulation = async ({ buildingName, date: d, time: t }) => {
    setCrowdLoading(true);
    setCrowdError(null);
    try {
      const result = await fetchCrowdSimulation({ buildingName, date: d, time: t, nPerSide: 8 });
      setCrowdResult(result);
    } catch (err) {
      setCrowdError(err.message);
    } finally {
      setCrowdLoading(false);
    }
  };

  const handleCloseOnboarding = () => {
    setShowOnboarding(false);
    window.localStorage.setItem(ONBOARDING_SEEN_KEY, "1");
  };

  return (
    <div id="app-shell">
      {showOnboarding && <OnboardingModal onClose={handleCloseOnboarding} />}

      <header id="app-header">
        <h1>AED Discovery &amp; Routing — Sentosa Prototype</h1>
        <div className="header-right">
          <span className="aed-count">{aeds.length} AEDs loaded</span>
          <button
            type="button"
            id="help-button"
            title="How to use this tool"
            aria-label="How to use this tool"
            onClick={() => setShowOnboarding(true)}
          >
            ?
          </button>
        </div>
      </header>

      <DisclaimerBanner />

      <div id="main-body">
        <aside id="sidebar">
          <Section
            title="Find AEDs"
            helperText="Pick a location on the map, set a date and time, then rank nearby AEDs by real walking distance, operating hours, and data trust."
          >
            <QueryControls
              testPoint={testPoint}
              date={date}
              time={time}
              mobility={mobility}
              paceMPerS={paceMPerS}
              onDateChange={setDate}
              onTimeChange={setTime}
              onMobilityChange={handleMobilityChange}
              onPaceChange={handlePaceChange}
              onManualLocation={handlePickLocation}
              onSubmit={handleRank}
              submitting={rankLoading}
            />
            <ResultsPanel
              ranking={ranking}
              loading={rankLoading}
              error={rankError}
              selectedAedId={activeAedId}
              onSelectAed={setSelectedAedId}
            />
          </Section>

          <Section
            title="Time Slider"
            helperText="Drag through the hours of the day to see how the ranking for your chosen location shifts as operating hours change. Set a location and click Find AEDs first."
          >
            <TimeSlider
              timeline={timeline}
              loading={timelineLoading}
              error={timelineError}
              hour={sliderHour}
              onHourChange={setSliderHour}
            />
            {timeline && (
              <ResultsPanel ranking={sliderRanking} loading={false} error={null} />
            )}
          </Section>

          <Section
            title="Crowd Simulation"
            helperText="Spreads simulated starting points across a busy attraction and tallies which AED wins most often, revealing a potential bottleneck. Simulated points only, not real footfall."
            defaultOpen={false}
          >
            <CrowdSimulation
              aeds={aeds}
              date={date}
              time={time}
              onRun={handleRunCrowdSimulation}
              result={crowdResult}
              loading={crowdLoading}
              error={crowdError}
            />
          </Section>

          <Section
            title="Registry Quality"
            helperText="Badges show how confidently each AED's location and hours could be verified from the data — 'Needs Verification' means it should be re-surveyed."
            defaultOpen={false}
          >
            <NeedsVerification />
          </Section>
        </aside>

        <div id="map-wrap">
          {loadError && <div className="error-banner">Failed to load AEDs: {loadError}</div>}
          <MapView
            aeds={aeds}
            testPoint={testPoint}
            onPickLocation={handlePickLocation}
            crowdResult={crowdResult}
            route={routeData}
          />
          <div className="legend">
            {Object.entries(BADGE_COLORS).map(([label, color]) => (
              <div className="legend-row" key={label}>
                <span className="legend-dot" style={{ background: color }} />
                {label}
              </div>
            ))}
            {routeData && (
              <>
                <div className="legend-row">
                  <span className="legend-line legend-line-route" />
                  Simulated route to selected AED (not live navigation)
                </div>
                <div className="legend-row">
                  <span className="legend-line legend-line-baseline" />
                  Straight-line baseline (ignores walkability)
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {ranking && ranking.ranked && ranking.ranked.length > 0 && (
        <ChatPanel context={ranking} timeline={timeline} sessionId={chatSessionId} />
      )}
    </div>
  );
}

export default App;
