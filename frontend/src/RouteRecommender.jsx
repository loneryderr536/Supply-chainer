import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Truck, Ship, Plane, Train,
  AlertTriangle, ShieldCheck, Clock, Download,
  Navigation, Zap, Globe, Eye, History, Radio,
  ArrowRightLeft, BarChart3, Activity, Layers, Terminal, Bell
} from 'lucide-react';
import RouteMap from './RouteMap.jsx';
import LiveOps from './LiveOps.jsx';
import { api, MODE_COLORS, hubName } from './api.js';

const PERSONA_CLASS = { FASTEST: 'tag-fastest', SAFEST: 'tag-safest', BALANCED: 'tag-balanced' };
const SHAPLEY_LABELS = {
  news_severity: 'News severity', destination: 'Destination hub', origin: 'Origin hub',
  transport_mode: 'Transport mode', weather_condition: 'Weather',
};

const getModeIcon = (mode) => {
  switch ((mode || '').toLowerCase()) {
    case 'air': return <Plane size={12} />;
    case 'sea': return <Ship size={12} />;
    case 'rail': return <Train size={12} />;
    case 'road': return <Truck size={12} />;
    case 'transfer': return <ArrowRightLeft size={12} />;
    default: return <Navigation size={12} />;
  }
};

function EtaBand({ band, planned }) {
  // Scale from the deterministic plan to the fully-correlated worst case so the spread is visible.
  const lo = planned;
  const hi = Math.max(band.p95_correlated || band.p95, lo + 1);
  const pct = (v) => `${((v - lo) / (hi - lo)) * 100}%`;
  return (
    <div aria-label={`ETA p50 ${band.p50} hours, p85 ${band.p85}, p95 ${band.p95}`}>
      <div className="band-track">
        <div className="band-range" style={{ left: pct(band.p50), width: `calc(${pct(band.p95)} - ${pct(band.p50)})` }} />
        <div className="band-tick" style={{ left: pct(band.p85) }} />
      </div>
      <div className="band-labels">
        <span>plan {planned}h</span><span>p50 {band.p50}h</span><span>p85 {band.p85}h</span><span>p95 {band.p95}h</span>
      </div>
    </div>
  );
}

function Attribution({ dominant }) {
  if (!dominant?.attribution?.contributions_h) return null;
  const { contributions_h: c, base_value_h, prediction_h } = dominant.attribution;
  const max = Math.max(...Object.values(c).map(Math.abs), 1);
  return (
    <div className="audit-trace-box" style={{ borderLeft: '4px solid #8b5cf6' }}>
      <div className="audit-title">Why this delay? (Shapley, p85)</div>
      <div style={{ marginBottom: 6 }}>
        Worst leg {hubName(dominant.from)} → {hubName(dominant.to)} ({dominant.mode}): base {base_value_h}h → model {prediction_h}h
      </div>
      {Object.entries(c).map(([k, v]) => (
        <div key={k} className="shap-row">
          <span>{SHAPLEY_LABELS[k] || k}</span>
          <div className="shap-bar">
            <div style={{ width: `${(Math.abs(v) / max) * 100}%`, background: v >= 0 ? '#ef4444' : '#10b981' }} />
          </div>
          <span style={{ width: 56, textAlign: 'right' }}>{v >= 0 ? '+' : ''}{v}h</span>
        </div>
      ))}
    </div>
  );
}

