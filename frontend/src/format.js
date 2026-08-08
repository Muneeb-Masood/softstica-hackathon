export function formatWalkingTime(seconds) {
  if (seconds == null) return "unknown";
  const total = Math.round(seconds);
  const min = Math.floor(total / 60);
  const sec = total % 60;
  if (min === 0) return `${sec}s`;
  return `${min}m ${sec}s`;
}

export function formatPercent(fraction) {
  if (fraction == null) return "unknown";
  return `${Math.round(fraction * 100)}%`;
}

export const HOURS_STATUS_LABEL = {
  open: "Open",
  closed: "Closed",
  unknown: "Hours unknown",
};
