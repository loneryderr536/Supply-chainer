import React, { useState } from 'react';
import { Bell, Radio, Eye, Trash2, Check, Send, Newspaper, RefreshCw, X } from 'lucide-react';
import { api, SEVERITY_COLORS, hubName } from './api.js';

export default function LiveOps({ scenarios, onScenariosChanged, watches, onWatchesChanged, alerts, onAlertsChanged, hubs, intel, onIntelChanged }) {
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const [intelHub, setIntelHub] = useState('');
  const [intelText, setIntelText] = useState('');
  const [intelResult, setIntelResult] = useState(null);

  const toggleScenario = async (s) => {
    setBusy(s.id);
    setNote(null);
    try {
      if (s.active) {
        await api.post(`/api/scenarios/${s.id}/deactivate`);
      } else {
        const out = await api.post(`/api/scenarios/${s.id}/activate`);
        setNote(`${s.name} is live. ${out.alerts_raised.length} monitored route(s) affected.`);
      }
      await Promise.all([onScenariosChanged(), onAlertsChanged()]);
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  };

  const submitIntel = async () => {
    if (!intelHub || !intelText.trim()) return;
    setBusy('intel');
    try {
      const out = await api.post('/api/intel/report', { hub_id: intelHub.trim().toUpperCase(), text: intelText });
      setIntelResult(out.intel);
      await Promise.all([onIntelChanged(), onAlertsChanged()]);
    } catch (e) {
      setIntelResult({ error: e.message });
    } finally {
      setBusy(null);
    }
  };

  const rescan = async () => {
    setBusy('scan');
    try {
      const out = await api.post('/api/intel/scan');
      setNote(out.status === 'ok' ? `Scanned ${out.hubs_scanned} hubs: ${out.threats} with a disruption signal.`
                                  : `News scan ${out.status}${out.reason ? `: ${out.reason}` : ''}.`);
      await Promise.all([onIntelChanged(), onAlertsChanged()]);
    } catch (e) { setNote(e.message); } finally { setBusy(null); }
  };

  const confirmIntel = async (t) => {
    await api.post('/api/intel/report', { hub_id: t.hub_id, text: t.headline });
    await Promise.all([onIntelChanged(), onAlertsChanged()]);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <section>
        <h3 className="ops-title"><Bell size={13} /> Alerts ({alerts.length})</h3>
        {alerts.length === 0 && <p className="ops-empty">No open alerts. Monitor a route, then activate a scenario.</p>}
        {alerts.map((a) => (
          <div key={a.id} className="alert-card" style={{ borderLeftColor: SEVERITY_COLORS[a.severity] || '#64748b' }}>
            <div className="alert-head">
              <span style={{ color: SEVERITY_COLORS[a.severity] }}>{a.severity}</span>
              <span>{new Date(a.created_at * 1000).toLocaleTimeString()}</span>
            </div>
            <div className="alert-body">{a.message}</div>
            <button className="ops-btn" onClick={async () => { await api.post(`/api/alerts/${a.id}/ack`); onAlertsChanged(); }}>
              <Check size={12} /> Acknowledge
            </button>
          </div>
        ))}
      </section>

      <section>
        <h3 className="ops-title"><Radio size={13} /> Live disruption picture</h3>
        <p className="ops-empty">Activating a scenario applies it to every new route and re-checks monitored routes.</p>
        {scenarios.map((s) => (
          <button key={s.id} className={`scenario-toggle ${s.active ? 'on' : ''}`} disabled={busy === s.id}
                  onClick={() => toggleScenario(s)} aria-pressed={s.active}>
            <span>{s.name}</span>
            <span className="scenario-state">{s.active ? 'LIVE' : 'off'}{s.closure ? ' · closure' : ''}</span>
          </button>
        ))}
        {note && <p className="ops-note">{note}</p>}
      </section>

      <section>
        <h3 className="ops-title"><Eye size={13} /> Monitored routes ({watches.length})</h3>
        {watches.length === 0 && <p className="ops-empty">Use “Monitor” on a route card.</p>}
        {watches.map((w) => (
          <div key={w.id} className="watch-row">
            <div>
              <div style={{ fontWeight: 700 }}>{w.label || `${w.request.source} → ${w.request.destination}`}</div>
              <div className="ops-empty">{w.persona} · p50 {w.route.eta_band.p50}h · via {w.route.chokepoints.map(hubName).join(', ') || w.route.primary_mode}</div>
            </div>
            <button className="icon-btn" aria-label="Stop monitoring" onClick={async () => { await api.del(`/api/watches/${w.id}`); onWatchesChanged(); }}>
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </section>

      <section>
        <h3 className="ops-title" style={{ justifyContent: 'space-between' }}>
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}><Newspaper size={13} /> Intel picture ({intel.length})</span>
          <button className="icon-btn" aria-label="Rescan live news" disabled={busy === 'scan'} onClick={rescan}><RefreshCw size={13} /></button>
        </h3>
        <p className="ops-empty">Live headlines must name the hub and describe a disruption; they count at 50% until confirmed.</p>
        {intel.map((t) => (
          <div key={t.hub_id} className="intel-row">
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700 }}>
                {hubName(t.hub_id)} · {Math.round(t.score * 100)}% {t.category}
                <span className="intel-src">{t.source === 'LIVE_NEWS' ? 'unverified' : 'analyst'}</span>
              </div>
              <div className="ops-empty">
                {t.link ? <a href={t.link} target="_blank" rel="noreferrer" style={{ color: '#93c5fd' }}>{t.headline}</a> : t.headline}
              </div>
              <div className="ops-empty">Applied: {Object.entries(t.threat_by_mode).filter(([, v]) => v > 0).map(([m, v]) => `${m} ${Math.round(v * 100)}%`).join(' · ') || 'none'}</div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {t.source === 'LIVE_NEWS' && (
                <button className="icon-btn" aria-label="Confirm at full weight" title="Confirm" onClick={() => confirmIntel(t)}><Check size={13} /></button>
              )}
              <button className="icon-btn" aria-label="Dismiss" title="Dismiss" onClick={async () => { await api.del(`/api/intel/${t.hub_id}`); onIntelChanged(); }}>
                <X size={13} />
              </button>
            </div>
          </div>
        ))}
      </section>

      <section>
        <h3 className="ops-title"><Send size={13} /> Analyst intel report</h3>
        <input className="sc-input" list="hub-ids" placeholder="Hub ID, e.g. PORT-ROTTERDAM" value={intelHub}
               onChange={(e) => setIntelHub(e.target.value)} aria-label="Hub ID" />
        <datalist id="hub-ids">{hubs.map((h, i) => <option key={`${h.id}-${i}`} value={h.id}>{h.display_name}</option>)}</datalist>
        <textarea className="sc-input" rows={3} style={{ marginTop: 6, width: '100%', resize: 'vertical' }}
                  placeholder="Headline or field report…" value={intelText} onChange={(e) => setIntelText(e.target.value)}
                  aria-label="Report text" />
        <button className="ops-btn" style={{ marginTop: 6 }} disabled={busy === 'intel'} onClick={submitIntel}>Score &amp; apply</button>
        {intelResult && (
          <div className="audit-trace-box" style={{ marginTop: 8 }}>
            {intelResult.error ? intelResult.error : intelResult.score > 0 ? (
              <>
                <div>NLP threat {Math.round(intelResult.score * 100)}% · {intelResult.category}</div>
                <div>CARF by mode: {Object.entries(intelResult.threat_by_mode).map(([m, v]) => `${m} ${Math.round(v * 100)}%`).join(' · ')}</div>
              </>
            ) : <div>{intelResult.note}</div>}
          </div>
        )}
      </section>
    </div>
  );
}
