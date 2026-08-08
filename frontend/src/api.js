export const API_BASE = "http://127.0.0.1:8000";

export async function fetchAeds() {
  const res = await fetch(`${API_BASE}/aeds`);
  if (!res.ok) throw new Error(`GET /aeds failed: ${res.status}`);
  return res.json();
}

export async function fetchRanking({ lat, lon, date, time }) {
  const res = await fetch(`${API_BASE}/rank`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, date, time }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`POST /rank failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export async function fetchTimeline({ lat, lon, date }) {
  const res = await fetch(`${API_BASE}/rank/timeline`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, date }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`POST /rank/timeline failed: ${res.status} ${detail}`);
  }
  return res.json();
}