const RouteRecommender = ({ onNavigate, status, network, alertTick }) => {
  const [source, setSource] = useState('');
  const [destination, setDestination] = useState('');
  const [transportMode, setTransportMode] = useState('any');
  const [routingPolicy, setRoutingPolicy] = useState('STRICT');
  const [operationalConfig, setOperationalConfig] = useState('NORMAL');
  const [cargoType, setCargoType] = useState('general');
  const [priority, setPriority] = useState('normal');
  const [result, setResult] = useState(null);
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState({ source: '', dest: '' });
  const [searchResults, setSearchResults] = useState({ source: [], dest: [] });
  const [scenarios, setScenarios] = useState([]);
  const [rightTab, setRightTab] = useState('audit');
  const [watches, setWatches] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [history, setHistory] = useState([]);
  const [intel, setIntel] = useState([]);
  const [watchNote, setWatchNote] = useState(null);

  const recommendations = result?.recommendations || [];
  const rec = recommendations[selected];

  const loadScenarios = useCallback(() => api.get('/api/scenarios').then(setScenarios).catch(console.error), []);
  const loadWatches = useCallback(() => api.get('/api/watches').then(setWatches).catch(console.error), []);
  const loadAlerts = useCallback(() => api.get('/api/alerts').then(setAlerts).catch(console.error), []);
  const loadHistory = useCallback(() => api.get('/api/history?limit=25').then(setHistory).catch(console.error), []);
  const loadIntel = useCallback(() => api.get('/api/intel').then((d) => setIntel(d.threats || [])).catch(console.error), []);

  useEffect(() => { loadScenarios(); loadWatches(); loadAlerts(); loadHistory(); loadIntel(); }, []);
  // A pushed alert arrived over the WebSocket: refresh the alert list and jump to it.
  useEffect(() => { if (alertTick) { loadAlerts(); setRightTab('ops'); } }, [alertTick]);

  const activeScenarios = scenarios.filter((s) => s.active);
  const whatIf = scenarios.find((s) => s.id === operationalConfig);
  const { disruptedHubs, closedHubs } = useMemo(() => {
    const dis = new Set(), closed = new Set();
    [...activeScenarios, ...(whatIf ? [whatIf] : [])].forEach((s) =>
      s.affected_nodes.forEach((n) => (s.closure ? closed : dis).add(n)));
    return { disruptedHubs: dis, closedHubs: closed };
  }, [scenarios, operationalConfig]);
  const intelHubs = useMemo(() => new Map(intel.map((t) => [t.hub_id, t])), [intel]);

  const getRecommendations = async () => {
    const src = source || searchQuery.source;
    const dst = destination || searchQuery.dest;
    if (!src || !dst) { setError('Choose an origin and a destination.'); return; }
    setLoading(true);
    setError(null);
    setWatchNote(null);
    try {
      const data = await api.post('/api/recommend', {
        source: src, destination: dst,
        transport_preference: transportMode, routing_policy: routingPolicy,
        cargo_type: cargoType, priority,
        scenario: operationalConfig !== 'NORMAL' ? operationalConfig : null,
      });
      if (data.error) { setError(data.error); setResult(null); }
      else { setResult(data); setSelected(0); loadHistory(); }
    } catch (err) {
      setError('Engine connection failed. Verify backend status.');
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (type, query) => {
    setSearchQuery((prev) => ({ ...prev, [type]: query }));
    if (type === 'source') setSource(''); else setDestination('');
    if (query.length < 2) { setSearchResults((prev) => ({ ...prev, [type]: [] })); return; }
    try {
      const data = await api.get(`/api/hubs/search?q=${encodeURIComponent(query)}`);
      setSearchResults((prev) => ({ ...prev, [type]: data.slice(0, 12) }));
    } catch (err) { console.error('Search failed'); }
  };

  const selectHub = (type, hub) => {
    if (type === 'source') { setSource(hub.id); setSearchQuery((p) => ({ ...p, source: hub.display_name })); }
    else { setDestination(hub.id); setSearchQuery((p) => ({ ...p, dest: hub.display_name })); }
    setSearchResults((prev) => ({ ...prev, [type]: [] }));
  };

  const monitor = async (c) => {
    try {
      await api.post('/api/watches', { run_id: result.run_id, persona: c.persona,
        label: `${searchQuery.source || result.origin} → ${searchQuery.dest || result.destination} (${c.personas.join('/')})` });
      setWatchNote(`Monitoring the ${c.personas.join('/')} route. You will be alerted if the live picture affects it.`);
      loadWatches();
    } catch (e) { setWatchNote(e.message); }
  };

  const openRun = async (id) => {
    const run = await api.get(`/api/runs/${id}`);
    setResult({ ...run.response, run_id: run.id });
    setSelected(0);
    setRightTab('audit');
  };

  const searchBox = (type, label) => (
    <div className="sc-input-group" style={{ position: 'relative' }}>
      <label className="sc-label" htmlFor={`search-${type}`}>{label}</label>
      <input id={`search-${type}`} type="text" value={searchQuery[type]} autoComplete="off"
             onChange={(e) => handleSearch(type, e.target.value)}
             className="sc-input" placeholder="City, hub name or code…" />
      {searchResults[type].length > 0 && (
        <div className="search-results" role="listbox">
          {searchResults[type].map((h, idx) => (
            <button key={`${h.id}-${idx}`} role="option" onClick={() => selectHub(type, h)} className="search-item">
              {h.display_name} <span style={{ color: '#64748b' }}>{h.id}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="dashboard-layout">
      <header className="dashboard-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <Globe size={28} color="#3b82f6" />
          <div>
            <h1 style={{ fontSize: '1.25rem', fontWeight: 800 }}>Supplychainer Command Console</h1>
            <p style={{ fontSize: '0.7rem', color: '#64748b', fontWeight: 700 }}>UNIFIED MULTIMODAL DECISION ENGINE</p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <span className="sc-badge-active" title={(status?.notes || []).join(' ')}
                style={{ color: status?.engine_status === 'FULLY OPERATIONAL' ? '#10b981' : '#f59e0b' }}>
            <Activity size={14} /> {status?.engine_status || 'CONNECTING'}
          </span>
          <button className="sc-badge-active" style={{ cursor: 'pointer' }} onClick={() => setRightTab('ops')}
                  aria-label={`${alerts.length} open alerts`}>
            <Bell size={14} /> {alerts.length}
          </button>
          <button className="sc-badge-active" onClick={() => onNavigate('suppliers')} style={{ cursor: 'pointer' }}>
            <ShieldCheck size={14} /> SUPPLIER INTELLIGENCE
          </button>
        </div>
      </header>

      <aside className="sidebar-left">
        <h2 className="panel-title"><Terminal size={14} /> Strategic Input Panel</h2>
        {searchBox('source', 'Origin')}
        {searchBox('dest', 'Destination')}

        <div className="sc-input-group">
          <label className="sc-label" htmlFor="mode">Transport Mode</label>
          <select id="mode" value={transportMode} onChange={(e) => setTransportMode(e.target.value)} className="sc-select">
            <option value="any">Unconstrained</option>
            <option value="sea">SEA (Maritime Corridors)</option>
            <option value="air">AIR (Express Cargo)</option>
            <option value="rail">RAIL (Inland Freight)</option>
            <option value="road">ROAD (Local Distribution)</option>
          </select>
        </div>

        <div className="sc-input-group">
          <label className="sc-label" htmlFor="policy">Routing Policy</label>
          <select id="policy" value={routingPolicy} onChange={(e) => setRoutingPolicy(e.target.value)} className="sc-select">
            <option value="STRICT">STRICT (only this mode + first/last-mile road)</option>
            <option value="PREFERRED">PREFERRED (other modes cost 1.5×)</option>
          </select>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
          <div className="sc-input-group">
            <label className="sc-label" htmlFor="cargo">Cargo</label>
            <select id="cargo" value={cargoType} onChange={(e) => setCargoType(e.target.value)} className="sc-select">
              <option value="general">General</option>
              <option value="perishable_urgent">Perishable (no sea)</option>
              <option value="hazardous_waste">Hazardous (no air)</option>
              <option value="oversize_heavy">Oversize (no road)</option>
            </select>
          </div>
          <div className="sc-input-group">
            <label className="sc-label" htmlFor="priority">Priority</label>
            <select id="priority" value={priority} onChange={(e) => setPriority(e.target.value)} className="sc-select">
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="urgent">Urgent</option>
            </select>
          </div>
        </div>

        <div className="sc-input-group">
          <label className="sc-label" htmlFor="whatif">What-if Scenario (this request only)</label>
          <select id="whatif" value={operationalConfig} onChange={(e) => setOperationalConfig(e.target.value)} className="sc-select"
                  style={{ borderColor: operationalConfig !== 'NORMAL' ? '#ef4444' : '#1e293b' }}>
            <option value="NORMAL">Operational Normal</option>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>

        <button className="sc-btn-execute" onClick={getRecommendations} disabled={loading}>
          {loading ? <Zap className="animate-pulse" size={16} /> : 'GENERATE STRATEGIC ROUTE OPTIONS'}
        </button>
      </aside>

      <main className="main-content">
        {(whatIf || activeScenarios.length > 0) && (
          <div className="scenario-banner animate-slide-in">
            <AlertTriangle size={20} />
            <div>
              <span style={{ fontWeight: 800, fontSize: '0.75rem', display: 'block' }}>DISRUPTION PICTURE</span>
              <span style={{ fontSize: '0.875rem' }}>
                {activeScenarios.length > 0 && <>Live: {activeScenarios.map((s) => s.name).join(', ')}. </>}
                {whatIf && <>What-if: {whatIf.name}.</>}
              </span>
            </div>
          </div>
        )}

        {result?.hold_option && (
          <div className="hold-banner">
            <Clock size={18} />
            <div>
              <b>{result.hold_option.verdict === 'REROUTE' ? 'Reroute recommended' : 'Holding is faster'}</b>
              {' — '}{result.hold_option.note} Hold ETA p50 {result.hold_option.eta_band.p50}h.
            </div>
          </div>
        )}

        {error && <div className="error-box">{error}</div>}

        <RouteMap network={network} recommendations={recommendations} selected={selected}
                  disruptedHubs={disruptedHubs} closedHubs={closedHubs} intelHubs={intelHubs} />

        {watchNote && <div className="ops-note">{watchNote}</div>}

        <div className="path-grid">
          {recommendations.map((c, idx) => (
            <div key={idx} className={`path-card ${idx === selected ? 'selected' : ''}`} onClick={() => setSelected(idx)}
                 tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setSelected(idx)} role="button" aria-pressed={idx === selected}>
              <div className="card-header">
                <div style={{ display: 'flex', gap: 4 }}>
                  {c.personas.map((p) => <span key={p} className={`persona-badge ${PERSONA_CLASS[p]}`}>{p}</span>)}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.75rem', fontFamily: 'JetBrains Mono' }}>
                  <Clock size={12} /> {c.adjusted_eta}h planned
                </div>
              </div>
              <div style={{ padding: '1.25rem' }}>
                <EtaBand band={c.eta_band} planned={c.adjusted_eta} />
                <p style={{ fontSize: '0.8rem', color: '#cbd5e1', margin: '1rem 0' }}>{c.explanation}</p>
                <div className="leg-list">
                  {c.legs.filter((l) => l.type !== 'transfer').map((leg, lIdx) => (
                    <div key={lIdx} className="leg-row">
                      <span className="leg-mode" style={{ color: MODE_COLORS[leg.mode] }}>{getModeIcon(leg.mode)} {leg.mode}</span>
                      <span className="leg-name">{leg.to_name}</span>
                      {leg.threat > 0.1 && (
                        <span className="leg-threat" title={leg.reason}>
                          {Math.round(leg.threat * 100)}%{leg.threat_category ? ` ${leg.threat_category}` : ''}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
              <div className="card-footer">
                <span style={{ color: '#10b981' }}>${c.total_cost.toLocaleString()}</span>
                <span style={{ color: c.threat_level >= 0.5 ? '#ef4444' : '#94a3b8' }}>risk {Math.round(c.threat_level * 100)}%</span>
                <button className="ops-btn" onClick={(e) => { e.stopPropagation(); monitor(c); }}><Eye size={12} /> Monitor</button>
              </div>
            </div>
          ))}
        </div>
      </main>

      <aside className="sidebar-right">
        <div className="tabs" role="tablist">
          {[['audit', Layers, 'Audit'], ['ops', Radio, 'Live Ops'], ['history', History, 'History']].map(([id, Icon, label]) => (
            <button key={id} role="tab" aria-selected={rightTab === id} className={`tab ${rightTab === id ? 'active' : ''}`}
                    onClick={() => { setRightTab(id); if (id === 'history') loadHistory(); }}>
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>

        {rightTab === 'audit' && (rec ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div className="audit-trace-box" style={{ borderLeft: '4px solid #3b82f6' }}>
              <div className="audit-title">ETA audit · {rec.personas.join('/')}</div>
              <div>Transit: {rec.audit_trace.eta.transit}h</div>
              <div>Transfers: +{rec.audit_trace.eta.transfer}h ({rec.transfers} handoffs)</div>
              <div>Scenario (announced): {rec.audit_trace.eta.scenario > 0 ? `+${rec.audit_trace.eta.scenario}h` : 'none'}</div>
              <div>ML delay buffer p50/p85/p95: +{rec.audit_trace.ml.buffer_h.p50} / +{rec.audit_trace.ml.buffer_h.p85} / +{rec.audit_trace.ml.buffer_h.p95}h</div>
              <div>Route p95 if leg delays are fully correlated: {rec.eta_band.p95_correlated}h</div>
              <div style={{ marginTop: 6, color: '#64748b' }}>
                Planned on {rec.audit_trace.ml.quantile_used}; {rec.audit_trace.ml.legs_on_trained_hub} legs on trained hubs,
                {' '}{rec.audit_trace.ml.legs_on_mode_prior} on the mode prior.
              </div>
            </div>

            <Attribution dominant={rec.audit_trace.ml.dominant_leg} />

            <div className="audit-trace-box" style={{ borderLeft: '4px solid #10b981' }}>
              <div className="audit-title">Cost composition</div>
              <div>Linehaul: ${rec.audit_trace.cost.transit.toLocaleString()}</div>
              <div>Transfer fees: ${rec.audit_trace.cost.transfer.toLocaleString()}</div>
              <div>Disruption surcharge: ${rec.audit_trace.cost.scenario.toLocaleString()}</div>
            </div>

            <div className="audit-trace-box" style={{ borderLeft: '4px solid #f59e0b' }}>
              <div className="audit-title">Risk sources</div>
              <div>Scenario: {Math.round(rec.audit_trace.risk.scenario * 100)}%</div>
              <div>Live news / analyst: {Math.round(rec.audit_trace.risk.live_news * 100)}%</div>
              <div>Handling (transfers): {Math.round(rec.audit_trace.risk.baseline * 100)}%</div>
            </div>

            {result.run_id && (
              <div style={{ display: 'flex', gap: 8 }}>
                <a className="ops-btn" href={`/api/runs/${result.run_id}/export.csv`}><Download size={12} /> Audit CSV</a>
                <a className="ops-btn" href={`/api/runs/${result.run_id}/export.json`} target="_blank" rel="noreferrer">
                  <Download size={12} /> TMS JSON
                </a>
              </div>
            )}
          </div>
        ) : (
          <div style={{ textAlign: 'center', color: '#64748b', marginTop: '2rem' }}>
            <Activity size={48} style={{ opacity: 0.1, marginBottom: '1rem' }} />
            <p style={{ fontSize: '0.8rem' }}>Generate routes to see the decision audit.</p>
          </div>
        ))}

        {rightTab === 'ops' && (
          <LiveOps scenarios={scenarios} onScenariosChanged={loadScenarios} watches={watches} onWatchesChanged={loadWatches}
                   alerts={alerts} onAlertsChanged={loadAlerts} hubs={network?.nodes || []} intel={intel} onIntelChanged={loadIntel} />
        )}

        {rightTab === 'history' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {history.length === 0 && <p className="ops-empty">No saved runs yet.</p>}
            {history.map((h) => (
              <button key={h.id} className="history-row" onClick={() => openRun(h.id)}>
                <div style={{ fontWeight: 700 }}>{h.origin} → {h.destination}</div>
                <div className="ops-empty">
                  {new Date(h.created_at * 1000).toLocaleString()} · {h.request.transport_preference}
                  {h.applied_scenarios.length > 0 && ` · ${h.applied_scenarios.join(', ')}`}
                </div>
                <div className="ops-empty">
                  {h.options.map((o) => `${o.persona} ${o.eta_p50}h/$${Math.round(o.total_cost).toLocaleString()}`).join(' · ')}
                </div>
              </button>
            ))}
          </div>
        )}
      </aside>

      <footer className="tradeoff-strip">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <BarChart3 size={20} color="#64748b" />
          <span style={{ fontSize: '0.75rem', fontWeight: 800, color: '#64748b' }}>TRADEOFF ANALYSIS</span>
        </div>
        <div style={{ display: 'flex', gap: '3rem', flex: 1, justifyContent: 'center' }}>
          <div className="strip-item">
            <span>FASTEST (p50):</span>
            <b style={{ color: '#f59e0b' }}>{recommendations.length ? Math.min(...recommendations.map((r) => r.eta_band.p50)) : '--'}h</b>
          </div>
          <div className="strip-item">
            <span>LOWEST COST:</span>
            <b style={{ color: '#10b981' }}>${recommendations.length ? Math.min(...recommendations.map((r) => r.total_cost)).toLocaleString() : '--'}</b>
          </div>
          <div className="strip-item">
            <span>TIGHTEST p95:</span>
            <b style={{ color: '#3b82f6' }}>{recommendations.length ? Math.min(...recommendations.map((r) => r.eta_band.p95)) : '--'}h</b>
          </div>
          <div className="strip-item">
            <span>RISK FLOOR:</span>
            <b style={{ color: '#3b82f6' }}>{recommendations.length ? Math.round(Math.min(...recommendations.map((r) => r.threat_level)) * 100) : '--'}%</b>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default RouteRecommender;
