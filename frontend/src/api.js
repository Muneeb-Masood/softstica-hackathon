export const API_BASE = "http://127.0.0.1:8000";

export async function fetchAeds() {
  const res = await fetch(`${API_BASE}/aeds`);
  if (!res.ok) throw new Error(`GET /aeds failed: ${res.status}`);
  return res.json();
}

export async function fetchRanking({ lat, lon, date, time, mobility = "walk" }) {
  const res = await fetch(`${API_BASE}/rank`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, date, time, mobility }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`POST /rank failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export async function fetchTimeline({ lat, lon, date, mobility = "walk" }) {
  const res = await fetch(`${API_BASE}/rank/timeline`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, date, mobility }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`POST /rank/timeline failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export async function fetchNeedsVerification() {
  const res = await fetch(`${API_BASE}/aeds/needs-verification`);
  if (!res.ok) throw new Error(`GET /aeds/needs-verification failed: ${res.status}`);
  return res.json();
}

export async function fetchTrustExplanation(aedId) {
  const res = await fetch(`${API_BASE}/aeds/${encodeURIComponent(aedId)}/trust-explanation`);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`GET /aeds/${aedId}/trust-explanation failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export async function fetchAedDetail({ aedId, lat, lon, date, time, mobility = "walk" }) {
  const params = new URLSearchParams({ lat, lon, date, time, mobility });
  const res = await fetch(`${API_BASE}/aed/${encodeURIComponent(aedId)}/detail?${params}`);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`GET /aed/${aedId}/detail failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export async function fetchCrowdSimulation({ buildingName, date, time, nPerSide }) {
  const res = await fetch(`${API_BASE}/crowd-simulation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      building_name: buildingName,
      date,
      time,
      n_per_side: nPerSide,
    }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`POST /crowd-simulation failed: ${res.status} ${detail}`);
  }
  return res.json();
}
