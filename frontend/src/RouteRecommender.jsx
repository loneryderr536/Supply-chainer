import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Truck, Ship, Plane, Train,
  AlertTriangle, ShieldCheck, Clock, Download,
  Navigation, Zap, Globe, Eye, History, Radio,
  ArrowRightLeft, BarChart3, Activity, Layers, Terminal, Bell
} from 'lucide-react';
import RouteMap from './RouteMap.jsx';
import LiveOps from './LiveOps.jsx';
import { api, apiUrl, DEMO, MODE_COLORS, hubName } from './api.js';
import { demoManifest } from './demo.js';
import { useI18n, explain, LANGUAGES, CURRENCIES } from './i18n.jsx';

const PERSONA_CLASS = { FASTEST: 'tag-fastest', SAFEST: 'tag-safest', BALANCED: 'tag-balanced' };

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
  const { t, num } = useI18n();
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
        <span>{t('card.plan')} {num(planned, 0)}h</span><span>p50 {num(band.p50, 0)}h</span>
        <span>p85 {num(band.p85, 0)}h</span><span>p95 {num(band.p95, 0)}h</span>
      </div>
    </div>
  );
}

function Attribution({ dominant }) {
  const { t } = useI18n();
  if (!dominant?.attribution?.contributions_h) return null;
  const { contributions_h: c, base_value_h, prediction_h } = dominant.attribution;
  const max = Math.max(...Object.values(c).map(Math.abs), 1);
  return (
    <div className="audit-trace-box" style={{ borderLeft: '4px solid #8b5cf6' }}>
      <div className="audit-title">{t('audit.why')}</div>
      <div style={{ marginBottom: 6 }}>
        {t('audit.worst', { from: hubName(dominant.from), to: hubName(dominant.to), mode: dominant.mode,
                            base: base_value_h, pred: prediction_h })}
      </div>
      {Object.entries(c).map(([k, v]) => (
        <div key={k} className="shap-row">
          <span>{t(`shap.${k}`)}</span>
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
  const i18n = useI18n();
  const { t, money, num, lang, setLang, currency, setCurrency, fx, toUsd } = i18n;
  const [cargoValue, setCargoValue] = useState('');
  const [demo, setDemo] = useState(null);
  const [carryRate, setCarryRate] = useState('25');
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
  const [feeds, setFeeds] = useState([]);
  const [watchNote, setWatchNote] = useState(null);

  const recommendations = result?.recommendations || [];
  const rec = recommendations[selected];

  const loadScenarios = useCallback(() => api.get('/api/scenarios').then(setScenarios).catch(console.error), []);
  const loadWatches = useCallback(() => api.get('/api/watches').then(setWatches).catch(console.error), []);
  const loadAlerts = useCallback(() => api.get('/api/alerts').then(setAlerts).catch(console.error), []);
  const loadHistory = useCallback(() => api.get('/api/history?limit=25').then(setHistory).catch(console.error), []);
  const loadIntel = useCallback(() => api.get('/api/intel').then((d) => { setIntel(d.threats || []); setFeeds(d.feeds || []); })
    .catch(console.error), []);

  useEffect(() => { loadScenarios(); loadWatches(); loadAlerts(); loadHistory(); loadIntel(); }, []);
  useEffect(() => { if (DEMO) demoManifest().then(setDemo).catch(console.error); }, []);
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
  const intelHubs = useMemo(() => {
    const m = new Map();
    intel.forEach((e) => { if (!m.has(e.hub_id) || e.score > m.get(e.hub_id).score) m.set(e.hub_id, e); });
    return m;
  }, [intel]);

  const getRecommendations = async () => {
    const src = source || searchQuery.source;
    const dst = destination || searchQuery.dest;
    if (!src || !dst) { setError(t('error.pick')); return; }
    setLoading(true);
    setError(null);
    setWatchNote(null);
    try {
      const data = await api.post('/api/recommend', {
        source: src, destination: dst,
        transport_preference: transportMode, routing_policy: routingPolicy,
        cargo_type: cargoType, priority,
        cargo_value_usd: toUsd(cargoValue),
        carrying_cost_rate: (Number(carryRate) || 0) / 100,
        scenario: operationalConfig !== 'NORMAL' ? operationalConfig : null,
      });
      if (data.error) { setError(data.error); setResult(null); }
      else { setResult(data); setSelected(0); loadHistory(); }
    } catch (err) {
      setError(t('error.engine'));
    } finally {
      setLoading(false);
    }
  };

  // Demo build: fill the form with a recorded request and show its real engine result.
  const runPreset = async (id) => {
    const p = demo?.presets.find((x) => x.id === id);
    if (!p) return;
    const r = p.request;
    setSource(''); setDestination('');
    setSearchQuery({ source: r.source, dest: r.destination });
    setTransportMode(r.transport_preference); setRoutingPolicy(r.routing_policy);
    setCargoType(r.cargo_type); setPriority(r.priority);
    setOperationalConfig(r.scenario || 'NORMAL');
    setCarryRate(String(Math.round(r.carrying_cost_rate * 100)));
    const rate = fx.rates?.[currency] || 1;
    setCargoValue(r.cargo_value_usd ? String(Math.round(r.cargo_value_usd * rate)) : '');
    setError(null); setWatchNote(null);
    const data = await api.post('/api/recommend', r);
    if (data.error) { setError(data.error); setResult(null); } else { setResult(data); setSelected(0); }
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
      setWatchNote(t('monitor.ok', { p: c.personas.map((p) => t(`persona.${p}`)).join('/') }));
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
             onKeyDown={(e) => e.key === 'Escape' && setSearchResults((prev) => ({ ...prev, [type]: [] }))}
             // Close the suggestions when focus leaves, after a click on one of them has registered.
             onBlur={() => setTimeout(() => setSearchResults((prev) => ({ ...prev, [type]: [] })), 150)}
             className="sc-input" placeholder={t('input.placeholder')} />
      {searchResults[type].length > 0 && (
        <div className="search-results" role="listbox">
          {searchResults[type].map((h, idx) => (
            <button key={`${h.id}-${idx}`} role="option" onMouseDown={(e) => e.preventDefault()}
                    onClick={() => selectHub(type, h)} className="search-item">
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
            <h1 style={{ fontSize: '1.25rem', fontWeight: 800 }}>{t('app.title')}</h1>
            <p style={{ fontSize: '0.7rem', color: '#64748b', fontWeight: 700 }}>{t('app.subtitle')}</p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <span className="sc-badge-active" title={(status?.notes || []).join(' ')}
                style={{ color: status?.engine_status === 'FULLY OPERATIONAL' ? '#10b981' : '#f59e0b' }}>
            <Activity size={14} /> {t(`status.${status?.engine_status || 'CONNECTING'}`)}
          </span>
          <select className="header-select" value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t('app.language')}>
            {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </select>
          <select className="header-select" value={currency} onChange={(e) => setCurrency(e.target.value)} aria-label={t('app.currency')}
                  title={fx.source}>
            {CURRENCIES.filter((c) => fx.rates?.[c]).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button className="sc-badge-active" style={{ cursor: 'pointer' }} onClick={() => setRightTab('ops')}
                  aria-label={t('alerts.open', { n: alerts.length })}>
            <Bell size={14} /> {alerts.length}
          </button>
          <button className="sc-badge-active" onClick={() => onNavigate('suppliers')} style={{ cursor: 'pointer' }}>
            <ShieldCheck size={14} /> {t('app.suppliers')}
          </button>
        </div>
      </header>

      <aside className="sidebar-left">
        <h2 className="panel-title"><Terminal size={14} /> {t('input.title')}</h2>
        {DEMO && demo && (
          <div className="sc-input-group">
            <label className="sc-label" htmlFor="preset">{t('demo.presets')}</label>
            <select id="preset" className="sc-select demo-select" value="" onChange={(e) => runPreset(e.target.value)}>
              <option value="">{t('demo.pick', { n: demo.presets.length })}</option>
              {demo.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
        )}
        {searchBox('source', t('input.origin'))}
        {searchBox('dest', t('input.destination'))}

        <div className="sc-input-group">
          <label className="sc-label" htmlFor="mode">{t('input.mode')}</label>
          <select id="mode" value={transportMode} onChange={(e) => setTransportMode(e.target.value)} className="sc-select">
            {['any', 'sea', 'air', 'rail', 'road'].map((m) => <option key={m} value={m}>{t(`mode.${m}`)}</option>)}
          </select>
        </div>

        <div className="sc-input-group">
          <label className="sc-label" htmlFor="policy">{t('input.policy')}</label>
          <select id="policy" value={routingPolicy} onChange={(e) => setRoutingPolicy(e.target.value)} className="sc-select">
            {['STRICT', 'PREFERRED'].map((p) => <option key={p} value={p}>{t(`policy.${p}`)}</option>)}
          </select>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
          <div className="sc-input-group">
            <label className="sc-label" htmlFor="cargo">{t('input.cargo')}</label>
            <select id="cargo" value={cargoType} onChange={(e) => setCargoType(e.target.value)} className="sc-select">
              {['general', 'perishable_urgent', 'hazardous_waste', 'oversize_heavy'].map((c) =>
                <option key={c} value={c}>{t(`cargo.${c}`)}</option>)}
            </select>
          </div>
          <div className="sc-input-group">
            <label className="sc-label" htmlFor="priority">{t('input.priority')}</label>
            <select id="priority" value={priority} onChange={(e) => setPriority(e.target.value)} className="sc-select">
              {['low', 'normal', 'urgent'].map((p) => <option key={p} value={p}>{t(`priority.${p}`)}</option>)}
            </select>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '0.75rem' }}>
          <div className="sc-input-group">
            <label className="sc-label" htmlFor="value">{t('input.value', { cur: currency })}</label>
            <input id="value" type="number" min="0" step="10000" className="sc-input" value={cargoValue}
                   onChange={(e) => setCargoValue(e.target.value)} placeholder="0" aria-describedby="value-hint" />
          </div>
          <div className="sc-input-group">
            <label className="sc-label" htmlFor="rate">{t('input.rate')}</label>
            <input id="rate" type="number" min="0" max="100" step="1" className="sc-input" value={carryRate}
                   onChange={(e) => setCarryRate(e.target.value)} />
          </div>
        </div>
        <p id="value-hint" className="ops-empty" style={{ marginTop: '-1rem' }}>{t('input.value.hint')}</p>

        <div className="sc-input-group">
          <label className="sc-label" htmlFor="whatif">{t('input.whatif')}</label>
          <select id="whatif" value={operationalConfig} onChange={(e) => setOperationalConfig(e.target.value)} className="sc-select"
                  style={{ borderColor: operationalConfig !== 'NORMAL' ? '#ef4444' : '#1e293b' }}>
            <option value="NORMAL">{t('input.normal')}</option>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{t(`scenario.${s.id}`)}</option>)}
          </select>
        </div>

        <button className="sc-btn-execute" onClick={getRecommendations} disabled={loading}>
          {loading ? <Zap className="animate-pulse" size={16} /> : t('input.go')}
        </button>
      </aside>

      <main className="main-content">
        {DEMO && (
          <div className="demo-banner" role="note">
            <b>{t('demo.title')}</b> {t('demo.banner', { date: demo?.generated_at || '' })}{' '}
            <a href="https://github.com/loneryderr536/Supply-chainer" target="_blank" rel="noreferrer">{t('demo.local')}</a>
          </div>
        )}
        {(whatIf || activeScenarios.length > 0) && (
          <div className="scenario-banner animate-slide-in">
            <AlertTriangle size={20} />
            <div>
              <span style={{ fontWeight: 800, fontSize: '0.75rem', display: 'block' }}>{t('banner.title')}</span>
              <span style={{ fontSize: '0.875rem' }}>
                {activeScenarios.length > 0 && <>{t('banner.live', { names: activeScenarios.map((s) => t(`scenario.${s.id}`)).join(', ') })} </>}
                {whatIf && t('banner.whatif', { name: t(`scenario.${whatIf.id}`) })}
              </span>
            </div>
          </div>
        )}

        {result?.hold_option && (
          <div className="hold-banner">
            <Clock size={18} />
            <div>
              <b>{result.hold_option.verdict === 'REROUTE' ? t('hold.reroute') : t('hold.hold')}</b>
              {' — '}{t('hold.text', {
                hubs: result.hold_option.waits_at.map(hubName).join(', '), p50: num(result.hold_option.eta_band.p50, 0),
                delta: num(Math.abs(result.hold_option.delta_vs_best_reroute_h), 0),
                dir: result.hold_option.delta_vs_best_reroute_h > 0 ? t('hold.later') : t('hold.sooner') })}
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
                  {c.personas.map((p) => <span key={p} className={`persona-badge ${PERSONA_CLASS[p]}`}>{t(`persona.${p}`)}</span>)}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.75rem', fontFamily: 'JetBrains Mono' }}>
                  <Clock size={12} /> {t('card.planned', { h: num(c.adjusted_eta, 0) })}
                </div>
              </div>
              <div style={{ padding: '1.25rem' }}>
                <EtaBand band={c.eta_band} planned={c.adjusted_eta} />
                <p style={{ fontSize: '0.8rem', color: '#cbd5e1', margin: '1rem 0' }}>
                  {c.explanation_facts ? explain(c.explanation_facts, i18n) : c.explanation}
                </p>
                <div className="leg-list">
                  {c.legs.filter((l) => l.type !== 'transfer').map((leg, lIdx) => (
                    <div key={lIdx} className="leg-row">
                      <span className="leg-mode" style={{ color: MODE_COLORS[leg.mode] }}>{getModeIcon(leg.mode)} {t(`legmode.${leg.mode}`)}</span>
                      <span className="leg-name">{leg.to_name}</span>
                      {leg.threat > 0.1 && (
                        <span className="leg-threat" title={leg.reason}>
                          {Math.round(leg.threat * 100)}%{leg.threat_category ? ` ${t(`cat.${leg.threat_category}`)}` : ''}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
              <div className="card-footer">
                <span style={{ color: '#10b981' }} title={t('card.freight')}>
                  {c.inventory_cost?.per_hour > 0
                    ? <>{money(c.landed_cost)} <small style={{ color: '#64748b' }}>{t('card.landed')}</small></>
                    : money(c.total_cost)}
                </span>
                <span style={{ color: c.threat_level >= 0.5 ? '#ef4444' : '#94a3b8' }}>{t('card.risk', { p: Math.round(c.threat_level * 100) })}</span>
                <button className="ops-btn" onClick={(e) => { e.stopPropagation(); monitor(c); }}><Eye size={12} /> {t('card.monitor')}</button>
              </div>
            </div>
          ))}
        </div>
      </main>

      <aside className="sidebar-right">
        <div className="tabs" role="tablist">
          {[['audit', Layers, t('tab.audit')], ['ops', Radio, t('tab.ops')], ['history', History, t('tab.history')]].map(([id, Icon, label]) => (
            <button key={id} role="tab" aria-selected={rightTab === id} className={`tab ${rightTab === id ? 'active' : ''}`}
                    onClick={() => { setRightTab(id); if (id === 'history') loadHistory(); }}>
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>

        {rightTab === 'audit' && (rec ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div className="audit-trace-box" style={{ borderLeft: '4px solid #3b82f6' }}>
              <div className="audit-title">{t('audit.eta')} · {rec.personas.map((p) => t(`persona.${p}`)).join('/')}</div>
              <div>{t('audit.transit', { h: num(rec.audit_trace.eta.transit) })}</div>
              <div>{t('audit.transfers', { h: num(rec.audit_trace.eta.transfer), n: rec.transfers })}</div>
              <div>{t('audit.scenario', { v: rec.audit_trace.eta.scenario > 0 ? `+${num(rec.audit_trace.eta.scenario)}h` : t('audit.none') })}</div>
              <div>{t('audit.buffer', { a: num(rec.audit_trace.ml.buffer_h.p50), b: num(rec.audit_trace.ml.buffer_h.p85), c: num(rec.audit_trace.ml.buffer_h.p95) })}</div>
              <div>{t('audit.corr', { h: num(rec.eta_band.p95_correlated) })}</div>
              <div style={{ marginTop: 6, color: '#64748b' }}>
                {t('audit.planned', { q: rec.audit_trace.ml.quantile_used, a: rec.audit_trace.ml.legs_on_trained_hub, b: rec.audit_trace.ml.legs_on_mode_prior })}
              </div>
            </div>

            <Attribution dominant={rec.audit_trace.ml.dominant_leg} />

            <div className="audit-trace-box" style={{ borderLeft: '4px solid #10b981' }}>
              <div className="audit-title">{t('audit.cost')}</div>
              <div>{t('audit.linehaul', { c: money(rec.audit_trace.cost.transit) })}</div>
              <div>{t('audit.transferfees', { c: money(rec.audit_trace.cost.transfer) })}</div>
              <div>{t('audit.surcharge', { c: money(rec.audit_trace.cost.scenario) })}</div>
              {rec.inventory_cost?.per_hour > 0 && (
                <>
                  <div>{t('audit.inventory', { c: money(rec.inventory_cost.p50), d: money(rec.inventory_cost.p95) })}</div>
                  <div style={{ color: '#f8fafc' }}>{t('audit.landed', { c: money(rec.landed_cost) })}</div>
                </>
              )}
              <div style={{ marginTop: 6, color: '#64748b' }}>{t('audit.fx', { src: fx.source })}</div>
            </div>

            <div className="audit-trace-box" style={{ borderLeft: '4px solid #f59e0b' }}>
              <div className="audit-title">{t('audit.risk')}</div>
              <div>{t('audit.risk.scenario', { p: Math.round(rec.audit_trace.risk.scenario * 100) })}</div>
              <div>{t('audit.risk.live', { p: Math.round(rec.audit_trace.risk.live_news * 100) })}</div>
              <div>{t('audit.risk.handling', { p: Math.round(rec.audit_trace.risk.baseline * 100) })}</div>
            </div>

            {result.run_id && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <a className="ops-btn" href={apiUrl(`/api/runs/${result.run_id}/report.pdf?currency=${currency}`)} target="_blank" rel="noreferrer">
                  <Download size={12} /> {t('audit.pdf')}
                </a>
                <a className="ops-btn" href={apiUrl(`/api/runs/${result.run_id}/export.csv`)} download><Download size={12} /> {t('audit.csv')}</a>
                <a className="ops-btn" href={apiUrl(`/api/runs/${result.run_id}/export.json`)} target="_blank" rel="noreferrer">
                  <Download size={12} /> {t('audit.json')}
                </a>
              </div>
            )}
          </div>
        ) : (
          <div style={{ textAlign: 'center', color: '#64748b', marginTop: '2rem' }}>
            <Activity size={48} style={{ opacity: 0.1, marginBottom: '1rem' }} />
            <p style={{ fontSize: '0.8rem' }}>{t('audit.empty')}</p>
          </div>
        ))}

        {rightTab === 'ops' && (
          <LiveOps feeds={feeds} onFeedsChanged={loadIntel} scenarios={scenarios} onScenariosChanged={loadScenarios} watches={watches} onWatchesChanged={loadWatches}
                   alerts={alerts} onAlertsChanged={loadAlerts} hubs={network?.nodes || []} intel={intel} onIntelChanged={loadIntel} />
        )}

        {rightTab === 'history' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {history.length === 0 && <p className="ops-empty">{t('history.empty')}</p>}
            {history.map((h) => (
              <button key={h.id} className="history-row" onClick={() => openRun(h.id)}>
                <div style={{ fontWeight: 700 }}>{h.origin} → {h.destination}</div>
                <div className="ops-empty">
                  {new Date(h.created_at * 1000).toLocaleString(i18n.locale)} · {t(`mode.${h.request.transport_preference}`)}
                  {h.applied_scenarios.length > 0 && ` · ${h.applied_scenarios.map((s) => t(`scenario.${s}`)).join(', ')}`}
                </div>
                <div className="ops-empty">
                  {h.options.map((o) => `${o.persona.split('/').map((p) => t(`persona.${p}`)).join('/')} ${num(o.eta_p50, 0)}h / ${money(o.total_cost)}`).join(' · ')}
                </div>
              </button>
            ))}
          </div>
        )}
      </aside>

      <footer className="tradeoff-strip">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <BarChart3 size={20} color="#64748b" />
          <span style={{ fontSize: '0.75rem', fontWeight: 800, color: '#64748b' }}>{t('strip.title')}</span>
        </div>
        <div style={{ display: 'flex', gap: '3rem', flex: 1, justifyContent: 'center' }}>
          <div className="strip-item">
            <span>{t('strip.fastest')}</span>
            <b style={{ color: '#f59e0b' }}>{recommendations.length ? num(Math.min(...recommendations.map((r) => r.eta_band.p50)), 0) : '--'}h</b>
          </div>
          <div className="strip-item">
            <span>{t('strip.cost')}</span>
            <b style={{ color: '#10b981' }}>{recommendations.length ? money(Math.min(...recommendations.map((r) => r.landed_cost ?? r.total_cost))) : '--'}</b>
          </div>
          <div className="strip-item">
            <span>{t('strip.p95')}</span>
            <b style={{ color: '#3b82f6' }}>{recommendations.length ? num(Math.min(...recommendations.map((r) => r.eta_band.p95)), 0) : '--'}h</b>
          </div>
          <div className="strip-item">
            <span>{t('strip.risk')}</span>
            <b style={{ color: '#3b82f6' }}>{recommendations.length ? Math.round(Math.min(...recommendations.map((r) => r.threat_level)) * 100) : '--'}%</b>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default RouteRecommender;
