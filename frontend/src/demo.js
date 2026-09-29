// Static demo backend for GitHub Pages.
//
// Answers the dashboard's API calls from results recorded from the real engine
// (scripts/build_demo_data.py -> public/demo/). Live-state features (switching scenarios on,
// monitoring routes, alerts, dismissing intel) are kept in the browser and use the recorded
// routes; anything that needs the Python engine (scoring a new report, new routes) says so.

const BASE = `${import.meta.env.BASE_URL}demo/`;
const cache = {};
const load = (file) => {
  if (!cache[file]) {
    cache[file] = fetch(BASE + file).then((r) => {
      if (!r.ok) throw new Error(`demo data missing: ${file}`);
      return r.json();
    });
  }
  return cache[file];
};

const state = { active: new Set(), watches: [], alerts: [], dismissed: new Set(), seq: 1 };
const norm = (s) => (s ?? '').toString().trim().toLowerCase();
const uid = () => `demo${(state.seq++).toString(36)}${Date.now().toString(36).slice(-4)}`;
const NOT_RECORDED =
  "This combination isn't in the recorded demo. Choose one from “Demo routes”, or run the project " +
  'locally for the full live engine.';

export const demoUrl = (path) => {
  let m = path.match(/^\/api\/runs\/([\w]+)\/report\.pdf\?currency=(\w+)/);
  if (m) return `${BASE}pdf/${m[1]}_${m[2]}.pdf`;
  m = path.match(/^\/api\/runs\/([\w]+)\/export\.(csv|json)/);
  if (m) return `${BASE}exports/${m[1]}.${m[2]}`;
  return path;
};

export const demoPresets = async () => (await load('manifest.json')).presets;
export const demoManifest = () => load('manifest.json');

async function presetRuns() {
  const { presets } = await load('manifest.json');
  return Promise.all(presets.map(async (p) => ({ preset: p, run: await load(`runs/${p.id}.json`) })));
}

function endpointNames(value, hub, legName) {
  return [value, hub, legName].filter(Boolean).map(norm);
}

async function findRun(body) {
  const active = [...state.active];
  if (!body.scenario && active.length > 1) return null;
  const scenario = body.scenario || active[0] || null;
  for (const { preset, run } of await presetRuns()) {
    const r = preset.request;
    const resp = run.response;
    const first = resp.recommendations[0];
    const srcNames = endpointNames(r.source, resp.origin_hub, first.legs[0].from_name);
    const dstNames = endpointNames(r.destination, resp.destination_hub, first.legs[first.legs.length - 1].to_name);
    const value = Number(body.cargo_value_usd || 0);
    const sameValue = Math.abs(value - (r.cargo_value_usd || 0)) <= Math.max(1, 0.02 * (r.cargo_value_usd || 0));
    if (srcNames.includes(norm(body.source)) && dstNames.includes(norm(body.destination))
        && norm(body.transport_preference || 'any') === norm(r.transport_preference)
        && norm(body.routing_policy || 'STRICT') === norm(r.routing_policy)
        && norm(body.cargo_type || 'general') === norm(r.cargo_type)
        && norm(body.priority || 'normal') === norm(r.priority)
        && (scenario || null) === (r.scenario || null)
        && sameValue
        && Math.abs((body.carrying_cost_rate ?? 0.25) - r.carrying_cost_rate) < 0.001) {
      return { preset, run };
    }
  }
  return null;
}

const summary = (c) => ({
  persona: c.personas.join('/'), primary_mode: c.primary_mode, chokepoints: c.chokepoints,
  adjusted_eta: c.adjusted_eta, eta_band: c.eta_band, total_cost: c.total_cost, threat_level: c.threat_level,
});

async function evaluateWatches(scenarioId) {
  const scenarios = await load('scenarios.json');
  const sc = scenarios.find((s) => s.id === scenarioId);
  const raised = [];
  for (const w of state.watches) {
    const hubs = new Set([w.legs[0]?.from, ...w.legs.map((l) => l.to)]);
    const hit = sc.affected_nodes.filter((n) => hubs.has(n));
    if (!hit.length) continue;
    if (state.alerts.some((a) => !a.acknowledged && a.watch_id === w.id && a.signature === scenarioId)) continue;
    // Suggested alternative: the recorded run of the same trip under this scenario, if there is one.
    let alt = null;
    for (const { preset, run } of await presetRuns()) {
      const r = preset.request;
      if (r.scenario === scenarioId && norm(r.source) === norm(w.request.source)
          && norm(r.destination) === norm(w.request.destination)
          && norm(r.transport_preference) === norm(w.request.transport_preference)) {
        const recs = run.response.recommendations;
        alt = recs.find((c) => c.personas.includes(w.persona)) || recs[0];
      }
    }
    const route = w.label || `${w.request.source} -> ${w.request.destination} (${w.persona})`;
    let message = sc.closure
      ? `${route}: route is impassable at ${hit.join(', ')}.`
      : `${route}: route is affected at ${hit.join(', ')} (${sc.name}).`;
    if (alt) {
      message += ` Suggested reroute via ${alt.chokepoints.join(', ') || alt.primary_mode} `
        + `(p50 ${alt.eta_band.p50}h, $${Math.round(alt.total_cost).toLocaleString('en-US')}).`;
    }
    const alert = {
      id: uid(), created_at: Date.now() / 1000, watch_id: w.id, signature: scenarioId,
      severity: sc.closure ? 'CRITICAL' : 'HIGH', message, acknowledged: false,
      payload: { affected_hubs: hit, alternative: alt && summary(alt) },
    };
    state.alerts.unshift(alert);
    raised.push(alert);
  }
  return raised;
}

