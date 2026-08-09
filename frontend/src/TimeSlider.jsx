function hourLabel(h) {
  return `${String(h).padStart(2, "0")}:00`;
}

export default function TimeSlider({ timeline, loading, error, hour, onHourChange }) {
  if (loading) return <div className="results-status">Precomputing all 24 hours…</div>;
  if (error) return <div className="results-status results-error">{error}</div>;
  if (!timeline) return null;

  const { stats } = timeline;
  const segmentBoundaries = [];
  let lastTop = null;
  for (const h of timeline.hours) {
    const top = h.ranked[0]?.aed_id ?? null;
    if (top !== lastTop) {
      segmentBoundaries.push(h.hour);
      lastTop = top;
    }
  }

  return (
    <div className="time-slider-inner">
      <h2 className="subheading">Explore across the day — {timeline.query.date}</h2>
      <div className="slider-row">
        <span className="slider-hour-label">{hourLabel(hour)}</span>
        <input
          type="range"
          min={0}
          max={23}
          step={1}
          value={hour}
          onChange={(e) => onHourChange(Number(e.target.value))}
        />
      </div>
      {segmentBoundaries.length > 1 && (
        <div className="slider-ticks">
          #1 pick changes at: {segmentBoundaries.filter((h) => h !== 0).map(hourLabel).join(", ")}
        </div>
      )}
      <p className="slider-note">
        24 hourly rankings precomputed locally (no extra cost). The #1 pick changed{" "}
        {stats.segments - 1} time{stats.segments - 1 === 1 ? "" : "s"} across the day —{" "}
        {stats.gemini_calls} new explanation{stats.gemini_calls === 1 ? "" : "s"} generated,{" "}
        {stats.cache_hits} reused from cache
        {stats.capped_segments > 0 ? `, ${stats.capped_segments} further change(s) capped (see note below)` : ""}.
      </p>
    </div>
  );
}
