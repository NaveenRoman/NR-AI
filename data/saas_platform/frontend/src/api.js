const API_BASE = 'http://localhost:8000/api';

export async function fetchMetrics() {
  try {
    const res = await fetch(`${API_BASE}/metrics`);
    if (res.ok) return await res.json();
  } catch (err) {
    console.warn('API offline, using mock metrics');
  }
  return { users: 1420, revenue: 58200, uptime: '99.98%' };
}