// Same rules as backend/engine/supplier_scorer.py get_procurement_advice().
function procurementAdvice({ current_inventory: inv, safety_stock: safety, demand_forecast: demand }) {
  const projected = inv - demand;
  const shortage = safety - projected;
  let status = 'HEALTHY', recommendation = 'Maintain current replenishment schedule.', urgency = 'LOW';
  if (projected <= 0) {
    status = 'CRITICAL_SHORTAGE';
    recommendation = 'EMERGENCY REPLENISHMENT REQUIRED. Projected stockout in current cycle.';
    urgency = 'CRITICAL';
  } else if (projected < safety) {
    status = 'SAFETY_STOCK_VIOLATION';
    recommendation = 'Expedite sourcing from high-reliability suppliers to restore safety buffers.';
    urgency = 'HIGH';
  }
  return { status, shortage_quantity: Math.max(0, shortage), recommendation, urgency_level: urgency,
           projected_inventory: projected };
}

export async function demoRequest(method, fullPath, body) {
  const [path, query = ''] = fullPath.split('?');
  const params = new URLSearchParams(query);
  let m;

  if (method === 'GET') {
    if (path === '/api/network' || path === '/api/fx' || path === '/api/feeds') return load(`${path.slice(5)}.json`);
    if (path === '/api/history') return load('history.json');
    if (path === '/api/scenarios') {
      return (await load('scenarios.json')).map((s) => ({ ...s, active: state.active.has(s.id) }));
    }
    if (path === '/api/intel') {
      const intel = await load('intel.json');
      return { ...intel, threats: intel.threats.filter((e) => !state.dismissed.has(`${e.hub_id}|${e.source}`)) };
    }
    if (path === '/api/watches') return state.watches.map(({ legs, ...w }) => w);
    if (path === '/api/alerts') return state.alerts.filter((a) => !a.acknowledged);
    if (path === '/api/hubs/search') {
      const q = norm(params.get('q'));
      return (await load('network.json')).nodes
        .filter((n) => norm(n.display_name).includes(q) || norm(n.id).includes(q) || norm(n.country).includes(q))
        .map((n) => ({ ...n, aliases: [] }));
    }
    if ((m = path.match(/^\/api\/runs\/(\w+)$/))) return load(`runs/${m[1]}.json`);
    if (path === '/api/status') return { engine_status: 'DEMO' };
  }

  if (method === 'POST') {
    if (path === '/api/recommend') {
      const hit = await findRun(body);
      if (!hit) return { error: NOT_RECORDED };
      return {
        ...hit.run.response, run_id: hit.preset.id,
        applied_scenarios: [...new Set([...(hit.run.response.applied_scenarios || []), ...state.active])],
      };
    }
    if ((m = path.match(/^\/api\/scenarios\/(\w+)\/activate$/))) {
      state.active.add(m[1]);
      return { active_scenarios: [...state.active], alerts_raised: await evaluateWatches(m[1]) };
    }
    if ((m = path.match(/^\/api\/scenarios\/(\w+)\/deactivate$/))) {
      state.active.delete(m[1]);
      return { active_scenarios: [...state.active] };
    }
    if (path === '/api/watches') {
      const run = await load(`runs/${body.run_id}.json`);
      const c = run.response.recommendations.find((x) => x.personas.includes(body.persona));
      const w = { id: uid(), created_at: Date.now() / 1000, run_id: body.run_id, persona: body.persona,
                  label: body.label, request: run.request, route: summary(c), active: true, legs: c.legs };
      state.watches.unshift(w);
      return { id: w.id, run_id: w.run_id, persona: w.persona, label: w.label, route: w.route };
    }
    if ((m = path.match(/^\/api\/alerts\/(\w+)\/ack$/))) {
      const a = state.alerts.find((x) => x.id === m[1]);
      if (a) a.acknowledged = true;
      return { ok: true };
    }
    if (path === '/api/intel/report') {
      return {
        intel: { hub_id: body.hub_id, score: 0, threat_by_mode: {}, headline: body.text,
                 note: 'Scoring a new report needs the live engine (the NLP model runs in Python). '
                       + 'Run the project locally to try it.' },
        alerts_raised: [],
      };
    }
    if (path === '/api/intel/scan') {
      const scan = (await load('intel.json')).last_scan || {};
      return { ...scan, status: 'ok' };
    }
    if (path === '/api/feeds/gdacs/refresh') return (await load('feeds.json')).feeds[0];
    if (path === '/api/suppliers') {
      const recorded = await load(`suppliers/${body.category}_${body.scenario || 'NORMAL'}.json`);
      return { ...recorded, advice: procurementAdvice(body) };
    }
  }

  if (method === 'DELETE') {
    if ((m = path.match(/^\/api\/watches\/(\w+)$/))) {
      state.watches = state.watches.filter((w) => w.id !== m[1]);
      return { ok: true };
    }
    if ((m = path.match(/^\/api\/intel\/([\w-]+)$/))) {
      const intel = await load('intel.json');
      intel.threats.filter((e) => e.hub_id === m[1] && (!params.get('source') || e.source === params.get('source')))
        .forEach((e) => state.dismissed.add(`${e.hub_id}|${e.source}`));
      return { ok: true };
    }
  }
  throw new Error(`Not available in the recorded demo: ${method} ${path}`);
}
