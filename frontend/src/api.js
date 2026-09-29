import { demoRequest, demoUrl } from './demo.js';

// Static GitHub Pages build: answer from recorded engine results instead of a live server.
export const DEMO = import.meta.env.VITE_STATIC_DEMO === 'true';

async function request(path, options = {}) {
  if (DEMO) return demoRequest(options.method || 'GET', path, options.body);
  const res = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: 'POST', body }),
  del: (path) => request(path, { method: 'DELETE' }),
};

// URL for a downloadable export (PDF/CSV/JSON); static files in the demo build.
export const apiUrl = (path) => (DEMO ? demoUrl(path) : path);

export const MODE_COLORS = {
  SEA: '#3b82f6',
  AIR: '#f59e0b',
  RAIL: '#10b981',
  ROAD: '#f97316',
  TRANSFER: '#94a3b8',
};

export const SEVERITY_COLORS = {
  CRITICAL: '#ef4444',
  HIGH: '#f97316',
  MEDIUM: '#f59e0b',
};

export const hubName = (id) => (id || '').replace(/^(CHOKE|PORT|AIR|RAIL|HUB)-/, '');
