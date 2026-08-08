export const BADGE_COLORS = {
  High: "#1e8e3e",
  Medium: "#f2a600",
  "Needs Verification": "#d93025",
};

export function badgeColor(badge) {
  return BADGE_COLORS[badge] || "#6b6375";
}
